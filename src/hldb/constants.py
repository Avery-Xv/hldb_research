from __future__ import annotations

from pathlib import Path


CACHE_DIR = Path("data/cache")

FILES = {
    "adj_factor": "adj_factor_2015.parquet",
    "calendar": "trade_calendar_2014.parquet",
    "cashflow": "cashflow_jydb_2012.parquet",
    "daily_basic": "daily_basic_2015.parquet",
    "dividend": "dividend_jydb_2013.parquet",
    "fin_indicator": "fin_indicator_2012.parquet",
    "income": "income_jydb_2012.parquet",
    "list_status": "list_status_jydb.parquet",
    "namechange": "namechange_2010.parquet",
    "stock_daily": "stock_daily_2014.parquet",
    "stk_limit": "stk_limit_2015.parquet",
    "suspend": "suspend_2015.parquet",
    "tail_30m": "tail_30m_money_2015.parquet",
    "universe": "stock_universe_jydb.parquet",
}

CANONICAL_SYMBOL_PATTERN = r"^(SH|SZ|BJ)[0-9]{6}$"
REBALANCE_MONTHS = (4, 8, 10, 12)

# if_adjusted is not the main PIT rule. It is only used as a stable tie-breaker
# when multiple consolidated records share the same symbol/period/announcement.
IF_ADJUSTED_PRIORITY = {5: 4, 4: 3, 2: 2, 1: 1}
