# Backtest Report

Run date: 2026-07-16

## Main Result

The main implementation follows the stated strategy rule: rebalance on the last trading day of April, August, October, and December. Signals use the previous trading day, execution uses the daily open as the closest available proxy for 09:31, and financial statements use the `original_report` point-in-time mode.

```bash
conda run -n hldb python scripts/run_backtest.py --start 2020-04-30 --end 2026-04-30 --financial-mode original_report --trade-at open --initial-capital 1000000 --min-commission 5 --output-dir outputs/backtest_hldb_2020_major_risks_fixed_open
```

| Metric | Value |
|---|---:|
| Total return | 124.56% |
| Annual return | 15.05% |
| Annual volatility | 14.18% |
| Sharpe-like ratio | 1.06 |
| Max drawdown | -12.45% |
| Rebalances | 25 |
| Total turnover | 21.58 |
| Recorded cost drag | 1.94% |

Recorded costs include 0.03% buy commission, 0.03% sell commission, 0.10% sell stamp tax, and a CNY 5 minimum commission per order for a CNY 1 million account. Slippage remains zero, matching the reference script but not a conservative live-trading assumption.

## NAV Comparison

![NAV comparison](../reports/nav_comparison.png)

The close-execution sensitivity run returned 123.65%, only 0.91 percentage points below the open-execution run.

The reference comments say to trade on the last trading day of each target month, but the actual condition only fires when the natural calendar month-end is itself a trading day. Reproducing that condition gives 17 rebalances and a 93.69% total return. This is shown as `Reference code calendar / open` in the chart.

## Return And Account Controls

- Adjusted returns use the official `dwd_quant_adj_factor_eod_1day_di` factor.
- The denominator uses the prior actual raw close, not the exchange-adjusted `prev_close` field.
- Position weights drift with returns; there is no free daily rebalance.
- Filtered or blocked targets leave cash instead of renormalizing surviving stocks to 100%.
- Missing held-stock returns and adjusted returns above 100% fail fast.
- Zero-volume rows are not tradable.
- Dividend inputs require `info_pub_date <= signal_date` and use raw cash per 10 shares.

Across 145,568 held stock-days in the main run, adjusted-return missing values were zero and the largest absolute stock return was 20.03%. The largest stock weight was 4.34%, below the 5% cap.

## Remaining Limitation

The local cache does not contain matching one-minute history for strict 09:31 fills and the reference 14:00 limit-open check. Daily open and close runs bound this timing sensitivity, but minute-level execution cannot be reproduced without minute bars. Market-impact slippage is also not yet modeled.
