from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

from hldb.data_portal import DataPortal


def print_section(title: str) -> None:
    print(f"\n## {title}")


def show(df: pl.DataFrame, limit: int | None = None) -> None:
    if limit is not None:
        df = df.head(limit)
    print(df)


def dividend_base(portal: DataPortal) -> pl.LazyFrame:
    return (
        portal.scan("dividend")
        .filter(pl.col("symbol").str.contains(r"^(SH|SZ|BJ)[0-9]{6}$"))
        .with_columns(
            pl.col("cash_divi_rmb").cast(pl.Float64).alias("cash_per_10_raw"),
            pl.col("actual_cash_divi_rmb").cast(pl.Float64).alias("actual_cash_per_10_raw"),
            pl.col("cash_divi_rmb_adj").cast(pl.Float64).alias("cash_per_10_adj"),
            pl.col("bonus_share_ratio").cast(pl.Float64).alias("bonus_per_10_raw"),
            pl.col("tran_add_share_ratio").cast(pl.Float64).alias("transfer_per_10_raw"),
            pl.col("bonus_share_ratio_adj").cast(pl.Float64).alias("bonus_per_10_adj"),
            pl.col("tran_add_share_ratio_adj").cast(pl.Float64).alias("transfer_per_10_adj"),
            pl.col("total_cash_divi").cast(pl.Float64).alias("total_cash_divi_f"),
            pl.col("divi_base").cast(pl.Float64).alias("divi_base_f"),
            pl.col("ex_divi_ref_price").cast(pl.Float64).alias("ex_divi_ref_price_f"),
        )
    )


def canonical_raw_daily(portal: DataPortal) -> pl.LazyFrame:
    quality = (
        (pl.col("limit_up") > 0).cast(pl.Int8) * 4
        + (pl.col("limit_down") > 0).cast(pl.Int8) * 2
        + (pl.col("num_trades") > 0).cast(pl.Int8)
    )
    return (
        portal.scan("stock_daily")
        .filter(pl.col("symbol").str.contains(r"^(SH|SZ|BJ)[0-9]{6}$"))
        .with_columns(quality.alias("_quality_rank"))
        .sort(["trade_date", "symbol", "_quality_rank", "money"], descending=[False, False, True, True])
        .unique(["trade_date", "symbol"], keep="first", maintain_order=True)
        .drop("_quality_rank")
    )


def action_by_ex_date(portal: DataPortal, active_only: bool = False) -> pl.LazyFrame:
    div = dividend_base(portal)
    if active_only:
        div = div.filter(pl.col("if_dividend") == 1)
    return (
        div.filter(pl.col("ex_divi_date").is_not_null())
        .filter(pl.col("info_pub_date") <= pl.col("ex_divi_date"))
        .with_columns(
            pl.coalesce([pl.col("cash_per_10_adj"), pl.col("cash_per_10_raw")]).fill_null(0.0).alias(
                "cash_per_10_current"
            ),
            pl.coalesce([pl.col("cash_per_10_raw"), pl.col("actual_cash_per_10_raw")]).fill_null(0.0).alias(
                "cash_per_10_raw_effective"
            ),
            pl.col("bonus_per_10_raw").fill_null(0.0).alias("bonus_per_10_current"),
            pl.col("transfer_per_10_raw").fill_null(0.0).alias("transfer_per_10_current"),
            pl.col("bonus_per_10_raw").fill_null(0.0),
            pl.col("transfer_per_10_raw").fill_null(0.0),
        )
        .group_by(["symbol", "ex_divi_date"])
        .agg(
            pl.len().alias("event_rows"),
            (pl.col("if_dividend") == 1).sum().alias("active_event_rows"),
            (pl.col("if_dividend") != 1).sum().alias("non_active_event_rows"),
            pl.col("scheme_no").n_unique().alias("scheme_count"),
            pl.col("process").n_unique().alias("process_count"),
            pl.col("cash_per_10_current").sum().alias("cash_per_10_current"),
            pl.col("cash_per_10_raw_effective").sum().alias("cash_per_10_raw"),
            pl.col("bonus_per_10_current").sum().alias("bonus_per_10_current"),
            pl.col("transfer_per_10_current").sum().alias("transfer_per_10_current"),
            pl.col("bonus_per_10_raw").sum().alias("bonus_per_10_raw"),
            pl.col("transfer_per_10_raw").sum().alias("transfer_per_10_raw"),
            pl.col("ex_divi_ref_price_f").drop_nulls().last().alias("ex_divi_ref_price"),
            pl.col("info_pub_date").min().alias("first_info_pub_date"),
            pl.col("info_pub_date").max().alias("last_info_pub_date"),
        )
        .rename({"ex_divi_date": "trade_date"})
    )


def joined_ex_date_panel(portal: DataPortal) -> pl.LazyFrame:
    daily = canonical_raw_daily(portal)
    actions = action_by_ex_date(portal, active_only=False)
    return (
        daily.join(actions, on=["trade_date", "symbol"], how="inner")
        .with_columns(
            (1.0 + (pl.col("bonus_per_10_current") + pl.col("transfer_per_10_current")) / 10.0).alias(
                "share_factor_current"
            ),
            (1.0 + (pl.col("bonus_per_10_raw") + pl.col("transfer_per_10_raw")) / 10.0).alias(
                "share_factor_raw"
            ),
            (pl.col("cash_per_10_current") / 10.0).alias("cash_per_share_current"),
            (pl.col("cash_per_10_raw") / 10.0).alias("cash_per_share_raw"),
        )
        .with_columns(
            ((pl.col("prev_close") - pl.col("cash_per_share_current")) / pl.col("share_factor_current")).alias(
                "prev_adj_current_formula"
            ),
            ((pl.col("prev_close") - pl.col("cash_per_share_raw")) / pl.col("share_factor_raw")).alias(
                "prev_adj_raw_formula"
            ),
        )
        .with_columns(
            (pl.col("close") / pl.col("prev_close").replace(0, None) - 1.0).alias("raw_close_return"),
            (pl.col("close") / pl.col("prev_adj_current_formula").replace(0, None) - 1.0).alias(
                "current_formula_return"
            ),
            (pl.col("close") / pl.col("prev_adj_raw_formula").replace(0, None) - 1.0).alias("raw_formula_return"),
            (pl.col("close") / pl.col("ex_divi_ref_price").replace(0, None) - 1.0).alias("ref_price_return"),
            pl.when((pl.col("prev_close") > 0) & (pl.col("ex_divi_ref_price") > 0))
            .then((pl.col("prev_adj_current_formula") / pl.col("ex_divi_ref_price")) - 1.0)
            .otherwise(None)
            .alias("current_vs_ref_prev_diff"),
            pl.when((pl.col("prev_close") > 0) & (pl.col("ex_divi_ref_price") > 0))
            .then((pl.col("prev_adj_raw_formula") / pl.col("ex_divi_ref_price")) - 1.0)
            .otherwise(None)
            .alias("raw_vs_ref_prev_diff"),
            ((pl.col("cash_per_10_current") / pl.col("cash_per_10_raw").replace(0, None)) - 1.0).alias(
                "cash_current_vs_raw_diff"
            ),
        )
    )


def dividend_event_checks(portal: DataPortal, sample: int) -> None:
    print_section("Dividend Event Summary")
    div = dividend_base(portal)
    summary = div.select(
        pl.len().alias("rows"),
        pl.col("symbol").n_unique().alias("symbols"),
        (pl.col("if_dividend") == 1).sum().alias("active_dividend_rows"),
        pl.col("cash_per_10_raw").is_null().sum().alias("cash_raw_nulls"),
        pl.col("cash_per_10_adj").is_null().sum().alias("cash_adj_nulls"),
        pl.col("total_cash_divi_f").is_null().sum().alias("total_cash_nulls"),
        pl.col("ex_divi_date").is_null().sum().alias("ex_date_nulls"),
        pl.col("ex_divi_ref_price_f").is_null().sum().alias("ex_ref_price_nulls"),
    ).collect()
    show(summary)

    print_section("Potential Duplicate Dividend Schemes")
    duplicates = (
        div.filter(pl.col("if_dividend") == 1)
        .group_by(["symbol", "end_date", "scheme_no"])
        .agg(
            pl.len().alias("rows"),
            pl.col("process").n_unique().alias("process_count"),
            pl.col("info_pub_date").min().alias("first_info_pub_date"),
            pl.col("info_pub_date").max().alias("last_info_pub_date"),
            pl.col("cash_per_10_raw").drop_nulls().n_unique().alias("cash_raw_versions"),
            pl.col("total_cash_divi_f").drop_nulls().n_unique().alias("total_cash_versions"),
        )
        .filter(pl.col("rows") > 1)
        .sort(["rows", "process_count", "cash_raw_versions"], descending=True)
        .collect()
    )
    show(duplicates, sample)

    print_section("Cash Field Unit Cross-Check")
    unit = (
        div.filter(pl.col("if_dividend") == 1)
        .filter(pl.col("total_cash_divi_f").is_not_null() & pl.col("divi_base_f").is_not_null())
        .with_columns((pl.col("total_cash_divi_f") / pl.col("divi_base_f") * 10.0).alias("cash_per_10_from_total"))
        .with_columns(
            (pl.col("cash_per_10_raw") - pl.col("cash_per_10_from_total")).abs().alias("raw_total_abs_diff"),
            (pl.col("cash_per_10_adj") - pl.col("cash_per_10_from_total")).abs().alias("adj_total_abs_diff"),
            ((pl.col("cash_per_10_adj") / pl.col("cash_per_10_raw").replace(0, None)) - 1.0).alias(
                "adj_vs_raw_pct_diff"
            ),
        )
    )
    show(
        unit.select(
            pl.len().alias("checked_rows"),
            (pl.col("raw_total_abs_diff") > 0.01).sum().alias("raw_vs_total_gt_0_01"),
            (pl.col("adj_total_abs_diff") > 0.01).sum().alias("adj_vs_total_gt_0_01"),
            pl.col("raw_total_abs_diff").quantile(0.99).alias("raw_total_abs_diff_p99"),
            pl.col("adj_total_abs_diff").quantile(0.99).alias("adj_total_abs_diff_p99"),
            pl.col("adj_vs_raw_pct_diff").quantile(0.99).alias("adj_vs_raw_pct_diff_p99"),
        ).collect()
    )
    print("\nLargest cash-per-10 mismatches against total_cash_divi/divi_base:")
    show(
        unit.select(
            "symbol",
            "end_date",
            "scheme_no",
            "process",
            "info_pub_date",
            "cash_per_10_raw",
            "cash_per_10_adj",
            "cash_per_10_from_total",
            "raw_total_abs_diff",
            "adj_total_abs_diff",
        )
        .sort("adj_total_abs_diff", descending=True)
        .collect(),
        sample,
    )


def adjustment_checks(portal: DataPortal, sample: int) -> None:
    panel = joined_ex_date_panel(portal)

    print_section("Ex-Date Adjustment Summary")
    show(
        panel.select(
            pl.len().alias("ex_date_price_rows"),
            (pl.col("event_rows") > 1).sum().alias("ex_dates_with_multiple_event_rows"),
            pl.col("event_rows").max().alias("max_event_rows_same_ex_date"),
            pl.col("ex_divi_ref_price").is_null().sum().alias("missing_ex_ref_price"),
            (pl.col("cash_current_vs_raw_diff").abs() > 0.001).sum().alias("cash_adj_differs_from_raw_rows"),
            (pl.col("prev_close") <= 0).sum().alias("prev_close_le_0"),
            (pl.col("non_active_event_rows") > 0).sum().alias("ex_dates_with_non_active_events"),
            (pl.col("current_vs_ref_prev_diff").abs() > 0.001).sum().alias("current_formula_ref_diff_gt_10bp"),
            (pl.col("raw_vs_ref_prev_diff").abs() > 0.001).sum().alias("raw_formula_ref_diff_gt_10bp"),
            pl.col("current_vs_ref_prev_diff").abs().quantile(0.99).alias("current_formula_ref_abs_diff_p99"),
            pl.col("raw_vs_ref_prev_diff").abs().quantile(0.99).alias("raw_formula_ref_abs_diff_p99"),
        ).collect()
    )

    print_section("Current Formula Differs Most From Ex-Dividend Reference Price")
    show(
        panel.filter((pl.col("prev_close") > 0) & (pl.col("ex_divi_ref_price") > 0))
        .select(
            "trade_date",
            "symbol",
            "prev_close",
            "close",
            "pct_chg",
            "cash_per_10_current",
            "cash_per_10_raw",
            "bonus_per_10_current",
            "transfer_per_10_current",
            "ex_divi_ref_price",
            "prev_adj_current_formula",
            "prev_adj_raw_formula",
            "current_vs_ref_prev_diff",
            "raw_vs_ref_prev_diff",
            "current_formula_return",
            "ref_price_return",
            "event_rows",
            "active_event_rows",
            "non_active_event_rows",
            "scheme_count",
        )
        .sort(pl.col("current_vs_ref_prev_diff").abs(), descending=True)
        .collect(),
        sample,
    )

    print_section("Large Return Changes Introduced By Adjustment")
    show(
        panel.filter(pl.col("prev_close") > 0)
        .with_columns((pl.col("current_formula_return") - pl.col("raw_close_return")).alias("return_lift"))
        .select(
            "trade_date",
            "symbol",
            "prev_close",
            "close",
            "pct_chg",
            "cash_per_10_current",
            "cash_per_10_raw",
            "bonus_per_10_current",
            "transfer_per_10_current",
            "raw_close_return",
            "current_formula_return",
            "ref_price_return",
            "return_lift",
            "event_rows",
            "active_event_rows",
            "non_active_event_rows",
        )
        .sort(pl.col("return_lift").abs(), descending=True)
        .collect(),
        sample,
    )

    print_section("Rows Actually Changed By DataPortal.canonical_daily")
    changed = (
        portal.canonical_daily()
        .filter((pl.col("adjusted_prev_close") - pl.col("prev_close")).abs() > 1e-8)
        .join(action_by_ex_date(portal, active_only=False), on=["trade_date", "symbol"], how="left")
        .with_columns(
            (pl.col("close") / pl.col("prev_close").replace(0, None) - 1.0).alias("raw_close_return"),
            (pl.col("close") / pl.col("adjusted_prev_close").replace(0, None) - 1.0).alias("adjusted_return"),
        )
    )
    show(
        changed.select(
            pl.len().alias("changed_rows"),
            pl.col("symbol").n_unique().alias("changed_symbols"),
            pl.col("raw_close_return").abs().quantile(0.99).alias("raw_abs_return_p99"),
            pl.col("adjusted_return").abs().quantile(0.99).alias("adjusted_abs_return_p99"),
            (pl.col("adjusted_return").abs() > 0.2).sum().alias("adjusted_abs_return_gt_20pct"),
        ).collect()
    )
    print("\nLargest adjusted daily returns after DataPortal correction:")
    show(
        changed.select(
            "trade_date",
            "symbol",
            "prev_close",
            "adjusted_prev_close",
            "close",
            "cash_per_10_current",
            "bonus_per_10_current",
            "transfer_per_10_current",
            "raw_close_return",
            "adjusted_return",
            "event_rows",
            "active_event_rows",
            "non_active_event_rows",
            "scheme_count",
        )
        .sort(pl.col("adjusted_return").abs(), descending=True)
        .collect(),
        sample,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check dividend event units and corporate-action price adjustments.")
    parser.add_argument("--sample", type=int, default=20, help="Number of suspicious rows to print per section.")
    args = parser.parse_args()

    portal = DataPortal()
    dividend_event_checks(portal, args.sample)
    adjustment_checks(portal, args.sample)


if __name__ == "__main__":
    main()
