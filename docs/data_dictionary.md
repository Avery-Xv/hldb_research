# 红利低波策略数据说明

本文档说明本地 parquet 缓存数据的来源、用途、字段口径和后续开发注意事项。目标是把原聚宽策略脚本迁移为本机可运行版本，策略逻辑以 `reference/79_红利低波_可以直接在聚宽做回测和模拟.py` 为主，研报为辅。

## 数据范围

- 回测起点：2015 年。
- 日行情起点：2014-01-01，用于 2015 年首个调仓日前的 252 日历史窗口。
- 代码格式：统一为 `SH600000`、`SZ000001`、`BJxxxxx`。
- 缓存目录：`data/cache/`。
- 数据格式：Parquet。
- 数据来源：本机 ClickHouse 只读库，主要来自 `dwd_dwd`、`ods`、`jydb` 聚源镜像。

## 文件总览

| 文件 | 来源表 | 主要用途 |
|---|---|---|
| `stock_universe_jydb.parquet` | `ods.ods_jydb_secu_main` | 股票池、上市日期、交易所、上市板块 |
| `stock_daily_2014.parquet` | `dwd_dwd.dwd_quant_stock_none_1day_di` | 日行情、波动率、日均成交额 |
| `daily_basic_2015.parquet` | `dwd_dwd.dwd_quant_daily_basic_eod_1day_di` | 市值、换手率、估值、股息率 |
| `tail_30m_money_2015.parquet` | `ods.ods_quant_stock_tushare_none_30min_di` | 尾盘成交占比 |
| `dividend_jydb_2013.parquet` | `dwd_dwd.dwd_quant_dividend_jydb_event_di` | 分红、TTM 股息率、红利支付率 |
| `income_jydb_2012.parquet` | `dwd_dwd.dwd_quant_fin_jydb_income_ann_1quarter_di` | 归母净利润、营业收入、SUE |
| `cashflow_jydb_2012.parquet` | `dwd_dwd.dwd_quant_fin_jydb_cashflow_ann_1quarter_di` | 经营现金流 |
| `fin_indicator_2012.parquet` | `dwd_dwd.dwd_quant_fin_indicator_ann_1quarter_scd2` | ROE、净利增速、现金流占比 |
| `stk_limit_2015.parquet` | `dwd_dwd.dwd_quant_stk_limit_eod_1day_di` | 涨跌停过滤 |
| `suspend_2015.parquet` | `dwd_dwd.dwd_quant_suspend_eod_1day_di` | 停牌过滤 |
| `namechange_2010.parquet` | `dwd_dwd.dwd_quant_namechange_event_di` | ST/名称变更辅助过滤 |
| `list_status_jydb.parquet` | `dwd_dwd.dwd_quant_list_status_jydb_event_di` | 上市、暂停、退市事件 |
| `trade_calendar_2014.parquet` | `ods.ods_ref_trade_cal_tushare_snapshot_of` | 交易日历、月末调仓日 |

## 策略依赖映射

### 股票池

代码原逻辑：

- 全 A 股票池。
- 剔除科创板、北交所。
- 剔除 ST。
- 剔除上市不足 90 天。

本地数据：

- `stock_universe_jydb.parquet`
  - `symbol`：标准股票代码。
  - `inner_code`：聚源证券内部编码。
  - `company_code`：聚源公司代码，用于连接财务类数据。
  - `listed_date`：上市日期。
  - `secu_market`：交易市场，`83` 为上交所，`90` 为深交所，`18` 为北交所。
  - `listed_sector`：上市板块，`7` 为科创板，`8` 为北交所。
  - `listed_state`：上市状态。

迁移建议：

- 剔除北交所：`secu_market != 18` 或 `listed_sector != 8`。
- 剔除科创板：`listed_sector != 7`，也可结合 `symbol` 是否为 `SH688xxx`。
- 上市满 90 天：`rebalance_date - listed_date >= 90 days`。
- ST 过滤可结合 `namechange_2010.parquet` 和当日可得的最新名称判断。

### 日行情与流动性

代码原逻辑：

- 过去 252 日平均成交额。
- 过去 252 日年化波动率。
- 日线收盘价用于 TTM 股息率分母。

本地数据：

- `stock_daily_2014.parquet`
  - `trade_date`
  - `symbol`
  - `open`
  - `high`
  - `low`
  - `close`
  - `prev_close`
  - `volume`
  - `money`
  - `pct_chg`
  - `limit_up`
  - `limit_down`
  - `num_trades`

迁移建议：

- 波动率：按 `symbol` 排序后对 `close` 计算日收益率，滚动 252 日标准差，再乘 `sqrt(252)`。研报使用 `sqrt(242)`，代码使用 `sqrt(252)`，本地迁移若以代码为主应使用 `sqrt(252)`。
- 平均成交额：滚动 252 日 `money` 均值。
- 调仓日取数：使用调仓日前一交易日的数据，避免未来函数。

### 市值、换手率与估值

代码原逻辑：

- 剔除市值最小 20%。
- 低波复合因子中使用低换手率。

本地数据：

- `daily_basic_2015.parquet`
  - `trade_date`
  - `symbol`
  - `turnover_rate`
  - `turnover_rate_f`
  - `total_mv`
  - `circ_mv`
  - `total_share`
  - `float_share`
  - `free_share`
  - `dv_ratio`
  - `dv_ttm`

迁移建议：

- 聚宽 `valuation.market_cap` 对应 `total_mv`。
- 聚宽 `valuation.turnover_ratio` 对应 `turnover_rate`。
- `dv_ttm` 可作为交叉校验字段，但代码当前是用分红事件自算 TTM 股息率。

### 尾盘成交占比

代码原逻辑：

- 取最后一根 30 分钟 K 线成交额。
- `tail_money_ratio = tail_money_30m / daily_money`。

本地数据：

- `tail_30m_money_2015.parquet`
  - `trade_date`
  - `symbol`
  - `tail_money_30m`
  - `tail_bar_time`
  - `bar_count`

迁移建议：

- 日成交额分母用 `stock_daily_2014.money`。
- `tail_money_30m` 已按 `trade_date, symbol` 聚合为每日最后一根 30m K 线的成交额。
- 该文件来自 ODS Tushare 30m 数据，不是 jydb 源；使用它是因为 DWD 30m 表仅覆盖 2022-08 之后。

### 分红与红利指标

代码原逻辑：

- 过滤取消分红或不分配方案。
- 计算年度现金分红。
- 红利支付率 = 年度现金分红 / 年度归母净利润。
- TTM 股息率 = 过去一年每股分红合计 / 最新收盘价。
- 分红支付率需在 `[0, 1]`。
- 按 TTM 股息率取前 300。

本地数据：

- `dividend_jydb_2013.parquet`
  - `symbol`
  - `end_date`
  - `scheme_no`
  - `process`
  - `info_pub_date`
  - `if_dividend`
  - `cash_divi_rmb`
  - `actual_cash_divi_rmb`
  - `total_cash_divi`
  - `cash_divi_a_share`
  - `divi_base`
  - `register_date`
  - `ex_divi_date`
  - `cash_divi_rmb_adj`

迁移建议：

- TTM 每股分红优先使用 `cash_divi_rmb` 或 `cash_divi_rmb_adj`，具体单位需在迁移时用样例校验。
- 年度现金分红可优先使用 `total_cash_divi`；若缺失，再用 `cash_divi_rmb * divi_base` 反推。
- 使用 `info_pub_date` 或 `ex_divi_date` 作为可得性日期需要明确。若复现聚宽代码，应尽量贴近其 `report_date` 口径；若做无未来函数回测，建议按公告/实施日期做 PIT。

### 利润表与 SUE

代码原逻辑：

- 年度归母净利润用于红利支付率。
- SUE 使用归母净利润当前期与去年同期差值，再除以历史同比差值标准差。

本地数据：

- `income_jydb_2012.parquet`
  - `symbol`
  - `period_end`
  - `ann_date`
  - `period_type`
  - `bulletin_type`
  - `accounting_standards`
  - `OperatingRevenue`
  - `TotalOperatingRevenue`
  - `NetProfit`
  - `NPParentCompanyOwners`

迁移建议：

- 年报口径优先筛 `bulletin_type = 20`。
- 调仓日只能使用 `ann_date <= rebalance_date` 的记录。
- SUE 至少需要 5 个季度数据；按代码逻辑，用最近一期和 4 个季度前比较。

### 现金流表与财务指标

代码原逻辑：

- ROE。
- SUE。
- 净利增速加速度。
- 现金流占比。

本地数据：

- `fin_indicator_2012.parquet`
  - `symbol`
  - `period_end`
  - `roe`
  - `roe_waa`
  - `ocf_to_or`
  - `q_ocf_to_or`
  - `netprofit_yoy`
  - `q_netprofit_yoy`
  - `valid_from`
  - `valid_to`
  - `is_current`
  - `pit_confidence`

- `cashflow_jydb_2012.parquet`
  - `NetOperateCashFlow`
  - `SubtotalOperateCashInflow`
  - `SubtotalOperateCashOutflow`

迁移建议：

- `fin_indicator_2012.parquet` 是 SCD2 表。历史回测时应按 `valid_from <= rebalance_date < valid_to` 取当时可见版本，不要直接取 `is_current = 1`。
- 代码里的现金流占比类似聚宽 `indicator.ocf_to_revenue`，本地可优先用 `ocf_to_or` 或 `q_ocf_to_or`。
- 加速增长可用最近两期 `netprofit_yoy` 或 `q_netprofit_yoy` 的差值。

### 交易过滤

代码原逻辑：

- 过滤停牌。
- 过滤 ST。
- 买入过滤涨停。
- 卖出过滤跌停。
- 昨日涨停股不在开盘调仓时强卖，下午若开板再卖。

本地数据：

- `stk_limit_2015.parquet`
  - `trade_date`
  - `symbol`
  - `up_limit`
  - `down_limit`

- `suspend_2015.parquet`
  - `trade_date`
  - `symbol`
  - `suspend_timing`
  - `suspend_type`

- `namechange_2010.parquet`
  - `event_date`
  - `new_name`
  - `name_end_date`

- `list_status_jydb.parquet`
  - `change_date`
  - `change_type`
  - `change_reason`
  - `statement`

迁移建议：

- 停牌判断：调仓日出现在 `suspend_2015` 的股票视为不可交易，后续可根据 `suspend_timing` 精细化。
- 涨停判断：调仓日最新价格接近或等于 `up_limit` 时不买入非持仓股。
- 跌停判断：调仓日最新价格接近或等于 `down_limit` 时不卖出。
- ST 判断：用 `namechange_2010` 中最新有效名称包含 `ST`、`*ST`、`退` 等关键词做近似。

### 交易日历

代码原逻辑有一个偏差：它用自然日跨月判断月底，这可能漏掉真正的月末最后一个交易日。

本地数据：

- `trade_calendar_2014.parquet`
  - `exchange`
  - `cal_date`
  - `is_open`
  - `pretrade_date`

迁移建议：

- 使用 `is_open = 1` 的交易日。
- 以沪深交易日历为准，筛选每年 4、8、10、12 月的最后一个开放交易日作为调仓日。
- 本地迁移建议修正原代码的自然月末判断。

## 本地实现建议

1. 先构建 `DataPortal` 或类似数据访问层，统一提供：
   - `get_universe(date)`
   - `get_daily_window(symbols, end_date, count)`
   - `get_daily_basic(date)`
   - `get_dividend_indicators(date, symbols)`
   - `get_quality_factors(date, symbols)`
   - `get_trading_filters(date)`

2. 所有因子计算使用 `context.previous_date`，避免未来数据。

3. 财务数据使用 PIT 口径：
   - DWD SCD2 表按 `valid_from/valid_to`。
   - jydb 财务表按 `ann_date <= signal_date`。

4. 中间因子建议落盘：
   - 每个调仓日的股票池。
   - 流动性过滤结果。
   - 红利指标。
   - 低波因子。
   - 质量因子。
   - 最终权重。

这样后续排查和对齐聚宽回测会快很多。

## 已知限制

- `tail_30m_money_2015.parquet` 来自 Tushare ODS，不是 jydb 源。
- `daily_basic_2015.parquet` 从 2015-01-01 起，不能用于更早完整回测。
- `stock_daily_2014.parquet` 用于 252 日窗口，若未来要把回测起点提前，需要重新导出更早日行情。
- 分红字段单位需要在迁移实现时用若干样例核验，尤其是 `cash_divi_rmb`、`cash_divi_rmb_adj` 和 `total_cash_divi`。
- ST 过滤当前依赖名称变更事件近似，若后续找到更标准的 ST 状态表，应替换。
