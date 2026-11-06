# Garcar crypto scanner — operating status

**READ-ONLY / NO LIVE TRADING.** This is market-data integration, not X Money account linking, exchange funding, or an execution service.

## Implemented
- Coinbase Advanced BTC-USD and Kraken Spot XBTUSD public order-book readers (no credentials).
- Parallel two-venue reads, stale Coinbase timestamp checks, receipt-time checks for both venues, validation of quotes and top-level liquidity.
- Decimal math, maximum notional, configurable fee/slippage assumptions, two-direction indicative comparisons.
- Live execution deliberately returns "blocked" and cannot submit orders. No realized profit is claimed.

## Commands from repository root

    python -m unittest discover -s tests -p 'test_*.py' -v
    python -m strategies.public_market_scan --quantity 0.001 --max-notional-usd 300

The scanner requires outbound HTTPS access to both providers. The scan cannot trade. Coinbase and Kraken quote data does not prove a fundable, executable arbitrage opportunity.

## Limits
- The default assumptions are 100 bps trading fee **per leg** and 20 bps slippage **per leg**; these are NOT verified account fees. Supply researched values using --buy-fee-bps, --sell-fee-bps and --slippage-bps.
- Only best bid/ask levels are sampled. This does not model partial fills, trade minimums, market impact, transfers, tax, latency, inventory or account-specific permissions.
- A positive mathematical spread is not profit. Cross-venue arbitrage usually requires pre-funded inventory on both exchanges.
- Kraken order-book depth includes price-level timestamps, which are not authoritative timestamps of the whole book. Receipt time is used; exchange-side freshness remains unproven.
- X Money is a separate payment and deposit product. No authenticated X Money crypto-trading interface, deposit/withdrawal automation, or exchange compatibility was established.
- No API keys, seed phrases, withdrawals, auto-reinvestment, leverage, or unattended execution.

## Live trading gate (still closed)
1. Owner identifies and authorizes trading venue(s); verified KYC, market eligibility and actual balances required.
2. Least-privilege encrypted credentials in an appropriate secret manager, never in code or chat; withdrawal permission must remain off.
3. Authenticated read-only checks, actual fees, pre-trade previews, small order sizes, risk limits, emergency halt, and idempotency must be tested.
4. Owner must explicitly approve each particular amount, venue, and order. No unaudited autonomous execution.
5. Exchange-confirmed fills and actual charges must reconcile to a durable ledger. Submission is not a fill.

## Evidence
Exact source file Git blob hashes were matched locally to repository commits; 20 isolated stdlib unit tests passed locally. These tests use injected responses, not a live market or a funded account.
GitHub Actions crypto-guardrails workflow failed before any visible test steps; no job logs were available. CI success is not established.

References:
- https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/public/get-public-product-book
- https://docs.kraken.com/api/docs/rest-api/get-order-book
- https://money.x.com/en/i/faq


## Advanced AEGIS research layer (2026-11-05)

See `AEGIS_README.md`, `strategies/aegis_scanner.py`, `strategies/aegis_verify.py` and their dedicated tests. This adds multi-level depth, VWAP, fee and adverse-price stress, notional caps, explicit estimates-only reporting, local hash-chain evidence and external-tip verification. **27/27 local AEGIS tests passed** on exact source code matching the Git blob checksums. No orders, withdrawals, or profits occurred. GitHub Actions CI remains failed/unverified; do not describe this branch as production-ready. Read-only live API calls have not been confirmed from this environment.
