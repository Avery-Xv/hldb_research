from datetime import date

import polars as pl
import pytest

from hldb.constants import FILES
from hldb.backtest import DailyBacktester
from hldb.data_portal import DataPortal


def _write_daily(path):
    pl.DataFrame(
        {
            "trade_date": [date(2024, 5, 20), date(2024, 5, 21)],
            "symbol": ["SH600000", "SH600000"],
            "open": [100.0, 25.5],
            "high": [101.0, 26.5],
            "low": [99.0, 25.0],
            "close": [100.0, 26.0],
            "prev_close": [99.0, 25.0],
            "volume": [1.0, 4.0],
            "money": [100.0, 104.0],
            "pct_chg": [1.0, 4.0],
            "limit_up": [110.0, 27.5],
            "limit_down": [90.0, 22.5],
            "num_trades": [10, 40],
        }
    ).write_parquet(path / "daily.parquet")


def test_split_reference_price_is_not_adjusted_twice(tmp_path, monkeypatch):
    _write_daily(tmp_path)
    pl.DataFrame(
        {
            "trade_date": [date(2024, 5, 20), date(2024, 5, 21)],
            "symbol": ["SH600000", "SH600000"],
            "adj_factor": [1.0, 4.0],
        }
    ).write_parquet(tmp_path / "factor.parquet")
    monkeypatch.setitem(FILES, "stock_daily", "daily.parquet")
    monkeypatch.setitem(FILES, "adj_factor", "factor.parquet")

    ex_date = DataPortal(tmp_path).canonical_daily().collect().row(1, named=True)

    assert ex_date["raw_previous_close"] == 100.0
    assert ex_date["adjusted_prev_close"] == 25.0
    assert abs(ex_date["daily_return"] - 0.04) < 1e-12


def test_missing_factor_produces_missing_return(tmp_path, monkeypatch):
    _write_daily(tmp_path)
    pl.DataFrame(
        {
            "trade_date": [date(2024, 5, 20)],
            "symbol": ["SH600000"],
            "adj_factor": [1.0],
        }
    ).write_parquet(tmp_path / "factor.parquet")
    monkeypatch.setitem(FILES, "stock_daily", "daily.parquet")
    monkeypatch.setitem(FILES, "adj_factor", "factor.parquet")

    result = DataPortal(tmp_path).canonical_daily().collect()

    assert result["daily_return"].to_list() == [None, None]


def test_held_symbol_with_missing_adjusted_return_fails_fast():
    backtester = DailyBacktester()
    with pytest.raises(ValueError, match="missing adjusted return.*SH600000"):
        backtester._apply_portfolio_returns({"SH600000": 1.0}, {})


def test_portfolio_weights_drift_with_returns():
    backtester = DailyBacktester()

    holdings, multiplier = backtester._apply_portfolio_returns(
        {"SH600000": 0.5}, {"SH600000": 0.10}
    )

    assert abs(multiplier - 1.05) < 1e-12
    assert abs(holdings["SH600000"] - 0.55 / 1.05) < 1e-12


def test_abnormal_held_return_fails_fast():
    backtester = DailyBacktester()
    with pytest.raises(ValueError, match="abnormal adjusted return.*SH600000"):
        backtester._apply_portfolio_returns({"SH600000": 1.0}, {"SH600000": 1.01})


def test_filtered_targets_are_not_renormalized_and_small_trades_are_skipped():
    backtester = DailyBacktester()
    prices = {
        "SH600000": {"open": 10.0, "close": 10.0, "volume": 100.0},
        "SH600001": {"open": 10.0, "close": 10.0, "volume": 100.0},
    }

    final, turnover, _, _ = backtester._rebalance_holdings(
        {"SH600000": 0.40},
        {"SH600000": 0.403, "SH600001": 0.40},
        prices,
        {},
        set(),
        nav=1.0,
    )

    assert final == {"SH600000": 0.40, "SH600001": 0.40}
    assert abs(turnover - 0.40) < 1e-12
    assert abs(1.0 - sum(final.values()) - 0.20) < 1e-12


def test_zero_volume_row_is_not_tradable():
    backtester = DailyBacktester()
    assert backtester._trade_price({"open": 10.0, "volume": 0.0}) is None
