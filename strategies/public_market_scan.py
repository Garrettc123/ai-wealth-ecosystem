#!/usr/bin/env python3
"""Read-only Coinbase/Kraken BTC-USD arbitrage observation.

No API keys, bank credentials, exchange balances, trade execution, or deposits.
A cross-venue spread is an *indicative paper estimate*, not executable profit.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import json
import urllib.request

from strategies.crypto_arbitrage import CryptoArbitrage, _decimal

COINBASE_BOOK_URL = (
    "https://api.coinbase.com/api/v3/brokerage/market/product_book"
    "?product_id=BTC-USD&limit=1"
)
KRAKEN_BOOK_URL = "https://api.kraken.com/0/public/Depth?pair=XBTUSD&count=1"
ALLOWED_URLS = frozenset((COINBASE_BOOK_URL, KRAKEN_BOOK_URL))


class MarketDataError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def public_json(url):
    """Only fetch known, unauthenticated, allowlisted HTTPS market-data URLs."""
    if url not in ALLOWED_URLS:
        raise MarketDataError("Market-data URL is not allowed")
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "Garcar-ReadOnly-Scanner/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            if response.geturl() != url:
                raise MarketDataError("Redirected market-data URL rejected")
            payload = response.read(200001)
        if len(payload) > 200000:
            raise MarketDataError("Market-data response exceeded byte limit")
        return json.loads(payload)
    except (OSError, ValueError) as exc:
        raise MarketDataError("Public market-data request failed") from exc


def _parse_timestamp(value):
    if not isinstance(value, str):
        raise MarketDataError("Invalid exchange timestamp")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketDataError("Unparseable exchange timestamp") from exc
    if stamp.tzinfo is None:
        raise MarketDataError("Exchange timestamp must have timezone")
    return stamp.astimezone(timezone.utc)


def _book(venue, bid_price, bid_size, ask_price, ask_size, received, source_time=None):
    try:
        bid = _decimal(bid_price, "bid_price")
        ask = _decimal(ask_price, "ask_price")
        bid_qty = _decimal(bid_size, "bid_size")
        ask_qty = _decimal(ask_size, "ask_size")
    except ValueError as exc:
        raise MarketDataError("Invalid exchange price or liquidity") from exc
    if bid >= ask:
        raise MarketDataError("Unexpected crossed or locked market")
    return {
        "venue": venue, "bid": bid, "ask": ask,
        "bid_size": bid_qty, "ask_size": ask_qty,
        "received": received, "source_time": source_time,
    }


def parse_coinbase(payload, received):
    try:
        pricebook = payload["pricebook"]
        bid = pricebook["bids"][0]
        ask = pricebook["asks"][0]
        if pricebook.get("product_id") != "BTC-USD":
            raise MarketDataError("Coinbase product mismatch")
        timestamp = _parse_timestamp(pricebook["time"])
        return _book("coinbase", bid["price"], bid["size"], ask["price"], ask["size"],
                     received, timestamp)
    except (KeyError, IndexError, TypeError) as exc:
        raise MarketDataError("Invalid Coinbase order-book response") from exc


def parse_kraken(payload, received):
    try:
        if payload.get("error"):
            raise MarketDataError("Kraken returned an API error")
        result = payload["result"]
        if len(result) != 1:
            raise MarketDataError("Unexpected Kraken trading pair")
        book = next(iter(result.values()))
        ask = book["asks"][0]
        bid = book["bids"][0]
        return _book("kraken", bid[0], bid[1], ask[0], ask[1], received)
    except (KeyError, IndexError, TypeError, AttributeError, StopIteration) as exc:
        raise MarketDataError("Invalid Kraken order-book response") from exc


def scan_once(
    *,
    quantity="0.001",
    max_notional_usd="300",
    min_net_edge_bps="20",
    buy_fee_bps="100",
    sell_fee_bps="100",
    slippage_bps="20",
    fetcher=public_json,
    clock=utc_now,
):
    """Compare two *freshly fetched* order books; returns no trading instructions.

    Fee and slippage inputs are explicit *illustrative assumptions*, not
    exchange-account fee tiers. Kraken REST depth has no authoritative book
    snapshot timestamp; its network receipt timestamp is used instead.
    """
    qty = _decimal(quantity, "quantity")
    engine = CryptoArbitrage({
        "max_notional_usd": max_notional_usd,
        "min_net_edge_bps": min_net_edge_bps,
    })
    fees = {name: str(_decimal(value, name, allow_zero=True)) for name, value in (
        ("buy_fee_bps", buy_fee_bps),
        ("sell_fee_bps", sell_fee_bps),
        ("slippage_bps", slippage_bps),
    )}

    def load(url, parser):
        payload = fetcher(url)
        received = clock()
        if received.tzinfo is None:
            raise MarketDataError("Clock must return timezone-aware UTC time")
        return parser(payload, received.astimezone(timezone.utc))

    with ThreadPoolExecutor(max_workers=2) as executor:
        cb_future = executor.submit(load, COINBASE_BOOK_URL, parse_coinbase)
        kr_future = executor.submit(load, KRAKEN_BOOK_URL, parse_kraken)
        coinbase, kraken = cb_future.result(), kr_future.result()

    now = clock().astimezone(timezone.utc)
    max_age = Decimal("10")
    for book in (coinbase, kraken):
        age_seconds = Decimal(str((now - book["received"]).total_seconds()))
        if age_seconds < -1 or age_seconds > max_age:
            raise MarketDataError("Market-data receipt too old or in the future")
        if book["source_time"] is not None:
            exchange_age = Decimal(str((now - book["source_time"]).total_seconds()))
            if exchange_age < -5 or exchange_age > max_age:
                raise MarketDataError("Coinbase exchange book timestamp stale")
    skew = abs((coinbase["received"] - kraken["received"]).total_seconds())
    if skew > 5:
        raise MarketDataError("Market-data snapshots too far apart")

    comparisons = []
    for buy, sell in ((coinbase, kraken), (kraken, coinbase)):
        depth_ok = buy["ask_size"] >= qty and sell["bid_size"] >= qty
        estimate = engine.evaluate_quote(
            buy_ask_usd=buy["ask"],
            sell_bid_usd=sell["bid"],
            quantity=qty,
            buy_fee_bps=fees["buy_fee_bps"],
            sell_fee_bps=fees["sell_fee_bps"],
            slippage_bps=fees["slippage_bps"],
        )
        comparisons.append({
            "buy_venue": buy["venue"], "sell_venue": sell["venue"],
            "top_of_book_liquidity_sufficient": depth_ok,
            "mathematical_candidate_only": depth_ok and estimate["candidate_only"],
            "estimate": estimate,
        })

    return {
        "mode": "public_market_data_only",
        "product": "BTC-USD",
        "quantity_btc": str(qty),
        "checked_at_utc": now.isoformat(),
        "fee_and_slippage_bps_assumptions": fees,
        "snapshots": [{
            "venue": book["venue"], "bid_usd": str(book["bid"]),
            "ask_usd": str(book["ask"]), "bid_size_btc": str(book["bid_size"]),
            "ask_size_btc": str(book["ask_size"]),
            "received_at_utc": book["received"].isoformat(),
            "exchange_time_utc": book["source_time"].isoformat() if book["source_time"] else None,
        } for book in (coinbase, kraken)],
        "comparisons": comparisons,
        "funding_verified": False,
        "cross_exchange_inventory_verified": False,
        "transfer_costs_included": False,
        "live_order_submitted": False,
        "realized_profit_usd": None,
        "warning": ("Indicative quote only. Requires pre-funded exchange inventories, "
                    "actual account-specific fees, executable order-book depth, and "
                    "individually authorized trade execution. No money was moved."),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Read-only BTC-USD two-exchange scanner")
    parser.add_argument("--quantity", default="0.001")
    parser.add_argument("--max-notional-usd", default="300")
    parser.add_argument("--buy-fee-bps", default="100",
                        help="Assumed fee, not an actual exchange fee tier")
    parser.add_argument("--sell-fee-bps", default="100",
                        help="Assumed fee, not an actual exchange fee tier")
    parser.add_argument("--slippage-bps", default="20",
                        help="Assumed slippage on EACH exchange")
    args = parser.parse_args()
    try:
        output = scan_once(quantity=args.quantity, max_notional_usd=args.max_notional_usd,
                           buy_fee_bps=args.buy_fee_bps, sell_fee_bps=args.sell_fee_bps,
                           slippage_bps=args.slippage_bps)
        print(json.dumps(output, indent=2))
    except (MarketDataError, ValueError) as exc:
        parser.exit(2, f"Scan blocked: {exc}\n")
