# -*- coding: utf-8 -*-
"""
79_hldb.py
红利低波增强策略 - 聚宽回测版本
复现研报：20260605-国泰海通证券-量化选股系列（四）——如何构建低波策略
调仓频率：每年4、8、10、12月底最后一个交易日
"""

from jqdata import *
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# ====================== 初始化 ======================
def initialize(context):
    # 设定基准
    set_benchmark('000905.XSHG')
    # 用真实价格交易
    set_option('use_real_price', True)
    # 打开防未来函数
    set_option("avoid_future_data", True)
    # 将滑点设置为0
    set_slippage(FixedSlippage(0))
    # 设置交易成本万分之三
    set_order_cost(
        OrderCost(open_tax=0, close_tax=0.001, open_commission=0.0003,
                  close_commission=0.0003, close_today_commission=0, min_commission=5),
        type='stock'
    )
    # 过滤order中低于error级别的日志
    log.set_level('order', 'error')

    # 初始化全局变量
    g.stock_num = 100
    g.max_weight = 0.05
    g.top_div_rank = 300
    g.quant_20pct = 0.2
    g.winsor_q = (0.05, 0.95)
    g.rebalance_months = [4, 8, 10, 12]
    g.hold_list = []
    g.high_limit_list = []
    g.is_rebalance_day = False

    # 设置定时任务
    run_daily(prepare_stock_list, time='9:00', reference_security='000300.XSHG')
    run_daily(rebalance_trade, time='9:31', reference_security='000300.XSHG')
    run_daily(check_limit_up, time='14:00', reference_security='000300.XSHG')
    run_daily(print_position_info, time='15:10', reference_security='000300.XSHG')


# ====================== 每日准备 ======================
def prepare_stock_list(context):
    # 获取已持有列表
    g.hold_list = list(context.portfolio.positions.keys())

    # 获取昨日涨停列表
    if g.hold_list:
        df = get_price(g.hold_list, end_date=context.previous_date, frequency='daily',
                       fields=['close', 'high_limit'],  count=1, panel=False, fill_paused=False)
        if not df.empty:
            df = df[df['close'] == df['high_limit']]
            g.high_limit_list = list(df.code)
        else:
            g.high_limit_list = []
    else:
        g.high_limit_list = []

    # 判断今天是否是调仓日（4/8/10/12月底最后一个交易日）
    today = context.current_dt
    g.is_rebalance_day = False
    if today.month in g.rebalance_months:
        # 判断是否是本月最后一个交易日
        next_day = today + timedelta(days=1)
        if next_day.month != today.month:
            g.is_rebalance_day = True


# ====================== 调仓执行 ======================
def rebalance_trade(context):
    if not g.is_rebalance_day:
        return

    target_list, target_weights = get_stock_list_and_weights(context)
    if not target_list:
        log.info("未选出目标股票，跳过调仓")
        return

    # 过滤停牌、涨停、跌停、ST
    target_list = filter_paused_stock(target_list)
    target_list = filter_st_stock(target_list)
    target_list = filter_limitup_stock(context, target_list)
    target_list = filter_limitdown_stock(context, target_list)

    # 卖出不在目标列表中的持仓（排除昨日涨停股）
    for stock in g.hold_list:
        if (stock not in target_list) and (stock not in g.high_limit_list):
            log.info("卖出[%s]" % stock)
            position = context.portfolio.positions[stock]
            close_position(position)
        else:
            log.info("继续持有[%s]" % stock)

    # 按权重买入目标股
    for stock in target_list:
        weight = target_weights.get(stock, 0)
        target_value = context.portfolio.total_value * weight
        current_value = context.portfolio.positions[stock].value if stock in context.portfolio.positions else 0
        # 偏差超过1%才调整
        if abs(target_value - current_value) > context.portfolio.total_value * 0.005:
            order_target_value(stock, target_value)


# ====================== 核心选股逻辑 ======================
def get_stock_list_and_weights(context):
    date = context.previous_date

    # ---------- 步骤0：股票池 ----------
    all_stocks = get_all_securities('stock', date).index.tolist()
    all_stocks = filter_kcbj_stock(all_stocks)
    all_stocks = filter_st_stock(all_stocks)
    all_stocks = filter_new_stock(context, all_stocks, 90)

    if not all_stocks:
        return [], {}

    # ---------- 步骤1：流动性筛选 ----------
    # 市值
    q_val = query(valuation.code, valuation.market_cap, valuation.turnover_ratio).filter(
        valuation.code.in_(all_stocks))
    val_df = get_fundamentals(q_val, date=date)
    if val_df.empty:
        return [], {}
    val_df = val_df.set_index('code')

    # 过去252日成交额
    money_df = get_price(all_stocks, end_date=date, frequency='daily',
                         fields=['money'], count=252, panel=False)
    if money_df.empty:
        return [], {}
    avg_money = money_df.groupby('code')['money'].mean()

    # 剔除市值最小20%
    cap_cut = val_df['market_cap'].quantile(g.quant_20pct)
    pool_1 = val_df[val_df['market_cap'] >= cap_cut].index.tolist()

    # 剔除日均成交额最小20%
    money_cut = avg_money.quantile(g.quant_20pct)
    pool_1 = [c for c in pool_1 if c in avg_money.index and avg_money[c] >= money_cut]

    if not pool_1:
        return [], {}

    # ---------- 步骤2：分红约束 + 高波剔除 ----------
    df_div = calc_dividend_indicators(context, pool_1, date)
    df_div = df_div[(df_div['dividend_payout_ratio'] >= 0) & (df_div['dividend_payout_ratio'] <= 1)]
    pool_2 = df_div['code'].tolist()

    if not pool_2:
        return [], {}

    # 波动率：过去252日年化
    close_df = get_price(pool_2, end_date=date, frequency='daily',
                         fields=['close'], count=253, panel=False)
    if close_df.empty:
        return [], {}
    close_pivot = close_df.pivot(index='time', columns='code', values='close')
    ret_df = close_pivot.pct_change().iloc[1:]
    vol = ret_df.std() * np.sqrt(252)
    vol_cut = vol.quantile(1 - g.quant_20pct)
    pool_2 = vol[vol <= vol_cut].index.tolist()

    if not pool_2:
        return [], {}

    # ---------- 步骤3：TTM股息率前300 ----------
    df_div_step3 = calc_dividend_indicators(context, pool_2, date)
    df_div_step3 = df_div_step3.sort_values('ttm_dividend_yield', ascending=False).head(g.top_div_rank)
    pool_3 = df_div_step3['code'].tolist()

    if not pool_3:
        return [], {}

    # ---------- 步骤4：复合打分 ----------
    # 低波因子数据
    close_df_2 = get_price(pool_3, end_date=date, frequency='daily',
                           fields=['close', 'high', 'low', 'money'], count=253, panel=False)
    if close_df_2.empty:
        return [], {}

    close_pivot_2 = close_df_2.pivot(index='time', columns='code', values='close')
    ret_df_2 = close_pivot_2.pct_change().iloc[1:]
    vol_series = ret_df_2.std() * np.sqrt(252)

    # 换手率
    turnover_series = val_df.loc[pool_3, 'turnover_ratio'] if 'turnover_ratio' in val_df.columns else pd.Series(index=pool_3)

    # 尾盘成交占比：30m最后一根 / 日频全天
    tail_df = get_price(pool_3, end_date=date, frequency='30m',
                        fields=['money'], count=1, panel=False)
    if not tail_df.empty:
        tail_money = tail_df.set_index('code')['money']
        daily_money = close_df_2.groupby('code')['money'].last()
        tail_ratio = tail_money / daily_money
    else:
        tail_ratio = pd.Series(np.nan, index=pool_3)

    df_low = pd.DataFrame({
        'code': pool_3,
        'volatility': vol_series.reindex(pool_3).values,
        'turnover_ratio': turnover_series.reindex(pool_3).values,
        'tail_money_ratio': tail_ratio.reindex(pool_3).values
    })

    # 质量因子
    df_quality = calc_quality_factors(context, pool_3, date)

    df_merge = pd.merge(df_low, df_quality, on='code', how='left')
    df_merge = pd.merge(df_merge, df_div_step3[['code', 'ttm_dividend_yield']], on='code', how='left')
    df_merge = df_merge.dropna()

    if len(df_merge) == 0:
        return [], {}

    # 打分
    df_merge['score_vol'] = factor_qcut_score(df_merge['volatility'], ascending=False)
    df_merge['score_turn'] = factor_qcut_score(df_merge['turnover_ratio'], ascending=False)
    df_merge['score_tail'] = factor_qcut_score(df_merge['tail_money_ratio'], ascending=False)
    df_merge['lowvol_composite'] = df_merge[['score_vol', 'score_turn', 'score_tail']].mean(axis=1)

    df_merge['score_roe'] = factor_qcut_score(df_merge['roe'], ascending=True)
    df_merge['score_sue'] = factor_qcut_score(df_merge['sue'], ascending=True)
    df_merge['score_acc'] = factor_qcut_score(df_merge['accelerate_growth'], ascending=True)
    df_merge['score_cf'] = factor_qcut_score(df_merge['cash_flow_ratio'], ascending=True)
    df_merge['quality_composite'] = df_merge[['score_roe', 'score_sue', 'score_acc', 'score_cf']].mean(axis=1)

    df_merge['final_score'] = (df_merge['lowvol_composite'] + df_merge['quality_composite']) / 2
    df_final = df_merge.sort_values('final_score', ascending=False).head(g.stock_num)

    # 权重：股息率 + 波动率倒数加权，上限5%
    df_final['raw_weight'] = df_final['ttm_dividend_yield'] + (1.0 / df_final['volatility'])
    df_final['weight'] = df_final['raw_weight'] / df_final['raw_weight'].sum()
    df_final['weight'] = df_final['weight'].clip(upper=g.max_weight)
    df_final['weight'] = df_final['weight'] / df_final['weight'].sum()

    target_list = df_final['code'].tolist()
    target_weights = dict(zip(df_final['code'], df_final['weight']))
    return target_list, target_weights


# ====================== 红利指标计算 ======================
def calc_dividend_indicators(context, stock_list, base_date):
    base_dt = pd.to_datetime(base_date)
    one_year_ago = pd.to_datetime(base_dt - timedelta(days=365))

    # 获取分红数据（分批，每批500）
    div_parts = []
    batch_size = 500
    for i in range(0, len(stock_list), batch_size):
        batch = stock_list[i:i + batch_size]
        q = query(
            finance.STK_XR_XD.code,
            finance.STK_XR_XD.report_date,
            finance.STK_XR_XD.plan_progress,
            finance.STK_XR_XD.implementation_bonusnote,
            finance.STK_XR_XD.distributed_share_base_board,
            finance.STK_XR_XD.distributed_share_base_shareholders,
            finance.STK_XR_XD.distributed_share_base_implement,
            finance.STK_XR_XD.bonus_ratio_rmb,
            finance.STK_XR_XD.bonus_ratio_hkd,
            finance.STK_XR_XD.bonus_ratio_usd,
            finance.STK_XR_XD.exchange_rate
        ).filter(
            finance.STK_XR_XD.code.in_(batch),
            finance.STK_XR_XD.report_date >= one_year_ago,
            finance.STK_XR_XD.report_date <= base_dt
        )
        df = finance.run_query(q)
        if not df.empty:
            div_parts.append(df)

    if not div_parts:
        return pd.DataFrame(columns=['code', 'dividend_payout_ratio', 'ttm_dividend_yield'])

    df_div = pd.concat(div_parts, ignore_index=True)

    # 无效分红过滤
    df_div['invalid'] = np.where(
        (df_div['plan_progress'] == '取消分红') |
        (df_div['implementation_bonusnote'].str.contains('不分配不转赠', na=False)),
        1, 0
    )
    df_div = df_div[df_div['invalid'] == 0]

    # 去重：按code+report_date保留最新阶段（实施方案优先）
    df_div = df_div.sort_values(['code', 'report_date', 'plan_progress'],
                                ascending=[True, True, False])
    df_div = df_div.drop_duplicates(subset=['code', 'report_date'], keep='last')

    # 股本优先级
    df_div['share_base'] = df_div['distributed_share_base_implement']
    df_div['share_base'] = df_div['share_base'].where(df_div['share_base'] > 0,
                                                      df_div['distributed_share_base_shareholders'])
    df_div['share_base'] = df_div['share_base'].where(df_div['share_base'] > 0,
                                                      df_div['distributed_share_base_board'])

    # 单条分红金额
    df_div['single_bonus'] = 0.0
    valid_mask = df_div['share_base'] > 0
    df_div.loc[valid_mask, 'single_bonus'] = (
        df_div.loc[valid_mask, 'bonus_ratio_rmb'] / 10
        * df_div.loc[valid_mask, 'share_base'] * 10000
    )
    hk_mask = valid_mask & (df_div['bonus_ratio_hkd'] > 0) & (df_div['exchange_rate'] > 0)
    df_div.loc[hk_mask, 'single_bonus'] += (
        df_div.loc[hk_mask, 'bonus_ratio_hkd'] / 10
        * df_div.loc[hk_mask, 'share_base'] * 10000
        * df_div.loc[hk_mask, 'exchange_rate']
    )
    usd_mask = valid_mask & (df_div['bonus_ratio_usd'] > 0) & (df_div['exchange_rate'] > 0)
    df_div.loc[usd_mask, 'single_bonus'] += (
        df_div.loc[usd_mask, 'bonus_ratio_usd'] / 10
        * df_div.loc[usd_mask, 'share_base'] * 10000
        * df_div.loc[usd_mask, 'exchange_rate']
    )

    # 年度现金分红
    df_div['year'] = pd.to_datetime(df_div['report_date']).dt.year
    df_year_bonus = df_div.groupby(['code', 'year'], as_index=False)['single_bonus'].sum()
    df_year_bonus.rename(columns={'single_bonus': 'annual_cash_bonus'}, inplace=True)

    # 年度归母净利润（取12-31年报数据）
    year_list = sorted(df_year_bonus['year'].unique().tolist())
    profit_list = []
    for y in year_list:
        q_p = query(
            income.code,
            income.np_parent_company_owners
        ).filter(
            income.code.in_(stock_list),
            income.statDate == f"{y}-12-31"
        )
        df_p = get_fundamentals(q_p, date=base_date)
        if not df_p.empty:
            df_p['year'] = y
            profit_list.append(df_p[['code', 'year', 'np_parent_company_owners']])

    if profit_list:
        df_profit = pd.concat(profit_list, ignore_index=True)
    else:
        df_profit = pd.DataFrame(columns=['code', 'year', 'np_parent_company_owners'])
    df_profit.rename(columns={'np_parent_company_owners': 'annual_net_profit'}, inplace=True)

    # 红利支付率
    df_payout = pd.merge(df_year_bonus, df_profit, on=['code', 'year'], how='left')
    df_payout['annual_net_profit'] = df_payout['annual_net_profit'].replace(0, np.nan)
    df_payout['dividend_payout_ratio'] = df_payout['annual_cash_bonus'] / df_payout['annual_net_profit']
    df_payout_latest = df_payout.sort_values(['code', 'year']).groupby('code').last().reset_index()
    df_payout_latest = df_payout_latest[['code', 'dividend_payout_ratio']]

    # TTM每股分红
    df_ttm_div = df_div[pd.to_datetime(df_div['report_date']) >= one_year_ago].copy()
    df_ttm_div['div_per_share'] = df_ttm_div['bonus_ratio_rmb'] / 10
    mask_hkd = (df_ttm_div['bonus_ratio_hkd'] > 0) & (df_ttm_div['exchange_rate'] > 0)
    df_ttm_div.loc[mask_hkd, 'div_per_share'] += (
        df_ttm_div.loc[mask_hkd, 'bonus_ratio_hkd'] / 10 * df_ttm_div.loc[mask_hkd, 'exchange_rate']
    )
    mask_usd = (df_ttm_div['bonus_ratio_usd'] > 0) & (df_ttm_div['exchange_rate'] > 0)
    df_ttm_div.loc[mask_usd, 'div_per_share'] += (
        df_ttm_div.loc[mask_usd, 'bonus_ratio_usd'] / 10 * df_ttm_div.loc[mask_usd, 'exchange_rate']
    )
    df_ttm_total = df_ttm_div.groupby('code')['div_per_share'].sum().reset_index()
    df_ttm_total.rename(columns={'div_per_share': 'ttm_div_per_share'}, inplace=True)

    # TTM股息率
    price_df = get_price(stock_list, end_date=base_date, frequency='daily',
                         fields=['close'], count=1, panel=False)
    if not price_df.empty:
        price_lookup = price_df.set_index('code')[['close']].rename(columns={'close': 'price'})
    else:
        price_lookup = pd.DataFrame(columns=['price'])

    df_yield = pd.merge(df_ttm_total, price_lookup, left_on='code', right_index=True, how='left')
    df_yield['ttm_dividend_yield'] = df_yield['ttm_div_per_share'] / df_yield['price']
    df_yield = df_yield[['code', 'ttm_dividend_yield']]

    # 合并
    df_final = pd.merge(df_payout_latest, df_yield, on='code', how='left')
    df_final = df_final.fillna({'dividend_payout_ratio': 0, 'ttm_dividend_yield': 0})
    return df_final


# ====================== 季度日期工具 ======================
def get_quarter_end_dates(start_date, end_date):
    """自动生成区间内所有季末日期（3/31, 6/30, 9/30, 12/31）"""
    sd = pd.to_datetime(start_date)
    ed = pd.to_datetime(end_date)
    quarter_ends = pd.date_range(start=sd, end=ed, freq='Q')
    return [d.strftime("%Y-%m-%d") for d in quarter_ends]


# ====================== 质量因子计算 ======================
def calc_quality_factors(context, stock_list, base_date):
    base_dt = pd.to_datetime(base_date)
    quarter_start = base_dt - timedelta(days=900)

    # 拉取base_date之前所有季度末的财务数据
    quarter_dates = get_quarter_end_dates(quarter_start, base_dt)

    # income表：季度净利润（用于SUE），遍历季度日期拉取
    inc_parts = []
    for dt in quarter_dates:
        q_inc = query(
            income.code,
            income.np_parent_company_owners,
            income.statDate
        ).filter(income.code.in_(stock_list))
        df = get_fundamentals(q_inc, date=dt)
        if not df.empty:
            inc_parts.append(df)

    if not inc_parts:
        return pd.DataFrame(columns=['code', 'roe', 'sue', 'accelerate_growth', 'cash_flow_ratio'])

    df_inc = pd.concat(inc_parts, ignore_index=True)
    df_inc = df_inc.drop_duplicates(subset=['code', 'statDate'])
    if len(df_inc) < 5:
        return pd.DataFrame(columns=['code', 'roe', 'sue', 'accelerate_growth', 'cash_flow_ratio'])

    df_inc['statDate'] = pd.to_datetime(df_inc['statDate'])
    df_pivot_np = df_inc.pivot_table(index='code', columns='statDate',
                                     values='np_parent_company_owners', aggfunc='first')
    df_pivot_np = df_pivot_np.sort_index(axis=1)

    # SUE
    def calc_sue(pivot_df):
        if len(pivot_df.columns) < 5:
            return pd.Series(np.nan, index=pivot_df.index)
        curr_col = pivot_df.columns[-1]
        ly_col = pivot_df.columns[-5]
        diff = pivot_df[curr_col] - pivot_df[ly_col]
        diff_seq = []
        for i in range(len(pivot_df.columns) - 4):
            diff_seq.append(pivot_df.iloc[:, i + 4] - pivot_df.iloc[:, i])
        diff_df = pd.concat(diff_seq, axis=1)
        std = diff_df.std(axis=1)
        sue = np.where(std > 1e-6, diff / std, np.nan)
        return pd.Series(sue, index=pivot_df.index)

    sue_series = calc_sue(df_pivot_np)

    # indicator表：ROE、现金流占比、加速增长，同样遍历季度日期拉取
    ind_parts = []
    for dt in quarter_dates:
        q_ind = query(
            indicator.code,
            indicator.roe,
            indicator.ocf_to_revenue,
            indicator.inc_net_profit_to_shareholders_year_on_year,
            indicator.statDate
        ).filter(indicator.code.in_(stock_list))
        df = get_fundamentals(q_ind, date=dt)
        if not df.empty:
            ind_parts.append(df)

    if not ind_parts:
        return pd.DataFrame(columns=['code', 'roe', 'sue', 'accelerate_growth', 'cash_flow_ratio'])

    df_ind = pd.concat(ind_parts, ignore_index=True)
    df_ind = df_ind.drop_duplicates(subset=['code', 'statDate'])
    df_ind['statDate'] = pd.to_datetime(df_ind['statDate'])

    # 只保留 <= base_date 的数据
    df_ind = df_ind[df_ind['statDate'] <= base_dt]

    # ROE：取最近值
    df_roe = df_ind.sort_values(['code', 'statDate']).groupby('code')['roe'].last().reset_index()

    # 现金流占比：ocf_to_revenue，最近4个季度均值
    df_cf = df_ind.groupby('code').apply(
        lambda x: x.sort_values('statDate')['ocf_to_revenue'].tail(4).mean()
    ).reset_index()
    df_cf.columns = ['code', 'cash_flow_ratio']

    # 加速增长：最近两个季度同比增速差值
    def calc_acc_from_indicator(group):
        sorted_vals = group.sort_values('statDate')['inc_net_profit_to_shareholders_year_on_year'].tail(2).values
        if len(sorted_vals) < 2 or pd.isna(sorted_vals).any():
            return np.nan
        return sorted_vals[-1] - sorted_vals[-2]

    df_acc = df_ind.groupby('code').apply(calc_acc_from_indicator).reset_index()
    df_acc.columns = ['code', 'accelerate_growth']

    # 合并
    df_quality = pd.DataFrame({'code': df_pivot_np.index, 'sue': sue_series.values})
    df_quality = pd.merge(df_quality, df_roe, on='code', how='left')
    df_quality = pd.merge(df_quality, df_acc, on='code', how='left')
    df_quality = pd.merge(df_quality, df_cf, on='code', how='left')
    df_quality = df_quality.dropna()

    # 缩尾
    q_low, q_high = g.winsor_q
    for col in ['roe', 'sue', 'accelerate_growth', 'cash_flow_ratio']:
        if col in df_quality.columns:
            df_quality[col] = df_quality[col].clip(
                lower=df_quality[col].quantile(q_low),
                upper=df_quality[col].quantile(q_high))
    return df_quality


# ====================== 十分位打分 ======================
def factor_qcut_score(series, ascending=True):
    s = series.copy()
    if not ascending:
        s = -s
    rank = s.rank(method='first')
    score = pd.qcut(rank, 10, labels=range(1, 11), duplicates='drop').astype(int)
    return score


# ====================== 涨停处理 ======================
def check_limit_up(context):
    now_time = context.current_dt
    if g.high_limit_list:
        for stock in g.high_limit_list:
            current_data = get_price(stock, end_date=now_time, frequency='1m',
                                     fields=['close', 'high_limit'], skip_paused=False,
                                     fq='pre', count=1, panel=False, fill_paused=True)
            if not current_data.empty and current_data.iloc[0, 0] < current_data.iloc[0, 1]:
                log.info("[%s]涨停打开，卖出" % stock)
                position = context.portfolio.positions[stock]
                close_position(position)
            else:
                log.info("[%s]涨停，继续持有" % stock)


# ====================== 过滤函数 ======================
def filter_paused_stock(stock_list):
    current_data = get_current_data()
    return [stock for stock in stock_list if not current_data[stock].paused]


def filter_st_stock(stock_list):
    current_data = get_current_data()
    return [stock for stock in stock_list
            if not current_data[stock].is_st
            and 'ST' not in current_data[stock].name
            and '*' not in current_data[stock].name
            and '退' not in current_data[stock].name]


def filter_kcbj_stock(stock_list):
    return [s for s in stock_list
            if not (s[0] == '4' or s[0] == '8' or s[:2] == '68')]


def filter_new_stock(context, stock_list, d):
    yesterday = context.previous_date
    return [stock for stock in stock_list
            if not yesterday - get_security_info(stock).start_date < timedelta(days=d)]


def filter_limitup_stock(context, stock_list):
    last_prices = history(1, unit='1m', field='close', security_list=stock_list)
    current_data = get_current_data()
    return [stock for stock in stock_list if stock in context.portfolio.positions.keys()
            or last_prices[stock][-1] < current_data[stock].high_limit]


def filter_limitdown_stock(context, stock_list):
    last_prices = history(1, unit='1m', field='close', security_list=stock_list)
    current_data = get_current_data()
    return [stock for stock in stock_list if stock in context.portfolio.positions.keys()
            or last_prices[stock][-1] > current_data[stock].low_limit]


# ====================== 交易辅助函数 ======================
def open_position(security, value):
    order = order_target_value(security, value)
    if order is not None and order.filled > 0:
        return True
    return False


def close_position(position):
    security = position.security
    order = order_target_value(security, 0)
    if order is not None:
        if order.status == OrderStatus.held and order.filled == order.amount:
            return True
    return False


# ====================== 打印持仓信息 ======================
def print_position_info(context):
    trades = get_trades()
    for _trade in trades.values():
        print('成交记录：' + str(_trade))
    for position in list(context.portfolio.positions.values()):
        securities = position.security
        cost = position.avg_cost
        price = position.price
        ret = 100 * (price / cost - 1)
        value = position.value
        amount = position.total_amount
        print('代码:{}'.format(securities))
        print('成本价:{}'.format(format(cost, '.2f')))
        print('现价:{}'.format(price))
        print('收益率:{}%'.format(format(ret, '.2f')))
        print('持仓(股):{}'.format(amount))
        print('市值:{}'.format(format(value, '.2f')))
        print('———————————————————————————————————')
    print('———————————————————————————————————————分割线————————————————————————————————————————')
