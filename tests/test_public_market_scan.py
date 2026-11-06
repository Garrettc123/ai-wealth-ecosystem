import unittest
from datetime import datetime, timedelta, timezone

from strategies.public_market_scan import (
    COINBASE_BOOK_URL, KRAKEN_BOOK_URL, MarketDataError,
    parse_coinbase, parse_kraken, scan_once,
)


def data_pair(*, cb_bid="990", cb_ask="1000", kr_bid="1020", kr_ask="1030",
              cb_size="1", kr_size="1", seconds_old=0):
    timestamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds_old)).isoformat()
    coinbase = {
        "pricebook": {
            "product_id": "BTC-USD", "time": timestamp,
            "bids": [{"price": cb_bid, "size": cb_size}],
            "asks": [{"price": cb_ask, "size": cb_size}],
        },
    }
    kraken = {
        "error": [], "result": {"XXBTZUSD": {
            "bids": [[kr_bid, kr_size, 1720000000]],
            "asks": [[kr_ask, kr_size, 1720000000]],
        }},
    }
    return {COINBASE_BOOK_URL: coinbase, KRAKEN_BOOK_URL: kraken}


class PublicMarketScanTests(unittest.TestCase):
    def scan(self, **kwargs):
        data = kwargs.pop("payloads", data_pair())
        return scan_once(
            quantity=kwargs.pop("quantity", "0.1"),
            max_notional_usd=kwargs.pop("max_notional_usd", "300"),
            buy_fee_bps=kwargs.pop("buy_fee_bps", "10"),
            sell_fee_bps=kwargs.pop("sell_fee_bps", "10"),
            slippage_bps=kwargs.pop("slippage_bps", "2"),
            fetcher=lambda url: data[url],
            **kwargs,
        )

    def test_reads_both_real_exchange_response_shapes(self):
        result = self.scan()
        self.assertEqual(result["mode"], "public_market_data_only")
        self.assertEqual([r["venue"] for r in result["snapshots"]], ["coinbase", "kraken"])
        self.assertEqual(len(result["comparisons"]), 2)

    def test_spread_is_only_a_mathematical_candidate(self):
        result = self.scan()
        self.assertTrue(result["comparisons"][0]["mathematical_candidate_only"])
        self.assertFalse(result["live_order_submitted"])
        self.assertFalse(result["funding_verified"])
        self.assertFalse(result["cross_exchange_inventory_verified"])
        self.assertIsNone(result["realized_profit_usd"])
        self.assertIsNone(result["comparisons"][0]["estimate"]["realized_profit_usd"])

    def test_insufficient_book_depth_rejects_candidate(self):
        data = data_pair(kr_size="0.01")
        result = self.scan(payloads=data)
        self.assertFalse(result["comparisons"][0]["mathematical_candidate_only"])
        self.assertFalse(result["comparisons"][0]["top_of_book_liquidity_sufficient"])

    def test_old_coinbase_time_rejected(self):
        with self.assertRaisesRegex(MarketDataError, "stale"):
            self.scan(payloads=data_pair(seconds_old=30))

    def test_invalid_kraken_error_rejected(self):
        data = data_pair()
        data[KRAKEN_BOOK_URL]["error"] = ["EGeneral:Invalid arguments"]
        with self.assertRaises(MarketDataError):
            self.scan(payloads=data)

    def test_invalid_coinbase_payload_rejected(self):
        data = data_pair()
        data[COINBASE_BOOK_URL]["pricebook"]["bids"] = []
        with self.assertRaises(MarketDataError):
            self.scan(payloads=data)

    def test_invalid_price_rejected(self):
        data = data_pair(cb_ask="NaN")
        with self.assertRaises(MarketDataError):
            self.scan(payloads=data)

    def test_locked_orderbook_rejected(self):
        data = data_pair(cb_bid="1000", cb_ask="1000")
        with self.assertRaises(MarketDataError):
            self.scan(payloads=data)

    def test_negative_size_rejected(self):
        with self.assertRaises(ValueError):
            self.scan(quantity="-0.1")

    def test_notional_limit_rejects_candidate(self):
        result = self.scan(max_notional_usd="50")
        self.assertFalse(result["comparisons"][0]["mathematical_candidate_only"])

    def test_data_connection_failure_propagates(self):
        def broken(_url):
            raise MarketDataError("Unavailable")
        with self.assertRaises(MarketDataError):
            scan_once(fetcher=broken)

    def test_parsers_reject_wrong_product(self):
        data = data_pair()
        data[COINBASE_BOOK_URL]["pricebook"]["product_id"] = "ETH-USD"
        with self.assertRaises(MarketDataError):
            parse_coinbase(data[COINBASE_BOOK_URL], datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main()
