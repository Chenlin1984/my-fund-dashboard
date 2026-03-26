# =================================================
# 【Cell 8】寫入 backtest_engine.py（回測引擎）
# 說明：生成策略回測引擎，計算 Sharpe/Sortino/MaxDD/Calmar 等
#        績效指標，支援月底再平衡模擬。
# 新手提示：直接執行即可，不需要修改。
# =================================================
"""
回測引擎 v13 — Backtest Engine
功能：
  - 模擬策略歷史績效
  - 每月再平衡
  - 與 Benchmark 比較
  - 計算 Sharpe / Sortino / MaxDD / Calmar
"""
import pandas as pd
import numpy as np
from typing import Dict, Optional


# ── 基礎回測 ──────────────────────────────────────────────────────────────
def backtest_portfolio(nav_df: pd.DataFrame,
                       weights: pd.Series,
                       rebalance: str = "ME") -> pd.DataFrame:
    """
    參數：
        nav_df    : 每日 NAV DataFrame（columns=基金代碼）
        weights   : 各基金目標權重（Series，自動歸一化）
        rebalance : 再平衡頻率（'ME'=月底, 'QE'=季底, None=買入持有）
    回傳：
        DataFrame：equity_curve / portfolio_return / drawdown
    """
    # 歸一化權重
    w = weights / weights.sum()

    returns = nav_df.pct_change().dropna()

    if rebalance is None:
        # 買入持有：不再平衡
        port_ret = (returns * w).sum(axis=1)
    else:
        # 每期再平衡
        port_ret_list = []
        for date, row in returns.iterrows():
            port_ret_list.append((row * w).sum())
        port_ret = pd.Series(port_ret_list, index=returns.index)

        # 月底重設權重（簡化版）
        if rebalance == "ME":
            monthly = port_ret.resample("ME").apply(lambda x: (1 + x).prod() - 1)
            port_ret = monthly

    equity_curve = (1 + port_ret).cumprod()
    rolling_max  = equity_curve.cummax()
    drawdown     = (equity_curve - rolling_max) / rolling_max

    return pd.DataFrame({
        "equity_curve":     equity_curve,
        "portfolio_return": port_ret,
        "drawdown":         drawdown,
    })


# ── 績效指標計算 ───────────────────────────────────────────────────────────
def calc_performance_metrics(equity_curve: pd.Series,
                             returns: pd.Series,
                             rf: float = 0.02,
                             freq: int = 12) -> Dict:
    """
    計算投資組合績效指標。
    freq=12 代表月頻，freq=252 代表日頻。
    """
    if len(returns) < 3:
        return {}

    total_return  = float(equity_curve.iloc[-1] - 1)
    ann_return    = float((1 + total_return) ** (freq / len(returns)) - 1)
    ann_vol       = float(returns.std() * np.sqrt(freq))
    sharpe        = round((ann_return - rf) / ann_vol, 4) if ann_vol > 0 else 0.0

    # Sortino（下行標準差）
    downside = returns[returns < 0]
    ann_downside = float(downside.std() * np.sqrt(freq)) if len(downside) > 0 else 0.0
    sortino  = round((ann_return - rf) / ann_downside, 4) if ann_downside > 0 else 0.0

    # Max Drawdown
    rolling_max = equity_curve.cummax()
    drawdown    = (equity_curve - rolling_max) / rolling_max
    max_dd      = round(float(drawdown.min()), 4)

    # Calmar
    calmar = round(ann_return / abs(max_dd), 4) if max_dd != 0 else 0.0

    return {
        "total_return":  round(total_return * 100, 2),
        "ann_return":    round(ann_return * 100, 2),
        "ann_vol":       round(ann_vol * 100, 2),
        "sharpe":        sharpe,
        "sortino":       sortino,
        "max_drawdown":  round(max_dd * 100, 2),
        "calmar":        calmar,
    }


# ── Benchmark 比較 ─────────────────────────────────────────────────────────
def compare_with_benchmark(port_curve: pd.Series,
                           bench_curve: pd.Series) -> Dict:
    """
    比較策略 vs Benchmark
    回傳：超額報酬 / Tracking Error / Information Ratio
    """
    # 對齊時間軸
    common = port_curve.index.intersection(bench_curve.index)
    if len(common) < 3:
        return {"error": "資料不足，無法比較"}

    p = port_curve.loc[common]
    b = bench_curve.loc[common]

    p_ret = p.pct_change().dropna()
    b_ret = b.pct_change().dropna()

    excess      = p_ret - b_ret
    alpha       = round(float(excess.mean() * 12) * 100, 2)   # 年化超額報酬%
    tracking_err= round(float(excess.std() * np.sqrt(12)) * 100, 2)
    info_ratio  = round(alpha / tracking_err, 4) if tracking_err > 0 else 0.0

    p_total = round(float(p.iloc[-1] / p.iloc[0] - 1) * 100, 2)
    b_total = round(float(b.iloc[-1] / b.iloc[0] - 1) * 100, 2)

    return {
        "port_total_return":   p_total,
        "bench_total_return":  b_total,
        "alpha_ann":           alpha,
        "tracking_error":      tracking_err,
        "information_ratio":   info_ratio,
    }


# ── 快速單基金回測包裝 ─────────────────────────────────────────────────────
def quick_backtest(nav_series: pd.Series, freq: int = 12) -> Dict:
    """
    對單一基金淨值序列做快速回測，回傳績效指標。
    nav_series：每月（或每日）淨值序列
    """
    if len(nav_series) < 4:
        return {"error": "淨值資料不足（需至少 4 期）"}

    returns     = nav_series.pct_change().dropna()
    equity      = (1 + returns).cumprod()
    metrics     = calc_performance_metrics(equity, returns, rf=0.02, freq=freq)
    metrics["periods"] = len(returns)
    return metrics
