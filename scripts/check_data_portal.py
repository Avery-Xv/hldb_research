from __future__ import annotations

from datetime import date

import polars as pl

from hldb import DataPortal


def assert_one_row(df: pl.DataFrame, label: str) -> None:
    if df.height != 1:
        raise AssertionError(f"{label}: expected one-row aggregate, got {df.height}")


def main() -> None:
    portal = DataPortal()
    signal_date = "2020-04-30"

    daily = portal.canonical_daily()
    daily_check = daily.select(
        pl.len().alias("rows"),
        pl.struct(["trade_date", "symbol"]).n_unique().alias("keys"),
        pl.col("symbol").n_unique().alias("symbols"),
    ).collect()
    assert_one_row(daily_check, "canonical_daily")
    row = daily_check.row(0, named=True)
    assert row["rows"] == row["keys"], row

    basic = portal.daily_basic()
    basic_check = basic.select(
        pl.len().alias("rows"),
        pl.struct(["trade_date", "symbol"]).n_unique().alias("keys"),
    ).collect()
    assert_one_row(basic_check, "daily_basic")
    row = basic_check.row(0, named=True)
    assert row["rows"] == row["keys"], row

    rebalance_count = portal.rebalance_dates().select(pl.len()).collect().item()
    assert rebalance_count > 0, "no rebalance dates"

    income = portal.latest_visible_income(signal_date)
    income_check = income.select(
        pl.len().alias("rows"),
        pl.struct(["symbol", "period_end"]).n_unique().alias("keys"),
        (pl.col("ann_date") > pl.lit(date(2020, 4, 30))).sum().alias("future_rows"),
        (pl.col("if_merged") != 1).sum().alias("non_merged_rows"),
    ).collect()
    assert_one_row(income_check, "latest_visible_income")
    row = income_check.row(0, named=True)
    assert row["rows"] == row["keys"], row
    assert row["future_rows"] == 0, row
    assert row["non_merged_rows"] == 0, row

    print("data portal checks passed")
    print("canonical_daily", daily_check)
    print("daily_basic", basic_check)
    print("latest_visible_income", income_check)


if __name__ == "__main__":
    main()
