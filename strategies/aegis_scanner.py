#!/usr/bin/env python3
"""Garcar Aegis: evidence-producing, read-only L2 market research.

EXPLICIT NON-GOALS: no orders, transfers, authentication, wallet signing, or
claimed realized P&L. Prices are unexecutable observations, not firm quotes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

COINBASE = "https://api.coinbase.com/api/v3/brokerage/market/product_book?product_id=BTC-USD&limit=25"
KRAKEN = "https://api.kraken.com/0/public/Depth?pair=XBTUSD&count=25"
ALLOWED_URLS = frozenset((COINBASE, KRAKEN))


class ResearchError(RuntimeError):
    """Fail-closed market data, evidence, or validation error."""


def dec(v: Any, label: str, zero: bool = False) -> Decimal:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ResearchError(f"{label}: invalid decimal") from exc
    if not d.is_finite() or d < 0 or (d == 0 and not zero):
        raise ResearchError(f"{label}: expected finite {'nonnegative' if zero else 'positive'} decimal")
    return d


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256(data: Any) -> str:
    return hashlib.sha256(canonical(data).encode("utf-8")).hexdigest()


def iso_time(value: str) -> datetime:
    try:
        t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ResearchError("invalid Coinbase exchange time") from exc
    if t.tzinfo is None:
        raise ResearchError("Coinbase exchange time lacks timezone")
    return t.astimezone(timezone.utc)


def levels(rows: list, *, side: str, provider: str) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(rows, list) or not 1 <= len(rows) <= 25:
        raise ResearchError(f"{provider} {side}: no valid depth levels")
    out = []
    for r in rows:
        try:
            price, size = (r["price"], r["size"]) if provider == "coinbase" else (r[0], r[1])
        except (KeyError, IndexError, TypeError) as exc:
            raise ResearchError(f"{provider} {side}: malformed level") from exc
        price, size = dec(price, "price"), dec(size, "size")
        if out:
            earlier = out[-1][0]
            if (side == "asks" and price <= earlier) or (side == "bids" and price >= earlier):
                raise ResearchError(f"{provider} {side}: prices not strictly ordered")
        out.append((price, size))
    return tuple(out)


def parse_book(provider: str, payload: dict, received: datetime) -> dict:
    if received.tzinfo is None:
        raise ResearchError("receipt time must be timezone-aware")
    try:
        if provider == "coinbase":
            pb = payload["pricebook"]
            if pb["product_id"] != "BTC-USD":
                raise ResearchError("Coinbase product mismatch")
            bids, asks = pb["bids"], pb["asks"]
            source = iso_time(pb["time"])
        elif provider == "kraken":
            if payload["error"]:
                raise ResearchError("Kraken API error")
            result = payload["result"]
            if set(result) != {"XXBTZUSD"}:
                raise ResearchError("Kraken pair mismatch")
            bids, asks = result["XXBTZUSD"]["bids"], result["XXBTZUSD"]["asks"]
            source = None  # Kraken REST Depth has no whole-book snapshot timestamp.
        else:
            raise ResearchError("Unknown market-data provider")
    except (KeyError, TypeError) as exc:
        raise ResearchError(f"{provider}: invalid order-book response") from exc
    parsed_bids = levels(bids, side="bids", provider=provider)
    parsed_asks = levels(asks, side="asks", provider=provider)
    if parsed_bids[0][0] >= parsed_asks[0][0]:
        raise ResearchError(f"{provider}: locked or crossed book")
    return {"provider": provider, "bids": parsed_bids, "asks": parsed_asks,
            "received": received.astimezone(timezone.utc), "source": source}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ResearchError("Unexpected exchange redirect")


def get_public_json(url: str) -> dict:
    if url not in ALLOWED_URLS:
        raise ResearchError("Market-data URL is not allowlisted")
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "Garcar-Aegis-ReadOnly/1.0"})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=7) as response:
            data = response.read(250001)
        if len(data) > 250000:
            raise ResearchError("Market-data response too large")
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ResearchError("Market-data response not an object")
        return payload
    except (OSError, ValueError) as exc:
        raise ResearchError("Market-data network or JSON failure") from exc


def sweep(book: tuple[tuple[Decimal, Decimal], ...], quantity: Decimal) -> Decimal | None:
    """Cumulative L2 quote across prices; None if observed depth insufficient."""
    remaining, total = quantity, Decimal(0)
    for price, available in book:
        take = min(remaining, available)
        total += price * take
        remaining -= take
        if remaining == 0:
            return total
    return None


def compare(buy: dict, sell: dict, *, quantity: Any, max_usd: Any,
            fee_bps: Any, stress_bps: Any, fixed_cost_usd: Any,
            edge_bps: Any) -> dict:
    qty = dec(quantity, "quantity")
    max_notional = dec(max_usd, "max_usd")
    fee = dec(fee_bps, "fee_bps", zero=True) / 10000
    stress = dec(stress_bps, "stress_bps", zero=True) / 10000
    fixed = dec(fixed_cost_usd, "fixed_cost_usd", zero=True)
    minimum = dec(edge_bps, "edge_bps", zero=True)
    if fee >= 1 or stress >= 1:
        raise ResearchError("fee and stress must each be less than 100%")
    raw_buy = sweep(buy["asks"], qty)
    raw_sell = sweep(sell["bids"], qty)
    enough = raw_buy is not None and raw_sell is not None
    reasons = []
    if not enough:
        reasons.append("insufficient_observed_depth")
    result = {"buy": buy["provider"], "sell": sell["provider"],
              "quantity_btc": str(qty), "observed_depth_sufficient": enough,
              "mathematical_candidate_only": False, "trade_allowed": False,
              "estimated_net_usd": None, "estimated_edge_bps": None,
              "execution_price_guaranteed": False, "verified_inventory": False,
              "realized_profit_usd": None, "blockers": reasons}
    if not enough:
        return result
    buy_cost = raw_buy * (1 + fee) * (1 + stress)
    sell_proceeds = raw_sell * (1 - fee) * (1 - stress)
    net = sell_proceeds - buy_cost - fixed
    edge = (net / buy_cost) * 10000
    if buy_cost > max_notional:
        reasons.append("notional_cap_exceeded")
    if edge < minimum:
        reasons.append("below_minimum_net_edge")
    result.update({"estimated_buy_cost_usd": str(buy_cost),
                   "estimated_sell_proceeds_usd": str(sell_proceeds),
                   "estimated_fixed_cost_usd": str(fixed),
                   "estimated_net_usd": str(net), "estimated_edge_bps": str(edge),
                   "buy_vwap_usd": str(raw_buy / qty),
                   "sell_vwap_usd": str(raw_sell / qty),
                   "mathematical_candidate_only": not reasons})
    return result


def snapshot_book(book: dict) -> dict:
    return {"provider": book["provider"], "received_utc": book["received"].isoformat(),
            "exchange_time_utc": book["source"].isoformat() if book["source"] else None,
            "bids": [[str(p), str(q)] for p, q in book["bids"]],
            "asks": [[str(p), str(q)] for p, q in book["asks"]]}


def analyze(*, fetcher=get_public_json, clock=utcnow, quantity="0.001",
            max_usd="300", fee_bps="100", stress_bps="20",
            fixed_cost_usd="0", edge_bps="20", max_age_seconds="10") -> dict:
    """One non-trading observation; reject stale, mismatched, or partial data."""
    age_limit = dec(max_age_seconds, "max_age_seconds")
    def get(provider: str, url: str):
        payload = fetcher(url)
        received = clock()
        return parse_book(provider, payload, received)
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_cb = pool.submit(get, "coinbase", COINBASE)
        f_kr = pool.submit(get, "kraken", KRAKEN)
        cb, kr = f_cb.result(), f_kr.result()
    now = clock()
    if now.tzinfo is None:
        raise ResearchError("Clock is not timezone-aware")
    now = now.astimezone(timezone.utc)
    for book in (cb, kr):
        age = Decimal(str((now - book["received"]).total_seconds()))
        if age < -1 or age > age_limit:
            raise ResearchError("Stale or future exchange receipt")
        if book["source"] is not None:
            age = Decimal(str((now - book["source"]).total_seconds()))
            if age < -5 or age > age_limit:
                raise ResearchError("Stale or future Coinbase book timestamp")
    if abs((cb["received"] - kr["received"]).total_seconds()) > 5:
        raise ResearchError("Market-data receipt skew exceeds five seconds")
    settings = {"quantity_btc": str(dec(quantity, "quantity")),
                "maximum_buy_cost_usd": str(dec(max_usd, "max_usd")),
                "per_leg_fee_bps_assumed": str(dec(fee_bps, "fee_bps", zero=True)),
                "per_leg_adverse_stress_bps_assumed": str(dec(stress_bps, "stress_bps", zero=True)),
                "fixed_cost_usd_assumed": str(dec(fixed_cost_usd, "fixed_cost_usd", zero=True)),
                "minimum_net_edge_bps": str(dec(edge_bps, "edge_bps", zero=True))}
    args = dict(quantity=quantity, max_usd=max_usd, fee_bps=fee_bps,
                stress_bps=stress_bps, fixed_cost_usd=fixed_cost_usd, edge_bps=edge_bps)
    report = {"schema": "garcar.aegis.v1", "mode": "READ_ONLY_PUBLIC_DATA",
              "created_utc": now.isoformat(), "instrument": "BTC-USD",
              "settings": settings, "books": [snapshot_book(cb), snapshot_book(kr)],
              "comparisons": [compare(cb, kr, **args), compare(kr, cb, **args)],
              "execution_enabled": False, "order_submitted": False,
              "balance_verified": False, "inventory_verified": False,
              "fees_verified": False, "funding_link_verified": False,
              "realized_profit_usd": None,
              "caveat": "Mathematical estimates, not executable fills. Actual fee tier, inventories, transfer costs, market impact and eligibility unverified."}
    report["evidence_sha256"] = sha256(report)
    return report


class EvidenceLedger:
    """Local append-only-by-application hash chain, *not* immutable storage."""
    def __init__(self, path: str):
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.execute("PRAGMA busy_timeout=10000")
        self.db.execute("CREATE TABLE IF NOT EXISTS evidence (seq INTEGER PRIMARY KEY, prev TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL)")

    def append(self, report: dict) -> str:
        payload = canonical(report)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if not self.verify():
                raise ResearchError("Evidence history failed verification; refusing append")
            row = self.db.execute("SELECT seq, digest FROM evidence ORDER BY seq DESC LIMIT 1").fetchone()
            seq, prev = ((row[0] + 1, row[1]) if row else (1, "0" * 64))
            digest = hashlib.sha256(f"{seq}:{prev}:{payload}".encode()).hexdigest()
            self.db.execute("INSERT INTO evidence VALUES (?,?,?,?)", (seq, prev, digest, payload))
            self.db.execute("COMMIT")
            return digest
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def verify(self) -> bool:
        prev = "0" * 64
        expected_seq = 1
        for seq, previous, digest, payload in self.db.execute("SELECT seq, prev, digest, payload FROM evidence ORDER BY seq"):
            if seq != expected_seq or previous != prev:
                return False
            if hashlib.sha256(f"{seq}:{previous}:{payload}".encode()).hexdigest() != digest:
                return False
            try:
                report = json.loads(payload)
                checksum = report.pop("evidence_sha256")
                if sha256(report) != checksum:
                    return False
            except (ValueError, TypeError, KeyError):
                return False
            prev, expected_seq = digest, seq + 1
        return True

    def close(self):
        self.db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Garcar Aegis — read-only market evidence")
    parser.add_argument("--quantity-btc", default="0.001")
    parser.add_argument("--max-usd", default="300")
    parser.add_argument("--fee-bps", default="100")
    parser.add_argument("--stress-bps", default="20")
    parser.add_argument("--fixed-cost-usd", default="0")
    parser.add_argument("--edge-bps", default="20")
    parser.add_argument("--ledger", help="Optional SQLite evidence file")
    args = parser.parse_args(argv)
    try:
        report = analyze(quantity=args.quantity_btc, max_usd=args.max_usd,
                         fee_bps=args.fee_bps, stress_bps=args.stress_bps,
                         fixed_cost_usd=args.fixed_cost_usd, edge_bps=args.edge_bps)
        if args.ledger:
            ledger = EvidenceLedger(args.ledger)
            try:
                report["ledger_chain_digest"] = ledger.append(report)
            finally:
                ledger.close()
        print(json.dumps(report, indent=2))
        return 0
    except (ResearchError, sqlite3.Error) as exc:
        parser.exit(2, f"Aegis safely blocked: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
