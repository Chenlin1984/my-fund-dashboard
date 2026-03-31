"""
💼 投資組合庫存損益
輸入持倉 → 自動計算損益、MDD、資產分佈圓餅圖
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go

st.set_page_config(page_title="庫存損益", layout="wide", page_icon="💼")

UP_COLOR   = "#FF4B4B"
DOWN_COLOR = "#00C07F"

ASSET_CLASSES = ["台股ETF", "美股ETF", "個股-台", "個股-美", "債券ETF", "其他"]
DONUT_COLORS  = ["#FF4B4B","#FF9800","#2196F3","#9C27B0","#00C07F","#607D8B","#FFEB3B","#00BCD4"]


def _clean_ticker(t: str) -> str:
    t = (t or "").strip().upper()
    if len(t) == 4 and t.isdigit():                           return f"{t}.TW"
    if len(t) == 5 and t[:4].isdigit() and t.endswith("B"):  return f"{t}.TW"
    if len(t) == 6 and t[:4].isdigit():                       return f"{t}.TW"
    return t


# ── Session state ─────────────────────────────────────────
if "portfolio_v2" not in st.session_state:
    st.session_state.portfolio_v2 = []


# ── 資料抓取 ──────────────────────────────────────────────
@st.cache_data(ttl=600, show_spinner=False)
def _fetch_prices(tickers: tuple) -> dict[str, float]:
    result = {}
    for t in tickers:
        try:
            h = yf.Ticker(t).history(period="2d", auto_adjust=True)
            if not h.empty:
                result[t] = float(h["Close"].iloc[-1])
        except Exception:
            pass
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def _calc_mdd(ticker: str) -> float | None:
    try:
        h = yf.Ticker(ticker).history(period="1y", auto_adjust=True)
        if h.empty:
            return None
        roll_max = h["Close"].cummax()
        dd = (h["Close"] - roll_max) / roll_max * 100
        return float(dd.min())
    except Exception:
        return None


# ══════════════════════════════════════════════════════════
# 頁面
# ══════════════════════════════════════════════════════════
st.markdown("## 💼 投資組合庫存損益")
st.caption("輸入持倉後自動抓取現價，計算損益、MDD 與資產分佈")

# ── 新增持倉表單 ──────────────────────────────────────────
with st.expander("➕ 新增 / 管理持倉", expanded=not bool(st.session_state.portfolio_v2)):
    _fc1, _fc2, _fc3, _fc4, _fc5 = st.columns([2, 2, 1.2, 1.2, 0.8])
    with _fc1:
        _nt = st.text_input("代碼", placeholder="2330 / SPY / 00878", key="pv2_t", label_visibility="visible")
    with _fc2:
        _nc = st.selectbox("資產類別", ASSET_CLASSES, key="pv2_c")
    with _fc3:
        _ns = st.number_input("股數/單位", min_value=0.0, step=100.0, key="pv2_s")
    with _fc4:
        _nk = st.number_input("平均成本", min_value=0.0, step=0.01, key="pv2_k",
                               help="填入台幣（台股）或美元（美股）均可，損益以原幣計算")
    with _fc5:
        st.markdown("<div style='margin-top:28px'></div>", unsafe_allow_html=True)
        if st.button("➕ 加入", use_container_width=True, key="btn_pv2"):
            if _nt and _ns > 0 and _nk > 0:
                _code = _clean_ticker(_nt)
                st.session_state.portfolio_v2.append({
                    "ticker":      _code,
                    "display":     _nt.strip().upper(),
                    "shares":      _ns,
                    "avg_cost":    _nk,
                    "asset_class": _nc,
                })
                _fetch_prices.clear()
                st.rerun()
            else:
                st.warning("代碼、股數、成本均需填寫。")

    # 移除持倉
    if st.session_state.portfolio_v2:
        _opts = [f"{p['display']} ｜ {p['asset_class']} ｜ {p['shares']} 股 @ {p['avg_cost']}"
                 for p in st.session_state.portfolio_v2]
        _rm_c1, _rm_c2 = st.columns([4, 1])
        with _rm_c1:
            _to_rm = st.selectbox("移除持倉", ["— 選擇 —"] + _opts,
                                  label_visibility="collapsed", key="pv2_rm")
        with _rm_c2:
            if st.button("🗑️ 移除", key="btn_pv2_rm") and not _to_rm.startswith("—"):
                idx = _opts.index(_to_rm)
                st.session_state.portfolio_v2.pop(idx)
                _fetch_prices.clear()
                st.rerun()

if not st.session_state.portfolio_v2:
    st.info("尚無持倉記錄。請在上方表單新增第一筆持倉。")
    st.stop()

# ── 抓取現價 ──────────────────────────────────────────────
all_tickers = tuple(set(p["ticker"] for p in st.session_state.portfolio_v2))
with st.spinner("更新現價中…"):
    prices = _fetch_prices(all_tickers)

# ── 計算損益 ──────────────────────────────────────────────
rows         = []
total_cost   = 0.0
total_mktval = 0.0
sector_map: dict[str, float] = {}

for p in st.session_state.portfolio_v2:
    cur  = prices.get(p["ticker"])
    cost = p["shares"] * p["avg_cost"]
    mkt  = p["shares"] * cur if cur else None
    pnl  = (mkt - cost)     if mkt is not None else None
    roi  = pnl / cost * 100 if (pnl is not None and cost > 0) else None
    mdd  = _calc_mdd(p["ticker"])

    total_cost   += cost
    total_mktval += mkt or 0

    cls = p.get("asset_class", "其他")
    sector_map[cls] = sector_map.get(cls, 0.0) + (mkt or cost)

    rows.append({
        "代碼":       p["display"],
        "類別":       cls,
        "股數":       p["shares"],
        "平均成本":   p["avg_cost"],
        "現價":       cur,
        "投資成本":   cost,
        "市值":       mkt,
        "損益":       pnl,
        "報酬率(%)":  roi,
        "MDD(%)":     round(mdd, 1) if mdd is not None else None,
    })

total_pnl = total_mktval - total_cost
total_roi = total_pnl / total_cost * 100 if total_cost > 0 else 0.0

# ── KPI 卡片 ──────────────────────────────────────────────
k1, k2, k3, k4 = st.columns(4)
k1.metric("💰 總投資成本",  f"NT$ {total_cost:,.0f}")
k2.metric("📈 當前總市值",  f"NT$ {total_mktval:,.0f}")
_pnl_clr = UP_COLOR if total_pnl >= 0 else DOWN_COLOR
k3.metric(
    "💹 未實現損益",
    f"{'▲' if total_pnl>=0 else '▼'} NT$ {abs(total_pnl):,.0f}",
    f"{total_roi:+.2f}%",
    delta_color="normal" if total_pnl >= 0 else "inverse",
)
k4.metric("📊 持倉檔數", f"{len(st.session_state.portfolio_v2)} 檔")

st.divider()

# ── 圓餅圖 + 損益明細 ────────────────────────────────────
_pie_col, _tbl_col = st.columns([1, 2])

with _pie_col:
    st.markdown("**資產類別分佈**")
    pie_labels = list(sector_map.keys())
    pie_values = list(sector_map.values())
    fig_pie = go.Figure(go.Pie(
        labels=pie_labels, values=pie_values,
        hole=0.45,
        marker=dict(colors=DONUT_COLORS[:len(pie_labels)]),
        textinfo="label+percent",
        hovertemplate="%{label}: %{value:,.0f}<extra></extra>",
    ))
    fig_pie.update_layout(
        height=300, template="plotly_dark",
        paper_bgcolor="#0d1117",
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=False,
    )
    st.plotly_chart(fig_pie, use_container_width=True)
    st.caption(f"♻️ 現價快取 10 分鐘 | 上次更新 {pd.Timestamp.now().strftime('%H:%M')}")
    if st.button("♻️ 強制刷新現價", key="btn_refresh_pv2"):
        _fetch_prices.clear()
        st.rerun()

with _tbl_col:
    st.markdown("**持倉損益明細**")
    df_disp = pd.DataFrame([{
        "代碼":      r["代碼"],
        "類別":      r["類別"],
        "股數":      r["股數"],
        "現價":      f"{r['現價']:,.2f}" if r["現價"] else "—",
        "市值":      f"{r['市值']:,.0f}"  if r["市值"]  else "—",
        "損益":      r["損益"],
        "報酬率(%)": r["報酬率(%)"],
        "MDD(%)":    r["MDD(%)"],
    } for r in rows])

    def _sty_pnl(v):
        if not isinstance(v, (int, float)) or (isinstance(v, float) and np.isnan(v)):
            return ""
        return f"color: {UP_COLOR if v >= 0 else DOWN_COLOR}; font-weight: bold"

    def _fmt_pnl(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "—"
        return f"{v:+,.0f}"

    def _fmt_pct(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "—"
        return f"{v:+.2f}%"

    def _fmt_mdd(v):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "—"
        return f"{v:.1f}%"

    styled_df = (
        df_disp.style
        .map(_sty_pnl, subset=["損益", "報酬率(%)"])
        .format({"損益": _fmt_pnl, "報酬率(%)": _fmt_pct, "MDD(%)": _fmt_mdd})
    )
    st.dataframe(styled_df, use_container_width=True, hide_index=True)

# ── 損益長條圖 ────────────────────────────────────────────
st.markdown("**各持倉報酬率比較**")
_rois   = [r["報酬率(%)"] for r in rows if r["報酬率(%)"] is not None]
_labels = [r["代碼"]       for r in rows if r["報酬率(%)"] is not None]
if _rois:
    _bar_clr = [UP_COLOR if v >= 0 else DOWN_COLOR for v in _rois]
    fig_bar = go.Figure(go.Bar(
        x=_labels, y=_rois,
        marker_color=_bar_clr,
        text=[f"{v:+.1f}%" for v in _rois],
        textposition="outside",
    ))
    fig_bar.update_layout(
        height=250, template="plotly_dark",
        paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
        margin=dict(l=10, r=10, t=10, b=40),
        yaxis_title="%",
        yaxis_zeroline=True, yaxis_zerolinecolor="#555",
    )
    fig_bar.update_xaxes(showgrid=False)
    fig_bar.update_yaxes(showgrid=True, gridcolor="#21262d")
    st.plotly_chart(fig_bar, use_container_width=True)

# ── AI 大師評斷 ───────────────────────────────────────────
st.divider()
with st.expander("🤖 點此查看 AI 大師庫存診斷報告", expanded=True):
    _best_roi  = max(_rois, default=None)
    _worst_roi = min(_rois, default=None)
    _worst_mdd = min((r["MDD(%)"] for r in rows if r["MDD(%)"] is not None), default=None)
    _best_lbl  = next((r["代碼"] for r in rows if r["報酬率(%)"] == _best_roi),  "—")
    _worst_lbl = next((r["代碼"] for r in rows if r["報酬率(%)"] == _worst_roi), "—")

    _overall = "正報酬" if total_roi >= 0 else "負報酬"
    _action  = (
        "組合整體獲利，持續持有並每季定期再平衡，控制單一標的不超過總資產 **25%**"
        if total_roi >= 0 else
        "組合整體虧損，優先檢視最大虧損標的是否觸及停損線 **-15%**，考慮換股或補倉攤低成本"
    )
    st.markdown(f"""
- **整體績效**：總報酬率 **{total_roi:+.2f}%**（{_overall}）｜市值 NT$ {total_mktval:,.0f}
- **最佳持倉**：**{_best_lbl}** 報酬率 {f'{_best_roi:+.2f}%' if _best_roi is not None else '—'}
- **最差持倉**：**{_worst_lbl}** 報酬率 {f'{_worst_roi:+.2f}%' if _worst_roi is not None else '—'}
- **最大回撤**：組合中最大單檔 MDD **{f'{_worst_mdd:.1f}%' if _worst_mdd is not None else '—'}**{"，超過 -20% 建議評估汰換" if (_worst_mdd and _worst_mdd < -20) else "，回撤尚在可接受範圍"}

> ⚡ **行動建議**：{_action}
""")
