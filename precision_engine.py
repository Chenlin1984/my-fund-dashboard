"""
精準策略引擎 v4.0
複合風險溫度計 (Composite Risk Thermometer)
+ 微觀防護盾 (Micro Defense Shield — 三率檢核)
適用於 2026 年 K 型分化行情
"""
import logging
import re

import numpy as np
import pandas as pd

logger = logging.getLogger("PrecisionStrategyEngine")

# ── 常見持倉名稱 → Ticker 對照表 ─────────────────────────────────────
_TW_NAME_MAP = {
    "台積電": "2330.TW", "聯發科": "2454.TW", "鴻海":   "2317.TW",
    "聯電":   "2303.TW", "日月光": "3711.TW", "瑞昱":   "2379.TW",
    "力積電": "6770.TW", "南亞科": "2408.TW", "威剛":   "4967.TW",
    "富邦金": "2881.TW", "國泰金": "2882.TW", "中信金": "2891.TW",
    "兆豐金": "2886.TW", "台新金": "2887.TW", "玉山金": "2884.TW",
    "台達電": "2308.TW", "廣達":   "2382.TW", "緯創":   "3231.TW",
    "群聯":   "8299.TW", "旺宏":   "2337.TW", "大立光": "3008.TW",
}
_US_NAME_MAP = {
    "NVIDIA": "NVDA", "APPLE": "AAPL", "MICROSOFT": "MSFT",
    "ALPHABET": "GOOGL", "GOOGLE": "GOOGL", "AMAZON": "AMZN",
    "META": "META", "TESLA": "TSLA", "BROADCOM": "AVGO",
    "QUALCOMM": "QCOM", "TSMC": "TSM", "SAMSUNG": "005930.KS",
    "ASML": "ASML", "AMD": "AMD", "INTEL": "INTC",
    "JPMORGAN": "JPM", "BERKSHIRE": "BRK-B", "VISA": "V",
    "MASTERCARD": "MA", "EXXON": "XOM", "UNITEDHEALTH": "UNH",
    "JOHNSON": "JNJ", "ABBVIE": "ABBV", "ELI LILLY": "LLY",
    "NOVO NORDISK": "NVO", "NETFLIX": "NFLX", "SALESFORCE": "CRM",
}


class PrecisionStrategyEngine:
    """精準策略引擎：複合風險溫度計 + 微觀防護盾"""

    def __init__(self):
        self.logger = logging.getLogger("PrecisionStrategyEngine")

    # ── 1. 複合風險溫度計 ─────────────────────────────────────────────
    def calculate_composite_risk(self, df_macro: pd.DataFrame) -> float:
        """
        計算宏觀複合風險溫度計
        需傳入含 'VIX', 'HY_Spread', 'Yield_Curve_10Y_2Y' 的歷史 DataFrame（≥20 筆）

        Risk_Score = Z_VIX×0.3 + Z_HY×0.4 + Z_YC×0.3
        > 1.5 且快速攀升 → 強制提高現金/短債部位，衛星嚴格停利
        """
        if df_macro is None or df_macro.empty or len(df_macro) < 20:
            self.logger.warning("宏觀數據筆數不足（<%d），返回中性值 0.0", len(df_macro) if df_macro is not None else 0)
            return 0.0
        try:
            latest = df_macro.iloc[-1]
            means  = df_macro.mean()
            stds   = df_macro.std().replace(0, np.nan)   # 防分母為零

            z_vix   = (latest["VIX"]               - means["VIX"])               / stds["VIX"]
            z_hy    = (latest["HY_Spread"]          - means["HY_Spread"])          / stds["HY_Spread"]
            z_yield = (latest["Yield_Curve_10Y_2Y"] - means["Yield_Curve_10Y_2Y"]) / stds["Yield_Curve_10Y_2Y"]

            risk_score = float(z_vix * 0.3 + z_hy * 0.4 + z_yield * 0.3)
            return round(risk_score if not np.isnan(risk_score) else 0.0, 2)
        except KeyError as e:
            self.logger.error("缺少必要欄位: %s", e)
            return 0.0
        except Exception as e:
            self.logger.error("複合風險計算異常: %s", e)
            return 0.0

    def risk_score_strategy(self, risk_score: float) -> dict:
        """
        根據 Risk_Score 返回策略研判
        Returns: {level, color, icon, action, cash_pct}
        """
        if risk_score > 1.5:
            return {"level": "極高風險", "color": "#f44336", "icon": "🚨",
                    "action": "流動性危機前兆：核心現金/短債 ≥50%，衛星嚴格停利出場，不宜追高",
                    "cash_pct": 50}
        elif risk_score > 0.8:
            return {"level": "風險偏高", "color": "#ff7043", "icon": "⚠️",
                    "action": "流動性收縮：核心配置防禦性資產，衛星部位縮減至 20% 以內",
                    "cash_pct": 30}
        elif risk_score > 0.0:
            return {"level": "中性偏高", "color": "#ff9800", "icon": "🔔",
                    "action": "市場情緒緊張但未惡化：維持現有配置，設好停利停損位",
                    "cash_pct": 15}
        elif risk_score > -0.5:
            return {"level": "中性偏低", "color": "#66bb6a", "icon": "✅",
                    "action": "風險可控：正常核心/衛星配置，可適度加碼成長部位",
                    "cash_pct": 10}
        else:
            return {"level": "風險極低", "color": "#42a5f5", "icon": "🚀",
                    "action": "流動性寬鬆：積極配置，股 60%+，左側布局高成長衛星",
                    "cash_pct": 5}

    def build_macro_df(self, indicators: dict) -> pd.DataFrame:
        """
        從 macro_engine indicators dict 組裝對齊的 VIX/HY_Spread/Yield_Curve DataFrame
        VIX 為週頻 → 重採樣為月頻後與 HY_SPREAD / YIELD_10Y2Y 對齊
        """
        try:
            vix_s = (indicators.get("VIX")        or {}).get("series")
            hy_s  = (indicators.get("HY_SPREAD")   or {}).get("series")
            yc_s  = (indicators.get("YIELD_10Y2Y") or {}).get("series")

            if any(s is None for s in [vix_s, hy_s, yc_s]):
                missing = [k for k, s in [("VIX", vix_s), ("HY_SPREAD", hy_s), ("YIELD_10Y2Y", yc_s)] if s is None]
                self.logger.warning("指標序列缺失: %s", missing)
                return pd.DataFrame()

            def _to_monthly(s, name: str) -> pd.Series:
                s = pd.Series(s).copy()
                s.index = pd.to_datetime(s.index, errors="coerce")
                s = s[s.index.notna()].dropna()
                # 判斷是否為週頻（平均間隔 < 20 天）
                if len(s) > 2:
                    avg_days = (s.index[-1] - s.index[0]).days / max(len(s) - 1, 1)
                    if avg_days < 20:
                        s = s.resample("MS").mean()
                s.name = name
                return s

            df = pd.concat([
                _to_monthly(vix_s, "VIX"),
                _to_monthly(hy_s,  "HY_Spread"),
                _to_monthly(yc_s,  "Yield_Curve_10Y_2Y"),
            ], axis=1).dropna()

            if len(df) < 20:
                self.logger.warning("對齊後資料筆數不足 20（實際 %d）", len(df))
                return pd.DataFrame()
            return df
        except Exception as e:
            self.logger.error("build_macro_df 失敗: %s", e)
            return pd.DataFrame()

    # ── 2. 微觀防護盾 ────────────────────────────────────────────────
    def evaluate_fund_three_ratios(self, fund_holdings: list) -> str:
        """
        掃描基金前十大持倉三率（毛利率/營益率/淨利率）QoQ 動能
        fund_holdings: [{'stock': 'NVDA', 'gross_margin_diff': 1.2,
                          'op_margin_diff': 0.8, 'net_margin_diff': 1.0}, ...]
        """
        if not fund_holdings:
            return "無法解析持倉，跳過三率檢核"
        total_momentum, valid_stocks = 0.0, 0
        for stock_data in fund_holdings:
            try:
                gd = float(stock_data.get("gross_margin_diff", 0.0))
                od = float(stock_data.get("op_margin_diff",    0.0))
                nd = float(stock_data.get("net_margin_diff",   0.0))
                total_momentum += gd + od + nd
                valid_stocks   += 1
            except (ValueError, TypeError):
                continue
        if valid_stocks == 0:
            return "持倉三率數據格式異常"
        avg = total_momentum / valid_stocks
        if avg > 2.0:
            return "🟢 核心持倉三率強勢雙升，具備實質基本面防護"
        elif avg < -2.0:
            return "🔴 核心持倉三率顯著衰退，警惕估值虛漲風險（價值陷阱）"
        else:
            return "🟡 核心持倉三率持平，需搭配技術面布林通道研判"

    def fetch_stock_three_ratios(self, holding_name: str) -> "dict | None":
        """
        以 yfinance 抓取持倉股最近兩季財報，計算三率 QoQ 差值
        Returns: {stock, ticker, q_new, q_old,
                  gross/op/net _margin_new/old/diff} or None
        """
        ticker_sym = self._resolve_ticker(holding_name)
        if not ticker_sym:
            self.logger.debug("無法解析 Ticker: %s", holding_name)
            return None
        try:
            import yfinance as yf
            tkr = yf.Ticker(ticker_sym)
            # 相容新舊 yfinance API
            qf = getattr(tkr, "quarterly_income_stmt", None)
            if qf is None or (hasattr(qf, "empty") and qf.empty):
                qf = getattr(tkr, "quarterly_financials", None)
            if qf is None or (hasattr(qf, "empty") and qf.empty) or qf.shape[1] < 2:
                return None

            def _find_row(keywords: list) -> "pd.Series | None":
                """不分大小寫、忽略空格，匹配第一個命中的財報列"""
                for kw in keywords:
                    matches = [i for i in qf.index
                               if kw.lower() in str(i).lower().replace(" ", "")]
                    if matches:
                        return qf.loc[matches[0]]
                return None

            rev = _find_row(["totalrevenue", "revenue"])
            gp  = _find_row(["grossprofit"])
            op  = _find_row(["operatingincome", "ebit"])
            ni  = _find_row(["netincome"])
            if rev is None:
                return None

            quarters = []
            for i in range(min(2, qf.shape[1])):
                r = float(rev.iloc[i])
                if not r:
                    continue
                quarters.append({
                    "quarter":      str(qf.columns[i])[:10],
                    "gross_margin": round(float(gp.iloc[i]) / r * 100, 2) if gp is not None else None,
                    "op_margin":    round(float(op.iloc[i]) / r * 100, 2) if op is not None else None,
                    "net_margin":   round(float(ni.iloc[i]) / r * 100, 2) if ni is not None else None,
                })
            if len(quarters) < 2:
                return None

            def _diff(key: str) -> float:
                v0, v1 = quarters[0].get(key), quarters[1].get(key)
                return round(v0 - v1, 2) if (v0 is not None and v1 is not None) else 0.0

            return {
                "stock":             holding_name,
                "ticker":            ticker_sym,
                "q_new":             quarters[0]["quarter"],
                "q_old":             quarters[1]["quarter"],
                "gross_margin_new":  quarters[0].get("gross_margin"),
                "gross_margin_old":  quarters[1].get("gross_margin"),
                "op_margin_new":     quarters[0].get("op_margin"),
                "op_margin_old":     quarters[1].get("op_margin"),
                "net_margin_new":    quarters[0].get("net_margin"),
                "net_margin_old":    quarters[1].get("net_margin"),
                "gross_margin_diff": _diff("gross_margin"),
                "op_margin_diff":    _diff("op_margin"),
                "net_margin_diff":   _diff("net_margin"),
            }
        except Exception as e:
            self.logger.warning("三率抓取失敗 %s(%s): %s", holding_name, ticker_sym, e)
            return None

    def _resolve_ticker(self, name: str) -> "str | None":
        """
        從持倉名稱解析 Ticker Symbol
        優先順序：台股4碼 → 中文名對照 → 英文名對照 → 純字母短代碼
        """
        if not name:
            return None
        name_up = name.upper().strip()

        # 台股：字串含獨立 4 位數字
        tw_match = re.search(r"\b(\d{4})\b", name)
        if tw_match:
            return tw_match.group(1) + ".TW"

        # 中文名稱對照
        for cn_key, sym in _TW_NAME_MAP.items():
            if cn_key in name:
                return sym

        # 英文公司名對照（部分匹配）
        for en_key, sym in _US_NAME_MAP.items():
            if en_key in name_up:
                return sym

        # 2~5 大寫字母直接當作 Ticker（如 NVDA、AAPL）
        if re.fullmatch(r"[A-Z]{2,5}", name_up):
            return name_up

        return None


# ── 模組層工具函式（供 app.py 直接呼叫）────────────────────────────
def risk_score_gauge_html(risk_score: float, strategy: dict) -> str:
    """
    返回複合風險溫度計的 HTML 卡片字串
    """
    color     = strategy["color"]
    level     = strategy["level"]
    action    = strategy["action"]
    cash_pct  = strategy["cash_pct"]
    icon      = strategy["icon"]
    # gauge bar：線性映射 [-3, +3] → [0%, 100%]
    gauge_pct = min(100, max(0, int((risk_score + 3) / 6 * 100)))
    return (
        f"<div style='background:#161b22;border:2px solid {color};"
        f"border-radius:12px;padding:16px 20px;margin:12px 0'>"
        f"<div style='display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:10px'>"
        f"<div>"
        f"<div style='color:#888;font-size:11px;letter-spacing:1px;margin-bottom:2px'>複合風險溫度計 Risk Score</div>"
        f"<div style='color:{color};font-size:34px;font-weight:900;line-height:1.1'>{risk_score:+.2f}</div>"
        f"<div style='color:{color};font-size:12px;font-weight:700;margin-top:3px'>{level}</div>"
        f"</div>"
        f"<div style='text-align:right'>"
        f"<div style='color:#888;font-size:10px'>建議現金/短債部位</div>"
        f"<div style='color:#fff;font-size:26px;font-weight:700'>{cash_pct}%</div>"
        f"</div>"
        f"</div>"
        f"<div style='background:#0d1117;border-radius:6px;height:10px;margin-bottom:10px;overflow:hidden'>"
        f"<div style='background:linear-gradient(90deg,#42a5f5 0%,#66bb6a 35%,#ff9800 65%,#f44336 100%);"
        f"width:{gauge_pct}%;height:100%;border-radius:6px'></div>"
        f"</div>"
        f"<div style='color:#ccc;font-size:12px'>{icon} {action}</div>"
        f"<div style='color:#444;font-size:10px;margin-top:6px'>"
        f"公式：Z_{{VIX}}×0.3 + Z_{{HY}}×0.4 + Z_{{YC}}×0.3 ｜ &gt;1.5 強制提現金</div>"
        f"</div>"
    )


def three_ratio_row_html(r: dict) -> str:
    """
    返回單一持倉三率檢核列的 HTML 字串
    r = fetch_stock_three_ratios 的回傳值
    """
    def _fmt(v):
        return f"{v:+.1f}%" if isinstance(v, (int, float)) else "N/A"

    def _color(v):
        if not isinstance(v, (int, float)):
            return "#888"
        return "#00c853" if v > 0.5 else ("#f44336" if v < -0.5 else "#ff9800")

    gd = r.get("gross_margin_diff", 0)
    od = r.get("op_margin_diff",    0)
    nd = r.get("net_margin_diff",   0)
    momentum = (gd or 0) + (od or 0) + (nd or 0)
    bg = "#061a06" if momentum > 2 else ("#1a0606" if momentum < -2 else "#161b22")
    border = "#00c853" if momentum > 2 else ("#f44336" if momentum < -2 else "#30363d")

    return (
        f"<div style='background:{bg};border:1px solid {border};border-radius:8px;"
        f"padding:8px 12px;margin:4px 0;display:flex;align-items:center;gap:12px;flex-wrap:wrap'>"
        f"<div style='flex:1.5;font-size:11px;color:#ccc'>{r.get('stock','')[:20]}"
        f"<span style='color:#555;margin-left:6px;font-size:10px'>{r.get('ticker','')}</span></div>"
        f"<div style='flex:1;font-size:10px;color:#666'>{r.get('q_old','')[-7:]}→{r.get('q_new','')[-7:]}</div>"
        f"<div style='text-align:center'>"
        f"<div style='color:#666;font-size:9px'>毛利率</div>"
        f"<div style='color:{_color(gd)};font-weight:700;font-size:12px'>{_fmt(gd)}</div></div>"
        f"<div style='text-align:center'>"
        f"<div style='color:#666;font-size:9px'>營益率</div>"
        f"<div style='color:{_color(od)};font-weight:700;font-size:12px'>{_fmt(od)}</div></div>"
        f"<div style='text-align:center'>"
        f"<div style='color:#666;font-size:9px'>淨利率</div>"
        f"<div style='color:{_color(nd)};font-weight:700;font-size:12px'>{_fmt(nd)}</div></div>"
        f"</div>"
    )
