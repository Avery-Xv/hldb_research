from __future__ import annotations

import polars as pl

from hldb.factors import FactorEngine


def main() -> None:
    engine = FactorEngine()
    signal_date = "2020-04-30"

    universe = engine.base_universe(signal_date)
    print("base_universe", universe.shape)

    liquidity = engine.liquidity_factors(signal_date, universe["symbol"])
    liquidity_filtered = engine.apply_liquidity_filter(liquidity)
    print("liquidity", liquidity.shape, "filtered", liquidity_filtered.shape)

    div = engine.dividend_indicators(signal_date, liquidity_filtered["symbol"])
    div_filtered = div.filter((pl.col("dividend_payout_ratio") >= 0) & (pl.col("dividend_payout_ratio") <= 1))
    print("dividend", div.shape, "filtered", div_filtered.shape)

    candidates = engine.candidate_factors(signal_date)
    print("candidates", candidates.shape)
    print(candidates.select("symbol", "ttm_dividend_yield", "volatility", "roe", "sue").head(10))

    portfolio = engine.score_and_weight(candidates)
    print("portfolio", portfolio.shape)
    print(portfolio.select("symbol", "final_score", "weight", "ttm_dividend_yield", "volatility").head(20))
    print("weight_sum", portfolio.select(pl.col("weight").sum()).item())


if __name__ == "__main__":
    main()
