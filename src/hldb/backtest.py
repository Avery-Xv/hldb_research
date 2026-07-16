from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import polars as pl

from hldb.data_portal import DataPortal, DateLike
from hldb.factors import FactorEngine, StrategyParams


@dataclass(frozen=True)
class BacktestConfig:
    start_date: str = "2015-04-30"
    end_date: str = "2026-04-30"
    initial_cash: float = 1.0
    open_commission: float = 0.0003
    close_commission: float = 0.0003
    close_tax: float = 0.001
    min_commission_cash: float = 5.0
    initial_capital_cash: float = 1_000_000.0
    rebalance_tolerance: float = 0.005
    max_abs_daily_return: float = 1.0
    trade_at: str = "open"
    financial_mode: str = "latest_visible"


@dataclass
class BacktestResult:
    nav: pl.DataFrame
    trades: pl.DataFrame
    holdings: pl.DataFrame
    metrics: dict[str, float | int | str]


class DailyBacktester:
    """Daily approximation of the reference JoinQuant settings."""

    def __init__(
        self,
        portal: DataPortal | None = None,
        factors: FactorEngine | None = None,
        config: BacktestConfig | None = None,
    ) -> None:
        self.portal = portal or DataPortal()
        self.config = config or BacktestConfig()
        if self.config.trade_at not in {"open", "close"}:
            raise ValueError("trade_at must be 'open' or 'close'")
        self.factors = factors or FactorEngine(
            self.portal, StrategyParams(financial_mode=self.config.financial_mode)
        )

    def run(self) -> BacktestResult:
        start = self.portal._date(self.config.start_date)
        end = self.portal._date(self.config.end_date)

        prices = self._price_table(start, end)
        trading_dates = prices.select("trade_date").unique(maintain_order=True).sort("trade_date")["trade_date"].to_list()
        if not trading_dates:
            raise ValueError("no trading dates in requested backtest window")

        previous_date = self._previous_trade_date_map()
        rebalance_dates = [
            d
            for d in self.portal.rebalance_dates().collect()["rebalance_date"].to_list()
            if start <= d <= end and d in previous_date
        ]

        price_by_date = {
            self._group_key_date(d): {r["symbol"]: r for r in g.to_dicts()}
            for d, g in prices.group_by("trade_date", maintain_order=True)
        }
        return_by_date = {
            self._group_key_date(d): dict(zip(g["symbol"].to_list(), g["daily_return"].to_list(), strict=False))
            for d, g in prices.group_by("trade_date", maintain_order=True)
        }
        overnight_by_date = {
            self._group_key_date(d): dict(zip(g["symbol"].to_list(), g["overnight_return"].to_list(), strict=False))
            for d, g in prices.group_by("trade_date", maintain_order=True)
        }
        intraday_by_date = {
            self._group_key_date(d): dict(zip(g["symbol"].to_list(), g["intraday_return"].to_list(), strict=False))
            for d, g in prices.group_by("trade_date", maintain_order=True)
        }
        limit_by_date = self._limits_by_date(start, end)
        suspended_by_date = self._suspended_by_date(start, end)

        holdings: dict[str, float] = {}
        nav = self.config.initial_cash
        nav_rows: list[dict] = []
        trade_rows: list[dict] = []
        holding_rows: list[dict] = []
        prev_date: date | None = None

        for current_date in trading_dates:
            turnover = 0.0
            cost = 0.0
            rebalance = current_date in rebalance_dates
            signal_date = previous_date.get(current_date)
            trade_diag = {
                "target_count": 0,
                "blocked_buy_count": 0,
                "blocked_sell_count": 0,
                "frozen_count": 0,
            }

            if rebalance and signal_date is not None and self.config.trade_at == "open":
                if prev_date is not None:
                    holdings, return_multiplier = self._apply_portfolio_returns(
                        holdings, overnight_by_date.get(current_date, {})
                    )
                    nav *= return_multiplier
                target = self._target_weights(
                    signal_date,
                    current_date,
                    holdings,
                    price_by_date.get(current_date, {}),
                    limit_by_date,
                    suspended_by_date,
                )
                holdings, turnover, cost, trade_diag = self._rebalance_holdings(
                    holdings,
                    target,
                    price_by_date.get(current_date, {}),
                    limit_by_date.get(current_date, {}),
                    suspended_by_date.get(current_date, set()),
                    nav,
                )
                nav *= max(0.0, 1.0 - cost)
                holdings, return_multiplier = self._apply_portfolio_returns(
                    holdings, intraday_by_date.get(current_date, {})
                )
                nav *= return_multiplier
            else:
                if prev_date is not None:
                    holdings, return_multiplier = self._apply_portfolio_returns(
                        holdings, return_by_date.get(current_date, {})
                    )
                    nav *= return_multiplier
                if rebalance and signal_date is not None:
                    target = self._target_weights(
                        signal_date,
                        current_date,
                        holdings,
                        price_by_date.get(current_date, {}),
                        limit_by_date,
                        suspended_by_date,
                    )
                    holdings, turnover, cost, trade_diag = self._rebalance_holdings(
                        holdings,
                        target,
                        price_by_date.get(current_date, {}),
                        limit_by_date.get(current_date, {}),
                        suspended_by_date.get(current_date, set()),
                        nav,
                    )
                    nav *= max(0.0, 1.0 - cost)

            if rebalance and signal_date is not None:
                trade_rows.append(
                    {
                        "trade_date": current_date,
                        "signal_date": signal_date,
                        "target_count": trade_diag["target_count"],
                        "holding_count": len(holdings),
                        "blocked_buy_count": trade_diag["blocked_buy_count"],
                        "blocked_sell_count": trade_diag["blocked_sell_count"],
                        "frozen_count": trade_diag["frozen_count"],
                        "cash_weight": max(0.0, 1.0 - sum(holdings.values())),
                        "turnover": turnover,
                        "cost": cost,
                        "nav_after_cost": nav,
                    }
                )

            nav_rows.append(
                {
                    "trade_date": current_date,
                    "nav": nav,
                    "daily_return": None if prev_date is None else nav / nav_rows[-1]["nav"] - 1.0,
                    "is_rebalance": rebalance,
                    "holding_count": len(holdings),
                    "cash_weight": max(0.0, 1.0 - sum(holdings.values())),
                    "turnover": turnover,
                    "cost": cost,
                }
            )

            for symbol, weight in holdings.items():
                holding_rows.append({"trade_date": current_date, "symbol": symbol, "weight": weight})

            prev_date = current_date

        nav_df = pl.DataFrame(nav_rows)
        trades_df = pl.DataFrame(trade_rows) if trade_rows else pl.DataFrame()
        holdings_df = pl.DataFrame(holding_rows) if holding_rows else pl.DataFrame()
        return BacktestResult(nav=nav_df, trades=trades_df, holdings=holdings_df, metrics=self._metrics(nav_df, trades_df))

    def _price_table(self, start: date, end: date) -> pl.DataFrame:
        return (
            self.portal.canonical_daily()
            .filter((pl.col("trade_date") >= start) & (pl.col("trade_date") <= end))
            .with_columns(
                (pl.col("open") / pl.col("adjusted_prev_close").replace(0, None) - 1.0).alias("overnight_return"),
                (pl.col("close") / pl.col("open").replace(0, None) - 1.0).alias("intraday_return"),
            )
            .select(
                "trade_date",
                "symbol",
                "open",
                "close",
                "volume",
                "adjusted_prev_close",
                "daily_return",
                "overnight_return",
                "intraday_return",
            )
            .sort(["trade_date", "symbol"])
            .collect()
        )

    def _previous_trade_date_map(self) -> dict[date, date]:
        dates = self.portal.trading_dates().collect()["trade_date"].to_list()
        return {dates[i]: dates[i - 1] for i in range(1, len(dates))}

    def _limits_by_date(self, start: date, end: date) -> dict[date, dict[str, tuple[float, float]]]:
        df = (
            self.portal.stock_limits()
            .filter((pl.col("trade_date") >= start) & (pl.col("trade_date") <= end))
            .select("trade_date", "symbol", "up_limit", "down_limit")
            .collect()
        )
        return {
            self._group_key_date(d): {r["symbol"]: (r["up_limit"], r["down_limit"]) for r in g.to_dicts()}
            for d, g in df.group_by("trade_date", maintain_order=True)
        }

    def _suspended_by_date(self, start: date, end: date) -> dict[date, set[str]]:
        df = (
            self.portal.suspensions()
            .filter((pl.col("trade_date") >= start) & (pl.col("trade_date") <= end))
            .select("trade_date", "symbol")
            .collect()
        )
        return {self._group_key_date(d): set(g["symbol"].to_list()) for d, g in df.group_by("trade_date", maintain_order=True)}

    def _target_weights(
        self,
        signal_date: date,
        trade_date: date,
        current_holdings: dict[str, float],
        prices: dict[str, dict],
        limit_by_date: dict[date, dict[str, tuple[float, float]]],
        suspended_by_date: dict[date, set[str]],
    ) -> dict[str, float]:
        selected = self.factors.select_portfolio(signal_date)
        if selected.is_empty() or "weight" not in selected.columns:
            return {}
        raw = selected.select("symbol", "weight")
        st_set = set(self.portal.st_name_flags(trade_date).collect()["symbol"].to_list())
        suspended = suspended_by_date.get(trade_date, set())
        limits = limit_by_date.get(trade_date, {})

        target: dict[str, float] = {}
        for row in raw.to_dicts():
            symbol = row["symbol"]
            price = self._trade_price(prices.get(symbol))
            if price is None or symbol in suspended or symbol in st_set:
                continue
            up_down = limits.get(symbol)
            if up_down is not None:
                up_limit, _ = up_down
                is_new_buy = symbol not in current_holdings
                if is_new_buy and up_limit > 0 and price >= up_limit * 0.9999:
                    continue
            target[symbol] = float(row["weight"])

        return target

    def _rebalance_holdings(
        self,
        current: dict[str, float],
        target: dict[str, float],
        prices: dict[str, dict],
        limits: dict[str, tuple[float, float]],
        suspended: set[str],
        nav: float,
    ) -> tuple[dict[str, float], float, float, dict[str, int]]:
        locked: dict[str, float] = {}
        tradable_target: dict[str, float] = {}
        blocked_buy = 0
        blocked_sell = 0
        frozen = 0

        for symbol in set(current) | set(target):
            current_weight = current.get(symbol, 0.0)
            target_weight = target.get(symbol, 0.0)
            if (
                current_weight > 1e-10
                and target_weight > 1e-10
                and abs(target_weight - current_weight) <= self.config.rebalance_tolerance
            ):
                target_weight = current_weight
            price = self._trade_price(prices.get(symbol))
            if price is None or symbol in suspended:
                if current_weight > 1e-10:
                    locked[symbol] = current_weight
                    frozen += 1
                continue

            up_limit, down_limit = limits.get(symbol, (0.0, 0.0))
            wants_buy = target_weight > current_weight + 1e-10
            wants_sell = target_weight < current_weight - 1e-10
            if wants_buy and up_limit > 0 and price >= up_limit * 0.9999:
                if current_weight > 1e-10:
                    locked[symbol] = current_weight
                blocked_buy += 1
            elif wants_sell and down_limit > 0 and price <= down_limit * 1.0001:
                if current_weight > 1e-10:
                    locked[symbol] = current_weight
                blocked_sell += 1
            else:
                tradable_target[symbol] = target_weight

        locked_weight = sum(locked.values())
        capacity = max(0.0, 1.0 - locked_weight)
        tradable_weight = sum(tradable_target.values())
        scale = min(1.0, capacity / tradable_weight) if tradable_weight > 0 else 0.0
        final = dict(locked)
        for symbol, weight in tradable_target.items():
            scaled = weight * scale
            if scaled > 1e-10:
                final[symbol] = scaled

        symbols = set(current) | set(final)
        buy_turnover = sum(max(final.get(s, 0.0) - current.get(s, 0.0), 0.0) for s in symbols)
        sell_turnover = sum(max(current.get(s, 0.0) - final.get(s, 0.0), 0.0) for s in symbols)
        turnover = buy_turnover + sell_turnover
        portfolio_cash = nav * self.config.initial_capital_cash
        min_commission_rate = (
            self.config.min_commission_cash / portfolio_cash if portfolio_cash > 0 else 0.0
        )
        buy_commission = sum(
            max((final.get(s, 0.0) - current.get(s, 0.0)) * self.config.open_commission, min_commission_rate)
            for s in symbols
            if final.get(s, 0.0) > current.get(s, 0.0) + 1e-10
        )
        sell_cost = sum(
            max((current.get(s, 0.0) - final.get(s, 0.0)) * self.config.close_commission, min_commission_rate)
            + (current.get(s, 0.0) - final.get(s, 0.0)) * self.config.close_tax
            for s in symbols
            if current.get(s, 0.0) > final.get(s, 0.0) + 1e-10
        )
        cost = buy_commission + sell_cost
        diag = {
            "target_count": len(target),
            "blocked_buy_count": blocked_buy,
            "blocked_sell_count": blocked_sell,
            "frozen_count": frozen,
        }
        return final, turnover, cost, diag

    def _trade_price(self, row: dict | None) -> float | None:
        if row is None:
            return None
        price = row.get(self.config.trade_at)
        volume = row.get("volume")
        if price is None or price <= 0 or volume is None or volume <= 0:
            return None
        return float(price)

    def _apply_portfolio_returns(
        self,
        holdings: dict[str, float],
        daily_returns: dict[str, float],
    ) -> tuple[dict[str, float], float]:
        if not holdings:
            return {}, 1.0
        cash_weight = max(0.0, 1.0 - sum(holdings.values()))
        grown: dict[str, float] = {}
        for symbol, weight in holdings.items():
            ret = daily_returns.get(symbol)
            if ret is None:
                raise ValueError(f"missing adjusted return for held symbol {symbol}")
            if abs(ret) > self.config.max_abs_daily_return:
                raise ValueError(f"abnormal adjusted return for held symbol {symbol}: {ret:.6f}")
            grown[symbol] = weight * (1.0 + ret)
        multiplier = cash_weight + sum(grown.values())
        if multiplier <= 0:
            raise ValueError("portfolio value became non-positive")
        return {symbol: value / multiplier for symbol, value in grown.items()}, multiplier

    @staticmethod
    def _group_key_date(key: object) -> date:
        if isinstance(key, tuple):
            return key[0]
        return key

    @staticmethod
    def _metrics(nav: pl.DataFrame, trades: pl.DataFrame) -> dict[str, float | int | str]:
        nav = nav.with_columns(pl.col("daily_return").fill_null(0.0))
        start_nav = nav["nav"][0]
        end_nav = nav["nav"][-1]
        n_days = nav.height
        years = n_days / 252.0
        annual_return = (end_nav / start_nav) ** (1.0 / years) - 1.0 if years > 0 else 0.0
        daily_std = nav.select(pl.col("daily_return").std()).item() or 0.0
        annual_vol = daily_std * 252.0**0.5
        sharpe = annual_return / annual_vol if annual_vol > 0 else 0.0
        drawdown = nav.with_columns((pl.col("nav") / pl.col("nav").cum_max() - 1.0).alias("drawdown"))
        max_drawdown = drawdown.select(pl.col("drawdown").min()).item()
        total_turnover = trades.select(pl.col("turnover").sum()).item() if trades.height else 0.0
        total_cost = trades.select(pl.col("cost").sum()).item() if trades.height else 0.0
        return {
            "start_date": str(nav["trade_date"][0]),
            "end_date": str(nav["trade_date"][-1]),
            "trading_days": n_days,
            "total_return": end_nav / start_nav - 1.0,
            "annual_return": annual_return,
            "annual_volatility": annual_vol,
            "sharpe_like": sharpe,
            "max_drawdown": max_drawdown,
            "rebalance_count": trades.height,
            "total_turnover": total_turnover,
            "total_cost": total_cost,
        }


def write_result(result: BacktestResult, output_dir: str | Path) -> None:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    result.nav.write_csv(out / "nav.csv")
    result.trades.write_csv(out / "trades.csv")
    result.holdings.write_parquet(out / "holdings.parquet")
    pl.DataFrame([result.metrics]).write_csv(out / "metrics.csv")
