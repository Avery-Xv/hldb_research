from __future__ import annotations

import polars as pl

from hldb import DataPortal


def show(name: str, lf: pl.LazyFrame) -> None:
    print(f"\n## {name}")
    print(lf.collect())


def main() -> None:
    portal = DataPortal()
    signal_date = "2020-04-30"
    symbols = ["SH600519", "SZ000001", "SH600000"]

    show("rebalance_dates_tail", portal.rebalance_dates().tail(8))
    show(
        "eligible_universe_count",
        portal.eligible_universe(signal_date).select(
            pl.len().alias("rows"),
            pl.col("symbol").n_unique().alias("symbols"),
        ),
    )
    show("daily_window", portal.daily_window(signal_date, 3, symbols).select("trade_date", "symbol", "close", "money"))
    show("daily_basic", portal.daily_basic(signal_date, symbols).select("trade_date", "symbol", "turnover_rate", "total_mv"))
    show("tail_30m", portal.tail_30m(signal_date, symbols))
    show("limits", portal.stock_limits(signal_date, symbols))
    show("suspensions", portal.suspensions(signal_date, symbols))
    show("st_flags", portal.st_name_flags(signal_date, symbols))
    show(
        "income_latest_visible",
        portal.latest_visible_income(signal_date, symbols).select(
            "symbol", "period_end", "ann_date", "if_merged", "if_adjusted", "bulletin_type", "NPParentCompanyOwners"
        ).sort(["symbol", "period_end"]).tail(12),
    )
    show(
        "fin_indicator_pit",
        portal.fin_indicator_pit(signal_date, symbols).select(
            "symbol", "period_end", "valid_from", "roe", "ocf_to_or", "netprofit_yoy"
        ).sort(["symbol", "period_end"]).tail(12),
    )


if __name__ == "__main__":
    main()
