# =================================================
# 【Cell 9】寫入 portfolio_engine.py（投資組合引擎）
# 說明：生成投資組合最佳化引擎，包含六因子評分、配息安全分析、
#        最大化 Sharpe 配置、即時風險預警、凱利公式計算。
# 新手提示：直接執行即可，不需要修改。
# =================================================
"""
投資組合引擎 v13 — Portfolio Engine
包含：
  1. Fund Factor Model    基金六因子評分
  2. Dividend Safety      配息安全分析
  3. Portfolio Optimizer  最大化 Sharpe 投資組合最佳化
  4. Risk Alert System    即時風險預警
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional

# ── scipy 可選（Optimizer 需要）────────────────────────────────────────────
try:
    from scipy.optimize import minimize
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False


# ══════════════════════════════════════════════════════════════════════════
# 一、Fund Factor Model — 六因子評分模型
# ══════════════════════════════════════════════════════════════════════════

def calc_fund_factor_score(fund_data: Dict,
                           risk_table: Optional[Dict] = None,
                           expense_ratio: Optional[float] = None) -> Dict:
    """
    六因子評分：Sharpe / Sortino / MaxDD / Calmar / Alpha / 費用率
    輸入：
        fund_data   : 含 perf(1Y/3Y/5Y)、metrics(max_drawdown, sharpe 等) 的 dict
        risk_table  : MoneyDJ 風險表（含 Sharpe、標準差 等）
        expense_ratio: 費用率 % (optional)
    回傳：
        {"score": 0~100, "grade": "A/B/C/D", "factors": {...}}
    """
    factors = {}
    total_w = 0.0
    total_s = 0.0

    rt = (risk_table or {}).get("一年", {}) if risk_table else {}
    m  = fund_data.get("metrics", {}) or {}
    pf = fund_data.get("perf", {}) or {}

    # ── 1. Sharpe Ratio（權重 25）──────────────────────────────────────
    sharpe = None
    try:
        sharpe = float(rt.get("Sharpe") or m.get("sharpe") or 0)
    except (TypeError, ValueError):
        sharpe = None
    if sharpe is not None:
        s = min(max((sharpe + 1) / 2 * 100, 0), 100)   # -1~+1 → 0~100
        factors["Sharpe"] = {"value": sharpe, "score": round(s, 1), "weight": 25}
        total_s += s * 25; total_w += 25

    # ── 2. Sortino Ratio（權重 15）─────────────────────────────────────
    sortino = m.get("sortino")
    if sortino is not None:
        try:
            sortino = float(sortino)
            s = min(max((sortino + 1) / 2 * 100, 0), 100)
            factors["Sortino"] = {"value": sortino, "score": round(s, 1), "weight": 15}
            total_s += s * 15; total_w += 15
        except (TypeError, ValueError):
            pass

    # ── 3. Max Drawdown（權重 20，正向：回撤越小越好）──────────────────
    maxdd = m.get("max_drawdown")
    if maxdd is None:
        try: maxdd = float((rt.get("最大回撤") or "0").replace("%", ""))
        except Exception: maxdd = None
    if maxdd is not None:
        try:
            maxdd_f = float(maxdd)
            # 0% → 100分；-30% → 0分
            s = min(max((1 - abs(maxdd_f) / 30) * 100, 0), 100)
            factors["MaxDrawdown"] = {"value": round(maxdd_f, 2), "score": round(s, 1), "weight": 20}
            total_s += s * 20; total_w += 20
        except (TypeError, ValueError):
            pass

    # ── 4. Calmar Ratio（權重 10）──────────────────────────────────────
    calmar = m.get("calmar")
    if calmar is not None:
        try:
            calmar = float(calmar)
            s = min(max(calmar / 2 * 100, 0), 100)
            factors["Calmar"] = {"value": calmar, "score": round(s, 1), "weight": 10}
            total_s += s * 10; total_w += 10
        except (TypeError, ValueError):
            pass

    # ── 5. Alpha（超額報酬，權重 20）───────────────────────────────────
    tr1y = pf.get("1Y")
    adr  = m.get("annual_div_rate", 0) or 0
    if tr1y is not None:
        try:
            alpha = float(tr1y) - float(adr)   # 真實收益 = 含息報酬 - 配息率
            s = min(max((alpha + 10) / 20 * 100, 0), 100)  # -10%~+10% → 0~100
            factors["Alpha"] = {"value": round(alpha, 2), "score": round(s, 1), "weight": 20}
            total_s += s * 20; total_w += 20
        except (TypeError, ValueError):
            pass

    # ── 6. 費用率（Expense Ratio，權重 10，越低越好）───────────────────
    er = expense_ratio or m.get("expense_ratio")
    if er is not None:
        try:
            er = float(er)
            s = min(max((3 - er) / 3 * 100, 0), 100)   # 0%→100分；3%→0分
            factors["ExpenseRatio"] = {"value": er, "score": round(s, 1), "weight": 10}
            total_s += s * 10; total_w += 10
        except (TypeError, ValueError):
            pass

    # ── 總分 ──────────────────────────────────────────────────────────
    final_score = round(total_s / total_w, 1) if total_w > 0 else 50.0
    if final_score >= 75:   grade = "A"
    elif final_score >= 55: grade = "B"
    elif final_score >= 40: grade = "C"
    else:                   grade = "D"

    return {
        "score":   final_score,
        "grade":   grade,
        "factors": factors,
        "factors_count": len(factors),
    }


# ══════════════════════════════════════════════════════════════════════════
# 二、Dividend Safety Model — 配息安全分析
# ══════════════════════════════════════════════════════════════════════════

def dividend_safety(total_return: Optional[float],
                    dividend_yield: float,
                    nav_change: Optional[float] = None) -> Dict:
    """
    配息安全分析。
    參數：
        total_return  : 含息報酬率 % (1Y)
        dividend_yield: 年化配息率 %
        nav_change    : 淨值變化率 % (可選，用於交叉驗證)
    回傳：
        {status, coverage, eating_principal, alert_level, message}
    """
    if dividend_yield <= 0:
        return {"status": "N/A", "alert_level": "grey",
                "message": "無配息資料", "coverage": None, "eating_principal": False}

    if total_return is None:
        return {"status": "無報酬資料", "alert_level": "grey",
                "message": "需要含息報酬率資料", "coverage": None, "eating_principal": False}

    # 覆蓋率：含息報酬率 / 配息率
    coverage = round(total_return / dividend_yield, 4)
    eating   = total_return < dividend_yield

    if coverage < 0:
        status = "🔴 嚴重吃本金（報酬為負）"
        alert  = "red"
        msg    = f"含息報酬{total_return:.1f}% < 0，配息{dividend_yield:.1f}%，本金快速流失"
    elif coverage < 1.0:
        status = "🔴 吃本金警示"
        alert  = "red"
        msg    = f"含息報酬{total_return:.1f}% < 配息{dividend_yield:.1f}%，覆蓋率{coverage:.2f}"
    elif coverage < 1.2:
        status = "🟡 邊緣（建議觀察）"
        alert  = "yellow"
        msg    = f"覆蓋率{coverage:.2f}，略高於1但空間不足，需觀察淨值趨勢"
    else:
        status = "🟢 健康"
        alert  = "green"
        msg    = f"含息報酬{total_return:.1f}% 充分覆蓋配息{dividend_yield:.1f}%，覆蓋率{coverage:.2f}"

    # 淨值交叉驗證
    nav_warn = None
    if nav_change is not None and nav_change < -5:
        nav_warn = f"⚠️ 淨值下跌{nav_change:.1f}%，配息源頭值得確認"

    return {
        "status":          status,
        "alert_level":     alert,
        "coverage":        coverage,
        "eating_principal": eating,
        "message":         msg,
        "nav_warning":     nav_warn,
    }


# ══════════════════════════════════════════════════════════════════════════
# 三、Portfolio Optimizer — 最大化 Sharpe 投資組合最佳化
# ══════════════════════════════════════════════════════════════════════════

def optimize_portfolio(returns_df: pd.DataFrame,
                       rf: float = 0.02,
                       max_weight: float = 0.6,
                       min_weight: float = 0.0) -> Dict:
    """
    最大化 Sharpe Ratio 最佳化投資組合。
    參數：
        returns_df : 各基金月報酬率 DataFrame (列=時間, 欄=基金)
        rf         : 無風險利率（年化）
        max_weight : 單一資產最大權重
        min_weight : 單一資產最小權重
    回傳：
        {weights, expected_return, expected_vol, expected_sharpe, status}
    """
    if not _SCIPY_OK:
        return {"status": "❌ 需要安裝 scipy：pip install scipy",
                "weights": None}

    n = len(returns_df.columns)
    if n < 2:
        return {"status": "❌ 需要至少 2 檔基金", "weights": None}

    mean_ret  = returns_df.mean() * 12              # 年化報酬
    cov_matrix = returns_df.cov() * 12              # 年化共變異數

    # ── 目標函數：最大化 Sharpe（最小化負 Sharpe）────────────────────
    def neg_sharpe(w):
        port_ret = float(w @ mean_ret)
        port_vol = float(np.sqrt(w @ cov_matrix.values @ w))
        if port_vol <= 0:
            return 0.0
        return -(port_ret - rf) / port_vol

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds      = [(min_weight, max_weight)] * n
    w0          = np.ones(n) / n

    try:
        res = minimize(neg_sharpe, w0, method="SLSQP",
                       bounds=bounds, constraints=constraints,
                       options={"maxiter": 500, "ftol": 1e-9})
        if not res.success:
            # fallback：等權
            opt_w = np.ones(n) / n
            status = "⚠️ 最佳化未收斂，回退等權配置"
        else:
            opt_w  = res.x
            status = "✅ 最佳化成功"
    except Exception as e:
        opt_w  = np.ones(n) / n
        status = f"❌ 最佳化失敗：{e}"

    weights_dict = {col: round(float(w), 4)
                    for col, w in zip(returns_df.columns, opt_w)}
    exp_ret = float(opt_w @ mean_ret)
    exp_vol = float(np.sqrt(opt_w @ cov_matrix.values @ opt_w))
    exp_shp = round((exp_ret - rf) / exp_vol, 4) if exp_vol > 0 else 0.0

    return {
        "status":            status,
        "weights":           weights_dict,
        "expected_return":   round(exp_ret * 100, 2),
        "expected_vol":      round(exp_vol * 100, 2),
        "expected_sharpe":   exp_shp,
    }


# ══════════════════════════════════════════════════════════════════════════
# 四、Risk Alert System — 即時風險預警
# ══════════════════════════════════════════════════════════════════════════

def risk_alert(drawdown:       Optional[float] = None,
               coverage:       Optional[float] = None,
               regime:         str = "",
               fed_direction:  str = "",
               hy_spread:      Optional[float] = None,
               vix:            Optional[float] = None) -> List[Dict]:
    """
    即時風險預警系統。
    參數（均可選）：
        drawdown      : 最大回撤（負值，e.g. -0.25）
        coverage      : 配息覆蓋率（<1 吃本金）
        regime        : 景氣循環標籤
        fed_direction : 'up' 升息 / 'down' 降息 / ''
        hy_spread     : HY 信用利差 %
        vix           : VIX 恐慌指數
    回傳：
        [{"level": "red/yellow/green", "type": str, "message": str}]
    """
    alerts = []

    # ── 最大回撤 ──────────────────────────────────────────────────────
    if drawdown is not None:
        if drawdown < -0.30:
            alerts.append({"level": "red",    "type": "MaxDrawdown",
                           "message": f"🔴 最大回撤 {drawdown*100:.1f}%，已超過 30% 高危門檻，建議減碼"})
        elif drawdown < -0.20:
            alerts.append({"level": "yellow", "type": "MaxDrawdown",
                           "message": f"🟡 最大回撤 {drawdown*100:.1f}%，接近 20% 警戒線，持續觀察"})

    # ── 配息覆蓋率 ─────────────────────────────────────────────────────
    if coverage is not None:
        if coverage < 1.0:
            alerts.append({"level": "red",    "type": "DividendRisk",
                           "message": f"🔴 配息覆蓋率 {coverage:.2f} < 1，正在吃本金，考慮汰換"})
        elif coverage < 1.2:
            alerts.append({"level": "yellow", "type": "DividendRisk",
                           "message": f"🟡 配息覆蓋率 {coverage:.2f}，邊緣狀態，需監控淨值趨勢"})

    # ── 景氣衰退 ───────────────────────────────────────────────────────
    if "衰退" in regime:
        alerts.append({"level": "red",    "type": "RegimeAlert",
                       "message": "🔴 景氣衰退期：建議降低股票型比重，增加投資等級債 / 貨幣型"})
    elif "過熱" in regime:
        alerts.append({"level": "yellow", "type": "RegimeAlert",
                       "message": "🟡 景氣過熱期：注意通膨壓力，降低久期，減少高收益債"})

    # ── 升息 ───────────────────────────────────────────────────────────
    if fed_direction == "up":
        alerts.append({"level": "yellow", "type": "RateAlert",
                       "message": "🟡 升息環境：降低長久期債券，避免利率風險"})

    # ── HY 信用利差擴大 ────────────────────────────────────────────────
    if hy_spread is not None and hy_spread > 6.0:
        alerts.append({"level": "red",    "type": "CreditSpread",
                       "message": f"🔴 HY 利差 {hy_spread:.2f}%，市場避險情緒高，減少非投資等級債"})
    elif hy_spread is not None and hy_spread > 4.5:
        alerts.append({"level": "yellow", "type": "CreditSpread",
                       "message": f"🟡 HY 利差 {hy_spread:.2f}%，信用風險升高，保持謹慎"})

    # ── VIX 恐慌 ───────────────────────────────────────────────────────
    if vix is not None and vix > 35:
        alerts.append({"level": "red",    "type": "VIXAlert",
                       "message": f"🔴 VIX {vix:.1f} 極度恐慌，可能是逢低加碼機會（需搭配其他確認）"})
    elif vix is not None and vix > 25:
        alerts.append({"level": "yellow", "type": "VIXAlert",
                       "message": f"🟡 VIX {vix:.1f} 恐慌升高，市場波動加劇，縮短操作週期"})

    # ── 無警示 ─────────────────────────────────────────────────────────
    if not alerts:
        alerts.append({"level": "green", "type": "AllClear",
                       "message": "✅ 目前無重大風險預警，維持現有配置"})

    return alerts


# ══════════════════════════════════════════════════════════════════════
# v16.0 Task 3: 凱利公式 (Kelly Criterion)
# f* = (b*p - q) / b   Half-Kelly = f*/2
# 輸入：series (pd.Series NAV), risk_free=0.02
# ══════════════════════════════════════════════════════════════════════
def calc_kelly(series: "pd.Series",
               lookback: int = 252,
               risk_free: float = 0.02) -> Dict:
    """
    凱利公式：根據歷史日報酬計算最佳資金投入比例
    - b  = 平均獲利日 / 平均虧損日  (賠率)
    - p  = 正報酬日佔比              (勝率)
    - q  = 1 - p                     (敗率)
    - f* = (b*p - q) / b             (全凱利)
    - Half-Kelly = f*/2              (建議實用值)
    邊界保護：f* 超過 1 時夾在 [0, 1]
    """
    if series is None or len(series) < 30:
        return {"kelly": None, "half_kelly": None,
                "win_rate": None, "odds": None, "note": "資料不足"}
    try:
        import numpy as np
        s = series.tail(lookback).dropna()
        if len(s) < 30:
            return {"kelly": None, "half_kelly": None,
                    "win_rate": None, "odds": None, "note": "資料不足"}
        r = s.pct_change().dropna()
        wins  = r[r > 0]
        losses= r[r < 0]
        if len(wins) == 0 or len(losses) == 0:
            return {"kelly": None, "half_kelly": None,
                    "win_rate": None, "odds": None, "note": "無法計算"}
        p  = len(wins) / len(r)
        q  = 1 - p
        b      = wins.mean() / abs(losses.mean())     # 賠率
        _f_raw = (b * p - q) / b                     # 全凱利（未夾取，負值代表期望值為負）
        f_star = float(np.clip(_f_raw, 0, 1))        # 夾在 [0,1]
        half_k = round(f_star / 2, 4)
        return {
            "kelly":          round(f_star, 4),
            "kelly_raw":      round(_f_raw, 4),   # 負值代表數學期望為負
            "half_kelly":     half_k,
            "half_kelly_pct": round(half_k * 100, 1),
            "win_rate":       round(p, 4),
            "win_rate_pct":   round(p * 100, 1),
            "odds":           round(b, 4),
            "note": (
                f"勝率{p*100:.1f}% 賠率{b:.2f}x → 建議投入{half_k*100:.1f}%資金"
                if f_star > 0 else
                f"勝率{p*100:.1f}% 賠率{b:.2f}x，期望值為負(f*={_f_raw:.3f})，建議不加碼"
            ),
        }
    except Exception as e:
        return {"kelly": None, "half_kelly": None,
                "win_rate": None, "odds": None, "note": str(e)[:60]}
