# Garcar AEGIS V2 — Evidence-first BTC/USD Market Research

**Build status:** local unit tests green, **not production trading-ready**. No private keys, account-linked funds, orders, or profit claims.

This package extends the original Garcar AEGIS V1 research engine. It evaluates Coinbase Advanced and Kraken Spot **public** BTC/USD order-book data as research observations; it does not execute trades or connect X Money.

## Installation / run

Requires Python 3.11+ (standard library only). Run from the extracted `garcar_aegis` directory:

```bash
python -m unittest discover -s tests -v
python -m strategies.aegis_v2 --quantity-btc 0.001 --max-usd 300 --output latest.json --ledger local-evidence.sqlite3
python -m strategies.aegis_v2 --replay latest.json
```

The scanner performs two HTTPS requests to Coinbase and Kraken and fails if either is unavailable, invalid, or stale. Both exchange URLs are allowlisted in `strategies/aegis_scanner.py`. Network access is required for a live research snapshot, not for unit tests/replay. No private trading API routes exist.

### Configurable parameters

In addition to CLI quantity, cap, and fee assumptions, the frozen `Policy` object provides:

- Base size increment and minimum order notional (the defaults are assumptions, not verified exchange constraints).
- Per-venue simulated buy and sell fee rates in basis points.
- Adverse execution stress per leg, with 0/10/25/50/100/250 bps incremental shocks.
- Depth-size haircut to avoid treating all displayed liquidity as certain.
- Minimum mathematical profit, net return, and worst-case net threshold.
- Maximum allowed within-venue spread, cross-venue midpoint divergence, receipt staleness, and clock skew.

Use `Policy` programmatically for extended policy fields. The CLI offers a conservative subset. All of these are **research-screening controls**, not guarantees against execution or market loss.

## 5 × 5 upgrade matrix (capabilities, not a return multiplier)

| Layer | Five implemented enhancements |
| --- | --- |
| Market data | Allowed public feeds; sorted 25-level depth validation; exchange/receipt staleness checks; receipt synchronization; crossed-book/quote-dislocation rejection |
| Pricing | Increment-based rounding; depth liquidity haircut; multiprice VWAP; separate buy/sell fee assumptions; break-even sell VWAP |
| Risk screening | Gross/max cost cap; minimum order check; minimum profit+edge thresholds; six adverse-stress scenarios; worst-case profitability gate |
| Forensics | Canonical record; source-book fingerprint; SHA-256 evidence digest; deterministic replay of derived values; locally hash-linked SQLite evidence with separately saved tip |
| Operations | No credentials required; bounded policy schema; explicit no-trade/no-inventory flags; no partial-data fallback; offline adversarial regression suite |

## What the scanner **does not** establish

The default order increments, order minimums, fee rates, liquidity haircut, and hypothetical stress assumptions **are not verified for a particular funded account**. Positive mathematical estimates are *not* accessible profit. Exchange executions may slip, fail, or result in partial fills; simulated snapshots are not executable. Cross-exchange arbitrage generally needs funded inventory at both venues. Transfer and tax consequences are excluded. No brokerage eligibility, geography, X Money compatibility or available capital is established.

Kraken REST depth lacks a whole-book timestamp, so its receipt timestamp is used as a rough freshness proxy. Coinbase exchange snapshot time is checked where provided. REST snapshots are not atomic across exchanges; order books may change instantly. This engine is designed to flag issues, not make actionable trading recommendations.

## Evidence and limitations

- `verify_replay()` reproduces all derived fields from an unchanged report and fails on manipulated profit or trade flags, including after SHA rehashing if source data is inconsistent.
- `EvidenceLedger` is a local hash chain, not immutable storage. Save the final chain tip **outside** the SQLite file and compare using `strategies.aegis_verify` to detect truncation/tampering. An adversary who controls both database and anchor can forge both.
- Static tests do not measure market-data uptime, latency performance, real slippage, net profit, multi-day resilience, or live trading outcomes.
- Tests use **synthetic price fixtures** with arbitrary price levels, intentionally not real BTC quotes.
- No order, withdrawal, transfer, exchange credential handling or unsupervised money movement is implemented.

## Live-execution acceptance gates (not completed)

1. Verified exchange accounts, read-only authenticated balances, fee schedules, jurisdiction and pair availability.
2. Reproducible historical order-book replay and walk-forward sampling using **captured** real data, with latency and outage distributions and cost sensitivity.
3. Independent strategy validation including unbiased sampling, drawdowns, adverse selection, transfer costs, and out-of-sample expectancy.
4. Audited order-preview + owner-confirmed limit-order flow, idempotency, circuit breaker, no withdrawal permissions and isolated credentials.
5. Settlement and fee reconciliation from exchange-confirmed fills into a protected ledger; full incident and recovery plan.

Until these are met, the system must remain read-only. This is not a profit-guaranteeing autonomous trader.

### Public API references

- Coinbase Advanced public product book: https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/public/get-public-product-book
- Kraken Spot REST order book: https://docs.kraken.com/api/docs/rest-api/get-order-book
