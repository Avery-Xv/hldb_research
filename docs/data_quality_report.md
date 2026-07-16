# Data Quality Report

Validation date: 2026-07-15

Run with:

```bash
conda activate hldb
python scripts/validate_data_quality.py
```

## Summary

The cached data is sufficient to start a local replication of the dividend low-volatility strategy from 2015 onward. Core coverage is present for daily prices, daily valuation, tail 30-minute turnover, dividends, financial indicators, trading status, limits, and the SSE trading calendar.

Before strategy implementation, add a data-cleaning layer. The raw files contain duplicate market rows, mixed symbol formats in the 30-minute tail file, and multiple financial statement versions.

## Coverage

- `stock_daily_2014.parquet`: 14,659,833 rows, 5,539 symbols, 2014-01-02 to 2026-07-10.
- `daily_basic_2015.parquet`: 11,409,140 rows, 5,862 symbols, 2015-01-05 to 2026-07-10.
- `tail_30m_money_2015.parquet`: 11,625,360 rows, 5,783 symbols, 2015-01-05 to 2026-07-13.
- `trade_calendar_2014.parquet`: SSE calendar, 2014-01-01 to 2026-07-11.
- Financial data reaches 2026Q1; income announcements reach 2026-05-29.

All 4/8/10/12 month-end rebalance dates from 2015 through available 2026 data have daily price, daily basic, tail 30-minute, and limit-table coverage.

## Blocking Issues To Handle

1. `stock_daily_2014.parquet` has 2,783,592 duplicate `(trade_date, symbol)` groups. OHLC/close are consistent, but 1,797,028 groups have one row with valid limit/trade fields and one row with `limit_up = limit_down = 0`, `num_trades = 0`. Keep the row with positive `limit_up`, `limit_down`, and `num_trades` where available.

2. Even after deduping daily rows by that rule, 1,739,698 rows still have invalid daily limit fields. Do not use `stock_daily.limit_up/limit_down` for trading filters. Use `stk_limit_2015.parquet`, which has unique `(trade_date, symbol)` keys.

3. `daily_basic_2015.parquet` has 22,088 duplicate `(trade_date, symbol)` groups. They are exact duplicates for checked pricing, market value, and turnover fields; safe to `unique(["trade_date", "symbol"])`.

4. `income_jydb_2012.parquet` and `cashflow_jydb_2012.parquet` contain multiple versions per statement key. For strategy factors, filter to consolidated statements first (`if_merged = 1`), then choose a deterministic adjusted-version priority before PIT selection.

5. `tail_30m_money_2015.parquet` contains 276,529 rows in suffix format such as `920001.BJ`. These are mainly Beijing Exchange records. Since the strategy excludes BSE, either filter them out or normalize before joining.

## Non-Blocking Notes

- `stock_universe_jydb.parquet` has 23 duplicate symbols and 9 rows with `listed_date = 1970-01-01`; these are mostly pending or special listings. Exclude rows with invalid listed dates and use market/listing status filters.
- `fin_indicator_2012.parquet` has no duplicate PIT keys and no bad valid intervals, but `valid_to` is the sentinel `2149-06-06` for all rows. Treat `valid_from <= signal_date` as the practical PIT rule.
- Active dividend rows mostly have usable cash fields. Among `if_dividend = 1` rows, `cash_divi_rmb` is null in 720 rows and `total_cash_divi` is null in 722 rows. Dividend unit checks are still required before final factor implementation.

## Recommended Cleaning Contract

- Normalize and filter symbols to `^(SH|SZ|BJ)[0-9]{6}$`, then exclude BSE and STAR Market by `secu_market/listed_sector`.
- Build canonical daily prices by sorting duplicate rows by valid limit fields, valid `num_trades`, and `money`, then taking one row per `(trade_date, symbol)`.
- Build canonical daily basic data with exact duplicate removal.
- Use `stk_limit_2015.parquet` for limit-up/limit-down filters.
- Use `suspend_2015.parquet` for suspension filters.
- Use PIT financial records only where announcement or valid-from dates are no later than the signal date.
