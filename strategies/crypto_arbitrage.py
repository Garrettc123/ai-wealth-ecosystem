#!/usr/bin/env python3
"""Fail-closed crypto arbitrage evaluator for Garcar.

This module does not talk to exchanges or X Money and NEVER places an order.
A quote is not a fill or realized profit. Use only verified exchange data and
manual owner-authorized execution when a separately audited adapter exists.
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Dict, List

logger = logging.getLogger(__name__)


def _decimal(value, name: str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        raise ValueError(f"{name} must be {'nonnegative' if allow_zero else 'positive'} and finite")
    return result


class CryptoArbitrage:
    """Evaluate a potential spread, without suggesting an order actually filled."""

    def __init__(self, config: Dict):
        self.config = dict(config)
        self.exchanges = []  # No real exchange adapter is wired yet.
        self.opportunities = []
        self.max_notional_usd = _decimal(
            self.config.get("max_notional_usd", "100"), "max_notional_usd"
        )
        self.min_net_edge_bps = _decimal(
            self.config.get("min_net_edge_bps", "20"),
            "min_net_edge_bps", allow_zero=True
        )

    async def scan_opportunities(self) -> List[Dict]:
        """Return no opportunities until real, authenticated market feeds exist."""
        logger.warning("Exchange data feeds are not configured; no scan performed")
        self.opportunities = []
        return []

    def evaluate_quote(
        self,
        *,
        buy_ask_usd,
        sell_bid_usd,
        quantity,
        buy_fee_bps,
        sell_fee_bps,
        slippage_bps,
    ) -> Dict:
        """Compute illustrative net edge for verified external quotes.

        Slippage is charged against BOTH legs. This is a theoretical estimate,
        not an actual arbitrage opportunity, guaranteed result, or order.
        """
        ask = _decimal(buy_ask_usd, "buy_ask_usd")
        bid = _decimal(sell_bid_usd, "sell_bid_usd")
        qty = _decimal(quantity, "quantity")
        buy_fee = _decimal(buy_fee_bps, "buy_fee_bps", allow_zero=True) / 10000
        sell_fee = _decimal(sell_fee_bps, "sell_fee_bps", allow_zero=True) / 10000
        slippage = _decimal(slippage_bps, "slippage_bps", allow_zero=True) / 10000
        if slippage >= 1 or buy_fee >= 1 or sell_fee >= 1:
            raise ValueError("Fees and slippage must each be below 100%")

        buy_cost = ask * qty * (1 + slippage) * (1 + buy_fee)
        sell_proceeds = bid * qty * (1 - slippage) * (1 - sell_fee)
        net = sell_proceeds - buy_cost
        edge_bps = (net / buy_cost) * 10000
        within_limit = buy_cost <= self.max_notional_usd
        candidate = within_limit and edge_bps >= self.min_net_edge_bps
        return {
            "status": "paper_quote",
            "live_order_submitted": False,
            "candidate_only": bool(candidate),
            "within_notional_limit": bool(within_limit),
            "buy_cost_usd": str(buy_cost),
            "sell_proceeds_usd": str(sell_proceeds),
            "estimated_net_usd": str(net),
            "estimated_net_edge_bps": str(edge_bps),
            "realized_profit_usd": None,
            "order_id": None,
            "warning": "Market movement, liquidity, transfers and fees can invalidate this estimate.",
        }

    async def execute_trade(self, opportunity: Dict) -> Dict:
        """Reject live orders until an audited, owner-approved adapter exists.

        Crucially, never return success when no exchange order was placed.
        """
        logger.warning("Live order rejected: no exchange execution adapter configured")
        return {
            "status": "blocked",
            "reason": "No live exchange adapter or owner-authorized order execution is configured",
            "live_order_submitted": False,
            "order_id": None,
            "realized_profit_usd": None,
        }
