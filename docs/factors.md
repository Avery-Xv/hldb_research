# Factor Layer

The factor layer is implemented in `src/hldb/factors.py` through `FactorEngine`.

## Run One Signal Date

```bash
conda activate hldb
python scripts/select_portfolio.py 2020-04-30 --output outputs/portfolio_2020-04-30.csv
```

For a quick diagnostic run:

```bash
python scripts/smoke_factors.py
```

## Implemented Pipeline

1. `base_universe(signal_date)` starts from the cleaned A-share universe, removes BSE, STAR Market, stocks listed less than 90 days, suspended stocks, and ST-like name records.
2. `liquidity_factors()` calculates `total_mv`, `turnover_rate`, and 252-day average daily money.
3. `apply_liquidity_filter()` removes the bottom 20% by market value and the bottom 20% by average money.
4. `dividend_indicators()` calculates dividend payout ratio and TTM dividend yield.
5. `volatility_factors()` calculates annualized 252-day volatility from cleaned close returns.
6. `candidate_factors()` applies payout, high-volatility, and top-300 dividend-yield filters, then joins low-volatility and quality factors.
7. `score_and_weight()` builds decile scores, selects 100 stocks, and normalizes capped weights.

## Current Replication Choices

- Dividend cash fields are treated as cash per 10 shares, so per-share dividend is `cash_divi_rmb_adj / 10`.
- Dividend records use `info_pub_date <= signal_date` for point-in-time availability.
- Financial statements use consolidated records with `ann_date <= signal_date`; for each `symbol, period_end`, the latest visible version is kept.
- `if_adjusted` is only a deterministic tie-breaker when the same announcement date has multiple versions.
- Limit-up/down filters are not applied inside the factor layer. They should be applied in the later trading/backtest layer with `DataPortal.stock_limits()`.

## Important Gaps

- Dividend field units have been sanity-checked, but should still be cross-checked against several known cash-dividend examples before final performance attribution.
- Quality factors currently use the latest PIT financial indicator row per symbol for ROE, cash-flow ratio, and net-profit growth acceleration.
- This layer generates target weights; it does not simulate orders, taxes, commissions, suspended exits, or intraday limit-open behavior.
