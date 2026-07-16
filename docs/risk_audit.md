# Backtest Risk Audit

## Closed in code

- Adjusted returns use the official `dwd_quant_adj_factor_eod_1day_di` factor and the prior actual raw close. Exchange-adjusted `prev_close` is not adjusted twice.
- Missing adjusted returns and held-stock returns above 100% fail fast instead of becoming zero or entering NAV silently.
- Position weights drift with returns. The account no longer performs a free daily rebalance.
- Stocks removed by ST, suspension, zero-volume, or limit filters leave cash; surviving targets are not renormalized to 100%.
- Existing target positions are only rebalanced when their weight gap exceeds 0.5%, matching the reference script.
- Trading costs include commission, sell stamp tax, and a CNY 5 minimum commission per order. The cash capital is explicit and configurable.
- Dividend-yield inputs require `info_pub_date <= signal_date`, use a trailing 365-day visible-announcement window, and use raw cash per 10 shares. The DWD event table has no duplicate active `(symbol, end_date)` schemes.

## Remaining data limitation

The reference trades at 09:31 and checks limit reopening at 14:00. The cache has daily bars but no matching 1-minute history, so `--trade-at open` remains an execution proxy. Run the same backtest with `--trade-at close` as a timing sensitivity bound. This limitation cannot be removed without minute bars.

## Validation

Run:

```bash
conda run -n hldb pytest -q tests/test_adjusted_returns.py
conda run -n hldb python scripts/run_backtest.py --start 2020-04-30 --end 2026-04-30 --financial-mode original_report --trade-at open --initial-capital 1000000 --min-commission 5 --output-dir outputs/backtest_hldb_2020_major_risks_fixed_open
conda run -n hldb python scripts/run_backtest.py --start 2020-04-30 --end 2026-04-30 --financial-mode original_report --trade-at close --initial-capital 1000000 --min-commission 5 --output-dir outputs/backtest_hldb_2020_major_risks_fixed_close
```
