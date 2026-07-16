from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import polars as pl

from hldb.constants import (
    CACHE_DIR,
    CANONICAL_SYMBOL_PATTERN,
    FILES,
    IF_ADJUSTED_PRIORITY,
    REBALANCE_MONTHS,
)


DateLike = str | date | datetime


class DataPortal:
    """Cleaned, point-in-time aware access to local cached parquet data."""

    def __init__(self, cache_dir: str | Path = CACHE_DIR) -> None:
        self.cache_dir = Path(cache_dir)

    def scan(self, name: str) -> pl.LazyFrame:
        try:
            filename = FILES[name]
        except KeyError as exc:
            known = ", ".join(sorted(FILES))
            raise KeyError(f"unknown dataset {name!r}; expected one of: {known}") from exc
        return pl.scan_parquet(str(self.cache_dir / filename))

    @staticmethod
    def _date(value: DateLike) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return date.fromisoformat(value)

    @staticmethod
    def _symbols(symbols: Iterable[str] | None) -> list[str] | None:
        if symbols is None:
            return None
        return list(dict.fromkeys(symbols))

    @staticmethod
    def _filter_symbols(lf: pl.LazyFrame, symbols: Iterable[str] | None) -> pl.LazyFrame:
        symbol_list = DataPortal._symbols(symbols)
        if symbol_list is None:
            return lf
        return lf.filter(pl.col("symbol").is_in(symbol_list))

    def trading_calendar(self) -> pl.LazyFrame:
        return self.scan("calendar").filter(pl.col("exchange") == "SSE")

    def trading_dates(self) -> pl.LazyFrame:
        return (
            self.trading_calendar()
            .filter(pl.col("is_open") == 1)
            .select(pl.col("cal_date").alias("trade_date"))
            .sort("trade_date")
        )

    def rebalance_dates(self, months: Iterable[int] = REBALANCE_MONTHS) -> pl.LazyFrame:
        month_list = list(months)
        return (
            self.trading_dates()
            .with_columns(
                pl.col("trade_date").dt.year().alias("year"),
                pl.col("trade_date").dt.month().alias("month"),
            )
            .filter(pl.col("month").is_in(month_list))
            .group_by(["year", "month"])
            .agg(pl.col("trade_date").max().alias("rebalance_date"))
            .sort("rebalance_date")
        )

    def canonical_universe(self) -> pl.LazyFrame:
        return (
            self.scan("universe")
            .filter(pl.col("symbol").str.contains(CANONICAL_SYMBOL_PATTERN))
            .filter(pl.col("listed_date").dt.year() > 1970)
            .sort(["symbol", "listed_state", "updated_at"], descending=[False, False, True])
            .unique(["symbol"], keep="first", maintain_order=True)
        )

    def eligible_universe(
        self,
        signal_date: DateLike,
        min_listed_days: int = 90,
        exclude_star: bool = True,
        exclude_bse: bool = True,
    ) -> pl.LazyFrame:
        signal = self._date(signal_date)
        active_status = (
            self.scan("list_status")
            .filter(pl.col("symbol").str.contains(CANONICAL_SYMBOL_PATTERN))
            .filter(pl.col("change_date") <= signal)
            .sort(["symbol", "change_date", "change_type"], descending=[False, True, True])
            .unique(["symbol"], keep="first", maintain_order=True)
            .filter(pl.col("change_type").is_in([1, 3]))
            .select("symbol", pl.col("secu_market").alias("pit_secu_market"))
        )
        lf = (
            self.canonical_universe()
            .filter(pl.col("listed_date").dt.date() <= pl.lit(signal))
            .join(active_status, on="symbol", how="inner")
            .with_columns(pl.coalesce(["pit_secu_market", "secu_market"]).alias("secu_market"))
            .drop("pit_secu_market")
        )
        lf = lf.filter((pl.lit(signal) - pl.col("listed_date").dt.date()).dt.total_days() >= min_listed_days)
        if exclude_bse:
            lf = lf.filter(~pl.col("symbol").str.starts_with("BJ"))
        if exclude_star:
            lf = lf.filter(~pl.col("symbol").str.starts_with("SH688"))
        return lf

    def canonical_daily(self) -> pl.LazyFrame:
        quality = (
            (pl.col("limit_up") > 0).cast(pl.Int8) * 4
            + (pl.col("limit_down") > 0).cast(pl.Int8) * 2
            + (pl.col("num_trades") > 0).cast(pl.Int8)
        )
        daily = (
            self.scan("stock_daily")
            .filter(pl.col("symbol").str.contains(CANONICAL_SYMBOL_PATTERN))
            .with_columns(quality.alias("_quality_rank"))
            .sort(
                ["trade_date", "symbol", "_quality_rank", "money"],
                descending=[False, False, True, True],
            )
            .unique(["trade_date", "symbol"], keep="first", maintain_order=True)
            .drop("_quality_rank")
        )
        adj = (
            self.scan("adj_factor")
            .filter(pl.col("symbol").str.contains(CANONICAL_SYMBOL_PATTERN))
            .unique(["trade_date", "symbol"], keep="first", maintain_order=True)
            .select("trade_date", "symbol", pl.col("adj_factor").cast(pl.Float64))
        )
        return (
            daily.join(adj, on=["trade_date", "symbol"], how="left")
            .sort(["symbol", "trade_date"])
            .with_columns(
                pl.col("close").shift(1).over("symbol").alias("raw_previous_close"),
                pl.col("adj_factor").shift(1).over("symbol").alias("prev_adj_factor"),
            )
            .with_columns(
                pl.when(
                    (pl.col("raw_previous_close") > 0)
                    & (pl.col("adj_factor") > 0)
                    & (pl.col("prev_adj_factor") > 0)
                )
                .then(pl.col("raw_previous_close") * pl.col("prev_adj_factor") / pl.col("adj_factor"))
                .otherwise(None)
                .alias("adjusted_prev_close"),
                (pl.col("adj_factor") / pl.col("prev_adj_factor").replace(0, None)).alias("adj_factor_ratio"),
            )
            .with_columns((pl.col("close") / pl.col("adjusted_prev_close").replace(0, None) - 1.0).alias("daily_return"))
        )

    def daily_window(
        self,
        end_date: DateLike,
        count: int,
        symbols: Iterable[str] | None = None,
    ) -> pl.LazyFrame:
        end = self._date(end_date)
        symbol_list = self._symbols(symbols)
        daily = self._filter_symbols(self.canonical_daily(), symbol_list).filter(pl.col("trade_date") <= end)
        return (
            daily.sort(["symbol", "trade_date"], descending=[False, True])
            .with_columns(pl.col("trade_date").cum_count().over("symbol").alias("_rn"))
            .filter(pl.col("_rn") <= count)
            .drop("_rn")
            .sort(["symbol", "trade_date"])
        )

    def daily_basic(self, signal_date: DateLike | None = None, symbols: Iterable[str] | None = None) -> pl.LazyFrame:
        lf = (
            self.scan("daily_basic")
            .filter(pl.col("symbol").str.contains(CANONICAL_SYMBOL_PATTERN))
            .unique(["trade_date", "symbol"], keep="first", maintain_order=True)
        )
        if signal_date is not None:
            lf = lf.filter(pl.col("trade_date") == self._date(signal_date))
        return self._filter_symbols(lf, symbols)

    def tail_30m(self, signal_date: DateLike | None = None, symbols: Iterable[str] | None = None) -> pl.LazyFrame:
        lf = (
            self.scan("tail_30m")
            .filter(pl.col("symbol").str.contains(CANONICAL_SYMBOL_PATTERN))
            .unique(["trade_date", "symbol"], keep="first", maintain_order=True)
        )
        if signal_date is not None:
            lf = lf.filter(pl.col("trade_date") == self._date(signal_date))
        return self._filter_symbols(lf, symbols)

    def stock_limits(self, signal_date: DateLike | None = None, symbols: Iterable[str] | None = None) -> pl.LazyFrame:
        lf = self.scan("stk_limit").unique(["trade_date", "symbol"], keep="first", maintain_order=True)
        if signal_date is not None:
            lf = lf.filter(pl.col("trade_date") == self._date(signal_date))
        return self._filter_symbols(lf, symbols)

    def suspensions(self, signal_date: DateLike | None = None, symbols: Iterable[str] | None = None) -> pl.LazyFrame:
        lf = self.scan("suspend").unique(["trade_date", "symbol"], keep="first", maintain_order=True)
        if signal_date is not None:
            lf = lf.filter(pl.col("trade_date") == self._date(signal_date))
        return self._filter_symbols(lf, symbols)

    def st_name_flags(self, signal_date: DateLike, symbols: Iterable[str] | None = None) -> pl.LazyFrame:
        signal = self._date(signal_date)
        lf = (
            self.scan("namechange")
            .filter(pl.col("symbol").str.contains(CANONICAL_SYMBOL_PATTERN))
            .filter(pl.col("ann_date") <= signal)
            .filter(pl.col("event_date") <= signal)
            .sort(["symbol", "ann_date", "event_date"], descending=[False, True, True])
            .unique(["symbol"], keep="first", maintain_order=True)
            .with_columns(
                (
                    pl.col("new_name").str.contains("ST")
                    | pl.col("new_name").str.contains(r"\*")
                    | pl.col("new_name").str.contains("退")
                ).alias("is_st_name")
            )
            .filter(pl.col("is_st_name"))
            .select("symbol", "new_name", "event_date", "ann_date", "name_end_date", "is_st_name")
        )
        return self._filter_symbols(lf, symbols)

    def latest_visible_income(self, signal_date: DateLike, symbols: Iterable[str] | None = None) -> pl.LazyFrame:
        return self._latest_visible_statement("income", signal_date, symbols)

    def latest_visible_cashflow(self, signal_date: DateLike, symbols: Iterable[str] | None = None) -> pl.LazyFrame:
        return self._latest_visible_statement("cashflow", signal_date, symbols)

    def original_report_income(self, signal_date: DateLike, symbols: Iterable[str] | None = None) -> pl.LazyFrame:
        return self._original_report_statement("income", signal_date, symbols)

    def original_report_cashflow(self, signal_date: DateLike, symbols: Iterable[str] | None = None) -> pl.LazyFrame:
        return self._original_report_statement("cashflow", signal_date, symbols)

    def _latest_visible_statement(
        self,
        name: str,
        signal_date: DateLike,
        symbols: Iterable[str] | None = None,
    ) -> pl.LazyFrame:
        signal = self._date(signal_date)
        priority = pl.col("if_adjusted").replace_strict(
            IF_ADJUSTED_PRIORITY,
            default=0,
            return_dtype=pl.Int8,
        )
        lf = (
            self.scan(name)
            .filter(pl.col("symbol").str.contains(CANONICAL_SYMBOL_PATTERN))
            .filter(pl.col("if_merged") == 1)
            .filter(pl.col("period_end") <= signal)
            .filter(pl.col("ann_date") <= signal)
            .with_columns(priority.alias("_adjusted_priority"))
            .sort(
                ["symbol", "period_end", "ann_date", "_adjusted_priority", "bulletin_type"],
                descending=[False, False, True, True, True],
            )
            .unique(["symbol", "period_end"], keep="first", maintain_order=True)
            .drop("_adjusted_priority")
        )
        return self._filter_symbols(lf, symbols)


    def _original_report_statement(
        self,
        name: str,
        signal_date: DateLike,
        symbols: Iterable[str] | None = None,
    ) -> pl.LazyFrame:
        signal = self._date(signal_date)
        lf = (
            self.scan(name)
            .filter(pl.col("symbol").str.contains(CANONICAL_SYMBOL_PATTERN))
            .filter(pl.col("if_merged") == 1)
            .filter(pl.col("if_adjusted") == 1)
            .filter(pl.col("period_end") <= signal)
            .filter(pl.col("ann_date") <= signal)
            .sort(
                ["symbol", "period_end", "ann_date", "bulletin_type"],
                descending=[False, False, False, True],
            )
            .unique(["symbol", "period_end"], keep="first", maintain_order=True)
        )
        return self._filter_symbols(lf, symbols)

    def fin_indicator_pit(self, signal_date: DateLike, symbols: Iterable[str] | None = None) -> pl.LazyFrame:
        signal = self._date(signal_date)
        lf = (
            self.scan("fin_indicator")
            .filter(pl.col("symbol").str.contains(CANONICAL_SYMBOL_PATTERN))
            .filter(pl.col("period_end") <= signal)
            .filter(pl.col("valid_from") <= signal)
            .sort(["symbol", "period_end", "valid_from"], descending=[False, False, True])
            .unique(["symbol", "period_end"], keep="first", maintain_order=True)
        )
        return self._filter_symbols(lf, symbols)
