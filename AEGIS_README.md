# Garcar AEGIS — Research-Grade Crypto Market Evidence

**Build status:** Local Python verification passed (27/27 tests). Not production deployed. No authenticated exchange, X Money, wallet, or bank connectivity. **Zero real trades executed.**

## Purpose

AEGIS is a read-only Bitcoin/USD market-data research system that compares Coinbase Advanced and Kraken Spot. It is designed to reduce false positives in cross-exchange arbitrage screening and produce independently checkable observations. It does not claim profitable, executable opportunities.

## What is implemented

- Two real public-market API endpoint adapters requesting up to 25 price levels per venue. No API keys are used.
- Validation of ascending asks, descending bids, positive sizes, positive finite prices, correct instrument, and un-crossed books.
- Coinbase source timestamp check, receipt-time checks on both feeds, and cross-venue snapshot-skew rejection. Kraken's REST Depth has no book-wide server timestamp, so receipt time is not a replacement for one.
- Multi-level cumulative bid/ask notional, VWAP, price stress per leg, fee assumptions, fixed-cost assumptions, minimum edge, and a maximum buy-notional control.
- Both trade directions are evaluated. Estimates remain *hypothetical*, even when the arithmetic qualifies as a candidate.
- SHA-256 event checksum, local SQLite hash-linked append-only-by-application evidence, and an independent anchor verifier. It is **not immutable storage** and cannot prove a trade was executed.
- No order endpoints, signed transactions, deposits, withdrawals, loan/margin or short positions, private account data, API secrets, or autonomous trading.

## Run from the repository root

Python 3.11 or later; no third-party packages for AEGIS.

    python -m unittest discover -s tests -p 'test_aegis*.py' -v
    python -m strategies.aegis_scanner --quantity-btc 0.001 --max-usd 300 --fee-bps 100 --stress-bps 20 --edge-bps 20 --ledger garcar-aegis-evidence.sqlite3

The scanner emits JSON. The field `ledger_chain_digest` is the latest local evidence hash. Store that digest **outside** the SQLite database and ideally outside the same machine, then verify later by running the verifier using the observed digest:

    python -m strategies.aegis_verify --ledger garcar-aegis-evidence.sqlite3 --expected-tip 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

The shown digest is an **invalid example anchor**. Replace it with the **actual** `ledger_chain_digest` from your run. The verifier succeeds only when the independently retained anchor matches the current chain.

## Interpret results correctly

`mathematical_candidate_only = true` means the stated assumptions and observed depth passed the arithmetic gates; it **does not** establish available account balances, live quoted execution, contract settlement, net profits, permission to trade or exchange eligibility. `trade_allowed` always remains `false`; `realized_profit_usd` is always `null`.

The default 100 bps fee and 20 bps stress are illustrations, **not** verified Coinbase/Kraken tiers or measured slippage. Trading costs, inventory risks, market impact, order limits, and bank transfers can erase apparent edges. The fixed-cost assumption is zero unless explicitly set; zero does not mean transfers are free. Cross-exchange arbitrage usually requires pre-funded inventory on both platforms.

## Release gates before real-money execution

1. Connect owner-authorized exchange accounts; establish eligibility and independently verified balances with read-only access.
2. Verify actual account-specific fee schedules and min trade sizes, ensure enough funds/inventory on both venues, and model order quantity increments.
3. Benchmark latency and obtain authoritative market data with order-book sequence handling; use robust fill and outage models.
4. Add no-withdrawal least-privilege key controls, independent execution kill switch, per-order approvals, idempotency and maximum loss thresholds, and an external monitoring service.
5. Reconcile every actual fill, fee, refund and withdrawal with exchange receipts before recording realized profit.
6. Investigate and resolve GitHub Actions failures; passing offline unit tests alone does not verify production readiness.

## Known limits

The two snapshots are not synchronized, source freshness is not fully established for Kraken, only 25 levels are read, funding and account information are absent, and this module does not perform backtesting, continuous observation, external attestation, or actual execution. Local SQLite can be altered by anyone with write access; independently retained digest anchors can expose some post-hoc changes, not prevent them. GitHub Actions runs on this branch have been failing before test steps; CI is not green.

## Sources

- Coinbase product-book documentation: https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/public/get-public-product-book
- Kraken order-book documentation: https://docs.kraken.com/api-reference/market-data/get-order-book
- X Money FAQ: https://money.x.com/en/i/faq
