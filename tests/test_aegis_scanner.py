import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from strategies.aegis_scanner import (
    COINBASE, KRAKEN, ResearchError, EvidenceLedger, analyze,
    dec, get_public_json, parse_book, sha256, sweep,
)


def payloads(time=None):
    now = time or datetime.now(timezone.utc)
    cb = {"pricebook": {"product_id": "BTC-USD", "time": now.isoformat(),
                        "bids": [{"price": "101", "size": ".5"}, {"price": "100", "size": "2"}],
                        "asks": [{"price": "102", "size": ".5"}, {"price": "103", "size": "2"}]}}
    kr = {"error": [], "result": {"XXBTZUSD": {
        "bids": [["106", ".5", 1000], ["105", "2", 1000]],
        "asks": [["107", ".5", 1000], ["108", "2", 1000]]}}}
    return {COINBASE: cb, KRAKEN: kr}


class AegisTests(unittest.TestCase):
    def scan(self, data=None, now=None, **kwargs):
        now = now or datetime.now(timezone.utc)
        data = data or payloads(now)
        settings = dict(quantity="1", max_usd="500", fee_bps="10",
                        stress_bps="10", edge_bps="10")
        settings.update(kwargs)
        return analyze(fetcher=lambda url: data[url], clock=lambda: now, **settings)

    def test_multi_level_depth_sweep(self):
        book = ((dec("100", "p"), dec("0.5", "q")),
                (dec("103", "p"), dec("2", "q")))
        self.assertEqual(str(sweep(book, dec("1", "qty"))), "101.5")
        self.assertIsNone(sweep(book, dec("3", "qty")))

    def test_valid_math_and_no_real_trading(self):
        report = self.scan()
        self.assertEqual(report["schema"], "garcar.aegis.v1")
        self.assertTrue(report["comparisons"][0]["mathematical_candidate_only"])
        self.assertFalse(report["execution_enabled"])
        self.assertFalse(report["order_submitted"])
        self.assertIsNone(report["realized_profit_usd"])
        self.assertFalse(report["balance_verified"])
        self.assertFalse(report["fees_verified"])
        self.assertFalse(report["funding_link_verified"])
        self.assertTrue(report["evidence_sha256"])

    def test_second_direction_not_profitable(self):
        self.assertFalse(self.scan()["comparisons"][1]["mathematical_candidate_only"])

    def test_insufficient_depth_fails_closed(self):
        data = payloads()
        data[COINBASE]["pricebook"]["asks"] = [{"price": "102", "size": ".1"}]
        out = self.scan(data=data)
        self.assertIsNone(out["comparisons"][0]["estimated_net_usd"])
        self.assertFalse(out["comparisons"][0]["mathematical_candidate_only"])
        self.assertIn("insufficient_observed_depth", out["comparisons"][0]["blockers"])

    def test_cap_blocks_candidate(self):
        out = self.scan(max_usd="20")
        self.assertIn("notional_cap_exceeded", out["comparisons"][0]["blockers"])
        self.assertFalse(out["comparisons"][0]["mathematical_candidate_only"])

    def test_stress_blocks_candidate(self):
        out = self.scan(stress_bps="300")
        self.assertIn("below_minimum_net_edge", out["comparisons"][0]["blockers"])

    def test_fixed_cost_blocks_candidate(self):
        out = self.scan(fixed_cost_usd="50")
        self.assertIn("below_minimum_net_edge", out["comparisons"][0]["blockers"])

    def test_invalid_numbers_all_rejected(self):
        for value in ("NaN", "Infinity", "-1", "0", "abc"):
            with self.subTest(value=value), self.assertRaises(ResearchError):
                dec(value, "test")

    def test_out_of_order_book_rejected(self):
        data = payloads()
        data[KRAKEN]["result"]["XXBTZUSD"]["bids"][1][0] = "106.1"
        with self.assertRaisesRegex(ResearchError, "not strictly ordered"):
            self.scan(data)

    def test_duplicate_prices_rejected(self):
        data = payloads()
        data[COINBASE]["pricebook"]["asks"][1]["price"] = "102"
        with self.assertRaises(ResearchError):
            self.scan(data)

    def test_crossed_book_rejected(self):
        data = payloads()
        data[KRAKEN]["result"]["XXBTZUSD"]["bids"][0][0] = "108"
        with self.assertRaisesRegex(ResearchError, "crossed"):
            self.scan(data)

    def test_coinbase_stale_timestamp_rejected(self):
        now = datetime.now(timezone.utc)
        data = payloads(now - timedelta(seconds=30))
        with self.assertRaisesRegex(ResearchError, "Stale"):
            self.scan(data, now)

    def test_received_time_skew_rejected(self):
        now = datetime.now(timezone.utc)
        calls = iter([now - timedelta(seconds=8), now, now])
        with self.assertRaisesRegex(ResearchError, "skew"):
            analyze(fetcher=lambda url: payloads(now)[url], clock=lambda: next(calls))

    def test_wrong_kraken_pair_rejected(self):
        data = payloads()
        data[KRAKEN]["result"]["BTCUSD"] = data[KRAKEN]["result"].pop("XXBTZUSD")
        with self.assertRaisesRegex(ResearchError, "pair mismatch"):
            self.scan(data)

    def test_unknown_venue_rejected(self):
        with self.assertRaises(ResearchError):
            parse_book("other", {}, datetime.now(timezone.utc))

    def test_network_allowlist_rejected(self):
        with self.assertRaisesRegex(ResearchError, "allowlisted"):
            get_public_json("https://bad.example/test")

    def test_partial_feed_failure_rejected(self):
        def fetch(url):
            if url == KRAKEN:
                raise ResearchError("bad exchange")
            return payloads()[url]
        with self.assertRaisesRegex(ResearchError, "bad exchange"):
            analyze(fetcher=fetch)

    def test_evidence_checksum_recomputes(self):
        report = self.scan()
        expected = report.pop("evidence_sha256")
        self.assertEqual(sha256(report), expected)

    def test_hash_chain_append_and_validate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "audit.db")
            ledger = EvidenceLedger(path)
            a = ledger.append(self.scan())
            b = ledger.append(self.scan())
            self.assertNotEqual(a, b)
            self.assertTrue(ledger.verify())
            ledger.close()
            reopened = EvidenceLedger(path)
            self.assertTrue(reopened.verify())
            reopened.close()

    def test_hash_chain_detects_payload_tamper(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = EvidenceLedger(str(Path(temp) / "audit.db"))
            ledger.append(self.scan())
            ledger.db.execute("UPDATE evidence SET payload='{}' WHERE seq=1")
            self.assertFalse(ledger.verify())
            with self.assertRaises(ResearchError):
                ledger.append(self.scan())
            ledger.close()

    def test_hash_chain_detects_deleted_middle_row(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = EvidenceLedger(str(Path(temp) / "audit.db"))
            ledger.append(self.scan())
            ledger.append(self.scan())
            ledger.append(self.scan())
            ledger.db.execute("DELETE FROM evidence WHERE seq=2")
            self.assertFalse(ledger.verify())
            ledger.close()

    def test_kraken_missing_time_is_unknown_not_assumed(self):
        report = self.scan()
        self.assertIsNone(report["books"][1]["exchange_time_utc"])


if __name__ == "__main__":
    unittest.main()
