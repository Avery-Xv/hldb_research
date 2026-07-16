# 红利低波本地缓存数据

本目录保存从本机 ClickHouse 导出的 parquet 缓存，用于后续把 `reference/79_红利低波_可以直接在聚宽做回测和模拟.py` 的依赖从聚宽 API 改成本地数据读取。

导出口径：以代码复现为主，研报为辅；回测起点按 2015 年开始准备。日行情从 2014-01-01 起导出，用于 2015 年首个调仓日前的 252 日窗口。

## 文件清单

| 文件 | 行数 | 来源 | 用途 |
|---|---:|---|---|
| `stock_universe_jydb.parquet` | 8,553 | `ods.ods_jydb_secu_main` | 股票池、上市日期、市场、板块、上市状态 |
| `stock_daily_2014.parquet` | 14,659,833 | `dwd_dwd.dwd_quant_stock_none_1day_di` | 日线 OHLCV、成交额、252 日波动率、252 日均成交额 |
| `adj_factor_2015.parquet` | 11,988,985 | `dwd_dwd.dwd_quant_adj_factor_eod_1day_di` | 官方累计后复权因子，用于持有期总收益和波动率 |
| `daily_basic_2015.parquet` | 11,409,140 | `dwd_dwd.dwd_quant_daily_basic_eod_1day_di` | 总市值、流通市值、换手率、股息率等日频估值数据 |
| `tail_30m_money_2015.parquet` | 11,625,360 | `ods.ods_quant_stock_tushare_none_30min_di` | 每个股票每日最后一根 30m K 线成交额，用于尾盘成交占比 |
| `dividend_jydb_2013.parquet` | 96,411 | `dwd_dwd.dwd_quant_dividend_jydb_event_di` | 分红事件、TTM 股息率、红利支付率 |
| `income_jydb_2012.parquet` | 889,465 | `dwd_dwd.dwd_quant_fin_jydb_income_ann_1quarter_di` | 归母净利润、营业收入、SUE、红利支付率分母 |
| `cashflow_jydb_2012.parquet` | 781,823 | `dwd_dwd.dwd_quant_fin_jydb_cashflow_ann_1quarter_di` | 经营现金流，作为现金流质量因子的补充来源 |
| `fin_indicator_2012.parquet` | 187,480 | `dwd_dwd.dwd_quant_fin_indicator_ann_1quarter_scd2` | ROE、净利增速、现金流占比等质量因子 |
| `stk_limit_2015.parquet` | 13,878,464 | `dwd_dwd.dwd_quant_stk_limit_eod_1day_di` | 涨跌停价格过滤 |
| `suspend_2015.parquet` | 334,372 | `dwd_dwd.dwd_quant_suspend_eod_1day_di` | 停牌过滤 |
| `namechange_2010.parquet` | 3,820 | `dwd_dwd.dwd_quant_namechange_event_di` | ST/名称变更辅助过滤 |
| `list_status_jydb.parquet` | 5,687 | `dwd_dwd.dwd_quant_list_status_jydb_event_di` | 上市、暂停、退市状态事件 |
| `trade_calendar_2014.parquet` | 4,575 | `ods.ods_ref_trade_cal_tushare_snapshot_of` | 交易日历、月末最后交易日判断 |

## 策略字段映射

| 聚宽代码字段/函数 | 本地缓存字段 |
|---|---|
| `get_all_securities('stock')` | `stock_universe_jydb` |
| `get_security_info(stock).start_date` | `stock_universe_jydb.listed_date` |
| `valuation.market_cap` | `daily_basic_2015.total_mv` |
| `valuation.turnover_ratio` | `daily_basic_2015.turnover_rate` |
| `get_price(... fields=['close','high','low','money'])` | `stock_daily_2014.close/high/low/money` |
| 过去 252 日成交额均值 | `stock_daily_2014.money` rolling mean |
| 过去 252 日年化波动率 | `stock_daily_2014.close` pct-change rolling std |
| 最后一根 30m 成交额 | `tail_30m_money_2015.tail_money_30m` |
| 分红事件 | `dividend_jydb_2013` |
| `income.np_parent_company_owners` | `income_jydb_2012.NPParentCompanyOwners` |
| `indicator.roe` | `fin_indicator_2012.roe` |
| `indicator.ocf_to_revenue` | `fin_indicator_2012.ocf_to_or` 或 `q_ocf_to_or` |
| 净利润同比增速 | `fin_indicator_2012.netprofit_yoy` 或 `q_netprofit_yoy` |
| 涨跌停价 | `stk_limit_2015.up_limit/down_limit` |
| 停牌 | `suspend_2015` |

## 口径注意

- 股票代码统一使用 `SH600000` / `SZ000001` / `BJxxxxx` 格式。
- 代码中的科创板和北交所过滤可用 `stock_universe_jydb.listed_sector` 或 symbol 前缀实现；科创板为 `listed_sector = 7`，北交所为 `secu_market = 18` 或 `listed_sector = 8`。
- `tail_30m_money_2015` 来自 ODS Tushare 30m 数据，不是 jydb 源。它是为了覆盖 2015 起的尾盘成交占比；DWD 30m 表只覆盖 2022-08 之后。
- `fin_indicator_2012` 是 SCD2 表，后续本地实现需要按调仓日使用 `valid_from <= date < valid_to` 或等价 PIT 口径取值。
- 复权收益使用 `close_t * adj_factor_t / (close_{t-1} * adj_factor_{t-1}) - 1`。分母必须是上一实际交易日原始收盘价，不能使用除权日行情中已经调整过的 `prev_close`。
- 若严格复现代码，需要用 `trade_calendar_2014` 判断 4/8/10/12 月最后一个交易日；不要沿用代码里“自然月最后一天”的判断。
