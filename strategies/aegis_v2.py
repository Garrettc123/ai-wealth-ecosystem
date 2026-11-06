#!/usr/bin/env python3
"""AEGIS V2: deterministic, depth-aware, read-only crypto research engine.

No exchange credentials, orders, transfers, or realized profit accounting.
All candidates are hypothetical. No part of this module can execute a trade.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any

from strategies.aegis_scanner import (
    COINBASE, KRAKEN, EvidenceLedger, ResearchError, canonical, dec,
    get_public_json, parse_book, sha256, snapshot_book, utcnow,
)

SCHEMA = 'garcar.aegis.v2'
SHOCK_BPS = (0, 10, 25, 50, 100, 250)


def positive_bps(v: Any, name: str, cap: str = '5000') -> Decimal:
    value = dec(v, name, zero=True)
    if value > Decimal(cap):
        raise ResearchError(f'{name} exceeds safety policy bound')
    return value


@dataclass(frozen=True)
class Policy:
    quantity_btc: str = '0.001'
    quantity_step_btc: str = '0.00000001'
    min_order_usd: str = '10'
    max_buy_usd: str = '300'
    buy_fee_bps: str = '100'  # illustrative only; NOT account fee tier
    sell_fee_bps: str = '100'
    base_stress_bps: str = '20'
    depth_haircut_bps: str = '2000'
    fixed_cost_usd: str = '0'
    min_net_profit_usd: str = '1'
    min_net_edge_bps: str = '20'
    min_stressed_net_usd: str = '0'
    max_book_spread_bps: str = '500'
    max_cross_venue_mid_gap_bps: str = '1000'
    max_age_seconds: str = '10'
    max_receipt_skew_seconds: str = '5'

    def validated(self) -> dict[str, Decimal]:
        vals: dict[str, Decimal] = {}
        for field in ('quantity_btc', 'quantity_step_btc', 'min_order_usd', 'max_buy_usd'):
            vals[field] = dec(getattr(self, field), field)
        for field in ('fixed_cost_usd', 'min_net_profit_usd', 'min_stressed_net_usd'):
            vals[field] = dec(getattr(self, field), field, zero=True)
        for field in ('buy_fee_bps', 'sell_fee_bps', 'base_stress_bps', 'depth_haircut_bps',
                      'min_net_edge_bps', 'max_book_spread_bps', 'max_cross_venue_mid_gap_bps'):
            vals[field] = positive_bps(getattr(self, field), field,
                                        cap='9999' if field == 'depth_haircut_bps' else '5000')
        for field in ('max_age_seconds', 'max_receipt_skew_seconds'):
            vals[field] = dec(getattr(self, field), field)
            if vals[field] > 60:
                raise ResearchError(f'{field} must not exceed 60 seconds')
        if vals['depth_haircut_bps'] >= 10000:
            raise ResearchError('Depth haircut must be less than 100%')
        if vals['quantity_step_btc'] > vals['quantity_btc']:
            raise ResearchError('Quantity smaller than exchange increment')
        if vals['min_order_usd'] > vals['max_buy_usd']:
            raise ResearchError('Minimum order exceeds notional cap')
        if vals['min_stressed_net_usd'] > vals['max_buy_usd']:
            raise ResearchError('Stressed minimum exceeds maximum buy notional')
        return vals


def as_policy(data: dict[str, Any]) -> Policy:
    if not isinstance(data, dict) or set(data) != set(Policy.__dataclass_fields__):
        raise ResearchError('Policy schema mismatch or missing risk controls')
    result = Policy(**{k: str(v) for k, v in data.items()})
    result.validated()
    return result


def _check_time(book: dict, now: datetime, cfg: dict[str, Decimal]):
    rec = book.get('received')
    if not isinstance(rec, datetime) or rec.tzinfo is None:
        raise ResearchError('Book receipt timestamp missing timezone')
    age = Decimal(str((now - rec.astimezone(timezone.utc)).total_seconds()))
    if age < -1 or age > cfg['max_age_seconds']:
        raise ResearchError(f"{book.get('provider')} receipt stale or in future")
    source = book.get('source')
    if source is not None:
        if not isinstance(source, datetime) or source.tzinfo is None:
            raise ResearchError('Exchange timestamp invalid')
        src_age = Decimal(str((now - source.astimezone(timezone.utc)).total_seconds()))
        if src_age < -5 or src_age > cfg['max_age_seconds']:
            raise ResearchError('Exchange-side snapshot stale')


def _check_book(book: dict):
    """Defense in depth for books supplied by something other than parse_book."""
    provider = book.get('provider')
    if provider not in ('coinbase', 'kraken'):
        raise ResearchError('Unrecognized exchange')
    for side in ('asks', 'bids'):
        rows = book.get(side)
        if not isinstance(rows, (list, tuple)) or not 1 <= len(rows) <= 25:
            raise ResearchError('Invalid level count')
        last = None
        for row in rows:
            if not isinstance(row, (tuple, list)) or len(row) != 2:
                raise ResearchError('Malformed depth level')
            p, q = dec(row[0], 'price'), dec(row[1], 'size')
            if last is not None and (p <= last if side == 'asks' else p >= last):
                raise ResearchError('Incorrect depth ordering or duplicate price')
            last = p
    if dec(book['bids'][0][0], 'best bid') >= dec(book['asks'][0][0], 'best ask'):
        raise ResearchError('Locked or crossed book')


def _serialize_book(book: dict) -> dict:
    return snapshot_book(book)


def _deserialize_book(saved: dict) -> dict:
    try:
        if set(saved) != {'provider', 'received_utc', 'exchange_time_utc', 'bids', 'asks'}:
            raise ResearchError('Book evidence shape changed')
        t = datetime.fromisoformat(saved['received_utc'])
        s = datetime.fromisoformat(saved['exchange_time_utc']) if saved['exchange_time_utc'] else None
        if t.tzinfo is None or (s is not None and s.tzinfo is None):
            raise ResearchError('Book evidence timestamps missing timezone')
        b = dict(provider=saved['provider'], received=t, source=s,
                 bids=tuple((dec(p,'bid price'),dec(q,'bid size')) for p,q in saved['bids']),
                 asks=tuple((dec(p,'ask price'),dec(q,'ask size')) for p,q in saved['asks']))
        _check_book(b)
        return b
    except (KeyError, TypeError, ValueError) as exc:
        raise ResearchError('Book evidence cannot be replayed') from exc


def _fill(levels: tuple, qty: Decimal, haircut: Decimal) -> dict | None:
    """Conservative observed liquidity with haircut at each level."""
    left, gross, touched, top = qty, Decimal(0), 0, None
    for price, size in levels:
        p, s = dec(price, 'level price'), dec(size, 'level size')
        if top is None:
            top = p
        safe_qty = s * (Decimal(1) - haircut / 10000)
        take = min(left, safe_qty)
        if take > 0:
            gross += p * take
            left -= take
            touched += 1
        if left == 0:
            break
    if left > 0:
        return None
    return {'gross_usd': gross, 'vwap_usd': gross / qty,
            'top_price_usd': top, 'levels_used': touched}


def _route(buy: dict, sell: dict, qty: Decimal, cfg: dict[str, Decimal]) -> dict:
    reasons = []
    h = cfg['depth_haircut_bps']
    buyfill = _fill(buy['asks'], qty, h)
    sellfill = _fill(sell['bids'], qty, h)
    if buyfill is None:
        reasons.append('buy_depth_after_haircut_insufficient')
    if sellfill is None:
        reasons.append('sell_depth_after_haircut_insufficient')
    result = {'buy_venue': buy['provider'], 'sell_venue': sell['provider'],
              'quantity_btc': str(qty), 'screen_passed': False,
              'trade_allowed': False, 'funding_verified': False,
              'exchange_inventory_verified': False, 'fee_tier_verified': False,
              'orders_submitted': 0, 'realized_profit_usd': None,
              'reasons': reasons, 'stress_scenarios': []}
    if buyfill is None or sellfill is None:
        return result
    buy_raw, sell_raw = buyfill['gross_usd'], sellfill['gross_usd']
    if buy_raw < cfg['min_order_usd']:
        reasons.append('below_exchange_order_notional_assumption')
    if buy_raw > cfg['max_buy_usd']:
        reasons.append('raw_buy_cap_exceeded')
    fee_buy, fee_sell = cfg['buy_fee_bps'] / 10000, cfg['sell_fee_bps'] / 10000
    # Adverse stress is applied to *both* legs, not a one-sided optimistic move.
    stress_rows = []
    for shock in SHOCK_BPS:
        total = cfg['base_stress_bps'] + Decimal(shock)
        if total >= 10000:
            raise ResearchError('Excessive scenario shock')
        shocked_buy = buy_raw * (1 + total / 10000) * (1 + fee_buy)
        shocked_sell = sell_raw * (1 - total / 10000) * (1 - fee_sell)
        net = shocked_sell - shocked_buy - cfg['fixed_cost_usd']
        edge = net / shocked_buy * 10000
        stress_rows.append({'incremental_shock_bps_per_leg': shock,
                            'total_shock_bps_per_leg': str(total),
                            'buy_cost_usd': str(shocked_buy),
                            'sell_proceeds_usd': str(shocked_sell),
                            'net_usd': str(net), 'net_edge_bps': str(edge)})
    base = stress_rows[0]
    worst = stress_rows[-1]
    if Decimal(base['buy_cost_usd']) > cfg['max_buy_usd']:
        reasons.append('total_buy_cost_cap_exceeded')
    if Decimal(base['net_usd']) < cfg['min_net_profit_usd']:
        reasons.append('insufficient_base_net_profit')
    if Decimal(base['net_edge_bps']) < cfg['min_net_edge_bps']:
        reasons.append('insufficient_base_net_edge')
    if Decimal(worst['net_usd']) < cfg['min_stressed_net_usd']:
        reasons.append('stress_case_not_profitable')
    # Minimum SELL VWAP to break even at base assumptions, not a fill guarantee.
    m = cfg['base_stress_bps'] / 10000
    denom = qty * (1 - m) * (1 - fee_sell)
    break_even = (Decimal(base['buy_cost_usd']) + cfg['fixed_cost_usd']) / denom
    result.update({'buy_vwap_usd': str(buyfill['vwap_usd']),
                   'sell_vwap_usd': str(sellfill['vwap_usd']),
                   'levels_consumed': {'buy': buyfill['levels_used'], 'sell': sellfill['levels_used']},
                   'break_even_sell_vwap_usd': str(break_even),
                   'base_estimated_net_usd': base['net_usd'],
                   'worst_estimated_net_usd': worst['net_usd'],
                   'stress_scenarios': stress_rows, 'screen_passed': not reasons})
    return result


def evaluate_books(coinbase: dict, kraken: dict, *, policy: Policy | None = None,
                   asof: datetime | None = None) -> dict:
    policy = policy or Policy()
    cfg = policy.validated()
    now = asof or utcnow()
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ResearchError('Observation time must have timezone')
    now = now.astimezone(timezone.utc)
    if coinbase.get('provider') != 'coinbase' or kraken.get('provider') != 'kraken':
        raise ResearchError('Expected Coinbase/Kraken books, in order')
    for b in (coinbase, kraken):
        _check_book(b)
        _check_time(b, now, cfg)
    skew = Decimal(str(abs((coinbase['received']-kraken['received']).total_seconds())))
    if skew > cfg['max_receipt_skew_seconds']:
        raise ResearchError('Provider receipt skew exceeds policy')
    qty_raw = cfg['quantity_btc']
    step = cfg['quantity_step_btc']
    qty = (qty_raw / step).to_integral_value(rounding=ROUND_DOWN) * step
    if qty <= 0:
        raise ResearchError('Rounded quantity must be positive')
    metrics = {}
    for b in (coinbase, kraken):
        bid = dec(b['bids'][0][0], 'bid')
        ask = dec(b['asks'][0][0], 'ask')
        metrics[b['provider']] = {'mid': (ask + bid) / 2,
                                   'spread_bps': (ask - bid) / ((ask + bid) / 2) * 10000}
    midgap = abs(metrics['coinbase']['mid'] - metrics['kraken']['mid']) / min(
        metrics['coinbase']['mid'], metrics['kraken']['mid']) * 10000
    outliers = []
    if midgap > cfg['max_cross_venue_mid_gap_bps']:
        outliers.append('excessive_cross_venue_mid_gap')
    if any(v['spread_bps'] > cfg['max_book_spread_bps'] for v in metrics.values()):
        outliers.append('wide_bid_ask_spread')
    routes = [_route(coinbase, kraken, qty, cfg), _route(kraken, coinbase, qty, cfg)]
    for r in routes:
        r['reasons'].extend(outliers)
        r['screen_passed'] = not r['reasons']
    report = {'schema': SCHEMA, 'mode': 'READ_ONLY',
              'observed_at_utc': now.isoformat(), 'instrument': 'BTC-USD',
              'policy': asdict(policy), 'requested_quantity_btc': str(qty_raw),
              'rounded_quantity_btc': str(qty),
              'market_metrics': {k: {'mid_usd': str(v['mid']), 'spread_bps': str(v['spread_bps'])}
                                 for k,v in metrics.items()},
              'cross_venue_mid_gap_bps': str(midgap), 'books': [_serialize_book(coinbase), _serialize_book(kraken)],
              'routes': routes, 'observed_book_digest_sha256': sha256([_serialize_book(coinbase), _serialize_book(kraken)]),
              'verified_exchange_accounts': False, 'verified_both_venue_inventories': False,
              'real_trade_permission': False, 'orders_submitted': 0, 'realized_profit_usd': None,
              'warning': 'L2 snapshots are indicative; no authenticated balances, inventory, fee tier, fill, profit or X Money funding verified.'}
    report['evidence_sha256'] = sha256(report)
    return report


def verify_replay(report: dict) -> bool:
    """Recompute every derived output, not merely the SHA-256 string."""
    if not isinstance(report, dict) or report.get('schema') != SCHEMA:
        return False
    try:
        source = dict(report)
        digest = source.pop('evidence_sha256')
        if not isinstance(digest, str) or sha256(source) != digest:
            return False
        policy = as_policy(report['policy'])
        books = report['books']
        if not isinstance(books, list) or len(books) != 2:
            return False
        restored = [_deserialize_book(b) for b in books]
        at = datetime.fromisoformat(report['observed_at_utc'])
        rebuilt = evaluate_books(restored[0], restored[1], policy=policy, asof=at)
        return canonical(report) == canonical(rebuilt)
    except (ResearchError, KeyError, TypeError, ValueError, ArithmeticError, OverflowError):
        return False


def scan_market(*, policy: Policy | None = None, fetcher=get_public_json, clock=utcnow) -> dict:
    # Network deliberately uses only the existing strict public allowlist.
    from concurrent.futures import ThreadPoolExecutor
    def fetch(provider, url):
        payload = fetcher(url)
        received = clock()
        return parse_book(provider, payload, received)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(fetch, 'coinbase', COINBASE)
        b = pool.submit(fetch, 'kraken', KRAKEN)
        cb, kr = a.result(), b.result()
    return evaluate_books(cb, kr, policy=policy, asof=clock())


def main(argv=None):
    ap = argparse.ArgumentParser(description='AEGIS V2 | zero-trade, stress-tested BTC/USD research')
    ap.add_argument('--quantity-btc', default='0.001')
    ap.add_argument('--max-usd', default='300')
    ap.add_argument('--buy-fee-bps', default='100')
    ap.add_argument('--sell-fee-bps', default='100')
    ap.add_argument('--ledger')
    ap.add_argument('--output')
    ap.add_argument('--replay', help='Recompute and verify every field from a saved V2 report')
    args = ap.parse_args(argv)
    if args.replay:
        report = json.loads(Path(args.replay).read_text(encoding='utf-8'))
        valid = verify_replay(report)
        print(json.dumps({'replay_verified': valid, 'orders_submitted': 0}))
        return 0 if valid else 2
    try:
        policy = Policy(quantity_btc=args.quantity_btc, max_buy_usd=args.max_usd,
                        buy_fee_bps=args.buy_fee_bps, sell_fee_bps=args.sell_fee_bps)
        report = scan_market(policy=policy)
        if args.ledger:
            ledger = EvidenceLedger(args.ledger)
            try:
                ledger_tip = ledger.append(report)
                import sys
                print(f'AEGIS evidence chain tip (save independently): {ledger_tip}', file=sys.stderr)
            finally:
                ledger.close()
        if args.output:
            Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
        return 0
    except (ResearchError, OSError, ValueError) as exc:
        ap.exit(2, f'AEGIS V2 blocked: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
