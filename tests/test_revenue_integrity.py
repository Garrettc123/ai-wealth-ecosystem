import asyncio
import unittest

from wealth_generator import WealthGenerator


class RevenueIntegrityTests(unittest.TestCase):
    def test_targets_are_not_realized_revenue(self):
        engine = WealthGenerator()
        engine.initialize_streams()
        status = engine.get_status()
        self.assertEqual(status["total_earnings"], 0.0)
        self.assertEqual(status["active_streams"], 0)
        self.assertEqual(status["monthly_projection"], 0)
        self.assertTrue(all(s["status"] == "unverified" for s in status["streams"]))

    def test_no_receipts_means_zero_earned(self):
        engine = WealthGenerator()
        engine.initialize_streams()
        asyncio.run(engine.run_income_cycle())
        self.assertEqual(engine.total_earnings, 0.0)

    def test_legacy_income_strategy_never_mints_money(self):
        engine = WealthGenerator()
        engine.initialize_streams()
        stream = engine.income_streams[0]
        self.assertEqual(engine._passive_income_strategy(stream), 0.0)
        self.assertEqual(engine._active_income_strategy(stream), 0.0)


if __name__ == "__main__":
    unittest.main()
