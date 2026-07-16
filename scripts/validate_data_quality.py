from __future__ import annotations

from pathlib import Path

import polars as pl


CACHE = Path("data/cache")


FILES = {
    "stock_daily": "stock_daily_2014.parquet",
    "daily_basic": "daily_basic_2015.parquet",
    "tail_30m": "tail_30m_money_2015.parquet",
    "stk_limit": "stk_limit_2015.parquet",
    "suspend": "suspend_2015.parquet",
    "universe": "stock_universe_jydb.parquet",
    "dividend": "dividend_jydb_2013.parquet",
    "income": "income_jydb_2012.parquet",
    "cashflow": "cashflow_jydb_2012.parquet",
    "fin_indicator": "fin_indicator_2012.parquet",
    "namechange": "namechange_2010.parquet",
    "list_status": "list_status_jydb.parquet",
    "calendar": "trade_calendar_2014.parquet",
}


KEYS = {
    "stock_daily": ["trade_date", "symbol"],
    "daily_basic": ["trade_date", "symbol"],
    "tail_30m": ["trade_date", "symbol"],
    "stk_limit": ["trade_date", "symbol"],
    "suspend": ["trade_date", "symbol"],
    "universe": ["symbol"],
    "dividend": ["symbol", "end_date", "scheme_no"],
    "income": ["symbol", "period_end", "ann_date", "bulletin_type"],
    "cashflow": ["symbol", "period_end", "ann_date", "bulletin_type"],
    "fin_indicator": ["symbol", "period_end", "valid_from", "valid_to"],
    "namechange": ["symbol", "event_date", "ann_date", "new_name"],
    "list_status": ["symbol", "change_date", "change_type"],
    "calendar": ["exchange", "cal_date"],
}


KEY_NULLS = {
    "stock_daily": ["trade_date", "symbol", "close", "money", "limit_up", "limit_down"],
    "daily_basic": ["trade_date", "symbol", "close", "turnover_rate", "total_mv"],
    "tail_30m": ["trade_date", "symbol", "tail_money_30m"],
    "stk_limit": ["trade_date", "symbol", "up_limit", "down_limit"],
    "universe": ["symbol", "listed_date", "secu_market", "listed_sector"],
    "dividend": ["symbol", "end_date", "info_pub_date", "if_dividend"],
    "income": ["symbol", "period_end", "ann_date", "NPParentCompanyOwners"],
    "fin_indicator": ["symbol", "period_end", "valid_from", "valid_to", "roe"],
    "calendar": ["cal_date", "is_open", "pretrade_date"],
}


def scan(name: str) -> pl.LazyFrame:
    return pl.scan_parquet(str(CACHE / FILES[name]))


def print_section(title: str) -> None:
    print(f"\n## {title}")


def table(rows: list[dict]) -> None:
    if rows:
        print(pl.DataFrame(rows))
    else:
        print("(none)")


def overview() -> None:
    print_section("File Overview")
    rows = []
    for name, filename in FILES.items():
        lf = scan(name)
        schema = lf.collect_schema()
        date_cols = [c for c, t in schema.items() if t in (pl.Date, pl.Datetime)]
        exprs = [pl.len().alias("rows"), pl.col("symbol").n_unique().alias("symbols") if "symbol" in schema else pl.lit(None).alias("symbols")]
        for col in date_cols[:3]:
            exprs += [pl.col(col).min().alias(f"{col}_min"), pl.col(col).max().alias(f"{col}_max")]
        out = lf.select(exprs).collect().to_dicts()[0]
        out = {"table": name, "file": filename, **out}
        rows.append(out)
    table(rows)


def duplicate_keys() -> None:
    print_section("Duplicate Key Checks")
    rows = []
    for name, keys in KEYS.items():
        lf = scan(name)
        dup_groups = lf.group_by(keys).len().filter(pl.col("len") > 1).select(
            pl.len().alias("duplicate_groups"),
            pl.col("len").sum().alias("duplicate_rows"),
            pl.col("len").max().alias("max_group_size"),
        )
        out = dup_groups.collect().to_dicts()[0]
        rows.append({"table": name, "key": ",".join(keys), **out})
    table(rows)


def null_checks() -> None:
    print_section("Key Null Checks")
    rows = []
    for name, cols in KEY_NULLS.items():
        lf = scan(name)
        schema = lf.collect_schema()
        exprs = [pl.len().alias("rows")]
        for col in cols:
            if col in schema:
                exprs.append(pl.col(col).is_null().sum().alias(col))
        out = lf.select(exprs).collect().to_dicts()[0]
        total = out.pop("rows")
        for col, n in out.items():
            rows.append({"table": name, "column": col, "nulls": n, "null_pct": n / total if total else 0})
    table(rows)


def symbol_format() -> None:
    print_section("Symbol Format Checks")
    rows = []
    pattern = r"^(SH|SZ|BJ)[0-9]{6}$"
    for name in FILES:
        schema = scan(name).collect_schema()
        if "symbol" not in schema:
            continue
        out = scan(name).select(
            pl.len().alias("rows"),
            pl.col("symbol").n_unique().alias("symbols"),
            (~pl.col("symbol").str.contains(pattern)).sum().alias("bad_symbol_rows"),
        ).collect().to_dicts()[0]
        rows.append({"table": name, **out})
    table(rows)


def market_data_checks() -> None:
    print_section("Market Data Sanity")
    daily = scan("stock_daily")
    rows = [
        daily.select(
            pl.len().alias("rows"),
            (pl.col("close") <= 0).sum().alias("close_le_0"),
            (pl.col("money") < 0).sum().alias("money_lt_0"),
            (pl.col("high") < pl.col("low")).sum().alias("high_lt_low"),
            (pl.col("close") > pl.col("high")).sum().alias("close_gt_high"),
            (pl.col("close") < pl.col("low")).sum().alias("close_lt_low"),
            (pl.col("limit_up") <= pl.col("limit_down")).sum().alias("bad_limit_band"),
            (pl.col("close") > pl.col("limit_up") * 1.0001).sum().alias("close_above_limit"),
            (pl.col("close") < pl.col("limit_down") * 0.9999).sum().alias("close_below_limit"),
        ).collect().to_dicts()[0]
    ]
    table(rows)

    print("\nDaily basic sanity:")
    table([
        scan("daily_basic").select(
            pl.len().alias("rows"),
            (pl.col("close") <= 0).sum().alias("close_le_0"),
            (pl.col("total_mv") <= 0).sum().alias("total_mv_le_0"),
            (pl.col("turnover_rate") < 0).sum().alias("turnover_lt_0"),
        ).collect().to_dicts()[0]
    ])


def coverage_checks() -> None:
    print_section("Cross-Table Coverage")
    daily_keys = scan("stock_daily").select("trade_date", "symbol")
    basic_keys = scan("daily_basic").select("trade_date", "symbol")
    tail_keys = scan("tail_30m").select("trade_date", "symbol")
    limit_keys = scan("stk_limit").select("trade_date", "symbol")
    rows = []
    for name, other in [("daily_basic", basic_keys), ("tail_30m", tail_keys), ("stk_limit", limit_keys)]:
        rows.append(
            daily_keys.join(other, on=["trade_date", "symbol"], how="anti")
            .select(pl.len().alias("stock_daily_keys_missing_in_" + name))
            .collect()
            .to_dicts()[0]
        )
    table(rows)

    print("\nRebalance-date coverage:")
    cal = scan("calendar").filter((pl.col("is_open") == 1) & (pl.col("exchange") == "SSE"))
    rebal = cal.with_columns(
        pl.col("cal_date").dt.year().alias("year"),
        pl.col("cal_date").dt.month().alias("month"),
    ).filter(pl.col("month").is_in([4, 8, 10, 12])).group_by(["year", "month"]).agg(
        pl.col("cal_date").max().alias("rebalance_date")
    ).sort("rebalance_date")

    daily_cov = daily_keys.group_by("trade_date").agg(pl.col("symbol").n_unique().alias("daily_symbols"))
    basic_cov = basic_keys.group_by("trade_date").agg(pl.col("symbol").n_unique().alias("basic_symbols"))
    tail_cov = tail_keys.group_by("trade_date").agg(pl.col("symbol").n_unique().alias("tail_symbols"))
    limit_cov = limit_keys.group_by("trade_date").agg(pl.col("symbol").n_unique().alias("limit_symbols"))
    cov = rebal.join(daily_cov, left_on="rebalance_date", right_on="trade_date", how="left")
    cov = cov.join(basic_cov, left_on="rebalance_date", right_on="trade_date", how="left")
    cov = cov.join(tail_cov, left_on="rebalance_date", right_on="trade_date", how="left")
    cov = cov.join(limit_cov, left_on="rebalance_date", right_on="trade_date", how="left")
    table(cov.collect().to_dicts())


def financial_checks() -> None:
    print_section("Financial PIT Checks")
    rows = []
    rows.append(
        scan("income").select(
            pl.len().alias("income_rows"),
            (pl.col("ann_date") < pl.col("period_end")).sum().alias("income_ann_before_period_end"),
            pl.col("period_end").max().alias("income_max_period"),
            pl.col("ann_date").max().alias("income_max_ann"),
        ).collect().to_dicts()[0]
    )
    rows.append(
        scan("fin_indicator").select(
            pl.len().alias("fin_indicator_rows"),
            (pl.col("valid_to") <= pl.col("valid_from")).sum().alias("bad_valid_interval"),
            pl.col("period_end").max().alias("fin_max_period"),
            pl.col("valid_from").max().alias("fin_max_valid_from"),
            pl.col("valid_to").max().alias("fin_max_valid_to"),
        ).collect().to_dicts()[0]
    )
    table(rows)

    print("\nDividend sanity:")
    table([
        scan("dividend").select(
            pl.len().alias("rows"),
            (pl.col("info_pub_date") < pl.col("end_date")).sum().alias("info_pub_before_end_date"),
            (pl.col("if_dividend") == 1).sum().alias("if_dividend_rows"),
            pl.col("cash_divi_rmb").is_null().sum().alias("cash_divi_rmb_nulls"),
            pl.col("total_cash_divi").is_null().sum().alias("total_cash_divi_nulls"),
        ).collect().to_dicts()[0]
    ])


def main() -> None:
    overview()
    duplicate_keys()
    null_checks()
    symbol_format()
    market_data_checks()
    coverage_checks()
    financial_checks()


if __name__ == "__main__":
    main()
