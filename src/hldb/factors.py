from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

import pandas as pd
import polars as pl

from hldb.data_portal import DataPortal, DateLike


@dataclass(frozen=True)
class StrategyParams:
    stock_num: int = 100
    top_div_rank: int = 300
    quantile_cut: float = 0.2
    winsor_low: float = 0.05
    winsor_high: float = 0.95
    max_weight: float = 0.05
    rolling_window: int = 252
    volatility_ann_factor: float = 252.0
    financial_mode: str = "latest_visible"


class FactorEngine:
    """Factor calculations for the local dividend low-volatility replication."""

    def __init__(self, portal: DataPortal | None = None, params: StrategyParams | None = None) -> None:
        self.portal = portal or DataPortal()
        self.params = params or StrategyParams()
        if self.params.financial_mode not in {"latest_visible", "original_report"}:
            raise ValueError("financial_mode must be 'latest_visible' or 'original_report'")

    def base_universe(self, signal_date: DateLike) -> pl.DataFrame:
        st = self.portal.st_name_flags(signal_date).select("symbol").with_columns(pl.lit(True).alias("is_st_name"))
        suspended = self.portal.suspensions(signal_date).select("symbol").with_columns(pl.lit(True).alias("is_suspended"))
        return (
            self.portal.eligible_universe(signal_date)
            .select("symbol", "listed_date", "secu_market", "listed_sector")
            .join(st, on="symbol", how="left")
            .join(suspended, on="symbol", how="left")
            .filter(pl.col("is_st_name").is_null())
            .filter(pl.col("is_suspended").is_null())
            .drop("is_st_name", "is_suspended")
            .collect()
        )

    def liquidity_factors(self, signal_date: DateLike, symbols: Iterable[str]) -> pl.DataFrame:
        symbols = list(symbols)
        basic = self.portal.daily_basic(signal_date, symbols).select("symbol", "total_mv", "turnover_rate")
        avg_money = (
            self.portal.daily_window(signal_date, self.params.rolling_window, symbols)
            .group_by("symbol")
            .agg(
                pl.col("money").mean().alias("avg_money_252"),
                pl.len().alias("daily_obs_252"),
            )
        )
        return basic.join(avg_money, on="symbol", how="inner").collect()

    def apply_liquidity_filter(self, factors: pl.DataFrame) -> pl.DataFrame:
        cap_cut = factors.select(pl.col("total_mv").quantile(self.params.quantile_cut)).item()
        money_cut = factors.select(pl.col("avg_money_252").quantile(self.params.quantile_cut)).item()
        return factors.filter(
            (pl.col("total_mv") >= cap_cut)
            & (pl.col("avg_money_252") >= money_cut)
            & (pl.col("daily_obs_252") >= self.params.rolling_window)
        )

    def dividend_indicators(self, signal_date: DateLike, symbols: Iterable[str]) -> pl.DataFrame:
        signal = self.portal._date(signal_date)
        one_year_ago = signal - timedelta(days=365)
        symbols = list(symbols)

        div = (
            self.portal.scan("dividend")
            .filter(pl.col("symbol").is_in(symbols))
            .filter(pl.col("if_dividend") == 1)
            .filter(pl.col("info_pub_date") <= signal)
            .with_columns(
                pl.col("cash_divi_rmb").cast(pl.Float64).alias("cash_per_10"),
                pl.col("total_cash_divi").cast(pl.Float64).alias("total_cash_divi_f"),
            )
            .filter(pl.col("cash_per_10").is_not_null())
        )

        annual_bonus = (
            div.with_columns(pl.col("end_date").dt.year().alias("div_year"))
            .group_by(["symbol", "div_year"])
            .agg(pl.col("total_cash_divi_f").sum().alias("annual_cash_bonus"))
        )

        income = (
            self._income_statement(signal, symbols)
            .filter(pl.col("period_end").dt.month() == 12)
            .filter(pl.col("period_end").dt.day() == 31)
            .with_columns(
                pl.col("period_end").dt.year().alias("div_year"),
                pl.col("NPParentCompanyOwners").cast(pl.Float64).alias("annual_net_profit"),
            )
            .select("symbol", "div_year", "annual_net_profit")
        )

        payout = (
            annual_bonus.join(income, on=["symbol", "div_year"], how="left")
            .with_columns(
                (pl.col("annual_cash_bonus") / pl.col("annual_net_profit").replace(0, None)).alias(
                    "dividend_payout_ratio"
                )
            )
            .sort(["symbol", "div_year"])
            .group_by("symbol")
            .agg(pl.col("dividend_payout_ratio").last())
        )

        ttm_div = (
            div.filter(pl.col("info_pub_date") >= one_year_ago)
            # jydb cash_divi_rmb is cash per 10 shares; convert to cash per share.
            .with_columns((pl.col("cash_per_10") / 10.0).alias("div_per_share"))
            .group_by("symbol")
            .agg(pl.col("div_per_share").sum().alias("ttm_div_per_share"))
        )

        price = self.portal.daily_basic(signal, symbols).select("symbol", pl.col("close").alias("price"))

        return (
            payout.join(ttm_div, on="symbol", how="outer_coalesce")
            .join(price, on="symbol", how="left")
            .with_columns(
                (pl.col("ttm_div_per_share") / pl.col("price").replace(0, None)).alias("ttm_dividend_yield")
            )
            .select("symbol", "dividend_payout_ratio", "ttm_dividend_yield", "ttm_div_per_share")
            .with_columns(
                pl.col("dividend_payout_ratio").fill_null(0.0),
                pl.col("ttm_dividend_yield").fill_null(0.0),
                pl.col("ttm_div_per_share").fill_null(0.0),
            )
            .collect()
        )

    def volatility_factors(self, signal_date: DateLike, symbols: Iterable[str]) -> pl.DataFrame:
        symbols = list(symbols)
        return (
            self.portal.daily_window(signal_date, self.params.rolling_window + 1, symbols)
            .sort(["symbol", "trade_date"])
            .with_columns(pl.col("daily_return").alias("ret"))
            .filter(pl.col("ret").is_not_null())
            .group_by("symbol")
            .agg(
                (pl.col("ret").std() * self.params.volatility_ann_factor**0.5).alias("volatility"),
                pl.len().alias("ret_obs"),
            )
            .collect()
        )

    def lowvol_factors(self, signal_date: DateLike, symbols: Iterable[str], liquidity: pl.DataFrame) -> pl.DataFrame:
        symbols = list(symbols)
        vol = self.volatility_factors(signal_date, symbols)
        tail = self.portal.tail_30m(signal_date, symbols).select("symbol", "tail_money_30m")
        daily_money = (
            self.portal.daily_window(signal_date, 1, symbols)
            .select("symbol", pl.col("money").alias("daily_money"))
        )
        tail_ratio = (
            tail.join(daily_money, on="symbol", how="left")
            .with_columns((pl.col("tail_money_30m") / pl.col("daily_money").replace(0, None)).alias("tail_money_ratio"))
            .select("symbol", "tail_money_ratio")
            .collect()
        )
        return (
            vol.join(liquidity.select("symbol", "turnover_rate"), on="symbol", how="left")
            .join(tail_ratio, on="symbol", how="left")
        )

    def quality_factors(self, signal_date: DateLike, symbols: Iterable[str]) -> pl.DataFrame:
        symbols = list(symbols)
        income = (
            self._income_statement(signal_date, symbols)
            .select("symbol", "period_end", pl.col("NPParentCompanyOwners").cast(pl.Float64).alias("np_parent"))
        )

        sue = (
            income.sort(["symbol", "period_end"])
            .with_columns(
                (pl.col("np_parent") - pl.col("np_parent").shift(4).over("symbol")).alias("yoy_np_diff")
            )
            .with_columns(
                pl.col("yoy_np_diff").std().over("symbol").alias("yoy_np_diff_std"),
                pl.col("period_end").max().over("symbol").alias("_latest_period"),
            )
            .filter(pl.col("period_end") == pl.col("_latest_period"))
            .with_columns(
                (pl.col("yoy_np_diff") / pl.col("yoy_np_diff_std").replace(0, None)).alias("sue")
            )
            .select("symbol", "sue")
        )

        fin = self.portal.fin_indicator_pit(signal_date, symbols)
        fin_enriched = fin.sort(["symbol", "period_end"]).with_columns(
            (pl.col("netprofit_yoy") - pl.col("netprofit_yoy").shift(1).over("symbol")).alias(
                "accelerate_growth"
            ),
            pl.coalesce(["ocf_to_or", "q_ocf_to_or"]).alias("_cash_flow_ratio_raw"),
            pl.col("period_end").rank("ordinal", descending=True).over("symbol").alias("_period_desc_rank"),
            pl.col("period_end").max().over("symbol").alias("_latest_period"),
        )

        cash_flow_mean = (
            fin_enriched.filter(pl.col("_period_desc_rank") <= 4)
            .group_by("symbol")
            .agg(pl.col("_cash_flow_ratio_raw").mean().alias("cash_flow_ratio"))
        )

        latest = (
            fin_enriched.filter(pl.col("period_end") == pl.col("_latest_period"))
            .select("symbol", "roe", "accelerate_growth")
            .join(cash_flow_mean, on="symbol", how="left")
        )

        df = latest.join(sue, on="symbol", how="left").drop_nulls()
        return self._winsorize(df, ["roe", "sue", "accelerate_growth", "cash_flow_ratio"]).collect()

    def _income_statement(self, signal_date: DateLike, symbols: Iterable[str]) -> pl.LazyFrame:
        if self.params.financial_mode == "original_report":
            return self.portal.original_report_income(signal_date, symbols)
        return self.portal.latest_visible_income(signal_date, symbols)

    def _cashflow_statement(self, signal_date: DateLike, symbols: Iterable[str]) -> pl.LazyFrame:
        if self.params.financial_mode == "original_report":
            return self.portal.original_report_cashflow(signal_date, symbols)
        return self.portal.latest_visible_cashflow(signal_date, symbols)

    def candidate_factors(self, signal_date: DateLike) -> pl.DataFrame:
        universe = self.base_universe(signal_date)
        liquidity = self.liquidity_factors(signal_date, universe["symbol"])
        liquidity_filtered = self.apply_liquidity_filter(liquidity)

        div = self.dividend_indicators(signal_date, liquidity_filtered["symbol"])
        div_filtered = div.filter(
            (pl.col("dividend_payout_ratio") >= 0.0) & (pl.col("dividend_payout_ratio") <= 1.0)
        )

        vol = self.volatility_factors(signal_date, div_filtered["symbol"])
        vol_cut = vol.select(pl.col("volatility").quantile(1.0 - self.params.quantile_cut)).item()
        low_vol_pool = vol.filter(
            (pl.col("volatility") <= vol_cut) & (pl.col("ret_obs") >= self.params.rolling_window)
        )

        div_ranked = (
            div_filtered.join(low_vol_pool.select("symbol"), on="symbol", how="inner")
            .sort("ttm_dividend_yield", descending=True)
            .head(self.params.top_div_rank)
        )

        liquidity_final = liquidity_filtered.join(div_ranked.select("symbol"), on="symbol", how="inner")
        low = self.lowvol_factors(signal_date, div_ranked["symbol"], liquidity_final)
        quality = self.quality_factors(signal_date, div_ranked["symbol"])

        return (
            div_ranked.join(low, on="symbol", how="inner")
            .join(quality, on="symbol", how="inner")
            .drop_nulls()
        )

    def score_and_weight(self, factors: pl.DataFrame) -> pl.DataFrame:
        if factors.is_empty():
            return factors

        df = factors.to_pandas()
        score_specs = {
            "score_vol": ("volatility", False),
            "score_turn": ("turnover_rate", False),
            "score_tail": ("tail_money_ratio", False),
            "score_roe": ("roe", True),
            "score_sue": ("sue", True),
            "score_acc": ("accelerate_growth", True),
            "score_cf": ("cash_flow_ratio", True),
        }
        for score_col, (source_col, higher_is_better) in score_specs.items():
            df[score_col] = self._qcut_decile_score(df[source_col], higher_is_better)

        df["lowvol_composite"] = df[["score_vol", "score_turn", "score_tail"]].mean(axis=1)
        df["quality_composite"] = df[["score_roe", "score_sue", "score_acc", "score_cf"]].mean(axis=1)
        df["final_score"] = (df["lowvol_composite"] + df["quality_composite"]) / 2.0
        df = df.sort_values("final_score", ascending=False).head(self.params.stock_num).copy()
        df["raw_weight"] = df["ttm_dividend_yield"] + (1.0 / df["volatility"].replace(0, pd.NA))
        df["_weight_pre_cap"] = df["raw_weight"] / df["raw_weight"].sum()
        df["_weight_capped"] = df["_weight_pre_cap"].clip(upper=self.params.max_weight)
        df["weight"] = df["_weight_capped"] / df["_weight_capped"].sum()
        df = df.drop(columns=["_weight_pre_cap", "_weight_capped"])
        return pl.from_pandas(df)

    def select_portfolio(self, signal_date: DateLike) -> pl.DataFrame:
        return self.score_and_weight(self.candidate_factors(signal_date))

    def _winsorize(self, lf: pl.LazyFrame, cols: list[str]) -> pl.LazyFrame:
        quantiles = lf.select(
            *[pl.col(c).quantile(self.params.winsor_low).alias(f"{c}_lo") for c in cols],
            *[pl.col(c).quantile(self.params.winsor_high).alias(f"{c}_hi") for c in cols],
        ).collect().row(0, named=True)
        exprs = [pl.col(c).clip(quantiles[f"{c}_lo"], quantiles[f"{c}_hi"]).alias(c) for c in cols]
        return lf.with_columns(exprs)

    @staticmethod
    def _qcut_decile_score(values: pd.Series, higher_is_better: bool) -> pd.Series:
        ranks = values.rank(method="first", ascending=True)
        labels = list(range(1, 11))
        score = pd.qcut(ranks, q=10, labels=labels, duplicates="drop").astype("int8")
        if not higher_is_better:
            score = 11 - score
        return score
