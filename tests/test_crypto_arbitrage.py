import asyncio
import unittest

from strategies.crypto_arbitrage import CryptoArbitrage


class CryptoArbitrageTests(unittest.TestCase):
    def setUp(self):
        self.engine = CryptoArbitrage({"max_notional_usd": "300", "min_net_edge_bps": "20"})

    def quote(self, **overrides):
        args = dict(buy_ask_usd="100", sell_bid_usd="103", quantity="1",
                    buy_fee_bps="10", sell_fee_bps="10", slippage_bps="5")
        args.update(overrides)
        return self.engine.evaluate_quote(**args)

    def test_live_execution_fails_closed(self):
        result = asyncio.run(self.engine.execute_trade({"symbol": "BTC-USD"}))
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["live_order_submitted"])
        self.assertIsNone(result["order_id"])
        self.assertIsNone(result["realized_profit_usd"])

    def test_scan_does_not_invent_market_data(self):
        self.assertEqual(asyncio.run(self.engine.scan_opportunities()), [])

    def test_paper_quote_is_not_realized_profit(self):
        result = self.quote()
        self.assertEqual(result["status"], "paper_quote")
        self.assertTrue(result["candidate_only"])
        self.assertFalse(result["live_order_submitted"])
        self.assertIsNone(result["realized_profit_usd"])

    def test_unprofitable_quote_is_not_candidate(self):
        result = self.quote(sell_bid_usd="100")
        self.assertFalse(result["candidate_only"])
        self.assertLess(float(result["estimated_net_usd"]), 0)

    def test_position_limit(self):
        result = self.quote(quantity="4")
        self.assertFalse(result["within_notional_limit"])
        self.assertFalse(result["candidate_only"])

    def test_rejects_nan_and_negative(self):
        for invalid in ["NaN", "Infinity", "-5", "0", "text"]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.quote(quantity=invalid)

    def test_rejects_excess_fee(self):
        with self.assertRaises(ValueError):
            self.quote(buy_fee_bps="10000")

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            CryptoArbitrage({"max_notional_usd": "-1"})


if __name__ == "__main__":
    unittest.main()
