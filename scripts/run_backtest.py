from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl

from hldb.backtest import BacktestConfig, DailyBacktester, write_result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily backtest for the local dividend low-volatility strategy.")
    parser.add_argument("--start", default="2015-04-30", help="Backtest start date.")
    parser.add_argument("--end", default="2026-04-30", help="Backtest end date.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/backtest_hldb"), help="Output directory.")
    parser.add_argument(
        "--financial-mode",
        choices=["latest_visible", "original_report"],
        default="latest_visible",
        help="Financial statement PIT mode for income and cashflow data.",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=1_000_000.0,
        help="Cash capital used to convert the CNY 5 minimum commission into a return drag.",
    )
    parser.add_argument(
        "--min-commission",
        type=float,
        default=5.0,
        help="Minimum commission in CNY per buy or sell order.",
    )
    parser.add_argument(
        "--trade-at",
        choices=["open", "close"],
        default="open",
        help="Daily execution price. Use open as the closest available proxy for JoinQuant 09:31 orders.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = BacktestConfig(
        start_date=args.start,
        end_date=args.end,
        financial_mode=args.financial_mode,
        trade_at=args.trade_at,
        initial_capital_cash=args.initial_capital,
        min_commission_cash=args.min_commission,
    )
    result = DailyBacktester(config=config).run()
    write_result(result, args.output_dir)

    print(pl.DataFrame([result.metrics]))
    print("\nTrades tail:")
    print(result.trades.tail(10))
    print(f"\nwrote {args.output_dir}")


if __name__ == "__main__":
    main()
