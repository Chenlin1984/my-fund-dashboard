"""
🔬 單一標的深度診斷
K線技術面 / 法人籌碼 / 財報獲利 / 河流圖估值
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

st.set_page_config(page_title="深度診斷", layout="wide", page_icon="🔬")

UP_COLOR   = "#FF4B4B"
DOWN_COLOR = "#00C07F"


def _clean_ticker(t: str) -> str:
    t = (t or "").strip().upper()
    if len(t) == 4 and t.isdigit():                           return f"{t}.TW"
    if len(t) == 5 and t[:4].isdigit() and t.endswith("B"):  return f"{t}.TW"
    if len(t) == 6 and t[:4].isdigit():                       return f"{t}.TW"
    return t


# ── 資料載入 ──────────────────────────────────────────────
@st.cache_data(ttl=900, show_spinner=False)
def _load_hist(ticker: str, period: str) -> pd.DataFrame:
    try:
        return yf.Ticker(ticker).history(period=period, auto_adjust=True)
    except Exception:
        return pd.DataFrame()

@st.cache_data(ttl=3600, show_spinner=False)
def _load_info(ticker: str) -> dict:
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}

@st.cache_data(ttl=3600, show_spinner=False)
def _load_financials(ticker: str):
    try:
        obj = yf.Ticker(ticker)
        return obj.quarterly_income_stmt
    except Exception:
        return None

@st.cache_data(ttl=3600, show_spinner=False)
def _load_inst(ticker: str):
    try:
        obj = yf.Ticker(ticker)
        return obj.institutional_holders
    except Exception:
        return None


# ── 標題與輸入 ────────────────────────────────────────────
st.markdown("## 🔬 單一標的深度診斷")
_ic1, _ic2 = st.columns([4, 1])
with _ic1:
    raw_input = st.text_input(
        "代碼", placeholder="輸入台股（2330、00878）或美股（SPY、NVDA）代碼",
        label_visibility="collapsed", key="diag_raw"
    )
with _ic2:
    go_btn = st.button("🔍 開始分析", type="primary", use_container_width=True)

if not raw_input:
    st.info("請輸入股票或 ETF 代碼，台股自動補 .TW 後綴。")
    st.stop()

TICKER = _clean_ticker(raw_input)

with st.spinner(f"載入 {TICKER}…"):
    hist2y = _load_hist(TICKER, "2y")
    hist1y = _load_hist(TICKER, "1y")
    info   = _load_info(TICKER)

if hist1y.empty:
    st.error(f"❌ 無法取得 **{TICKER}** 的資料，請確認代碼是否正確。")
    st.stop()

name = info.get("shortName") or info.get("longName") or TICKER
_last_p  = float(hist1y["Close"].iloc[-1])
_prev_p  = float(hist1y["Close"].iloc[-2]) if len(hist1y) >= 2 else _last_p
_chg_pct = (_last_p - _prev_p) / _prev_p * 100 if _prev_p else 0
_clr     = UP_COLOR if _chg_pct > 0 else DOWN_COLOR

st.markdown(
    f"<div style='padding:12px 0 4px'>"
    f"<span style='font-size:22px;font-weight:800;color:#e6edf3'>{name}</span>"
    f"<span style='font-size:13px;color:#888;margin-left:10px'>({TICKER})</span>"
    f"<span style='font-size:20px;font-weight:700;color:{_clr};margin-left:16px'>"
    f"{_last_p:,.2f} <small style='font-size:13px'>{'+' if _chg_pct>=0 else ''}{_chg_pct:.2f}%</small></span></div>",
    unsafe_allow_html=True
)

# ── 四個子分頁 ────────────────────────────────────────────
sub1, sub2, sub3, sub4 = st.tabs(["📈 K線技術面", "🏦 法人籌碼", "📊 財報獲利", "🌊 河流圖估值"])


# ════════════════════════════════════════════════════════
# SUB1: K線圖
# ════════════════════════════════════════════════════════
with sub1:
    @st.cache_data(ttl=900, show_spinner=False)
    def _build_kline(ticker: str) -> go.Figure | None:
        df = _load_hist(ticker, "1y").copy()
        if df.empty:
            return None
        df["MA20"]   = df["Close"].rolling(20).mean()
        df["MA60"]   = df["Close"].rolling(60).mean()
        df["MA120"]  = df["Close"].rolling(120).mean()
        df["BB_mid"] = df["Close"].rolling(20).mean()
        df["BB_std"] = df["Close"].rolling(20).std()
        df["BB_up"]  = df["BB_mid"] + 2 * df["BB_std"]
        df["BB_dn"]  = df["BB_mid"] - 2 * df["BB_std"]
        ema12        = df["Close"].ewm(span=12, adjust=False).mean()
        ema26        = df["Close"].ewm(span=26, adjust=False).mean()
        df["MACD"]   = ema12 - ema26
        df["Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
        df["Hist"]   = df["MACD"] - df["Signal"]

        fig = make_subplots(
            rows=3, cols=1, shared_xaxes=True,
            row_heights=[0.55, 0.20, 0.25],
            vertical_spacing=0.02,
        )
        # Candlestick (台灣慣例：紅漲綠跌)
        fig.add_trace(go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
            increasing_line_color=UP_COLOR,   decreasing_line_color=DOWN_COLOR,
            increasing_fillcolor=UP_COLOR,    decreasing_fillcolor=DOWN_COLOR,
            name="K線", showlegend=False
        ), row=1, col=1)
        # Moving averages
        for col, clr, w in [("MA20","#FF9800",1.3),("MA60","#2196F3",1.3),("MA120","#9C27B0",1.0)]:
            fig.add_trace(go.Scatter(x=df.index, y=df[col], name=col,
                                     line=dict(color=clr, width=w)), row=1, col=1)
        # Bollinger Bands
        fig.add_trace(go.Scatter(x=df.index, y=df["BB_up"],
                                  line=dict(color="#555", width=0.6, dash="dot"),
                                  showlegend=False), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["BB_dn"],
                                  line=dict(color="#555", width=0.6, dash="dot"),
                                  fill="tonexty", fillcolor="rgba(120,120,220,0.08)",
                                  showlegend=False, name="布林通道"), row=1, col=1)
        # Volume
        vol_clr = [UP_COLOR if c >= o else DOWN_COLOR
                   for c, o in zip(df["Close"].ffill(), df["Open"].ffill())]
        fig.add_trace(go.Bar(x=df.index, y=df["Volume"],
                              marker_color=vol_clr, showlegend=False, name="成交量"), row=2, col=1)
        # MACD
        hist_clr = [UP_COLOR if v >= 0 else DOWN_COLOR for v in df["Hist"].fillna(0)]
        fig.add_trace(go.Bar(x=df.index, y=df["Hist"],
                              marker_color=hist_clr, showlegend=False, name="MACD柱"), row=3, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["MACD"],   name="MACD",
                                  line=dict(color="#FF9800", width=1.2)), row=3, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["Signal"], name="Signal",
                                  line=dict(color="#2196F3", width=1.2)), row=3, col=1)

        fig.update_layout(
            height=640, template="plotly_dark",
            paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
            xaxis_rangeslider_visible=False,
            margin=dict(l=10, r=10, t=20, b=10),
            legend=dict(orientation="h", y=1.03, font=dict(size=10)),
        )
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(showgrid=True, gridcolor="#21262d")
        return fig

    _kfig = _build_kline(TICKER)
    if _kfig:
        st.plotly_chart(_kfig, use_container_width=True)
    else:
        st.warning("⚠️ 無法繪製 K 線圖（資料不足）")

    # AI 評斷
    with st.expander("🤖 K線技術分析 AI 評斷", expanded=True):
        if len(hist1y) >= 60:
            _lc  = float(hist1y["Close"].iloc[-1])
            _m20 = float(hist1y["Close"].rolling(20).mean().iloc[-1])
            _m60 = float(hist1y["Close"].rolling(60).mean().iloc[-1])
            _trend = ("多頭排列" if _lc > _m20 > _m60
                      else "空頭排列" if _lc < _m20 < _m60
                      else "均線糾結")
            _action = {
                "多頭排列": "趨勢明確，持續持有；回測 MA20 不破為加碼機會",
                "空頭排列": "建議減碼或觀望，等待 MA20 翻揚確認底部",
                "均線糾結": "方向不明，縮手等待，等量能擴大後突破再進場",
            }[_trend]
            st.markdown(f"""
- **均線狀態**：**{_trend}**（收 {_lc:.2f} | MA20 {_m20:.2f} | MA60 {_m60:.2f}）
- **布林通道**：2σ 通道已繪製，收縮後的方向性突破為重要訊號
- **MACD**：柱體顏色 紅=多頭動能 / 綠=空頭動能

> ⚡ **行動建議**：{_action}
""")
        else:
            st.info("資料不足 60 日，無法完整計算均線。")


# ════════════════════════════════════════════════════════
# SUB2: 法人籌碼
# ════════════════════════════════════════════════════════
with sub2:
    inst_df = _load_inst(TICKER)

    if inst_df is not None and not inst_df.empty:
        st.markdown("#### 🏦 機構法人持股（前 15 大）")
        st.dataframe(inst_df.head(15), use_container_width=True)
    else:
        st.warning(
            f"⚠️ **{TICKER}** 無法取得法人持股資料。\n\n"
            "yfinance 台股法人資料覆蓋有限；美股完整。"
            "台股法人三大買賣超可至 [台灣證交所](https://www.twse.com.tw/) 或接入 FinMind API 取得。"
        )

    # 量能代理買賣超（所有標的均可算）
    st.markdown("#### 📊 成交量代理買賣超（量增漲=買超代理，量增跌=賣超代理）")
    if len(hist1y) >= 20:
        _dv = hist1y[["Close","Volume"]].copy().tail(60).ffill()
        _dv["vol_ma20"] = _dv["Volume"].rolling(20).mean()
        _dv["pchg"]     = _dv["Close"].pct_change().fillna(0)
        _dv["proxy"]    = _dv.apply(
            lambda r: r["Volume"] if r["pchg"] > 0 else -r["Volume"], axis=1
        )
        bar_clr = [UP_COLOR if v >= 0 else DOWN_COLOR for v in _dv["proxy"]]
        fig_v = go.Figure(go.Bar(x=_dv.index, y=_dv["proxy"], marker_color=bar_clr))
        fig_v.update_layout(
            height=300, template="plotly_dark",
            paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
            margin=dict(l=10, r=10, t=10, b=10),
            yaxis_title="量能（正=買超代理 | 負=賣超代理）",
        )
        fig_v.update_xaxes(showgrid=False)
        fig_v.update_yaxes(showgrid=True, gridcolor="#21262d")
        st.plotly_chart(fig_v, use_container_width=True)
        st.caption("⚠️ 此圖為代理指標（非真實三大法人報告），僅供趨勢參考")

    with st.expander("🤖 籌碼 AI 評斷", expanded=True):
        st.markdown("""
- **法人持股**：機構持股比率高（> 60%）通常代表籌碼穩定，散戶比率高則波動較大
- **量能訊號**：連續 3 日以上「大量（> 均量 1.5x）+ 價漲」為**籌碼集中**訊號
- **警示**：量增但股價滯漲，可能是**主力出貨**前兆，需提高警覺

> ⚡ **行動建議**：觀察量能是否配合趨勢，量縮回檔後量增突破為較佳進場機會
""")


# ════════════════════════════════════════════════════════
# SUB3: 財報獲利
# ════════════════════════════════════════════════════════
with sub3:
    income_stmt = _load_financials(TICKER)
    has_data    = income_stmt is not None and not income_stmt.empty

    if not has_data:
        st.warning(f"⚠️ **{TICKER}** 無財報資料（台股 ETF/指數 yfinance 財報覆蓋率較低）")
    else:
        try:
            # 欄位：最新在左，排序後統一
            cols_sorted = sorted(income_stmt.columns, key=str)[:8]
            stmt = income_stmt[cols_sorted]
            col_labels = [str(c)[:10] for c in cols_sorted]

            def _safe_row(key: str) -> pd.Series | None:
                for k in income_stmt.index:
                    if key.lower() in str(k).lower():
                        return stmt.loc[k].apply(pd.to_numeric, errors="coerce")
                return None

            rev  = _safe_row("Total Revenue")
            gp   = _safe_row("Gross Profit")
            oi   = _safe_row("Operating Income")
            ni   = _safe_row("Net Income")
            eps  = _safe_row("Basic EPS") or _safe_row("Diluted EPS")

            if rev is not None and gp is not None and rev.replace(0, np.nan).notna().any():
                gross_m = (gp / rev.replace(0, np.nan) * 100).round(1)
                op_m    = (oi / rev.replace(0, np.nan) * 100).round(1) if oi is not None else None
                net_m   = (ni / rev.replace(0, np.nan) * 100).round(1) if ni is not None else None

                fig_fin = go.Figure()
                fig_fin.add_trace(go.Scatter(
                    x=col_labels, y=gross_m.values, name="毛利率 %",
                    line=dict(color="#FF9800", width=2), mode="lines+markers"))
                if op_m is not None:
                    fig_fin.add_trace(go.Scatter(
                        x=col_labels, y=op_m.values, name="營業利益率 %",
                        line=dict(color="#2196F3", width=2), mode="lines+markers"))
                if net_m is not None:
                    fig_fin.add_trace(go.Scatter(
                        x=col_labels, y=net_m.values, name="稅後淨利率 %",
                        line=dict(color=UP_COLOR, width=2), mode="lines+markers"))
                fig_fin.update_layout(
                    height=320, template="plotly_dark",
                    paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                    margin=dict(l=10, r=10, t=20, b=10), yaxis_title="%",
                    title_text="單季三率走勢",
                )
                fig_fin.update_xaxes(showgrid=False)
                fig_fin.update_yaxes(showgrid=True, gridcolor="#21262d")
                st.plotly_chart(fig_fin, use_container_width=True)
            else:
                st.info("營收資料不足，無法計算三率。")

            if eps is not None:
                eps_vals  = eps.values
                eps_clrs  = [UP_COLOR if (v is not None and not np.isnan(v) and v >= 0) else DOWN_COLOR
                             for v in eps_vals]
                fig_eps = go.Figure(go.Bar(
                    x=col_labels, y=eps_vals,
                    marker_color=eps_clrs, name="EPS"
                ))
                fig_eps.update_layout(
                    height=220, template="plotly_dark",
                    paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                    margin=dict(l=10, r=10, t=30, b=10), title_text="單季 EPS",
                )
                fig_eps.update_xaxes(showgrid=False)
                fig_eps.update_yaxes(showgrid=True, gridcolor="#21262d", zeroline=True, zerolinecolor="#555")
                st.plotly_chart(fig_eps, use_container_width=True)
                st.caption("EPS 紅柱=正數（獲利）｜綠柱=負數（虧損）")
        except Exception as e:
            st.warning(f"財報解析失敗：{e}")

    with st.expander("🤖 財報 AI 評斷", expanded=True):
        st.markdown("""
- **三率健康標準**：毛利率 > 30%、營業利益率 > 15%、淨利率 > 10% 為優質企業
- **EPS 趨勢**：連續 3 季正成長為強烈買入訊號；若 **EPS < 0** → 河流圖自動切換 PB 估值
- **警示**：毛利率連續下滑 > 3% 可能面臨競爭壓力，需結合籌碼面判斷

> ⚡ **行動建議**：三率穩定 + EPS 逐季成長 → 可納入核心持股；三率下滑 → 先觀望
""")


# ════════════════════════════════════════════════════════
# SUB4: 河流圖估值
# ════════════════════════════════════════════════════════
with sub4:
    @st.cache_data(ttl=3600, show_spinner=False)
    def _build_river(ticker: str) -> tuple[go.Figure | None, str]:
        hist5y = _load_hist(ticker, "5y")
        info_d = _load_info(ticker)
        if hist5y.empty:
            return None, "no_data"

        price          = hist5y["Close"].ffill()
        trailing_eps   = info_d.get("trailingEps")
        book_value     = info_d.get("bookValue")
        use_pb         = (not trailing_eps) or (trailing_eps <= 0)

        if not use_pb:
            # PE 河流圖：以歷史 PE 分位數決定通道
            hist_pe  = price / trailing_eps
            pe_pcts  = [hist_pe.quantile(p) for p in [0.1, 0.25, 0.5, 0.75, 0.9]]
            bands    = [trailing_eps * pe for pe in pe_pcts]
            labels   = ["特價（危機入市）", "便宜價", "合理價（中位）", "偏高價", "瘋狂價（昂貴）"]
            val_type = "本益比(PE) 河流圖"
        elif book_value and book_value > 0:
            # PB 河流圖
            pb_mults = [0.5, 1.0, 1.5, 2.0, 3.0]
            bands    = [book_value * m for m in pb_mults]
            labels   = ["0.5x BV 特價", "1x BV 便宜", "1.5x BV 合理", "2x BV 偏貴", "3x BV 昂貴"]
            val_type = "股價淨值比(PB) 河流圖（EPS≤0 自動切換）"
        else:
            return None, "no_valuation"

        BAND_COLORS = [
            "rgba(33,150,243,0.25)",   # 最低：藍
            "rgba(33,150,243,0.12)",   # 便宜：淺藍
            "rgba(76,175,80,0.12)",    # 合理：綠
            "rgba(255,152,0,0.18)",    # 偏高：橘
            "rgba(244,67,54,0.22)",    # 最高：紅
        ]
        x = hist5y.index
        fig = go.Figure()
        # Fill zones between bands
        for i in range(len(bands) - 1):
            y_hi = [bands[i + 1]] * len(x)
            y_lo = [bands[i]]     * len(x)
            fig.add_trace(go.Scatter(
                x=list(x) + list(x)[::-1],
                y=y_hi + y_lo[::-1],
                fill="toself", fillcolor=BAND_COLORS[i + 1],
                line=dict(color="rgba(0,0,0,0)"),
                name=labels[i + 1], mode="none", showlegend=True,
            ))
        # Bottom band line
        fig.add_trace(go.Scatter(
            x=x, y=[bands[0]] * len(x),
            line=dict(color="#2196F3", width=0.8, dash="dot"),
            name=labels[0], mode="lines",
        ))
        # Actual price
        fig.add_trace(go.Scatter(
            x=x, y=price,
            line=dict(color="#FFFFFF", width=2.5),
            name="實際股價", mode="lines",
        ))
        fig.update_layout(
            height=430, template="plotly_dark",
            paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
            title=f"{ticker} {val_type}（5年）",
            margin=dict(l=10, r=10, t=40, b=10),
            legend=dict(font=dict(size=10), bgcolor="rgba(13,17,23,0.8)"),
        )
        fig.update_xaxes(showgrid=False)
        fig.update_yaxes(showgrid=True, gridcolor="#21262d")
        return fig, val_type

    with st.spinner("計算估值河流圖…"):
        _rfig, _val_type = _build_river(TICKER)

    if _rfig is None:
        if _val_type == "no_valuation":
            st.warning("⚠️ 無 EPS 及帳面價值資料，無法繪製河流圖（純 ETF/指數不適用）。")
        else:
            st.warning("⚠️ 資料不足，無法繪製河流圖。")
    else:
        st.plotly_chart(_rfig, use_container_width=True)
        st.caption("白線=實際股價｜通道由下至上：特價 → 便宜 → 合理 → 偏貴 → 瘋狂")

    with st.expander("🤖 估值 AI 評斷", expanded=True):
        pe  = info.get("trailingPE")
        pb  = info.get("priceToBook")
        fpe = info.get("forwardPE")
        st.markdown(f"""
- **本益比(PE)**：{f"**{pe:.1f}x**" if (pe and pe > 0) else "無法計算（EPS ≤ 0，已切換 PB）"}
- **遠期 PE**：{f"**{fpe:.1f}x**" if fpe else "—"}
- **股價淨值比(PB)**：{f"**{pb:.2f}x**" if pb else "—"}
- **河流圖解讀**：白線（實際股價）所處的色帶 = 目前估值象限

> ⚡ **行動建議**：{"PE < 15x，估值偏低，可逢低建倉" if (pe and 0 < pe < 15) else "PE > 30x，溢價偏高，建議分批獲利了結或觀望" if (pe and pe > 30) else "估值適中或無法計算，搭配技術面與籌碼面研判"}
""")
