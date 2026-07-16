# Data Portal

The local cleaning layer lives in `src/hldb/` and is exposed through `hldb.DataPortal`.

## Setup

```bash
conda activate hldb
python -m pip install -e .
```

`scripts/setup_hldb_env.sh` also installs the package in editable mode.

## Core Accessors

- `DataPortal().rebalance_dates()` returns the last SSE trading day of April, August, October, and December.
- `eligible_universe(signal_date)` returns listed A-share candidates after invalid listing dates, Beijing Exchange, STAR Market, and new-stock filters.
- `canonical_daily()` deduplicates `stock_daily_2014.parquet` by preferring rows with valid limit fields, valid `num_trades`, and larger `money`.
- `daily_window(end_date, count, symbols=None)` returns the last `count` cleaned daily bars per symbol.
- `daily_basic(signal_date=None, symbols=None)` removes exact duplicate valuation rows.
- `tail_30m(signal_date=None, symbols=None)` filters symbols to canonical `SH/SZ/BJ` format.
- `stock_limits()` and `suspensions()` provide trading filter inputs from dedicated event tables.
- `latest_visible_income(signal_date)` and `latest_visible_cashflow(signal_date)` use consolidated statements with `ann_date <= signal_date`, then keep the latest visible record per `symbol, period_end`.
- `fin_indicator_pit(signal_date)` uses `valid_from <= signal_date` and keeps the latest visible record per `symbol, period_end`.

## Validation

```bash
python scripts/smoke_data_portal.py
python scripts/check_data_portal.py
```

The check script verifies cleaned daily keys, cleaned daily-basic keys, and no future or non-consolidated records in latest-visible income data.
