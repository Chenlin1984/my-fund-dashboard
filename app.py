# =================================================
# 【Cell 12】寫入 main.py（Streamlit 主程式）
# 說明：生成 Streamlit 前端介面主程式，包含所有頁面：
#        儀表板、基金分析、投資組合、AI 對話等功能。
# 新手提示：直接執行即可，不需要修改。
#            此 Cell 執行時間較長（約 10-30 秒），請耐心等待。
# =================================================
import streamlit as st
import os, datetime, time, re
# Fix: Colab runs in UTC; use Taiwan timezone (UTC+8) for all display times
TW_TZ = datetime.timezone(datetime.timedelta(hours=8))
def _now_tw():
    """Return current Taiwan time (UTC+8)"""
    return datetime.datetime.now(TW_TZ)
import plotly.graph_objects as go
import pandas as pd, numpy as np

from macro_engine import fetch_all_indicators, calc_macro_phase, ENGINE_VERSION

# ── 版本戳記：在這裡改版本號 = 確認 app.py 已部署至 Streamlit Cloud
APP_VERSION = "v17.3_CnyesAPI"
from fund_fetcher  import (fetch_fund_by_key, search_moneydj_by_name,
                            fetch_fund_structure, fetch_fund_from_moneydj_url,
                            tdcc_search_fund,
                            safe_float, classify_fetch_status, clean_risk_table,
                            normalize_result_state, merge_non_empty)
from ai_engine     import analyze_macro, analyze_fund_pro, analyze_fund_json
from backtest_engine   import backtest_portfolio, calc_performance_metrics, quick_backtest
from portfolio_engine  import (calc_fund_factor_score, dividend_safety as div_safety_check,
                                optimize_portfolio, risk_alert as portfolio_risk_alert)
from macro_engine      import identify_regime

# ══════════════════════════════════════════════════════════════
# v10.7 資產角色分類函數（關鍵字辨識，取代動態指標判斷）
# ══════════════════════════════════════════════════════════════

# v13 排錯：依資料完整度分三態顯示（不再統一顯示紅色失敗）
def _render_fetch_status_badge(fd: dict) -> str:
    """回傳 HTML badge，依 classify_fetch_status 分三色"""
    status = classify_fetch_status(fd)
    if status == "complete":
        return ""   # 完整成功不顯示 badge
    elif status == "partial":
        return (
            "<span style='background:#1a1200;color:#ff9800;border:1px solid #ff9800;"
            "border-radius:10px;padding:2px 8px;font-size:10px;margin-left:6px'>"
            "⚠️ 部分資料（歷史/指標不完整）</span>"
        )
    else:
        return (
            "<span style='background:#2a0a0a;color:#f44336;border:1px solid #f44336;"
            "border-radius:10px;padding:2px 8px;font-size:10px;margin-left:6px'>"
            "❌ 資料抓取失敗</span>"
        )

def assign_asset_role(fund_name: str) -> bool:
    """
    v10.7.1 修正版：依基金名稱關鍵字判斷是否為核心資產。
    回傳 True = 核心資產🛡️，False = 衛星資產⚡

    判斷優先順序：
      1. 白名單（特定基金完整名稱）→ 直接定性，不再走關鍵字
      2. 核心關鍵字優先（若名稱含核心+衛星混用詞，核心優先）
      3. 純衛星關鍵字 → 判為衛星
      4. 無法判斷 → 預設衛星（保守原則）
    """
    name = (fund_name or "").lower()

    # ── Step 1：白名單（優先、直接定性）────────────────────
    # 這裡列出「名稱含有衛星關鍵字但實際是核心資產」的特例
    CORE_WHITELIST = [
        "安聯收益成長",   # 多重資產收益型，非純成長
        "收益成長",        # 泛稱：同類產品
        "多元收益",
        "安聯多元入息",
        "摩根多重收益",
        "富達多重資產",
        "聯博收益",
        "柏瑞多重資產",
        "施羅德多元收益",
        "瀚亞多重資產",
        "富蘭克林收益",
        "先機多元收益",
    ]
    if any(wl in name for wl in CORE_WHITELIST):
        return True

    # ── Step 2：核心關鍵字（強核心，不被衛星詞干擾）───────
    # 這些詞彙明確代表核心資產屬性
    STRONG_CORE_KW = [
        "配息","高股息","投資等級債","非投資等級債",
        "公司債","公債","債券","債","特別股","基建","公用事業",
        "infrastructure","preferred","utility","corporate bond",
        "income fund","bond fund","fixed income",
    ]
    if any(k in name for k in STRONG_CORE_KW):
        return True

    # ── Step 3：一般核心關鍵字（若同時含衛星詞，核心仍優先）
    core_kw = [
        "收益","平衡","多元","多重資產","balanced",
        "income","bond","fixed","dividend","多重收益",
        "全球股息","全球高股息","非投資等級","投資等級",
    ]
    # ── 純衛星關鍵字（不與核心詞重疊）────────────────────
    sat_kw = [
        "科技","ai","半導體","生技","醫療","電動車",
        "創新","綠能","機器人","網通",
        "印度","越南","中國a股","a股",
        "航太","能源轉型","元宇宙","nft",
        "theme","tech","growth","biotech",
        "semiconductor","robot","ev","india","vietnam",
    ]
    # 注意：「成長」故意從 sat_kw 移出，因為「收益成長」是核心
    # 「純成長」才是衛星 → 透過白名單與 core_kw 二段辨識

    has_core = any(k in name for k in core_kw)
    has_sat  = any(k in name for k in sat_kw)

    if has_core and has_sat:
        return True   # 同時含兩類關鍵字 → 核心優先（如「收益+科技」）
    if has_core:
        return True
    if has_sat:
        return False

    # Step 4: 預設衛星（保守原則）
    return False


# ══════════════════════════════════════════════════════════════
# v10.7 代碼防呆 — 台股 ETF 自動補 .TW 後綴
# ══════════════════════════════════════════════════════════════
def clean_ticker(ticker: str) -> str:
    """
    自動修正使用者輸入代碼：
    - 0056  → 0056.TW  (4碼台股)
    - 00679B → 00679B.TW (5碼含B債券ETF)
    - FLZ64 等境外基金代碼維持原樣
    """
    t = (ticker or "").strip().upper()
    if len(t) == 4 and t.isdigit():
        return f"{t}.TW"
    if len(t) == 5 and t[:4].isdigit() and t.endswith("B"):
        return f"{t}.TW"
    if len(t) == 6 and t[:4].isdigit():
        return f"{t}.TW"
    return t



st.set_page_config(page_title="📊 基金監控 AI 戰情室",
                   layout="wide", page_icon="📊",
                   initial_sidebar_state="expanded")

# AI Error Ledger display function
# [Tutorial] Shows past AI-recorded errors at app startup
def _show_error_ledger():
    import os as _os_sl
    _lp = "/content/AI_Error_Ledger.md"
    if _os_sl.path.exists(_lp):
        try:
            with open(_lp, encoding="utf-8") as _f:
                _ledger_content = _f.read()
            if _ledger_content.strip() and "---" in _ledger_content:
                with st.expander("🧠 AI 錯誤學習日誌（有過去錯誤記錄，點擊展開）", expanded=False):
                    st.markdown(_ledger_content[-4000:], unsafe_allow_html=False)
                    if st.button("🗑️ 清除日誌", key="clear_error_ledger"):
                        with open(_lp, "w", encoding="utf-8") as _f:
                            _f.write("# AI_Error_Ledger\n\n> 日誌已清除。\n")
                        st.rerun()
        except Exception:
            pass
_show_error_ledger()



def _load_keys():                                        # [Fixed] Colab JSON → st.secrets
    fred = (st.secrets.get("FRED_API_KEY",  "") or
            os.environ.get("FRED_API_KEY", ""))
    gem  = (st.secrets.get("GEMINI_API_KEY","") or
            os.environ.get("GEMINI_API_KEY",""))
    if fred: os.environ["FRED_API_KEY"]   = fred
    if gem:  os.environ["GEMINI_API_KEY"] = gem
    return fred, gem

FRED_KEY, GEMINI_KEY = _load_keys()

st.markdown("""<style>
body,.stApp{background:#0e1117;color:#e6edf3}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 18px;margin:6px 0}
.phase-banner{border-radius:12px;padding:18px 24px;margin:10px 0;font-weight:800;text-align:center}
.inflect-box{border-radius:10px;padding:14px 20px;margin:8px 0;font-size:17px;font-weight:700}
.score-box{background:#1a1f2e;border:1px solid #30363d;border-radius:8px;padding:10px;text-align:center}
.step-flow{display:flex;align-items:center;gap:8px;padding:12px 16px;background:#0d1b2a;border:1px solid #1e3a5f;border-radius:10px;margin:8px 0}
.step-node{border-radius:8px;padding:8px 14px;text-align:center;min-width:110px;font-size:13px}
.arrow{color:#555;font-size:22px;flex-shrink:0}
.fc{background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:10px 14px;margin:3px 0}
.signal-buy{background:#1c3a2a;color:#3fb950;border:1px solid #3fb950;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;display:inline-block}
.signal-sell{background:#3a1010;color:#f85149;border:1px solid #f85149;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;display:inline-block}
.signal-hold{background:#1a3450;color:#58a6ff;border:1px solid #58a6ff;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;display:inline-block}
.signal-switch{background:#3a2a10;color:#f0b132;border:1px solid #f0b132;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;display:inline-block}
</style>""", unsafe_allow_html=True)

for k, v in [("macro_done",False),("indicators",{}),("phase_info",{}),
               ("news_headlines",[]),("current_fund",None),
              ("fund_results",{}),("macro_ai",""),("fund_ai",{}),
              ("prev_phase",""),("phase_history",[]),
              ("macro_last_update", None)]:   # v14.5: 記錄最後更新時間
    if k not in st.session_state:
        st.session_state[k] = v

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📊 基金監控 AI 戰情室")
    _sb_upd = st.session_state.get("macro_last_update")
    _sb_upd_str = _sb_upd.strftime("%m/%d %H:%M") if _sb_upd else "未載入"
    st.caption(f"📡 總經更新：{_sb_upd_str} ‧ {_now_tw().strftime('%m/%d %H:%M')} TW")
    # ── 版本戳記（看到版本號 = 確認 GitHub 已部署至 Streamlit Cloud）
    st.info(f"系統版本：{APP_VERSION}\nEngine：{ENGINE_VERSION}")
    st.divider()

    # ── API 狀態 ────────────────────────────────────────
    st.markdown(
        f"{'✅' if FRED_KEY else '❌'} FRED　　"
        f"{'✅' if GEMINI_KEY else '❌'} Gemini"
    )

    # ── 強制同步 GitHub 最新邏輯
    if st.sidebar.button("♻️ 強制同步 GitHub 最新邏輯", use_container_width=True,
                         help="清除 cache_data + cache_resource，確保載入 GitHub 最新版本邏輯。"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.session_state["macro_done"]        = False
        st.session_state["indicators"]        = {}
        st.session_state["phase_info"]        = {}
        st.session_state["macro_last_update"] = None
        st.success(f"已同步至 {APP_VERSION}，請重新整理網頁")
        st.rerun()
    st.divider()

    # ── 核心/衛星目標 ────────────────────────────────────
    st.markdown("### ⚖️ 核心/衛星目標")
    _sb_core = st.slider(
        "核心資產目標 %", 0, 100,
        int(st.session_state.get("portfolio_core_pct", 80)), 5,
        key="sb_core_pct", help="核心=配息型；衛星=成長型")
    if st.session_state.get("portfolio_core_pct") != _sb_core:
        st.session_state["portfolio_core_pct"] = _sb_core
    _sb_sat = 100 - _sb_core
    st.markdown(
        f"<div style='background:#0d1b2a;border-radius:6px;padding:8px 12px;font-size:12px'>"
        f"<div style='display:flex;justify-content:space-between;margin-bottom:4px'>"
        f"<span style='color:#64b5f6;font-weight:700'>🛡️ 核心 {_sb_core}%</span>"
        f"<span style='color:#ff9800;font-weight:700'>⚡ 衛星 {_sb_sat}%</span>"
        f"<span style='color:#00c853;font-size:10px'>= 100% ✅</span>"
        f"</div>"
        f"<div style='height:6px;border-radius:3px;overflow:hidden;background:#1a1f2e'>"
        f"<div style='height:100%;width:{_sb_core}%;background:linear-gradient(to right,#64b5f6,#ff9800)'></div>"
        f"</div></div>", unsafe_allow_html=True)

    # ── 目前核心比例偏離 ─────────────────────────────────
    _pf_sb = [f for f in st.session_state.get("portfolio_funds", []) if f.get("loaded")]
    if _pf_sb:
        _tot_sb = sum(f.get("invest_twd", 0) or 0 for f in _pf_sb)
        if _tot_sb > 0:
            _core_sb = sum(f.get("invest_twd", 0) or 0 for f in _pf_sb if f.get("is_core"))
            _cur_c_sb = round(_core_sb / _tot_sb * 100, 1)
            _d_sb = round(_cur_c_sb - _sb_core, 1)
            _dc = "#f44336" if abs(_d_sb) > 10 else ("#ff9800" if abs(_d_sb) > 5 else "#00c853")
            _icon_sb = "🚨" if abs(_d_sb) > 10 else ("⚠️" if abs(_d_sb) > 5 else "✅")
            st.markdown(
                f"<div style='background:#161b22;border-radius:6px;padding:8px;margin-top:6px'>"
                f"<div style='color:#888;font-size:10px'>目前核心比例</div>"
                f"<div style='color:{_dc};font-size:22px;font-weight:900'>{_cur_c_sb}%"
                f"<span style='font-size:11px;margin-left:4px'>"
                f"({'+' if _d_sb>0 else ''}{_d_sb}%)</span></div>"
                f"<div style='color:{_dc};font-size:11px'>"
                f"{_icon_sb} {'需調整' if abs(_d_sb)>5 else '配置正常'}</div>"
                f"</div>", unsafe_allow_html=True)

    st.divider()

    # ══════════════════════════════════════════════════════
    # 🚀 全局 AI 投資決策（sidebar 按鈕，結果顯示在主畫面 Tab 下方）
    # ══════════════════════════════════════════════════════
    st.markdown("### 🤖 AI 投資決策")

    _ai_macro_ok = st.session_state.get("macro_done", False)
    _ai_pf_ok    = bool([f for f in st.session_state.get("portfolio_funds",[]) if f.get("loaded")])
    _ai_fd_ok    = bool(st.session_state.get("current_fund"))
    _ready_count = sum([_ai_macro_ok, _ai_pf_ok, _ai_fd_ok])
    _ai_sk       = "global_ai_result"
    _can_run     = bool(GEMINI_KEY) and _ready_count >= 1

    st.markdown(
        f"<div style='font-size:11px;color:#aaa;margin-bottom:6px'>"
        f"{'✅' if _ai_macro_ok else '⬜'} 總經　"
        f"{'✅' if _ai_pf_ok else '⬜'} 組合　"
        f"{'✅' if _ai_fd_ok else '⬜'} 個別基金"
        f"</div>", unsafe_allow_html=True)

    _sb_col1, _sb_col2 = st.columns([3, 1])
    with _sb_col1:
        if st.button("🚀 產出全局投資決策", type="primary",
                     key="btn_global_ai", use_container_width=True,
                     disabled=not _can_run):
            st.session_state["_ai_pending"] = True
            st.rerun()
    with _sb_col2:
        if _ai_sk in st.session_state:
            if st.button("🔄", key="btn_global_re", help="清除重分析"):
                del st.session_state[_ai_sk]
                st.session_state.pop("_ai_pending", None)
                st.rerun()

    if not _can_run and not GEMINI_KEY:
        st.caption("⚠️ 請先填入 Gemini API Key")
    elif not _can_run:
        st.caption("請先載入至少一項數據")
    elif _ai_sk in st.session_state:
        st.caption("✅ 分析完成 ↓ 見下方主畫面")
    else:
        st.caption("⬇️ 結果顯示於主畫面 Tab 下方")
    st.divider()
    # ── Debug 模式 ───────────────────────────────────────
    _debug_mode = st.checkbox("🔧 Debug 模式", value=False,
                               help="顯示爬蟲原始資料與 AI 回傳內容")
    st.session_state["debug_mode"] = _debug_mode
    st.caption("資料來源：FRED / yfinance / TDCC / MoneyDJ")

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🌐 總經儀表板",
    "📊 我的投資組合",
    "🔍 個別基金分析",
    "🔬 資料診斷",
    "📖 說明書",
])

# ═══════════════════════════════════════════════════════════
# MK 基金訊號引擎
# ═══════════════════════════════════════════════════════════
def mk_fund_signal(fund_info: dict, phase: str, score: float) -> dict:
    """根據景氣位階與基金屬性給出 MK 操作建議"""
    name  = (fund_info.get("基金名稱","") or fund_info.get("name","")).lower()
    ftype = (fund_info.get("基金種類","") or "").lower()

    core_kw = ["收益","配息","債","高股息","均衡","平衡","公債","income","bond","fixed"]
    sat_kw  = ["科技","ai","半導體","新興","生技","成長","tech","equity","growth","theme"]

    is_core = any(k in name or k in ftype for k in core_kw)
    is_sat  = any(k in name or k in ftype for k in sat_kw) and not is_core
    asset_class = "核心資產 🛡️" if is_core else ("衛星資產 ⚡" if is_sat else "混合型 ⚖️")

    phase_recs = {
        "復甦": {
            True:  ("🟢 買進加碼", "buy",    "復甦期景氣反轉，核心配息資產為最高勝率佈局，配息率此時最高"),
            False: ("🟢 積極買進", "buy",    "復甦期是衛星資產最佳進場點，中小型成長股爆發力強"),
        },
        "擴張": {
            True:  ("⚪ 持有核心", "hold",   "擴張期繼續持有核心配息資產，定期收息再投入"),
            False: ("🟡 持有設停利","hold",  "擴張期衛星資產保持持有，需設嚴格停利點(+10~15%)"),
        },
        "高峰": {
            True:  ("🟡 持有減碼", "switch", "景氣高峰，核心資產可適度減碼，增加防禦性債券"),
            False: ("🔴 賣出獲利", "sell",   "高峰期衛星資產應積極獲利了結，避免高基期風險"),
        },
        "衰退": {
            True:  ("🟢 逢低買進", "buy",    "衰退末期優先佈局核心配息資產，等待景氣拐點"),
            False: ("⏸️ 觀望等待", "hold",   "衰退期衛星資產避免進場，等待PMI落底確認訊號"),
        },
    }
    label, sig_type, reason = phase_recs.get(phase, phase_recs["擴張"])[is_core]
    # 使用 inline style（避免 Streamlit 跨元件 CSS class 問題）
    SIG_STYLES = {
        "buy":    "background:#1a3328;color:#00c853;border:1px solid #00c853",
        "sell":   "background:#3a1a1a;color:#f85149;border:1px solid #f85149",
        "hold":   "background:#1a3450;color:#58a6ff;border:1px solid #58a6ff",
        "switch": "background:#3a2a10;color:#f0a500;border:1px solid #f0a500",
    }
    sig_style = SIG_STYLES.get(sig_type, SIG_STYLES["hold"])
    sig_class = {"buy":"signal-buy","sell":"signal-sell",
                 "hold":"signal-hold","switch":"signal-switch"}.get(sig_type,"signal-hold")
    # ── v10.4 總經自動標籤：PMI/CPI/失業率驅動建議配比 ─────────────
    _ind_s = st.session_state.get("indicators", {})
    _pmi_s = _ind_s.get("PMI", {}).get("value")
    _vix_s = _ind_s.get("VIX", {}).get("value")
    _ue_s  = _ind_s.get("UNEMPLOYMENT", {}).get("value")
    _cpi_s = _ind_s.get("CPI", {}).get("value")
    _cpi_ps = _ind_s.get("CPI", {}).get("prev")
    auto_alloc = None   # (股%, 債%, label, color)
    if _pmi_s and _vix_s:
        _pf, _vf = float(_pmi_s), float(_vix_s)
        if _pf > 50 and _vf < 20:
            auto_alloc = (70, 30, "復甦/擴張—積極 (PMI>50,VIX<20)", "#00c853")
        elif _pf > 50:
            auto_alloc = (60, 40, "擴張—穩健 (PMI>50)", "#69f0ae")
        elif _pf < 50 and _vf > 25:
            auto_alloc = (40, 60, "衰退—保守 (PMI<50,VIX>25)", "#f44336")
        else:
            auto_alloc = (50, 50, "觀望—中性", "#ff9800")
    if _ue_s and float(_ue_s) > 4.0:
        auto_alloc = (40, 60, f"衰退 (失業率{_ue_s:.1f}%破4%)", "#f44336")
    if _cpi_s and _cpi_ps:
        try:
            if float(_cpi_s) > float(_cpi_ps) and float(_cpi_s) > 3.0:
                auto_alloc = (50, 50, f"升息尾聲—均衡 (CPI {_cpi_s:.1f}%↑)", "#ff9800")
        except: pass

    return dict(
        asset_class=asset_class, label=label,
        sig_type=sig_type, sig_class=sig_class,
        sig_style=sig_style, reason=reason,
        auto_alloc=auto_alloc,   # v10.4 總經自動配比
    )


# ══════════════════════════════════════════════════════════════
# 四分位法 + 吃本金評估（v10.4 helper）
# ══════════════════════════════════════════════════════════════
def _quartile_check(peer_compare: dict, risk_table: dict) -> dict:
    """
    根據 peer_compare Sharpe 分布判斷基金所屬四分位。
    回傳: {quartile, color, label, warning, fund_sharpe, peer_avg, advice}
    """
    out = {"quartile": None, "color": "#888", "label": "無同類資料",
           "warning": False, "fund_sharpe": None, "peer_avg": None, "advice": ""}
    if not peer_compare and not risk_table:
        return out

    # 取本基金 Sharpe（優先 risk_table.一年）
    fund_sh = None
    try: fund_sh = float(str(risk_table.get("一年", {}).get("Sharpe", "") or "").replace("—",""))
    except: pass

    # 從 peer_compare 抓同類 Sharpe
    peer_sharpes = []
    for row_k, row_v in (peer_compare or {}).items():
        if isinstance(row_v, dict):
            for k2, v2 in row_v.items():
                if "sharpe" in k2.lower() or "夏普" in k2:
                    try: peer_sharpes.append(float(str(v2).replace("—","")))
                    except: pass
            # Try direct key
            try:
                sh_v = float(str(row_v.get("Sharpe", row_v.get("夏普", "")) or "").replace("—",""))
                peer_sharpes.append(sh_v)
            except: pass

    if fund_sh is None and not peer_sharpes:
        return out

    # If we only have fund_sh, estimate quartile vs typical thresholds
    if not peer_sharpes:
        q = 1 if fund_sh > 1.5 else (2 if fund_sh > 0.8 else (3 if fund_sh > 0 else 4))
        c = ["#00c853","#69f0ae","#ff9800","#f44336"][q-1]
        lbl = ["第1四分位🏆(前25%)","第2四分位✅(前50%)","第3四分位⚠️(後50%)","第4四分位🔴(後25%)"][q-1]
        adv = "⚠️ 後25%達2季→建議跨行轉存至同類前25%標的" if q == 4 else ("追蹤：若下季仍第3四分位考慮替換" if q == 3 else "")
        return {"quartile": q, "color": c, "label": lbl, "warning": q >= 4,
                "fund_sharpe": fund_sh, "peer_avg": None, "advice": adv}

    import statistics as _st
    ps = sorted(peer_sharpes)
    n = len(ps)
    q25 = ps[max(0, n//4-1)]
    q75 = ps[min(n-1, 3*n//4)]
    p_avg = _st.mean(ps)

    sh_ref = fund_sh if fund_sh is not None else p_avg
    if sh_ref >= q75:        q, c, lbl = 1, "#00c853", "第1四分位🏆(前25%)"
    elif sh_ref >= p_avg:    q, c, lbl = 2, "#69f0ae", "第2四分位✅(前50%)"
    elif sh_ref >= q25:      q, c, lbl = 3, "#ff9800", "第3四分位⚠️(後50%)"
    else:                    q, c, lbl = 4, "#f44336", "第4四分位🔴(後25%—警戒)"

    adv = "⚠️ 後25%達2季→建議「跨行轉存」至同類型前25%標的，移往更強勁的管理團隊" if q >= 4 else           ("注意：若下季仍在第3四分位，考慮替換" if q == 3 else "")
    return {"quartile": q, "color": c, "label": lbl, "warning": q >= 4,
            "fund_sharpe": fund_sh, "peer_avg": round(p_avg, 3), "advice": adv}


# ═══════════════════════════════════════════════════════════
# TAB 1 — 總經位階
# ═══════════════════════════════════════════════════════════

def _render_macro_dashboard(ind, phase_info):
    sc    = phase_info["score"]
    ph    = phase_info["phase"]
    ph_c  = phase_info["phase_color"]
    alloc = phase_info["alloc"]
    alerts= phase_info.get("alerts", [])
    advice= phase_info.get("advice", "")
    rec_p = phase_info.get("rec_prob")

    # ── 頂部：景氣時鐘 ─────────────────────────────────────
    # v14.5: 顯示指標資料截至日期
    _ind_dates = [v.get("date","") for v in ind.values() if isinstance(v,dict) and v.get("date")]
    _latest_data_date = max(_ind_dates) if _ind_dates else ""
    if _latest_data_date:
        st.caption(f"📅 指標資料截至 {_latest_data_date}（FRED 有發布時差，部分指標為上月數據）")
    clock_col, score_col, alloc_col = st.columns([1.2, 1, 1.5])

    with clock_col:
        PHASES = ["衰退", "復甦", "擴張", "高峰"]
        COLORS = {"衰退":"#ff9800","復甦":"#64b5f6","擴張":"#00c853","高峰":"#f44336"}
        nxt_ph     = phase_info.get("next_phase", ph)
        t_arrow    = phase_info.get("trend_arrow", "→")
        t_label    = phase_info.get("trend_label", "持穩")
        t_color    = phase_info.get("trend_color", "#888888")
        nxt_color  = COLORS.get(nxt_ph, "#888")
        # 顯示拐點方向
        same_phase = nxt_ph == ph
        infl_html = (
            f"<div style='background:#0d1117;border:1px dashed {t_color};border-radius:8px;"
            f"padding:6px 10px;margin-top:10px;text-align:center'>"
            f"<div style='color:#888;font-size:10px;letter-spacing:1px;margin-bottom:4px'>拐點偵測</div>"
            f"<div style='font-size:15px;font-weight:800;"
            f"color:{ph_c}'>{ph}</div>"
            f"<div style='font-size:18px;color:{t_color};margin:2px 0'>{t_arrow}</div>"
            f"<div style='font-size:15px;font-weight:800;color:{nxt_color}'>{'（持穩）' if same_phase else nxt_ph}</div>"
            f"<div style='color:{t_color};font-size:10px;margin-top:4px'>{t_label}</div>"
            f"</div>"
        )
        st.markdown(f"""<div style='background:#0d1117;border:2px solid {ph_c};border-radius:14px;
            padding:18px;text-align:center'>
            <div style='color:#888;font-size:12px;letter-spacing:2px'>景氣時鐘</div>
            <div style='color:{ph_c};font-size:42px;font-weight:900;margin:6px 0'>{ph}</div>
            <div style='display:flex;justify-content:center;gap:8px;margin-top:8px'>
            {''.join(f"<span style='background:{COLORS[p] if p==ph else '#1a1a2e'};color:{'#fff' if p==ph else '#555'};padding:3px 10px;border-radius:20px;font-size:11px'>{p}</span>" for p in PHASES)}
            </div>
            <div style='color:#888;font-size:11px;margin-top:8px'>0-2衰退 | 3-4復甦 | 5-7擴張 | 8-10高峰</div>
            {infl_html}
            </div>""", unsafe_allow_html=True)

    with score_col:
        bar = "█" * int(sc) + "░" * (10 - int(sc))
        rec_html = ""
        if rec_p is not None:
            rc = "#f44336" if rec_p>60 else ("#ff9800" if rec_p>35 else "#00c853")
            rec_html = (f"<div style='margin-top:8px'><div style='color:#888;font-size:11px'>衰退機率</div>"
                        f"<div style='color:{rc};font-size:22px;font-weight:800'>{rec_p:.0f}%</div></div>")

        # v15: Weather metaphor
        _w_icon  = phase_info.get("weather_icon", "⛅")
        _w_label = phase_info.get("weather_label", "多雲")
        _w_color = phase_info.get("weather_color", "#90caf9")
        _w_alloc = phase_info.get("weather_alloc_str", "")
        _weather_bg = (
            "linear-gradient(135deg,#1a1000,#2a1f00)" if "晴" in _w_label else
            "linear-gradient(135deg,#0d1a2a,#0d1117)" if "多雲" in _w_label else
            "linear-gradient(135deg,#1a0d0d,#0d1117)")

        st.markdown(
            f"<div style='background:{_weather_bg};border:2px solid {_w_color};"
            f"border-radius:14px;padding:18px;text-align:center'>"
            f"<div style='color:#888;font-size:11px;letter-spacing:2px;margin-bottom:4px'>總經天氣預報</div>"
            f"<div style='font-size:48px;line-height:1.1;margin:4px 0'>{_w_icon}</div>"
            f"<div style='color:{_w_color};font-size:22px;font-weight:900'>{_w_label}</div>"
            f"<div style='color:#ccc;font-size:11px;margin:6px 0;padding:4px 8px;"
            f"background:#1a1a1a;border-radius:6px'>建議：{_w_alloc}</div>"
            f"<div style='color:{ph_c};font-size:13px;font-weight:700;margin-top:4px'>"
            f"Macro Score {sc}/10</div>"
            f"<div style='color:{ph_c};font-size:10px;letter-spacing:1px'>{bar}</div>"
            f"{rec_html}"
            f"</div>",
            unsafe_allow_html=True)

    with alloc_col:
        st.markdown(f"""<div style='background:#0d1117;border:1px solid #30363d;border-radius:14px;
            padding:18px'>
            <div style='color:#888;font-size:12px;letter-spacing:2px;margin-bottom:10px'>AI 建議配置</div>
            {"".join(f"<div style='display:flex;align-items:center;margin:5px 0'><div style='color:#ccc;width:38px;font-size:13px'>{k}</div><div style='flex:1;background:#161b22;border-radius:4px;height:14px;margin:0 8px'><div style='background:{'#2196f3' if k=='股票' else '#ff9800' if k=='債券' else '#78909c'};width:{v}%;height:100%;border-radius:4px'></div></div><div style='color:{'#2196f3' if k=='股票' else '#ff9800' if k=='債券' else '#78909c'};font-weight:700;font-size:13px'>{v}%</div></div>" for k,v in alloc.items())}
            <div style='color:#69f0ae;font-size:11px;margin-top:8px;line-height:1.6'>{advice}</div>
            </div>""", unsafe_allow_html=True)

    # ── 成長/通膨雙軸象限顯示（文件建議 §1）─────────────────────
    _gi = phase_info.get("growth_inflation", {})
    if _gi:
        _quad       = _gi.get("quadrant", "")
        _quad_en    = _gi.get("quadrant_en", "")
        _qcol       = _gi.get("quad_color", "#888")
        _qico       = _gi.get("quad_icon", "")
        _qdesc      = _gi.get("quad_desc", "")
        _qalloc     = _gi.get("quad_alloc", "")
        _gs         = _gi.get("growth_score", 0)
        _is         = _gi.get("inflation_score", 0)
        _g_bar_w    = int(max(0, min(100, (_gs + 1) / 2 * 100)))
        _i_bar_w    = int(max(0, min(100, (_is + 1) / 2 * 100)))
        _g_bar_col  = "#00c853" if _gs > 0 else "#f44336"
        _i_bar_col  = "#f44336" if _is > 0 else "#00c853"
        st.markdown(
            f"<div style='background:#0a1628;border:1px solid {_qcol};"
            f"border-radius:12px;padding:14px 18px;margin:8px 0;"
            f"display:flex;align-items:center;gap:16px'>"
            f"<div style='font-size:28px;line-height:1'>{_qico}</div>"
            f"<div style='flex:1'>"
            f"<div style='font-size:11px;color:#888;letter-spacing:1px;margin-bottom:4px'>"
            f"成長/通膨雙軸分析 | Growth-Inflation Matrix</div>"
            f"<div style='display:flex;align-items:baseline;gap:8px;margin-bottom:6px'>"
            f"<span style='font-size:18px;font-weight:900;color:{_qcol}'>{_quad}</span>"
            f"<span style='font-size:12px;color:#666'>{_quad_en}</span>"
            f"<span style='font-size:12px;color:{_qcol};margin-left:4px'>{_qdesc}</span>"
            f"</div>"
            f"<div style='display:flex;gap:20px'>"
            f"<div style='flex:1'><div style='font-size:10px;color:#888;margin-bottom:2px'>"
            f"成長訊號 ({'+' if _gs>0 else ''}{_gs:.2f})</div>"
            f"<div style='background:#161b22;border-radius:3px;height:6px'>"
            f"<div style='background:{_g_bar_col};width:{_g_bar_w}%;height:100%;border-radius:3px'></div></div></div>"
            f"<div style='flex:1'><div style='font-size:10px;color:#888;margin-bottom:2px'>"
            f"通膨訊號 ({'+' if _is>0 else ''}{_is:.2f})</div>"
            f"<div style='background:#161b22;border-radius:3px;height:6px'>"
            f"<div style='background:{_i_bar_col};width:{_i_bar_w}%;height:100%;border-radius:3px'></div></div></div>"
            f"</div>"
            f"<div style='font-size:10px;color:#aaa;margin-top:6px'>建議調整：{_qalloc}</div>"
            f"</div></div>",
            unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════
    # v16.0 Task 1: 總經紅綠燈 — VIX>30 / 深度倒掛強制警告
    # ══════════════════════════════════════════════════════════════
    _vix_now    = (ind.get("VIX") or {}).get("value")
    _spread_now = (ind.get("YIELD_10Y2Y") or {}).get("value")
    _hy_now     = (ind.get("HY_SPREAD") or {}).get("value")

    # Risk level: 0=green 1=yellow 2=red
    _risk_level = 0
    _risk_reasons = []
    if _vix_now is not None and _vix_now > 30:
        _risk_level = max(_risk_level, 2)
        _risk_reasons.append(f"VIX={_vix_now:.1f} > 30（市場恐慌）")
    elif _vix_now is not None and _vix_now > 22:
        _risk_level = max(_risk_level, 1)
        _risk_reasons.append(f"VIX={_vix_now:.1f} 偏高（謹慎）")
    if _spread_now is not None and _spread_now < -0.3:
        _risk_level = max(_risk_level, 2)
        _risk_reasons.append(f"殖利率深度倒掛 {_spread_now:.3f}%（衰退警告）")
    elif _spread_now is not None and _spread_now < 0:
        _risk_level = max(_risk_level, 1)
        _risk_reasons.append(f"殖利率倒掛 {_spread_now:.3f}%（觀察）")
    if _hy_now is not None and _hy_now > 6:
        _risk_level = max(_risk_level, 2)
        _risk_reasons.append(f"HY利差={_hy_now:.2f}% > 6%（信用風險高）")

    if _risk_level == 2 and _risk_reasons:
        st.markdown(
            f"<div style='background:linear-gradient(135deg,#2a0a0a,#1a0000);"
            f"border:2px solid #f44336;border-radius:12px;padding:16px 20px;"
            f"margin-bottom:12px'>"
            f"<div style='font-size:16px;font-weight:900;color:#f44336;margin-bottom:8px'>"
            f"🚨 總經環境處於高風險</div>"
            f"<div style='font-size:13px;color:#e57373;line-height:1.8'>"
            f"{'　|　'.join(_risk_reasons)}</div>"
            f"<div style='margin-top:10px;padding:10px;background:#1a0000;"
            f"border-radius:8px;font-size:12px;color:#ffcdd2;line-height:1.7'>"
            f"⚠️ <b>AI 建議</b>：建議核心資產提高「投資等級債券基金 / 防禦型公用事業基金」水位，"
            f"暫緩重壓中小型成長基金。<br>"
            f"核心部位建議 ≥ 80%，衛星部位 ≤ 20%，現金 ≥ 10%。</div>"
            f"</div>",
            unsafe_allow_html=True)
    elif _risk_level == 1 and _risk_reasons:
        st.warning(f"⚠️ 市場溫度偏高：{'　|　'.join(_risk_reasons)}　→ 維持核心配置，衛星部位可設停利")

    if alerts:
        alert_html = "".join(f"<span style='background:#3a1a00;color:#ffb300;padding:4px 12px;border-radius:20px;font-size:12px;margin:3px 3px;display:inline-block'>{a}</span>" for a in alerts)
        st.markdown(f"<div style='padding:10px 0'>{alert_html}</div>", unsafe_allow_html=True)

    # ── 指標貢獻分解（展開顯示）──────────────────────────────────
    with st.expander("📊 各指標貢獻明細（點擊展開）", expanded=False):
        _contrib_rows = []
        for _ik, _iv in ind.items():
            if not isinstance(_iv, dict): continue
            _iw  = _iv.get("weight", 1)
            _is  = _iv.get("score", 0)
            _iv2 = _iv.get("value")
            _in  = _iv.get("name", _ik)
            _isg = _iv.get("signal", "⬜")
            _clr = _iv.get("color", "#888")
            _capped = max(-_iw, min(_iw, _is))
            _contrib_rows.append({
                "指標": _in[:16],
                "數值": f"{_iv2:.2f}" if isinstance(_iv2, (int,float)) else str(_iv2)[:10],
                "信號": _isg,
                "得分": _capped,
                "權重": _iw,
                "貢獻": f"{_capped*_iw/max(sum(v.get('weight',1) for v in ind.values() if isinstance(v,dict)),1)*10:+.2f}",
            })
        if _contrib_rows:
            import pandas as _pd_contrib
            _df_contrib = _pd_contrib.DataFrame(_contrib_rows)
            st.dataframe(_df_contrib, use_container_width=True, hide_index=True)
        else:
            st.info("請先載入總經資料")

    # ── 拐點訊號橫幅 ─────────────────────────────────────────
    infl_obj = phase_info.get("inflection", {})
    infl_sigs = phase_info.get("signals", [])
    nxt_ph_b   = phase_info.get("next_phase", phase_info.get("phase",""))
    t_arrow_b  = phase_info.get("trend_arrow", "→")
    t_label_b  = phase_info.get("trend_label", "")
    t_color_b  = phase_info.get("trend_color", "#888")
    ph_b       = phase_info.get("phase","")
    ph_c_b     = phase_info.get("phase_color","#ccc")
    _PCOL      = {"衰退":"#ff9800","復甦":"#64b5f6","擴張":"#00c853","高峰":"#f44336"}
    nxt_c_b    = _PCOL.get(nxt_ph_b, "#ccc")
    direction_html = (
        f"<div style='background:#0d1117;border:1px solid {t_color_b};"
        f"border-radius:10px;padding:12px 20px;margin:8px 0;"
        f"display:flex;align-items:center;gap:16px'>"
        f"<div style='font-size:12px;color:#888;min-width:70px'>📍 拐點轉向</div>"
        f"<div style='font-size:18px;font-weight:900;color:{ph_c_b}'>{ph_b}</div>"
        f"<div style='font-size:24px;color:{t_color_b};font-weight:900'>{t_arrow_b}</div>"
        f"<div style='font-size:18px;font-weight:900;color:{nxt_c_b}'>{'（持穩）' if nxt_ph_b==ph_b else nxt_ph_b}</div>"
        f"<div style='font-size:12px;color:{t_color_b};margin-left:4px'>{t_label_b}</div>"
        + (f"<div style='margin-left:auto;font-size:12px;color:{infl_obj.get('color','#888')}'>"
           f"{infl_obj.get('label','')}</div>" if infl_obj.get('label') else "")
        + "</div>"
    )
    st.markdown(direction_html, unsafe_allow_html=True)

    # ── 拐點訊號列表 ──────────────────────────────────────
    if infl_sigs:
        sig_icons = {"buy":"🟢","bull":"🔵","warn":"🔴"}
        sigs_html = " ".join(
            f"<span style='background:{'#0a2a0a' if s['type']=='buy' else '#0a0a2a' if s['type']=='bull' else '#2a0a0a'};"
            f"color:{'#69f0ae' if s['type']=='buy' else '#64b5f6' if s['type']=='bull' else '#ff7043'};"
            f"padding:4px 10px;border-radius:16px;font-size:11px;display:inline-block;margin:2px'>"
            f"{sig_icons.get(s['type'],'ℹ️')} {s['text']}</span>"
            for s in infl_sigs[:8]
        )
        st.markdown(f"<div style='padding:6px 0'>{sigs_html}</div>", unsafe_allow_html=True)

    st.divider()

    # ── 景氣時鐘可視化圖 ──────────────────────────────────
    c_gauge, c_spread = st.columns(2)
    with c_gauge:
        sc_color = ph_c
        fig_g = go.Figure(go.Indicator(
            mode="gauge+number",
            value=sc,
            number={"font":{"size":48,"color":sc_color}},
            gauge={
                "axis":{"range":[0,10],"tickwidth":1,"tickcolor":"#8b949e"},
                "bar":{"color":sc_color,"thickness":0.3},
                "bgcolor":"#161b22","bordercolor":"#30363d",
                "steps":[
                    {"range":[0,2],"color":"#2d1515"},
                    {"range":[2,4],"color":"#152d1f"},
                    {"range":[4,7],"color":"#151f2d"},
                    {"range":[7,10],"color":"#2d2315"},
                ],
                "threshold":{"line":{"color":sc_color,"width":4},"value":sc}
            },
            title={"text":"景氣位階計","font":{"color":"#8b949e","size":14}}
        ))
        fig_g.update_layout(
            paper_bgcolor="#161b22", font={"color":"#e6edf3"},
            height=260, margin=dict(t=40,b=10,l=20,r=20)
        )
        st.plotly_chart(fig_g, use_container_width=True)

    with c_spread:
        spreads = {}
        if "YIELD_10Y3M" in ind:
            spreads["10Y-3M"] = ind["YIELD_10Y3M"].get("value",0)
        if "YIELD_10Y2Y" in ind:
            spreads["10Y-2Y"] = ind["YIELD_10Y2Y"].get("value",0)
        if spreads:
            bar_cols = ["#00c853" if v>0 else "#f44336" for v in spreads.values()]
            fig_sp = go.Figure(go.Bar(
                x=list(spreads.keys()), y=list(spreads.values()),
                marker_color=bar_cols,
                text=[f"{v:+.3f}%" for v in spreads.values()],
                textposition="outside"
            ))
            fig_sp.add_hline(y=0, line_color="#8b949e", line_dash="dash", line_width=1)
            fig_sp.update_layout(
                title="殖利率利差（由負翻正=黃金進場訊號）",
                paper_bgcolor="#161b22", plot_bgcolor="#161b22",
                font={"color":"#e6edf3","size":12},
                height=260, margin=dict(t=40,b=10,l=20,r=20),
                xaxis=dict(gridcolor="#21262d"), yaxis=dict(gridcolor="#21262d")
            )
            st.plotly_chart(fig_sp, use_container_width=True)

    st.divider()

    # ── 指標矩陣（3欄×N行）────────────────────────────────
    INDICATOR_ORDER = [
        ("PMI",          "⭐⭐⭐⭐⭐", "weight=2"),
        ("HY_SPREAD",    "⭐⭐⭐⭐⭐", "weight=2"),
        ("YIELD_10Y2Y",  "⭐⭐⭐⭐⭐", "weight=2"),
        ("YIELD_10Y3M",  "⭐⭐⭐⭐⭐", "weight=2"),
        ("M2",           "⭐⭐⭐⭐",   "weight=1"),
        ("ADL",          "⭐⭐⭐⭐",   "weight=1"),
        ("FED_BS",       "⭐⭐⭐",     "weight=1"),
        ("DXY",          "⭐⭐⭐⭐",   "weight=1"),
        ("VIX",          "⭐⭐⭐",     "weight=1"),
        ("CPI",          "⭐⭐⭐",     "weight=0.5"),
        ("FED_RATE",     "⭐⭐",       "weight=0.5"),
        ("UNEMPLOYMENT", "⭐⭐",       "weight=0.5"),
        ("PPI",          "⭐⭐",       "weight=0.5"),
        ("COPPER",       "⭐⭐",       "weight=0.5"),
        ("CONSUMER_CONF","⭐",         "weight=0.5"),
        ("JOBLESS",      "⭐",         "weight=0.5"),
        ("NEW_HOME",     "⭐",         "weight=0.5"),
    ]
    keys_present = [k for k,_,_ in INDICATOR_ORDER if k in ind]
    stars_map    = {k:s for k,s,_ in INDICATOR_ORDER}

    cols = st.columns(3)
    for idx, k in enumerate(keys_present):
        d = ind[k]
        c = cols[idx % 3]
        v    = d.get("value", 0)
        prev = d.get("prev")
        sig  = d.get("signal", "🟡")
        col  = d.get("color", "#ff9800")
        unit = d.get("unit", "")
        desc = d.get("desc", "")
        stars= stars_map.get(k,"")
        typ  = d.get("type","")
        w    = d.get("weight",1)

        if prev is not None:
            try:
                diff = round(float(v) - float(prev), 3)
                arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "─")
                a_col = "#f44336" if diff > 0 and k in ("CPI","FED_RATE","HY_SPREAD","VIX","DXY") else \
                        "#00c853" if diff > 0 else \
                        "#f44336" if diff < 0 else "#888"
                diff_str = f'<span style="color:{a_col};font-size:11px">{arrow}{abs(diff)}</span>'
            except:
                diff_str = ""
        else:
            diff_str = ""

        with c:
            series = d.get("series")
            has_spark = series is not None and hasattr(series, "__len__") and len(series) >= 4
            spark_html = ""
            if has_spark:
                try:
                    vals = list(series.values)[-24:]
                    mn, mx = min(vals), max(vals)
                    rng = mx - mn if mx != mn else 1
                    pts = [(i/(len(vals)-1)*80, 28-(v2-mn)/rng*24) for i,v2 in enumerate(vals)]
                    path = " ".join(f"{'M' if i==0 else 'L'}{x:.1f},{y:.1f}" for i,(x,y) in enumerate(pts))
                    spark_html = f'<svg width="80" height="30" style="float:right;margin-top:-4px"><path d="{path}" stroke="{col}" stroke-width="1.5" fill="none" opacity="0.8"/></svg>'
                except:
                    pass

            _n  = d.get("name","")
            _vf = (f"{v:,.0f}" if isinstance(v,float) and abs(v)>=1000 else
                   f"{v:.2f}" if isinstance(v,float) else str(v) if v is not None else "—")
            # Z-Score badge
            _z  = d.get("z_score")
            _zhtml = ""
            if _z is not None:
                _zc = "#f44336" if _z > 1.5 else ("#00c853" if _z < -1.5 else "#888")
                _zlbl = "過熱" if _z > 1.5 else ("低估" if _z < -1.5 else "中性")
                _zhtml = (f'<span style="background:#1a1a2e;color:{_zc};'
                          f'font-size:9px;padding:1px 5px;border-radius:8px;'
                          f'border:1px solid {_zc};margin-left:4px" '
                          f'title="Z-Score = {_z}（相對2年均值的標準差位置）">'
                          f'Z={_z:+.1f} {_zlbl}</span>')
            # 趨勢斜率 badge（解決月報平躺問題，改用 np.polyfit 斜率）
            _slope = d.get("trend_slope")
            _stale = d.get("days_stale")
            _slope_html = ""
            if _slope is not None:
                _abs_s = abs(_slope)
                if _abs_s < 0.005:
                    _slope_html = '<span style="color:#555;font-size:9px;margin-left:4px">▬ 平穩</span>'
                elif _slope > 0:
                    _sc = "#00c853" if _abs_s > 0.1 else "#69f0ae"
                    _slope_html = (f'<span style="color:{_sc};font-size:9px;margin-left:4px">'
                                   f'⚡ 加速↑ +{_slope:.3f}</span>')
                else:
                    _sc = "#f44336" if _abs_s > 0.1 else "#ff7043"
                    _slope_html = (f'<span style="color:{_sc};font-size:9px;margin-left:4px">'
                                   f'⚡ 加速↓ {_slope:.3f}</span>')
            # 停滯天數 badge
            _stale_html = ""
            if _stale is not None:
                if _stale > 45:
                    _stale_html = (f'<span style="color:#f44336;font-size:9px;margin-left:4px">'
                                   f'⚠️ 停滯 {_stale}天</span>')
                elif _stale > 20:
                    _stale_html = (f'<span style="color:#ff9800;font-size:9px;margin-left:4px">'
                                   f'⚠️ {_stale}天前</span>')
                else:
                    _stale_html = (f'<span style="color:#555;font-size:9px;margin-left:4px">'
                                   f'📅 {_stale}天前</span>')
            _h  = (f'<div style="background:#0d1117;border:1px solid #21262d;border-radius:10px;'
                   f'padding:12px 14px;margin-bottom:8px;min-height:88px">'
                   f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
                   f'<div>'
                   f'<div style="color:#888;font-size:10px">{typ} {stars} (w={w})</div>'
                   f'<div style="color:#c9d1d9;font-size:12px;font-weight:600">{_n}</div>'
                   f'</div>{spark_html}</div>'
                   f'<div style="display:flex;align-items:baseline;gap:6px;margin-top:4px;flex-wrap:wrap">'
                   f'<span style="color:{col};font-size:22px;font-weight:800">{_vf}</span>'
                   f'<span style="color:#555;font-size:11px">{unit}</span>'
                   f'{diff_str}'
                   f'<span style="font-size:16px;margin-left:4px">{sig}</span>'
                   f'{_zhtml}'
                   f'{_slope_html}'
                   f'{_stale_html}'
                   f'</div>'
                   f'<div style="color:#555;font-size:10px;margin-top:2px;line-height:1.4">{desc}</div>'
                   f'</div>')
            st.markdown(_h, unsafe_allow_html=True)

def _render_fund_structure(holdings: dict, mj_raw: dict = None):
    """顯示基金基本資料 + 持股：產業配置圓餅圖 + 前10大持股表格"""
    mj = mj_raw or {}
    data_date = holdings.get("data_date","") if holdings else ""

    st.markdown(
        f"### 🏗️ 基金結構分析"
        + (f"  <span style='font-size:12px;color:#888'>持股資料：{data_date}</span>" if data_date else ""),
        unsafe_allow_html=True
    )

    # ── 基本資料卡 ─────────────────────────────────────────
    info_fields = [
        ("🌏 投資區域",   mj.get("fund_region","")),
        ("⚠️ 風險等級",   mj.get("risk_level","")),
        ("🏷 基金類型",   mj.get("fund_type","")),
        ("🎯 投資標的",   mj.get("investment_target","")),
        ("⭐ 基金評等",   mj.get("fund_rating","") or "—"),
        ("💰 配息頻率",   mj.get("dividend_freq","")),
        ("☂️ 傘型架構",   mj.get("umbrella_fund","") or "—"),
        ("💱 計價幣別",   mj.get("currency","")),
        ("📏 基金規模",   (mj.get("fund_scale","") or "")[:30]),
        ("📅 最高淨值(年)", f"{mj['year_high_nav']:.4f}" if mj.get("year_high_nav") else "—"),
        ("📅 最低淨值(年)", f"{mj['year_low_nav']:.4f}" if mj.get("year_low_nav") else "—"),
    ]
    info_fields = [(k,v) for k,v in info_fields if v and v != "—" and v != "None"]
    if info_fields:
        cols_per_row = 4
        rows = [info_fields[i:i+cols_per_row] for i in range(0, len(info_fields), cols_per_row)]
        for row in rows:
            c = st.columns(len(row))
            for ci, (label, val) in enumerate(row):
                with c[ci]:
                    st.markdown(
                        f"<div style='background:#1a1f2e;border-radius:6px;padding:8px 10px;margin:2px 0'>"
                        f"<div style='color:#888;font-size:10px'>{label}</div>"
                        f"<div style='color:#e6edf3;font-size:13px;font-weight:600'>{val}</div>"
                        f"</div>",
                        unsafe_allow_html=True
                    )
        st.markdown("")

    if not holdings:
        st.warning("⚠️ 持股資料未取得（MoneyDJ 可能封鎖伺服器 IP，請改用完整網址）")
        return

    col_a, col_b = st.columns([1, 1])

    # ── 產業配置圓餅圖 ──
    with col_a:
        sectors = holdings.get("sector_alloc", [])
        # Filter bad rows
        _SEC_KW = ("資料日期","比例","投資金額","名稱","月份","日期")
        sectors = [s for s in sectors
                   if s.get("name") and
                   not any(kw in str(s.get("name","")) for kw in _SEC_KW) and
                   len(str(s.get("name",""))) <= 25 and
                   isinstance(s.get("pct"), (int,float)) and 0 < s.get("pct",0) < 100]
        if sectors:
            st.markdown("#### 📊 產業配置")
            labels = [s["name"] for s in sectors]
            values = [s["pct"] for s in sectors]
            fig = go.Figure(go.Pie(
                labels=labels, values=values, hole=0.42,
                textinfo="label+percent", textfont_size=10,
                marker=dict(colors=[
                    "#2196f3","#00bcd4","#4caf50","#ff9800","#f44336",
                    "#9c27b0","#e91e63","#00e5ff","#76ff03","#ffab40",
                    "#ff7043","#69f0ae"
                ]),
            ))
            fig.update_layout(
                paper_bgcolor="#0e1117", plot_bgcolor="#161b22",
                font_color="#e6edf3", height=320,
                margin=dict(t=10,b=10,l=10,r=10),
                legend=dict(font_size=10, orientation="v")
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("無產業配置資料")

    # ── 前10大持股 ──
    with col_b:
        top10 = holdings.get("top_holdings", [])
        # Filter out any header/garbage rows from parsing errors
        _HDR_KW = ("資料日期","投資名稱","比例","産業","持股","資料月份","Fund")
        top10 = [h for h in top10
                 if h.get("name") and
                 not any(kw in str(h.get("name","")) for kw in _HDR_KW) and
                 len(str(h.get("name",""))) <= 60 and
                 isinstance(h.get("pct"), (int,float)) and 0 < h.get("pct",0) < 100]
        if top10:
            st.markdown("#### 🔝 前10大持股")
            rows_html = "".join(
                f"<tr>"
                f"<td style='color:#888;padding:5px 8px 5px 0;font-size:12px'>{i+1}.</td>"
                f"<td style='color:#e6edf3;padding:5px 8px 5px 0;font-size:12px'>{h['name']}</td>"
                f"<td style='color:#888;padding:5px 8px 5px 0;font-size:11px'>{h.get('sector','')}</td>"
                f"<td style='color:#f0a500;padding:5px 0;font-size:12px;font-weight:700;text-align:right'>{h['pct']:.2f}%</td>"
                f"</tr>"
                for i, h in enumerate(top10)
            )
            st.markdown(
                f"<table style='width:100%;border-collapse:collapse'>{rows_html}</table>",
                unsafe_allow_html=True
            )
        else:
            st.info("無持股明細資料")


def _render_fund_analysis(fd, phase_info=None):
    if fd.get("error"):
        st.error(fd["error"]); return
    s    = fd.get("series")
    m    = fd.get("metrics", {})
    divs = fd.get("dividends", [])
    name = fd.get("fund_name", "")
    fk   = fd.get("full_key", "")
    mj_raw = fd.get("moneydj_raw", {}) or {}   # v10.3 fix: define early for peer_compare/yearly blocks
    if s is None or s.empty or not m:
        st.warning("資料不足，無法顯示分析"); return

    n_nav = len(s); n_div = len(divs)
    st.success(f"✅ **{name or fk}** ｜ 淨值 {n_nav} 筆 ‧ 配息 {n_div} 筆")

    # ── MK 訊號卡片 ─────────────────────────────────────────
    if phase_info:
        sig = mk_fund_signal(fd, phase_info["phase"], phase_info["score"])
        ph_c = phase_info["phase_color"]
        # v10.4: show auto allocation recommendation
        _aa = sig.get("auto_alloc")
        if _aa:
            _aa_stk, _aa_bnd, _aa_lbl, _aa_c = _aa
            st.markdown(
                f"<div style='background:#0d1b2a;border:1px solid {_aa_c};border-radius:8px;"
                f"padding:8px 14px;margin:4px 0 8px 0;display:flex;align-items:center;gap:16px'>"
                f"<span style='font-size:18px'>📊</span>"
                f"<div><div style='color:{_aa_c};font-weight:700;font-size:12px'>"
                f"總經自動配比建議：{_aa_lbl}</div>"
                f"<div style='color:#ccc;font-size:12px;margin-top:2px'>"
                f"股 {_aa_stk}% ／ 債 {_aa_bnd}%</div></div>"
                f"</div>", unsafe_allow_html=True)
        st.markdown(f"""<div style='background:#161b22;border:1px solid #30363d;border-radius:10px;
            padding:14px 18px;margin:8px 0;display:flex;align-items:center;gap:16px;flex-wrap:wrap'>
            <div>
              <div style='color:#888;font-size:11px'>資產屬性</div>
              <div style='font-size:14px;font-weight:700;color:#58a6ff'>{sig["asset_class"]}</div>
            </div>
            <div>
              <div style='color:#888;font-size:11px'>MK 操作訊號</div>
              <span style='{sig.get("sig_style","background:#1a3450;color:#58a6ff;border:1px solid #58a6ff")};padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700;display:inline-block'>{sig["label"]}</span>
            </div>
            <div style='flex:1'>
              <div style='color:#888;font-size:11px'>依景氣位階（{phase_info["phase"]} {phase_info["score"]}/10）</div>
              <div style='font-size:12px;color:#c9d1d9'>{sig["reason"]}</div>
            </div>
            </div>""", unsafe_allow_html=True)

    # ── 現價 + 停損停利 快速面板 ────────────────────────────
    _nav_now  = m.get("nav", 0)
    _buy1_v   = m.get("buy1")
    _buy2_v   = m.get("buy2")
    _buy3_v   = m.get("buy3")
    _sell1_v  = m.get("sell1")
    _sell2_v  = m.get("sell2")
    _pos_l    = m.get("pos_label", "正常波動區")
    _pos_c    = m.get("pos_color", "#888")
    _buy_mode = m.get("buy_mode", "")
    _std_1y_v = m.get("std_1y", 0) or 0
    _yr_h     = m.get("year_high_nav") or m.get("high_2y") or m.get("high_1y")
    _yr_l     = m.get("year_low_nav")
    _sigma_amt= round((_yr_h - _yr_l) / 3, 4) if (_yr_h and _yr_l and _yr_h > _yr_l) else round((_yr_h or _nav_now) * _std_1y_v / 100, 4)

    if _nav_now:
        def _pct_from_high(p):
            return f"({(p/(_yr_h or p)-1)*100:+.1f}%)" if (_yr_h and p) else ""
        _nav_cols = st.columns([2,1,1,1,1,1])
        with _nav_cols[0]:
            st.markdown(
                f"<div style='background:#0d1117;border:2px solid {_pos_c};"
                f"border-radius:10px;padding:10px 14px;text-align:center'>"
                f"<div style='font-size:10px;color:#888;margin-bottom:2px'>目前淨值</div>"
                f"<div style='font-size:26px;font-weight:900;color:#e6edf3'>{_nav_now:.4f}</div>"
                f"<div style='font-size:12px;font-weight:700;color:{_pos_c};margin-top:3px'>{_pos_l}</div>"
                f"</div>", unsafe_allow_html=True)
        _price_rows = [
            ("🔔 停利2", _sell2_v, "#f44336", "突破年高"),
            ("🔔 停利1", _sell1_v, "#ff7043", "回到年高"),
            ("✅ 買1 -1σ", _buy1_v, "#69f0ae", "建議加碼20%"),
            ("📈 買2 -2σ", _buy2_v, "#00c853", "建議加碼30%"),
            ("🔥 買3 -3σ", _buy3_v, "#9c27b0", "建議加碼50%"),
        ]
        for ci, (label, price, color, hint) in enumerate(_price_rows):
            if price:
                with _nav_cols[ci+1]:
                    st.markdown(
                        f"<div style='background:#161b22;border:1px solid {color}30;"
                        f"border-radius:8px;padding:8px;text-align:center;height:100%'>"
                        f"<div style='font-size:9px;color:#888'>{label}</div>"
                        f"<div style='font-size:15px;font-weight:900;color:{color}'>{price:.4f}</div>"
                        f"<div style='font-size:9px;color:#555'>{_pct_from_high(price)}</div>"
                        f"<div style='font-size:8px;color:#555'>{hint}</div>"
                        f"</div>", unsafe_allow_html=True)
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

    # ── 淨值走勢 + MK 買點 ──────────────────────────────────────
    st.markdown("### 📈 淨值走勢 + MK 買點")
    df_show = s.reset_index(); df_show.columns = ["date","nav"]
    if "ma20" in m: df_show["ma20"] = s.rolling(20).mean().values
    if "ma60" in m: df_show["ma60"] = s.rolling(60).mean().values
    fig_n = go.Figure()
    fig_n.add_trace(go.Scatter(x=df_show["date"],y=df_show["nav"],
        name="淨值",line=dict(color="#2196f3",width=1.5)))
    if "ma20" in df_show.columns:
        fig_n.add_trace(go.Scatter(x=df_show["date"],y=df_show["ma20"],
            name="MA20",line=dict(color="#ff9800",width=1,dash="dot")))
    if "ma60" in df_show.columns:
        fig_n.add_trace(go.Scatter(x=df_show["date"],y=df_show["ma60"],
            name="MA60",line=dict(color="#9c27b0",width=1,dash="dot")))
    buy_levels = [
        (m.get("buy1"), f"買1(-1σ) {m.get('buy1','')}", "#69f0ae"),
        (m.get("buy2"), f"買2(-2σ) {m.get('buy2','')}", "#00c853"),
        (m.get("buy3"), f"🔥買3(-3σ) {m.get('buy3','')}", "#9c27b0"),
    ]
    for bv,bl,bc in buy_levels:
        if bv:
            fig_n.add_hline(y=bv, line_color=bc, line_dash="dot",
                annotation_text=bl, annotation_font_color=bc,
                annotation_position="bottom right")
    # BB 布林通道 — rolling 時間序列（正確方式）
    bb_up_s = m.get("bb_upper_series")
    bb_lo_s = m.get("bb_lower_series")
    if bb_up_s is not None and len(bb_up_s) > 0:
        fig_n.add_trace(go.Scatter(
            x=bb_up_s.index, y=bb_up_s.values,
            name="BB上軌", line=dict(color="#f44336", width=1, dash="dash"),
            opacity=0.7))
    if bb_lo_s is not None and len(bb_lo_s) > 0:
        fig_n.add_trace(go.Scatter(
            x=bb_lo_s.index, y=bb_lo_s.values,
            name="BB下軌", line=dict(color="#00c853", width=1, dash="dash"),
            opacity=0.7, fill="tonexty" if bb_up_s is not None else None,
            fillcolor="rgba(100,200,100,0.04)"))
    fig_n.update_layout(paper_bgcolor="#0e1117",plot_bgcolor="#161b22",
        font_color="#e6edf3",height=370,margin=dict(t=15,b=30,l=40,r=20),
        legend=dict(orientation="h",font_size=10,y=1.02),
        hovermode="x unified",yaxis_title="淨值")
    st.plotly_chart(fig_n,use_container_width=True)

    # ── 三欄指標 ────────────────────────────────────────────────
    st.markdown("### 📊 關鍵指標")
    kb1,kb2,kb3 = st.columns(3)
    with kb1:
        st.markdown("#### 📏 標準差風險指標")
        _pos_bar_c = m.get("pos_color","#888"); _pos_bar_l = m.get("pos_label","")
        _buy3_bar  = m.get("buy3",0)
        _h2y_bar   = m.get("high_2y", m.get("high_1y",0))
        _nav_bar   = m.get("nav",0)
        if _h2y_bar and _buy3_bar and _h2y_bar > _buy3_bar:
            _pct_bar  = max(0, min(100, (_nav_bar-_buy3_bar)/(_h2y_bar-_buy3_bar)*100))
            _buy1_pct = max(0, min(100, ((m.get('buy1',_nav_bar) or _nav_bar)-_buy3_bar)/(_h2y_bar-_buy3_bar)*100))
            _buy2_pct = max(0, min(100, ((m.get('buy2',_nav_bar) or _nav_bar)-_buy3_bar)/(_h2y_bar-_buy3_bar)*100))
            if _pct_bar <= _buy2_pct:
                _zone_lbl, _zone_c = '🟢 便宜區（建議加碼）', '#00c853'
            elif _pct_bar <= _buy1_pct:
                _zone_lbl, _zone_c = '🟡 合理區（持續扣款）', '#ff9800'
            else:
                _zone_lbl, _zone_c = '🔴 偏高區（準備停利）', '#f44336'
            st.markdown(
                f"<div style='background:#1a1f2e;border-radius:10px;padding:10px 12px;margin-bottom:8px'>"
                f"<div style='font-size:11px;color:#888;margin-bottom:5px'>買賣水位計</div>"
                f"<div style='position:relative;height:14px;border-radius:7px;overflow:hidden;"
                f"background:linear-gradient(to right,#00c853 0%,#00c853 {_buy2_pct:.0f}%,"
                f"#ff9800 {_buy2_pct:.0f}%,#ff9800 {_buy1_pct:.0f}%,"
                f"#f44336 {_buy1_pct:.0f}%,#f44336 100%)'>"
                f"<div style='position:absolute;top:0;left:{_pct_bar:.0f}%;transform:translateX(-50%);"
                f"width:4px;height:100%;background:#fff;border-radius:2px'></div></div>"
                f"<div style='display:flex;justify-content:space-between;font-size:9px;color:#666;margin-top:3px'>"
                f"<span>-3σ<br>買3</span><span>-2σ<br>買2</span><span>-1σ<br>買1</span><span>年高<br>停利</span></div>"
                f"<div style='margin-top:6px;font-size:12px;font-weight:700;color:{_zone_c}'>"
                f"{_zone_lbl} — 現價 {_nav_bar}</div>"
                f"</div>", unsafe_allow_html=True)
        risk_tbl_d = m.get("risk_table", {})
        std_source  = m.get("std_source", "nav")
        src_label   = "📊 wb07 績效評比" if std_source=="wb07" else "📈 淨值計算"
        st.markdown(f"<div style='color:#888;font-size:10px;margin-top:4px'>σ 資料來源：{src_label}</div>",
                    unsafe_allow_html=True)
        _std_cn  = m.get("std_multi_cn", {})
        _period_labels = ["1年", "2年", "3年", "5年"]
        _wb07_periods  = ["一年", "三年", "五年"]
        _wb07_stds = [risk_tbl_d.get(p,{}).get("標準差") for p in _wb07_periods if p in risk_tbl_d]
        _use_cn = (not risk_tbl_d) or (len(set(str(v) for v in _wb07_stds if v)) <= 1)
        if _use_cn and _std_cn:
            _cn_pairs = [(lb, _std_cn.get(lb)) for lb in _period_labels if _std_cn.get(lb)]
            if _cn_pairs:
                sc2 = st.columns(len(_cn_pairs))
                for ci, (period, sv) in enumerate(_cn_pairs):
                    _rc = "#00c853" if sv<8 else ("#ff9800" if sv<15 else "#f44336")
                    with sc2[ci]:
                        st.markdown(
                            f"<div style='background:#1a1f2e;border-radius:6px;padding:6px;text-align:center'>"
                            f"<div style='font-size:10px;color:#888'>{period}</div>"
                            f"<div style='font-size:16px;font-weight:900;color:{_rc}'>{sv:.2f}%</div>"
                            f"<div style='font-size:9px;color:#555'>年化σ</div>"
                            f"</div>", unsafe_allow_html=True)
        elif risk_tbl_d:
            # ── wb07 完整績效評比表（標準差/Sharpe/Alpha/Beta/R²/相關係數/TE/Variance）──
            periods_d = [p for p in ["六個月","一年","三年","五年","十年"] if p in risk_tbl_d]
            if periods_d:
                # 1) 頂部快速指標卡（一年期重點）
                _r1y = risk_tbl_d.get("一年", risk_tbl_d.get(periods_d[0], {}))
                _std1  = _r1y.get("標準差", "—")
                _sh1   = _r1y.get("Sharpe", "—")
                _al1   = _r1y.get("Alpha", "—")
                _be1   = _r1y.get("Beta", "—")
                _rs1   = _r1y.get("R-squared", _r1y.get("R²", "—"))
                _cor1  = _r1y.get("與指數相關係數", "—")
                _te1   = _r1y.get("Tracking Error", "—")
                _var1  = _r1y.get("Variance", "—")
                def _rc(v, low=8, hi=15):
                    try: fv=float(v); return "#00c853" if fv<low else("#ff9800" if fv<hi else"#f44336")
                    except: return "#888"
                def _sc(v, lo=0, hi=1):
                    try: fv=float(v); return "#00c853" if fv>hi else("#ff9800" if fv>lo else"#f44336")
                    except: return "#888"
                # 主要指標：σ + Sharpe（永遠顯示）
                _main_cards = [
                    ("波動幅度(σ 1Y)", f"{_std1}%", _rc(_std1), "核心目標<15%"),
                    ("績效CP值 Sharpe", str(_sh1),   _sc(_sh1, 0, 1), "每承擔1分風險的報酬"),
                ]
                sc2m = st.columns(2)
                for ci,(lbl,val,col,sub) in enumerate(_main_cards):
                    with sc2m[ci]:
                        st.markdown(
                            f"<div style='background:#1a1f2e;border-radius:8px;padding:10px;text-align:center'>"
                            f"<div style='font-size:10px;color:#888'>{lbl}</div>"
                            f"<div style='font-size:20px;font-weight:900;color:{col}'>{val}</div>"
                            f"<div style='font-size:9px;color:#555'>{sub}</div>"
                            f"</div>", unsafe_allow_html=True)
                # 進階量化指標：Alpha/Beta/R²（折疊，供進階投資人參考）
                with st.expander("🤓 進階量化指標（Alpha / Beta / R² / TE）"):
                    _adv_cards = [
                        ("Alpha(1Y)",  str(_al1), _sc(_al1,-0.1,0.1), "超額報酬"),
                        ("Beta(1Y)",   str(_be1), "#69f0ae",           "市場敏感度"),
                        ("R²(1Y)",     str(_rs1), "#69f0ae",           "與指數連動度"),
                        ("TE(1Y)",     str(_te1), "#888",              "追蹤誤差"),
                    ]
                    sc2a = st.columns(4)
                    for ci,(lbl,val,col,sub) in enumerate(_adv_cards):
                        with sc2a[ci]:
                            st.markdown(
                                f"<div style='background:#1a1f2e;border-radius:8px;padding:8px;text-align:center'>"
                                f"<div style='font-size:10px;color:#888'>{lbl}</div>"
                                f"<div style='font-size:15px;font-weight:900;color:{col}'>{val}</div>"
                                f"<div style='font-size:9px;color:#555'>{sub}</div>"
                                f"</div>", unsafe_allow_html=True)
                # 2) 完整 wb07 表格（所有期間 × 所有指標）
                st.markdown("<div style='margin-top:10px'></div>", unsafe_allow_html=True)
                _FIELD_ORDER = ["標準差","Sharpe","Alpha","Beta","R-squared",
                                "與指數相關係數","Tracking Error","Variance"]
                _field_labels = {
                    "標準差":        "標準差 (σ)",
                    "Sharpe":        "Sharpe Ratio",
                    "Alpha":         "Alpha",
                    "Beta":          "Beta",
                    "R-squared":     "R-squared",
                    "與指數相關係數": "相關係數",
                    "Tracking Error":"Tracking Error",
                    "Variance":      "Variance",
                }
                # 收集所有實際出現的 fields
                all_fields = []
                for f in _FIELD_ORDER:
                    if any(f in risk_tbl_d.get(p,{}) for p in periods_d):
                        all_fields.append(f)
                # 加上未列在清單的欄位
                for p in periods_d:
                    for f in risk_tbl_d.get(p,{}):
                        if f not in all_fields: all_fields.append(f)
                if all_fields:
                    # Build HTML table
                    _hdr = "<tr><th style='background:#1c2333;padding:5px 10px;color:#888;font-size:11px'>指標</th>"
                    for p in periods_d:
                        _hdr += f"<th style='background:#1c2333;padding:5px 10px;color:#69f0ae;font-size:11px'>{p}</th>"
                    _hdr += "</tr>"
                    _rows_html = ""
                    for field in all_fields:
                        _rows_html += f"<tr><td style='padding:4px 10px;font-size:11px;color:#ccc;background:#161b22'>{_field_labels.get(field,field)}</td>"
                        for p in periods_d:
                            val = risk_tbl_d.get(p,{}).get(field,"—")
                            # Colour coding
                            if field == "標準差":
                                try: _c = "#00c853" if float(val)<8 else("#ff9800" if float(val)<15 else"#f44336")
                                except: _c="#aaa"
                            elif field == "Sharpe":
                                try: _c = "#00c853" if float(val)>1 else("#ff9800" if float(val)>0 else"#f44336")
                                except: _c="#aaa"
                            elif field == "Alpha":
                                try: _c = "#00c853" if float(val)>0 else "#f44336"
                                except: _c="#aaa"
                            else:
                                _c = "#ddd"
                            _disp = f"{val}%" if field in ("標準差","Tracking Error","Variance") and val!="—" else str(val)
                            _rows_html += f"<td style='padding:4px 10px;font-size:12px;color:{_c};text-align:center;background:#161b22'>{_disp}</td>"
                        _rows_html += "</tr>"
                    st.markdown(
                        f"<div style='overflow-x:auto;margin-bottom:8px'>"
                        f"<table style='border-collapse:collapse;width:100%;border:1px solid #30363d'>"
                        f"{_hdr}{_rows_html}</table></div>",
                        unsafe_allow_html=True)
                # 3) 同類排名表（peer_compare）
                _peer = mj_raw.get("risk_metrics", {}).get("peer_compare", {})
                if not _peer:
                    st.info("📊 **同組基金排行**：MoneyDJ wb07 同類比較資料暫時無法取得。"                            " 可能原因：①IP 限制 ②頁面結構異動。"                            " 請直接訪問 [MoneyDJ wb07]("                            f"https://www.moneydj.com/funddj/yp/wb07.djhtm?a={mj_raw.get('fund_code','')}) 查閱。")
                if _peer:
                    st.markdown("##### 📊 同類型排名比較")
                    _peer_fields = ["年平均報酬率","Sharpe","Beta","標準差"]
                    _peer_rows = list(_peer.items())[:8]  # 最多8行
                    _ph = "<tr><th style='background:#1c2333;padding:4px 8px;color:#888;font-size:11px'>項目</th>"
                    # Detect columns from first row
                    _pcols = list(_peer_rows[0][1].keys()) if _peer_rows else []
                    for pc in _pcols:
                        _ph += f"<th style='background:#1c2333;padding:4px 8px;color:#69f0ae;font-size:11px'>{pc}</th>"
                    _ph += "</tr>"
                    _pr_html = ""
                    for row_k, row_v in _peer_rows:
                        _is_fund = ("本基金" in row_k or "基金" in row_k or len(row_k) > 10)
                        _bg = "#1a2a1a" if _is_fund else "#161b22"
                        _pr_html += f"<tr><td style='padding:4px 8px;font-size:10px;color:#ccc;background:{_bg}'>{row_k}</td>"
                        for pc in _pcols:
                            v = row_v.get(pc, "—")
                            _pr_html += f"<td style='padding:4px 8px;font-size:11px;color:#ddd;text-align:center;background:{_bg}'>{v}</td>"
                        _pr_html += "</tr>"
                    st.markdown(
                        f"<table style='border-collapse:collapse;width:100%;border:1px solid #30363d'>"
                        f"{_ph}{_pr_html}</table>",
                        unsafe_allow_html=True)
                # 4) 年度績效比較表（yearly_stats）
                _yearly = mj_raw.get("risk_metrics", {}).get("yearly_stats", {})
                if _yearly:
                    st.markdown("##### 📅 年度績效比較（wb07）")
                    _yrs = sorted(_yearly.keys(), reverse=True)[:5]
                    _yfields = []
                    for yr in _yrs:
                        for f in _yearly.get(yr, {}):
                            if f not in _yfields: _yfields.append(f)
                    _yh = "<tr><th style='background:#1c2333;padding:4px 8px;color:#888;font-size:11px'>指標</th>"
                    for yr in _yrs:
                        _yh += f"<th style='background:#1c2333;padding:4px 8px;color:#69f0ae;font-size:11px'>{yr}</th>"
                    _yh += "</tr>"
                    _yr_html = ""
                    for yf in _yfields:
                        _yr_html += f"<tr><td style='padding:4px 8px;font-size:11px;color:#ccc;background:#161b22'>{yf}</td>"
                        for yr in _yrs:
                            yv = _yearly.get(yr, {}).get(yf, "—")
                            try:
                                fv = float(yv)
                                if yf == "年化標準差":
                                    _c = "#00c853" if fv<8 else("#ff9800" if fv<15 else"#f44336")
                                elif "Ratio" in yf or "Index" in yf:
                                    _c = "#00c853" if fv>0 else "#f44336"
                                else: _c = "#ddd"
                                _yr_html += f"<td style='padding:4px 8px;font-size:11px;color:{_c};text-align:center;background:#161b22'>{yv}</td>"
                            except:
                                _yr_html += f"<td style='padding:4px 8px;font-size:11px;color:#888;text-align:center;background:#161b22'>{yv}</td>"
                        _yr_html += "</tr>"
                    st.markdown(
                        f"<table style='border-collapse:collapse;width:100%;border:1px solid #30363d'>"
                        f"{_yh}{_yr_html}</table>",
                        unsafe_allow_html=True)
    if divs and len(divs) >= 2:
        st.markdown("### 💸 配息記錄")

        # ── ⚠️ 概念說明：配息年化率 vs 含息報酬率 ───────────────────
        # v10: 優先使用 MoneyDJ wb05「年化配息率%」欄（官方值）
        _mj_raw_d   = fd.get("moneydj_raw", {}) if hasattr(fd, "get") else {}
        _mj_div_yield = _mj_raw_d.get("moneydj_div_yield")
        try: _mj_div_yield = float(_mj_div_yield) if _mj_div_yield is not None else None
        except (ValueError, TypeError): _mj_div_yield = None
        _adr_disp   = _mj_div_yield if (_mj_div_yield and _mj_div_yield > 0) else (m.get("annual_div_rate", 0) or 0)
        try: _adr_disp = float(_adr_disp)
        except (ValueError, TypeError): _adr_disp = 0.0
        _adr_src    = "MoneyDJ wb05" if (_mj_div_yield and _mj_div_yield > 0) else "自算估值"
        # v10: 優先使用 MoneyDJ wb01 含息報酬率（績效頁，已考慮配息）
        _perf_disp  = _mj_raw_d.get("perf", {})
        _tr1_disp   = _perf_disp.get("1Y") if isinstance(_perf_disp, dict) else None
        _tr3_disp   = _perf_disp.get("3Y") if isinstance(_perf_disp, dict) else None
        _tr5_disp   = _perf_disp.get("5Y") if isinstance(_perf_disp, dict) else None
        _perf_src   = _perf_disp.get("perf_source", "wb01") if isinstance(_perf_disp, dict) else "wb01"
        _gain_disp  = round(_tr1_disp - _adr_disp, 2) if _tr1_disp is not None else None
        _gain_c     = "#f44336" if (_gain_disp is not None and _gain_disp < 0) else "#00c853"
        _eat_lbl    = "🔴 吃本金⚠️" if (_gain_disp is not None and _gain_disp < 0) else ("✅ 健康成長" if _gain_disp is not None else "⚪ 無法判斷")
        st.markdown(
            f"<div style='background:#131a0a;border:1px solid #f0a500;border-radius:10px;"
            f"padding:12px 16px;margin:6px 0 10px 0'>"
            f"<div style='font-size:12px;font-weight:700;color:#f0a500;margin-bottom:6px'>"
            f"⚠️ 配息年化率 vs 含息報酬率（兩者完全不同）</div>"
            f"<div style='display:flex;gap:20px;flex-wrap:wrap;font-size:11px;color:#ccc'>"
            f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px'>"
            # 欄1: 配息年化率（MoneyDJ wb05 直接讀取）
            f"<div style='background:#1e1a0a;border-radius:6px;padding:10px'>"
            f"<div style='color:#888;font-size:10px'>📌 配息年化率"
            f"<span style='background:#333;border-radius:3px;padding:1px 5px;margin-left:4px;font-size:9px'>{_adr_src}</span></div>"
            f"<div style='font-size:20px;font-weight:900;color:#ff9800'>{_adr_disp:.2f}%</div>"
            f"<div style='color:#666;font-size:10px'>每單位配息÷除息前淨值×年配次數<br/>MoneyDJ wb05「年化配息率%」欄</div>"
            f"</div>"
            # 欄2: 含息報酬率（MoneyDJ wb01 績效頁）
            f"<div style='background:#0a1e0e;border-radius:6px;padding:10px'>"
            f"<div style='color:#888;font-size:10px'>📈 含息報酬率"
            f"<span style='background:#333;border-radius:3px;padding:1px 5px;margin-left:4px;font-size:9px'>MoneyDJ wb01</span></div>"
            f"<div style='font-size:20px;font-weight:900;color:#69f0ae'>{'N/A' if _tr1_disp is None else f'{_tr1_disp:+.2f}%'}</div>"
            f"<div style='color:#666;font-size:10px'>1年　"
            f"{'3年: '+f'{_tr3_disp:+.2f}%' if _tr3_disp is not None else '3年: N/A'}　"
            f"{'5年: '+f'{_tr5_disp:+.2f}%' if _tr5_disp is not None else ''}</div>"
            f"<div style='color:#555;font-size:9px;margin-top:2px'>績效計算皆有考慮配息情況（MoneyDJ說明）</div>"
            f"</div>"
            # 欄3: 真實收益
            f"<div style='background:#0a0a1e;border-radius:6px;padding:10px'>"
            f"<div style='color:#888;font-size:10px'>🔬 真實收益能力</div>"
            f"<div style='font-size:20px;font-weight:900;color:{_gain_c}'>{'N/A' if _gain_disp is None else f'{_gain_disp:+.2f}%'}</div>"
            f"<div style='color:#666;font-size:10px'>= 含息報酬率 − 配息年化率</div>"
            f"<div style='font-size:12px;font-weight:700;color:{_gain_c};margin-top:4px'>{_eat_lbl}</div>"
            f"<div style='color:#555;font-size:9px'>負值代表配息消耗本金</div>"
            f"</div>"
            f"</div></div>",
            unsafe_allow_html=True)

        df_d = pd.DataFrame(divs).sort_values("date").reset_index(drop=True)
        # ── 最新二期配息數值（圖示上方）──────────────────────
        _last2 = df_d.tail(2)
        _div_cols = st.columns(2)
        for _di, (_drow_idx, _drow) in enumerate(_last2.iterrows()):
            _d_amt = _drow.get("amount", 0)
            _d_dt  = str(_drow.get("date",""))[:10]
            _d_prev_amt = df_d.iloc[-3]["amount"] if len(df_d)>=3 and _di==1 else None
            _d_diff_html = ""
            if _di == 1 and _d_prev_amt is not None:
                _d_diff = round(_d_amt - _d_prev_amt, 6)
                _d_diff_c = "#00c853" if _d_diff >= 0 else "#f44336"
                _d_arr = "▲" if _d_diff > 0 else ("▼" if _d_diff < 0 else "─")
                _d_diff_html = f"<span style='color:{_d_diff_c};font-size:11px'>{_d_arr}{abs(_d_diff):.6f}</span>"
            _period_label = "最新一期" if _di == 1 else "前一期"
            _div_cols[_di].markdown(
                f"<div style='background:#1a1200;border:2px solid #ff9800;border-radius:10px;"
                f"padding:10px 14px;text-align:center'>"
                f"<div style='color:#888;font-size:10px'>{_period_label} 每單位配息</div>"
                f"<div style='color:#ff9800;font-size:24px;font-weight:900'>{_d_amt:.6f}</div>"
                f"<div style='color:#555;font-size:10px;margin-top:2px'>{_d_dt} {_d_diff_html}</div>"
                f"</div>", unsafe_allow_html=True)
        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        # ── 配息柱狀圖 ──────────────────────────────────────
        fig_d = go.Figure(go.Bar(x=df_d["date"], y=df_d["amount"],
            marker_color="#ff9800", name="配息",
            text=[f"{v:.6f}" if v < 1 else f"{v:.4f}" for v in df_d["amount"]],
            textposition="outside", textfont=dict(size=9, color="#ff9800")))
        fig_d.update_layout(paper_bgcolor="#0e1117", plot_bgcolor="#161b22",
            font_color="#e6edf3", height=240, margin=dict(t=30, b=30, l=40, r=20),
            yaxis=dict(gridcolor="#21262d"))
        st.plotly_chart(fig_d, use_container_width=True)



with tab1:
    st.markdown("## 🌐 總經位階評估 ＆ 拐點偵測")
    st.caption("MK（郭俊宏）三層指標加權方法論 v7 — 領先×2 | 中級×1 | 次級×0.5")

    FRED_KEY, GEMINI_KEY = _load_keys()
    if not FRED_KEY:
        st.warning("⚠️ 請在 Cell 1 填入 FRED_API_KEY")
    else:
        # v14.5: 顯示最後更新時間 + 過期提示
        _last_upd = st.session_state.get("macro_last_update")
        _stale    = False
        if _last_upd:
            _age_h = (_now_tw() - _last_upd).total_seconds() / 3600
            _stale = _age_h > 4
            _upd_str = _last_upd.strftime("%Y-%m-%d %H:%M")
            if _stale:
                st.warning(f"⏰ 總經資料已 {_age_h:.1f} 小時未更新（上次：{_upd_str}），建議重新載入")
            else:
                st.caption(f"🕐 最後從 FRED 抓取：{_upd_str}（{_age_h:.1f} 小時前）｜PMI / 失業率為月頻資料，每月更新一次")
        else:
            st.info("💡 尚未載入總經資料，點擊下方按鈕開始")

        # 首次進入或資料過期 → 自動觸發載入
        _auto_load = (not st.session_state.macro_done) or _stale
        _btn_label = "🔄 更新總經資料" if st.session_state.macro_done else "📡 載入總經資料"

        _btn_clicked = st.button(_btn_label, type="primary")
        # Fix: clear st.cache_data when button clicked or stale, so fresh FRED data is fetched
        if _btn_clicked or _stale:
            fetch_all_indicators.clear()
        if _btn_clicked or _auto_load:
            with st.spinner("📡 從 FRED / Yahoo Finance 抓取最新指標..."):
                # 傳入今日日期作為 cache key，確保每天自動失效一次
                ind   = fetch_all_indicators(FRED_KEY, cache_date=datetime.date.today().isoformat())
                phase = calc_macro_phase(ind)
                old_phase = st.session_state.phase_info.get("phase","") if st.session_state.phase_info else ""
                new_phase = phase.get("phase","")
                if old_phase and old_phase != new_phase:
                    st.session_state.phase_history.append({
                        "from": old_phase, "to": new_phase,
                        "date": datetime.date.today().isoformat(),
                        "score": phase.get("score",0)
                    })
                st.session_state.indicators       = ind
                st.session_state.prev_phase       = old_phase
                st.session_state.phase_info       = phase
                st.session_state.macro_done       = True
                st.session_state.macro_ai         = ""
                st.session_state.macro_last_update = _now_tw()  # Fix: use TW timezone
                if not _auto_load:
                    # Show latest FRED data date for key monthly indicators
                    _pmi_date = ind.get("PMI", {}).get("date", "")
                    _unrate_date = ind.get("UNRATE", {}).get("date", "")
                    _note = ""
                    if _pmi_date:
                        _note += f" | PMI 資料期：{_pmi_date}"
                    if _unrate_date:
                        _note += f" | 失業率資料期：{_unrate_date}"
                    st.success(f"✅ 已從 FRED 抓取最新資料！{len(ind)} 個指標（{_now_tw().strftime('%H:%M')} TW）{_note}")

    if not st.session_state.macro_done:
        st.info("👆 點擊「載入總經資料」開始分析")
    else:
        ind   = st.session_state.indicators
        phase = st.session_state.phase_info

        # ── v10.4 PMI/VIX 總經自動標籤 Banner（移至 fetch 之後，確保使用新鮮資料）
        _pmi_t1  = ind.get("PMI", {}).get("value")
        _vix_t1  = ind.get("VIX", {}).get("value")
        _ue_t1   = ind.get("UNEMPLOYMENT", {}).get("value")
        _cpi_t1  = ind.get("CPI", {}).get("value")
        _cpi_pt1 = ind.get("CPI", {}).get("prev")
        if _pmi_t1 or _vix_t1 or _ue_t1:
            _t1_banners = []
            if _pmi_t1 and _vix_t1:
                _pf_t1 = float(_pmi_t1); _vf_t1 = float(_vix_t1)
                if _pf_t1 > 50 and _vf_t1 < 20:
                    _t1_banners.append(("🚀", "#00c853", "#071a0f",
                        f"復甦/擴張訊號：PMI {_pf_t1:.1f} > 50 且 VIX {_vf_t1:.1f} < 20",
                        "建議【加碼衛星資產】（成長基金）→ 股 7 債 3 配置"))
                elif _pf_t1 > 50:
                    _t1_banners.append(("📈", "#69f0ae", "#071a10",
                        f"景氣擴張但波動偏高：PMI {_pf_t1:.1f} > 50，VIX {_vf_t1:.1f} ≥ 20",
                        "維持持有，衛星設停利 +10%~15%；等待 VIX < 20 再加碼"))
                elif _pf_t1 < 50 and _vf_t1 > 25:
                    _t1_banners.append(("🔴", "#f44336", "#1f0505",
                        f"衰退訊號：PMI {_pf_t1:.1f} < 50 且 VIX {_vf_t1:.1f} > 25",
                        "建議【轉入核心資產】（配息/債券型）→ 股 4 債 6 配置"))
                else:
                    _t1_banners.append(("⚠️", "#ff9800", "#1f1200",
                        f"觀望：PMI {_pf_t1:.1f}，VIX {_vf_t1:.1f}",
                        "維持股 5 債 5 中性配置，等待 PMI 方向確認"))
            if _ue_t1:
                try:
                    _ue_f = float(_ue_t1)
                    if _ue_f > 4.0:
                        _fed_v1  = ind.get("FED_RATE", {}).get("value")
                        _fed_pv1 = ind.get("FED_RATE", {}).get("prev")
                        _rate_cutting = False
                        try:
                            if _fed_v1 and _fed_pv1 and float(_fed_v1) < float(_fed_pv1):
                                _rate_cutting = True
                        except: pass
                        if _rate_cutting:
                            _t1_banners.append(("🏦", "#9c27b0", "#0d0014",
                                f"失業率 {_ue_f:.1f}% > 4% ＋ 降息趨勢確立",
                                "📌 鎖定長年期債券基金（20年期美債等）— 降息→債券價格上漲，鞏固現金流"))
                        else:
                            _t1_banners.append(("🔴", "#f44336", "#1f0505",
                                f"失業率警戒：{_ue_f:.1f}% > 4%（衰退訊號）",
                                "核心資產為主 → 股 4 債 6，等待 PMI 落底確認"))
                except: pass
            if _cpi_t1 and _cpi_pt1:
                try:
                    if float(_cpi_t1) > float(_cpi_pt1) and float(_cpi_t1) > 3.0:
                        _t1_banners.append(("⚠️", "#ff9800", "#1f1200",
                            f"CPI 升溫：{float(_cpi_t1):.1f}% ↑（升息尾聲觀察期）",
                            "衛星資產獲利了結 → 股 5 債 5；等待升息暫停訊號"))
                except: pass
            for _ic, _cc, _bg, _ttl, _act in _t1_banners:
                st.markdown(
                    f"<div style='background:{_bg};border:2px solid {_cc};border-radius:10px;"
                    f"padding:12px 18px;margin:4px 0'>"
                    f"<div style='display:flex;align-items:flex-start;gap:12px'>"
                    f"<span style='font-size:22px;line-height:1'>{_ic}</span>"
                    f"<div><div style='color:{_cc};font-weight:700;font-size:13px'>{_ttl}</div>"
                    f"<div style='color:#ccc;font-size:12px;margin-top:4px'>➤ {_act}</div>"
                    f"</div></div></div>", unsafe_allow_html=True)
            st.caption("📖 指標說明｜PMI（採購經理人指數）：製造業景氣溫度計，>50=擴張，<50=收縮｜VIX（恐慌指數）：市場恐慌程度，<20=平靜，>30=恐慌，>40=極度恐慌")

        # ── ⚠️ 拐點警示 Banner ──────────────────────────────
        trend_arrow  = phase.get("trend_arrow","→")
        trend_label  = phase.get("trend_label","")
        next_p       = phase.get("next_phase_name","")
        alloc_trans  = phase.get("alloc_transition",{})
        cur_phase    = phase.get("phase","")
        cur_score    = phase.get("score",0)

        # 判斷是否接近拐點（trend_arrow 含 ↗ 或 ↘）
        is_inflection = "↗" in trend_arrow or "↘" in trend_arrow

        if is_inflection and next_p:
            # 配置變更說明
            trans_parts = []
            for asset, v in alloc_trans.items():
                diff = v['to'] - v['from']
                if diff > 0:
                    tag = f"<span style='color:#00c853'>↑ {asset} {v['from']}%→{v['to']}%</span>"
                elif diff < 0:
                    tag = f"<span style='color:#f44336'>↓ {asset} {v['from']}%→{v['to']}%</span>"
                else:
                    tag = f"<span style='color:#888'>{asset} {v['from']}% 不變</span>"
                trans_parts.append(tag)
            trans_html = "　｜　".join(trans_parts)

            arrow_color = "#00c853" if "↗" in trend_arrow else "#f44336"
            banner_html = (
                "<div style='background:linear-gradient(135deg,#1a2a1a,#0d1117);"
                "border:2px solid #f0a500;border-radius:12px;padding:16px 20px;margin:8px 0;"
                "box-shadow:0 0 20px rgba(240,165,0,0.3)'>"
                "<div style='display:flex;align-items:center;gap:10px;margin-bottom:10px'>"
                "<span style='font-size:24px'>⚠️</span>"
                f"<span style='font-size:18px;font-weight:900;color:#f0a500'>景氣拐點預警</span>"
                f"<span style='font-size:14px;color:#888'>｜ {cur_phase} → {next_p} 訊號浮現</span>"
                "</div>"
                f"<div style='font-size:13px;margin-bottom:8px;color:#e6edf3'>"
                f"趨勢方向：<b style='color:{arrow_color};font-size:16px'>{trend_arrow}</b>　"
                f"<span style='color:#f0a500'>{trend_label}</span></div>"
                f"<div style='font-size:12px;color:#888;margin-bottom:8px'>若確認轉入「{next_p}」，建議配置調整：</div>"
                f"<div style='font-size:13px'>{trans_html}</div>"
                "</div>"
            )
            st.markdown(banner_html, unsafe_allow_html=True)

        # ── v10.4 景氣象限一句話總結卡 ────────────────────────────
        _phase_actions = {
            "高峰": ("🔴", "#f44336", "#1f0505",
                     "市場過熱，接近轉折",
                     "減碼成長型衛星 → 鎖定高息核心，股5債5中性"),
            "擴張": ("🟢", "#00c853", "#071a0f",
                     "景氣擴張，動能充足",
                     "加碼衛星資產（成長/科技基金），股7債3積極配置"),
            "復甦": ("🔵", "#64b5f6", "#0d1525",
                     "景氣底部回升中",
                     "分批加碼核心配息，等待PMI>50確認後加碼衛星"),
            "衰退": ("🟡", "#ff9800", "#1f1200",
                     "景氣下行，避險優先",
                     "鎖定長年期債券基金，核心配息為主，股4債6"),
        }
        _pa = _phase_actions.get(cur_phase, ("⚪", "#888", "#111", "分析中", "維持中性配置"))
        _pico, _pcol, _pbg, _psum, _pact = _pa
        st.markdown(
            f"<div style='background:{_pbg};border:2px solid {_pcol};"
            f"border-radius:12px;padding:14px 20px;margin:6px 0;"
            f"display:flex;align-items:center;gap:16px'>"
            f"<div style='font-size:28px;line-height:1'>{_pico}</div>"
            f"<div style='flex:1'>"
            f"<div style='display:flex;align-items:baseline;gap:10px;margin-bottom:4px'>"
            f"<span style='font-size:20px;font-weight:900;color:{_pcol}'>{cur_phase}期</span>"
            f"<span style='font-size:13px;color:#888'>（景氣分數：{cur_score}/10）</span>"
            f"<span style='font-size:13px;color:{_pcol};font-weight:600'>{_psum}</span>"
            f"</div>"
            f"<div style='font-size:13px;color:#ccc;background:rgba(255,255,255,0.04);"
            f"border-radius:6px;padding:6px 10px'>➤ {_pact}</div>"
            f"</div></div>",
            unsafe_allow_html=True)

        # ── Z-Score × Slope 二維景氣確認卡（說明書 §3 get_market_phase）
        _mp2d = phase.get("market_phase_2d", {})
        if _mp2d and _mp2d.get("phase2d","") != "未知":
            _p2d      = _mp2d.get("phase2d", "")
            _p2d_col  = _mp2d.get("phase2d_color", "#888")
            _p2d_desc = _mp2d.get("phase2d_desc", "")
            _p2d_conf = _mp2d.get("phase2d_conf", 0)
            # 說明書 §4：四象限燈號標籤（位階 × 動能）
            _QUAD_LABEL = {
                "復甦": ("🔵", "築底 Recovery"),
                "擴張": ("🟢", "繁榮 Boom"),
                "減速": ("🟡", "警戒 Slowdown"),
                "衰退": ("🔴", "衰退 Recession"),
            }
            _p2d_ico_raw, _p2d_label = _QUAD_LABEL.get(_p2d, ("🔍", _p2d))
            _p2d_icon = _p2d_ico_raw
            _p2d_agree = cur_phase in (_p2d,) or \
                         (cur_phase == "擴張" and _p2d == "擴張") or \
                         (cur_phase == "衰退" and _p2d == "衰退")
            _conf_bar_w = _p2d_conf
            _conf_bar_c = "#00c853" if _p2d_conf >= 67 else "#ff9800"
            st.markdown(
                f"<div style='background:#0a1628;border:1px solid {_p2d_col};border-left:4px solid {_p2d_col};"
                f"border-radius:10px;padding:12px 16px;margin:6px 0;"
                f"display:flex;align-items:center;gap:14px'>"
                f"<div style='font-size:22px'>{_p2d_icon}</div>"
                f"<div style='flex:1'>"
                f"<div style='font-size:10px;color:#666;letter-spacing:1px'>Z-Score × 斜率二維確認</div>"
                f"<div style='display:flex;align-items:baseline;gap:8px;margin:2px 0'>"
                f"<span style='font-size:16px;font-weight:900;color:{_p2d_col}'>{_p2d_ico_raw} {_p2d_label}</span>"
                f"<span style='font-size:11px;color:#888'>{_p2d_desc}</span>"
                f"</div>"
                f"<div style='display:flex;align-items:center;gap:6px;margin-top:4px'>"
                f"<span style='font-size:10px;color:#666'>信心度</span>"
                f"<div style='flex:1;background:#161b22;border-radius:3px;height:5px'>"
                f"<div style='background:{_conf_bar_c};width:{_conf_bar_w}%;height:100%;border-radius:3px'></div></div>"
                f"<span style='font-size:10px;color:{_conf_bar_c}'>{_p2d_conf}%</span>"
                f"<span style='font-size:10px;color:{'#00c853' if _p2d_agree else '#ff9800'};margin-left:8px'>"
                f"{'✅ 與加權評分一致' if _p2d_agree else '⚠️ 與加權評分分歧，請留意'}</span>"
                f"</div></div></div>",
                unsafe_allow_html=True)

        _render_macro_dashboard(ind, phase)

        # ── TAA 戰術配置警告（文件建議 §3：連動持倉）─────────────
        _pf_taa     = st.session_state.get("portfolio_funds", [])
        _total_taa  = sum(f.get("invest_twd", 0) or 0 for f in _pf_taa)
        _phase_alloc= phase.get("alloc", {})
        _rec_stock  = _phase_alloc.get("股票", 50)
        _rec_bond   = _phase_alloc.get("債券", 40)
        if _total_taa > 0 and _phase_alloc:
            _sat_taa = sum(f.get("invest_twd", 0) or 0 for f in _pf_taa if not f.get("is_core"))
            _sat_pct = round(_sat_taa / _total_taa * 100, 1)
            _deviation = _sat_pct - _rec_stock
            if abs(_deviation) >= 20:
                if _deviation > 0:
                    st.markdown(
                        f"<div style='background:linear-gradient(135deg,#2a1000,#1f0a00);"
                        f"border:1.5px solid #ff9800;border-radius:10px;"
                        f"padding:12px 16px;margin:8px 0'>"
                        f"<span style='color:#ff9800;font-weight:700'>⚠️ TAA 配置警告</span>"
                        f"<span style='color:#ccc;font-size:13px;margin-left:8px'>"
                        f"衛星（成長/股票型）佔比 <b style='color:#ff9800'>{_sat_pct}%</b>，"
                        f"高於 {cur_phase}期建議股票 <b>{_rec_stock}%</b>（偏高 {abs(_deviation):.0f}%）"
                        f"— 建議逐步減碼衛星，增加投資等級債或現金部位</span></div>",
                        unsafe_allow_html=True)
                else:
                    st.markdown(
                        f"<div style='background:linear-gradient(135deg,#001a0a,#0a1f00);"
                        f"border:1.5px solid #69f0ae;border-radius:10px;"
                        f"padding:12px 16px;margin:8px 0'>"
                        f"<span style='color:#69f0ae;font-weight:700'>💡 TAA 配置提示</span>"
                        f"<span style='color:#ccc;font-size:13px;margin-left:8px'>"
                        f"衛星（成長/股票型）佔比 <b style='color:#69f0ae'>{_sat_pct}%</b>，"
                        f"低於 {cur_phase}期建議股票 <b>{_rec_stock}%</b>（低於 {abs(_deviation):.0f}%）"
                        f"— 景氣仍在擴張，可適度加碼衛星成長型資產</span></div>",
                        unsafe_allow_html=True)

        # ── 台灣市場水溫計（v15 TPI）────────────────────────────
    with st.expander("🇹🇼 台灣市場水溫計（TPI 三因子轉折指標）", expanded=False):
        st.caption("TPI = Z(市場寬度) × 0.4 + Z(外資淨買) × 0.3 + Z(M1B/M2) × 0.3 | 資料來源：證交所 OpenAPI")
        st.caption("📖 術語說明｜M1B = 活期存款+活期儲蓄（資金活化指標，M1B/M2↑代表市場偏多）｜Z-score = 標準化分數（>0 = 高於均值 = 偏樂觀）｜外資淨買 = 外國法人買超金額（籌碼指標）")
        if st.button("📡 取得台股即時水溫", key="btn_tw_tpi"):
            with st.spinner("連線 TWSE + FinMind + CBC (M1B/M2)..."):
                try:
                    from macro_engine import fetch_tw_market_tpi
                    _tpi_data = fetch_tw_market_tpi()
                    st.session_state["tw_tpi"] = _tpi_data
                except Exception as _te:
                    st.error(f"❌ {_te}")
                    try:
                        _write_error_ledger(_te, "TW TPI fetch", GEMINI_KEY)
                    except Exception:
                        pass

        _tpi = st.session_state.get("tw_tpi", {})
        if _tpi and _tpi.get("tpi") is not None:
            _tpi_v   = _tpi["tpi"]
            _tpi_c   = _tpi.get("color","#888")
            _tpi_lbl = _tpi.get("water_label","?")
            _tpi_adv = _tpi.get("advice","")

            # Water thermometer visual
            _therm_pct = max(0, min(100, (_tpi_v + 3) / 6 * 100))
            st.markdown(
                f"<div style='background:#0d1117;border-radius:12px;padding:16px 20px'>"
                f"<div style='font-size:32px;text-align:center;margin-bottom:8px'>{_tpi_lbl}</div>"
                f"<div style='height:20px;background:#1a1f2e;border-radius:10px;overflow:hidden;margin:8px 0'>"
                f"<div style='height:100%;width:{_therm_pct:.0f}%;border-radius:10px;"
                f"background:linear-gradient(to right,#9c27b0,#64b5f6,#00c853,#ff9800,#f44336)'></div></div>"
                f"<div style='display:flex;justify-content:space-between;font-size:10px;color:#555'>"
                f"<span>🥶 冰點(-3)</span><span>⚖️ 中性(0)</span><span>🥵 沸點(+3)</span></div>"
                f"<div style='text-align:center;margin-top:10px'>"
                f"<span style='color:{_tpi_c};font-size:24px;font-weight:900'>TPI = {_tpi_v:+.2f}</span></div>"
                f"<div style='color:#ccc;font-size:13px;margin-top:8px;text-align:center'>{_tpi_adv}</div>"
                f"</div>",
                unsafe_allow_html=True)

            # Factor breakdown
            _fb_cols = st.columns(3)
            # Factor A: 市場寬度
            _fv_b = _tpi.get("z_breadth")
            _fv_b_c = "#00c853" if (_fv_b or 0)>0 else "#f44336"
            _fv_b_sub = f"漲跌比:{_tpi.get('breadth',0):+.1f}%" if _tpi.get("breadth") is not None else ""
            _fb_cols[0].markdown(
                f"<div style='background:#1a1f2e;border-radius:8px;padding:8px;text-align:center'>"
                f"<div style='font-size:9px;color:#888'>市場寬度 Z(Breadth)</div>"
                f"<div style='font-size:18px;font-weight:700;color:{_fv_b_c}'>"
                f"{'N/A' if _fv_b is None else f'{_fv_b:+.2f}'}</div>"
                f"<div style='font-size:10px;color:#666'>{_fv_b_sub}</div>"
                f"</div>", unsafe_allow_html=True)
            # Factor B: 外資
            _fv_f = _tpi.get("z_fii")
            _fv_f_c = "#00c853" if (_fv_f or 0)>0 else "#f44336"
            _fii_net = _tpi.get("fii_net")
            _fii_sub = f"淨買:{_fii_net/1e8:+.0f}億" if _fii_net is not None else ""
            _fb_cols[1].markdown(
                f"<div style='background:#1a1f2e;border-radius:8px;padding:8px;text-align:center'>"
                f"<div style='font-size:9px;color:#888'>外資籌碼 Z(FII)</div>"
                f"<div style='font-size:18px;font-weight:700;color:{_fv_f_c}'>"
                f"{'N/A' if _fv_f is None else f'{_fv_f:+.2f}'}</div>"
                f"<div style='font-size:10px;color:#666'>{_fii_sub}</div>"
                f"</div>", unsafe_allow_html=True)
            # Factor C: M1B/M2（含三層備援狀態顯示）
            _fv_m      = _tpi.get("z_m1b_m2")
            _m1b_yoy   = _tpi.get("m1b_yoy")
            _m2_yoy    = _tpi.get("m2_yoy")
            _gap       = _tpi.get("m1b_m2_gap")
            _is_proxy  = _tpi.get("m1b_is_proxy", False)
            _fv_m_c    = ("#00c853" if (_fv_m or 0)>0 else
                         ("#f44336" if (_fv_m or 0)<0 else "#888"))
            if _m1b_yoy is not None:
                _cross_icon = "🟢 黃金" if (_gap or 0)>0 else "🔴 死亡"
                _proxy_tag  = " 📊估算" if _is_proxy else ""
                _m_sub = (f"{_cross_icon}交叉{_proxy_tag}"
                          f" M1B:{_m1b_yoy:.1f}% M2:{_m2_yoy:.1f}%")
                _m_val = f"{_fv_m:+.2f}" if _fv_m is not None else "0.00"
                if _is_proxy: _fv_m_c = "#ff9800"
            else:
                _m_sub  = "⚠️ M1B/M2 暫無法取得"
                _m_val  = "N/A"
                _fv_m_c = "#555"
            _fb_cols[2].markdown(
                f"<div style='background:#1a1f2e;border-radius:8px;padding:8px;text-align:center'>"
                f"<div style='font-size:9px;color:#888'>M1B/M2 Z(M1B_M2)</div>"
                f"<div style='font-size:16px;font-weight:700;color:{_fv_m_c}'>{_m_val}</div>"
                f"<div style='font-size:9px;color:#666;margin-top:2px;line-height:1.4'>{_m_sub}</div>"
                f"</div>",
                unsafe_allow_html=True)
        elif not _tpi:
            st.info("💡 點擊「取得台股即時水溫」載入指標（需連線至證交所 OpenAPI）")

    # ── 國際財經新聞 ─────────────────────────────────────
        st.divider()
        st.markdown("### 📰 國際財經新聞（影響股市 ／ 匯率 ／ 債券）")
        _ncol1, _ncol2 = st.columns([4,1])
        with _ncol2:
            if st.button("🔄 更新新聞", key="btn_news_reload", use_container_width=True):
                if "market_news" in st.session_state: del st.session_state["market_news"]
                st.rerun()
        if "market_news" not in st.session_state:
            with st.spinner("抓取國際財經新聞..."):
                from fund_fetcher import fetch_market_news
                st.session_state["market_news"] = fetch_market_news(max_per_feed=5)
        _news_list = st.session_state.get("market_news", [])
        if _news_list:
            def _render_news_col(nws, tag_color, fallback):
                items = nws[:4] if nws else fallback[:4]
                for _n in items:
                    _sm = _n["summary"][:180] + ("..." if len(_n["summary"])>180 else "")
                    _a  = f"<a href='{_n['url']}' target='_blank' style='color:#555;font-size:9px'>↗原文</a>" if _n["url"] else ""
                    st.markdown(
                        f"<div style='background:#0d1117;border-left:3px solid {tag_color};"
                        f"padding:7px 11px;margin:3px 0;border-radius:0 6px 6px 0'>"
                        f"<div style='font-size:11px;font-weight:700;color:#e6edf3'>{_n['title'][:80]}</div>"
                        f"<div style='font-size:9px;color:#666'>{_n['source']} · {_n['published'][:16]} {_a}</div>"
                        f"<div style='font-size:10px;color:#999'>{_sm}</div></div>",
                        unsafe_allow_html=True)
            _stk = [n for n in _news_list if any(k in (n["title"]+n["summary"]).lower()
                    for k in ["stock","equity","nasdaq","s&p","shares","earnings","market","ipo"])]
            _fxn = [n for n in _news_list if any(k in (n["title"]+n["summary"]).lower()
                    for k in ["dollar","currency","yen","euro","forex","usd","eur","jpy","exchange"])]
            _bnd = [n for n in _news_list if any(k in (n["title"]+n["summary"]).lower()
                    for k in ["bond","treasury","yield","fed","interest rate","inflation","cpi","ecb","boj","pboc"])]
            _nc1, _nc2, _nc3 = st.columns(3)
            with _nc1:
                st.markdown("**📈 股市相關**")
                _render_news_col(_stk, "#00c853", _news_list)
            with _nc2:
                st.markdown("**💱 匯率相關**")
                _render_news_col(_fxn, "#ff9800", _news_list)
            with _nc3:
                st.markdown("**🏦 債券 / 利率**")
                _render_news_col(_bnd, "#2196f3", _news_list)
            # Build news text for AI prompt
            _news_ai_lines = ["【國際財經新聞（請結合以下新聞與總經指標進行 AI 研判）】"]
            for _n in _news_list[:15]:
                _news_ai_lines.append("[" + _n["source"] + "] " + _n["title"])
                if _n["summary"]: _news_ai_lines.append("  摘要：" + _n["summary"][:200])
            st.session_state["news_ai_text"] = chr(10).join(_news_ai_lines)
        else:
            st.info("⚠️ RSS 來源暫時無法存取，AI 分析仍可正常執行（無新聞數據）")
            st.session_state["news_ai_text"] = ""




with tab3:
    st.markdown("## 🔍 個別基金深度分析")
    st.caption("貼上任何 MoneyDJ 基金網址，自動抓取淨值、持股、配息並套用 MK 方法論分析")

    # ── v10.4 DXY 美元微笑曲線提示 ──────────────────────────
    _dxy_ind = st.session_state.get("indicators", {}).get("DXY", {})
    if _dxy_ind:
        try:
            _dxy_vf = float(_dxy_ind.get("value", 0))
            _dxy_cf = float(_dxy_ind.get("prev", 0))   # prev = 月漲跌%
            if _dxy_cf >= 1.5:
                st.markdown(
                    f"<div style='background:#1a0d00;border:1px solid #ff9800;"
                    f"border-radius:8px;padding:10px 16px;margin:4px 0;"
                    f"display:flex;align-items:center;gap:10px'>"
                    f"<span style='font-size:20px'>💵</span>"
                    f"<div><span style='color:#ff9800;font-weight:700'>美元走強 — 美元資產優先</span>"
                    f"<span style='color:#aaa;font-size:12px;margin-left:8px'>"
                    f"DXY {_dxy_vf:.1f}（月漲 +{_dxy_cf:.1f}%）</span><br>"
                    f"<span style='color:#888;font-size:12px'>強美元→新興市場承壓。建議優先分析：</span>"
                    f"<span style='color:#ffd54f;font-size:12px'>"
                    f" 🇺🇸 美股基金 ｜ 💼 美元投資等級債 ｜ 🏢 美元公司債（USD IG/HY）</span>"
                    f"</div></div>",
                    unsafe_allow_html=True)
            elif _dxy_cf <= -1.5:
                st.markdown(
                    f"<div style='background:#071a0f;border:1px solid #00c853;"
                    f"border-radius:8px;padding:10px 16px;margin:4px 0;"
                    f"display:flex;align-items:center;gap:10px'>"
                    f"<span style='font-size:20px'>🌏</span>"
                    f"<div><span style='color:#00c853;font-weight:700'>美元走弱 — 新興市場利多</span>"
                    f"<span style='color:#aaa;font-size:12px;margin-left:8px'>"
                    f"DXY {_dxy_vf:.1f}（月跌 {_dxy_cf:.1f}%）</span><br>"
                    f"<span style='color:#888;font-size:12px'>弱美元利好新興市場與原物料。建議關注：</span>"
                    f"<span style='color:#69f0ae;font-size:12px'>"
                    f" 🌏 新興亞洲/東南亞基金 ｜ 🛢️ 原物料/能源 ｜ 🇧🇷 新興市場債</span>"
                    f"</div></div>",
                    unsafe_allow_html=True)
        except: pass

    # ── 初始化 session state ──────────────────────────────
    for _k in ["selected_fund","fund_data","fund_struct","tdcc_results","mj_fund_data"]:
        if _k not in st.session_state: st.session_state[_k] = None if "results" not in _k else []

    # ══════════════════════════════════════════════════════
    # 主要入口：MoneyDJ URL / 代碼
    # ══════════════════════════════════════════════════════
    st.markdown("### 🔗 輸入 MoneyDJ 網址或基金代碼")
    st.caption("範例：`https://www.moneydj.com/funddj/ya/yp010001.djhtm?a=tlzf9` 或直接輸入 `tlzf9`")
    col_url, col_go = st.columns([5, 1])
    with col_url:
        mj_url_input = st.text_input(
            "MoneyDJ URL", placeholder="貼上 MoneyDJ 網址 或 輸入代碼（tlzf9 / LU0095940420）",
            label_visibility="collapsed", key="mj_url_input")
    with col_go:
        do_load = st.button("🚀 分析", type="primary", use_container_width=True, key="btn_mj_load")

    if do_load and mj_url_input.strip():
        with st.spinner("📡 正在抓取 MoneyDJ 資料（基本資料 + 持股 + 績效評比）..."):
            from fund_fetcher import fetch_fund_from_moneydj_url, calc_metrics
            fd_raw = fetch_fund_from_moneydj_url(mj_url_input.strip())
            st.session_state.mj_fund_data = fd_raw
            # v13.5: 依 status 三態顯示，不再只看 error 欄位
            from fund_fetcher import normalize_result_state, classify_fetch_status
            fd_raw = normalize_result_state(fd_raw)   # 確保狀態正確
            _fd_status = fd_raw.get("status", classify_fetch_status(fd_raw))

            st.session_state.fund_data = {
                "full_key":  fd_raw.get("full_key", ""),
                "fund_name": fd_raw.get("fund_name", ""),
                "portal":    "www",
                "series":    fd_raw.get("series"),
                "dividends": fd_raw.get("dividends", []),
                "metrics":   fd_raw.get("metrics", {}),
                "error":     fd_raw.get("error"),
                "warning":   fd_raw.get("warning"),
                "status":    _fd_status,
                "moneydj_raw": fd_raw,
            }
            n_nav = len(fd_raw["series"]) if fd_raw.get("series") is not None else 0
            n_div = len(fd_raw.get("dividends", []))
            _fname = fd_raw.get("fund_name") or fd_raw.get("full_key", "")

            if _fd_status == "complete":
                st.success(f"✅ 載入完整：{_fname}（{n_nav} 筆淨值，{n_div} 筆配息）")
            elif _fd_status == "partial":
                _warn = fd_raw.get("warning") or f"部分資料（{n_nav} 筆淨值，{n_div} 筆配息）"
                st.warning(f"⚠️ {_fname} — {_warn}")
            else:
                _err = fd_raw.get("error", "所有來源均無法取得資料")
                st.error(f"❌ {_err}")
                # v6.8: 顯示 source_trace 幫助診斷
                _trace = fd_raw.get("source_trace", [])
                if _trace:
                    with st.expander("🔍 來源追蹤（診斷用）", expanded=False):
                        for _t in _trace:
                            _icon = "✅" if _t.get("success") else "❌"
                            _te = f" ({_t['error'][:50]})" if _t.get("error") else ""
                            _cnt = f" {_t['nav_count']}筆" if _t.get("nav_count") else ""
                            st.markdown(f"- {_icon} `{_t.get('source','?')}`{_cnt}{_te}")

            # ── 資料完整性診斷條（個股）────────────────────────────────
            _perf_d  = fd_raw.get("perf", {}) or {}
            _risk_d  = fd_raw.get("risk_metrics", {}) or {}
            _hold_d  = fd_raw.get("holdings", {}) or {}
            _mj_dy_d = fd_raw.get("moneydj_div_yield")
            _checks = [
                ("淨值history", n_nav >= 30, f"{n_nav}筆"),
                ("配息記錄",    n_div >= 1,  f"{n_div}筆"),
                ("wb01含息報酬", bool(_perf_d.get("1Y")), "已取得" if _perf_d.get("1Y") else "缺失"),
                ("wb05配息率",  _mj_dy_d is not None, f"{_mj_dy_d:.2f}%" if _mj_dy_d else "缺失"),
                ("wb07標準差",  bool((_risk_d.get("risk_table") or {})), "已取得" if (_risk_d.get("risk_table") or {}) else "缺失"),
                ("產業配置",    bool(_hold_d.get("sector_alloc")), f"{len(_hold_d.get('sector_alloc',[]))}項" if _hold_d.get("sector_alloc") else "缺失"),
                ("前10大持股",  bool(_hold_d.get("top_holdings")), f"{len(_hold_d.get('top_holdings',[]))}檔" if _hold_d.get("top_holdings") else "缺失"),
                ("基金名稱",    bool(fd_raw.get("fund_name")), fd_raw.get("fund_name","?")[:12]),
            ]
            _badges = "".join(
                f"<span style='background:{'#0a2218' if ok else '#2a0e0e'};"
                f"color:{'#00c853' if ok else '#f44336'};"
                f"border-radius:4px;padding:2px 7px;margin:2px;font-size:10px;display:inline-block'>"
                f"{'✅' if ok else '❌'} {lbl}: {val}</span>"
                for lbl, ok, val in _checks)
            st.markdown(
                f"<div style='background:#0d1117;border:1px solid #30363d;"
                f"border-radius:8px;padding:8px 10px;margin-top:6px'>"
                f"<div style='font-size:11px;color:#888;margin-bottom:4px'>📋 資料完整性</div>"
                f"{_badges}</div>",
                unsafe_allow_html=True)

            # 診斷小結：缺什麼、為何缺、建議
            _missing = [lbl for lbl, ok, val in _checks if not ok and val != "尚未載入"]
            if _missing:
                _miss_reasons = {
                    "wb01含息報酬率": "MoneyDJ wb01 未抓取到（IP 限制，請改用完整網址）",
                    "wb05配息率":     "MoneyDJ wb05 未抓取到（IP 限制或基金無配息）",
                    "wb07標準差":     "MoneyDJ wb07 風險表未載入",
                    "產業配置":       "MoneyDJ 持股頁未載入（IP 限制）",
                    "前10大持股":     "MoneyDJ 持股頁未載入（IP 限制）",
                    "淨值history":    "淨值歷史資料不足 30 筆",
                    "配息記錄":       "無配息記錄（可能為累積型基金）",
                }
                _sugg = []
                for m in _missing[:3]:
                    reason = _miss_reasons.get(m, "資料暫時無法取得")
                    _sugg.append(f"• **{m}**：{reason}")
                if _sugg:
                    st.info("💡 **資料缺漏說明**：\n" + "\n".join(_sugg) +
                            "\n\n👉 如為 IP 限制，請在 Colab 重新連線運行環境（Runtime → Disconnect and delete runtime）以取得新 IP。")


    # ══════════════════════════════════════════════════════════════════
    # v13.7 降級診斷模式：當自動抓取失敗時，手動輸入 4 個數字也能做健康診斷
    # ══════════════════════════════════════════════════════════════════
    with st.expander("📝 手動輸入診斷（抓不到資料時使用）", expanded=False):
        st.markdown(
            "<div style='background:#0d1117;border:1px solid #30363d;border-radius:8px;"
            "padding:12px;margin-bottom:8px;font-size:12px;color:#888'>"
            "💡 當 MoneyDJ 封鎖伺服器 IP 時，手動輸入以下 4 個數字仍可完成"
            " <b style='color:#e0e0e0'>含息報酬率 / 配息年化率 / 吃本金判斷</b><br>"
            "這些數字可從：MoneyDJ 網頁 / 安聯投信官網 / 基金月報 / 對帳單 取得"
            "</div>", unsafe_allow_html=True)

        _m_name = st.text_input("基金名稱（選填）", key="manual_fund_name",
                                placeholder="例：安聯AI收益成長多重資產基金 B月配 TWD")
        _m_c1, _m_c2 = st.columns(2)
        with _m_c1:
            _m_nav_now  = st.number_input("目前淨值", min_value=0.01, value=10.00,
                                          step=0.01, key="manual_nav_now",
                                          help="從 MoneyDJ 或基金公司網站取得今天淨值")
            _m_nav_1y   = st.number_input("一年前淨值", min_value=0.01, value=10.00,
                                          step=0.01, key="manual_nav_1y",
                                          help="一年前（約365天前）的淨值，用來算含息報酬率")
        with _m_c2:
            _m_div_unit = st.number_input("最近一期每單位配息", min_value=0.0, value=0.05,
                                          step=0.001, format="%.4f", key="manual_div",
                                          help="最近一次配息金額（從 MoneyDJ 配息頁或基金月報取得）")
            _m_freq = st.selectbox("配息頻率", [("月配（12次/年）",12),("季配（4次/年）",4),
                                                ("半年配（2次/年）",2),("年配（1次/年）",1)],
                                   format_func=lambda x: x[0], key="manual_freq")
        _m_freq_n = _m_freq[1]

        if st.button("🔬 手動診斷", key="btn_manual_diag", type="primary"):
            from fund_fetcher import calc_health_from_manual
            _mh = calc_health_from_manual(
                nav_current  = _m_nav_now,
                nav_1y_ago   = _m_nav_1y,
                div_per_unit = _m_div_unit,
                div_freq     = _m_freq_n,
                fund_name    = _m_name or "手動輸入基金",
            )
            if _mh.get("error"):
                st.error(_mh["error"])
            else:
                # 顯示診斷結果
                _hc = _mh["health_color"]
                st.markdown(
                    f"<div style='background:#0d1117;border:2px solid {_hc};"
                    f"border-radius:12px;padding:16px;margin-top:8px'>"
                    f"<div style='font-size:18px;font-weight:700;color:{_hc};margin-bottom:12px'>"
                    f"{_mh['health']} — {_m_name or '手動輸入基金'}</div>"
                    f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-bottom:12px'>"
                    f"<div style='background:#161b22;border-radius:8px;padding:10px;text-align:center'>"
                    f"<div style='color:#888;font-size:11px'>📊 含息報酬率(1Y)</div>"
                    f"<div style='color:{'#00c853' if _mh['total_return_pct']>=0 else '#f44336'};"
                    f"font-size:22px;font-weight:900'>{_mh['total_return_pct']:+.2f}%</div></div>"
                    f"<div style='background:#161b22;border-radius:8px;padding:10px;text-align:center'>"
                    f"<div style='color:#888;font-size:11px'>📌 配息年化率</div>"
                    f"<div style='color:#ff9800;font-size:22px;font-weight:900'>{_mh['div_yield_pct']:.2f}%</div></div>"
                    f"<div style='background:#161b22;border-radius:8px;padding:10px;text-align:center'>"
                    f"<div style='color:#888;font-size:11px'>🔬 真實收益</div>"
                    f"<div style='color:{_hc};font-size:22px;font-weight:900'>{_mh['real_return_pct']:+.2f}%</div></div>"
                    f"</div>"
                    f"<div style='background:#1a1a2e;border-radius:6px;padding:10px;font-size:12px;color:#b0bec5'>"
                    f"<b style='color:#e0e0e0'>計算說明：</b><br>"
                    f"• 淨值漲跌：{_mh['nav_change_pct']:+.2f}%（{_mh['nav_1y_ago']} → {_mh['nav_current']}）<br>"
                    f"• 年配息：{_mh['annual_div']:.4f}（{_mh['div_per_unit']:.4f} × {_m_freq_n}次）<br>"
                    f"• 配息年化率：{_mh['annual_div']:.4f} ÷ {_mh['nav_current']} = {_mh['div_yield_pct']:.2f}%<br>"
                    f"• 含息報酬 = 淨值漲跌({_mh['nav_change_pct']:+.2f}%) + 配息率({_mh['div_yield_pct']:.2f}%) = {_mh['total_return_pct']:+.2f}%"
                    f"</div>"
                    f"<div style='margin-top:10px;padding:8px;background:{'#2a0a0a' if _mh['eating_principal'] else '#0a1a0a'};"
                    f"border-radius:6px;font-size:12px;color:{_hc}'>{_mh['advice']}</div>"
                    f"</div>", unsafe_allow_html=True)

                # 儲存到 session state 供後續 AI 分析使用
                st.session_state["manual_health_result"] = _mh
                st.session_state["manual_health_code"] = _m_name or "MANUAL"

        # ── 基金基本資訊卡片 ─────────────────────────────────
    mj_fd = st.session_state.get("mj_fund_data")
    if mj_fd and mj_fd.get("fund_name"):
        RISK_COLOR = {"RR1":"#69f0ae","RR2":"#64b5f6","RR3":"#ff9800",
                      "RR4":"#ff7043","RR5":"#f44336"}
        rr      = mj_fd.get("risk_level","").replace(" ","")
        rr_c    = RISK_COLOR.get(rr, "#888")
        tags = []
        if rr: tags.append(f"<span style='background:#1a2332;color:{rr_c};padding:3px 10px;border-radius:20px;font-size:12px'>⚠️ {rr}</span>")
        if mj_fd.get("fund_type"):    tags.append(f"<span style='background:#1a2332;color:#9c27b0;padding:3px 10px;border-radius:20px;font-size:12px'>🏷 {mj_fd['fund_type']}</span>")
        if mj_fd.get("investment_target"): tags.append(f"<span style='background:#1a2332;color:#64b5f6;padding:3px 10px;border-radius:20px;font-size:12px'>🎯 {mj_fd['investment_target']}</span>")
        if mj_fd.get("fund_region"):  tags.append(f"<span style='background:#1a2332;color:#4caf50;padding:3px 10px;border-radius:20px;font-size:12px'>🌏 {mj_fd['fund_region']}</span>")
        if mj_fd.get("dividend_freq"):tags.append(f"<span style='background:#1a2332;color:#ff9800;padding:3px 10px;border-radius:20px;font-size:12px'>💰 {mj_fd['dividend_freq']}</span>")
        if mj_fd.get("currency"):     tags.append(f"<span style='background:#1a2332;color:#888;padding:3px 10px;border-radius:20px;font-size:12px'>{mj_fd['currency']}</span>")
        if mj_fd.get("umbrella_fund")=="Y": tags.append("<span style='background:#1a2332;color:#888;padding:3px 10px;border-radius:20px;font-size:12px'>☂️ 傘型</span>")
        if mj_fd.get("fund_scale"):   tags.append(f"<span style='background:#1a2332;color:#888;padding:3px 10px;border-radius:20px;font-size:12px'>規模：{mj_fd['fund_scale'][:20]}</span>")

        # 年度高低點（buy point reference）
        yh = mj_fd.get("year_high_nav") or (mj_fd.get("metrics") or {}).get("year_high_nav")
        yl = mj_fd.get("year_low_nav")  or (mj_fd.get("metrics") or {}).get("year_low_nav")
        hl_html = ""
        if yh and yl:
            hl_html = (
                "<div style='display:flex;gap:16px;margin-top:8px;padding-top:8px;"
                "border-top:1px solid #21262d'>"
                f"<span style='color:#888;font-size:11px'>📅 年最高：<b style='color:#f44336'>{yh}</b></span>"
                f"<span style='color:#888;font-size:11px'>年最低：<b style='color:#00c853'>{yl}</b></span>"
                f"<span style='color:#888;font-size:11px'>年內σ≈<b style='color:#ff9800'>{round((yh-yl)/3,4)}</b></span>"
                "</div>"
            )

        card_html = (
            "<div style='background:#0d1117;border:1px solid #30363d;border-radius:12px;padding:16px;margin:8px 0'>"
            f"<div style='font-size:16px;font-weight:800;color:#e6edf3;margin-bottom:10px'>{mj_fd['fund_name']}</div>"
            f"<div style='display:flex;gap:8px;flex-wrap:wrap'>" + "".join(tags) + "</div>"
            + hl_html +
            "</div>"
        )
        st.markdown(card_html, unsafe_allow_html=True)

        # ── MK 景氣訊號 ──────────────────────────────────
        if st.session_state.macro_done:
            phase_info = st.session_state.phase_info
            sig = mk_fund_signal(
                {"基金名稱": mj_fd.get("fund_name",""), "基金種類": mj_fd.get("category","")},
                phase_info["phase"], phase_info["score"])
            # v10.7 Debug 模式
            if st.session_state.get("debug_mode"):
                with st.expander("🔧 Debug: 基金爬蟲原始資料 (mj_fd)"):
                    st.json({k: str(v)[:300] for k,v in mj_fd.items()})
            sig_html = (
                "<div style='margin:6px 0;padding:10px 16px;border-radius:8px;"
                "background:#0d1117;border:1px solid #30363d;text-align:center'>"
                "<div style='color:#888;font-size:10px;margin-bottom:6px'>📍 MK 景氣訊號</div>"
                f"<span style='{sig['sig_style']};padding:4px 14px;border-radius:20px;"
                f"font-size:15px;font-weight:700;display:inline-block'>{sig['label']}</span>"
                f"<div style='color:#8b949e;font-size:11px;margin-top:6px'>{sig['reason']}</div>"
                "</div>"
            )
            st.markdown(sig_html, unsafe_allow_html=True)

    st.divider()

    # ── 次要入口：關鍵字搜尋 ───────────────────────────────
    with st.expander("🔍 關鍵字搜尋境外基金（TDCC / FundClear）", expanded=False):
        col_kw, col_btn = st.columns([4, 1])
        with col_kw:
            keyword = st.text_input("基金關鍵字", placeholder="安聯、收益成長、摩根、聯博...",
                label_visibility="collapsed", key="fund_keyword")
        with col_btn:
            do_search = st.button("🔍 搜尋", type="primary", use_container_width=True, key="btn_search")
        if do_search and keyword.strip():
            with st.spinner(f"搜尋「{keyword}」中..."):
                results = tdcc_search_fund(keyword.strip())
                st.session_state.tdcc_results = results
                if not results:
                    st.warning("⚠️ 查無結果，請直接使用上方 MoneyDJ 網址輸入")
                else:
                    st.success(f"✅ 找到 {len(results)} 檔基金")
        results = st.session_state.get("tdcc_results", [])
        if results:
            options = {f"{r.get('基金名稱','')} | {r.get('基金代碼','')}": r for r in results}
            sel_label = st.selectbox(f"選擇基金（{len(results)} 筆）", list(options.keys()), key="tdcc_select")
            sel_fund = options[sel_label]
            fc = sel_fund.get("基金代碼","")
            st.info(f"💡 代碼：**{fc}** → 在上方輸入：`https://www.moneydj.com/funddj/ya/yp010001.djhtm?a={fc.lower()}`")

    # ── 分析結果：子 Tab ──────────────────────────────────
    fd = st.session_state.fund_data
    st.session_state['current_fund'] = fd  # 供底部 AI 讀取
    if fd:
        sub1, sub2 = st.tabs(["📈 MK 買點分析", "🏗️ 持股結構"])
        with sub1:
            # ── v16.0 T2: 顯示總經風險狀態 banner（Tab3 聯動 Tab1）──
            _t3_ind = st.session_state.get("indicators", {})
            _t3_vix = (_t3_ind.get("VIX") or {}).get("value")
            _t3_spr = (_t3_ind.get("YIELD_10Y2Y") or {}).get("value")
            if _t3_vix is not None or _t3_spr is not None:
                _t3_risk = 0
                _t3_msgs = []
                if _t3_vix and _t3_vix > 30:
                    _t3_risk = 2; _t3_msgs.append(f"VIX={_t3_vix:.1f}>30 🔴")
                elif _t3_vix and _t3_vix > 22:
                    _t3_risk = max(_t3_risk,1); _t3_msgs.append(f"VIX={_t3_vix:.1f}偏高 🟡")
                if _t3_spr is not None and _t3_spr < -0.3:
                    _t3_risk = 2; _t3_msgs.append(f"利差{_t3_spr:.2f}%深度倒掛 🔴")
                elif _t3_spr is not None and _t3_spr < 0:
                    _t3_risk = max(_t3_risk,1); _t3_msgs.append(f"利差{_t3_spr:.2f}%倒掛 🟡")
                if _t3_risk == 2:
                    st.error(
                        f"🚨 **總經高風險環境** | {'　'.join(_t3_msgs)}\n\n"
                        f"建議提高投資等級債券基金水位，嚴禁重壓中小型成長基金")
                elif _t3_risk == 1:
                    st.warning(f"⚠️ 總經溫度偏高 | {'　'.join(_t3_msgs)}　→ 衛星部位設停利")

            phase_info_s = st.session_state.phase_info if st.session_state.macro_done else None
            _render_fund_analysis(fd, phase_info_s)

            # ── 以息養股健康診斷儀表板 ─────────────────────────
            st.divider()
            st.markdown("### 💎 以息養股健康診斷")
            _m   = fd.get("metrics", {})
            _mj  = fd.get("moneydj_raw", {})
            _rm  = _mj.get("risk_metrics", {})
            _rt  = _rm.get("risk_table", {})
            # v10.1: 優先使用 MoneyDJ wb05「年化配息率%」
            _mj_dy = _mj.get("moneydj_div_yield")
            try: _mj_dy = float(_mj_dy) if _mj_dy is not None else None
            except (ValueError, TypeError): _mj_dy = None
            _adr = _mj_dy if (_mj_dy and _mj_dy > 0) else (_m.get("annual_div_rate", 0) or 0)
            try: _adr = float(_adr)
            except (ValueError, TypeError): _adr = 0.0
            _adr_src = "MoneyDJ wb05" if (_mj_dy and _mj_dy > 0) else "估算"
            _mdr = _m.get("monthly_div", 0)
            _nav = _m.get("nav", 0)
            _ret1y  = _m.get("ret_1y")
            # ── 修正：優先從 risk_metrics.risk_table 取 Sharpe / 標準差 ──
            _std1y = (_rt.get("一年",{}).get("標準差")
                      or _m.get("risk_table",{}).get("一年",{}).get("標準差")
                      or _m.get("std_1y", 0))
            _shp1y = (_rt.get("一年",{}).get("Sharpe")
                      or _m.get("risk_table",{}).get("一年",{}).get("Sharpe")
                      or _m.get("sharpe"))
            _maxdd  = _m.get("max_drawdown", 0) or 0
            _div_stb = (_m.get("div_stability") or {}).get("label","N/A")
            _div_stb_c = (_m.get("div_stability") or {}).get("color","#888")
            _currency = _mj.get("currency","USD")
            _mgmt_fee_raw = _mj.get("mgmt_fee","")
            _cat     = _mj.get("investment_target","") or _mj.get("category","")

            # ── 含息總報酬 vs 配息率（單位統一為 %）──
            # v10.1: 優先使用 MoneyDJ wb01「一年報酬率」= 真實含息報酬（已考慮配息）
            _wb01_1y = _mj.get("perf", {}).get("1Y") if isinstance(_mj.get("perf"), dict) else None
            if _wb01_1y is not None:
                _total_ret   = _wb01_1y
                _has_ret1y   = True
                _nav_chg     = None   # wb01 已含，不分解
                _nav_chg_safe = 0.0
                _total_src   = "MoneyDJ wb01（含息實績）"
            else:
                # 備援：淨值漲跌% + 配息率估算
                _nav_chg = float(_ret1y) if isinstance(_ret1y, (int, float)) else None
                _has_ret1y = _nav_chg is not None
                _nav_chg_safe = _nav_chg if _nav_chg is not None else 0.0
                _total_ret = _nav_chg_safe + _adr
                _total_src = "估算（淨值漲跌+配息率）"
            # 吃本金判斷：含息總報酬 < 配息率 → 淨值下滑超過配息補償
            _eat_principal = _has_ret1y and (_total_ret < _adr) and _adr > 0

            # ─── 吃本金大警示 ───
            if _eat_principal and _adr > 0:
                # v16.0 T4b: 計算本金侵蝕率與長期複利效果
                _erosion_pct = round(_adr - _total_ret, 2)  # 每年侵蝕率
                # 複利計算：持有N年後本金剩餘比例 = (1 - erosion/100)^N
                _remain_10yr = round(max(0, (1 - _erosion_pct / 100) ** 10 * 100), 1)
                _remain_5yr  = round(max(0, (1 - _erosion_pct / 100) ** 5  * 100), 1)
                st.error(
                    f"⚠️ **MK 避雷警示：疑似吃本金！每年侵蝕 ~{_erosion_pct:.2f}%** \n\n"
                    f"含息總報酬 {_total_ret:.2f}% < 配息率 {_adr:.2f}%，"
                    f"差距 {_erosion_pct:.2f}% 即為淨值損耗速度。\n"
                    f"📉 複利侵蝕估算：5年後本金剩 **{_remain_5yr:.1f}%**，"
                    f"10年後剩 **{_remain_10yr:.1f}%**（以年化侵蝕率複利推算）。\n"
                    f"建議汰弱留強，換成含息報酬率 > 配息率的標的。")
            elif not _has_ret1y and _adr > 0:  # 無 wb01 也無 ret_1y
                st.info("ℹ️ 淨值年報酬率資料尚未取得，含息總報酬計算以 0% 淨值變動估計，請參考實際績效再判斷。")

            # ── v16.0 T3: 配息轉配股 (DRIP) vs 領現金 建議 ──────────────
            # 防雷：含息報酬率>0 → DRIP 複利加速 | <0 → 負複利陷阱，嚴禁配股
            st.divider()
            st.markdown("### 💸 配息處理建議：DRIP 複利 vs 領現金防雷")
            st.caption(
                "**MK 核心原則**：只有體質健康的基金才能開啟「配息轉配股」。"
                "淨值長期衰退的基金若開啟 DRIP，等於用虧損淨值強制買進更多虧損單位 → **負複利陷阱**！")

            if not _has_ret1y or _adr <= 0:
                # 邊界保護：無資料或無配息（累積型基金）
                st.info("ℹ️ 累積型基金或報酬資料不足，不適用配息 DRIP 建議。")
            elif _total_ret > 3 and not _eat_principal:
                # ✅ DRIP 最佳：含息報酬率 > 3% 且未吃本金
                st.success(
                    f"🟢 **體質優良！建議開啟「配息轉配股 (DRIP)」**\n\n"
                    f"含息總報酬 **{_total_ret:.2f}%** > 0，配息率 {_adr:.2f}%，"
                    f"淨值持續成長 → 每次配息自動買入更多單位，**複利加速累積**。\n"
                    f"✅ 策略建議：維持持有，設定配息轉配股，勿頻繁進出。")
            elif 0 < _total_ret <= 3 and not _eat_principal:
                # 🟡 DRIP 可行但留意
                st.warning(
                    f"🟡 **體質尚可，謹慎開啟 DRIP**\n\n"
                    f"含息總報酬 **{_total_ret:.2f}%**，小幅正報酬，建議觀察 2-3 個月趨勢後再決定 DRIP。\n"
                    f"若含息報酬持續 > 配息率 ({_adr:.2f}%)，方可開啟配息轉配股。")
            else:
                # 🚨 DRIP 禁止：含息報酬率 <= 0 或吃本金
                _drip_warn_detail = ""
                if _eat_principal:
                    _drip_warn_detail = (
                        f"含息報酬 {_total_ret:.2f}% < 配息率 {_adr:.2f}%，"
                        f"每次「配股」等於 **以虧損淨值強制補倉**，加速本金侵蝕。")
                else:
                    _drip_warn_detail = (
                        f"含息總報酬 **{_total_ret:.2f}%** ≤ 0，淨值長期衰退，"
                        f"開啟 DRIP 將使單位數增加但總資產持續縮水。")
                st.error(
                    f"🚨 **價值陷阱警告：嚴禁開啟配息轉配股！**\n\n"
                    f"{_drip_warn_detail}\n\n"
                    f"**正確做法**：\n"
                    f"① 立即關閉 DRIP，改為「領取現金」保存戰鬥力\n"
                    f"② 以「汰弱留強」評分評估是否換成含息報酬率 > 配息率的優質標的\n"
                    f"③ 新資金暫停投入此基金，等待好轉訊號（含息報酬 > 配息率連續 2 季）")

            st.divider()

            # ① 5 診斷卡片
            _dg1, _dg2, _dg3, _dg4, _dg5 = st.columns(5)

            # Card1: 含息總報酬 vs 配息率
            _eat_c = "#f44336" if _eat_principal else ("#00c853" if _has_ret1y else "#888")
            if not _has_ret1y:
                _eat_icon = "⚪ 無淨值變動資料"
            elif _eat_principal:
                _eat_icon = "🔴 疑似吃本金"
            else:
                _eat_icon = "✅ 配息健康"
            # 公式明細顯示
            _nav_chg_str = f"{_nav_chg_safe:+.2f}%" if _has_ret1y else "N/A"
            _formula_html = (
                f"<div style='font-size:9px;color:#555;margin-top:4px;line-height:1.6'>"
                f"淨值變動 {_nav_chg_str} + 配息 {_adr:.2f}% = {_total_ret:.2f}%</div>"
            )
            _dg1.markdown(
                f"<div style='background:#161b22;border:2px solid {_eat_c};"
                f"border-radius:10px;padding:10px;text-align:center;min-height:140px'>"
                f"<div style='font-size:10px;color:#888'>含息總報酬(1Y)</div>"
                f"<div style='font-size:22px;font-weight:900;color:{_eat_c}'>"
                f"{'N/A' if not _has_ret1y else f'{_total_ret:.2f}%'}</div>"
                f"<div style='font-size:10px;color:#888'>vs 配息率 {_adr:.2f}%</div>"
                f"{_formula_html}"
                f"<div style='font-size:11px;color:{_eat_c};margin-top:4px'>{_eat_icon}</div>"
                f"</div>", unsafe_allow_html=True)

            # Card2: Sharpe Ratio
            try:
                _shp_v = float(str(_shp1y).replace("—","").replace("N/A","").strip()) if _shp1y else 0
            except: _shp_v = 0
            _shp_has_data = _shp1y and str(_shp1y) not in ("","—","N/A","None")
            _shp_c = "#00c853" if _shp_v>1 else ("#ff9800" if _shp_v>0 else ("#888" if not _shp_has_data else "#f44336"))
            _shp_grade = ("A 優秀" if _shp_v>1.5 else ("B 良好" if _shp_v>1 else ("C 普通" if _shp_v>0 else ("N/A 待更新" if not _shp_has_data else "D 偏差"))))
            _dg2.markdown(
                f"<div style='background:#161b22;border:2px solid {_shp_c};"
                f"border-radius:10px;padding:12px;text-align:center;height:140px'>"
                f"<div style='font-size:10px;color:#888'>績效CP值 (Sharpe)</div>"
                f"<div style='font-size:22px;font-weight:900;color:{_shp_c}'>{_shp1y or 'N/A'}</div>"
                f"<div style='font-size:10px;color:#888;margin-top:4px'>每承擔1分風險的報酬</div>"
                f"<div style='font-size:11px;color:{_shp_c};margin-top:6px'>{_shp_grade}</div>"
                f"</div>", unsafe_allow_html=True)

            # Card3: 費用率監控 vs 同類平均
            try:
                _fee_v = float(str(_mgmt_fee_raw).replace("%","").strip()) if _mgmt_fee_raw else None
            except: _fee_v = None
            # 同類型平均費用率（MK 參考值）
            _peer_fee_map = {"股票型": 1.5, "債券型": 1.0, "平衡型": 1.3, "貨幣型": 0.5}
            _peer_fee = 1.5  # 預設股票型
            for _k, _v in _peer_fee_map.items():
                if _k in (_cat or ""):
                    _peer_fee = _v; break
            if _fee_v is not None:
                _fee_diff = _fee_v - _peer_fee
                _fee_c  = "#00c853" if _fee_diff <= 0 else ("#ff9800" if _fee_diff <= 0.3 else "#f44336")
                _fee_label = "低於平均✅" if _fee_diff <= 0 else ("持平⚖️" if _fee_diff <= 0.3 else "偏高⚠️")
                _fee_disp = f"{_fee_v:.2f}%"
            else:
                _fee_c = "#888"; _fee_label = "資料待更新"; _fee_disp = "N/A"
            _dg3.markdown(
                f"<div style='background:#161b22;border:2px solid {_fee_c};"
                f"border-radius:10px;padding:12px;text-align:center;height:140px'>"
                f"<div style='font-size:10px;color:#888'>最高費用率</div>"
                f"<div style='font-size:22px;font-weight:900;color:{_fee_c}'>{_fee_disp}</div>"
                f"<div style='font-size:10px;color:#888'>同類平均 {_peer_fee:.1f}%</div>"
                f"<div style='font-size:11px;color:{_fee_c};margin-top:6px'>{_fee_label}</div>"
                f"</div>", unsafe_allow_html=True)

            # Card4: 配息穩定度
            _dg4.markdown(
                f"<div style='background:#161b22;border:2px solid {_div_stb_c};"
                f"border-radius:10px;padding:12px;text-align:center;height:140px'>"
                f"<div style='font-size:10px;color:#888'>配息穩定度</div>"
                f"<div style='font-size:20px;font-weight:900;color:{_div_stb_c}'>{_div_stb}</div>"
                f"<div style='font-size:10px;color:#888;margin-top:4px'>CV 變異係數</div>"
                f"<div style='font-size:11px;color:{_div_stb_c};margin-top:6px'>MK：穩定→適合核心</div>"
                f"</div>", unsafe_allow_html=True)

            # Card5: 年化標準差
            _std_v_f = float(str(_std1y).replace("—","0") or 0) if _std1y else 0
            _std_c = "#00c853" if _std_v_f<8 else ("#ff9800" if _std_v_f<15 else "#f44336")
            _std_label = "低波動🛡️" if _std_v_f<8 else ("中波動⚖️" if _std_v_f<15 else "高波動⚠️")
            _dg5.markdown(
                f"<div style='background:#161b22;border:2px solid {_std_c};"
                f"border-radius:10px;padding:12px;text-align:center;height:140px'>"
                f"<div style='font-size:10px;color:#888'>波動幅度(標準差1Y)</div>"
                f"<div style='font-size:22px;font-weight:900;color:{_std_c}'>{_std1y or 'N/A'}%</div>"
                f"<div style='font-size:10px;color:#888;margin-top:4px'>核心資產目標 <15%</div>"
                f"<div style='font-size:11px;color:{_std_c};margin-top:6px'>{_std_label}</div>"
                f"</div>", unsafe_allow_html=True)

            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

            # ── v10.4 標準差買賣訊號鏡 ─────────────────────────────────
            st.divider()
            st.markdown("### 📐 標準差買賣訊號鏡（σ 量化高低點）")
            _s_bb = fd.get("series")
            _m_bb = fd.get("metrics", {})
            _mj_bb = fd.get("moneydj_raw", {}) or {}
            _rt_bb = _mj_bb.get("risk_metrics", {}).get("risk_table", {})
            _std_bb = None
            try:
                _std_bb = float(str(_rt_bb.get("一年", {}).get("標準差", "") or "").replace("—",""))
            except: pass
            if _std_bb is None:
                try: _std_bb = float(str(_m_bb.get("std_1y", 0) or 0))
                except: pass

            _nav_now = _m_bb.get("nav", 0) or 0
            _high1y  = _m_bb.get("high_1y", 0) or 0
            _perf_bb = _mj_bb.get("perf", {})
            _tr1y_bb = _perf_bb.get("1Y") if isinstance(_perf_bb, dict) else None
            _adr_bb  = (_mj_bb.get("moneydj_div_yield") or _m_bb.get("annual_div_rate") or 0)
            try: _adr_bb = float(_adr_bb) if _adr_bb is not None else None
            except (ValueError, TypeError): _adr_bb = None

            if _high1y and _std_bb and _std_bb > 0:
                # σ 買點計算（nav 單位）
                _buy1 = round(_high1y - _std_bb / 100 * _high1y, 4)
                _buy2 = round(_high1y - 2 * _std_bb / 100 * _high1y, 4)
                # 收割訊號判斷：nav ≥ 近一年最高 且 含息年回報 > 20%
                _near_high = _high1y > 0 and _nav_now >= _high1y * 0.97
                _high_ret   = _tr1y_bb is not None and float(_tr1y_bb) > 20
                _harvest    = _near_high and _high_ret

                # 倉位判斷
                if _nav_now <= _buy2:
                    _pos_lbl, _pos_c = "🟢 大買區(跌破2σ)", "#00c853"
                    _pos_act = "量化絕對買點：大幅加碼，克服恐慌"
                elif _nav_now <= _buy1:
                    _pos_lbl, _pos_c = "🟡 小買區(跌破1σ)", "#69f0ae"
                    _pos_act = "量化買點：適量加碼，分批進場"
                elif _harvest:
                    _pos_lbl, _pos_c = "🔴 收割區(高點+高報酬)", "#f44336"
                    _pos_act = "停利衛星、轉入核心—克服貪婪"
                elif _near_high:
                    _pos_lbl, _pos_c = "🟠 觀察高點區", "#ff9800"
                    _pos_act = "接近近一年高點，設好停利點"
                else:
                    _pos_lbl, _pos_c = "⚪ 中性區間", "#888"
                    _pos_act = "靜待訊號，勿追高"

                # Harvest alert
                if _harvest:
                    st.error(
                        f"🔴 **停利訊號啟動！** 淨值 {_nav_now:.4f} 接近1年高點 {_high1y:.4f}"
                        f"，含息年報酬 {_tr1y_bb:.1f}% > 20%。"
                        f"→ **「停利衛星、轉入核心」**：賣出衛星部位，將資金轉入核心配息資產。")
                elif _nav_now <= _buy2:
                    # Fix: strict NAV <= buy2 (跌破-2σ) → 大買訊號
                    st.success(
                        f"🛒 **量化大買訊號！** 淨值 {_nav_now:.4f} ≤ 買點-2σ ({_buy2:.4f})\n\n"
                        f"（近1年高點 {_high1y:.4f} − 2×{_std_bb:.1f}% = {_buy2:.4f}）\n"
                        f"→ **量化絕對買點，請嚴守紀律分批單筆申購**。凱利建議見下方。")
                elif _nav_now <= _buy1:
                    # Fix: strict NAV <= buy1 (跌破-1σ) → 小買訊號
                    st.success(
                        f"🛒 **量化加碼價：{_buy1:.4f} 元（已跌破 1個標準差）**\n\n"
                        f"淨值 {_nav_now:.4f} ≤ 買點-1σ ({_buy1:.4f})\n"
                        f"→ 請嚴守紀律分批單筆申購，勿一次滿倉。")
                else:
                    # Fix: NAV > buy1 → 未達標，顯示距離差距（正確資訊）
                    _gap1 = round(_nav_now - _buy1, 4)   # 距離-1σ買點還差幾元
                    _gap2 = round(_nav_now - _buy2, 4)   # 距離-2σ買點還差幾元
                    _gap1_pct = round(_gap1 / _nav_now * 100, 2)
                    st.info(
                        f"⏳ **尚未達到量化買點**\n\n"
                        f"目前淨值 {_nav_now:.4f} 仍高於 -1σ 買點 {_buy1:.4f}\n"
                        f"距離 -1σ 買點還差 **${_gap1:.4f}**（{_gap1_pct:.1f}%）\n"
                        f"距離 -2σ 買點還差 **${_gap2:.4f}**\n"
                        f"→ 目前淨值在合理區間，請耐心等待訊號，保留現金備用。")

                # σ 訊號卡片
                _sc1, _sc2, _sc3, _sc4 = st.columns(4)
                _sig_cards = [
                    ("近1年高點", f"{_high1y:.4f}", "#69f0ae", "收割參考基準"),
                    (f"買點1σ (-{_std_bb:.1f}%)", f"{_buy1:.4f}", "#ffeb3b", "小買區—分批進場"),
                    (f"買點2σ (-{_std_bb*2:.1f}%)", f"{_buy2:.4f}", "#00c853", "大買區—量化底部"),
                    ("目前倉位", _pos_lbl, _pos_c, _pos_act),
                ]
                for _col, (_lbl, _val, _vc, _sub) in zip([_sc1,_sc2,_sc3,_sc4], _sig_cards):
                    _col.markdown(
                        f"<div style='background:#161b22;border:1px solid {_vc};border-radius:8px;"
                        f"padding:10px;text-align:center;min-height:90px'>"
                        f"<div style='font-size:10px;color:#888'>{_lbl}</div>"
                        f"<div style='font-size:15px;font-weight:900;color:{_vc};word-break:break-all'>{_val}</div>"
                        f"<div style='font-size:9px;color:#666;margin-top:3px'>{_sub}</div>"
                        f"</div>", unsafe_allow_html=True)
            else:
                st.info("📐 需有「近一年最高淨值」與「1年標準差」才能計算 σ 量化買點"
                        "（請確認 MoneyDJ wb07 已載入 → 需有 1年期標準差%）")

            # ── v16.0 T3: 凱利公式加碼建議 ────────────────────────────
            st.markdown("### 💰 凱利公式加碼建議（Kelly Criterion）")
            st.caption("公式：f* = (b×p − q) / b　Half-Kelly = f*/2　b=賠率 p=勝率 q=敗率")
            _s_kelly = fd.get("series")
            try:
                from portfolio_engine import calc_kelly as _calc_kelly
                _kelly_res = _calc_kelly(_s_kelly, lookback=252)
            except Exception as _ke:
                _kelly_res = {"kelly": None, "note": str(_ke)[:60]}

            if _kelly_res.get("half_kelly") is not None:
                _hk   = _kelly_res["half_kelly_pct"]
                _wr   = _kelly_res["win_rate_pct"]
                _odds = _kelly_res["odds"]
                _full = _kelly_res["kelly"] * 100
                _hk_c = "#00c853" if _hk >= 20 else ("#ff9800" if _hk >= 10 else "#f44336")
                _kc1, _kc2, _kc3 = st.columns(3)
                _kc1.markdown(
                    f"<div style='background:#0a1a0e;border:1px solid {_hk_c};"
                    f"border-radius:10px;padding:12px;text-align:center'>"
                    f"<div style='font-size:10px;color:#888'>Half-Kelly 建議投入</div>"
                    f"<div style='font-size:28px;font-weight:900;color:{_hk_c}'>{_hk:.1f}%</div>"
                    f"<div style='font-size:10px;color:#666'>全凱利 {_full:.1f}% 的一半</div>"
                    f"</div>", unsafe_allow_html=True)
                _kc2.markdown(
                    f"<div style='background:#0d1117;border:1px solid #30363d;"
                    f"border-radius:10px;padding:12px;text-align:center'>"
                    f"<div style='font-size:10px;color:#888'>勝率 (p)</div>"
                    f"<div style='font-size:24px;font-weight:700;color:#64b5f6'>{_wr:.1f}%</div>"
                    f"<div style='font-size:10px;color:#666'>正報酬日佔比</div>"
                    f"</div>", unsafe_allow_html=True)
                _kc3.markdown(
                    f"<div style='background:#0d1117;border:1px solid #30363d;"
                    f"border-radius:10px;padding:12px;text-align:center'>"
                    f"<div style='font-size:10px;color:#888'>賠率 (b)</div>"
                    f"<div style='font-size:24px;font-weight:700;color:#ff9800'>{_odds:.2f}x</div>"
                    f"<div style='font-size:10px;color:#666'>平均獲利/平均虧損</div>"
                    f"</div>", unsafe_allow_html=True)
                # Fix: show WHY Kelly=0 (f*<0 = negative expected value)
                _kelly_zero = (_kelly_res.get("kelly", 1) == 0.0 and
                               _kelly_res.get("kelly_raw", 0) < 0)
                if _kelly_zero:
                    st.warning(
                        f"⚠️ **凱利公式：此時不宜加碼（數學期望值為負）**\n\n"
                        f"原始 f* = {_kelly_res.get('kelly_raw',0):.3f}（負值）"
                        f" = 勝率{_wr:.1f}% × 賠率{_odds:.2f}x 仍不足以覆蓋敗率 {100-_wr:.1f}%\n"
                        f"→ 保留現金，等待淨值跌至 σ 買點後勝率提升再進場。")
                else:
                    st.markdown(
                        f"<div style='background:#0d1117;border-left:4px solid {_hk_c};"
                        f"padding:10px 14px;border-radius:0 8px 8px 0;margin-top:8px'>"
                        f"<b style='color:{_hk_c}'>🛒 凱利建議</b>："
                        f"{_kelly_res['note']}<br>"
                        f"<span style='color:#888;font-size:11px'>"
                        f"⚠️ Half-Kelly 可降低破產風險，實務建議不超過 Full-Kelly 的 50%</span>"
                        f"</div>", unsafe_allow_html=True)
            else:
                st.info(f"💡 凱利公式需至少 30 筆淨值歷史　{_kelly_res.get('note','')}")

            # ══════════════════════════════════════════════════════
            # v16.0 Tasks 6-11: 法人級進階防雷模組
            # ══════════════════════════════════════════════════════
            st.divider()
            _adv_tabs = st.tabs([
                "📊 T6 勝率位階", "💱 T7 匯率地雷",
                "🔄 T8 活水投資", "🔗 T9 假分散",
                "💰 T10 費用精算", "🛡️ T11 壓力測試"])

            # ── T6: 投資勝率與大盤位階對比 ──────────────────────────
            with _adv_tabs[0]:
                st.markdown("#### 📊 T6 投資勝率與美林時鐘位階對比")
                st.caption("不同景氣位階下，各基金類別的歷史平均勝率（MK 策略表）")

                # 美林時鐘四象限 × 基金類別勝率表
                _CLOCK_TABLE = {
                    "復甦期": {"股票型(全球/美股)":"🟢 高(75%)","債券型(投資等級)":"🟡 中(55%)",
                               "科技/成長型":"🟢 高(70%)","防禦/公用事業":"🟡 中(50%)","配息平衡型":"🟢 高(65%)"},
                    "擴張期": {"股票型(全球/美股)":"🟢 高(80%)","債券型(投資等級)":"🔴 低(40%)",
                               "科技/成長型":"🟢 最高(85%)","防禦/公用事業":"🔴 低(35%)","配息平衡型":"🟡 中(60%)"},
                    "高峰期": {"股票型(全球/美股)":"🟡 中(50%)","債券型(投資等級)":"🟡 中(55%)",
                               "科技/成長型":"🔴 低(40%)","防禦/公用事業":"🟡 中(55%)","配息平衡型":"🟡 中(55%)"},
                    "衰退期": {"股票型(全球/美股)":"🔴 低(30%)","債券型(投資等級)":"🟢 高(70%)",
                               "科技/成長型":"🔴 極低(20%)","防禦/公用事業":"🟢 高(65%)","配息平衡型":"🟡 中(50%)"},
                }

                import pandas as _pd_t6
                _df_clock = _pd_t6.DataFrame(_CLOCK_TABLE).T
                st.dataframe(_df_clock, use_container_width=True)

                # 判斷當前位階
                _cur_phase_t6 = (st.session_state.phase_info.get("phase","—")
                                 if st.session_state.macro_done else "—")
                _mj_cat = (_mj.get("investment_target","") or _mj.get("category","") or "")

                # 勝率警告邏輯
                _LOW_WIN_PAIRS = {
                    "衰退期": ["科技","成長","科技/成長型","股票型"],
                    "高峰期": ["科技","成長","主題","新興"],
                    "擴張期": ["債券","保守","公用"],
                }
                _warn_cat = _LOW_WIN_PAIRS.get(_cur_phase_t6, [])
                _is_low_win = any(kw in _mj_cat for kw in _warn_cat)

                if _cur_phase_t6 != "—" and _is_low_win:
                    st.error(
                        f"⚠️ **當前景氣位階「{_cur_phase_t6}」不適合重壓此類別！**\n\n"
                        f"基金類別：{_mj_cat[:30]}｜歷史勝率偏低\n"
                        f"建議：控制資金水位至 20% 以下，或轉入當期高勝率類別。")
                elif _cur_phase_t6 != "—":
                    st.success(f"✅ 當前景氣位階「{_cur_phase_t6}」與此基金類別相容，勝率尚可。")
                else:
                    st.info("💡 請先在「🌐 總經儀表板」載入總經資料以取得當前景氣位階。")

            # ── T7: 計價幣別趨勢防雷網 ──────────────────────────────
            with _adv_tabs[1]:
                st.markdown("#### 💱 T7 計價幣別趨勢防雷網（非美元/台幣 警戒）")
                _fx_currency = _mj.get("currency","USD") or "USD"

                # 高風險幣別：南非幣、土耳其里拉、巴西幣等
                _HIGH_RISK_CCY = {"ZAR":"南非幣","TRY":"土耳其里拉","BRL":"巴西幣","IDR":"印尼盾"}
                _FX_TICKERS = {"ZAR":"ZAR=X","TRY":"TRY=X","BRL":"BRL=X",
                               "EUR":"EURUSD=X","JPY":"JPY=X","AUD":"AUDUSD=X"}

                if _fx_currency in ("TWD", "USD") or _fx_currency not in _FX_TICKERS:
                    st.info(f"ℹ️ {_fx_currency} 計價基金不在高風險幣別監控名單。")
                else:
                    _fx_tk = _FX_TICKERS[_fx_currency]
                    _ccy_name = _HIGH_RISK_CCY.get(_fx_currency, _fx_currency)
                    try:
                        import yfinance as _yf7, pandas as _pd7
                        _fx_hist = _yf7.Ticker(_fx_tk).history(period="1y",
                                                                auto_adjust=True)["Close"].dropna()
                        if len(_fx_hist) >= 50:
                            # 200日均線（不足200日時用全部資料均線）
                            _win = min(200, len(_fx_hist))
                            _fx_ma = _fx_hist.rolling(_win).mean()
                            _fx_now   = float(_fx_hist.iloc[-1])
                            _fx_ma_v  = float(_fx_ma.iloc[-1])
                            _fx_chg   = round((_fx_now / _fx_hist.iloc[-60] - 1)*100, 2)
                            _below_ma = _fx_now < _fx_ma_v
                            if _below_ma:
                                st.error(
                                    f"🚨 **匯率地雷警告！{_ccy_name}（{_fx_currency}）**\n\n"
                                    f"目前匯率 {_fx_now:.4f} < {_win}日均線 {_fx_ma_v:.4f}\n"
                                    f"近60日貶值 {_fx_chg:.1f}%，處於長期貶值趨勢\n"
                                    f"→ **強烈建議觀望或轉換計價級別（改持台幣或美元）**")
                            else:
                                st.success(
                                    f"✅ {_ccy_name} 匯率 {_fx_now:.4f} > {_win}日均線 {_fx_ma_v:.4f}，"
                                    f"近60日{'+' if _fx_chg>=0 else ''}{_fx_chg:.1f}%，趨勢尚可。")
                        else:
                            st.warning(f"⚠️ {_ccy_name} 匯率歷史資料不足，請手動查核趨勢。")
                    except Exception as _e7:
                        st.warning(f"⚠️ 匯率資料抓取失敗（{_e7}），請手動查核 {_ccy_name} 趨勢。")
                        try:
                            _write_error_ledger(_e7, "FX rate fetch", GEMINI_KEY)
                        except Exception:
                            pass

            # ── T8: 以息養股活水投資建議器 ──────────────────────────
            with _adv_tabs[2]:
                st.markdown("#### 🔄 T8 活水投資建議器（核心配息養衛星）")
                st.caption("當衛星標的跌破1σ時，建議動用本月核心配息進行單筆申購")

                # 讀取組合現金流資料
                _pf_t8 = [f for f in st.session_state.get("portfolio_funds",[])
                           if f.get("loaded")]
                _core_t8 = [f for f in _pf_t8 if f.get("is_core")]
                _sat_t8  = [f for f in _pf_t8 if not f.get("is_core")]

                # 計算核心月配息總額
                _monthly_flow = 0.0
                for _cf in _core_t8:
                    _mj_cf  = _cf.get("moneydj_raw", {}) or {}
                    _m_cf   = _cf.get("metrics", {}) or {}
                    _mdr_cf = _m_cf.get("monthly_div", 0) or 0
                    _inv_cf = _cf.get("invest_twd", 0) or 0
                    _adr_cf = float(_mj_cf.get("moneydj_div_yield") or
                                    _m_cf.get("annual_div_rate", 0) or 0)
                    if _mdr_cf > 0:
                        _monthly_flow += _mdr_cf * (_inv_cf / 32 / 10) * 32  # approx units
                    elif _adr_cf > 0 and _inv_cf > 0:
                        _monthly_flow += _inv_cf * _adr_cf / 100 / 12

                # 檢查當前基金是否為衛星且跌破1σ
                _is_sat_fund = assign_asset_role(_mj.get("fund_name","")) != "core"
                # Fix: _std_bb/_high1y may not be in scope at T8 time
                try:
                    _at_dip = (bool(_std_bb) and _std_bb > 0 and bool(_high1y) and bool(_nav_now) and
                               _nav_now <= round(_high1y - _std_bb / 100 * _high1y, 4))
                except Exception:
                    _at_dip = False

                if not _pf_t8:
                    st.info("💡 請先在「我的投資組合」加入核心配息基金，系統才能計算活水金額。")
                elif _is_sat_fund and _at_dip and _monthly_flow > 0:
                    st.success(
                        f"🔄 **活水投資啟動！**\n\n"
                        f"此衛星標的已跌破 -1σ 買點，觸發活水條件。\n"
                        f"建議將本月核心配息收入 **NT${_monthly_flow:,.0f}** 元\n"
                        f"→ 單筆申購此超跌衛星標的「{_mj.get('fund_name','?')[:20]}」\n"
                        f"✅ 以息養股最佳實踐：核心提供子彈，衛星逢低擴張。")
                elif not _is_sat_fund:
                    st.info("ℹ️ 此為核心基金，活水機制不適用（活水目標為衛星標的）。")
                elif not _at_dip:
                    _gap_t8 = (round(_nav_now - round(_high1y - _std_bb/100*_high1y, 4), 4)
                               if _std_bb and _high1y and _nav_now else None)
                    _gap_str = f"距離 -1σ 買點還差 ${_gap_t8:.4f}" if _gap_t8 else ""
                    st.info(f"⏳ 衛星標的尚未跌破買點，暫不觸發活水。{_gap_str}\n"
                            f"核心月配息預估：NT${_monthly_flow:,.0f}")
                else:
                    st.info("💡 核心月配息資料不足，請確認組合內核心基金已設定投入金額。")

            # ── T9: 假分散檢測器（皮爾森相關係數）───────────────────
            with _adv_tabs[3]:
                st.markdown("#### 🔗 T9 假分散檢測器（資產相關性矩陣）")
                st.caption(
                    "皮爾森相關係數 r：接近+1=同漲同跌（假分散）｜"
                    "接近0=低相關（真分散）｜接近-1=反向（最佳對沖）")

                _pf_t9 = [f for f in st.session_state.get("portfolio_funds",[])
                           if f.get("loaded")]
                _nav_dict_t9 = {}
                for _f9 in _pf_t9:
                    _mj9 = _f9.get("moneydj_raw",{}) or {}
                    # Fix: avoid `or` on pandas Series (ambiguous truth)
                    _s9_a = _mj9.get("series"); _s9_b = _f9.get("series")
                    import pandas as _pd_s9fix
                    _s9 = (_s9_a if isinstance(_s9_a, _pd_s9fix.Series) and len(_s9_a) > 0
                           else _s9_b if isinstance(_s9_b, _pd_s9fix.Series) and len(_s9_b) > 0
                           else _s9_a if _s9_a is not None else _s9_b)
                    _n9  = (_f9.get("name","") or _f9["code"])[:12]
                    if _s9 is not None and hasattr(_s9,"__len__") and len(_s9) >= 30:
                        _nav_dict_t9[_n9] = _s9

                if len(_nav_dict_t9) < 2:
                    st.info("💡 需要至少 2 檔已載入組合基金（含淨值歷史）才能計算相關係數。")
                else:
                    import pandas as _pd_t9
                    # Fix: 向量化計算，避免 for 迴圈；dropna 防止空白欄位
                    _df_nav9 = _pd_t9.DataFrame(_nav_dict_t9).ffill().bfill().dropna(how="all")
                    _corr9   = _df_nav9.pct_change().dropna().corr().round(3)

                    # 找出高相關對（r > 0.8）
                    _high_corr_pairs = []
                    _cols9 = list(_corr9.columns)
                    for _i9 in range(len(_cols9)):
                        for _j9 in range(_i9+1, len(_cols9)):
                            _rv = _corr9.iloc[_i9, _j9]
                            if _rv > 0.8:
                                _high_corr_pairs.append((_cols9[_i9], _cols9[_j9], _rv))

                    if _high_corr_pairs:
                        st.error(
                            f"⚠️ **假分散警示：偵測到 {len(_high_corr_pairs)} 對高相關資產！**\n\n"
                            + "\n".join(f"🔴 {a} × {b}  r={v:.3f}（遇股災將同步重跌）"
                                         for a,b,v in _high_corr_pairs)
                            + "\n\n→ 建議納入低相關的投資等級債券基金或防禦型配息基金")
                    else:
                        st.success("✅ 組合相關係數均 ≤ 0.8，分散效果良好！")

                    # 熱力圖
                    try:
                        import plotly.graph_objects as _pgo9
                        _z9 = _corr9.values.tolist()
                        _n9l = list(_corr9.columns)
                        _fig9 = _pgo9.Figure(_pgo9.Heatmap(
                            z=_z9, x=_n9l, y=_n9l, colorscale="RdBu_r",
                            zmin=-1, zmax=1, zmid=0,
                            text=[[f"{v:.2f}" for v in row] for row in _z9],
                            texttemplate="%{text}", textfont={"size":11}))
                        _fig9.update_layout(
                            height=max(250, len(_n9l)*70),
                            paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                            font={"color":"#e6edf3"}, margin=dict(l=10,r=10,t=10,b=10))
                        st.plotly_chart(_fig9, use_container_width=True)
                    except Exception:
                        st.dataframe(_corr9, use_container_width=True)

            # ── T10: 摩擦成本與稅後精算 ─────────────────────────────
            with _adv_tabs[4]:
                st.markdown("#### 💰 T10 摩擦成本精算（費用率 × 稅後複利）")

                _expense_raw = _mj.get("mgmt_fee","") or _mj.get("expense_ratio","") or ""
                _expense = None
                try:
                    import re as _re10
                    _nums = _re10.findall(r"[\d.]+", str(_expense_raw))
                    if _nums:
                        _expense = float(_nums[0])
                except Exception:
                    pass

                _tc1, _tc2 = st.columns(2)
                with _tc1:
                    _er_input = st.number_input(
                        "基金總費用率（%/年）", min_value=0.0, max_value=5.0,
                        value=_expense or 1.5, step=0.05, key="t10_er",
                        help="MoneyDJ 基金資料頁通常有標示管理費+保管費")
                with _tc2:
                    _yrs_t10 = st.slider("試算年數", 5, 30, 15, key="t10_yrs")

                # 費用率警告
                if _er_input > 2.0:
                    st.error(f"🚨 費用率 {_er_input:.2f}% > 2%，屬高成本基金！長期複利嚴重損耗。")
                elif _er_input > 1.5:
                    st.warning(f"⚠️ 費用率 {_er_input:.2f}%，偏高，建議尋找 <1.5% 同類替代標的。")
                else:
                    st.success(f"✅ 費用率 {_er_input:.2f}%，屬合理範圍。")

                # 稅後複利試算（假設毛報酬率 8%）
                _gross = 8.0
                _net_accum  = _gross - _er_input          # 累積型（費用扣除，無配息稅）
                _dist_tax   = 1.0                          # 配息稅率（台灣境外2%手續+稅）
                _net_dist   = _gross - _er_input - _dist_tax  # 配息型（另扣稅）

                _inv_base = 1_000_000  # 100萬試算
                _accum_end = _inv_base * (1 + _net_accum/100) ** _yrs_t10
                _dist_end  = _inv_base * (1 + _net_dist/100)  ** _yrs_t10
                _diff      = _accum_end - _dist_end

                import pandas as _pd_t10
                _years_arr = list(range(1, _yrs_t10+1))
                _df_t10 = _pd_t10.DataFrame({
                    "年度":         _years_arr,
                    "累積型 NT$":  [round(_inv_base*(1+_net_accum/100)**y) for y in _years_arr],
                    "配息型 NT$":  [round(_inv_base*(1+_net_dist /100)**y) for y in _years_arr],
                })
                st.markdown(
                    f"**假設毛報酬率 {_gross:.0f}%，100萬試算 {_yrs_t10} 年**\n\n"
                    f"累積型淨報酬：{_net_accum:.2f}%　→　NT${_accum_end:,.0f}\n"
                    f"配息型淨報酬：{_net_dist:.2f}%（另扣 {_dist_tax:.1f}% 稅後損耗）→　NT${_dist_end:,.0f}\n"
                    f"**累積型多賺：NT${_diff:,.0f}**（{_yrs_t10} 年複利差距）")
                st.dataframe(_df_t10.set_index("年度"), use_container_width=True)

            # ── T11: 黑天鵝壓力測試模擬器 ───────────────────────────
            with _adv_tabs[5]:
                st.markdown("#### 🛡️ T11 黑天鵝壓力測試模擬器（MDD 極端情境）")
                st.caption("根據此基金歷史最大回撤（MDD），模擬極端空頭情境下的資產損失")

                # 取得 MDD（可能正值或負值，統一用 abs）
                _mdd_raw = _m_bb.get("max_drawdown", 0) or 0
                try:
                    _mdd_abs = abs(float(_mdd_raw))  # Fix: 統一為正值
                except (TypeError, ValueError):
                    _mdd_abs = 0

                _sv1_t11, _sv2_t11 = st.columns(2)
                with _sv1_t11:
                    _stress_inv = st.number_input(
                        "投入金額（NT$）", min_value=10000, max_value=50000000,
                        value=500000, step=10000, key="t11_inv")
                with _sv2_t11:
                    _stress_mdd = st.number_input(
                        "假設最大回撤（%）", min_value=1.0, max_value=99.0,
                        value=float(f"{_mdd_abs:.1f}") if _mdd_abs > 0 else 30.0,
                        step=0.5, key="t11_mdd",
                        help=f"歷史 MDD：{_mdd_abs:.2f}%（可手動調整壓力測試幅度）")

                # 壓力測試計算（O(1)，純算術）
                _loss_amt   = round(_stress_inv * _stress_mdd / 100)
                _remain_amt = _stress_inv - _loss_amt
                _recover_pct = round((1 / (1 - _stress_mdd/100) - 1) * 100, 1) if _stress_mdd < 100 else float('inf')

                # 顏色判斷
                _stress_c = "#f44336" if _stress_mdd > 30 else ("#ff9800" if _stress_mdd > 15 else "#00c853")

                st.markdown(
                    f"<div style='background:#1a0505;border:2px solid {_stress_c};"
                    f"border-radius:12px;padding:16px 20px;margin-top:10px'>"
                    f"<div style='font-size:14px;font-weight:900;color:{_stress_c}'>"
                    f"🛡️ 壓力測試結果（極端空頭 -{_stress_mdd:.1f}%）</div>"
                    f"<div style='font-size:13px;color:#e6edf3;margin-top:10px;line-height:2'>"
                    f"投入本金　：NT${_stress_inv:>12,.0f}<br>"
                    f"預估最大虧損：<b style='color:{_stress_c}'>-NT${_loss_amt:>10,.0f}</b><br>"
                    f"剩餘資產　：NT${_remain_amt:>12,.0f}<br>"
                    f"回本所需漲幅：<b style='color:#ff9800'>+{_recover_pct:.1f}%</b>（虧損後需漲更多才回本）"
                    f"</div>"
                    f"{'<div style=color:#f44336;font-size:13px;margin-top:10px;font-weight:700>🚨 損失超過 30%！若超過您的心理承受底線，請立即調降成長型基金部位至 20% 以下。</div>' if _stress_mdd > 30 else ''}"
                    f"</div>",
                    unsafe_allow_html=True)

                st.markdown(
                    f"**MK 風控原則**：若組合最壞情況虧損 NT${_loss_amt:,.0f} 超過可承受底線，"
                    f"請調降衛星部位或增加投資等級債比例至核心。")

            # ── v10.4 布林通道（Bollinger Bands）走勢圖 ──────────────────
            if _s_bb is not None and len(_s_bb) >= 20:
                try:
                    import plotly.graph_objects as _pgo_bb
                    import pandas as _pd_bb
                    _ss = _s_bb.copy()
                    if hasattr(_ss, "iloc"):
                        _ss = _ss.astype(float)
                        _window = 20
                        _bb_mid  = _ss.rolling(_window).mean()
                        _bb_std  = _ss.rolling(_window).std()
                        _bb_up   = _bb_mid + 2 * _bb_std
                        _bb_dn   = _bb_mid - 2 * _bb_std
                        _bb_up1  = _bb_mid + 1 * _bb_std
                        _bb_dn1  = _bb_mid - 1 * _bb_std
                        _idx_bb  = _ss.index
                        _fig_bb = _pgo_bb.Figure()
                        _fig_bb.add_trace(_pgo_bb.Scatter(
                            x=list(_idx_bb), y=list(_bb_up),
                            name="上軌(+2σ)", line=dict(color="#f44336", width=1, dash="dot")))
                        _fig_bb.add_trace(_pgo_bb.Scatter(
                            x=list(_idx_bb), y=list(_bb_up1),
                            name="上軌1σ", line=dict(color="#ff9800", width=1, dash="dot"),
                            fill="tonexty", fillcolor="rgba(244,67,54,0.04)"))
                        _fig_bb.add_trace(_pgo_bb.Scatter(
                            x=list(_idx_bb), y=list(_bb_mid),
                            name="中軌(MA20)", line=dict(color="#888", width=1)))
                        _fig_bb.add_trace(_pgo_bb.Scatter(
                            x=list(_idx_bb), y=list(_bb_dn1),
                            name="下軌1σ", line=dict(color="#69f0ae", width=1, dash="dot"),
                            fill="tonexty", fillcolor="rgba(0,200,83,0.04)"))
                        _fig_bb.add_trace(_pgo_bb.Scatter(
                            x=list(_idx_bb), y=list(_bb_dn),
                            name="下軌(-2σ)🟢買點", line=dict(color="#00c853", width=2, dash="dot"),
                            fill="tonexty", fillcolor="rgba(0,200,83,0.08)"))
                        _fig_bb.add_trace(_pgo_bb.Scatter(
                            x=list(_idx_bb), y=list(_ss),
                            name="淨值", line=dict(color="#58a6ff", width=2)))
                        # Mark current NAV point
                        if _nav_now and len(_idx_bb) > 0:
                            _fig_bb.add_hline(
                                y=_nav_now, line_dash="dash",
                                line_color="#ffeb3b", line_width=1,
                                annotation_text=f"現價 {_nav_now:.4f}",
                                annotation_font_color="#ffeb3b")
                        _fig_bb.update_layout(
                            title="📊 布林通道（Bollinger Bands）- 觸碰下軌=買點訊號",
                            height=340, paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                            font=dict(color="#e6edf3", size=11),
                            legend=dict(orientation="h", y=-0.15, font_size=10),
                            margin=dict(l=10, r=10, t=40, b=10),
                            yaxis=dict(gridcolor="#21262d"),
                            xaxis=dict(gridcolor="#21262d"))
                        st.plotly_chart(_fig_bb, use_container_width=True)
                        # Check if current nav touches lower band
                        _last_dn = float(_bb_dn.dropna().iloc[-1]) if len(_bb_dn.dropna()) > 0 else None
                        _last_dn1= float(_bb_dn1.dropna().iloc[-1]) if len(_bb_dn1.dropna()) > 0 else None
                        if _last_dn and _nav_now <= _last_dn:
                            st.error("🔔 **布林通道警示**：淨值已觸碰/跌破下軌(-2σ)！→ 量化買點出現，建議分批進場。")
                        elif _last_dn1 and _nav_now <= _last_dn1:
                            st.warning("🔔 **布林通道提示**：淨值觸碰下軌1σ，小買訊號出現。")
                except Exception as _ebb:
                    st.caption(f"布林通道圖表載入失敗：{_ebb}")

            # ── v10.4 四分位排名 + 吃本金強化 ─────────────────────────
            _peer_bb = (_mj_bb.get("risk_metrics") or {}).get("peer_compare", {})
            _qr = _quartile_check(_peer_bb, (_mj_bb.get("risk_metrics") or {}).get("risk_table", {}))
            if _qr["quartile"] is not None:
                st.markdown(
                    f"<div style='background:#161b22;border:2px solid {_qr['color']};"
                    f"border-radius:8px;padding:10px 14px;margin:6px 0;display:flex;"
                    f"align-items:center;gap:14px'>"
                    f"<div style='min-width:120px'>"
                    f"<div style='font-size:10px;color:#888'>同類四分位排名（Sharpe）</div>"
                    f"<div style='font-size:15px;font-weight:900;color:{_qr['color']}'>{_qr['label']}</div>"
                    f"</div>"
                    f"<div style='font-size:11px;color:#ccc'>{_qr['advice'] or '持續保持，定期複查'}</div>"
                    f"</div>", unsafe_allow_html=True)
                if _qr["warning"]:
                    st.error("🔴 **四分位警示**：" + (_qr["advice"] or "") + " 汰弱留強：將資金移往同組別 Sharpe 排名前 25% 的標的。")

            # ② 以息養股試算
            if _mdr and _nav:
                st.markdown("#### 💰 以息養股現金流試算")
                _inv_col1, _inv_col2 = st.columns([1,1])
                with _inv_col1:
                    _invest_amt = st.number_input(
                        "投入金額（台幣 NT$）", min_value=10000, max_value=10000000,
                        value=500000, step=10000, key="iss_invest_amt",
                        help="計算每月配息，用於投入衛星資產")
                with _inv_col2:
                    _exch_rate = st.number_input(
                        f"匯率（{_currency}/TWD）", min_value=1.0, max_value=200.0,
                        value=32.0 if _currency=="USD" else 1.0, step=0.1,
                        key="iss_exch_rate")
                if _nav > 0 and _exch_rate > 0:
                    # v10.5: 雙模式 — 新購試算 vs 現有持倉
                    _calc_mode = st.radio(
                        "試算模式", ["🛒 新購試算（台幣投入→自動換算單位）", "📦 現有持倉（已持有單位→匯率影響配息）"],
                        horizontal=True, key="iss_mode",
                        help="新購試算：台幣/匯率/淨值三者決定單位數，最終台幣配息不受匯率影響（數學抵消）\n現有持倉：已知單位數固定，匯率直接影響台幣配息金額")

                    if "新購" in _calc_mode:
                        # 新購模式：完整四步驟計算
                        _usd_amt     = _invest_amt / _exch_rate          # ① TWD → USD
                        _units       = _usd_amt / _nav                   # ② USD / 淨值 = 單位數
                        _monthly_fx  = _mdr * _units                     # ③ 單位數 × 每單位月配息 = USD配息
                        _monthly_twd = _monthly_fx * _exch_rate          # ④ USD配息 × 匯率 = TWD配息
                        _annual_twd  = _monthly_twd * 12
                        st.markdown(
                            f"<div style='background:#0d2818;border:1px solid #00c853;"
                            f"border-radius:10px;padding:14px;margin:8px 0'>"
                            f"<div style='color:#00c853;font-weight:700;font-size:14px;margin-bottom:8px'>"
                            f"📊 投入 NT${_invest_amt:,.0f} 試算結果（新購試算）</div>"
                            f"<div style='background:#071a0e;border-radius:8px;padding:10px;margin-bottom:10px;"
                            f"font-size:12px;color:#9e9e9e;line-height:2.0'>"
                            f"<span style='color:#69f0ae;font-weight:bold'>計算步驟：</span><br>"
                            f"① NT${_invest_amt:,.0f} ÷ <b style='color:#fff'>{_exch_rate:.2f}</b>"
                            f" = <b style='color:#64b5f6'>{_usd_amt:,.2f} {_currency}</b>"
                            f"<span style='color:#555'>（台幣換外幣）</span><br>"
                            f"② {_usd_amt:,.2f} {_currency} ÷ <b style='color:#fff'>{_nav:.4f}</b>（淨值）"
                            f" = <b style='color:#64b5f6'>{_units:,.2f} 單位</b><br>"
                            f"③ {_units:,.2f} × <b style='color:#fff'>{_mdr:.4f}</b>（每單位月配息）"
                            f" = <b style='color:#64b5f6'>{_monthly_fx:,.4f} {_currency}</b><br>"
                            f"④ {_monthly_fx:,.4f} × <b style='color:#fff'>{_exch_rate:.2f}</b>（匯率）"
                            f" = <b style='color:#00c853;font-size:14px'>NT${_monthly_twd:,.0f} / 月</b>"
                            f"</div>"
                            f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:8px'>"
                            f"<div style='text-align:center'><div style='color:#888;font-size:11px'>持有單位數</div>"
                            f"<div style='color:#e6edf3;font-size:18px;font-weight:900'>{_units:,.2f}</div></div>"
                            f"<div style='text-align:center'><div style='color:#888;font-size:11px'>每月配息（台幣）</div>"
                            f"<div style='color:#00c853;font-size:18px;font-weight:900'>NT${_monthly_twd:,.0f}</div></div>"
                            f"<div style='text-align:center'><div style='color:#888;font-size:11px'>年化配息（台幣）</div>"
                            f"<div style='color:#00c853;font-size:18px;font-weight:900'>NT${_annual_twd:,.0f}</div></div>"
                            f"</div>"
                            f"<div style='color:#444;font-size:11px;padding-top:6px;border-top:1px solid #1a3a28'>"
                            f"ℹ️ 新購模式下台幣配息不受匯率影響（①④互相抵消）。"
                            f"若需觀察匯率變動效果，請切換至「現有持倉」模式。</div>"
                            f"<div style='margin-top:8px;color:#888;font-size:12px'>"
                            f"💡 每月 NT${_monthly_twd:,.0f} 可定期定額投入衛星資產（AI/半導體/區域成長基金）</div>"
                            f"</div>", unsafe_allow_html=True)
                    else:
                        # 現有持倉模式：units fixed, exchange rate directly affects TWD dividend
                        _held_units = st.number_input(
                            "已持有單位數", min_value=0.01, max_value=9999999.0,
                            value=round((_invest_amt / _exch_rate) / _nav, 2) if _nav > 0 else 100.0,
                            step=1.0, key="iss_held_units",
                            help="輸入您實際持有的基金單位數（申購確認書上的單位數）")
                        _monthly_usd = _mdr * _held_units        # 外幣月配息
                        _monthly_twd = _monthly_usd * _exch_rate  # 台幣月配息（匯率真正生效！）
                        _annual_twd  = _monthly_twd * 12
                        _market_val  = _held_units * _nav * _exch_rate  # 目前市值（台幣）
                        st.markdown(
                            f"<div style='background:#0d2818;border:1px solid #00c853;"
                            f"border-radius:10px;padding:14px;margin:8px 0'>"
                            f"<div style='color:#00c853;font-weight:700;font-size:14px;margin-bottom:6px'>"
                            f"📦 現有持倉 {_held_units:,.2f} 單位 試算結果</div>"
                            f"<div style='color:#555;font-size:11px;margin-bottom:10px'>"
                            f"✅ 持倉模式：匯率直接影響台幣配息（強美元→更多台幣配息）</div>"
                            f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:10px'>"
                            f"<div style='text-align:center'>"
                            f"<div style='color:#888;font-size:11px'>目前市值（台幣）</div>"
                            f"<div style='color:#64b5f6;font-size:16px;font-weight:900'>NT${_market_val:,.0f}</div>"
                            f"</div>"
                            f"<div style='text-align:center'>"
                            f"<div style='color:#888;font-size:11px'>月配息（{_currency}）</div>"
                            f"<div style='color:#e6edf3;font-size:16px;font-weight:900'>{_monthly_usd:,.2f}</div>"
                            f"</div>"
                            f"<div style='text-align:center'>"
                            f"<div style='color:#888;font-size:11px'>每月配息（台幣）</div>"
                            f"<div style='color:#00c853;font-size:18px;font-weight:900'>NT${_monthly_twd:,.0f}</div>"
                            f"</div>"
                            f"<div style='text-align:center'>"
                            f"<div style='color:#888;font-size:11px'>年化配息（台幣）</div>"
                            f"<div style='color:#00c853;font-size:18px;font-weight:900'>NT${_annual_twd:,.0f}</div>"
                            f"</div>"
                            f"</div>"
                            f"<div style='margin-top:10px;padding-top:10px;border-top:1px solid #1a3a28;color:#888;font-size:12px'>"
                            f"💡 每月 NT${_monthly_twd:,.0f} 可定期定額投入衛星資產（AI/半導體/區域成長基金）"
                            f"</div>"
                            f"</div>", unsafe_allow_html=True)

            # ── v16.0 T4a: 真實報酬率（含匯率波動還原）──────────────────
            # 公式：真實報酬率 = (1 + 基金原幣報酬率) × (1 + 匯率變動率) - 1
            _fx_section_shown = False
            if _currency and _currency != "TWD" and _currency != "":
                st.markdown("### 💱 真實報酬率（匯率還原）")
                st.caption(
                    "公式：**真實報酬率 = (1 + 基金原幣報酬率) × (1 + 匯率變動率) − 1**　"
                    "外幣基金必須加計匯率損益才能反映真實台幣報酬")
                _fc1, _fc2 = st.columns([1, 1])
                with _fc1:
                    _fx_change_pct = st.number_input(
                        f"匯率變動率（{_currency}/TWD，正值=外幣升值＝對你有利）",
                        min_value=-30.0, max_value=30.0, value=0.0, step=0.1,
                        key="t4_fx_change", help="例：USD/TWD 今年從 30 升至 32 → 輸入 +6.7%")
                with _fc2:
                    _fund_ret_pct = st.number_input(
                        f"基金原幣報酬率（{_currency}計價，%）",
                        min_value=-100.0, max_value=200.0,
                        value=float(_total_ret) if _has_ret1y else 0.0,
                        step=0.1, key="t4_fund_ret",
                        help="從 MoneyDJ wb01 含息報酬率（原幣計）")
                # Fix: 避免除以零；使用乘法公式而非加法近似
                _true_ret = round(
                    (1 + _fund_ret_pct / 100) * (1 + _fx_change_pct / 100) - 1, 4) * 100
                _approx_ret = _fund_ret_pct + _fx_change_pct  # 加法近似（錯誤示範）
                _diff = round(_true_ret - _approx_ret, 2)
                _tr_c = "#00c853" if _true_ret >= 0 else "#f44336"
                st.markdown(
                    f"<div style='background:#0d1117;border:1px solid #30363d;"
                    f"border-radius:10px;padding:14px;margin-top:8px'>"
                    f"<div style='display:flex;gap:20px;align-items:center'>"
                    f"<div style='text-align:center'>"
                    f"<div style='font-size:10px;color:#888'>真實台幣報酬率</div>"
                    f"<div style='font-size:30px;font-weight:900;color:{_tr_c}'>{_true_ret:+.2f}%</div>"
                    f"<div style='font-size:10px;color:#555'>(1+{_fund_ret_pct:.1f}%)×(1+{_fx_change_pct:.1f}%)−1</div>"
                    f"</div>"
                    f"<div style='flex:1'>"
                    f"<div style='font-size:11px;color:#888'>vs 加法近似（不正確）：{_approx_ret:+.2f}%</div>"
                    f"<div style='font-size:11px;color:#555'>差異：{_diff:+.2f}%（匯率複利效果）</div>"
                    f"{'<div style=color:#f44336;font-size:12px;margin-top:4px>⚠️ 匯率侵蝕：真實報酬低於原幣報酬！</div>' if _true_ret < _fund_ret_pct - 0.5 else ''}"
                    f"{'<div style=color:#00c853;font-size:12px;margin-top:4px>✅ 匯率加分：外幣升值提升台幣報酬</div>' if _true_ret > _fund_ret_pct + 0.5 else ''}"
                    f"</div></div></div>",
                    unsafe_allow_html=True)
                _fx_section_shown = True

            # ③ 核心/衛星分類
            _fund_name_for_role = (_mj.get('fund_name') or _mj.get('category') or fd.get('full_key') or '')
            _is_core = assign_asset_role(_fund_name_for_role) == "core"
            _role_c  = "#64b5f6" if _is_core else "#ff9800"
            _role_l  = "核心資產 🛡️（建議持有 70-80%）" if _is_core else "衛星資產 ⚡（建議持有 20-30%）"
            _role_note = "穩定配息 + 低波動 → 適合作為現金流基礎" if _is_core else "成長潛力 + 較高波動 → 用核心配息養這裡"
            st.markdown(
                f"<div style='background:#161b22;border-left:4px solid {_role_c};"
                f"border-radius:0 8px 8px 0;padding:10px 14px;margin:6px 0'>"
                f"<span style='color:{_role_c};font-weight:700'>{_role_l}</span><br>"
                f"<span style='color:#888;font-size:12px'>{_role_note}</span>"
                f"</div>", unsafe_allow_html=True)

            # ④ 核心/衛星判斷補充說明
            if _eat_principal:
                st.warning(
                    f"⚠️ 注意：此基金含息總報酬（{_total_ret:.2f}%）低於配息率（{_adr:.2f}%），"
                    f"作為「核心資產」時需特別留意長期本金保全。")



        with sub2:
            mj_raw2 = fd.get("moneydj_raw", {})
            holdings_data = mj_raw2.get("holdings", {})
            # 傳入基金基本資料一起顯示
            _render_fund_structure(holdings_data, mj_raw2)


with tab2:
    st.markdown("## 📊 我的投資組合")
    st.caption("從宏觀到持倉：管理所有基金 ‧ 現金流監控 ‧ 再平衡提醒")

    # ── Session state ─────────────────────────────────────
    if "portfolio_funds" not in st.session_state:
        st.session_state.portfolio_funds = []
    if "portfolio_core_pct" not in st.session_state:
        st.session_state.portfolio_core_pct = 75  # 核心目標%
    if "portfolio_sat_pct" not in st.session_state:
        st.session_state.portfolio_sat_pct = 25   # 衛星目標%

    # ════════════════════════════════════════════════════
    # 以息養股子Tab: 組合比較 / 現金流管理 / 再平衡
    # ════════════════════════════════════════════════════
    pt1, pt2 = st.tabs(["📊 組合管理", "💰 現金流 & 再平衡"])

    # ═══════════════════════════
    # PT1: 組合比較分析
    # ═══════════════════════════
    with pt1:
        # ══ Hero: 核心/衛星即時配置狀態 ════════════════════════
        _pf_hero = [f for f in st.session_state.get("portfolio_funds",[]) if f.get("loaded")]
        if _pf_hero:
            _tot_h   = sum(f.get("invest_twd",0) or 0 for f in _pf_hero)
            _core_h  = sum(f.get("invest_twd",0) or 0 for f in _pf_hero if f.get("is_core"))
            _sat_h   = _tot_h - _core_h
            _core_pct_h = round(_core_h/_tot_h*100,1) if _tot_h else 0
            _target_h   = st.session_state.get("portfolio_core_pct",80)
            _diff_h     = round(_core_pct_h - _target_h, 1)
            _eat_h = sum(1 for f in _pf_hero
                         if (f.get("moneydj_raw",{}).get("perf",{}).get("1Y") or 0) <
                            (f.get("moneydj_raw",{}).get("moneydj_div_yield") or
                             f.get("metrics",{}).get("annual_div_rate",0) or 0))
            _dc_h = "#f44336" if abs(_diff_h)>10 else ("#ff9800" if abs(_diff_h)>5 else "#00c853")
            _eat_blk = (
                f"<div style='flex:1;min-width:100px'>"
                f"<div style='font-size:11px;color:#f44336;margin-bottom:3px'>🔴 吃本金警示</div>"
                f"<div style='font-size:28px;font-weight:900;color:#f44336'>{_eat_h} 檔</div>"
                f"<div style='font-size:11px;color:#888'>含息報酬&lt;配息率</div></div>"
            ) if _eat_h > 0 else ""
            st.markdown(
                f"<div style='background:linear-gradient(135deg,#0d1b2a,#1a2332);"
                f"border-radius:14px;padding:18px 22px;margin-bottom:16px;border:1px solid #30363d'>"
                f"<div style='font-size:13px;color:#888;margin-bottom:10px'>"
                f"📊 目前投資組合 — {len(_pf_hero)} 檔"
                + (f" · NT${_tot_h:,.0f}" if _tot_h else "") +
                f"</div><div style='display:flex;gap:20px;flex-wrap:wrap'>"
                f"<div style='flex:1;min-width:100px'>"
                f"<div style='font-size:11px;color:#64b5f6;margin-bottom:3px'>🛡️ 核心資產</div>"
                f"<div style='font-size:28px;font-weight:900;color:#64b5f6'>{_core_pct_h}%</div>"
                f"<div style='font-size:11px;color:#888'>"
                + (f"NT${_core_h:,.0f}" if _tot_h else "請填入投入金額") +
                f"</div></div>"
                f"<div style='flex:1;min-width:100px'>"
                f"<div style='font-size:11px;color:#ff9800;margin-bottom:3px'>⚡ 衛星資產</div>"
                f"<div style='font-size:28px;font-weight:900;color:#ff9800'>{100-_core_pct_h:.1f}%</div>"
                f"<div style='font-size:11px;color:#888'>" + (f"NT${_sat_h:,.0f}" if _tot_h else "") +
                f"</div></div>"
                f"<div style='flex:1;min-width:100px'>"
                f"<div style='font-size:11px;color:{_dc_h};margin-bottom:3px'>目標偏移</div>"
                f"<div style='font-size:28px;font-weight:900;color:{_dc_h}'>"
                f"{'+' if _diff_h>=0 else ''}{_diff_h}%</div>"
                f"<div style='font-size:11px;color:#888'>目標 {_target_h}%</div></div>"
                f"{_eat_blk}</div></div>", unsafe_allow_html=True)
            if _eat_h:
                st.error(f"🔴 **吃本金警示**：{_eat_h} 檔基金的含息總報酬 低於 配息率，正在侵蝕本金！")
        st.markdown("### ➕ 加入基金")

        col_p1, col_p2 = st.columns([5, 1])
        with col_p1:
            p_input = st.text_input(
                "基金代碼", placeholder="輸入代碼（TLZF9）或 MoneyDJ URL",
                label_visibility="collapsed", key="portfolio_input")
        with col_p2:
            add_clicked = st.button("➕ 加入", key="btn_add_portfolio", use_container_width=True)

        if add_clicked and p_input.strip():
            import re as _re3
            code_raw = p_input.strip()
            m3 = _re3.search(r"[?&][aA]=([A-Z0-9]{3,25})", code_raw, _re3.I)
            code_clean = m3.group(1).upper() if m3 else code_raw.upper()
            if code_clean and not any(f["code"] == code_clean for f in st.session_state.portfolio_funds):
                st.session_state.portfolio_funds.append({
                    "code": code_clean, "name": code_clean,
                    "metrics": {}, "holdings": {}, "risk_metrics": {},
                    "region": "", "category": "", "loaded": False,
                    "is_core": None, "invest_twd": 0,
                })
                st.rerun()

        pf = st.session_state.portfolio_funds
        if not pf:
            st.info("💡 請在上方輸入基金代碼加入，支援多檔同時比較")
        else:
            # 一鍵全部載入
            not_loaded = [i for i, f in enumerate(pf) if not f.get("loaded")]
            if not_loaded:
                if st.button(f"📡 一鍵載入全部未載入基金（{len(not_loaded)} 檔）",
                             key="btn_load_all", use_container_width=True):
                    prog = st.progress(0, text="載入中...")
                    _errors = []
                    for cnt, i in enumerate(not_loaded):
                        pf_item = st.session_state.portfolio_funds[i]
                        prog.progress((cnt+1)/len(not_loaded),
                                      text=f"載入 {pf_item['code']} （{cnt+1}/{len(not_loaded)}）")
                        try:
                            from fund_fetcher import fetch_fund_from_moneydj_url
                            pf_raw = fetch_fund_from_moneydj_url(pf_item["code"])
                            _pf_series_chk = pf_raw.get("series")
                            _pf_has_series = (_pf_series_chk is not None
                                              and hasattr(_pf_series_chk, "__len__")
                                              and len(_pf_series_chk) > 0)
                            if pf_raw.get("error") and not _pf_has_series:
                                _errors.append(f"{pf_item['code']}: {pf_raw['error']}")
                                st.session_state.portfolio_funds[i]["loaded"] = True
                                st.session_state.portfolio_funds[i]["load_error"] = pf_raw["error"]
                                continue
                            m_data = pf_raw.get("metrics", {})
                            adr = m_data.get("annual_div_rate", 0) or 0
                            try: adr = float(adr)
                            except (ValueError, TypeError): adr = 0.0
                            maxdd = abs(m_data.get("max_drawdown", 0) or 0)
                            st.session_state.portfolio_funds[i].update({
                                "name": pf_raw.get("fund_name") or pf_item["code"],
                                "metrics": m_data,
                                "holdings": pf_raw.get("holdings", {}),
                                "risk_metrics": pf_raw.get("risk_metrics", {}),
                                "moneydj_raw": pf_raw,
                                "region": pf_raw.get("fund_region", ""),
                                "category": pf_raw.get("investment_target", ""),
                                "currency": pf_raw.get("currency", "USD"),
                                "is_core": assign_asset_role(pf_raw.get("fund_name") or pf_item.get("code","")),
                                "loaded": True,
                                "load_error": None,
                            })
                        except Exception as _le:
                            _errors.append(f"{pf_item['code']}: {str(_le)[:80]}")
                            st.session_state.portfolio_funds[i]["loaded"] = True
                            st.session_state.portfolio_funds[i]["load_error"] = str(_le)[:80]
                    prog.empty()
                    if _errors:
                        st.warning("部分基金載入失敗：\n" + "\n".join(_errors))
                    st.rerun()

            # 基金列表
            st.markdown(f"**已加入 {len(pf)} 檔基金**")
            for i, pf_item in enumerate(pf):
                status_icon = "✅" if pf_item.get("loaded") else "⏳"
                m_i = pf_item.get("metrics", {})
                _mj_i = pf_item.get("moneydj_raw", {})
                _dy_i = _mj_i.get("moneydj_div_yield")
                try: _dy_i = float(_dy_i) if _dy_i is not None else None
                except (ValueError, TypeError): _dy_i = None
                adr_i = _dy_i if (_dy_i and _dy_i > 0) else m_i.get("annual_div_rate", 0)
                try: adr_i = float(adr_i)
                except (ValueError, TypeError): adr_i = 0.0
                rm_i  = pf_item.get("risk_metrics", {})
                std_i = rm_i.get("risk_table",{}).get("一年",{}).get("標準差","")
                shp_i = rm_i.get("risk_table",{}).get("一年",{}).get("Sharpe","")
                role_i = "🛡️核心" if pf_item.get("is_core") else ("⚡衛星" if pf_item.get("is_core") is False else "")

                # v13.5: 依 status 三態顯示，有 Sharpe/名稱就不應顯示紅色全失敗
                _raw_i     = pf_item.get("moneydj_raw") or {}
                _status_i  = _raw_i.get("status") or classify_fetch_status(_raw_i)
                load_err_i = pf_item.get("load_error") or _raw_i.get("error")
                _warn_i    = _raw_i.get("warning") or pf_item.get("warning")

                # 若 status 是 partial/complete，清掉「全失敗」紅字
                if _status_i in ("complete", "partial"):
                    if load_err_i and ("所有來源" in load_err_i or load_err_i.startswith("❌")):
                        load_err_i = None
                    if not load_err_i and _warn_i:
                        load_err_i = _warn_i   # 改用 warning 文字（黃色）

                _err_short = (load_err_i or "").split("\n")[0][:40] if load_err_i else ""
                _is_partial = (_status_i == "partial") or ("部分" in (load_err_i or ""))
                err_tag = (
                    f"<span style='color:#ff9800;font-size:10px'> ⚠️{_err_short}</span>"
                    if _is_partial else
                    f"<span style='color:#f44336;font-size:10px'> ❌{_err_short}</span>"
                ) if _err_short else ""
                row_html = (
                    f"<div style='background:#161b22;border-radius:8px;padding:8px 12px;margin:3px 0'>"
                    f"<span style='font-size:14px'>{status_icon}</span> "
                    f"<b style='color:#e6edf3'>{(pf_item.get('name','') or pf_item['code'])[:28]}</b> "
                    f"<span style='color:#888;font-size:11px'>{pf_item['code']}</span> "
                    + (f"<span style='color:#64b5f6;font-size:11px'>{role_i}</span> " if role_i else "")
                    + (f"<span style='color:#ff9800;font-size:11px'>配息{adr_i:.1f}%</span> " if adr_i else "")
                    + (f"<span style='color:#888;font-size:11px'>σ={std_i}%</span> " if std_i else "")
                    + (f"<span style='color:#00c853;font-size:11px'>Sharpe={shp_i}</span>" if shp_i else "")
                    + err_tag
                    + f"</div>"
                )
                col_r, col_load, col_del = st.columns([6, 1, 1])
                with col_r:
                    st.markdown(row_html, unsafe_allow_html=True)
                with col_load:
                    if not pf_item.get("loaded"):
                        if st.button("📡", key=f"btn_pf_{i}", help=f"載入 {pf_item['code']}"):
                            with st.spinner(f"載入 {pf_item['code']}..."):
                                from fund_fetcher import fetch_fund_from_moneydj_url
                                pf_raw  = fetch_fund_from_moneydj_url(pf_item["code"])
                                _s_chk  = pf_raw.get("series")
                                _s_ok   = _s_chk is not None and hasattr(_s_chk,"__len__") and len(_s_chk)>0
                                # v13.5: normalize 後再判斷是否記錄 load_error
                                from fund_fetcher import normalize_result_state, classify_fetch_status
                                pf_raw = normalize_result_state(pf_raw)
                                _pf_status = pf_raw.get("status", "failed")
                                if _pf_status == "failed":
                                    st.session_state.portfolio_funds[i]["load_error"] = str(pf_raw.get("error",""))[:60]
                                elif _pf_status == "partial":
                                    st.session_state.portfolio_funds[i]["load_error"] = str(pf_raw.get("warning",""))[:60]
                                else:
                                    st.session_state.portfolio_funds[i]["load_error"] = None
                                m_data  = pf_raw.get("metrics", {})
                                adr     = m_data.get("annual_div_rate", 0) or 0
                                try: adr = float(adr)
                                except (ValueError, TypeError): adr = 0.0
                                maxdd   = abs(m_data.get("max_drawdown", 0) or 0)
                                st.session_state.portfolio_funds[i].update({
                                    "name": pf_raw.get("fund_name") or pf_item["code"],
                                    "metrics": m_data,
                                    "holdings": pf_raw.get("holdings", {}),
                                    "risk_metrics": pf_raw.get("risk_metrics", {}),
                                    "moneydj_raw": pf_raw,
                                    "region": pf_raw.get("fund_region",""),
                                    "category": pf_raw.get("investment_target",""),
                                    "currency": pf_raw.get("currency","USD"),
                                    "is_core": assign_asset_role(pf_raw.get("fund_name") or pf_item.get("code","")),
                                    "loaded": True,
                                })
                                st.rerun()
                with col_del:
                    if st.button("🗑️", key=f"btn_pdel_{i}"):
                        st.session_state.portfolio_funds.pop(i); st.rerun()
                # v10.7.1：若有嚴重錯誤（非部分），展示操作指引
                _full_err = (pf_item.get("moneydj_raw") or {}).get("error","")
                _exp_status = (pf_item.get("moneydj_raw") or {}).get("status", "")
                # v13.5: 只有真正 failed 才展開錯誤，partial 不展開全失敗訊息
                _show_expander = (
                    _full_err and
                    pf_item.get("loaded") and
                    _exp_status == "failed" and
                    "所有來源均無法" in _full_err
                )
                if _show_expander:
                    with st.expander(f"⚠️ {pf_item.get('name',pf_item['code'])} 資料問題 & 解決方案"):
                        st.error(_full_err)
                        # v13.5: source_trace 顯示哪個來源成功/失敗
                        _trace = (pf_item.get("moneydj_raw") or {}).get("source_trace", [])
                        if _trace:
                            st.markdown("**📡 來源追蹤：**")
                            for _t in _trace:
                                _icon = "✅" if _t.get("success") else "❌"
                                _te = f" ({_t['error'][:30]})" if _t.get("error") else ""
                                st.markdown(f"- {_icon} `{_t.get('source','?')}`{_te}")
                        st.markdown("""
**🔧 快速排查步驟：**
1. **換 IP（最有效）**：Colab → 執行階段 → 變更執行階段類型 → GPU/CPU 切換後重連
2. **確認代碼格式**：境外基金請使用 MoneyDJ 代碼（如 `TLZF9`）
3. **嘗試 TCB 連結**：改用 `https://tcbbankfund.moneydj.com/funddj/ya/yp010000.djhtm?a=代碼`
4. **已有部分資料者**：可繼續使用，僅缺少淨值走勢圖
                        """)

            st.divider()

            # 統計
            loaded_funds = [f for f in pf if f.get("loaded")]
            c1, c2, c3 = st.columns(3)
            c1.metric("📂 總基金數", len(pf))
            c2.metric("✅ 已載入", len(loaded_funds))
            c3.metric("⏳ 待載入", len(pf) - len(loaded_funds))

            # ── v10.7 手動角色調整（data_editor）────────────────
            if loaded_funds:
                st.markdown("#### 🎛️ v10.7 手動調整基金角色（核心/衛星）")
                st.caption("系統已依名稱關鍵字自動分類。如需覆蓋，請直接勾選「核心資產」欄位。")
                import pandas as _pd_role
                _role_df = _pd_role.DataFrame([
                    {
                        "代碼": f.get("code",""),
                        "基金名稱": (f.get("name","") or f.get("code",""))[:22],
                        "核心資產 🛡️": bool(f.get("is_core", False)),
                        "自動分類依據": "🛡️核心（關鍵字）" if f.get("is_core") else "⚡衛星（關鍵字）",
                    }
                    for f in loaded_funds
                ])
                _edited_df = st.data_editor(
                    _role_df,
                    column_config={
                        "代碼":          st.column_config.TextColumn(disabled=True),
                        "基金名稱":      st.column_config.TextColumn(disabled=True),
                        "核心資產 🛡️":  st.column_config.CheckboxColumn(
                            help="勾選=核心資產🛡️（穩定領息），取消=衛星資產⚡（成長/波動）"
                        ),
                        "自動分類依據":  st.column_config.TextColumn(disabled=True),
                    },
                    hide_index=True,
                    use_container_width=True,
                    key="role_editor",
                )
                # 將使用者修改同步回 session_state
                for _idx, _row in _edited_df.iterrows():
                    _code = _row["代碼"]
                    for _fi, _pf_item in enumerate(st.session_state.portfolio_funds):
                        if _pf_item.get("code") == _code and _pf_item.get("loaded"):
                            st.session_state.portfolio_funds[_fi]["is_core"] = bool(_row["核心資產 🛡️"])
                # 重新讀取（已更新）
                loaded_funds = [f for f in st.session_state.portfolio_funds if f.get("loaded")]

            # ══════════════════════════════════════════════
            # 完整比較數據表（AI 分析前必須先顯示）
            # ══════════════════════════════════════════════
            if loaded_funds:
                import pandas as _pd3
                import numpy as _np3

                # ── 分析區塊（展開式） ─────────────────────────────
                _at1 = st.expander("📋 基本比較 & 健康診斷", expanded=True)
                _at2 = st.expander("📈 績效熱點圖 & σ 買賣訊號", expanded=False)
                _at3 = st.expander("🔗 相關係數矩陣（分散風險）", expanded=False)
                _at4 = st.expander("🥧 資產配置分析", expanded=False)

                # ════════════════════════════════════
                # AT1: 基本比較（原有表格 + 幣別分佈 + 同組排名）
                # ════════════════════════════════════
                with _at1:
                    st.markdown("#### 🏷️ 基本資料 & 分類")
                    _basic_rows = []
                    for _pf in loaded_funds:
                        _mj_p = _pf.get("moneydj_raw", {})
                        _m_p  = _pf.get("metrics", {})
                        _rm_p = _mj_p.get("risk_metrics", {})
                        _rt_p = _rm_p.get("risk_table", {})
                        _yr_p = _rt_p.get("一年", {})
                        _3y_p = _rt_p.get("三年", {})
                        _perf_p = _mj_p.get("perf", {})
                        _tr1 = _perf_p.get("1Y")
                        _tr3 = _perf_p.get("3Y")
                        _adr_p = _m_p.get("annual_div_rate",0) or 0
                        try: _adr_p = float(_adr_p)
                        except (ValueError, TypeError): _adr_p = 0.0
                        _eat = "🔴 吃本金" if (_tr1 is not None and _tr1 < _adr_p and _adr_p>0) else ("✅ 健康" if _tr1 is not None else "—")
                        # v10.4: quartile check per fund
                        _pc_p = _mj_p.get("risk_metrics", {}).get("peer_compare", {}) if isinstance(_mj_p.get("risk_metrics"), dict) else {}
                        _qr_p = _quartile_check(_pc_p, _rt_p)
                        _basic_rows.append({
                            "基金":    (_pf.get("name","") or _pf["code"])[:18],
                            "類型":    (_mj_p.get("investment_target","") or "")[:10],
                            "區域":    (_mj_p.get("fund_region","") or "")[:8],
                            "幣別":    _mj_p.get("currency",""),
                            "角色":    "🛡️核心" if _pf.get("is_core") else "⚡衛星",
                            "風險":    _mj_p.get("risk_level",""),
                            "配息率":  f"{_adr_p:.2f}%",
                            "含息1Y":  f"{_tr1:+.2f}%" if _tr1 is not None else "—",
                            "含息3Y":  f"{_tr3:+.2f}%" if _tr3 is not None else "—",
                            "Sharpe1Y": f"{_yr_p.get('Sharpe','—')}",
                            "Sharpe3Y": f"{_3y_p.get('Sharpe','—')}",
                            "STD1Y%":  f"{_yr_p.get('標準差','—')}",
                            "Beta":    f"{_yr_p.get('Beta','—')}",
                            "β分類":   (
                                "🛡️定海神針(β<0.8)" if str(_yr_p.get("Beta","")).replace(".","").replace("-","").isdigit() and float(_yr_p.get("Beta",1)) < 0.8 else
                                "🚀衝鋒陷陣(β>1.2)" if str(_yr_p.get("Beta","")).replace(".","").replace("-","").isdigit() and float(_yr_p.get("Beta",1)) > 1.2 else
                                "⚖️市場同步" if str(_yr_p.get("Beta","")).replace(".","").replace("-","").isdigit() else "—"
                            ),
                            "回撤%":   f"{_m_p.get('max_drawdown',0):.2f}%" if _m_p.get('max_drawdown') else "—",
                            "健康":    _eat,
                            "四分位":  _qr_p["label"] if _qr_p["quartile"] else "無資料",
                        })
                    _df_basic = _pd3.DataFrame(_basic_rows).set_index("基金")
                    st.dataframe(_df_basic, use_container_width=True)

                    # v10.4 red-flag summary bar (吃本金 + 四分位警告)
                    _redflags = []
                    for _rr in _basic_rows:
                        _fn = _rr["基金"]
                        if "🔴" in _rr["健康"]:
                            _redflags.append(f"🔴 **{_fn}**：吃本金（含息{_rr['含息1Y']} < 配息率{_rr['配息率']}）→ 優先汰換")
                        if "第4四分位" in _rr.get("四分位",""):
                            _redflags.append(f"⚠️ **{_fn}**：Sharpe 後25%（{_rr['四分位']}）→ 跨行轉存至前25%標的")
                    if _redflags:
                        st.error("**🚨 MK 汰弱留強警示**\n\n" + "\n".join(_redflags))

                    # TR vs ADR 警示卡
                    st.markdown("#### ⚠️ 含息報酬率 vs 配息年化率（健康診斷）")
                    # ── 概念說明欄 ────────────────────────────────────────────
                    st.markdown(
                        "<div style='background:#131a0a;border:1px solid #f0a500;border-radius:8px;"
                        "padding:10px 14px;margin-bottom:10px;font-size:11px;color:#ccc'>"
                        "<b style='color:#f0a500'>⚠️ 觀念釐清：</b>"
                        "<span style='color:#ff9800'>配息年化率</span> = 年配息額 ÷ 淨值（只算配出去的錢）"
                        "　｜　"
                        "<span style='color:#69f0ae'>含息報酬率</span> = 淨值漲跌 + 累積配息（真正的總報酬）"
                        "<br/>🔬 <b>真實收益 = 含息報酬率 − 配息年化率</b>　若為負數 → 🔴 吃本金（配息來自本金）"
                        "</div>",
                        unsafe_allow_html=True)
                    _warn_cols = st.columns(min(len(loaded_funds), 4))
                    for _ci, _pf in enumerate(loaded_funds[:4]):
                        _m_p   = _pf.get("metrics", {})
                        _mj_p  = _pf.get("moneydj_raw", {})
                        _perf_p = _mj_p.get("perf", {})
                        _tr1   = _perf_p.get("1Y")
                        _adr_p = _m_p.get("annual_div_rate",0) or 0
                        try: _adr_p = float(_adr_p)
                        except (ValueError, TypeError): _adr_p = 0.0
                        _gain  = round(_tr1 - _adr_p, 2) if _tr1 is not None else None
                        _c     = "#f44336" if (_gain is not None and _gain < 0) else ("#00c853" if _gain is not None else "#888")
                        _lbl   = "🔴 本金配息" if (_gain is not None and _gain<0) else ("✅ 資產成長" if _gain is not None else "⚪ 無資料")
                        _warn_cols[_ci].markdown(
                            f"<div style='background:#0d1117;border:2px solid {_c};border-radius:10px;padding:10px;text-align:center'>"
                            f"<div style='font-size:10px;color:#888'>{(_pf.get('name','') or _pf['code'])[:14]}</div>"
                            f"<div style='color:#888;font-size:9px;margin-top:4px'>📈 含息報酬率 (1Y)</div>"
                            f"<div style='font-size:18px;font-weight:900;color:{_c}'>"
                            f"{'N/A' if _tr1 is None else f'{_tr1:+.2f}%'}</div>"
                            f"<div style='color:#888;font-size:9px;margin-top:4px'>📌 配息年化率</div>"
                            f"<div style='font-size:13px;color:#ff9800;font-weight:700'>{_adr_p:.2f}%</div>"
                            f"<div style='font-size:10px;color:{_c};margin-top:4px'>"
                            f"{'真實收益='+f'{_gain:+.2f}%' if _gain is not None else ''}</div>"
                            f"<div style='font-size:12px;font-weight:700;margin-top:2px'>{_lbl}</div>"
                            f"</div>", unsafe_allow_html=True)

                    # 幣別分佈
                    st.markdown("#### 💱 幣別分佈（匯率風險）")
                    _ccy_map = {}
                    for _pf in loaded_funds:
                        _ccy = _pf.get("moneydj_raw",{}).get("currency","USD") or "USD"
                        _amt = _pf.get("invest_twd",0) or 0
                        _ccy_map[_ccy] = _ccy_map.get(_ccy, 0) + max(_amt, 1)
                    _total_ccy = sum(_ccy_map.values())
                    _ccy_cols = st.columns(len(_ccy_map) or 1)
                    _ccy_colors = {"USD":"#2196f3","TWD":"#00c853","EUR":"#ff9800","JPY":"#9c27b0","AUD":"#f44336"}
                    for _ci2, (_ccy2, _amt2) in enumerate(_ccy_map.items()):
                        _pct2 = round(_amt2/_total_ccy*100,1)
                        _c2   = _ccy_colors.get(_ccy2,"#888")
                        _ccy_cols[_ci2].markdown(
                            f"<div style='background:#0d1117;border:2px solid {_c2};border-radius:10px;padding:10px;text-align:center'>"
                            f"<div style='font-size:14px;font-weight:900;color:{_c2}'>{_ccy2}</div>"
                            f"<div style='font-size:22px;font-weight:900;color:{_c2}'>{_pct2}%</div>"
                            f"</div>", unsafe_allow_html=True)

                    # 同組排名（來自 peer_compare）
                    st.markdown("#### 🏆 同組基金排行（MoneyDJ peer_compare）")
                    for _pf in loaded_funds:
                        _mj_p = _pf.get("moneydj_raw", {})
                        _pc   = _mj_p.get("risk_metrics",{}).get("peer_compare",{})
                        if _pc:
                            st.markdown(f"**{(_pf.get('name','') or _pf['code'])[:20]}**")
                            _pc_rows = []
                            for _pk, _pv in list(_pc.items())[:6]:
                                _row = {"指標": _pk}
                                _row.update(_pv)
                                _pc_rows.append(_row)
                            if _pc_rows:
                                _df_pc = _pd3.DataFrame(_pc_rows).set_index("指標")
                                st.dataframe(_df_pc, use_container_width=True)

                # ════════════════════════════════════
                # AT2: 績效熱點圖 + 標準差σ位置
                # ════════════════════════════════════
                with _at2:
                    st.markdown("#### 🔥 報酬率熱點圖（各期間 %）")
                    _heat_rows = []
                    for _pf in loaded_funds:
                        _m_p   = _pf.get("metrics", {})
                        _perf_p = _pf.get("moneydj_raw",{}).get("perf",{})
                        _heat_rows.append({
                            "基金":    (_pf.get("name","") or _pf["code"])[:16],
                            "1個月":   _m_p.get("ret_1m"),
                            "3個月":   _m_p.get("ret_3m"),
                            "6個月":   _m_p.get("ret_6m"),
                            "1年(含息)": _perf_p.get("1Y") or _m_p.get("ret_1y"),
                            "3年(含息)": _perf_p.get("3Y") or _m_p.get("ret_3y"),
                            "5年(含息)": _perf_p.get("5Y"),
                        })
                    if _heat_rows:
                        _df_heat = _pd3.DataFrame(_heat_rows).set_index("基金")
                        try:
                            import plotly.graph_objects as _pgo
                            _z = _df_heat.values.astype(float)
                            _fig_h = _pgo.Figure(data=_pgo.Heatmap(
                                z=_z, x=list(_df_heat.columns), y=list(_df_heat.index),
                                colorscale="RdYlGn", zmid=0,
                                text=[[f"{v:.1f}%" if v == v else "N/A" for v in row] for row in _z],
                                texttemplate="%{text}", textfont={"size":11},
                                hovertemplate="%{y}<br>%{x}<br>%{text}<extra></extra>"))
                            _fig_h.update_layout(height=max(250, len(loaded_funds)*60),
                                                 paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                                                 font={"color":"#e6edf3"}, margin=dict(l=10,r=10,t=10,b=10))
                            st.plotly_chart(_fig_h, use_container_width=True)
                        except Exception as _he:
                            st.dataframe(_df_heat.fillna("—"), use_container_width=True)

                    st.divider()
                    st.markdown("#### 📍 各基金 MK 標準差σ 位置（買賣訊號）")
                    for _pf in loaded_funds:
                        _m_p  = _pf.get("metrics", {})
                        _nav  = _m_p.get("nav",0) or 0
                        _b1   = _m_p.get("buy1")
                        _b2   = _m_p.get("buy2")
                        _b3   = _m_p.get("buy3")
                        _s1   = _m_p.get("sell1")
                        _s2   = _m_p.get("sell2")
                        _pos  = _m_p.get("pos_label","—")
                        _pos_c = _m_p.get("pos_color","#888")
                        _name = (_pf.get("name","") or _pf["code"])[:18]
                        if _b1 and _s1:
                            st.markdown(
                                f"<div style='background:#0d1117;border-left:4px solid {_pos_c};"
                                f"padding:8px 14px;margin:4px 0;border-radius:0 8px 8px 0'>"
                                f"<span style='font-weight:700;color:#e6edf3'>{_name}</span> "
                                f"<span style='color:{_pos_c};font-weight:700'>{_pos}</span>"
                                f"<div style='font-size:10px;color:#888;margin-top:3px'>"
                                f"買3σ:{_b3} ｜ 買2σ:{_b2} ｜ 買1σ:{_b1} ｜ <b>現價:{_nav}</b> ｜ 停利1:{_s1} ｜ 停利2:{_s2}"
                                f"</div></div>", unsafe_allow_html=True)

                    st.divider()
                    st.markdown("#### 🏦 再平衡試算（目標 vs 現況）")
                    _core_t = st.session_state.get("portfolio_core_pct", 75)
                    _sat_t  = 100 - _core_t
                    _core_funds = [f for f in loaded_funds if f.get("is_core")]
                    _sat_funds  = [f for f in loaded_funds if not f.get("is_core")]
                    _total_inv  = sum(f.get("invest_twd",0) or 0 for f in loaded_funds)
                    if _total_inv > 0:
                        _core_inv = sum(f.get("invest_twd",0) or 0 for f in _core_funds)
                        _sat_inv  = sum(f.get("invest_twd",0) or 0 for f in _sat_funds)
                        _core_act = round(_core_inv/_total_inv*100,1)
                        _sat_act  = round(_sat_inv/_total_inv*100,1)
                        _core_diff = round(_core_act - _core_t,1)
                        _sat_diff  = round(_sat_act - _sat_t,1)
                        _reb_c1, _reb_c2 = st.columns(2)
                        with _reb_c1:
                            _rc = "#f44336" if abs(_core_diff)>5 else "#00c853"
                            st.markdown(
                                f"<div style='background:#0d1117;border:2px solid {_rc};border-radius:10px;padding:12px;text-align:center'>"
                                f"<div style='color:#888;font-size:11px'>🛡️ 核心資產</div>"
                                f"<div style='font-size:20px;font-weight:900;color:{_rc}'>{_core_act}%</div>"
                                f"<div style='font-size:10px;color:#888'>目標 {_core_t}% "
                                f"偏移 {'+' if _core_diff>=0 else ''}{_core_diff}%</div>"
                                f"{'<div style=color:#f44336;font-size:10px>⚠️ 建議再平衡</div>' if abs(_core_diff)>5 else ''}"
                                f"</div>", unsafe_allow_html=True)
                        with _reb_c2:
                            _rs = "#f44336" if abs(_sat_diff)>5 else "#00c853"
                            st.markdown(
                                f"<div style='background:#0d1117;border:2px solid {_rs};border-radius:10px;padding:12px;text-align:center'>"
                                f"<div style='color:#888;font-size:11px'>⚡ 衛星資產</div>"
                                f"<div style='font-size:20px;font-weight:900;color:{_rs}'>{_sat_act}%</div>"
                                f"<div style='font-size:10px;color:#888'>目標 {_sat_t}% "
                                f"偏移 {'+' if _sat_diff>=0 else ''}{_sat_diff}%</div>"
                                f"{'<div style=color:#f44336;font-size:10px>⚠️ 建議再平衡</div>' if abs(_sat_diff)>5 else ''}"
                                f"</div>", unsafe_allow_html=True)
                        # v16.0 T2: 偏離度公式 = |現有比例 − 目標比例|
                        _dev_pct = abs(_core_diff)
                        _dev_formula = abs(round(_core_act/100 - _core_t/100, 4))
                        _need = round(_dev_formula * _total_inv)
                        _from_a = "核心" if _core_diff>0 else "衛星"
                        _to_a   = "衛星" if _core_diff>0 else "核心"
                        if _dev_pct > 10:
                            st.markdown(
                                f"<div style='background:#1a0505;border:2px solid #f44336;"
                                f"border-radius:12px;padding:16px 20px;margin:10px 0'>"
                                f"<div style='color:#f44336;font-size:16px;font-weight:900'>"
                                f"🚨 偏離度 {_dev_formula*100:.1f}% — 必須執行再平衡！</div>"
                                f"<div style='color:#e57373;font-size:11px;margin:4px 0'>"
                                f"公式：|核心 {_core_act:.1f}% − 目標 {_core_t:.1f}%| = {_dev_formula*100:.1f}%</div>"
                                f"<div style='color:#ffcdd2;font-size:13px;margin-top:8px'>"
                                f"⚖️ 從【{_from_a}】贖回約 <b>NT${_need:,.0f}</b>，轉入【{_to_a}】</div>"
                                f"</div>", unsafe_allow_html=True)
                        elif _dev_pct > 5:
                            _direction = "賣出核心" if _core_diff>0 else "買入核心"
                            st.warning(
                                f"⚖️ 再平衡提醒 | 偏離度 {_dev_formula*100:.1f}% (>5%)"
                                f"　→ {_direction} 約 NT${_need:,.0f}")
                    else:
                        st.info("💡 請在「以息養股現金流」頁設定各基金投入金額後，此處自動計算再平衡")

                # ════════════════════════════════════
                # AT3: 相關係數矩陣
                # ════════════════════════════════════
                with _at3:
                    st.markdown("#### 🔗 相關係數分析（分散風險）")
                    st.caption("接近 1 = 同漲同跌（集中風險）｜接近 -1 = 反向（理想分散）｜建議保持 < 0.5")

                    _at3_sub1, _at3_sub2 = st.tabs([
                        "📈 淨值相關係數", "📊 綜合相關性分析"])

                    # ── Build nav series ─────────────────────────────────
                    _nav_series = {}
                    for _pf in loaded_funds:
                        _mj_pf = _pf.get("moneydj_raw", {})
                        _s_mj  = _mj_pf.get("series")
                        _s     = _s_mj if (_s_mj is not None and hasattr(_s_mj,"__len__") and len(_s_mj)>0) else _pf.get("series")
                        if _s is not None and hasattr(_s,"__len__") and len(_s) >= 20:
                            _nav_series[(_pf.get("name","") or _pf["code"])[:14]] = _s

                    # ── Sub1: NAV Correlation Heatmap ────────────────────
                    with _at3_sub1:
                        if len(_nav_series) < 2:
                            if len(loaded_funds) >= 2:
                                st.warning("⚠️ 需要至少 2 檔基金有完整淨值資料（≥20筆）才能計算。")
                            else:
                                st.info("💡 請加入至少 2 檔基金。")
                        if len(_nav_series) >= 2:
                            _df_nav = _pd3.DataFrame(_nav_series).ffill().bfill().dropna(how="all")
                            if len(_df_nav) >= 10:
                                _corr = _df_nav.pct_change().dropna().corr().round(3)
                                try:
                                    import plotly.graph_objects as _pgo2
                                    _n   = list(_corr.columns)
                                    _z2  = _corr.values.tolist()
                                    _txt = [[f"{v:.2f}" for v in row] for row in _z2]
                                    _fig_c = _pgo2.Figure(data=_pgo2.Heatmap(
                                        z=_z2, x=_n, y=_n, colorscale="RdBu_r",
                                        zmin=-1, zmax=1, zmid=0,
                                        text=_txt, texttemplate="%{text}", textfont={"size":13},
                                        hovertemplate="%{y} vs %{x}<br>相關係數：%{text}<extra></extra>"))
                                    _fig_c.update_layout(
                                        height=max(300, len(_n)*80),
                                        paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                                        font={"color":"#e6edf3"}, margin=dict(l=10,r=10,t=10,b=10))
                                    st.plotly_chart(_fig_c, use_container_width=True)
                                    _high_pairs = []; _high_names_c = set()
                                    for _i in range(len(_corr)):
                                        for _j in range(_i+1, len(_corr)):
                                            _v = _corr.iloc[_i, _j]
                                            if _v > 0.7:
                                                _high_pairs.append((_n[_i], _n[_j], _v))
                                                _high_names_c.update([_n[_i], _n[_j]])
                                            elif _v < -0.5:
                                                _high_pairs.append((_n[_i], _n[_j], _v))
                                    for _pa, _pb, _pv in _high_pairs:
                                        _icon = "🔴 高度相關" if _pv > 0.7 else "🟢 負相關（良好分散）"
                                        st.markdown(f"- {_icon}：**{_pa}** × **{_pb}** = {_pv:.2f}")
                                    if len(_high_names_c) >= 3:
                                        st.error("🔴 超過3檔高相關標的！建議增加不同類型/地區的基金分散風險。\n\n"
                                                 "MK 建議：🛡️ 核心A（全球多重資產）+ 🛡️ 核心B（非投等債）+ ⚡ 衛星（主題/區域）")
                                    st.dataframe(_corr, use_container_width=True)
                                except Exception as _ce:
                                    st.dataframe(_corr, use_container_width=True)
                            else:
                                st.info("淨值歷史重疊期間不足，無法計算相關係數")

                    # ── Sub2: Investment Target Overlap ──────────────────
                    with _at3_sub2:
                        # ══════════════════════════════════════════════════
                        # 新手說明：什麼是「綜合相關性分析」？
                        # 相關性越高 = 兩檔基金走勢越接近 = 分散效果越差
                        # 這裡從 4 個角度綜合評分：淨值走勢、持股重疊、類別、地區
                        # 分數 0~1：< 0.4 = 綠（分散好）；0.4~0.7 = 黃（普通）；> 0.7 = 紅（集中）
                        # ══════════════════════════════════════════════════
                        st.info(
                            "📊 **綜合相關性分數 = 淨值走勢×0.4 + 持股重疊×0.3 + 類別相似×0.15 + 地區相似×0.15**\n\n"
                            "分數越低越好（< 0.4 代表分散效果佳）。四個維度缺少資料時，現有維度等比加權。")

                        # ── 1. 收集各基金的類別 & 地區資訊 ─────────────────
                        _type_map = {
                            "股票": "股票型", "債券": "債券型", "平衡": "平衡型",
                            "混合": "混合型", "貨幣": "貨幣型", "REITs": "REITs",
                            "Stock": "股票型", "Bond": "債券型", "Balanced": "平衡型",
                            "收益": "債券/收益", "高收益": "高收益債",
                        }
                        def _get_type(pf):
                            _mj = pf.get("moneydj_raw",{}) or {}
                            _tg = (_mj.get("investment_target","") or "") + " " + (_mj.get("fund_type","") or "")
                            for _kw, _ak in _type_map.items():
                                if _kw.lower() in _tg.lower():
                                    return _ak
                            return "其他"

                        def _get_region(pf):
                            _mj = pf.get("moneydj_raw",{}) or {}
                            _reg = (pf.get("region","") or _mj.get("fund_region","") or
                                    _mj.get("investment_target","") or "")
                            if any(k in _reg for k in ["全球","Global","world","多元","多國"]): return "全球"
                            if any(k in _reg for k in ["美國","美","US","USA"]): return "美國"
                            if any(k in _reg for k in ["亞太","亞洲","Asia"]): return "亞太"
                            if any(k in _reg for k in ["新興","Emerging"]): return "新興市場"
                            if any(k in _reg for k in ["歐洲","Europe"]): return "歐洲"
                            if any(k in _reg for k in ["台灣","台股"]): return "台灣"
                            return "其他"

                        def _get_top10(pf):
                            _mj = pf.get("moneydj_raw",{}) or {}
                            _hold = pf.get("holdings",{}) or _mj.get("holdings",{}) or {}
                            _top = (_hold.get("top_holdings",[]) if isinstance(_hold,dict) else []) or []
                            return set(h.get("name","") for h in _top[:10] if h.get("name"))

                        _fund_meta = []
                        for _pf in loaded_funds:
                            _fund_meta.append({
                                "name": (_pf.get("name","") or _pf["code"])[:14],
                                "code": _pf["code"],
                                "type": _get_type(_pf),
                                "region": _get_region(_pf),
                                "top10": _get_top10(_pf),
                            })

                        # ── 2. 建立淨值相關係數矩陣（複用 sub1 邏輯）────────
                        _corr2 = None
                        if len(_nav_series) >= 2:
                            _df_nav2 = _pd3.DataFrame(_nav_series).ffill().bfill().dropna(how="all")
                            if len(_df_nav2) >= 10:
                                try:
                                    _corr2 = _df_nav2.pct_change().dropna().corr().round(3)
                                except Exception:
                                    _corr2 = None

                        # ── 3. 計算每對基金的綜合相關性分數 ─────────────────
                        _fund_names = [f["name"] for f in _fund_meta]
                        _n_f = len(_fund_meta)
                        import numpy as _np_c
                        _comp_scores = _np_c.zeros((_n_f, _n_f))
                        _detail_rows = []

                        for _i in range(_n_f):
                            for _j in range(_n_f):
                                if _i == _j:
                                    _comp_scores[_i][_j] = 1.0
                                    continue
                                fi, fj = _fund_meta[_i], _fund_meta[_j]
                                # 淨值相關（絕對值）
                                _w_nav, _s_nav = 0, 0
                                if _corr2 is not None and fi["name"] in _corr2.columns and fj["name"] in _corr2.columns:
                                    try:
                                        _s_nav = abs(float(_corr2.loc[fi["name"], fj["name"]]))
                                        _w_nav = 0.4
                                    except Exception:
                                        pass
                                # 持股重疊
                                _w_hold, _s_hold = 0, 0
                                if fi["top10"] and fj["top10"]:
                                    _overlap_n = len(fi["top10"] & fj["top10"])
                                    _s_hold = min(_overlap_n / 10, 1.0)
                                    _w_hold = 0.3
                                # 類別相似
                                _w_type, _s_type = 0.15, (1.0 if fi["type"] == fj["type"] else 0.0)
                                # 地區相似
                                _w_reg, _s_reg = 0.15, (1.0 if fi["region"] == fj["region"] else 0.0)
                                # 綜合加權（有缺失維度時重新歸一）
                                _tot_w = _w_nav + _w_hold + _w_type + _w_reg
                                if _tot_w > 0:
                                    _comp = (_w_nav*_s_nav + _w_hold*_s_hold + _w_type*_s_type + _w_reg*_s_reg) / _tot_w
                                else:
                                    _comp = 0.0
                                _comp_scores[_i][_j] = round(_comp, 3)
                                if _j > _i:
                                    _detail_rows.append({
                                        "基金A": fi["name"], "基金B": fj["name"],
                                        "綜合分": round(_comp, 2),
                                        "淨值相關": f"{_s_nav:.2f}" if _w_nav > 0 else "─",
                                        "持股重疊": f"{_s_hold:.2f}" if _w_hold > 0 else "─",
                                        "類別": "相同" if _s_type == 1 else "不同",
                                        "地區": "相同" if _s_reg == 1 else "不同",
                                        "風險評級": "🔴 高度相關" if _comp > 0.7 else ("🟡 中度相關" if _comp > 0.4 else "🟢 分散良好"),
                                    })

                        # ── 4. 顯示綜合熱力圖 ────────────────────────────
                        if _n_f >= 2:
                            try:
                                import plotly.graph_objects as _pgo_c
                                _z_comp = _comp_scores.tolist()
                                _txt_comp = [[f"{v:.2f}" for v in row] for row in _z_comp]
                                _fig_comp = _pgo_c.Figure(data=_pgo_c.Heatmap(
                                    z=_z_comp, x=_fund_names, y=_fund_names,
                                    colorscale=[[0,"#00c853"],[0.4,"#00c853"],[0.4,"#ff9800"],[0.7,"#ff9800"],[0.7,"#f44336"],[1,"#f44336"]],
                                    zmin=0, zmax=1,
                                    text=_txt_comp, texttemplate="%{text}", textfont={"size":14},
                                    hovertemplate="%{y} vs %{x}<br>綜合相關性：%{text}<extra></extra>"))
                                _fig_comp.update_layout(
                                    title="綜合相關性分數（0=完全不相關，1=高度相關）",
                                    height=max(300, _n_f*90),
                                    paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                                    font={"color":"#e6edf3"}, margin=dict(l=10,r=10,t=40,b=10))
                                st.plotly_chart(_fig_comp, use_container_width=True)
                            except Exception as _ce2:
                                st.warning(f"熱力圖繪製失敗：{_ce2}")
                                try:
                                    _write_error_ledger(_ce2, "heatmap plotly render", GEMINI_KEY)
                                except Exception:
                                    pass
                        else:
                            st.info("💡 需要至少 2 檔基金才能計算相關性")

                        # ── 5. 分項明細表 ────────────────────────────────
                        if _detail_rows:
                            st.markdown("**📋 各基金對分項明細**")
                            _df_detail = _pd3.DataFrame(_detail_rows)
                            # 風險高的排前面
                            _df_detail = _df_detail.sort_values("綜合分", ascending=False)
                            st.dataframe(_df_detail, use_container_width=True, hide_index=True)

                            # 高風險配對警示
                            _high_risk = [r for r in _detail_rows if r["綜合分"] > 0.7]
                            if _high_risk:
                                st.error(f"🔴 發現 {len(_high_risk)} 對高度相關基金！建議替換其中一檔以降低集中風險。\n\n"
                                         "建議方向：核心A（全球多重資產）+ 核心B（非投等債）+ 衛星（主題/區域）")
                            elif any(r["綜合分"] > 0.4 for r in _detail_rows):
                                st.warning("🟡 部分基金有中度相關，建議持續觀察。")
                            else:
                                st.success("🟢 所有基金組合相關性低，分散效果良好！")

                        # ── 6. 類別與地區分佈（整合原 sub3/sub4）────────────
                        st.divider()
                        _cls_c1, _cls_c2 = st.columns(2)
                        with _cls_c1:
                            st.markdown("**🏷️ 類別分佈**")
                            _type_cnt = {}
                            for _fm in _fund_meta:
                                _type_cnt[_fm["type"]] = _type_cnt.get(_fm["type"], 0) + 1
                            for _tp, _cnt in sorted(_type_cnt.items(), key=lambda x: -x[1]):
                                st.markdown(f"- {_tp}：{_cnt} 檔")
                        with _cls_c2:
                            st.markdown("**🌍 地區分佈**")
                            _reg_cnt = {}
                            for _fm in _fund_meta:
                                _reg_cnt[_fm["region"]] = _reg_cnt.get(_fm["region"], 0) + 1
                            for _rg, _cnt in sorted(_reg_cnt.items(), key=lambda x: -x[1]):
                                st.markdown(f"- {_rg}：{_cnt} 檔")

                                # ════════════════════════════════════
                # AT4: 資產配置圓餅圖 + 股債現金分析
                # ════════════════════════════════════
                with _at4:
                    st.markdown("#### 🥧 組合資產配置分析")
                    # Infer asset type from investment_target / category
                    _asset_map = {"股票":"equity","混合":"balanced","債券":"bond",
                                  "貨幣":"cash","REITs":"reit","黃金":"gold",
                                  "Stock":"equity","Bond":"bond","Mixed":"balanced"}
                    _asset_inv = {"equity":0, "bond":0, "balanced":0, "cash":0, "other":0}
                    for _pf in loaded_funds:
                        _mj_p = _pf.get("moneydj_raw",{})
                        _target = (_mj_p.get("investment_target","") or "") + " " + (_mj_p.get("fund_type","") or "")
                        _at_key = "other"
                        for _kw, _ak in _asset_map.items():
                            if _kw.lower() in _target.lower():
                                _at_key = _ak; break
                        _inv_amt = _pf.get("invest_twd",0) or 100  # default weight 1
                        _asset_inv[_at_key] += _inv_amt
                    _asset_labels = {"equity":"股票型","bond":"債券型","balanced":"平衡型","cash":"貨幣型","other":"其他"}
                    _asset_colors = {"equity":"#2196f3","bond":"#ff9800","balanced":"#9c27b0","cash":"#00c853","other":"#888"}
                    _pie_data = [(l, v) for l, v in _asset_inv.items() if v > 0]
                    if _pie_data:
                        try:
                            import plotly.express as _px
                            _pie_df = _pd3.DataFrame({"類別":[_asset_labels[k] for k,v in _pie_data],
                                                      "金額":[v for k,v in _pie_data],
                                                      "顏色":[_asset_colors[k] for k,v in _pie_data]})
                            _fig_pie = _px.pie(_pie_df, values="金額", names="類別",
                                               color_discrete_sequence=[row for row in _pie_df["顏色"]],
                                               hole=0.4)
                            _fig_pie.update_layout(
                                paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                                font={"color":"#e6edf3"}, showlegend=True,
                                margin=dict(l=10,r=10,t=10,b=10))
                            st.plotly_chart(_fig_pie, use_container_width=True)
                        except Exception as _pe:
                            for _k, _v in _pie_data:
                                st.markdown(f"**{_asset_labels[_k]}**: {_v:,.0f}")

                    # ── 合併產業配置（各基金持股合計）──────────────────
                    st.markdown("#### 📊 組合產業分佈（各基金合計）")
                    import pandas as _pd3  # Fix: ensure _pd3 in scope inside _at4
                    _sector_combined = {}
                    _top_holdings_combined = []
                    for _pf_s in loaded_funds:
                        _hold_s = _pf_s.get("holdings", {}) or _pf_s.get("moneydj_raw", {}).get("holdings", {}) or {}
                        _inv_s  = _pf_s.get("invest_twd", 100) or 100
                        _name_s = _pf_s.get("name","") or _pf_s["code"]
                        # sector alloc
                        for _sec in (_hold_s.get("sector_alloc", []) if isinstance(_hold_s, dict) else []):
                            _sname = _sec.get("name","")
                            _spct  = _sec.get("pct", 0) or 0
                            if _sname:
                                _sector_combined[_sname] = _sector_combined.get(_sname, 0) + _spct * _inv_s / 1000000
                        # top holdings
                        for _h in (_hold_s.get("top_holdings", []) if isinstance(_hold_s, dict) else [])[:5]:
                            _top_holdings_combined.append({
                                "基金": _name_s[:10], "持股": _h.get("name","")[:20],
                                "比例": f"{_h.get('pct',0):.1f}%",
                                "產業": _h.get("sector","")[:15],
                            })
                    if _sector_combined:
                        import plotly.express as _px2
                        _sc_sorted = sorted(_sector_combined.items(), key=lambda x: x[1], reverse=True)[:10]
                        _sec_df = _pd3.DataFrame(_sc_sorted, columns=["產業","加權金額"])
                        _fig_s = _px2.bar(_sec_df, x="加權金額", y="產業", orientation="h",
                                           color_discrete_sequence=["#64b5f6"])
                        _fig_s.update_layout(paper_bgcolor="#0d1117", plot_bgcolor="#0d1117",
                                             font={"color":"#e6edf3"}, margin=dict(l=10,r=10,t=10,b=10),
                                             height=280)
                        st.plotly_chart(_fig_s, use_container_width=True)
                    else:
                        st.info("💡 各基金需先載入持股資料（MoneyDJ wb05）才能顯示產業分佈")

                    # ── 各基金前10大持股彙整 ─────────────────────────────
                    if _top_holdings_combined:
                        st.markdown("#### 🔝 各基金前5大持股彙整")
                        st.dataframe(
                            _pd3.DataFrame(_top_holdings_combined),
                            use_container_width=True, hide_index=True)
                    else:
                        st.info("💡 暫無持股明細，請確認各基金已載入")

                    st.markdown("#### 🌍 景氣連動提示")
                    _ph_tab3 = (st.session_state.phase_info
                               if st.session_state.macro_done
                               else {})
                    _phase_n  = _ph_tab3.get("phase","—") if _ph_tab3 else "—"
                    _phase_s  = _ph_tab3.get("score",5)
                    _alloc_now = _ph_tab3.get("allocation",{})
                    _eq_cur = round(_asset_inv["equity"]/max(sum(_asset_inv.values()),1)*100,0) if sum(_asset_inv.values())>0 else 0
                    _bd_cur = round((_asset_inv["bond"]+_asset_inv["balanced"]*0.5)/max(sum(_asset_inv.values()),1)*100,0) if sum(_asset_inv.values())>0 else 0
                    if _alloc_now:
                        _eq_tgt  = _alloc_now.get("股票",_alloc_now.get("equity",50))
                        _bd_tgt  = _alloc_now.get("債券",_alloc_now.get("bond",30))
                        st.info(
                            f"景氣位階：**{_phase_n}（{_phase_s}/10）** → "
                            f"建議股票 **{_eq_tgt}%** / 債券 **{_bd_tgt}%**｜"
                            f"目前估計股票 ~{_eq_cur:.0f}% / 債券 ~{_bd_cur:.0f}%")
                    else:
                        _default_eq = {"衰退":30,"復甦":60,"擴張":70,"高峰":40}.get(_phase_n,50) if _phase_n!="—" else 50
                        _default_bd = 100-_default_eq-10
                        st.warning(
                            f"⚠️ 景氣連動提示：請先執行「🌐 總經位階」分析以取得景氣位階數據。\n\n"
                            f"目前組合估計：股票 ~{_eq_cur:.0f}% / 債券 ~{_bd_cur:.0f}%\n"
                            f"預設參考配置（中性）：股票 {_default_eq}% / 債券 {_default_bd}% / 現金 10%")
# ════════════════════════════════════
                # AI 分析（數據已全部展示，AI 基於上方數據）
                # ════════════════════════════════════
                st.divider()
                st.markdown("### 🤖 AI 組合深度分析")
                st.caption("AI 嚴格基於上方所有數據分析，不自行搜尋外部資料")



    # ═══════════════════════════
    # PT2: 以息養股現金流管理
    # ═══════════════════════════
    with pt2:
        st.markdown("### 💰 以息養股現金流管理")
        st.caption("從兩個角度管理現金流：新資金分配 或 現有持倉再平衡")

        loaded_funds = [f for f in st.session_state.portfolio_funds if f.get("loaded")]
        if not loaded_funds:
            st.info("💡 請先在「組合分析」頁加入並載入基金")
        else:
            # ══════════════════════════════════════
            # 兩種模式 Sub-tabs
            # ══════════════════════════════════════
            _cf_tab1, _cf_tab2 = st.tabs(["🆕 新增資金資產配置", "🔄 現有基金比例調配"])

            # ─── 共用：各基金每月配息率 ─────────────────────
            exch_rates_def = {"USD":32.0,"EUR":35.0,"JPY":0.22,"TWD":1.0,"AUD":21.0}
            _core_funds = [f for f in loaded_funds if f.get("is_core")]
            _sat_funds  = [f for f in loaded_funds if not f.get("is_core")]

            # ════════════════════════════════
            # MODE A: 新增資金做資產配置
            # ════════════════════════════════
            with _cf_tab1:
                st.markdown("#### 🆕 新增資金資產配置建議")
                st.caption("輸入新增資金總額，系統依景氣位階與核心/衛星比例分配")

                _na_c1, _na_c2, _na_c3 = st.columns(3)
                with _na_c1:
                    _new_capital = st.number_input(
                        "新增資金（NT$）", min_value=10000, max_value=100000000,
                        value=500000, step=50000, key="cf_new_capital")
                with _na_c2:
                    _core_target_pct = st.slider(
                        "核心目標比例 %", min_value=0, max_value=100, value=70, step=5,
                        key="cf_core_pct",
                        help="核心資產（🛡）= 穩定配息，適合保守型，如債券型/高股息基金。衛星資產（⚡）= 追求成長，適合積極型，如主題型/科技基金。新手建議：80% 核心 + 20% 衛星。")
                with _na_c3:
                    _usd_twd = st.number_input(
                        "USD/TWD 匯率", min_value=20.0, max_value=50.0,
                        value=32.0, step=0.5, key="cf_usd_twd")

                _sat_target_pct = 100 - _core_target_pct
                st.markdown(
                    f"<div style='font-size:12px;padding:4px 8px;background:#0d1b2a;"
                    f"border-radius:6px;display:inline-block;margin-bottom:4px'>"
                    f"<span style='color:#64b5f6'>🛡️ 核心 {_core_target_pct}%</span>"
                    f" + "
                    f"<span style='color:#ff9800'>⚡ 衛星 {_sat_target_pct}%</span>"
                    f" = <span style='color:#00c853;font-weight:700'>100% ✅</span>"
                    f"</div>", unsafe_allow_html=True)
                _core_capital = _new_capital * _core_target_pct / 100
                _sat_capital  = _new_capital * _sat_target_pct / 100

                # 核心/衛星分配金額
                st.markdown(
                    f"<div style='background:#0d1f30;border:1px solid #1e5f8a;border-radius:12px;"
                    f"padding:16px;margin:10px 0;display:flex;gap:20px;flex-wrap:wrap'>"
                    f"<div style='flex:1;text-align:center'>"
                    f"<div style='color:#888;font-size:11px'>新增資金總額</div>"
                    f"<div style='color:#e6edf3;font-size:22px;font-weight:900'>NT${_new_capital:,.0f}</div>"
                    f"</div>"
                    f"<div style='flex:1;text-align:center'>"
                    f"<div style='color:#888;font-size:11px'>核心資產（{_core_target_pct}%）</div>"
                    f"<div style='color:#64b5f6;font-size:20px;font-weight:900'>NT${_core_capital:,.0f}</div>"
                    f"</div>"
                    f"<div style='flex:1;text-align:center'>"
                    f"<div style='color:#888;font-size:11px'>衛星資產（{_sat_target_pct}%）</div>"
                    f"<div style='color:#ff9800;font-size:20px;font-weight:900'>NT${_sat_capital:,.0f}</div>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True)

                # ── 配置模式選擇 ────────────────────────────────────
                _alloc_mode = st.radio(
                    "分配方式",
                    ["⚖️ 配息率加權（自動）", "✏️ 自訂各檔百分比"],
                    horizontal=True, key="cf_alloc_mode")
                _is_custom_mode = "自訂" in _alloc_mode
                # ── 配置模式說明 ────────────────────────────────────
                if not _is_custom_mode:
                    st.info(
                        "**📚 配息率加權說明**\n\n"
                        "自動模式會依照各基金的「年化配息率」分配新增資金：\n"
                        "• 配息率較高的基金 → 分到較多資金（提升整體現金流）\n"
                        "• 配息率較低的基金 → 分到較少資金\n\n"
                        "⚠️ 注意事項：\n"
                        "• **高配息 ≠ 高報酬**：需確認配息來自「收益」而非「本金」\n"
                        "• 建議搭配「吃本金偵測」確保配息來源健康\n"
                        "• 若想手動指定比例，請選「自訂各檔百分比」")

                # ── 自訂比例輸入 UI（全部基金合計 = 100%）────────
                _custom_pcts = {}   # {code: pct}  各檔佔總資金的比例
                if _is_custom_mode:
                    _all_funds_cf = _core_funds + _sat_funds
                    _n_all = len(_all_funds_cf)
                    _default_pct_all = round(100 / _n_all) if _n_all > 0 else 0

                    st.markdown(
                        "<div style='background:#0d1117;border:1px solid #30363d;"
                        "border-radius:8px;padding:12px;margin:6px 0;font-size:12px;color:#888'>"
                        "💡 輸入各檔基金佔「新增總資金」的百分比，"
                        "<b style='color:#e6edf3'>所有基金合計須等於 100%</b>"
                        "</div>", unsafe_allow_html=True)

                    # 一次顯示全部基金（核心標藍、衛星標橘）
                    _all_cols = st.columns(min(_n_all, 4))
                    _total_pct_sum = 0
                    for _gi, _gf in enumerate(_all_funds_cf):
                        _gcode = _gf["code"]
                        _gname = (_gf.get("name","") or _gcode)[:14]
                        _is_c  = _gf.get("is_core", False)
                        _role_icon = "🛡️" if _is_c else "⚡"
                        _role_clr  = "#64b5f6" if _is_c else "#ff9800"
                        with _all_cols[_gi % min(_n_all, 4)]:
                            _role_reason = "核心（配息穩定，適合長期持有）" if _is_c else "衛星（成長型，適合景氣擴張期）"
                            st.markdown(
                                f"<div style='font-size:10px;color:{_role_clr};"
                                f"margin-bottom:2px'>{_role_icon} {_gname}"
                                f"<span style='color:#555;font-size:9px;margin-left:4px'>({_role_reason})</span>"
                                f"</div>",
                                unsafe_allow_html=True)
                            _p = st.number_input(
                                f"佔比 %",
                                min_value=0, max_value=100,
                                value=_default_pct_all,
                                step=5,
                                key=f"cf_pct_all_{_gcode}",
                                label_visibility="collapsed",
                                help=f"{_gcode} 佔新增總資金的比例")
                            _custom_pcts[_gcode] = _p
                            _total_pct_sum += _p

                    # 合計進度條 + 驗證
                    _bar_clr = "#00c853" if abs(_total_pct_sum - 100) < 1 else (
                               "#ff9800" if _total_pct_sum < 100 else "#f44336")
                    _bar_w   = min(_total_pct_sum, 100)
                    st.markdown(
                        f"<div style='margin-top:10px'>"
                        f"<div style='display:flex;justify-content:space-between;"
                        f"font-size:11px;margin-bottom:4px'>"
                        f"<span style='color:#888'>所有基金合計</span>"
                        f"<span style='color:{_bar_clr};font-weight:700'>{_total_pct_sum}% / 100%</span>"
                        f"</div>"
                        f"<div style='height:8px;background:#1a1f2e;border-radius:4px;overflow:hidden'>"
                        f"<div style='height:100%;width:{_bar_w:.0f}%;background:{_bar_clr};"
                        f"border-radius:4px'></div>"
                        f"</div></div>",
                        unsafe_allow_html=True)
                    if abs(_total_pct_sum - 100) > 0.5:
                        st.warning(f"⚠️ 合計 = **{_total_pct_sum}%**，請調整至 100%（差 {100-_total_pct_sum:+.0f}%）")
                    else:
                        st.success(f"✅ 合計 = 100%，可以開始分配")
                # 逐檔分配
                def _alloc_within_group(funds_list, group_capital, group_label, group_color):
                    # 新手說明：group_capital 僅自動模式使用；自訂模式各檔獨立計算，不受此限制
                    if not funds_list:
                        st.markdown(f"<div style='color:#555;font-size:12px'>無{group_label}基金</div>",
                                    unsafe_allow_html=True)
                        return []
                    if not _is_custom_mode and group_capital <= 0:
                        st.markdown(f"<div style='color:#555;font-size:12px'>無{group_label}基金（分配資金為 0，請調整核心/衛星滑桿）</div>",
                                    unsafe_allow_html=True)
                        return []
                    alloc_rows = []

                    if _is_custom_mode:
                        # ── 自訂比例模式（各檔佔總資金的%）──────────────
                        # _custom_pcts 是每檔佔「新增總資金」的比例
                        # group_capital 仍用於等比邏輯保持相容，但此處直接用 _new_capital
                        _total_custom = sum(_custom_pcts.get(fd["code"],0) for fd in funds_list)
                        if _total_custom <= 0:
                            _total_custom = 1  # fallback
                        for fd_p in funds_list:
                            _code = fd_p["code"]
                            # 此基金的金額 = 總資金 × 該基金的%
                            this_share = _new_capital * (_custom_pcts.get(_code, 0) / 100)
                            m_p   = fd_p.get("metrics",{})
                            nav_p = m_p.get("nav",0) or 0
                            mdr_p = m_p.get("monthly_div",0) or 0
                            adr_p = float(m_p.get("annual_div_rate",0) or 0)
                            curr_p = fd_p.get("currency","USD") or "USD"
                            rate_p = exch_rates_def.get(curr_p, 32.0)
                            if nav_p > 0 and mdr_p > 0 and rate_p > 0:
                                import math as _m
                                units_p = _m.floor((this_share / rate_p) / nav_p)
                                monthly_twd = mdr_p * units_p * rate_p
                            else:
                                monthly_twd = this_share * adr_p / 100 / 12
                            alloc_rows.append({
                                "name": (_code if not fd_p.get("name") else fd_p["name"][:22]),
                                "code": _code,
                                "share": this_share,
                                "pct_label": f"{_custom_pcts.get(_code,0):.0f}%",
                                "monthly_twd": monthly_twd,
                                "adr": adr_p, "currency": curr_p,
                            })
                    else:
                        # ── 配息率加權自動模式（原本邏輯）───────────────
                        weights = []
                        for fd_p in funds_list:
                            m_p = fd_p.get("metrics",{})
                            adr = m_p.get("annual_div_rate",0) or m_p.get("moneydj_div_yield",4) or 4
                            try: adr = float(adr)
                            except (ValueError, TypeError): adr = 0.0
                            weights.append(max(adr, 1.0))
                        total_w = sum(weights)
                        for wi, fd_p in enumerate(funds_list):
                            m_p   = fd_p.get("metrics",{})
                            nav_p = m_p.get("nav",0) or 0
                            mdr_p = m_p.get("monthly_div",0) or 0
                            adr_p = float(m_p.get("annual_div_rate",0) or 0)
                            curr_p = fd_p.get("currency","USD") or "USD"
                            name_p = (fd_p.get("name","") or fd_p["code"])[:22]
                            rate_p = exch_rates_def.get(curr_p, 32.0)
                            this_share = group_capital * (weights[wi]/total_w)
                            if nav_p > 0 and mdr_p > 0 and rate_p > 0:
                                import math as _m
                                units_p = _m.floor((this_share / rate_p) / nav_p)
                                monthly_twd = mdr_p * units_p * rate_p
                            else:
                                monthly_twd = this_share * adr_p / 100 / 12
                            alloc_rows.append({
                                "name": name_p, "code": fd_p["code"],
                                "share": this_share,
                                "pct_label": f"{weights[wi]/total_w*100:.0f}%",
                                "monthly_twd": monthly_twd,
                                "adr": adr_p, "currency": curr_p,
                            })
                    return alloc_rows

                col_core_alloc, col_sat_alloc = st.columns(2)

                with col_core_alloc:
                    st.markdown(f"##### 🛡️ 核心資產分配（{len(_core_funds)} 檔）")
                    _core_alloc = _alloc_within_group(_core_funds, _core_capital, "核心", "#64b5f6")
                    _core_monthly_new = 0
                    for row in _core_alloc:
                        _core_monthly_new += row["monthly_twd"]
                        st.markdown(
                            f"<div style='background:#0d1b2a;border:1px solid #1e3a5f;border-radius:8px;"
                            f"padding:10px 12px;margin:4px 0'>"
                            f"<div style='display:flex;justify-content:space-between'>"
                            f"<div style='color:#64b5f6;font-size:12px;font-weight:600'>{row['name']}</div>"
                            f"<div style='color:#888;font-size:11px'>{row['currency']} | {row['adr']:.1f}% 配息 | 佔比 {row.get('pct_label','')}</div>"
                            f"</div>"
                            f"<div style='display:flex;justify-content:space-between;margin-top:6px'>"
                            f"<div style='color:#e6edf3;font-size:13px;font-weight:700'>"
                            f"NT${row['share']:,.0f}</div>"
                            f"<div style='color:#00c853;font-size:12px'>"
                            f"月配息 NT${row['monthly_twd']:,.0f}</div>"
                            f"</div>"
                            f"</div>",
                            unsafe_allow_html=True)
                    if not _core_alloc:
                        st.info("組合中無核心資產（配息率≥4% 且最大跌幅<20%）")

                with col_sat_alloc:
                    st.markdown(f"##### ⚡ 衛星資產分配（{len(_sat_funds)} 檔）")
                    _sat_alloc = _alloc_within_group(_sat_funds, _sat_capital, "衛星", "#ff9800")
                    _sat_monthly_new = 0
                    for row in _sat_alloc:
                        _sat_monthly_new += row["monthly_twd"]
                        st.markdown(
                            f"<div style='background:#1f1400;border:1px solid #5f3e00;border-radius:8px;"
                            f"padding:10px 12px;margin:4px 0'>"
                            f"<div style='display:flex;justify-content:space-between'>"
                            f"<div style='color:#ff9800;font-size:12px;font-weight:600'>{row['name']}</div>"
                            f"<div style='color:#888;font-size:11px'>{row['currency']} | {row['adr']:.1f}% 配息</div>"
                            f"</div>"
                            f"<div style='display:flex;justify-content:space-between;margin-top:6px'>"
                            f"<div style='color:#e6edf3;font-size:13px;font-weight:700'>"
                            f"NT${row['share']:,.0f}</div>"
                            f"<div style='color:#ff9800;font-size:12px'>"
                            f"月配息 NT${row['monthly_twd']:,.0f}</div>"
                            f"</div>"
                            f"</div>",
                            unsafe_allow_html=True)
                    if not _sat_alloc:
                        st.info("組合中無衛星資產")

                # 新增資金後預期收益總覽
                _total_monthly_new = _core_monthly_new + _sat_monthly_new
                if _total_monthly_new > 0:
                    st.divider()
                    st.markdown(
                        f"<div style='background:#0d2818;border:1px solid #00c853;border-radius:12px;"
                        f"padding:16px;margin:10px 0'>"
                        f"<div style='color:#00c853;font-size:15px;font-weight:700;margin-bottom:12px'>"
                        f"📊 新增 NT${_new_capital:,.0f} 後預期現金流</div>"
                        f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:12px'>"
                        f"<div style='text-align:center'><div style='color:#888;font-size:11px'>每月總配息</div>"
                        f"<div style='color:#00c853;font-size:20px;font-weight:900'>NT${_total_monthly_new:,.0f}</div></div>"
                        f"<div style='text-align:center'><div style='color:#888;font-size:11px'>年化配息</div>"
                        f"<div style='color:#00c853;font-size:18px;font-weight:900'>NT${_total_monthly_new*12:,.0f}</div></div>"
                        f"<div style='text-align:center'><div style='color:#888;font-size:11px'>核心月配息</div>"
                        f"<div style='color:#64b5f6;font-size:18px;font-weight:900'>NT${_core_monthly_new:,.0f}</div></div>"
                        f"<div style='text-align:center'><div style='color:#888;font-size:11px'>衛星養本月撥入</div>"
                        f"<div style='color:#ff9800;font-size:18px;font-weight:900'>NT${_sat_monthly_new:,.0f}</div>"
                        f"<div style='color:#555;font-size:9px'>以核心配息養衛星</div></div>"
                        f"</div></div>",
                        unsafe_allow_html=True)

            # ════════════════════════════════
            # MODE B: 現有基金比例調配建議
            # ════════════════════════════════
            with _cf_tab2:
                st.markdown("#### 🔄 現有基金比例調配建議")
                st.caption("設定各基金現有投入金額，系統計算目前比例並提供再平衡建議")

                # ── Step 1: 設定各基金投入金額 ──────────────────────
                st.markdown("##### 📋 各基金現有持倉設定")
                _total_monthly_twd = 0
                _core_monthly_b = 0
                _sat_monthly_b  = 0
                _fund_cashflows = []
                _total_inv_b = 0

                for i, fd_p in enumerate(loaded_funds):
                    m_p    = fd_p.get("metrics",{})
                    nav_p  = m_p.get("nav",0)
                    mdr_p  = m_p.get("monthly_div",0)
                    _adr_p_raw = m_p.get("annual_div_rate",0) or (fd_p.get("moneydj_raw",{}).get("moneydj_div_yield",0) or 0)
                    try: adr_p = float(_adr_p_raw)
                    except (ValueError, TypeError): adr_p = 0.0
                    curr_p = fd_p.get("currency","USD") or "USD"
                    name_p = (fd_p.get("name","") or fd_p["code"])[:25]
                    is_c_p = fd_p.get("is_core", True)
                    role_icon = "🛡️" if is_c_p else "⚡"
                    rate_p = exch_rates_def.get(curr_p, 32.0)

                    with st.expander(
                        f"{role_icon} {name_p}（{fd_p['code']}）— 配息率 {adr_p:.1f}%",
                        expanded=(i==0)):
                        _ec1, _ec2, _ec3 = st.columns(3)
                        with _ec1:
                            # ── 快速金額按鈕 ──────────────────────
                            _qb1, _qb2, _qb3, _qb4 = st.columns(4)
                            for _qc, _ql, _qv in [
                                (_qb1,"10萬",100000), (_qb2,"50萬",500000),
                                (_qb3,"100萬",1000000), (_qb4,"300萬",3000000)]:
                                if _qc.button(_ql, key=f"qb_{i}_{_qv}", use_container_width=True):
                                    st.session_state[f"cfb_inv_{i}"] = _qv
                            inv_amt = st.number_input(
                                f"現有投入（NT$）", min_value=0, max_value=50000000,
                                value=int(st.session_state.get(f"cfb_inv_{i}",
                                          fd_p.get("invest_twd",300000) or 300000)),
                                step=50000, key=f"cfb_inv_{i}")
                            _idx_pf = next(
                                (j for j,_f in enumerate(st.session_state.portfolio_funds)
                                 if _f.get("code") == fd_p.get("code")), None)
                            if _idx_pf is not None:
                                st.session_state.portfolio_funds[_idx_pf]["invest_twd"] = inv_amt
                        with _ec2:
                            rate_input = st.number_input(
                                f"匯率（{curr_p}/TWD）", min_value=0.01, max_value=200.0,
                                value=exch_rates_def.get(curr_p,32.0),
                                step=0.5, key=f"cfb_rate_{i}")
                            # DRIP checkbox
                            _drip_mode = st.checkbox(
                                "DRIP 配股模式",
                                value=False,
                                key=f"cfb_drip_{i}",
                                help="配息換購本基金單位數"
                            )
                        with _ec3:
                            if nav_p > 0 and rate_input > 0 and mdr_p > 0 and inv_amt > 0:
                                import math
                                units_p = math.floor((inv_amt / rate_input) / nav_p)
                                monthly_twd_p = mdr_p * units_p * rate_input
                                annual_twd_p  = monthly_twd_p * 12
                                st.metric("每月配息（台幣）", f"NT${monthly_twd_p:,.0f}",
                                          delta=f"{units_p:,} 單位 × {mdr_p:.4f}")
                                st.metric("年化配息（台幣）", f"NT${annual_twd_p:,.0f}")
                                _total_monthly_twd += monthly_twd_p
                                if is_c_p: _core_monthly_b += monthly_twd_p
                                else:      _sat_monthly_b  += monthly_twd_p
                                _fund_cashflows.append({
                                    "name":name_p, "code":fd_p["code"],
                                    "is_core":is_c_p, "invest_twd":inv_amt,
                                    "monthly_twd":monthly_twd_p, "annual_twd":annual_twd_p,
                                    "adr":adr_p, "currency":curr_p,
                                    "drip": st.session_state.get(f"cfb_drip_{i}", False),
                                })
                                _total_inv_b += inv_amt
                            elif inv_amt > 0:
                                # ── 精確公式（依規格書）──────────────────────────
                                # Step1: 持有單位數 = floor(投入台幣 / (NAV × 匯率))
                                # Step2: 月配息    = 單位數 × 每單位月配 × 匯率
                                if nav_p > 0 and rate_input > 0 and adr_p > 0:
                                    # 換算單位數（取整，不含零頭）
                                    import math
                                    units_est = math.floor((inv_amt / rate_input) / nav_p)
                                    # 每單位月配息金額 = NAV × 年化配息率 / 12
                                    # [Bug B fix] monthly_div priority over adr estimation
                                    mdr_est = m_p.get("monthly_div", 0) or (nav_p * adr_p / 100 / 12 if nav_p > 0 else 0)
                                    est_monthly = units_est * mdr_est * rate_input if units_est else 0
                                    calc_note = f"⌊{inv_amt/rate_input/nav_p:.1f}⌋={units_est} 單位 × {mdr_est:.4f}/月"
                                else:
                                    # 無 NAV 時使用 % 估算（最後備援）
                                    units_est = None
                                    est_monthly = inv_amt * adr_p / 100 / 12
                                    calc_note = f"(估算) 本金×配息率/12"
                                st.metric("估算月配息", f"NT${est_monthly:,.0f}",
                                          delta=calc_note)
                                _total_monthly_twd += est_monthly
                                if is_c_p: _core_monthly_b += est_monthly
                                else:      _sat_monthly_b  += est_monthly
                                _fund_cashflows.append({
                                    "name":name_p, "code":fd_p["code"],
                                    "is_core":is_c_p, "invest_twd":inv_amt,
                                    "monthly_twd":est_monthly, "annual_twd":est_monthly*12,
                                    "adr":adr_p, "currency":curr_p,
                                    "units": units_est,
                                    "drip": st.session_state.get(f"cfb_drip_{i}", False),
                                })
                                _total_inv_b += inv_amt

                if _total_inv_b > 0 and _fund_cashflows:
                    st.divider()
                    # ── Step 2: 現有配置分析 ──────────────────────────
                    st.markdown("##### 📊 現有配置分析")
                    _cur_core_inv = sum(f["invest_twd"] for f in _fund_cashflows if f["is_core"])
                    _cur_sat_inv  = sum(f["invest_twd"] for f in _fund_cashflows if not f["is_core"])
                    _cur_core_pct = round(_cur_core_inv/_total_inv_b*100, 1)
                    _cur_sat_pct  = round(_cur_sat_inv/_total_inv_b*100, 1)

                    _tgt_core_pct = st.session_state.get("portfolio_core_pct", 75)
                    _tgt_sat_pct  = 100 - _tgt_core_pct

                    _c_an1, _c_an2 = st.columns(2)
                    with _c_an1:
                        # 目前配置 vs 目標
                        for label, cur_pct, tgt_pct, color in [
                            ("🛡️ 核心資產", _cur_core_pct, _tgt_core_pct, "#64b5f6"),
                            ("⚡ 衛星資產", _cur_sat_pct,  _tgt_sat_pct,  "#ff9800"),
                        ]:
                            diff = round(cur_pct - tgt_pct, 1)
                            diff_c = "#f44336" if abs(diff)>10 else ("#ff9800" if abs(diff)>5 else "#00c853")
                            st.markdown(
                                f"<div style='background:#161b22;border:1px solid #30363d;border-radius:8px;"
                                f"padding:10px 14px;margin:4px 0'>"
                                f"<div style='display:flex;justify-content:space-between;align-items:center'>"
                                f"<span style='color:{color};font-weight:700'>{label}</span>"
                                f"<span style='color:{diff_c};font-size:12px'>{'+' if diff>0 else ''}{diff}%</span>"
                                f"</div>"
                                f"<div style='display:flex;gap:12px;margin-top:6px'>"
                                f"<span style='color:#888;font-size:11px'>目前</span>"
                                f"<span style='color:{color};font-weight:700'>{cur_pct}%</span>"
                                f"<span style='color:#555;font-size:11px'>→ 目標</span>"
                                f"<span style='color:#e6edf3;font-weight:700'>{tgt_pct}%</span>"
                                f"<span style='color:#555;font-size:11px'>NT${_total_inv_b*tgt_pct/100:,.0f}</span>"
                                f"</div>"
                                f"<div style='background:#21262d;border-radius:4px;height:6px;margin-top:6px'>"
                                f"<div style='background:{color};width:{min(cur_pct,100)}%;height:100%;border-radius:4px'></div>"
                                f"</div>"
                                f"</div>",
                                unsafe_allow_html=True)

                    with _c_an2:
                        # 現金流總覽
                        st.markdown(
                            f"<div style='background:#0d2818;border:1px solid #00c853;border-radius:10px;"
                            f"padding:14px;text-align:center'>"
                            f"<div style='color:#00c853;font-weight:700;margin-bottom:10px'>💰 每月配息總覽</div>"
                            f"<div style='color:#00c853;font-size:28px;font-weight:900'>NT${_total_monthly_twd:,.0f}</div>"
                            f"<div style='color:#888;font-size:12px'>/ 月（年化 NT${_total_monthly_twd*12:,.0f}）</div>"
                            f"<hr style='border:1px solid #1a3a28;margin:10px 0'>"
                            f"<div style='display:flex;justify-content:space-around'>"
                            f"<div><div style='color:#888;font-size:11px'>核心月配</div>"
                            f"<div style='color:#64b5f6;font-weight:700'>NT${_core_monthly_b:,.0f}</div></div>"
                            f"<div><div style='color:#888;font-size:11px'>衛星月配</div>"
                            f"<div style='color:#ff9800;font-weight:700'>NT${_sat_monthly_b:,.0f}</div></div>"
                            f"</div></div>",
                            unsafe_allow_html=True)

                    # ── Step 3: 再平衡建議 ─────────────────────────────
                    st.markdown("##### ⚖️ 再平衡操作建議")
                    _core_diff_pct = _cur_core_pct - _tgt_core_pct
                    _core_adj_amt  = abs(round(_total_inv_b * abs(_core_diff_pct) / 100))

                    if abs(_core_diff_pct) < 5:
                        st.success(f"✅ 目前配置與目標偏差 {abs(_core_diff_pct):.1f}%，在正常範圍內（<5%），無需操作。")
                    elif abs(_core_diff_pct) < 10:
                        st.warning(f"⚠️ 核心比例偏差 {abs(_core_diff_pct):.1f}%（>5%），建議近期調整 NT${_core_adj_amt:,.0f}。")
                    else:
                        st.error(f"🚨 核心比例偏差 {abs(_core_diff_pct):.1f}%（>10%），必須執行再平衡，調整 NT${_core_adj_amt:,.0f}。")

                    # 逐檔再平衡建議
                    if abs(_core_diff_pct) >= 5:
                        if _core_diff_pct > 0:
                            st.markdown("**核心過重 → 賣出部分核心，買入衛星：**")
                            direction = "賣出"
                            from_group = [f for f in _fund_cashflows if f["is_core"]]
                            to_label = "衛星"
                        else:
                            st.markdown("**衛星過重 → 賣出部分衛星，買入核心：**")
                            direction = "賣出"
                            from_group = [f for f in _fund_cashflows if not f["is_core"]]
                            to_label = "核心"

                        if from_group:
                            total_from_inv = sum(f["invest_twd"] for f in from_group)
                            for fd_item in from_group:
                                if total_from_inv > 0:
                                    this_adj = round(_core_adj_amt * fd_item["invest_twd"] / total_from_inv)
                                    new_inv  = fd_item["invest_twd"] - this_adj
                                    new_monthly = fd_item["monthly_twd"] * new_inv / max(fd_item["invest_twd"],1)
                                    st.markdown(
                                        f"<div style='background:#1f1414;border-left:3px solid #f44336;"
                                        f"padding:8px 12px;margin:3px 0;border-radius:0 6px 6px 0'>"
                                        f"<span style='color:#f44336'>🔻 {direction}：{fd_item['name']}</span>"
                                        f"<span style='color:#888;font-size:11px;margin-left:8px'>"
                                        f"NT${this_adj:,.0f}（從 {fd_item['invest_twd']:,.0f} → {new_inv:,.0f}）</span>"
                                        f"<br><span style='color:#888;font-size:10px'>"
                                        f"月配息從 NT${fd_item['monthly_twd']:,.0f} → NT${new_monthly:,.0f}</span>"
                                        f"</div>",
                                        unsafe_allow_html=True)

                    # ── Step 4: 配息流向規劃 ─────────────────────────────
                    st.markdown("##### 🌊 配息流向規劃")
                    # DRIP fund split - separate DRIP funds from cash dividend funds
                    # [Tutorial] DRIP funds reinvest dividends as units, cash funds pay out
                    _drip_funds = [f for f in _fund_cashflows if f.get("drip")]
                    _cash_funds = [f for f in _fund_cashflows if not f.get("drip")]

                    if _drip_funds:
                        st.markdown("**🔄 DRIP 配股基金（配息轉單位）**")
                        st.info(
                            "💡 DRIP 教學：配息不領現金，直接買入本基金單位數。\n"
                            "優點：複利效應最大化，不需手動再投入。\n"
                            "公式：每月新增單位數 = ⌊月配息(台幣) ÷ 匯率 ÷ NAV⌋"
                        )
                        for _df in _drip_funds:
                            _df_m = next((f.get("metrics",{}) for f in loaded_funds if f.get("code")==_df["code"]), {})
                            _df_nav = _df_m.get("nav", 0) or 0
                            _df_rate = exch_rates_def.get(_df["currency"], 32.0)
                            if _df_nav > 0 and _df_rate > 0 and _df["monthly_twd"] > 0:
                                import math as _dm
                                _drip_units = _dm.floor(_df["monthly_twd"] / _df_rate / _df_nav)
                                _new_inv_after = _df["invest_twd"] + _df["monthly_twd"]
                            else:
                                _drip_units = 0
                                _new_inv_after = _df["invest_twd"]
                            st.markdown(
                                f"<div style='background:#0d1b0d;border:1px solid #1a5c1a;border-radius:8px;padding:10px 14px;margin:4px 0'>"
                                f"<span style='color:#00c853;font-weight:700'>🔄 {_df['name']}</span> | {_df['currency']} NAV {_df_nav:.4f}<br/>"
                                f"<span style='color:#ccc;font-size:12px'>月配息 NT${_df['monthly_twd']:,.0f} → 換購 <b style='color:#00c853'>{_drip_units} 單位</b></span>"
                                f"</div>",
                                unsafe_allow_html=True)

                    if _cash_funds:
                        st.markdown("**💰 現金配息基金（可手動再投入）**")

                    if _total_monthly_twd > 0:
                        _to_sat  = _core_monthly_b * 0.7
                        _to_cash = _core_monthly_b * 0.2
                        _to_core = _core_monthly_b * 0.1
                        st.markdown(
                            f"<div style='background:#0d1117;border:1px solid #30363d;border-radius:10px;"
                            f"padding:14px;margin:8px 0'>"
                            f"<div style='color:#e6edf3;font-weight:700;margin-bottom:10px'>"
                            f"核心配息 NT${_core_monthly_b:,.0f}/月 → 建議流向：</div>"
                            f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px'>"
                            f"<div style='background:#0a1f10;border-radius:8px;padding:10px;text-align:center'>"
                            f"<div style='color:#888;font-size:11px'>⚡ 再投入衛星（70%）</div>"
                            f"<div style='color:#00c853;font-size:16px;font-weight:900'>NT${_to_sat:,.0f}</div></div>"
                            f"<div style='background:#1a1400;border-radius:8px;padding:10px;text-align:center'>"
                            f"<div style='color:#888;font-size:11px'>🏦 現金備用（20%）</div>"
                            f"<div style='color:#ff9800;font-size:16px;font-weight:900'>NT${_to_cash:,.0f}</div></div>"
                            f"<div style='background:#0d1b2a;border-radius:8px;padding:10px;text-align:center'>"
                            f"<div style='color:#888;font-size:11px'>🛡️ 加碼核心（10%）</div>"
                            f"<div style='color:#64b5f6;font-size:16px;font-weight:900'>NT${_to_core:,.0f}</div></div>"
                            f"</div>"
                            f"<div style='color:#555;font-size:11px;margin-top:8px'>"
                            f"💡 MK 原則：以核心配息養衛星，本金盡量不動。"
                            f"衛星資產年化報酬率預計可達 10-15%，核心提供穩定 {(_total_monthly_twd*12/_total_inv_b*100):.1f}% 配息基礎。"
                            f"</div></div>",
                            unsafe_allow_html=True)

                    # ── Step 5: 零增資再平衡工具 ────────────────────────
                    st.divider()
                    st.markdown("##### 💡 零增資再平衡工具")
                    st.info(
                        "**什麼是零增資再平衡？**\n\n"
                        "不用投入新資金，透過以下方式維持最佳核心/衛星比例：\n"
                        "- 🔄 **賣出單位數換購**：賣掉過多的基金，買入不足的基金\n"
                        "- 💰 **配息再投入**：把每月配息投入到比例不足的基金\n\n"
                        "建議每季檢視一次，偏差 > 5% 才需要操作。")

                    if _fund_cashflows and _total_inv_b > 0:
                        _rb5_mode = st.radio(
                            "再平衡方式",
                            ["📊 僅顯示偏差（觀察）", "🔄 賣出單位數換購", "💰 配息再投入"],
                            horizontal=True, key="rb5_mode")

                        # 計算各檔目標金額
                        _tgt_pct_map = {}
                        _total_funds5 = len(_fund_cashflows)
                        if _total_funds5 > 0:
                            _core_funds5 = [f for f in _fund_cashflows if f["is_core"]]
                            _sat_funds5  = [f for f in _fund_cashflows if not f["is_core"]]
                            _tgt_core5 = st.session_state.get("portfolio_core_pct", 75)
                            _tgt_sat5  = 100 - _tgt_core5
                            _core_per = _tgt_core5 / max(len(_core_funds5), 1) if _core_funds5 else 0
                            _sat_per  = _tgt_sat5  / max(len(_sat_funds5), 1)  if _sat_funds5  else 0
                            for f5 in _fund_cashflows:
                                _tgt_pct_map[f5["code"]] = _core_per if f5["is_core"] else _sat_per

                        # 逐檔顯示偏差表
                        st.markdown("**📋 各基金現況 vs 目標**")
                        _rb5_rows = []
                        for f5 in _fund_cashflows:
                            _cur5_pct = round(f5["invest_twd"] / _total_inv_b * 100, 1)
                            _tgt5_pct = round(_tgt_pct_map.get(f5["code"], 0), 1)
                            _diff5    = round(_cur5_pct - _tgt5_pct, 1)
                            _diff5_amt= round(abs(_diff5) / 100 * _total_inv_b)
                            _action5  = "持平" if abs(_diff5) < 5 else ("🔻 過多，建議減少" if _diff5 > 0 else "🔺 不足，建議增加")
                            _rb5_rows.append({
                                "基金": f5["name"][:18],
                                "現況%": f"{_cur5_pct}%",
                                "目標%": f"{_tgt5_pct}%",
                                "偏差%": f"{_diff5:+.1f}%",
                                "建議": _action5,
                                "金額": f"NT${_diff5_amt:,}" if abs(_diff5) >= 5 else "─",
                            })
                        import pandas as _pd5
                        st.dataframe(_pd5.DataFrame(_rb5_rows), use_container_width=True, hide_index=True)

                        # 賣出單位數模式
                        if "賣出" in _rb5_mode:
                            st.markdown("**🔄 賣出單位數計算**")
                            st.caption("計算方式：需調整金額 ÷ 匯率 ÷ 淨值（NAV）= 應賣出/買入單位數（取整數）")
                            for f5 in _fund_cashflows:
                                _cur5_pct = f5["invest_twd"] / _total_inv_b * 100
                                _tgt5_pct = _tgt_pct_map.get(f5["code"], 0)
                                _diff5    = _cur5_pct - _tgt5_pct
                                if abs(_diff5) < 5:
                                    continue
                                _adj_amt  = abs(_diff5) / 100 * _total_inv_b
                                _m5 = next((f for f in loaded_funds if f["code"] == f5["code"]), {})
                                _nav5 = (_m5.get("metrics",{}) or {}).get("nav", 0) or 0
                                _curr5 = _m5.get("currency","USD") or "USD"
                                _rate5 = exch_rates_def.get(_curr5, 32.0)
                                if _nav5 > 0 and _rate5 > 0:
                                    import math as _m5h
                                    _units5 = _m5h.floor((_adj_amt / _rate5) / _nav5)
                                    _dir5 = "賣出" if _diff5 > 0 else "買入"
                                    _clr5 = "#f44336" if _dir5 == "賣出" else "#00c853"
                                    st.markdown(
                                        f"<div style='background:#161b22;border-left:3px solid {_clr5};"
                                        f"padding:8px 12px;margin:3px 0;border-radius:0 6px 6px 0'>"
                                        f"<span style='color:{_clr5};font-weight:700'>{('🔻' if _dir5=='賣出' else '🔺')} {_dir5}：{f5['name'][:20]}</span>"
                                        f"<span style='color:#888;font-size:11px;margin-left:8px'>"
                                        f"約 {_units5:,} 單位（NT${_adj_amt:,.0f} ÷ {_rate5} ÷ NAV {_nav5:.4f}）</span>"
                                        f"</div>", unsafe_allow_html=True)
                                else:
                                    _dir5b = "減少" if _diff5 > 0 else "增加"
                                    st.markdown(f"- **{f5['name'][:18]}**：建議{_dir5b} NT${_adj_amt:,.0f}（NAV 未載入，無法計算單位數）")

                        # 配息再投入模式
                        elif "配息" in _rb5_mode:
                            st.markdown("**💰 本月配息再投入建議**")
                            st.caption("策略：把本月配息投入比例最不足的基金，逐步拉近目標比例。")
                            if _total_monthly_twd > 0:
                                # 找出最需要補強的基金
                                _underweight5 = [(f5, _tgt_pct_map.get(f5["code"],0) - f5["invest_twd"]/_total_inv_b*100)
                                                 for f5 in _fund_cashflows
                                                 if _tgt_pct_map.get(f5["code"],0) - f5["invest_twd"]/_total_inv_b*100 > 5]
                                _underweight5.sort(key=lambda x: -x[1])
                                if _underweight5:
                                    for _uf5, _udiff5 in _underweight5:
                                        _suggest5 = round(_total_monthly_twd * (_udiff5 / sum(d for _,d in _underweight5)))
                                        st.markdown(
                                            f"<div style='background:#071a0f;border-left:3px solid #00c853;"
                                            f"padding:8px 12px;margin:3px 0;border-radius:0 6px 6px 0'>"
                                            f"<span style='color:#00c853;font-weight:700'>💰 {_uf5['name'][:20]}</span>"
                                            f"<span style='color:#888;font-size:11px;margin-left:8px'>"
                                            f"建議投入本月配息 NT${_suggest5:,}（偏差 +{_udiff5:.1f}%）</span>"
                                            f"</div>", unsafe_allow_html=True)
                                else:
                                    st.success("✅ 各基金比例均衡，本月配息可平均再投入或保留現金。")
                            else:
                                st.info("💡 請先在上方設定各基金投入金額以計算配息。")

    with pt2:
        st.markdown("### ⚖️ 再平衡管理（核心/衛星比例監控）")
        st.markdown("---")
        st.caption("MK：偏離目標 >5% 時應考慮再平衡，>10% 必須執行")

        loaded_funds = [f for f in st.session_state.portfolio_funds if f.get("loaded")]
        if not loaded_funds:
            st.info("💡 請先在「組合分析」頁加入並載入基金")
        else:
            # 核心/衛星目標設定
            st.markdown("#### 🎯 目標配置設定")
            rb1, rb2 = st.columns(2)
            with rb1:
                target_core = st.slider("核心資產目標比例 %", 0, 100,
                                        st.session_state.portfolio_core_pct, 5,
                                        key="rb_core_target")
                st.session_state.portfolio_core_pct = target_core
            with rb2:
                target_sat = 100 - target_core
                st.metric("衛星資產目標比例", f"{target_sat}%")

            st.divider()

            # 計算實際持倉比例
            total_invest = sum(f.get("invest_twd", 0) for f in loaded_funds)
            core_invest  = sum(f.get("invest_twd", 0) for f in loaded_funds if f.get("is_core", True))
            sat_invest   = total_invest - core_invest

            if total_invest > 0:
                actual_core_pct = core_invest / total_invest * 100
                actual_sat_pct  = sat_invest  / total_invest * 100
                core_drift      = actual_core_pct - target_core
                sat_drift       = actual_sat_pct  - target_sat

                # 視覺化
                drift_abs = abs(core_drift)
                if drift_abs >= 10:
                    alert_c, alert_msg = "#f44336", "🚨 必須再平衡！偏離 >10%"
                elif drift_abs >= 5:
                    alert_c, alert_msg = "#ff9800", "⚠️ 建議再平衡，偏離 >5%"
                else:
                    alert_c, alert_msg = "#00c853", "✅ 配置均衡，無需再平衡"

                st.markdown(
                    f"<div style='background:#161b22;border:2px solid {alert_c};"
                    f"border-radius:12px;padding:16px;margin:8px 0;text-align:center'>"
                    f"<div style='font-size:18px;font-weight:700;color:{alert_c}'>{alert_msg}</div>"
                    f"<div style='font-size:13px;color:#888;margin-top:6px'>"
                    f"核心偏離：{core_drift:+.1f}%</div>"
                    f"</div>", unsafe_allow_html=True)

                # 比例對比
                rb_c1, rb_c2 = st.columns(2)
                with rb_c1:
                    st.markdown("##### 🛡️ 核心資產")
                    st.progress(int(actual_core_pct), text=f"實際 {actual_core_pct:.1f}% （目標 {target_core}%）")
                with rb_c2:
                    st.markdown("##### ⚡ 衛星資產")
                    st.progress(int(actual_sat_pct), text=f"實際 {actual_sat_pct:.1f}% （目標 {target_sat}%）")

                # ── v10.4: 衛星 >35% 硬性收割警示（以息養股紀律硬頂） ──
                _SAT_HARD_CEIL = 35
                if actual_sat_pct > _SAT_HARD_CEIL:
                    _harvest_amt = ((actual_sat_pct - _SAT_HARD_CEIL) / 100) * total_invest
                    st.markdown(
                        f"<div style='background:#1f0505;border:3px solid #f44336;"
                        f"border-radius:12px;padding:16px;margin:8px 0'>"
                        f"<div style='font-size:15px;font-weight:900;color:#f44336;margin-bottom:8px'>"
                        f"🚨 衛星資產觸發硬性收割線（>{_SAT_HARD_CEIL}%）！</div>"
                        f"<div style='font-size:13px;color:#ccc;line-height:1.8'>"
                        f"目前衛星：<b style='color:#f44336'>{actual_sat_pct:.1f}%</b>　"
                        f"硬頂：<b>{_SAT_HARD_CEIL}%</b>　"
                        f"超出：<b style='color:#f44336'>{actual_sat_pct-_SAT_HARD_CEIL:.1f}%</b><br>"
                        f"建議立即獲利了結衛星資產約 "
                        f"<b style='color:#ff9800;font-size:15px'>NT${_harvest_amt:,.0f}</b>，"
                        f"轉入核心配息基金<br>"
                        f"<span style='color:#888;font-size:11px'>"
                        f"以息養股鐵律：衛星比例不超過35%，避免組合過度暴露成長波動風險</span>"
                        f"</div></div>",
                        unsafe_allow_html=True)

                # 再平衡操作指引
                if drift_abs >= 5:
                    st.markdown("#### 📋 再平衡操作建議")
                    # v15: 白話文今日行動指南 (Module 4)
                    _macro_weather = st.session_state.phase_info.get("weather_label","") if st.session_state.macro_done else ""
                    _macro_phase   = st.session_state.phase_info.get("phase","") if st.session_state.macro_done else ""
                    _weather_ctx   = f"目前總經天氣「{_macro_weather}·{_macro_phase}期」" if _macro_weather else "（請先載入總經數據以取得天氣背景）"

                    if core_drift > 0:
                        rebal_amt = (core_drift / 100) * total_invest
                        # Find the heaviest satellite fund to sell
                        _sat_funds_sorted = sorted(
                            [f for f in loaded_funds if not f.get("is_core")],
                            key=lambda x: x.get("invest_twd",0), reverse=True)
                        _core_funds_sorted = sorted(
                            [f for f in loaded_funds if f.get("is_core")],
                            key=lambda x: x.get("invest_twd",0))
                        _sell_fund = (_sat_funds_sorted[0].get("name","") or _sat_funds_sorted[0]["code"])[:15] if _sat_funds_sorted else "衛星基金"
                        _buy_fund  = (_core_funds_sorted[0].get("name","") or _core_funds_sorted[0]["code"])[:15] if _core_funds_sorted else "核心基金"
                        st.markdown(
                            f"<div style='background:#1a1a0d;border:1px solid #ff9800;"
                            f"border-radius:12px;padding:16px'>"
                            f"<div style='color:#ff9800;font-size:14px;font-weight:700;margin-bottom:8px'>"
                            f"📋 今日行動指南</div>"
                            f"<div style='color:#e6edf3;font-size:13px;line-height:1.8'>"
                            f"根據{_weather_ctx}，您的防禦力略顯過強。<br>"
                            f"🎯 <b>建議操作：</b><br>"
                            f"① 從「{_sell_fund}」<b style='color:#ff9800'>贖回 NT${rebal_amt:,.0f}</b><br>"
                            f"② 轉入核心配息資產「{_buy_fund}」增加現金流<br>"
                            f"<span style='color:#888;font-size:11px;margin-top:6px;display:block'>"
                            f"💡 實務：停止核心加碼，將下月配息全數投入衛星，"
                            f"或在買點時適量獲利了結核心轉入</span>"
                            f"</div></div>", unsafe_allow_html=True)
                    else:
                        rebal_amt = abs(core_drift / 100) * total_invest
                        _core_funds_sorted = sorted(
                            [f for f in loaded_funds if f.get("is_core")],
                            key=lambda x: x.get("invest_twd",0), reverse=True)
                        _sat_funds_sorted = sorted(
                            [f for f in loaded_funds if not f.get("is_core")],
                            key=lambda x: x.get("invest_twd",0))
                        _sell_fund = (_core_funds_sorted[0].get("name","") or _core_funds_sorted[0]["code"])[:15] if _core_funds_sorted else "衛星基金"
                        _buy_fund  = (_sat_funds_sorted[0].get("name","") or _sat_funds_sorted[0]["code"])[:15] if _sat_funds_sorted else "核心基金"
                        st.markdown(
                            f"<div style='background:#0d1a2a;border:1px solid #64b5f6;"
                            f"border-radius:12px;padding:16px'>"
                            f"<div style='color:#64b5f6;font-size:14px;font-weight:700;margin-bottom:8px'>"
                            f"📋 今日行動指南</div>"
                            f"<div style='color:#e6edf3;font-size:13px;line-height:1.8'>"
                            f"根據{_weather_ctx}，您的衛星部位比重過高。<br>"
                            f"🎯 <b>建議操作：</b><br>"
                            f"① 衛星部位「{_sell_fund}」已達停利點，<b style='color:#64b5f6'>獲利了結 NT${rebal_amt:,.0f}</b><br>"
                            f"② 這筆錢轉買核心配息「{_buy_fund}」，鞏固每月現金流<br>"
                            f"<span style='color:#888;font-size:11px;margin-top:6px;display:block'>"
                            f"💡 這樣您就能安穩度過接下來的市場震盪，"
                            f"並以核心配息繼續「養」下一波衛星佈局</span>"
                            f"</div></div>", unsafe_allow_html=True)

                # 各基金核心/衛星分類表
                st.markdown("#### 📂 基金分類總覽")
                for fd_r in loaded_funds:
                    m_r    = fd_r.get("metrics", {})
                    adr_r  = m_r.get("annual_div_rate", 0)
                    try: adr_r = float(adr_r)
                    except (ValueError, TypeError): adr_r = 0.0
                    ret1y_r = m_r.get("ret_1y")
                    nav_chg_r = float(ret1y_r) if isinstance(ret1y_r,(int,float)) else 0
                    total_ret_r = nav_chg_r + adr_r
                    eat_r  = total_ret_r < adr_r and adr_r > 0
                    is_c_r = fd_r.get("is_core", True)
                    role_r = "🛡️ 核心" if is_c_r else "⚡ 衛星"
                    role_c_r = "#64b5f6" if is_c_r else "#ff9800"
                    inv_r  = fd_r.get("invest_twd", 0)
                    pct_r  = inv_r / total_invest * 100 if total_invest > 0 else 0
                    eat_tag = " ⚠️吃本金" if eat_r else ""
                    st.markdown(
                        f"<div style='background:#161b22;border-left:3px solid {role_c_r};"
                        f"border-radius:0 8px 8px 0;padding:8px 12px;margin:4px 0;"
                        f"display:flex;justify-content:space-between;align-items:center'>"
                        f"<span><b style='color:{role_c_r}'>{role_r}</b> "
                        f"{(fd_r.get('name','') or fd_r['code'])[:25]}</span>"
                        f"<span style='color:#888;font-size:12px'>"
                        f"NT${inv_r:,.0f}（{pct_r:.1f}%）配息{adr_r:.1f}%"
                        f"<span style='color:#f44336'>{eat_tag}</span></span>"
                        f"</div>", unsafe_allow_html=True)

                st.divider()

                # ─── 3. 汰弱留強篩選（Security Ranking）────────────────
                st.divider()
                st.markdown("#### 🏆 汰弱留強篩選（Security Ranking）")
                st.caption("同組排行 ◆ 連續兩季後25%→觸發汰弱警示 ◆ 吃本金/大跌→替換建議")

                for fd_w in loaded_funds:
                    m_w     = fd_w.get("metrics", {})
                    mj_w    = fd_w.get("moneydj_raw", {})
                    # v10.1: 正確取 wb07 risk_table（在 risk_metrics 子字典內）
                    rm_w    = mj_w.get("risk_metrics", {}).get("risk_table", {})
                    # v10.1: 優先使用 MoneyDJ wb05「年化配息率%」
                    _mj_dy_w = mj_w.get("moneydj_div_yield")
                    try: _mj_dy_w = float(_mj_dy_w) if _mj_dy_w is not None else None
                    except (ValueError, TypeError): _mj_dy_w = None
                    adr_w   = _mj_dy_w if (_mj_dy_w and _mj_dy_w > 0) else (m_w.get("annual_div_rate", 0) or 0)
                    try: adr_w = float(adr_w)
                    except (ValueError, TypeError): adr_w = 0.0
                    # v10.1: 優先使用 MoneyDJ wb01 含息報酬率（績效頁，已含配息）
                    _perf_w = mj_w.get("perf", {})
                    _wb01_1y = _perf_w.get("1Y") if isinstance(_perf_w, dict) else None
                    ret1y_w = m_w.get("ret_1y")
                    ret3m_w = m_w.get("ret_3m")
                    # Sharpe 從 wb07 risk_metrics 取
                    sharpe_w = rm_w.get("一年",{}).get("Sharpe") or rm_w.get("三年",{}).get("Sharpe")
                    if _wb01_1y is not None:
                        # wb01: 含息總報酬（最準確）
                        total_ret_w = _wb01_1y
                    else:
                        # 備援: 自算（nav漲跌 + 配息估算）
                        nav_chg_w   = float(ret1y_w) if isinstance(ret1y_w,(int,float)) else 0
                        total_ret_w = nav_chg_w + adr_w
                    eat_w   = total_ret_w < adr_w and adr_w > 0
                    weak_y  = isinstance(ret1y_w,(int,float)) and ret1y_w < -10
                    weak_q  = isinstance(ret3m_w,(int,float)) and ret3m_w < -5
                    name_w  = (fd_w.get("name","") or fd_w["code"])[:28]
                    cat_w   = mj_w.get("investment_target","") or fd_w.get("category","")
                    mgmt_fee_w = mj_w.get("mgmt_fee","")
                    try: fee_w = float(str(mgmt_fee_w).replace("%","").strip()); has_fee_w=True
                    except: fee_w = None; has_fee_w = False

                    # 評分：基於含息報酬、Sharpe、費用率
                    score_w = 0
                    score_max = 0
                    reasons_ok = []; reasons_bad = []

                    # ──── 含息報酬 (40分) ─────────────────────────
                    # 來源: wb01「一年報酬率」= 真實含息總報酬（MoneyDJ說明：績效皆考慮配息）
                    score_max += 40
                    _src_lbl_w = "wb01" if _wb01_1y is not None else "估算"
                    if adr_w <= 0:
                        score_w += 20; reasons_ok.append("無配息基金（不適用配息評估）")
                    elif total_ret_w >= adr_w:
                        _margin_w = total_ret_w - adr_w
                        _pts = 40 if _margin_w >= 3 else (30 if _margin_w >= 1 else 20)
                        score_w += _pts
                        reasons_ok.append(f"含息報酬{total_ret_w:.1f}%({'↑↑' if _margin_w>=3 else '↑'}) [{_src_lbl_w}]")
                    else:
                        reasons_bad.append(f"🔴吃本金(含息{total_ret_w:.1f}% < 配{adr_w:.1f}%) [{_src_lbl_w}]")

                    # Sharpe
                    score_max += 30
                    try:
                        shp_w = float(str(sharpe_w).replace("—","").replace("N/A","")) if sharpe_w else 0
                    except: shp_w = 0
                    if shp_w >= 1:
                        score_w += 30; reasons_ok.append(f"Sharpe {shp_w:.2f}≥1.0")
                    elif shp_w > 0:
                        score_w += 15; reasons_ok.append(f"Sharpe {shp_w:.2f}")
                    elif shp_w < 0:
                        reasons_bad.append(f"Sharpe負值({shp_w:.2f})")

                    # 費用率
                    score_max += 30
                    _peer_fee_w = 1.5
                    for _k,_v in {"股票":1.5,"債券":1.0,"平衡":1.3,"貨幣":0.5}.items():
                        if _k in (cat_w or ""): _peer_fee_w=_v; break
                    if has_fee_w and fee_w is not None:
                        if fee_w <= _peer_fee_w:
                            score_w += 30; reasons_ok.append(f"費用率{fee_w:.2f}%≤平均")
                        elif fee_w <= _peer_fee_w + 0.3:
                            score_w += 15
                        else:
                            reasons_bad.append(f"費用率偏高{fee_w:.2f}%")
                    else:
                        score_w += 15  # 無資料視為中等

                    pct_score = int(score_w / score_max * 100) if score_max > 0 else 50

                    # 顏色判斷
                    if pct_score >= 70: card_c_w = "#00c853"; grade_w = "A 持續持有"
                    elif pct_score >= 45: card_c_w = "#ff9800"; grade_w = "B 觀察中"
                    else: card_c_w = "#f44336"; grade_w = "C 建議汰換"

                    # 觸發汰換警示條件
                    should_replace = eat_w or weak_y or (weak_q and shp_w < 0)

                    st.markdown(
                        f"<div style='background:#0d1117;border:2px solid {card_c_w};"
                        f"border-radius:10px;padding:14px;margin:6px 0'>"
                        f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px'>"
                        f"<div><b style='color:#e6edf3;font-size:14px'>{name_w}</b>"
                        f"<span style='color:#888;font-size:11px;margin-left:8px'>{fd_w['code']}</span></div>"
                        f"<div style='text-align:right'>"
                        f"<span style='background:{card_c_w}22;color:{card_c_w};padding:3px 10px;"
                        f"border-radius:20px;font-size:12px;font-weight:700'>{grade_w}</span>"
                        f"<div style='font-size:10px;color:#888;margin-top:2px'>綜合評分 {pct_score}%</div>"
                        f"</div></div>"
                        f"<div style='background:#0d1117;border-radius:4px;height:6px;margin-bottom:8px'>"
                        f"<div style='background:{card_c_w};width:{pct_score}%;height:6px;border-radius:4px'></div>"
                        f"</div>"
                        f"<div style='display:flex;flex-wrap:wrap;gap:6px;font-size:11px'>"
                        + "".join(f"<span style='background:#0d2818;color:#00c853;padding:2px 8px;border-radius:12px'>{r}</span>" for r in reasons_ok)
                        + "".join(f"<span style='background:#2a0a0a;color:#f44336;padding:2px 8px;border-radius:12px'>{r}</span>" for r in reasons_bad)
                        + f"</div>"
                        + (f"<div style='margin-top:10px;padding:8px;background:#1a0a0a;border-radius:6px;"
                           f"color:#f44336;font-size:12px'>⚠️ 建議汰換：{'、'.join(reasons_bad)}。"
                           f"參考替代標的：同類高股息配息基金 / 費用率低於1%的主動式配息基金</div>" if should_replace else "")
                        + f"</div>", unsafe_allow_html=True)

            else:
                st.info("💡 請在「以息養股現金流」頁填入各基金投入金額，以計算再平衡比例")

# ═══════════════════════════════════════════════════════════════════════
# 🤖 AI 綜合分析（三個 Tab 下方，共用一個按鈕）
# ═══════════════════════════════════════════════════════════════════════



# ══════════════════════════════════════════════════════════════════
# v13 🚨 全域風險預警面板（位於 Tab 區塊之後）
# ══════════════════════════════════════════════════════════════════
st.divider()
st.markdown(
    "<div style='text-align:center;padding:4px 0 0'>"
    "<span style='font-size:18px;font-weight:700;color:#e0e0e0'>🚨 即時風險預警</span>"
    "<span style='font-size:11px;color:#666;margin-left:8px'>v13 Risk Alert System</span>"
    "</div>",
    unsafe_allow_html=True)

_risk_ind  = st.session_state.get("indicators", {})
_risk_ph   = st.session_state.get("phase_info", {}) or {}
_pf_loaded = [f for f in st.session_state.get("portfolio_funds",[]) if f.get("loaded")]

if _risk_ind:
    try:
        _regime_info = identify_regime(_risk_ind)
        _regime_str  = _regime_info.get("regime", "")
        _hy_v   = (_risk_ind.get("HY_SPREAD") or {}).get("value")
        _vix_v  = (_risk_ind.get("VIX") or {}).get("value")
        _fed_v  = (_risk_ind.get("FED_RATE") or {}).get("value")
        _fed_p  = (_risk_ind.get("FED_RATE") or {}).get("prev")
        _fed_dir = "up" if (_fed_v and _fed_p and _fed_v > _fed_p) else "down"

        # 計算投資組合平均回撤與配息覆蓋率
        _avg_dd   = None
        _avg_cov  = None
        if _pf_loaded:
            dds  = [f.get("metrics", {}).get("max_drawdown") for f in _pf_loaded
                    if f.get("metrics", {}).get("max_drawdown") is not None]
            if dds:
                _avg_dd = min(dds) / 100   # 最差者（負值）
            covs = []
            for _pff in _pf_loaded:
                _m3  = _pff.get("metrics", {}) or {}
                _mj3 = _pff.get("moneydj_raw", {}) or {}
                _pf3 = _mj3.get("perf", {}) or {}
                _adr3 = _mj3.get("moneydj_div_yield") or _m3.get("annual_div_rate", 0) or 0
                _tr3  = _pf3.get("1Y")
                if _tr3 is not None and _adr3 > 0:
                    covs.append(_tr3 / _adr3)
            if covs:
                _avg_cov = min(covs)   # 最差覆蓋率

        _alerts = portfolio_risk_alert(
            drawdown=_avg_dd, coverage=_avg_cov,
            regime=_regime_str, fed_direction=_fed_dir,
            hy_spread=_hy_v, vix=_vix_v
        )

        _color_map = {"red": "#f44336", "yellow": "#ff9800", "green": "#00c853"}
        _bg_map    = {"red": "#2a0a0a", "yellow": "#1a1200", "green": "#0a1a0a"}
        _alert_cols = st.columns(min(len(_alerts), 3))
        for _i, _al in enumerate(_alerts[:3]):
            with _alert_cols[_i % len(_alert_cols)]:
                _lv = _al.get("level", "green")
                st.markdown(
                    f"<div style='background:{_bg_map[_lv]};border:1px solid {_color_map[_lv]};"
                    f"border-radius:8px;padding:10px 12px;margin:4px 0;font-size:12px;"
                    f"color:{_color_map[_lv]};line-height:1.5'>"
                    f"{_al['message']}</div>",
                    unsafe_allow_html=True)

        # 景氣循環 Regime
        _rc = _regime_info.get("regime_color", "#888888")
        _rs = _regime_info.get("alloc_suggest", {})
        st.markdown(
            f"<div style='background:#1a2332;border:1px solid {_rc}33;border-radius:8px;"
            f"padding:8px 14px;margin:6px 0;font-size:12px'>"
            f"<b style='color:{_rc}'>景氣循環：{_regime_str}</b>"
            + ("　建議配置：" + " / ".join(f"{k} {v}%" for k,v in _rs.items()) if _rs else "")
            + "</div>",
            unsafe_allow_html=True)

        # 六因子評分（顯示已載入基金）
        if _pf_loaded:
            _ff_col1, _ff_col2 = st.columns([4, 1])
            with _ff_col1:
                st.markdown(
                    "<div style='font-size:13px;font-weight:700;color:#e0e0e0;margin:8px 0 4px'>"
                    "📊 基金六因子評分（Fund Factor Model）</div>",
                    unsafe_allow_html=True)
            with _ff_col2:
                st.caption("📖 公式說明見「說明書」Tab")
            _factor_cols = st.columns(min(len(_pf_loaded), 3))
            for _fi, _pff in enumerate(_pf_loaded[:3]):
                with _factor_cols[_fi]:
                    _fname  = (_pff.get("name") or _pff.get("code","?"))[:16]
                    _mj_raw = _pff.get("moneydj_raw", {}) or {}
                    _rt_f   = (_mj_raw.get("risk_metrics") or {}).get("risk_table")
                    _fs = calc_fund_factor_score(_pff, risk_table=_rt_f)
                    _sc = _fs.get("score", 50)
                    _gd = _fs.get("grade", "?")
                    _gc = {"A": "#00c853", "B": "#69f0ae", "C": "#ff9800", "D": "#f44336"}.get(_gd, "#888")
                    # Build factor bars HTML
                    _fact_bars = ""
                    _FLABEL = {"Sharpe":"Sharpe", "Sortino":"Sortino",
                               "MaxDrawdown":"MaxDD", "Calmar":"Calmar",
                               "Alpha":"Alpha", "ExpenseRatio":"費用率"}
                    for _fk, _fv in list((_fs.get("factors") or {}).items())[:6]:
                        _fw  = _fv.get("score", 50)
                        _fvc = "#00c853" if _fw >= 60 else ("#ff9800" if _fw >= 40 else "#f44336")
                        _flb = _FLABEL.get(_fk, _fk)
                        _fvv = _fv.get("value", "?")
                        try: _fvv = f"{float(_fvv):.2f}"
                        except: _fvv = str(_fvv)[:6]
                        _fact_bars += (
                            f"<div style='margin:3px 0'>"
                            f"<div style='display:flex;justify-content:space-between;"
                            f"font-size:9px;color:#666'><span>{_flb}</span><span>{_fvv}</span></div>"
                            f"<div style='height:4px;background:#1a1f2e;border-radius:2px'>"
                            f"<div style='width:{_fw:.0f}%;height:100%;background:{_fvc};"
                            f"border-radius:2px'></div></div></div>")
                    # Missing factors note
                    _fc_count = _fs.get("factors_count", 0)
                    _miss_note = ""
                    if _fc_count < 4:
                        _miss_note = (f"<div style='font-size:9px;color:#555;margin-top:4px'>"
                                      f"⚠️ 僅 {_fc_count}/6 因子有資料</div>")
                    st.markdown(
                        f"<div style='background:#0d1117;border:1px solid {_gc}44;"
                        f"border-radius:8px;padding:10px'>"
                        f"<div style='text-align:center'>"
                        f"<div style='font-size:11px;color:#888;margin-bottom:2px'>{_fname}</div>"
                        f"<div style='font-size:26px;font-weight:900;color:{_gc}'>{_sc:.0f}</div>"
                        f"<div style='font-size:11px;color:{_gc};margin-bottom:6px'>Grade {_gd}</div>"
                        f"</div>"
                        f"{_fact_bars}{_miss_note}"
                        f"<div style='font-size:9px;color:#444;margin-top:4px;text-align:center'>"
                        f"Sharpe:{(_fs.get('factors') or {}).get('Sharpe',{}).get('value','—')} "
                        f"MaxDD:{(_fs.get('factors') or {}).get('MaxDrawdown',{}).get('value','—')}"
                        f"</div>"
                        f"</div>", unsafe_allow_html=True)

    except Exception as _re:
        st.warning(f"⚠️ 風險預警模組載入失敗：{_re}")
        try:
            _write_error_ledger(_re, "risk alert module", GEMINI_KEY)
        except Exception:
            pass
else:
    st.info("💡 請先在 Tab1 載入總經數據，即可顯示即時風險預警")


# ══════════════════════════════════════════════════════════════════════
# v13.7 組合管理頁：批次手動診斷（當多檔基金都抓不到時）
# ══════════════════════════════════════════════════════════════════════
with st.expander("📋 批次手動健康診斷（多檔基金同時輸入）", expanded=False):
    st.markdown(
        "<div style='font-size:12px;color:#888;padding:4px 0 8px'>"
        "💡 ACTI71 / ACTI98 等境內基金若無法自動抓取，在此輸入數字即可做健康診斷<br>"
        "數據來源：安聯投信官網 <code>tw.allianzgi.com</code> / MoneyDJ 網頁手動查閱"
        "</div>", unsafe_allow_html=True)

    _batch_funds_default = ("基金名稱,目前淨值,一年前淨值,每單位月配息,配息頻率(月=12)\n""安聯AI收益成長基金B月配TWD,11.14,10.80,0.060,12\n""ACTI71 安聯平衡基金A1,12.50,12.00,0.065,12\n""ACTI98 安聯平衡基金A,12.30,11.90,0.060,12")

    _batch_csv = st.text_area(
        "貼入基金數據（CSV格式）",
        value=_batch_funds_default,
        height=140, key="batch_manual_csv",
        help="每行一檔基金，逗號分隔：名稱,目前淨值,一年前淨值,每單位配息,配息頻率")

    if st.button("🔬 批次健康診斷", key="btn_batch_diag"):
        from fund_fetcher import calc_health_from_manual
        _lines = [l.strip() for l in _batch_csv.strip().splitlines() if l.strip()]

        _batch_results = []
        for _bline in _lines[1:]:  # 跳過 header
            parts = [p.strip() for p in _bline.split(",")]
            if len(parts) < 4:
                continue
            try:
                _bh = calc_health_from_manual(
                    nav_current  = float(parts[1]),
                    nav_1y_ago   = float(parts[2]),
                    div_per_unit = float(parts[3]),
                    div_freq     = int(parts[4]) if len(parts) > 4 else 12,
                    fund_name    = parts[0],
                )
                _batch_results.append(_bh)
            except Exception as _be:
                _batch_results.append({"fund_name": parts[0], "error": str(_be)})

        if _batch_results:
            _cols = st.columns(min(len(_batch_results), 3))
            for _bi, _bres in enumerate(_batch_results):
                with _cols[_bi % 3]:
                    if _bres.get("error"):
                        st.error(f"{_bres['fund_name']}: {_bres['error']}")
                        continue
                    _bhc = _bres["health_color"]
                    st.markdown(
                        f"<div style='background:#0d1117;border:2px solid {_bhc};"
                        f"border-radius:10px;padding:12px;margin:4px 0;text-align:center'>"
                        f"<div style='font-size:11px;color:#888;margin-bottom:4px'>"
                        f"{_bres['fund_name'][:20]}</div>"
                        f"<div style='font-size:13px;font-weight:700;color:{_bhc};margin-bottom:8px'>"
                        f"{_bres['health']}</div>"
                        f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:11px'>"
                        f"<div style='background:#161b22;border-radius:4px;padding:4px'>"
                        f"<div style='color:#888'>含息報酬</div>"
                        f"<div style='color:{'#00c853' if _bres['total_return_pct']>=0 else '#f44336'};font-weight:700'>"
                        f"{_bres['total_return_pct']:+.2f}%</div></div>"
                        f"<div style='background:#161b22;border-radius:4px;padding:4px'>"
                        f"<div style='color:#888'>配息年化率</div>"
                        f"<div style='color:#ff9800;font-weight:700'>{_bres['div_yield_pct']:.2f}%</div></div>"
                        f"<div style='background:#161b22;border-radius:4px;padding:4px'>"
                        f"<div style='color:#888'>真實收益</div>"
                        f"<div style='color:{_bhc};font-weight:700'>{_bres['real_return_pct']:+.2f}%</div></div>"
                        f"<div style='background:#161b22;border-radius:4px;padding:4px'>"
                        f"<div style='color:#888'>淨值漲跌</div>"
                        f"<div style='color:#64b5f6;font-weight:700'>{_bres['nav_change_pct']:+.2f}%</div></div>"
                        f"</div></div>", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════
# 🤖 AI 全局投資決策（Tab 下方主畫面）
# ══════════════════════════════════════════════════════════════════
st.divider()
st.markdown(
    "<div style='text-align:center;padding:6px 0 2px'>"
    "<span style='font-size:22px'>🤖</span> "
    "<span style='font-size:18px;font-weight:700;color:#e0e0e0'>"
    "AI 全局投資決策</span>"
    "<span style='font-size:12px;color:#666;margin-left:10px'>"
    "嚴格基於上方數據 · 不引用外部資訊</span>"
    "</div>",
    unsafe_allow_html=True)

_ai_sk = "global_ai_result"

# ── 執行 AI 分析（pending 狀態觸發）────────────────────────
if st.session_state.get("_ai_pending"):
    st.session_state.pop("_ai_pending", None)
    _news_m  = st.session_state.get("news_headlines", [])
    _ind_m   = st.session_state.get("indicators", {})
    _ph_m    = st.session_state.get("phase_info", {})
    _pf_m    = st.session_state.get("portfolio_funds", [])
    _fd_m    = st.session_state.get("current_fund")
    _core_m  = st.session_state.get("portfolio_core_pct", 80)
    with st.spinner("🤖 AI 分析中，請稍候（約 20-40 秒）..."):
        from ai_engine import analyze_global
        _ai_result = analyze_global(
            GEMINI_KEY, _ind_m, _ph_m,
            portfolio_funds=_pf_m,
            focus_fund=_fd_m,
            news_headlines=_news_m,
            core_target_pct=_core_m,
        )
    st.session_state[_ai_sk] = _ai_result
    st.rerun()

# ── 顯示 AI 結果 ────────────────────────────────────────────
if _ai_sk in st.session_state:
    _ai_text = st.session_state[_ai_sk]
    # 結果分段顯示（依 ### 標題分欄）
    _sections = [s.strip() for s in _ai_text.split("###") if s.strip()]
    if len(_sections) >= 4:
        # 四節分成 2×2 格局
        _r1c1, _r1c2 = st.columns(2)
        _r2c1, _r2c2 = st.columns(2)
        _cols_map = [_r1c1, _r1c2, _r2c1, _r2c2]
        for _ci, (_col, _sec) in enumerate(zip(_cols_map, _sections)):
            with _col:
                _lines = _sec.split("\n", 1)
                _title = _lines[0].strip()
                _body  = _lines[1].strip() if len(_lines) > 1 else ""
                st.markdown(
                    f"<div style='background:#1a2332;border-radius:10px;"
                    f"padding:14px 16px;height:100%;min-height:180px'>"
                    f"<div style='font-size:15px;font-weight:700;color:#e0e0e0;"
                    f"margin-bottom:8px'>### {_title}</div>"
                    f"<div style='font-size:13px;color:#b0bec5;line-height:1.7'>"
                    f"{_body.replace(chr(10), '<br>')}"
                    f"</div></div>",
                    unsafe_allow_html=True)
    else:
        # fallback: 直接顯示全文
        st.markdown(
            f"<div style='background:#1a2332;border-radius:10px;padding:20px;"
            f"font-size:13px;color:#b0bec5;line-height:1.8'>"
            f"{_ai_text.replace(chr(10),'<br>')}</div>",
            unsafe_allow_html=True)
elif not GEMINI_KEY:
    st.info("💡 請在側邊欄填入 Gemini API Key 後按下【🚀 產出全局投資決策】")
else:
    _ai_macro_ok2 = st.session_state.get("macro_done", False)
    _ai_pf_ok2 = bool([f for f in st.session_state.get("portfolio_funds",[]) if f.get("loaded")])
    if not _ai_macro_ok2 and not _ai_pf_ok2:
        st.info("💡 請先載入 Tab1 總經數據 或 Tab2 投資組合，再按側邊欄【🚀 產出全局投資決策】")
    else:
        st.info("⬅️ 點擊左側【🚀 產出全局投資決策】開始 AI 分析")
# ══════════════════════════════════════════════════════════════
# 🔬 Tab4: 資料診斷面板
# ══════════════════════════════════════════════════════════════
with tab4:
    _diag_hdr, _diag_btn = st.columns([3, 1])
    with _diag_hdr:
        st.markdown("## 🔬 資料診斷")
        st.caption("確認所有數據來源是否成功下載，方便排查問題")
    with _diag_btn:
        st.markdown("<div style='margin-top:20px'></div>", unsafe_allow_html=True)
        if st.button("🔄 重新載入總經", key="btn_diag_refresh"):
            st.session_state.macro_done = False
            st.rerun()

    # ── Section 1: FRED / 總經指標 ──────────────────────────
    st.markdown("### 🌐 總經指標（FRED / yfinance）")
    _ind_diag = st.session_state.get("indicators", {})
    _phase_diag = st.session_state.get("phase_info", {})

    _EXPECTED_IND = [
        ("PMI",          "ISM製造業PMI",           "FRED",     "NAPM",      ">50擴張"),
        ("CPI",          "CPI年增率",               "FRED",     "CPIAUCSL",  "<2%理想"),
        ("UNEMPLOYMENT", "失業率",                  "FRED",     "UNRATE",    "<4.5%"),
        ("YIELD_10Y2Y",  "殖利率利差(10Y-2Y)",      "計算",     "DGS10-DGS2","倒掛=衰退"),
        ("YIELD_10Y3M",  "殖利率利差(10Y-3M)",      "計算",     "DGS10-TB3MS","最強衰退指標"),
        ("HY_SPREAD",    "高收益債利差",             "FRED",     "BAMLH0A0HYM2","<4%樂觀"),
        ("M2",           "M2貨幣供給YoY",           "FRED",     "M2SL",      ">5%寬鬆"),
        ("FED_BS",       "Fed資產負債表YoY",        "FRED",     "WALCL",     "擴表=利多"),
        ("FED_RATE",     "聯準會利率",              "FRED",     "FEDFUNDS",  "升/降息"),
        ("PPI",          "PPI生產者物價YoY",        "FRED",     "PPIACO",    "通膨上游"),
        ("VIX",          "VIX恐慌指數",             "yfinance", "^VIX",      "<18平靜"),
        ("DXY",          "美元指數",                "yfinance", "DX-Y.NYB",  "月漲跌"),
        ("ADL",          "市場廣度RSP/SPY",         "yfinance", "RSP/SPY",   "多頭健康度"),
        ("COPPER",       "銅博士月漲跌",            "yfinance", "HG=F",      "景氣領先"),
    ]

    _cols_hdr = st.columns([2, 2, 1, 2, 1, 2])
    for _ch, _hd in zip(_cols_hdr, ["指標代碼", "中文名稱", "來源", "FRED/Ticker", "數值", "狀態"]):
        _ch.markdown(f"<div style='font-size:11px;color:#888;font-weight:700'>{_hd}</div>",
                     unsafe_allow_html=True)
    st.markdown("<hr style='margin:4px 0;border-color:#30363d'>", unsafe_allow_html=True)

    _ok_cnt = _fail_cnt = _na_cnt = 0
    for _key, _name, _src, _ticker, _note in _EXPECTED_IND:
        _d = _ind_diag.get(_key, {})
        _val = _d.get("value") if _d else None
        _sig = _d.get("signal", "") if _d else ""
        _err = _d.get("error", "") if _d else ""
        if _val is not None and str(_val) != "" and _val == _val:  # not None/NaN
            _status_icon = "✅"; _status_c = "#00c853"; _ok_cnt += 1
            _unit = _d.get("unit","") or ""
            _date_tag = f" ({_d.get('date','')})" if _d.get("date") else ""
            # Format number nicely
            try:
                _val_fmt = f"{float(_val):.2f}"
            except Exception:
                _val_fmt = str(_val)[:12]
            _val_str = f"{_val_fmt}{_unit}{_date_tag}"
        elif _err:
            _status_icon = "❌"; _status_c = "#f44336"; _fail_cnt += 1
            _val_str = str(_err)[:35]
        elif not _ind_diag:
            _status_icon = "⬜"; _status_c = "#555"; _na_cnt += 1
            _val_str = "尚未載入"
        else:
            _status_icon = "⚠️"; _status_c = "#ff9800"; _na_cnt += 1
            _val_str = "⚠️ 無資料（FRED延遲？）"

        _row_cols = st.columns([2, 2, 1, 2, 1, 2])
        _row_cols[0].markdown(f"<code style='font-size:11px'>{_key}</code>", unsafe_allow_html=True)
        _row_cols[1].markdown(f"<span style='font-size:11px;color:#ccc'>{_name}</span>", unsafe_allow_html=True)
        _row_cols[2].markdown(f"<span style='font-size:10px;color:#888'>{_src}</span>", unsafe_allow_html=True)
        _row_cols[3].markdown(f"<code style='font-size:9px;color:#555'>{_ticker}</code>", unsafe_allow_html=True)
        _row_cols[4].markdown(
            f"<span style='font-size:12px;color:{_status_c}'>{_val_str}</span>",
            unsafe_allow_html=True)
        _row_cols[5].markdown(
            f"<span style='font-size:14px'>{_status_icon}</span>"
            f"<span style='font-size:9px;color:#555;display:block'>{_note}</span>",
            unsafe_allow_html=True)

    # Summary bar
    _total_ind = len(_EXPECTED_IND)
    _ok_pct = round(_ok_cnt / _total_ind * 100) if _total_ind > 0 else 0
    _bar_c = "#00c853" if _ok_pct >= 80 else ("#ff9800" if _ok_pct >= 50 else "#f44336")
    _last_upd_str = (st.session_state.macro_last_update.strftime("%H:%M")
                     if hasattr(st.session_state.get("macro_last_update",""), "strftime")
                     else "未更新")
    st.markdown(
        f"<div style='background:#1a1f2e;border-radius:8px;padding:10px 14px;margin-top:8px'>"
        f"<div style='display:flex;justify-content:space-between;font-size:12px;margin-bottom:6px'>"
        f"<span>"
        f"<span style='color:#00c853'>✅ 成功 {_ok_cnt}</span>　"
        f"<span style='color:#f44336'>❌ 失敗 {_fail_cnt}</span>　"
        f"<span style='color:#ff9800'>⚠️ 缺漏 {_na_cnt}</span>　"
        f"<span style='color:#888'>/ 共 {_total_ind} 項</span>"
        f"</span>"
        f"<span style='color:#888;font-size:11px'>最後更新：{_last_upd_str}</span>"
        f"</div>"
        f"<div style='height:8px;background:#0d1117;border-radius:4px;overflow:hidden'>"
        f"<div style='height:100%;width:{_ok_pct}%;background:{_bar_c};border-radius:4px'></div>"
        f"</div>"
        f"<div style='font-size:10px;color:{_bar_c};margin-top:3px;text-align:right'>"
        f"資料完整率 {_ok_pct}%</div>"
        f"</div>", unsafe_allow_html=True)

    # PMI missing warning
    if _ind_diag and not _ind_diag.get("PMI"):
        st.warning("⚠️ **PMI** 暫無資料 — FRED NAPM 系列通常延遲 1-2 個月發布，非抓取錯誤，待 ISM 官方發布後自動更新。")

    if _phase_diag:
        st.markdown(
            f"<div style='font-size:12px;color:#888;margin-top:6px'>"
            f"景氣位階：<b style='color:#e6edf3'>{_phase_diag.get('phase','?')}</b> "
            f"評分：<b style='color:#e6edf3'>{_phase_diag.get('score','?')}/10</b> "
            f"衰退率：<b style='color:#e6edf3'>{_phase_diag.get('rec_prob','?')}%</b>"
            f"</div>", unsafe_allow_html=True)

    st.divider()

    # ── Section 2: 投資組合基金診斷 ─────────────────────────
    st.markdown("### 📊 投資組合基金")
    _pf_diag = st.session_state.get("portfolio_funds", [])
    _cf_diag = st.session_state.get("current_fund")

    if not _pf_diag and not _cf_diag:
        st.info("尚未加入任何基金。請至「我的投資組合」或「個別基金分析」Tab 加入基金。")
    else:
        # Combine portfolio + individual fund for diagnosis
        _diag_list = list(_pf_diag)
        if _cf_diag and not any(f.get("code") == _cf_diag.get("fund_code","") for f in _diag_list):
            # Add individual fund as synthetic entry for diagnosis
            _cf_code = _cf_diag.get("fund_code","") or _cf_diag.get("full_key","")
            _diag_list.append({
                "code": _cf_code, "name": _cf_diag.get("fund_name","") or _cf_code,
                "loaded": True, "metrics": _cf_diag.get("metrics",{}),
                "moneydj_raw": _cf_diag, "dividends": _cf_diag.get("dividends",[]),
                "series": _cf_diag.get("series"), "_source": "個別基金分析",
            })
        if not _diag_list:
            st.info("尚未載入任何基金資料。")
        for _fd in _diag_list:
            _code  = _fd.get("code", "?")
            _name  = _fd.get("name", "") or _code
            _loaded = _fd.get("loaded", False)
            _m     = _fd.get("metrics", {}) or {}
            _mj    = _fd.get("moneydj_raw", {}) or {}
            _err   = _fd.get("error", "") or _mj.get("error", "")
            _nav   = _m.get("nav")
            _adr   = _mj.get("moneydj_div_yield") or _m.get("annual_div_rate")
            _perf  = _mj.get("perf", {}) or {}
            _risk  = _mj.get("risk_metrics", {}) or {}
            _divs_raw = _fd.get("dividends") or _mj.get("dividends")
            _divs = _divs_raw if isinstance(_divs_raw, list) else []
            _raw_series = _fd.get("series")
            try:
                import pandas as _pd_diag
                if _raw_series is None:
                    _series_len = 0
                elif isinstance(_raw_series, _pd_diag.Series):
                    _series_len = len(_raw_series)
                elif hasattr(_raw_series, "__len__"):
                    _series_len = len(_raw_series)
                else:
                    _series_len = 0
            except Exception:
                _series_len = 0

            with st.expander(
                f"{'✅' if _loaded and not _err else ('❌' if _err else '⬜')} "
                f"{_name[:30]} ({_code})",
                expanded=bool(_err)):

                _c1, _c2, _c3, _c4 = st.columns(4)

                def _diag_cell(col, label, value, ok_cond=True, fmt=None):
                    import pandas as _pd_dc
                    _is_empty = (value is None or value == "" or
                                 (isinstance(value, dict) and not value) or
                                 (isinstance(value, list) and not value) or
                                 (isinstance(value, _pd_dc.Series) and value.empty))
                    if _is_empty:
                        _ic, _vc = "⚠️", "#ff9800"
                        _vstr = "無資料"
                    else:
                        try:
                            _ok = bool(ok_cond)
                            _ic  = "✅" if _ok else "⚠️"
                            _vc  = "#00c853" if _ok else "#ff9800"
                            _vstr = fmt(value) if fmt else str(value)[:60]
                        except Exception:
                            _ic, _vc, _vstr = "⚠️", "#ff9800", str(value)[:30]
                    col.markdown(
                        f"<div style='background:#1a1f2e;border-radius:6px;padding:6px 8px'>"
                        f"<div style='font-size:9px;color:#666'>{label}</div>"
                        f"<div style='font-size:13px;color:{_vc};font-weight:700'>{_ic} {_vstr}</div>"
                        f"</div>", unsafe_allow_html=True)

                _diag_cell(_c1, "最新淨值 NAV", _nav,
                           ok_cond=(_nav is not None and _nav > 0),
                           fmt=lambda v: f"{v:.4f}")
                _diag_cell(_c2, "年化配息率", _adr,
                           ok_cond=(_adr is not None and float(_adr or 0) > 0),
                           fmt=lambda v: f"{float(v):.2f}%")
                _diag_cell(_c3, "1Y含息報酬", _perf.get("1Y"),
                           ok_cond=(_perf.get("1Y") is not None),
                           fmt=lambda v: f"{v:.2f}%")
                _diag_cell(_c4, "淨值歷史筆數", _series_len if _series_len > 0 else None,
                           ok_cond=(_series_len >= 30),
                           fmt=lambda v: f"{v} 筆")

                _c5, _c6, _c7, _c8 = st.columns(4)
                _rt = (_risk.get("risk_table") or {})
                _r1y = _rt.get("一年", {}) or {}
                _diag_cell(_c5, "配息記錄筆數", len(_divs) if _divs else None,
                           ok_cond=(len(_divs) >= 1),
                           fmt=lambda v: f"{v} 筆")
                _diag_cell(_c6, "標準差(1Y)", _r1y.get("標準差"),
                           ok_cond=(_r1y.get("標準差") is not None),
                           fmt=lambda v: f"{v}%")
                _diag_cell(_c7, "Sharpe(1Y)", _r1y.get("Sharpe"),
                           ok_cond=(_r1y.get("Sharpe") is not None),
                           fmt=lambda v: str(v))
                _diag_cell(_c8, "MoneyDJ wb01", _perf.get("1Y"),
                           ok_cond=(_perf.get("1Y") is not None),
                           fmt=lambda v: "wb01 ✓")

                # Holdings / structure
                _holdings_d_raw = _fd.get("holdings") or _mj.get("holdings") or {}
                _holdings_d = _holdings_d_raw if isinstance(_holdings_d_raw, dict) else {}
                _sectors_d  = _holdings_d.get("sector_alloc") or []
                _sectors_d  = _sectors_d if isinstance(_sectors_d, list) else []
                _top10_d    = _holdings_d.get("top_holdings") or []
                _top10_d    = _top10_d if isinstance(_top10_d, list) else []
                _has_struct = bool((_fd.get("moneydj_raw") or {}).get("investment_target"))
                _c9, _c10, _c11, _c12 = st.columns(4)
                _diag_cell(_c9,  "holdings物件",   _holdings_d or None,
                           ok_cond=bool(_holdings_d),
                           fmt=lambda v: "有資料 ✓")
                _diag_cell(_c10, "產業配置筆數",   len(_sectors_d) if _sectors_d else None,
                           ok_cond=(len(_sectors_d) >= 3),
                           fmt=lambda v: f"{v} 項")
                _diag_cell(_c11, "前10大持股",      len(_top10_d) if _top10_d else None,
                           ok_cond=(len(_top10_d) >= 5),
                           fmt=lambda v: f"{v} 檔")
                _diag_cell(_c12, "基本資料",        _has_struct or None,
                           ok_cond=_has_struct,
                           fmt=lambda v: "已取得 ✓")

                # Source tag
                _src_tag = _fd.get("_source", "投資組合")
                st.markdown(f"<span style='font-size:10px;color:#555'>資料來源：{_src_tag} | "
                            f"is_core: {_fd.get('is_core','?')} | "
                            f"currency: {_fd.get('currency',_mj.get('currency','?'))}</span>",
                            unsafe_allow_html=True)

                if _err:
                    st.error(f"❌ 錯誤訊息：{str(_err)[:200]}")

    st.divider()

    # ── Section 3: API Keys ──────────────────────────────────
    st.markdown("### 🔑 API 金鑰狀態")
    _ks_c1, _ks_c2 = st.columns(2)
    with _ks_c1:
        _fred_ok = bool(FRED_KEY)
        st.markdown(
            f"<div style='background:#1a1f2e;border-radius:8px;padding:12px'>"
            f"<div style='font-size:11px;color:#888'>FRED API Key</div>"
            f"<div style='font-size:16px;font-weight:700;color:{'#00c853' if _fred_ok else '#f44336'}'>"
            f"{'✅ 已設定' if _fred_ok else '❌ 未填寫'}</div>"
            f"<div style='font-size:10px;color:#555'>"
            f"{'...'+FRED_KEY[-6:] if _fred_ok and len(FRED_KEY)>6 else '請在 Cell 1 填入'}"
            f"</div></div>", unsafe_allow_html=True)
    with _ks_c2:
        _gem_ok = bool(GEMINI_KEY)
        st.markdown(
            f"<div style='background:#1a1f2e;border-radius:8px;padding:12px'>"
            f"<div style='font-size:11px;color:#888'>Gemini API Key</div>"
            f"<div style='font-size:16px;font-weight:700;color:{'#00c853' if _gem_ok else '#f44336'}'>"
            f"{'✅ 已設定' if _gem_ok else '❌ 未填寫'}</div>"
            f"<div style='font-size:10px;color:#555'>"
            f"{'...'+GEMINI_KEY[-6:] if _gem_ok and len(GEMINI_KEY)>6 else '請在 Cell 1 填入'}"
            f"</div></div>", unsafe_allow_html=True)

    # ── Section 4: Session State Raw Dump (Debug only) ───────
    if st.session_state.get("debug_mode"):
        st.divider()
        st.markdown("### 🛠️ Session State 原始資料（Debug 模式）")
        _dump = {}
        for _k, _v in st.session_state.items():
            if _k in ("indicators", "phase_info"):
                _dump[_k] = f"<dict, {len(_v)} keys>" if isinstance(_v, dict) else str(type(_v))
            elif _k == "portfolio_funds":
                _dump[_k] = f"<list, {len(_v)} funds>"
            elif _k == "market_news":
                _dump[_k] = f"<list, {len(_v)} items>" if isinstance(_v, list) else str(_v)
            else:
                _dump[_k] = str(_v)[:120]
        st.json(_dump)

        # Raw indicators
        if st.session_state.get("indicators"):
            st.markdown("#### 原始指標數據")
            st.json({k: {kk: str(vv)[:50] for kk, vv in v.items()}
                     for k, v in st.session_state.indicators.items()})

# ══════════════════════════════════════════════════════════════════════
# 📖 Tab5 — 說明書（公式與判斷邏輯詳解）
# ══════════════════════════════════════════════════════════════════════
with tab5:
    import pandas as _pd_doc  # Fix: ensure _pd_doc in scope for entire tab5
    st.markdown("## 📖 系統說明書 v16.0 — 公式與判斷標準完整說明")
    st.caption("本說明書解釋系統中所有評分模型、公式與指標的計算方式，方便進階使用者理解決策邏輯。")

    _doc_tabs = st.tabs([
        "🧮 1. Macro Score",
        "🌤️ 2. 景氣天氣",
        "🏆 3. 六因子評分",
        "🔴 4. 吃本金診斷",
        "⚖️ 5. 再平衡公式",
        "🇹🇼 6. 台股TPI",
        "🛡️⚡ 7. 核心衛星分類",
        "🆕 8. v16.0 新增公式",
    ])

    # ── 1. Macro Score ─────────────────────────────────────────────
    with _doc_tabs[0]:
        st.markdown("### 🧮 AI Macro Score — 加權景氣評分")
        st.markdown("""
**公式：**
```
Macro_Score = Σ(wᵢ × sᵢ) / Σ(wᵢ)  →  正規化到 0~10
```
其中 `sᵢ` 是每個指標的得分，`wᵢ` 是對應的權重。

**正規化公式：**
```
score_normalized = (earned_score + total_weight) / (2 × total_weight) × 10
```
""")
        _score_data = [
            ["殖利率利差 10Y-2Y", "DGS10-DGS2",  2, "±2", "倒掛(<0)=-2，翻正=+2，>0.5=+1"],
            ["殖利率利差 10Y-3M", "DGS10-TB3MS", 2, "±2", "倒掛=-2，翻正=+3（降息確認）"],
            ["PMI 製造業",       "NAPM",         2, "±2", ">50=+2，45~50=-1，<45=-2"],
            ["HY 信用利差",       "BAMLH0A0HYM2",2, "±2", "<4%=+2，4~6%=0，>6%=-2"],
            ["M2 流動性",        "M2SL",         1, "±1", ">5%=+1，<0%=-1"],
            ["市場廣度 RSP/SPY", "RSP/SPY",      1, "±1", "月漲>0.5%=+1，月跌>1%=-1"],
            ["DXY 美元指數",     "DX-Y.NYB",     1, "±1", "月跌>1%=+1（弱美元利多），月漲>2%=-1"],
            ["Fed 資產負債表",   "WALCL",         1, "±1", "擴表>5%=+1，縮表<-5%=-1"],
            ["VIX 恐慌指數",     "^VIX",          1, "±1", "<18=+1（平靜），>30=-1（恐慌）"],
            ["CPI 通膨率",       "CPIAUCSL",     0.5,"±0.5","1~2.5%=+1，>4%=-1"],
            ["Fed Rate",         "FEDFUNDS",     0.5,"±0.5","降息=+1，>5%=-1"],
            ["失業率",            "UNRATE",       0.5,"±0.5","<4.5%=+1，>6%=-2"],
            ["PPI 生產者物價",   "PPIACO",        0.5,"±0.5","0~3%=+0.5，>5%=-0.5"],
            ["銅博士",            "HG=F",          0.5,"±0.5","月漲>2%=+0.5，月跌>5%=-0.5"],
        ]
        import pandas as _pd_doc
        _df_score = _pd_doc.DataFrame(_score_data,
            columns=["指標","FRED/Ticker","權重(w)","分值範圍","評分邏輯"])
        st.dataframe(_df_score, use_container_width=True, hide_index=True)

        st.markdown("""
**景氣位階對應：**
| Score | 位階 | 建議股債現金 |
|-------|------|------------|
| 8~10  | 🔴 高峰  | 股 35% / 債 45% / 現金 20% |
| 5~7   | 🟢 擴張  | 股 60% / 債 30% / 現金 10% |
| 3~4   | 🔵 復甦  | 股 40% / 債 40% / 現金 20% |
| 0~2   | 🟡 衰退  | 股 20% / 債 50% / 現金 30% |
""")

    # ── 2. 景氣天氣 ────────────────────────────────────────────────
    with _doc_tabs[1]:
        st.markdown("### 🌤️ 總經天氣預報 — Score → 天氣映射")
        st.markdown("""
**公式：**
```
Score ≥ 7  → ☀️ 晴天 (建議股票為主)
4 ≤ Score < 7 → ⛅ 多雲 (均衡配置)
Score < 4  → ⛈️ 暴雨 (防禦為主)
```

| 天氣 | Score 範圍 | 建議配置 | 行動 |
|------|----------|---------|------|
| ☀️ 晴天 | ≥ 7 | 股多債少 | 增加衛星部位，持有成長型基金 |
| ⛅ 多雲 | 4~6 | 股債均衡 | 維持核心配置，輕倉衛星 |
| ⛈️ 暴雨 | < 4 | 債多現金多 | 啟動防禦，核心配息資產優先 |
""")

    # ── 3. 六因子評分 ─────────────────────────────────────────────
    with _doc_tabs[2]:
        st.markdown("### 🏆 基金六因子評分（Fund Factor Model）")
        st.markdown("""
**公式：**
```
Fund_Score = Σ(因子得分ᵢ × 權重ᵢ) / Σ(權重ᵢ)    範圍：0~100
```
""")
        _factor_data = [
            ["1. Sharpe Ratio",  "每單位風險的超額報酬",      "25%",
             "score = min(max((Sharpe+1)/2×100, 0), 100)",
             "Sharpe=-1→0分；=0→50分；=+1→100分",
             "MoneyDJ wb07 一年Sharpe"],
            ["2. Sortino Ratio", "只懲罰下行波動的風險調整報酬","15%",
             "score = min(max((Sortino+1)/2×100, 0), 100)",
             "同 Sharpe 但只計負報酬標準差",
             "calc_metrics() 計算"],
            ["3. Max Drawdown",  "歷史最慘跌幅（越小越好）",   "20%",
             "score = min(max((1 - |MaxDD|/30)×100, 0), 100)",
             "MaxDD=0%→100分；=-30%→0分",
             "淨值歷史計算"],
            ["4. Calmar Ratio",  "年化報酬/最大回撤",         "10%",
             "score = min(max(Calmar/2×100, 0), 100)",
             "Calmar=0→0分；=2→100分",
             "calc_metrics() 計算"],
            ["5. Alpha",         "含息報酬率 - 配息年化率",    "20%",
             "score = min(max((Alpha+10)/20×100, 0), 100)",
             "Alpha=-10%→0分；=0→50分；=+10%→100分",
             "wb01含息報酬 - wb05配息率"],
            ["6. 費用率",        "年度管理費用（越低越好）",   "10%",
             "score = min(max((3-費用率)/3×100, 0), 100)",
             "費用率=0%→100分；=3%→0分",
             "MoneyDJ 基金資料"],
        ]
        _df_factor = _pd_doc.DataFrame(_factor_data,
            columns=["因子","說明","權重","計算公式","數值對應","資料來源"])
        st.dataframe(_df_factor, use_container_width=True, hide_index=True)

        st.markdown("""
**Grade 等級：**
| Score | Grade | 說明 |
|-------|-------|------|
| 75~100 | **A** | 優秀：風險調整後表現卓越 |
| 55~74  | **B** | 良好：整體表現在平均以上 |
| 40~54  | **C** | 普通：需關注弱項，考慮是否汰換 |
| 0~39   | **D** | 待改善：建議評估替代標的 |

⚠️ **注意**：若某因子缺乏資料（如 Sortino/Calmar），該因子**不計入**加權總分，
最少需要 Sharpe + Alpha 兩項才能計算有意義的分數。
""")

    # ── 4. 吃本金診斷 ─────────────────────────────────────────────
    with _doc_tabs[3]:
        st.markdown("### 🔴 吃本金診斷（Capital Return Detection）")
        st.markdown("""
**MK 以息養股核心公式：**
```
含息總報酬 = 含息報酬率(wb01 1Y)         ← 資料來源：MoneyDJ wb01
                                             （已涵蓋配息，無需再加）

吃本金判斷：含息總報酬 < 年化配息率
```

**資料來源優先序：**
| 數據 | 優先來源 | 備援 |
|------|---------|------|
| 含息報酬率 | MoneyDJ **wb01**（含息實績） | 淨值漲跌% + 配息率 |
| 年化配息率 | MoneyDJ **wb05**（官方值） | 自算：近12月配息/平均淨值 |

**燈號：**
- ✅ **健康**：含息報酬率 ≥ 配息率（有淨值成長作支撐）
- ⚠️ **警示**：含息報酬率略低於配息率（正在侵蝕本金）
- 🔴 **吃本金**：含息報酬率 << 配息率（配息主要來自本金返還）

**實例：**
```
安聯收益成長：含息1Y = +5.2%，配息率 = 9.6%
  → 差距 -4.4%，代表每年淨值被侵蝕 4.4%
  → 繼續持有10年後，本金將大幅減損
```
""")

    # ── 5. 再平衡公式 ─────────────────────────────────────────────
    with _doc_tabs[4]:
        st.markdown("### ⚖️ 再平衡公式（Module 4 One-Click Rebalance）")
        st.markdown("""
**MK 再平衡差額計算：**
```
Action_i = (Total_Portfolio × Target_Weight_i) - Current_Value_i
```

**觸發條件（MK 標準）：**
| 偏離程度 | 動作 |
|---------|------|
| < 5%   | ✅ 配置正常，無需再平衡 |
| 5~10%  | ⚠️ 建議再平衡（下次配息時執行） |
| > 10%  | 🚨 必須執行再平衡 |

**白話文行動指南生成邏輯：**
```
偏移方向 = 目前核心% - 目標核心%

> 0 → 核心太多，建議：
    從「最大衛星基金」贖回 ΔNT$
    轉入「最小核心基金」

< 0 → 衛星太多，建議：
    從「最大核心基金」獲利了結 ΔNT$
    轉入「最小衛星基金」
```

**注意**：偏離金額 = |偏移%| × 總投入金額
""")

    # ── 6. 台股TPI ────────────────────────────────────────────────
    with _doc_tabs[5]:
        st.markdown("### 🇹🇼 台灣市場轉折點指標（TPI v15.1）")
        st.markdown("""
**公式：**
```
TPI = Z(Breadth) × 0.4 + Z(FII) × 0.3 + Z(M1B/M2) × 0.3
```

| 因子 | 說明 | 正規化方式 | 資料來源 |
|------|------|----------|---------|
| **Z(Breadth)** 市場寬度 | (上漲家數-下跌家數)/(上漲+下跌) × 100 | ÷20，限制在±3 | 證交所 MI_INDEX |
| **Z(FII)** 外資淨買 | 外資買超-賣超（元） | ÷50億，限制在±3 | FinMind API（免費） |
| **Z(M1B/M2)** 貨幣動能 | M1B成長率 vs M2成長率的黃金/死亡交叉 | 暫設0，月頻更新 | 央行（待串接） |

**水溫對應：**
| TPI | 水溫 | 訊號 | 建議行動 |
|-----|------|------|---------|
| ≥ +1.5 | 🥵 沸點 | 🔴 | 上漲家數銳減，啟動獲利了結 |
| +0.5 ~ +1.5 | 🌡️ 溫熱 | 🟡 | 持續觀察，衛星設停利 |
| -0.5 ~ +0.5 | ⚖️ 常溫 | ⚪ | 維持配置，觀察變化 |
| -1.5 ~ -0.5 | 🌡️ 偏冷 | 🟡 | 外資轉弱，降低台股部位 |
| ≤ -1.5 | 🥶 冰點 | 🟢 | 散戶絕望期，分批建倉訊號 |

⚠️ TPI 僅為輔助參考指標，不代表精確的買賣訊號，需配合景氣位階綜合判斷。

**資料來源（完全免費，無需 Token）：**
- 市場寬度 → [TWSE MI_INDEX](https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=MS)
- 外資籌碼 → [FinMind TaiwanStockTotalInstitutionalInvestors](https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockTotalInstitutionalInvestors)
- M1B/M2 → [CBC ms1.json](https://www.cbc.gov.tw/public/data/ms1.json)（台灣央行官方公開資料）
""")

    # ── 7. 核心衛星分類 ───────────────────────────────────────────
    with _doc_tabs[6]:
        st.markdown("### 🛡️⚡ 核心/衛星分類邏輯")
        st.markdown("""
**優先序：手動設定 > 關鍵字比對 > 預設（衛星）**

**關鍵字規則：**
""")
        _kw_data = [
            ["🛡️ 核心", "債、收益、配息、平衡、高息、公用、多元、income、bond、dividend、balanced"],
            ["⚡ 衛星", "AI、科技、半導體、成長、主題、印度、越南、生技、醫療、能源、原物料、新興、tech、growth"],
        ]
        st.dataframe(_pd_doc.DataFrame(_kw_data, columns=["分類","觸發關鍵字（基金名稱含有任一）"]),
                     use_container_width=True, hide_index=True)

        st.markdown("""
**β 係數分類（Module 2 — 定海神針 vs 衝鋒陷陣）：**
| β 值 | 標籤 | 說明 | 建議比重 |
|------|------|------|---------|
| < 0.8 | 🛡️ 定海神針 | 低波動，抗跌性強 | 核心部位 60~80% |
| 0.8~1.2 | ⚖️ 市場同步 | 與大盤連動 | 視景氣位階調整 |
| > 1.2 | 🚀 衝鋒陷陣 | 高波動，高潛在報酬 | 衛星部位 10~20% |

**MK 核心/衛星比例目標（預設 80/20）：**
```
核心資產：提供穩定現金流（每月配息），作為「養」衛星的資金來源
衛星資產：追求價差成長，由核心配息「養」，不動用本金
```
偏離 >5% → ⚠️ 建議再平衡  
偏離 >10% → 🚨 必須執行  
""")

    # ── 8. 汰弱留強評分 ──────────────────────────────────────────
    with _doc_tabs[7]:
        st.markdown("### 🔄 汰弱留強評分（Security Ranking）")
        st.markdown("""
**核心邏輯：定期汰換績效落後的基金，換入同類前段班**

**觸發條件（任一滿足即亮警示）：**
| 條件 | 說明 | 建議行動 |
|------|------|---------|
| 同類四分位連續 ≥2季 落後（第3或4分位） | 長期跑輸同類 | ⚠️ 追蹤；若第3季仍落後 → 換 |
| 同類四分位連續 ≥2季 第4分位（後25%）| 嚴重落後 | 🚨 跨行轉存至前25%標的 |
| 吃本金（含息報酬 < 配息率）連續發生 | 本金持續侵蝕 | 🔴 優先汰換 |
| MaxDrawdown 超過同類平均 1.5x | 跌幅過大 | ⚠️ 評估是否替換 |

**汰弱留強評分公式（60分及格）：**
```
汰弱分數 = 含息報酬率 × 40%
         + Sharpe 比率 × 30%
         + (費用率 vs 同類均值) × 30%

< 60分 → 考慮汰換
≥ 75分 → 保留
```

**四分位等級說明：**
| 等級 | 排名 | 含義 |
|------|------|------|
| 第1四分位 | 前25% | 同類最強，優先持有 |
| 第2四分位 | 26~50% | 中上，繼續持有 |
| 第3四分位 | 51~75% | 中下，開始觀察 |
| 第4四分位 | 後25% | 最弱，考慮汰換 |

**實際操作原則（MK 方法論）：**
1. 每季（3個月）看一次同類排名
2. 連續2季後25% → 啟動汰換計畫（不要急，給它機會）
3. 找好替換標的後，在「買點」時換（避免在高點換進）
4. 核心資產不輕易換（穩定配息 > 短期績效排名）
""")
