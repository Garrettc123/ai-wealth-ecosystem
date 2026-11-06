"""Adversarial, no-network AEGIS V2 tests; all scenarios are synthetic fixtures."""
import json
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from strategies.aegis_scanner import EvidenceLedger, ResearchError, parse_book, sha256
from strategies.aegis_v2 import (
    Policy, as_policy, evaluate_books, scan_market, verify_replay, _fill,
)
from strategies.aegis_scanner import COINBASE, KRAKEN


def books(*, cb_bid='100', cb_ask='101', kr_bid='108', kr_ask='109',
          bid_size='5', ask_size='5', cb_spread=None, seconds_old=0):
    now = datetime.now(timezone.utc)
    src = (now - timedelta(seconds=seconds_old)).isoformat()
    def pair(bid, ask, step='1'):
        bids = [[str(Decimal(bid)-i), bid_size] for i in range(25)]
        asks = [[str(Decimal(ask)+i), ask_size] for i in range(25)]
        return bids, asks
    c_bids, c_asks = pair(cb_bid, cb_ask)
    k_bids, k_asks = pair(kr_bid, kr_ask)
    cb = {'pricebook':{'product_id':'BTC-USD','time':src,
                      'bids':[{'price':p,'size':q} for p,q in c_bids],
                      'asks':[{'price':p,'size':q} for p,q in c_asks]}}
    kr = {'error':[], 'result':{'XXBTZUSD':{
        'bids':[[p,q,123] for p,q in k_bids],
        'asks':[[p,q,123] for p,q in k_asks]}}}
    return {COINBASE:cb, KRAKEN:kr}, now


def evaluate(**kwargs):
    payloads, when = books(**{k: v for k,v in kwargs.items() if k in
                               ('cb_bid','cb_ask','kr_bid','kr_ask','bid_size','ask_size','seconds_old')})
    cb = parse_book('coinbase', payloads[COINBASE], when)
    kr = parse_book('kraken', payloads[KRAKEN], when)
    return evaluate_books(cb,kr,policy=kwargs.get('policy', Policy(
        quantity_btc='1', max_buy_usd='1000', min_order_usd='1',
        buy_fee_bps='10',sell_fee_bps='10',base_stress_bps='0',
        depth_haircut_bps='0',min_net_profit_usd='0',
        min_net_edge_bps='0',min_stressed_net_usd='0')),
        asof=kwargs.get('asof', when))


class CoreTests(unittest.TestCase):
    def test_01_evidence_verifies(self):
        self.assertTrue(verify_replay(evaluate()))

    def test_02_six_scenarios(self):
        self.assertEqual(len(evaluate()['routes'][0]['stress_scenarios']), 6)

    def test_03_no_trade_authorized(self):
        r=evaluate(); self.assertFalse(r['real_trade_permission']); self.assertEqual(r['orders_submitted'],0)
        for route in r['routes']:
            self.assertFalse(route['trade_allowed']); self.assertEqual(route['orders_submitted'],0)
            self.assertIsNone(route['realized_profit_usd'])

    def test_04_ask_buy_bid_sell(self):
        r=evaluate(); a=r['routes'][0]
        self.assertEqual(Decimal(a['buy_vwap_usd']),Decimal('101'))
        self.assertEqual(Decimal(a['sell_vwap_usd']),Decimal('108'))

    def test_05_direction_changes_result(self):
        r=evaluate()
        self.assertGreater(Decimal(r['routes'][0]['base_estimated_net_usd']),
                           Decimal(r['routes'][1]['base_estimated_net_usd']))

    def test_06_market_metric_integrity(self):
        r=evaluate(); self.assertEqual(r['schema'],'garcar.aegis.v2')
        self.assertEqual(r['observed_book_digest_sha256'],sha256(r['books']))

    def test_07_quantity_is_rounded_down(self):
        p=Policy(quantity_btc='0.222', quantity_step_btc='0.01',
                 max_buy_usd='1000',min_order_usd='1')
        r=evaluate(policy=p)
        self.assertEqual(Decimal(r['rounded_quantity_btc']),Decimal('0.22'))

    def test_08_rounding_over_notional_disallows(self):
        p=Policy(quantity_btc='1',max_buy_usd='101', min_order_usd='1',
                 buy_fee_bps='100', sell_fee_bps='100', base_stress_bps='0',
                 depth_haircut_bps='0',min_net_profit_usd='0',
                 min_net_edge_bps='0',min_stressed_net_usd='0')
        r=evaluate(policy=p)
        self.assertIn('total_buy_cost_cap_exceeded',r['routes'][0]['reasons'])

    def test_09_small_order_disallows(self):
        p=Policy(quantity_btc='0.1',max_buy_usd='1000',min_order_usd='100')
        self.assertIn('below_exchange_order_notional_assumption', evaluate(policy=p)['routes'][0]['reasons'])

    def test_10_insufficient_haircut_depth(self):
        p=Policy(quantity_btc='1',max_buy_usd='1000', min_order_usd='1',depth_haircut_bps='9500')
        a=evaluate(policy=p,ask_size='0.1')['routes'][0]
        self.assertIn('buy_depth_after_haircut_insufficient',a['reasons'])
        self.assertEqual(a['stress_scenarios'],[])

    def test_11_cross_venue_outlier_blocks(self):
        p=Policy(quantity_btc='1',max_buy_usd='10000',min_order_usd='1',
                 max_cross_venue_mid_gap_bps='10')
        self.assertIn('excessive_cross_venue_mid_gap',evaluate(policy=p)['routes'][0]['reasons'])

    def test_12_wide_spread_blocks(self):
        p=Policy(quantity_btc='1',max_buy_usd='10000',min_order_usd='1',max_book_spread_bps='10')
        self.assertIn('wide_bid_ask_spread',evaluate(policy=p)['routes'][0]['reasons'])

    def test_13_adverse_stress_monotonic(self):
        a=evaluate()['routes'][0]['stress_scenarios']
        vals=[Decimal(x['net_usd']) for x in a]
        self.assertEqual(vals,sorted(vals,reverse=True))

    def test_14_pessimistic_stress_blocks(self):
        p=Policy(quantity_btc='1',max_buy_usd='1000',min_order_usd='1',
                 buy_fee_bps='10',sell_fee_bps='10',base_stress_bps='0',
                 depth_haircut_bps='0',min_net_profit_usd='0',
                 min_net_edge_bps='0',min_stressed_net_usd='25')
        self.assertIn('stress_case_not_profitable',evaluate(policy=p)['routes'][0]['reasons'])

    def test_15_sell_break_even_is_positive(self):
        self.assertGreater(Decimal(evaluate()['routes'][0]['break_even_sell_vwap_usd']),0)

    def test_16_uses_multiple_levels(self):
        p=Policy(quantity_btc='3',max_buy_usd='1000',min_order_usd='1',
                 depth_haircut_bps='0')
        r=evaluate(policy=p,ask_size='1',bid_size='1')
        self.assertEqual(r['routes'][0]['levels_consumed'],{'buy':3,'sell':3})
        self.assertGreater(Decimal(r['routes'][0]['buy_vwap_usd']),Decimal('101'))

    def test_17_no_mutation_to_input(self):
        data,now=books(); cb=parse_book('coinbase',data[COINBASE],now); kr=parse_book('kraken',data[KRAKEN],now)
        prior=repr((cb,kr))
        evaluate_books(cb,kr,policy=Policy(max_buy_usd='1000'),asof=now)
        self.assertEqual(prior,repr((cb,kr)))

    def test_18_stale_receipt_rejected(self):
        data,now=books(); old=now-timedelta(seconds=30)
        cb=parse_book('coinbase',data[COINBASE],old);kr=parse_book('kraken',data[KRAKEN],old)
        with self.assertRaisesRegex(ResearchError,'stale'):
            evaluate_books(cb,kr,asof=now)

    def test_19_stale_source_rejected(self):
        with self.assertRaisesRegex(ResearchError,'stale'):
            evaluate(seconds_old=30)

    def test_20_receiver_skew_rejected(self):
        data,now=books();cb=parse_book('coinbase',data[COINBASE],now)
        kr=parse_book('kraken',data[KRAKEN],now-timedelta(seconds=6))
        p=Policy(max_age_seconds='10',max_receipt_skew_seconds='5')
        with self.assertRaisesRegex(ResearchError,'skew'):
            evaluate_books(cb,kr,policy=p,asof=now)

    def test_21_timestamp_without_tz_rejected(self):
        data,now=books();cb=parse_book('coinbase',data[COINBASE],now);kr=parse_book('kraken',data[KRAKEN],now)
        cb['received']=datetime.now()
        with self.assertRaisesRegex(ResearchError,'timezone'):
            evaluate_books(cb,kr,asof=now)

    def test_22_unrecognized_provider_rejected(self):
        data,now=books();cb=parse_book('coinbase',data[COINBASE],now);kr=parse_book('kraken',data[KRAKEN],now)
        kr['provider']='fake'
        with self.assertRaises(ResearchError): evaluate_books(cb,kr,asof=now)

    def test_23_duplicated_price_level_rejected(self):
        data,now=books();cb=parse_book('coinbase',data[COINBASE],now);kr=parse_book('kraken',data[KRAKEN],now)
        cb['asks']=(cb['asks'][0], cb['asks'][0])
        with self.assertRaisesRegex(ResearchError,'ordering'):
            evaluate_books(cb,kr,asof=now)

    def test_24_nan_level_rejected(self):
        data,now=books();cb=parse_book('coinbase',data[COINBASE],now);kr=parse_book('kraken',data[KRAKEN],now)
        cb['bids']=(('NaN','1'),)
        with self.assertRaises(ResearchError):evaluate_books(cb,kr,asof=now)

    def test_25_invalid_quantity_rejected(self):
        with self.assertRaises(ResearchError):Policy(quantity_btc='Infinity').validated()

    def test_26_excess_fee_rejected(self):
        with self.assertRaises(ResearchError):Policy(buy_fee_bps='6000').validated()

    def test_27_zero_step_rejected(self):
        with self.assertRaises(ResearchError):Policy(quantity_step_btc='0').validated()

    def test_28_inverted_order_limits_rejected(self):
        with self.assertRaises(ResearchError):Policy(min_order_usd='400',max_buy_usd='300').validated()

    def test_29_unreasonable_age_rejected(self):
        with self.assertRaises(ResearchError):Policy(max_age_seconds='900').validated()

    def test_30_missing_policy_field_rejected(self):
        d=asdict(Policy()); d.pop('max_buy_usd')
        with self.assertRaises(ResearchError):as_policy(d)

    def test_31_injected_unknown_policy_field_rejected(self):
        d=asdict(Policy()); d['override_risk']='yes'
        with self.assertRaises(ResearchError):as_policy(d)

    def test_32_modified_profit_rejected(self):
        r=evaluate();r['routes'][0]['base_estimated_net_usd']='999999'
        self.assertFalse(verify_replay(r))

    def test_33_rehashed_forgery_rejected(self):
        r=evaluate();r['routes'][0]['base_estimated_net_usd']='999999'
        r.pop('evidence_sha256');r['evidence_sha256']=sha256(r)
        self.assertFalse(verify_replay(r))

    def test_34_removed_book_rejected(self):
        r=evaluate();r['books'].pop()
        self.assertFalse(verify_replay(r))

    def test_35_malformed_book_rejected(self):
        r=evaluate();r['books'][0]['asks'][0][0]='NaN'
        r.pop('evidence_sha256');r['evidence_sha256']=sha256(r)
        self.assertFalse(verify_replay(r))

    def test_36_integrity_tip_and_chain(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'evidence.db')
            ledger=EvidenceLedger(path)
            r=evaluate(); ledger.append(r)
            self.assertTrue(ledger.verify())
            ledger.db.execute('UPDATE evidence SET payload = ? WHERE seq=1', ('{}',))
            self.assertFalse(ledger.verify())
            with self.assertRaises(ResearchError):ledger.append(r)
            ledger.close()

    def test_37_spot_scanner_uses_only_public_urls(self):
        payloads,now=books()
        calls=[]
        def fetch(url): calls.append(url);return payloads[url]
        r=scan_market(policy=Policy(max_buy_usd='1000'),fetcher=fetch,clock=lambda:now)
        self.assertTrue(verify_replay(r))
        self.assertCountEqual(calls,[COINBASE,KRAKEN])

    def test_38_scanner_fails_if_one_venue_fails(self):
        data,now=books()
        def fetch(url):
            if url == KRAKEN: raise ResearchError('exchange down')
            return data[url]
        with self.assertRaises(ResearchError):
            scan_market(fetcher=fetch,clock=lambda:now)

    def test_39_synthetic_profit_is_not_realized(self):
        r=evaluate()
        self.assertIsNone(r['realized_profit_usd'])
        self.assertIsNone(r['routes'][0]['realized_profit_usd'])
        self.assertTrue(Decimal(r['routes'][0]['base_estimated_net_usd']) > 0)

    def test_40_replayed_forged_executable_flag_rejected(self):
        r=evaluate();r['routes'][0]['trade_allowed']=True
        r.pop('evidence_sha256');r['evidence_sha256']=sha256(r)
        self.assertFalse(verify_replay(r))


if __name__=='__main__':unittest.main()
