"""
📊 策略選股清單
依景氣象限篩選策略清單，顯示即時報價與殖利率
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import yfinance as yf
import pandas as pd

st.set_page_config(page_title="策略選股清單", layout="wide", page_icon="📊")

# ── 台灣市場慣例：紅漲綠跌 ──────────────────────────────
UP_COLOR   = "#FF4B4B"
DOWN_COLOR = "#00C07F"
FLAT_COLOR = "#888888"

# ── 策略清單（預設精選標的）────────────────────────────
STRATEGIES: dict[str, list[str]] = {
    "🏦 7%存股聖經":    ["0056.TW","00878.TW","00919.TW","00929.TW","2884.TW","2882.TW","2881.TW","5880.TW"],
    "🚀 VCP強勢突破":   ["2330.TW","2454.TW","3008.TW","6505.TW","NVDA","AMD","MSFT","TSM"],
    "📉 低基期債券ETF": ["00679B.TW","00695B.TW","00696B.TW","00697B.TW","TLT","IEF","LQD"],
    "🌍 美股核心ETF":   ["SPY","QQQ","VTI","VOO","SCHD","IVV","GLD"],
    "🤖 AI & 半導體":   ["NVDA","AMD","MSFT","GOOGL","AVGO","TSM","2330.TW","2454.TW"],
    "🏠 台灣高息ETF":   ["0050.TW","00878.TW","006208.TW","00919.TW","00929.TW","00646.TW"],
}

PHASE_ALLOC = {
    "擴張": ("股 70 / 債 30", "景氣擴張，**積極持有**高股息與成長股，布局 VCP 突破強勢標的。"),
    "過熱": ("股 50 / 債 50", "景氣過熱，注意泡沫風險，**逢高減碼**高估值，轉進債券 ETF。"),
    "衰退": ("股 30 / 債 70", "景氣衰退，**增持低基期債券** 防禦，靜待景氣落底訊號。"),
    "復甦": ("股 60 / 債 40", "景氣復甦初期，**低接**高 Beta 股票，佈局下一波上漲行情。"),
}


# ── 資料抓取 ─────────────────────────────────────────────
@st.cache_data(ttl=900, show_spinner=False)
def get_twii():
    try:
        h = yf.Ticker("^TWII").history(period="2d", auto_adjust=True)
        if h.empty:
            return None, None
        last = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2]) if len(h) >= 2 else last
        return last, (last - prev) / prev * 100
    except Exception:
        return None, None


@st.cache_data(ttl=900, show_spinner=False)
def fetch_strategy_data(tickers: tuple) -> pd.DataFrame:
    rows = []
    for t in tickers:
        try:
            obj   = yf.Ticker(t)
            hist  = obj.history(period="5d", auto_adjust=True)
            if hist.empty:
                continue
            prev      = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else float(hist["Close"].iloc[0])
            last      = float(hist["Close"].iloc[-1])
            chg_pct   = (last - prev) / prev * 100 if prev else 0
            fi        = obj.fast_info
            div_yield = (getattr(fi, "three_month_average_volume", None) and
                         getattr(fi, "dividend_yield", None)) or 0
            # fallback: info dict
            if not div_yield:
                info_d    = obj.info or {}
                div_yield = info_d.get("dividendYield") or 0
            name     = getattr(fi, "short_name", None) or t
            currency = getattr(fi, "currency", "") or ""
            rows.append({
                "代碼":          t.replace(".TW", ""),
                "名稱":          str(name)[:22],
                "最新價格":      f"{currency} {last:,.2f}",
                "漲跌幅(%)":     round(chg_pct, 2),
                "近一年殖利率(%)": round(div_yield * 100, 2) if div_yield else 0.0,
                "_chg_raw":      chg_pct,
            })
        except Exception:
            pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ── 頁面內容 ──────────────────────────────────────────────
st.markdown("## 📊 總經位階與策略選股清單")

phase_info  = st.session_state.get("phase_info") or {}
phase_name  = phase_info.get("phase", "未載入（請先在主頁載入總經）")
phase_score = phase_info.get("score", 0)
rec_alloc, phase_desc = PHASE_ALLOC.get(phase_name, ("股 60 / 債 40", "請先在主頁載入總經資料以取得景氣象限。"))

_twii, _twii_chg = get_twii()

k1, k2, k3, k4 = st.columns(4)
with k1:
    if _twii:
        st.metric("🇹🇼 加權指數", f"{_twii:,.0f}", f"{_twii_chg:+.2f}%")
    else:
        st.metric("🇹🇼 加權指數", "—", "無資料")
with k2:
    st.metric("🕐 景氣象限", phase_name, f"分數 {phase_score}")
with k3:
    st.metric("💡 建議股債比", rec_alloc, "MK 方法論")
with k4:
    st.metric("⏱ 快取時效", "15 分鐘", pd.Timestamp.now().strftime("%m/%d %H:%M"))

st.divider()

# ── 策略 Pills ────────────────────────────────────────────
st.markdown("**選擇策略分類：**")
sel = st.pills("策略", list(STRATEGIES.keys()), default=list(STRATEGIES.keys())[0], key="strategy_pill")

if sel:
    with st.spinner(f"抓取 {sel} 即時報價…"):
        df = fetch_strategy_data(tuple(STRATEGIES[sel]))

    if df.empty:
        st.warning("⚠️ 目前無法取得此策略清單報價，請稍後再試。")
    else:
        def _color_chg(val):
            if not isinstance(val, (int, float)):
                return ""
            c = UP_COLOR if val > 0 else (DOWN_COLOR if val < 0 else FLAT_COLOR)
            return f"color: {c}; font-weight: bold"

        def _color_yield(val):
            if not isinstance(val, (int, float)):
                return ""
            if val >= 7:
                return f"color: {UP_COLOR}; font-weight: bold"
            if val >= 4:
                return "color: #FF9800"
            return ""

        styled = (
            df.drop(columns=["_chg_raw"])
            .style
            .map(_color_chg,   subset=["漲跌幅(%)"])
            .map(_color_yield, subset=["近一年殖利率(%)"])
            .format({"漲跌幅(%)": "{:+.2f}%", "近一年殖利率(%)": "{:.2f}%"})
        )
        st.dataframe(styled, use_container_width=True, hide_index=True)
        st.caption(f"共 {len(df)} 檔｜點擊欄位標題可排序｜資料快取 15 分鐘｜台灣紅漲綠跌")

# ── AI 大師評斷 ───────────────────────────────────────────
st.divider()
with st.expander("🤖 點此查看 AI 投資大師診斷報告", expanded=True):
    _action = {
        "🏦 7%存股聖費":    "逢回加碼核心高股息 ETF，殖利率門檻設 **> 7%**",
        "🚀 VCP強勢突破":   "等待股價突破前高 + 量能放大確認，嚴守停損 **-8%**",
        "📉 低基期債券ETF": "分批建倉，目標存續期 > 10 年長債，等降息兌現資本利得",
        "🌍 美股核心ETF":   "定期定額核心持有，勿頻繁進出，搭配 VIX < 20 時加碼",
        "🤖 AI & 半導體":   "高波動衛星部位，建議不超過總資產 **15%**，設動態停利",
        "🏠 台灣高息ETF":   "每月配息再投入複利，留意除息日前後雜訊，長期持有",
    }.get(sel or "", "依所選策略結合技術面確認進場時機")

    st.markdown(f"""
- **景氣象限**：{phase_name}（分數 {phase_score}）
- **總經研判**：{phase_desc}
- **建議股債比**：{rec_alloc}
- **當前策略**：{"—" if not sel else sel}

> ⚡ **行動建議**：{_action}
""")
