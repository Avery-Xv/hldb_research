from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

from hldb.factors import FactorEngine


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate dividend low-volatility portfolio for one signal date.")
    parser.add_argument("signal_date", help="Signal date in YYYY-MM-DD format, usually a rebalance date.")
    parser.add_argument("--output", type=Path, help="Optional CSV or Parquet output path.")
    return parser.parse_args()


def write_output(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df.write_csv(path)
    elif suffix in {".parquet", ".pq"}:
        df.write_parquet(path)
    else:
        raise ValueError(f"unsupported output suffix {path.suffix!r}; use .csv or .parquet")


def main() -> None:
    args = parse_args()
    engine = FactorEngine()
    portfolio = engine.select_portfolio(args.signal_date)

    print(portfolio.select("symbol", "final_score", "weight", "ttm_dividend_yield", "volatility").head(30))
    print(f"rows={portfolio.height} weight_sum={portfolio.select(pl.col('weight').sum()).item():.12f}")

    if args.output:
        write_output(portfolio, args.output)
        print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
