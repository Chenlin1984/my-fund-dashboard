#!/usr/bin/env python3
"""app.py — 基金戰情室 v18.0（重構版）
四模組架構：總經 / 單一基金 / 組合基金 / 回測
零快取：每次操作皆即時抓取，確保資料絕對最新
"""
import streamlit as st
import os, datetime, re, time as _time_mod
import plotly.graph_objects as go
import pandas as pd
import numpy as np

TW_TZ = datetime.timezone(datetime.timedelta(hours=8))
def _now_tw():
    return datetime.datetime.now(TW_TZ)

from macro_engine  import fetch_all_indicators, calc_macro_phase, ENGINE_VERSION, detect_systemic_risk
from fund_fetcher  import (
    fetch_fund_by_key, search_moneydj_by_name,
    fetch_fund_structure, fetch_fund_from_moneydj_url,
    tdcc_search_fund, get_proxy_config,
    safe_float, classify_fetch_status, clean_risk_table,
    normalize_result_state, merge_non_empty, set_risk_free_rate,
    fetch_market_news,
)
from ai_engine       import analyze_macro, analyze_fund_json, analyze_macro_structured
from backtest_engine import calc_performance_metrics, quick_backtest, backtest_portfolio
from portfolio_engine import (
    calc_fund_factor_score,
    dividend_safety as div_safety_check,
    risk_alert as portfolio_risk_alert,
)
from macro_engine import identify_regime

APP_VERSION = "v18.2_CoreProtocol"

# ══════════════════════════════════════════════════════
# Page config & CSS
# ══════════════════════════════════════════════════════
st.set_page_config(page_title="基金戰情室", page_icon="📊",
                   layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
body,.stApp{background:#0e1117;color:#e6edf3}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 18px;margin:6px 0}
.signal-buy{background:#1c3a2a;color:#3fb950;border:1px solid #3fb950;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;display:inline-block}
.signal-sell{background:#3a1010;color:#f85149;border:1px solid #f85149;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;display:inline-block}
.signal-hold{background:#1a3450;color:#58a6ff;border:1px solid #58a6ff;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;display:inline-block}
.signal-switch{background:#3a2a10;color:#f0b132;border:1px solid #f0b132;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;display:inline-block}
</style>""", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════
# Keys & Session State
# ══════════════════════════════════════════════════════
def _load_keys():
    fred = st.secrets.get("FRED_API_KEY","") or os.environ.get("FRED_API_KEY","")
    gem  = st.secrets.get("GEMINI_API_KEY","") or os.environ.get("GEMINI_API_KEY","")
    if fred: os.environ["FRED_API_KEY"]   = fred
    if gem:  os.environ["GEMINI_API_KEY"] = gem
    return fred, gem

FRED_KEY, GEMINI_KEY = _load_keys()

for _k, _v in {
    "macro_done":False,"indicators":{},"phase_info":{},
    "macro_last_update":None,"macro_ai":"",
    "prev_phase":"","phase_history":[],
    "current_fund":None,"fund_data":None,
    "tdcc_results":[],"mj_fund_data":None,
    "portfolio_funds":[],"portfolio_core_pct":75,
    "news_items":[],"systemic_risk_data":None,
    "api_latency_log":[],
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ══════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("## 📊 基金戰情室")
    _upd = st.session_state.get("macro_last_update")
    st.caption(f"📡 總經：{_upd.strftime('%m/%d %H:%M') if _upd else '未載入'}　|　{_now_tw().strftime('%m/%d %H:%M')} TW")
    st.markdown(f"<div style='background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:8px 12px;font-size:11px;color:#888'>App {APP_VERSION} | Engine {ENGINE_VERSION} | Fetcher v6.24</div>", unsafe_allow_html=True)
    st.divider()
    _proxy_cfg = get_proxy_config()
    _proxy_ep  = ""
    if _proxy_cfg:
        _m = re.search(r'@(.+)', _proxy_cfg.get("http",""))
        _proxy_ep = _m.group(1) if _m else "已設定"
    st.markdown(f"{'✅' if FRED_KEY else '❌'} FRED　　{'✅' if GEMINI_KEY else '❌'} Gemini　　{'✅' if _proxy_cfg else '⚠️'} Proxy")
    st.caption(f"🔒 {_proxy_ep}" if _proxy_cfg else "⚠️ Proxy 未設定（MoneyDJ 可能被擋）")
    st.divider()
    if st.sidebar.button("🔍 測試 Proxy 連線", use_container_width=True):
        import requests as _req
        _pcfg = get_proxy_config()
        if not _pcfg:
            st.sidebar.error("Proxy 未設定")
        else:
            for _nm, _url in [("MoneyDJ","http://www.moneydj.com/"),("TDCC","https://openapi.tdcc.com.tw/")]:
                try:
                    _r = _req.get(_url, proxies=_pcfg, timeout=25, allow_redirects=False, verify=False)
                    if _r.status_code in (200,301,302,403): st.sidebar.success(f"✅ {_nm} 可達！HTTP {_r.status_code}")
                    elif _r.status_code == 407: st.sidebar.error("❌ 407：帳密錯誤"); break
                    else: st.sidebar.warning(f"⚠️ {_nm} HTTP {_r.status_code}")
                except _req.exceptions.ProxyError as _e: st.sidebar.error(f"❌ {_nm} ProxyError：{str(_e)[:120]}")
                except _req.exceptions.Timeout: st.sidebar.error(f"❌ {_nm} Timeout（25s）")
                except Exception as _e: st.sidebar.error(f"❌ {_nm}：{str(_e)[:120]}")
    if st.sidebar.button("♻️ 強制同步 GitHub 最新邏輯", use_container_width=True):
        st.rerun()

# ══════════════════════════════════════════════════════
# HELPER: assign_asset_role
# ══════════════════════════════════════════════════════
def assign_asset_role(fund_name: str) -> bool:
    name = (fund_name or "").lower()
    CORE_WL = ["安聯收益成長","收益成長","多元收益","安聯多元入息","摩根多重收益","富達多重資產","聯博收益","柏瑞多重資產","施羅德多元收益","瀚亞多重資產","富蘭克林收益","先機多元收益"]
    if any(w in name for w in CORE_WL): return True
    STRONG = ["配息","高股息","投資等級債","非投資等級債","公司債","公債","債券","債","特別股","基建","公用事業","infrastructure","preferred","utility","corporate bond","income fund","bond fund","fixed income"]
    if any(k in name for k in STRONG): return True
    core_kw = ["收益","平衡","多元","多重資產","balanced","income","bond","fixed","dividend","多重收益","全球股息","全球高股息"]
    sat_kw  = ["科技","ai","半導體","生技","醫療","電動車","創新","綠能","機器人","網通","印度","越南","中國a股","a股","航太","theme","tech","growth","biotech","semiconductor","robot","ev","india","vietnam"]
    hc = any(k in name for k in core_kw); hs = any(k in name for k in sat_kw)
    if hc and hs: return True
    if hc: return True
    if hs: return False
    return False

# ══════════════════════════════════════════════════════
# HELPER: mk_fund_signal
# ══════════════════════════════════════════════════════
def mk_fund_signal(fund_info: dict, phase: str, score: float) -> dict:
    name  = (fund_info.get("基金名稱","") or fund_info.get("name","") or fund_info.get("fund_name","")).lower()
    ftype = (fund_info.get("基金種類","") or "").lower()
    core_kw = ["收益","配息","債","高股息","均衡","平衡","公債","income","bond","fixed"]
    sat_kw  = ["科技","ai","半導體","新興","生技","成長","tech","equity","growth","theme"]
    is_core = any(k in name or k in ftype for k in core_kw)
    is_sat  = any(k in name or k in ftype for k in sat_kw) and not is_core
    asset_class = "核心資產 🛡️" if is_core else ("衛星資產 ⚡" if is_sat else "混合型 ⚖️")
    RECS = {
        "復甦": {True:("🟢 買進加碼","buy","復甦期景氣反轉，核心配息資產為最高勝率佈局"),False:("🟢 積極買進","buy","復甦期是衛星資產最佳進場點，成長基金爆發力強")},
        "擴張": {True:("⚪ 持有核心","hold","擴張期繼續持有核心配息資產，定期收息再投入"),False:("🟡 持有設停利","hold","擴張期衛星資產保持持有，設停利點 +10~15%")},
        "高峰": {True:("🟡 持有減碼","switch","景氣高峰，核心資產可適度減碼增加防禦性債券"),False:("🔴 賣出獲利","sell","高峰期衛星資產應積極獲利了結，避免高基期風險")},
        "衰退": {True:("🟢 逢低買進","buy","衰退末期優先佈局核心配息資產，等待景氣拐點"),False:("⏸️ 觀望等待","hold","衰退期衛星資產避免進場，等待PMI落底確認訊號")},
    }
    label, sig_type, reason = RECS.get(phase, RECS["擴張"])[is_core]
    SIG = {"buy":"background:#1a3328;color:#00c853;border:1px solid #00c853","sell":"background:#3a1a1a;color:#f85149;border:1px solid #f85149","hold":"background:#1a3450;color:#58a6ff;border:1px solid #58a6ff","switch":"background:#3a2a10;color:#f0a500;border:1px solid #f0a500"}
    sig_style = SIG.get(sig_type, SIG["hold"])
    _ind  = st.session_state.get("indicators", {})
    _pmi  = _ind.get("PMI",{}).get("value"); _vix = _ind.get("VIX",{}).get("value")
    _ue   = _ind.get("UNEMPLOYMENT",{}).get("value")
    _cpi  = _ind.get("CPI",{}).get("value"); _cpip = _ind.get("CPI",{}).get("prev")
    auto_alloc = None
    if _pmi and _vix:
        pf, vf = float(_pmi), float(_vix)
        if pf>50 and vf<20: auto_alloc=(70,30,"復甦/擴張—積極","#00c853")
        elif pf>50:          auto_alloc=(60,40,"擴張—穩健","#69f0ae")
        elif pf<50 and vf>25: auto_alloc=(40,60,"衰退—保守","#f44336")
        else:                auto_alloc=(50,50,"觀望—中性","#ff9800")
    if _ue:
        try:
            if float(_ue)>4.0: auto_alloc=(40,60,f"衰退（失業率{float(_ue):.1f}%破4%）","#f44336")
        except: pass
    if _cpi and _cpip:
        try:
            if float(_cpi)>float(_cpip) and float(_cpi)>3.0: auto_alloc=(50,50,f"升息尾聲—均衡（CPI {float(_cpi):.1f}%↑）","#ff9800")
        except: pass
    return dict(asset_class=asset_class, label=label, sig_type=sig_type, sig_style=sig_style, reason=reason, auto_alloc=auto_alloc)

# ══════════════════════════════════════════════════════
# HELPER: _quartile_check
# ══════════════════════════════════════════════════════
def _quartile_check(peer_compare: dict, risk_table: dict) -> dict:
    out = {"quartile":None,"color":"#888","label":"無同類資料","warning":False,"fund_sharpe":None,"peer_avg":None,"advice":""}
    if not peer_compare and not risk_table: return out
    fund_sh = None
    try: fund_sh = float(str(risk_table.get("一年",{}).get("Sharpe","") or "").replace("—",""))
    except: pass
    peer_sharpes = []
    for row_v in (peer_compare or {}).values():
        if isinstance(row_v, dict):
            for k2, v2 in row_v.items():
                if "sharpe" in k2.lower() or "夏普" in k2:
                    try: peer_sharpes.append(float(str(v2).replace("—","")))
                    except: pass
            try:
                sh_v = float(str(row_v.get("Sharpe", row_v.get("夏普","")) or "").replace("—",""))
                peer_sharpes.append(sh_v)
            except: pass
    if fund_sh is None and not peer_sharpes: return out
    if not peer_sharpes:
        q = 1 if fund_sh > 1.5 else (2 if fund_sh > 0.8 else (3 if fund_sh > 0 else 4))
        c = ["#00c853","#69f0ae","#ff9800","#f44336"][q-1]
        lbl = ["第1四分位🏆(前25%)","第2四分位✅(前50%)","第3四分位⚠️(後50%)","第4四分位🔴(後25%)"][q-1]
        adv = "⚠️ 後25%達2季→建議跨行轉存至同類前25%標的" if q==4 else ("追蹤：若下季仍第3四分位考慮替換" if q==3 else "")
        return {"quartile":q,"color":c,"label":lbl,"warning":q>=4,"fund_sharpe":fund_sh,"peer_avg":None,"advice":adv}
    import statistics as _stat
    ps = sorted(peer_sharpes); n = len(ps)
    q25 = ps[max(0,n//4-1)]; q75 = ps[min(n-1,3*n//4)]; pavg = _stat.mean(ps)
    sh_ref = fund_sh if fund_sh is not None else pavg
    if sh_ref>=q75:    q,c,lbl = 1,"#00c853","第1四分位🏆(前25%)"
    elif sh_ref>=pavg: q,c,lbl = 2,"#69f0ae","第2四分位✅(前50%)"
    elif sh_ref>=q25:  q,c,lbl = 3,"#ff9800","第3四分位⚠️(後50%)"
    else:              q,c,lbl = 4,"#f44336","第4四分位🔴(後25%—警戒)"
    adv = "⚠️ 後25%達2季→建議跨行轉存至同類前25%標的" if q>=4 else ("注意：若下季仍第3四分位，考慮替換" if q==3 else "")
    return {"quartile":q,"color":c,"label":lbl,"warning":q>=4,"fund_sharpe":fund_sh,"peer_avg":round(pavg,3),"advice":adv}

# ══════════════════════════════════════════════════════
# TABS
# ══════════════════════════════════════════════════════
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["🌐 總經","🔍 單一基金","📊 組合基金","🔬 回測","🔬 資料診斷","📖 說明書"])

# ══════════════════════════════════════════════════════
# TAB 1 — 總經
# ══════════════════════════════════════════════════════
with tab1:
    st.markdown("## 🌐 總經位階評估 ＆ 拐點偵測")
    st.caption("MK 三層指標加權方法論 v7 — 領先×2 | 中級×1 | 次級×0.5")

    if not FRED_KEY:
        st.warning("⚠️ 請在 Streamlit Cloud Secrets 填入 FRED_API_KEY")
    else:
        _last_upd = st.session_state.get("macro_last_update")
        if _last_upd:
            _age_h   = (_now_tw() - _last_upd).total_seconds() / 3600
            _upd_str = _last_upd.strftime("%Y-%m-%d %H:%M")
            if _age_h > 4:
                st.warning(f"⏰ 總經資料已 {_age_h:.1f} 小時未更新（上次：{_upd_str}），建議重新載入")
            else:
                st.caption(f"🕐 最後從 FRED 抓取：{_upd_str}（{_age_h:.1f} 小時前）")
        else:
            st.info("💡 尚未載入總經資料，點擊下方按鈕開始")

        _btn_label = "🔄 更新總經資料" if st.session_state.macro_done else "📡 載入總經資料"
        if st.button(_btn_label, type="primary", key="btn_macro_load"):
            with st.spinner("📡 從 FRED / Yahoo Finance 抓取最新指標..."):
                _t0_macro = _time_mod.time()
                ind   = fetch_all_indicators(FRED_KEY)
                _macro_ms = round((_time_mod.time() - _t0_macro) * 1000)
                phase = calc_macro_phase(ind)
                old_phase = (st.session_state.phase_info.get("phase","")
                             if st.session_state.phase_info else "")
                new_phase = phase.get("phase","")
                if old_phase and old_phase != new_phase:
                    st.session_state.phase_history.append(
                        {"from":old_phase,"to":new_phase,
                         "date":datetime.date.today().isoformat(),
                         "score":phase.get("score",0)})
                st.session_state.indicators        = ind
                st.session_state.prev_phase        = old_phase
                st.session_state.phase_info        = phase
                st.session_state.macro_done        = True
                st.session_state.macro_ai          = ""
                st.session_state.macro_last_update = _now_tw()
                if ind and "FED_RATE" in ind:
                    set_risk_free_rate(ind["FED_RATE"].get("value",4.0) / 100)
                # ── 記錄 API 延遲（供 Tab5 延遲趨勢圖）──
                _lat_log = st.session_state.get("api_latency_log", [])
                _lat_log.append({
                    "label":    _now_tw().strftime("%H:%M"),
                    "macro_ms": _macro_ms,
                    "moneydj_ms": None,
                    "yf_ms":      None,
                })
                st.session_state["api_latency_log"] = _lat_log[-24:]
                st.success(f"✅ 已抓取 {len(ind)} 個指標！（{_now_tw().strftime('%H:%M')} TW｜{_macro_ms}ms）")
            with st.spinner("📰 抓取市場新聞 + 系統性風險掃描..."):
                try:
                    _news = fetch_market_news(max_per_feed=5)
                    st.session_state.news_items = _news
                    _srd = detect_systemic_risk(_news)
                    st.session_state.systemic_risk_data = _srd
                    _rl = _srd.get("risk_level","LOW")
                    _rs = _srd.get("risk_score",0)
                    st.info(f"📰 已掃描 {len(_news)} 則新聞｜系統性風險：{_srd.get('risk_icon','⬜')} {_rl}（評分 {_rs}）")
                except Exception as _ne:
                    st.session_state.news_items = []
                    st.session_state.systemic_risk_data = None
                    st.warning(f"⚠️ 新聞抓取失敗（不影響指標）：{str(_ne)[:80]}")

    if st.session_state.macro_done:
        ind   = st.session_state.indicators
        phase = st.session_state.phase_info
        sc    = phase["score"];  ph   = phase["phase"];  ph_c = phase["phase_color"]
        alloc = phase["alloc"];  advice = phase.get("advice","")
        rec_p = phase.get("rec_prob")

        # ── 景氣時鐘 + 天氣 + 配置 ──
        _ind_dates = [v.get("date","") for v in ind.values() if isinstance(v,dict) and v.get("date")]
        if _ind_dates:
            st.caption(f"📅 指標資料截至 {max(_ind_dates)}（FRED 有發布時差，部分指標為上月）")

        PHASES = ["衰退","復甦","擴張","高峰"]
        PCOLORS = {"衰退":"#ff9800","復甦":"#64b5f6","擴張":"#00c853","高峰":"#f44336"}
        nxt_ph = phase.get("next_phase", ph)
        t_arrow = phase.get("trend_arrow","→"); t_label = phase.get("trend_label","持穩")
        t_color = phase.get("trend_color","#888888"); nxt_color = PCOLORS.get(nxt_ph,"#888")

        c1, c2, c3 = st.columns([1.2, 1, 1.5])
        with c1:
            infl_html = (f"<div style='background:#0d1117;border:1px dashed {t_color};border-radius:8px;padding:6px 10px;margin-top:10px;text-align:center'>"
                         f"<div style='color:#888;font-size:10px;margin-bottom:4px'>拐點偵測</div>"
                         f"<div style='font-size:15px;font-weight:800;color:{ph_c}'>{ph}</div>"
                         f"<div style='font-size:18px;color:{t_color};margin:2px 0'>{t_arrow}</div>"
                         f"<div style='font-size:15px;font-weight:800;color:{nxt_color}'>{'（持穩）' if nxt_ph==ph else nxt_ph}</div>"
                         f"<div style='color:{t_color};font-size:10px;margin-top:4px'>{t_label}</div></div>")
            st.markdown(f"<div style='background:#0d1117;border:2px solid {ph_c};border-radius:14px;padding:18px;text-align:center'>"
                        f"<div style='color:#888;font-size:12px;letter-spacing:2px'>景氣時鐘</div>"
                        f"<div style='color:{ph_c};font-size:42px;font-weight:900;margin:6px 0'>{ph}</div>"
                        f"<div style='display:flex;justify-content:center;gap:8px;margin-top:8px'>"
                        + "".join(f"<span style='background:{PCOLORS[p] if p==ph else '#1a1a2e'};color:{'#fff' if p==ph else '#555'};padding:3px 10px;border-radius:20px;font-size:11px'>{p}</span>" for p in PHASES)
                        + f"</div>{infl_html}</div>", unsafe_allow_html=True)
        with c2:
            bar = "█"*int(sc) + "░"*(10-int(sc))
            rec_html = ""
            if rec_p is not None:
                rc = "#f44336" if rec_p>60 else ("#ff9800" if rec_p>35 else "#00c853")
                rec_html = f"<div style='margin-top:8px'><div style='color:#888;font-size:11px'>衰退機率</div><div style='color:{rc};font-size:22px;font-weight:800'>{rec_p:.0f}%</div></div>"
            _w_icon  = phase.get("weather_icon","⛅"); _w_label = phase.get("weather_label","多雲")
            _w_color = phase.get("weather_color","#90caf9"); _w_alloc = phase.get("weather_alloc_str","")
            _wbg = "linear-gradient(135deg,#1a1000,#2a1f00)" if "晴" in _w_label else "linear-gradient(135deg,#0d1a2a,#0d1117)"
            st.markdown(f"<div style='background:{_wbg};border:2px solid {_w_color};border-radius:14px;padding:18px;text-align:center'>"
                        f"<div style='color:#888;font-size:11px;letter-spacing:2px;margin-bottom:4px'>總經天氣預報</div>"
                        f"<div style='font-size:48px;line-height:1.1;margin:4px 0'>{_w_icon}</div>"
                        f"<div style='color:{_w_color};font-size:22px;font-weight:900'>{_w_label}</div>"
                        f"<div style='color:#ccc;font-size:11px;margin:6px 0;padding:4px 8px;background:#1a1a1a;border-radius:6px'>建議：{_w_alloc}</div>"
                        f"<div style='color:{ph_c};font-size:13px;font-weight:700;margin-top:4px'>Macro Score {sc}/10</div>"
                        f"<div style='color:{ph_c};font-size:10px;letter-spacing:1px'>{bar}</div>"
                        f"{rec_html}</div>", unsafe_allow_html=True)
        with c3:
            alloc_bars = "".join(
                f"<div style='display:flex;align-items:center;margin:5px 0'>"
                f"<div style='color:#ccc;width:38px;font-size:13px'>{k}</div>"
                f"<div style='flex:1;background:#161b22;border-radius:4px;height:14px;margin:0 8px'>"
                f"<div style='background:{'#2196f3' if k=='股票' else '#ff9800' if k=='債券' else '#78909c'};width:{v}%;height:100%;border-radius:4px'></div></div>"
                f"<div style='color:{'#2196f3' if k=='股票' else '#ff9800' if k=='債券' else '#78909c'};font-weight:700;font-size:13px'>{v}%</div></div>"
                for k,v in alloc.items())
            st.markdown(f"<div style='background:#0d1117;border:1px solid #30363d;border-radius:14px;padding:18px'>"
                        f"<div style='color:#888;font-size:12px;letter-spacing:2px;margin-bottom:10px'>AI 建議配置</div>"
                        f"{alloc_bars}"
                        f"<div style='color:#69f0ae;font-size:11px;margin-top:8px;line-height:1.6'>{advice}</div>"
                        f"</div>", unsafe_allow_html=True)

        # ── 風險警示燈號 ──
        _vix_v   = (ind.get("VIX") or {}).get("value")
        _spr_v   = (ind.get("YIELD_10Y2Y") or {}).get("value")
        _hy_v    = (ind.get("HY_SPREAD") or {}).get("value")
        _risk    = 0; _msgs = []
        if _vix_v is not None:
            if _vix_v > 30:  _risk = max(_risk,2); _msgs.append(f"VIX={_vix_v:.1f}>30（市場恐慌）")
            elif _vix_v > 22: _risk = max(_risk,1); _msgs.append(f"VIX={_vix_v:.1f}偏高")
        if _spr_v is not None:
            if _spr_v < -0.3: _risk = max(_risk,2); _msgs.append(f"殖利率深度倒掛{_spr_v:.3f}%")
            elif _spr_v < 0:  _risk = max(_risk,1); _msgs.append(f"殖利率倒掛{_spr_v:.3f}%")
        if _hy_v is not None and _hy_v > 6:
            _risk = max(_risk,2); _msgs.append(f"HY利差={_hy_v:.2f}%>6%（信用風險）")
        if _risk == 2 and _msgs:
            st.error(f"🚨 **總經高風險** | {'　|　'.join(_msgs)}\n\n⚠️ 建議提高投資等級債券基金水位，核心部位 ≥80%")
        elif _risk == 1 and _msgs:
            st.warning(f"⚠️ 市場溫度偏高：{'　|　'.join(_msgs)}　→ 衛星部位設停利")

        # ── 系統性風險偵測（新聞 NLP）──
        _srd = st.session_state.get("systemic_risk_data")
        if _srd:
            _rl  = _srd.get("risk_level","LOW")
            _rs  = _srd.get("risk_score",0)
            _rc  = _srd.get("risk_color","#888")
            _ri  = _srd.get("risk_icon","⬜")
            _adv = _srd.get("advice","")
            _trig = _srd.get("triggered",[])
            _srd_bg = {"HIGH":"#2a0a0a","MEDIUM":"#2a1f00","LOW":"#0a1a0a"}.get(_rl,"#111")
            _srd_border = {"HIGH":"#f44336","MEDIUM":"#ff9800","LOW":"#00c853"}.get(_rl,"#30363d")
            _trig_html = ""
            if _trig:
                _trig_html = "<div style='margin-top:6px;display:flex;flex-wrap:wrap;gap:4px'>"
                for t in _trig[:6]:
                    _trig_html += f"<span style='background:#1a1a2e;color:{_rc};border:1px solid {_rc};padding:2px 8px;border-radius:12px;font-size:11px'>#{t['keyword']}({t['sub_score']})</span>"
                _trig_html += "</div>"
            st.markdown(
                f"<div style='background:{_srd_bg};border:1px solid {_srd_border};border-radius:10px;padding:12px 16px;margin:8px 0'>"
                f"<div style='display:flex;align-items:center;gap:10px'>"
                f"<span style='font-size:24px'>{_ri}</span>"
                f"<div><div style='color:#888;font-size:11px'>新聞系統性風險偵測</div>"
                f"<div style='color:{_rc};font-weight:800;font-size:15px'>{_rl} （評分 {_rs}）</div></div>"
                f"<div style='flex:1;text-align:right;color:#ccc;font-size:11px'>{_adv}</div></div>"
                f"{_trig_html}</div>", unsafe_allow_html=True)

        # ── 宏觀風險溫度計（Core Protocol v2.0 Ch.3 多軸複合圖）──────
        _pmi_s   = (ind.get("PMI")         or {}).get("series")
        _spr_s   = (ind.get("YIELD_10Y2Y") or {}).get("series")
        _vix_s   = (ind.get("VIX")         or {}).get("series")
        _has_chart = any(
            s is not None and hasattr(s, "__len__") and len(s) >= 4
            for s in [_pmi_s, _spr_s, _vix_s])
        if _has_chart:
            with st.expander("📊 宏觀風險溫度計（多軸複合圖）", expanded=True):
                from plotly.subplots import make_subplots
                fig_mac = make_subplots(
                    rows=2, cols=1, shared_xaxes=True,
                    row_heights=[0.55, 0.45],
                    vertical_spacing=0.06,
                    specs=[[{"secondary_y": True}], [{"secondary_y": False}]])

                # ── 主軸 Bar：Macro Score 各期（以 breakdown 模擬）────────
                # 取最近 24 個月指標分數作為時序（使用各指標 value 趨勢代替）
                _score_val = sc  # 當前總分
                _sc_color  = "#f44336" if _score_val>=8 else ("#00c853" if _score_val>=5 else ("#64b5f6" if _score_val>=3 else "#ff9800"))
                # 無歷史評分序列時，顯示各指標貢獻 bar（靜態橫向）
                _ind_rows = [(k, v) for k, v in ind.items() if isinstance(v, dict) and v.get("score") is not None]
                if _ind_rows:
                    _bar_names = [v.get("name", k)[:10] for k, v in _ind_rows]
                    _bar_scores = [float(v.get("score", 0)) for _, v in _ind_rows]
                    _bar_colors = ["#00c853" if s > 0 else "#f44336" for s in _bar_scores]
                    fig_mac.add_trace(
                        go.Bar(x=_bar_names, y=_bar_scores,
                               name="各指標得分", marker_color=_bar_colors,
                               hovertemplate="%{x}: %{y:+.2f}<extra></extra>"),
                        row=1, col=1)
                    fig_mac.add_hline(y=0, line_color="#555", line_width=1, row=1, col=1)

                # ── 副軸 Lines：殖利率利差 / VIX / PMI ────────────────────
                import pandas as _pd_mac
                def _safe_series(s):
                    if s is None: return None
                    try:
                        if not isinstance(s, _pd_mac.Series): s = _pd_mac.Series(s)
                        return s.dropna().tail(60)
                    except Exception: return None

                _spr_clean = _safe_series(_spr_s)
                _vix_clean = _safe_series(_vix_s)
                _pmi_clean = _safe_series(_pmi_s)

                if _spr_clean is not None and len(_spr_clean) >= 2:
                    fig_mac.add_trace(
                        go.Scatter(x=list(_spr_clean.index), y=list(_spr_clean.values),
                                   name="10Y-2Y利差(%)", mode="lines",
                                   line=dict(color="#64b5f6", width=1.5),
                                   hovertemplate="%{y:.3f}%<extra>10Y-2Y</extra>"),
                        row=2, col=1)
                    # 倒掛警戒線
                    fig_mac.add_hline(y=0, line_color="#f44336", line_dash="dash",
                                      line_width=1, row=2, col=1,
                                      annotation_text="倒掛警戒",
                                      annotation_font_color="#f44336",
                                      annotation_position="bottom right")

                if _vix_clean is not None and len(_vix_clean) >= 2:
                    fig_mac.add_trace(
                        go.Scatter(x=list(_vix_clean.index), y=list(_vix_clean.values),
                                   name="VIX恐慌", mode="lines",
                                   line=dict(color="#ff9800", width=1.5, dash="dot"),
                                   hovertemplate="%{y:.1f}<extra>VIX</extra>"),
                        row=2, col=1)

                if _pmi_clean is not None and len(_pmi_clean) >= 2:
                    fig_mac.add_trace(
                        go.Scatter(x=list(_pmi_clean.index), y=list(_pmi_clean.values),
                                   name="PMI製造業", mode="lines",
                                   line=dict(color="#ce93d8", width=1.5, dash="dashdot"),
                                   hovertemplate="%{y:.1f}<extra>PMI</extra>"),
                        row=2, col=1)
                    fig_mac.add_hline(y=50, line_color="#888", line_dash="dot",
                                      line_width=1, row=2, col=1,
                                      annotation_text="50榮枯線",
                                      annotation_font_color="#888",
                                      annotation_position="bottom right")

                fig_mac.update_layout(
                    paper_bgcolor="#0e1117", plot_bgcolor="#161b22",
                    font_color="#e6edf3", height=480,
                    margin=dict(t=15, b=20, l=50, r=20),
                    legend=dict(orientation="h", font_size=10, y=1.03),
                    hovermode="x unified",
                    bargap=0.15)
                fig_mac.update_yaxes(title_text="指標得分", row=1, col=1,
                                     gridcolor="#1e2a3a")
                fig_mac.update_yaxes(title_text="指標數值", row=2, col=1,
                                     gridcolor="#1e2a3a")
                fig_mac.update_xaxes(gridcolor="#1e2a3a")
                st.plotly_chart(fig_mac, use_container_width=True)

                # 研判結論
                _res_txt = ""
                if _score_val >= 8:
                    _res_txt = "🔥 **擴張/高峰期**：居高思危，股 40% / 債 40% / 現金 20%。獲利轉入核心穩健配息基金。"
                elif _score_val >= 5:
                    _res_txt = "🌱 **復甦/擴張期**：積極佈局，股 60% / 債 30% / 現金 10%。衛星主攻成長題材。"
                elif _score_val >= 3:
                    _res_txt = "🍂 **衰退轉復甦**：防禦轉進，股 40% / 債 40% / 現金 20%。聚焦核心配息資產。"
                else:
                    _res_txt = "❄️ **衰退期**：現金為王，股 20% / 債 50% / 現金 30%。嚴格檢視吃本金風險。"
                st.info(_res_txt)

        # ── 指標貢獻明細（折疊）──
        with st.expander("📊 各指標貢獻明細", expanded=False):
            _rows = []
            for _ik, _iv in ind.items():
                if not isinstance(_iv, dict): continue
                _rows.append({
                    "指標": _iv.get("name",_ik)[:16],
                    "數值": f"{_iv.get('value'):.2f}" if isinstance(_iv.get("value"),(int,float)) else str(_iv.get("value",""))[:10],
                    "信號": _iv.get("signal","⬜"),
                    "得分": round(max(-_iv.get("weight",1), min(_iv.get("weight",1), _iv.get("score",0))),2),
                    "權重": _iv.get("weight",1),
                })
            if _rows:
                st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)

        # ── 市場新聞（折疊）──
        _news_items = st.session_state.get("news_items",[])
        if _news_items:
            with st.expander(f"📰 市場新聞（{len(_news_items)} 則）", expanded=False):
                for _ni in _news_items[:20]:
                    _nt = _ni.get("title","")[:90]
                    _ns = _ni.get("source","")
                    _nu = _ni.get("url","") or _ni.get("link","")
                    _nd = str(_ni.get("published",""))[:16]
                    if _nu:
                        st.markdown(f"**[{_nt}]({_nu})** <span style='color:#888;font-size:11px'>｜{_ns} {_nd}</span>", unsafe_allow_html=True)
                    else:
                        st.markdown(f"**{_nt}** <span style='color:#888;font-size:11px'>｜{_ns} {_nd}</span>", unsafe_allow_html=True)

        # ── AI 結構化總經摘要 ──
        st.divider()
        if GEMINI_KEY:
            # ── 三色燈號阻斷（Core Protocol v2.0 Ch.1）─────────────
            _ai_mac_pct = st.session_state.get("data_health_pct", 100)
            _ai_mac_tl  = st.session_state.get("data_health_traffic", "🟢")
            if _ai_mac_pct < 50:
                st.markdown(
                    "<div style='border-left:4px solid #f44336;background:#1a1f2e;"
                    "border-radius:0 8px 8px 0;padding:10px 14px;font-size:13px'>"
                    "🔴 <b>紅燈阻斷</b>：總經資料完整率 "
                    f"<b>{_ai_mac_pct}%</b>（&lt;50%），AI 分析停用。"
                    "請前往「🔬 資料診斷」頁確認指標載入狀況。</div>",
                    unsafe_allow_html=True)
            else:
                if _ai_mac_pct < 80:
                    st.warning(f"🟡 資料完整率 **{_ai_mac_pct}%**（黃燈），AI 結果參考性降低。")
                if st.button("🤖 AI 結構化總經摘要", key="btn_macro_ai", type="primary"):
                    with st.spinner("Gemini 生成【現狀解讀】【系統性風險】【觀察重點】中..."):
                        try:
                            _ai_txt = analyze_macro_structured(
                                api_key      = GEMINI_KEY,
                                indicators   = ind,
                                phase_info   = phase,
                                news_items   = st.session_state.get("news_items",[]),
                                systemic_risk= st.session_state.get("systemic_risk_data"),
                            )
                            st.session_state.macro_ai = _ai_txt
                        except Exception as _e:
                            st.error(f"AI 分析失敗：{_e}")
            if st.session_state.macro_ai:
                st.markdown(st.session_state.macro_ai)
        else:
            st.caption("⚠️ 未設定 GEMINI_API_KEY，AI 分析功能關閉")
    else:
        st.info("👆 點擊「載入總經資料」開始分析")

# ══════════════════════════════════════════════════════
# TAB 2 — 單一基金
# ══════════════════════════════════════════════════════
with tab2:
    st.markdown("## 🔍 單一基金深度分析")
    st.caption("輸入 MoneyDJ 代碼或網址，即時抓取淨值 / 持股 / 配息 / 風險指標")

    col_url, col_go = st.columns([5,1])
    with col_url:
        mj_url_input = st.text_input("MoneyDJ URL 或代碼",
            placeholder="貼上 MoneyDJ 網址 或 輸入代碼（tlzf9 / LU0095940420）",
            label_visibility="collapsed", key="mj_url_input")
    with col_go:
        do_load = st.button("🚀 分析", type="primary", use_container_width=True, key="btn_mj_load")

    if do_load and mj_url_input.strip():
        with st.spinner("📡 抓取 MoneyDJ 資料（基本資料 + 持股 + 績效）..."):
            fd_raw = fetch_fund_from_moneydj_url(mj_url_input.strip())
            fd_raw = normalize_result_state(fd_raw)
            _status = fd_raw.get("status", classify_fetch_status(fd_raw))
            st.session_state.fund_data = {
                "full_key":  fd_raw.get("full_key",""),
                "fund_name": fd_raw.get("fund_name",""),
                "portal":    "www",
                "series":    fd_raw.get("series"),
                "dividends": fd_raw.get("dividends",[]),
                "metrics":   fd_raw.get("metrics",{}),
                "error":     fd_raw.get("error"),
                "warning":   fd_raw.get("warning"),
                "status":    _status,
                "moneydj_raw": fd_raw,
            }
            if fd_raw.get("error"):
                st.error(f"❌ {fd_raw['error']}")
            elif _status == "partial":
                st.warning(f"⚠️ 部分資料（{fd_raw.get('warning','資料不完整')}）")
            else:
                st.success(f"✅ {fd_raw.get('fund_name','') or fd_raw.get('full_key','')} 資料已載入")

    # ── 關鍵字搜尋（折疊）──
    with st.expander("🔍 關鍵字搜尋境外基金（TDCC / FundClear）", expanded=False):
        c_kw, c_btn = st.columns([4,1])
        with c_kw:
            keyword = st.text_input("基金關鍵字", placeholder="安聯、收益成長、摩根、聯博...",
                label_visibility="collapsed", key="fund_keyword")
        with c_btn:
            do_search = st.button("🔍 搜尋", type="primary", use_container_width=True, key="btn_search")
        if do_search and keyword.strip():
            with st.spinner(f"搜尋「{keyword}」中..."):
                results = tdcc_search_fund(keyword.strip())
                st.session_state.tdcc_results = results
                if not results:
                    st.warning("⚠️ 查無結果，請直接使用上方 MoneyDJ 網址輸入")
                else:
                    st.success(f"✅ 找到 {len(results)} 檔基金")
        results = st.session_state.get("tdcc_results",[])
        if results:
            options = {f"{r.get('基金名稱','')} | {r.get('基金代碼','')}": r for r in results}
            sel = st.selectbox(f"選擇基金（{len(results)} 筆）", list(options.keys()), key="tdcc_select")
            fc  = options[sel].get("基金代碼","")
            st.info(f"💡 代碼：**{fc}** → 在上方輸入框貼入代碼即可分析")

    # ── 分析結果 ──
    fd = st.session_state.fund_data
    if fd:
        _status_fd = fd.get("status","")
        if _status_fd == "failed":
            st.error(f"❌ 資料抓取失敗：{fd.get('error','未知錯誤')}")
        else:
            s    = fd.get("series"); m = fd.get("metrics",{}); divs = fd.get("dividends",[])
            name = fd.get("fund_name",""); fk = fd.get("full_key","")
            mj_raw = fd.get("moneydj_raw",{}) or {}

            if s is None or (hasattr(s,"empty") and s.empty) or not m:
                st.warning("資料不足，無法顯示完整分析（代碼可能錯誤，或 MoneyDJ 暫時無法連線）")
            else:
                st.success(f"✅ **{name or fk}** ｜ 淨值 {len(s)} 筆 ‧ 配息 {len(divs)} 筆")

                # MK 訊號卡片
                phase_info_s = st.session_state.phase_info if st.session_state.macro_done else None
                if phase_info_s:
                    sig = mk_fund_signal(fd, phase_info_s["phase"], phase_info_s["score"])
                    _aa = sig.get("auto_alloc")
                    if _aa:
                        _aa_stk, _aa_bnd, _aa_lbl, _aa_c = _aa
                        st.markdown(f"<div style='background:#0d1b2a;border:1px solid {_aa_c};border-radius:8px;padding:8px 14px;margin:4px 0 8px 0;display:flex;align-items:center;gap:16px'>"
                            f"<span>📊</span><div><div style='color:{_aa_c};font-weight:700;font-size:12px'>總經自動配比建議：{_aa_lbl}</div>"
                            f"<div style='color:#ccc;font-size:12px'>股 {_aa_stk}% ／ 債 {_aa_bnd}%</div></div></div>", unsafe_allow_html=True)
                    _sig_style = sig["sig_style"]
                    st.markdown(f"<div style='background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 18px;margin:8px 0;display:flex;align-items:center;gap:16px;flex-wrap:wrap'>"
                        f"<div><div style='color:#888;font-size:11px'>資產屬性</div><div style='font-size:14px;font-weight:700;color:#58a6ff'>{sig['asset_class']}</div></div>"
                        f"<div><div style='color:#888;font-size:11px'>MK 操作訊號</div><span style='{_sig_style};padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700;display:inline-block'>{sig['label']}</span></div>"
                        f"<div style='flex:1'><div style='color:#888;font-size:11px'>景氣位階（{phase_info_s['phase']} {phase_info_s['score']}/10）</div>"
                        f"<div style='font-size:12px;color:#c9d1d9'>{sig['reason']}</div></div></div>", unsafe_allow_html=True)

                # 淨值走勢圖（Bollinger Bands + 配息標記 v2.0）
                st.markdown("### 📈 淨值走勢 + 布林通道 + 配息標記")
                df_show = s.reset_index(); df_show.columns = ["date","nav"]
                fig_n = go.Figure()

                # ── Bollinger Bands（MA20 ±2σ，半透明填色）──────────────
                _bb_period = min(20, len(s))
                _bb_ma  = s.rolling(_bb_period).mean()
                _bb_std = s.rolling(_bb_period).std()
                _bb_up  = (_bb_ma + 2 * _bb_std).dropna()
                _bb_dn  = (_bb_ma - 2 * _bb_std).dropna()
                # 上軌（填色基準，先畫，不顯示圖例線條）
                fig_n.add_trace(go.Scatter(
                    x=_bb_up.index, y=_bb_up.values, name="BB上軌",
                    line=dict(color="rgba(33,150,243,0.25)", width=1),
                    showlegend=False))
                # 下軌 + fill to 上軌（半透明藍色通道）
                fig_n.add_trace(go.Scatter(
                    x=_bb_dn.index, y=_bb_dn.values, name="布林通道(±2σ)",
                    fill="tonexty",
                    fillcolor="rgba(33,150,243,0.08)",
                    line=dict(color="rgba(33,150,243,0.25)", width=1)))
                # MA20 中軌
                fig_n.add_trace(go.Scatter(
                    x=_bb_ma.dropna().index, y=_bb_ma.dropna().values,
                    name="MA20", line=dict(color="#ff9800", width=1, dash="dot")))
                # MA60
                _ma60 = s.rolling(60).mean()
                fig_n.add_trace(go.Scatter(
                    x=_ma60.dropna().index, y=_ma60.dropna().values,
                    name="MA60", line=dict(color="#9c27b0", width=1, dash="dot")))
                # 淨值主線（最後畫，在最上層）
                fig_n.add_trace(go.Scatter(
                    x=df_show["date"], y=df_show["nav"],
                    name="淨值", line=dict(color="#2196f3", width=1.8)))

                # ── 配息標記 💰（除息日垂直虛線 + marker）───────────────
                _chart_divs = mj_raw.get("dividends") or []
                _chart_divs = _chart_divs if isinstance(_chart_divs, list) else []
                _div_dates, _div_navs, _div_texts = [], [], []
                for _cd in _chart_divs:
                    try:
                        _cd_date = pd.Timestamp(_cd.get("date",""))
                        if _cd_date in s.index:
                            _cd_nav = float(s.loc[_cd_date])
                        else:
                            # 找最近交易日
                            _near = s.index[s.index.get_indexer([_cd_date], method="nearest")[0]]
                            _cd_nav = float(s.loc[_near])
                            _cd_date = _near
                        _cd_amt = _cd.get("amount") or _cd.get("dividend") or ""
                        _div_dates.append(_cd_date)
                        _div_navs.append(_cd_nav)
                        _div_texts.append(f"💰 配息 {_cd_amt}" if _cd_amt else "💰 配息")
                    except Exception:
                        continue
                if _div_dates:
                    fig_n.add_trace(go.Scatter(
                        x=_div_dates, y=_div_navs,
                        mode="markers+text",
                        name="配息日",
                        marker=dict(symbol="triangle-up", size=10, color="#ffd600"),
                        text=_div_texts,
                        textposition="top center",
                        textfont=dict(size=9, color="#ffd600"),
                        hovertemplate="%{text}<br>淨值：%{y:.4f}<extra></extra>"))

                # ── MK 買點水平線 ───────────────────────────────────────
                for bv, bl, bc in [
                    (m.get("buy1"), "買1(年低+σ)", "#69f0ae"),
                    (m.get("buy2"), "買2(年低)",   "#00c853"),
                    (m.get("buy3"), "買3(年低-σ)", "#9c27b0"),
                ]:
                    if bv:
                        fig_n.add_hline(y=bv, line_color=bc, line_dash="dot",
                                        annotation_text=bl, annotation_font_color=bc,
                                        annotation_position="bottom right")
                # 停利線
                if m.get("sell1"):
                    fig_n.add_hline(y=m["sell1"], line_color="#f44336", line_dash="dash",
                                    annotation_text="停利1(年高-σ)",
                                    annotation_font_color="#f44336",
                                    annotation_position="top right")

                fig_n.update_layout(
                    paper_bgcolor="#0e1117", plot_bgcolor="#161b22",
                    font_color="#e6edf3", height=420,
                    margin=dict(t=15, b=30, l=40, r=20),
                    legend=dict(orientation="h", font_size=10, y=1.02),
                    hovermode="x unified", yaxis_title="淨值")
                st.plotly_chart(fig_n, use_container_width=True)

                # ── MK 標準差買點分析 ──
                _m_buy1 = m.get("buy1"); _m_buy2 = m.get("buy2"); _m_buy3 = m.get("buy3")
                _m_sell1 = m.get("sell1")
                _m_pl = m.get("pos_label",""); _m_pc = m.get("pos_color","#888")
                _m_mode = m.get("buy_mode",""); _m_std_src = m.get("std_source","nav")
                _m_nav_v = float(m.get("nav") or 0)
                if _m_buy1:
                    _buy_rows = ""
                    for _bv, _bl, _bc in [(_m_buy1,"年低+1σ 可買","#69f0ae"),(_m_buy2,"年低 大買","#00c853"),(_m_buy3,"年低-1σ 破底買","#9c27b0")]:
                        if _bv:
                            _dist = round(abs(_m_nav_v - _bv), 4) if _m_nav_v else 0
                            _dir  = "▲" if _m_nav_v > _bv else "▼"
                            _buy_rows += (f"<div style='display:flex;align-items:center;justify-content:space-between;"
                                          f"padding:4px 10px;background:#0d1117;border-radius:6px;margin:2px 0'>"
                                          f"<span style='color:{_bc};font-size:12px'>{_bl}</span>"
                                          f"<span style='font-weight:700;font-size:13px'>{_bv:.4f}</span>"
                                          f"<span style='color:#666;font-size:11px'>{_dir} {_dist:.4f}</span></div>")
                    _sell_row = (f"<div style='display:flex;align-items:center;justify-content:space-between;"
                                 f"padding:4px 10px;background:#0d1117;border-radius:6px;margin:2px 0'>"
                                 f"<span style='color:#f44336;font-size:12px'>🔔 停利點</span>"
                                 f"<span style='font-weight:700;font-size:13px'>{_m_sell1:.4f}</span>"
                                 f"<span style='color:#666;font-size:11px'></span></div>") if _m_sell1 else ""
                    st.markdown(
                        f"<div style='background:#161b22;border:1px solid #30363d;border-radius:10px;padding:12px 16px;margin:10px 0'>"
                        f"<div style='display:flex;align-items:center;justify-content:space-between;margin-bottom:8px'>"
                        f"<span style='color:#888;font-size:11px'>📍 MK 標準差買點（{_m_mode} ｜ σ 來源：{_m_std_src}）</span>"
                        f"<span style='background:#111;color:{_m_pc};border:1px solid {_m_pc};padding:2px 10px;"
                        f"border-radius:12px;font-size:12px;font-weight:700'>{_m_pl}</span>"
                        f"</div>"
                        + _buy_rows + _sell_row
                        + f"<div style='color:#666;font-size:10px;margin-top:6px'>現值 {_m_nav_v:.4f}</div>"
                        + "</div>", unsafe_allow_html=True)

                # 關鍵指標 + 配息
                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown("#### 📊 風險指標")
                    risk_tbl = mj_raw.get("risk_metrics",{}).get("risk_table",{})
                    _r1y = risk_tbl.get("一年",{})
                    _std1 = _r1y.get("標準差","—"); _sh1 = _r1y.get("Sharpe","—")
                    _al1  = _r1y.get("Alpha","—");  _be1 = _r1y.get("Beta","—")
                    for lbl, val in [("波動 σ(1Y)", f"{_std1}%"),("Sharpe(1Y)",str(_sh1)),("Alpha(1Y)",str(_al1)),("Beta(1Y)",str(_be1))]:
                        st.markdown(f"<div style='display:flex;justify-content:space-between;padding:5px 10px;background:#161b22;border-radius:6px;margin:3px 0'><span style='color:#888;font-size:12px'>{lbl}</span><span style='font-weight:700'>{val}</span></div>", unsafe_allow_html=True)
                    # 四分位
                    peer = mj_raw.get("risk_metrics",{}).get("peer_compare",{})
                    qr = _quartile_check(peer, risk_tbl)
                    if qr["quartile"]:
                        _qr_color = qr["color"]
                        _qr_adv = (f"<div style='color:#ff9800;font-size:11px;margin-top:4px'>{qr['advice']}</div>"
                                   if qr.get("advice") else "")
                        st.markdown(
                            f"<div style='background:#1a1f2e;border-radius:8px;padding:8px 12px;margin-top:6px'>"
                            f"<span style='color:{_qr_color};font-weight:700'>{qr['label']}</span>"
                            + _qr_adv + "</div>", unsafe_allow_html=True)

                with col_b:
                    st.markdown("#### 💸 近期配息")
                    if divs and len(divs) >= 1:
                        _mj_dy = mj_raw.get("moneydj_div_yield")
                        try: _mj_dy = float(_mj_dy) if _mj_dy is not None else None
                        except: _mj_dy = None
                        _adr = _mj_dy if (_mj_dy and _mj_dy > 0) else (m.get("annual_div_rate",0) or 0)
                        try: _adr = float(_adr)
                        except: _adr = 0.0
                        st.metric("年化配息率", f"{_adr:.2f}%", help="MoneyDJ wb05 官方值（優先）或自算估值")
                        for d in divs[:6]:
                            _dt = d.get("date",""); _amt = d.get("amount",""); _yld = d.get("yield_pct","")
                            st.markdown(f"<div style='display:flex;justify-content:space-between;padding:4px 10px;background:#161b22;border-radius:6px;margin:2px 0'><span style='color:#888;font-size:11px'>{_dt}</span><span style='font-weight:700'>{_amt}</span><span style='color:#ff9800;font-size:11px'>{_yld}</span></div>", unsafe_allow_html=True)

                        # ── 🚨 吃本金警示（Core Protocol Ch.3.2）──
                        _tr1y = m.get("ret_1y")  # 含息總報酬率近 1 年（%）
                        if _tr1y is not None and _adr > 0:
                            _ds = div_safety_check(
                                total_return=float(_tr1y),
                                dividend_yield=float(_adr),
                                nav_change=float(m.get("ret_1y", 0) or 0),
                            )
                            _al = _ds.get("alert_level","grey")
                            _bg = {"red":"#2a0a0a","yellow":"#2a1f00","green":"#0a1a0a"}.get(_al,"#111")
                            _bc = {"red":"#f44336","yellow":"#ff9800","green":"#00c853"}.get(_al,"#888")
                            st.markdown(
                                f"<div style='background:{_bg};border:1px solid {_bc};border-radius:8px;"
                                f"padding:8px 12px;margin-top:8px'>"
                                f"<div style='color:{_bc};font-weight:700;font-size:12px'>{_ds['status']}</div>"
                                f"<div style='color:#ccc;font-size:11px;margin-top:2px'>{_ds['message']}</div>"
                                + (f"<div style='color:#ff9800;font-size:10px;margin-top:4px'>{_ds['nav_warning']}</div>" if _ds.get("nav_warning") else "")
                                + "</div>", unsafe_allow_html=True)
                    else:
                        st.info("無配息記錄")

                # ── 持股分析（折疊）──
                _holdings = mj_raw.get("holdings", {}) or {}
                _sectors  = _holdings.get("sector_alloc", []) or []
                _tops     = _holdings.get("top_holdings", []) or []
                _hdate    = _holdings.get("data_date", "")
                if _sectors or _tops:
                    with st.expander(f"📂 持股分析" + (f"（{_hdate}）" if _hdate else ""), expanded=False):
                        _hc1, _hc2 = st.columns(2)
                        with _hc1:
                            if _sectors:
                                st.markdown("**🏭 產業配置**")
                                for _sec in _sectors[:10]:
                                    _sn = str(_sec.get("name",""))[:18]
                                    _sp = float(_sec.get("pct", 0) or 0)
                                    st.markdown(
                                        f"<div style='display:flex;align-items:center;gap:8px;margin:3px 0'>"
                                        f"<div style='color:#ccc;font-size:11px;width:95px;flex-shrink:0'>{_sn}</div>"
                                        f"<div style='flex:1;background:#1a1a2a;border-radius:3px;height:10px'>"
                                        f"<div style='background:#2196f3;width:{min(_sp*3,100):.0f}%;height:100%;border-radius:3px'></div></div>"
                                        f"<div style='color:#2196f3;font-size:11px;width:40px;text-align:right'>{_sp:.1f}%</div>"
                                        f"</div>", unsafe_allow_html=True)
                        with _hc2:
                            if _tops:
                                st.markdown("**🏆 前10大持股**")
                                for _i, _top in enumerate(_tops[:10], 1):
                                    _tn = str(_top.get("name",""))[:22]
                                    _tp = float(_top.get("pct", 0) or 0)
                                    _ts = str(_top.get("sector",""))[:12]
                                    st.markdown(
                                        f"<div style='display:flex;gap:6px;padding:3px 8px;background:#161b22;border-radius:6px;margin:2px 0'>"
                                        f"<span style='color:#555;font-size:11px;width:16px'>#{_i}</span>"
                                        f"<span style='font-size:11px;flex:1'>{_tn}</span>"
                                        f"<span style='color:#888;font-size:10px'>{_ts}</span>"
                                        f"<span style='color:#58a6ff;font-weight:700;font-size:11px;width:36px;text-align:right'>{_tp:.1f}%</span>"
                                        f"</div>", unsafe_allow_html=True)

                # AI 基金分析
                st.divider()
                if GEMINI_KEY:
                    # ── 三色燈號阻斷（Core Protocol v2.0 Ch.1）─────────
                    _ai_fd_pct = st.session_state.get("data_health_pct", 100)
                    if _ai_fd_pct < 50:
                        st.markdown(
                            "<div style='border-left:4px solid #f44336;background:#1a1f2e;"
                            "border-radius:0 8px 8px 0;padding:10px 14px;font-size:13px'>"
                            "🔴 <b>紅燈阻斷</b>：總經資料完整率 "
                            f"<b>{_ai_fd_pct}%</b>（&lt;50%），AI 基金分析停用。"
                            "請前往「🔬 資料診斷」確認指標載入狀況。</div>",
                            unsafe_allow_html=True)
                    else:
                        if _ai_fd_pct < 80:
                            st.warning(f"🟡 資料完整率 **{_ai_fd_pct}%**（黃燈），AI 結果參考性降低。")
                        if st.button("🤖 AI 基金分析", key="btn_fund_ai"):
                            with st.spinner("Gemini 分析中..."):
                                try:
                                    _ai = analyze_fund_json(GEMINI_KEY, name or fk, m,
                                        mj_raw.get("perf",{}), phase_info_s, divs)
                                    st.session_state.fund_ai_txt = _ai
                                except Exception as _e:
                                    st.error(f"AI 分析失敗：{_e}")
                    if st.session_state.get("fund_ai_txt"):
                        st.markdown(st.session_state.fund_ai_txt)

# ══════════════════════════════════════════════════════
# TAB 3 — 組合基金
# ══════════════════════════════════════════════════════
with tab3:
    st.markdown("## 📊 組合基金管理")
    st.caption("加入多檔基金，即時計算核心/衛星配比、六因子評分、現金流估算")

    if "portfolio_funds" not in st.session_state:
        st.session_state.portfolio_funds = []

    # Hero：核心/衛星配置概況
    _pf_loaded = [f for f in st.session_state.portfolio_funds if f.get("loaded")]
    if _pf_loaded:
        _tot  = sum(f.get("invest_twd",0) or 0 for f in _pf_loaded)
        _core = sum(f.get("invest_twd",0) or 0 for f in _pf_loaded if f.get("is_core"))
        _core_pct = round(_core/_tot*100,1) if _tot else 0
        _target   = st.session_state.get("portfolio_core_pct",75)
        _diff     = round(_core_pct - _target, 1)
        _dc       = "#f44336" if abs(_diff)>10 else ("#ff9800" if abs(_diff)>5 else "#00c853")
        st.markdown(
            f"<div style='background:linear-gradient(135deg,#0d1b2a,#1a2332);border-radius:14px;padding:18px 22px;margin-bottom:16px;border:1px solid #30363d'>"
            f"<div style='font-size:13px;color:#888;margin-bottom:10px'>📊 目前投資組合 — {len(_pf_loaded)} 檔" + (f" · NT${_tot:,.0f}" if _tot else "") + "</div>"
            f"<div style='display:flex;gap:20px;flex-wrap:wrap'>"
            f"<div><div style='color:#64b5f6;font-size:11px'>🛡️ 核心資產</div><div style='color:#64b5f6;font-size:28px;font-weight:900'>{_core_pct}%</div></div>"
            f"<div><div style='color:#ff9800;font-size:11px'>⚡ 衛星資產</div><div style='color:#ff9800;font-size:28px;font-weight:900'>{100-_core_pct:.1f}%</div></div>"
            f"<div><div style='color:{_dc};font-size:11px'>目標偏差</div><div style='color:{_dc};font-size:28px;font-weight:900'>{_diff:+.1f}%</div></div>"
            f"</div></div>", unsafe_allow_html=True)

        # ── 核心/衛星甜甜圈圖（Core Protocol v2.0 Ch.4）────────────
        _dn_col, _dn_info = st.columns([1, 1])
        with _dn_col:
            _dn_labels = [
                (f.get("code","?")[:8] + " 🛡️" if f.get("is_core") else f.get("code","?")[:8] + " ⚡")
                for f in _pf_loaded]
            _dn_values = [max(f.get("invest_twd", 0) or 0, 0) for f in _pf_loaded]
            _dn_colors = ["#64b5f6" if f.get("is_core") else "#ff9800" for f in _pf_loaded]
            _alert     = abs(_diff) > 10
            _bg_c      = "#1a0808" if _alert else "#0e1117"
            fig_dn = go.Figure()
            if sum(_dn_values) > 0:
                fig_dn.add_trace(go.Pie(
                    labels    = _dn_labels,
                    values    = _dn_values,
                    hole      = 0.55,
                    marker    = dict(colors=_dn_colors,
                                     line=dict(color="#0e1117", width=2)),
                    textinfo  = "label+percent",
                    textfont  = dict(size=10),
                    hovertemplate="%{label}: NT$%{value:,.0f} (%{percent})<extra></extra>",
                    domain    = dict(x=[0.05, 0.95], y=[0.05, 0.95]),
                ))
                # 偏移 >10%：外圈紅色警戒環
                if _alert:
                    fig_dn.add_trace(go.Pie(
                        labels   = ["⚠️ 配置偏離"],
                        values   = [1],
                        hole     = 0.88,
                        marker   = dict(colors=["rgba(244,67,54,0.25)"],
                                        line=dict(color="#f44336", width=3)),
                        textinfo = "none",
                        hoverinfo= "none",
                        showlegend= False,
                        domain   = dict(x=[0, 1], y=[0, 1]),
                    ))
            # 中央標註
            fig_dn.update_layout(
                paper_bgcolor = _bg_c,
                plot_bgcolor  = _bg_c,
                font_color    = "#e6edf3",
                height        = 270,
                margin        = dict(t=20, b=10, l=10, r=10),
                showlegend    = False,
                annotations   = [dict(
                    text      = f"<b>{_core_pct}%</b><br>核心",
                    x=0.5, y=0.5, font_size=16,
                    showarrow = False,
                    font      = dict(color="#64b5f6"))],
            )
            st.plotly_chart(fig_dn, use_container_width=True)

        with _dn_info:
            st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
            _target2 = st.session_state.get("portfolio_core_pct", 75)
            if _alert:
                st.error(
                    f"⚠️ **配置偏離警告**\n\n"
                    f"現核心 **{_core_pct}%** vs 目標 **{_target2}%**，"
                    f"偏差 **{_diff:+.1f}%**（>10%）。\n\n"
                    f"{'核心過重：建議贖回核心基金，轉入衛星資產。' if _diff > 0 else '衛星過重：建議獲利了結衛星，補回核心配置。'}"
                )
            else:
                st.success(
                    f"✅ **配置健康**\n\n"
                    f"核心 **{_core_pct}%** / 衛星 **{100-_core_pct:.1f}%**，"
                    f"偏差 {_diff:+.1f}%（目標 {_target2}%±10%）"
                )
            # 各基金市值明細
            st.markdown("<div style='margin-top:12px;font-size:12px;color:#888'>持倉明細</div>",
                        unsafe_allow_html=True)
            for _pfi in _pf_loaded:
                _pfi_role  = "🛡️" if _pfi.get("is_core") else "⚡"
                _pfi_pct   = round(_pfi.get("invest_twd",0) / _tot * 100, 1) if _tot else 0
                _pfi_c     = "#64b5f6" if _pfi.get("is_core") else "#ff9800"
                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;"
                    f"font-size:11px;padding:3px 0;border-bottom:1px solid #1e2a3a'>"
                    f"<span style='color:{_pfi_c}'>{_pfi_role} {_pfi.get('code','?')}</span>"
                    f"<span style='color:#ccc'>{_pfi_pct}%</span></div>",
                    unsafe_allow_html=True)

    st.markdown("### ➕ 加入基金")
    c_code, c_inv, c_add = st.columns([3,2,1])
    with c_code:
        pf_code_input = st.text_input("基金代碼或 MoneyDJ URL", label_visibility="collapsed",
            placeholder="輸入代碼（TLZF9）或 MoneyDJ URL", key="pf_code_input")
    with c_inv:
        pf_invest_twd = st.number_input("投入金額（NTD）", min_value=0, step=10000,
            label_visibility="collapsed", key="pf_invest_input")
    with c_add:
        pf_add_btn = st.button("➕ 加入", type="primary", use_container_width=True, key="btn_pf_add")

    if pf_add_btn and pf_code_input.strip():
        code_clean = pf_code_input.strip().upper()
        if not any(f["code"] == code_clean for f in st.session_state.portfolio_funds):
            st.session_state.portfolio_funds.append({
                "code": code_clean, "invest_twd": pf_invest_twd,
                "loaded": False, "load_error": None,
            })
            st.rerun()
        else:
            st.warning(f"⚠️ {code_clean} 已在組合中")

    pf = st.session_state.portfolio_funds
    if not pf:
        st.info("💡 請在上方輸入基金代碼加入，支援多檔同時比較")
    else:
        # 批次載入按鈕
        not_loaded = [i for i, f in enumerate(pf) if not f.get("loaded")]
        if not_loaded:
            if st.button(f"📡 載入所有未載入基金（{len(not_loaded)} 檔）", type="primary", key="btn_pf_load_all"):
                _errors = []
                for cnt, i in enumerate(not_loaded):
                    pf_item = st.session_state.portfolio_funds[i]
                    with st.spinner(f"載入 {pf_item['code']} （{cnt+1}/{len(not_loaded)}）"):
                        try:
                            pf_raw = fetch_fund_from_moneydj_url(pf_item["code"])
                            if pf_raw.get("error"):
                                _errors.append(f"{pf_item['code']}: {pf_raw['error']}")
                                st.session_state.portfolio_funds[i].update({"loaded":True,"load_error":pf_raw["error"]})
                            else:
                                st.session_state.portfolio_funds[i].update({
                                    "name":       pf_raw.get("fund_name") or pf_item["code"],
                                    "series":     pf_raw.get("series"),
                                    "dividends":  pf_raw.get("dividends",[]),
                                    "metrics":    pf_raw.get("metrics",{}),
                                    "moneydj_raw":pf_raw,
                                    "risk_metrics":pf_raw.get("risk_metrics",{}),
                                    "is_core":    assign_asset_role(pf_raw.get("fund_name") or pf_item["code"]),
                                    "loaded":     True, "load_error": None,
                                })
                        except Exception as _le:
                            _errors.append(f"{pf_item['code']}: {str(_le)[:80]}")
                            st.session_state.portfolio_funds[i].update({"loaded":True,"load_error":str(_le)[:80]})
                if _errors:
                    st.warning("部分基金載入失敗：\n" + "\n".join(_errors))
                st.rerun()

        # 基金清單
        for i, pf_item in enumerate(pf):
            status_icon = "✅" if (pf_item.get("loaded") and not pf_item.get("load_error")) else ("❌" if pf_item.get("load_error") else "⏳")
            m_i    = pf_item.get("metrics",{})
            rm_i   = pf_item.get("risk_metrics",{})
            rt_i   = rm_i.get("risk_table",{})
            role_i = "🛡️核心" if pf_item.get("is_core") else ("⚡衛星" if pf_item.get("is_core") is False else "")
            _nav_i  = m_i.get("nav") or (pf_item.get("moneydj_raw") or {}).get("nav_latest","")
            _adr_i  = (pf_item.get("moneydj_raw") or {}).get("moneydj_div_yield") or m_i.get("annual_div_rate","")
            _sh_i   = (rt_i.get("一年") or {}).get("Sharpe","")
            _std_i  = (rt_i.get("一年") or {}).get("標準差","")
            with st.container():
                ci1, ci2, ci3 = st.columns([4,4,1])
                with ci1:
                    st.markdown(
                        f"<div style='padding:8px 12px;background:#161b22;border-radius:8px;margin:3px 0'>"
                        f"{status_icon} <b style='color:#e6edf3'>{(pf_item.get('name','') or pf_item['code'])[:28]}</b> "
                        f"<span style='color:#888;font-size:11px'>{pf_item['code']}</span> "
                        f"<span style='color:#ff9800;font-size:11px;margin-left:6px'>{role_i}</span></div>",
                        unsafe_allow_html=True)
                with ci2:
                    st.markdown(
                        f"<div style='padding:8px 12px;background:#161b22;border-radius:8px;margin:3px 0;font-size:11px;color:#888'>"
                        f"NAV: <b style='color:#e6edf3'>{_nav_i}</b>"
                        f"　配息率: <b style='color:#ff9800'>{_adr_i}{'%' if _adr_i else ''}</b>"
                        f"　Sharpe: <b style='color:#69f0ae'>{_sh_i}</b>"
                        f"　σ: <b>{_std_i}{'%' if _std_i else ''}</b></div>",
                        unsafe_allow_html=True)
                with ci3:
                    if st.button("🗑️", key=f"del_pf_{i}", help=f"移除 {pf_item['code']}"):
                        st.session_state.portfolio_funds.pop(i)
                        st.rerun()

                if pf_item.get("load_error"):
                    st.caption(f"⚠️ {pf_item['load_error']}")

        # 核心/衛星目標設定
        st.divider()
        st.session_state.portfolio_core_pct = st.slider(
            "目標核心資產比例（%）", 50, 90,
            st.session_state.get("portfolio_core_pct",75), 5, key="slider_core_pct")

        # ── 真實收益長條圖（Core Protocol v2.0 Ch.4）────────────────
        _loaded_pf = [f for f in pf if f.get("loaded") and not f.get("load_error")]
        if _loaded_pf:
            st.divider()
            st.markdown("### 📊 真實收益 vs 配息率健康矩陣")
            st.caption("長條高度 < 紅虛線 → 含息報酬不足以支撐配息 → 吃本金警示")

            _rc_names, _rc_ret, _rc_div = [], [], []
            for _f in _loaded_pf:
                _mj  = _f.get("moneydj_raw", {}) or {}
                _m   = _f.get("metrics", {}) or {}
                _pf2 = _mj.get("perf", {}) or {}
                _name = (_f.get("name") or _f["code"])[:18]
                try:
                    _ret = float(_pf2.get("1Y") or _m.get("ret_1y") or 0)
                except Exception:
                    _ret = 0.0
                try:
                    _div = float(_mj.get("moneydj_div_yield") or _m.get("annual_div_rate") or 0)
                except Exception:
                    _div = 0.0
                _rc_names.append(_name)
                _rc_ret.append(round(_ret, 2))
                _rc_div.append(round(_div, 2))

            if _rc_names:
                _rc_colors = []
                for _r, _d in zip(_rc_ret, _rc_div):
                    if _d > 0 and _r < _d:
                        _rc_colors.append("#f44336")   # 吃本金 → 紅
                    elif _d > 0 and _r < _d * 1.2:
                        _rc_colors.append("#ff9800")   # 邊緣 → 橙
                    else:
                        _rc_colors.append("#00c853")   # 健康 → 綠

                fig_rc = go.Figure()
                # 含息報酬率長條
                fig_rc.add_trace(go.Bar(
                    x=_rc_names, y=_rc_ret,
                    name="含息報酬率(1Y)%",
                    marker_color=_rc_colors,
                    text=[f"{v:.1f}%" for v in _rc_ret],
                    textposition="outside",
                    hovertemplate="%{x}<br>含息報酬：%{y:.2f}%<extra></extra>"))
                # 配息年化率紅色點線
                if any(d > 0 for d in _rc_div):
                    fig_rc.add_trace(go.Scatter(
                        x=_rc_names, y=_rc_div,
                        name="配息年化率%",
                        mode="markers+lines",
                        line=dict(color="#f44336", width=1.5, dash="dot"),
                        marker=dict(symbol="diamond", size=8, color="#f44336"),
                        hovertemplate="%{x}<br>配息率：%{y:.2f}%<extra></extra>"))
                # 零基準線
                fig_rc.add_hline(y=0, line_color="#555", line_width=1)
                fig_rc.update_layout(
                    paper_bgcolor="#0e1117", plot_bgcolor="#161b22",
                    font_color="#e6edf3", height=320,
                    margin=dict(t=30, b=20, l=40, r=20),
                    legend=dict(orientation="h", font_size=10, y=1.08),
                    yaxis_title="報酬率 / 配息率 (%)",
                    bargap=0.35, hovermode="x unified")
                st.plotly_chart(fig_rc, use_container_width=True)

                # 吃本金統計摘要
                _eat_n = sum(1 for r, d in zip(_rc_ret, _rc_div) if d > 0 and r < d)
                _ok_n  = len(_rc_names) - _eat_n
                _sc1, _sc2, _sc3 = st.columns(3)
                _sc1.metric("組合基金數", len(_rc_names))
                _sc2.metric("✅ 現金流健康", _ok_n)
                _sc3.metric("🔴 吃本金警示", _eat_n,
                            delta=f"-{_eat_n} 檔需檢視" if _eat_n else None,
                            delta_color="inverse")

        # ── 以息養股雙模式現金流試算（Core Protocol Ch.3.3）──
        if _loaded_pf:
            st.divider(); st.markdown("### 💰 以息養股現金流試算")
            _cf_tab_new, _cf_tab_hold = st.tabs(["🛒 新購試算", "📦 現有持倉"])

            # 各基金配息率查表
            def _get_dy(f):
                _mj = f.get("moneydj_raw",{}) or {}
                try: return float(_mj.get("moneydj_div_yield") or f.get("metrics",{}).get("annual_div_rate",0) or 0)
                except: return 0.0
            def _get_nav(f):
                _mj = f.get("moneydj_raw",{}) or {}
                try: return float(_mj.get("nav_latest") or f.get("metrics",{}).get("nav") or 0)
                except: return 0.0

            with _cf_tab_new:
                st.caption("輸入預計投入金額，推算可購單位數與每月台幣配息預估")
                _new_invest = st.number_input("預計投入總金額（NTD）", min_value=0, step=10000,
                    value=1000000, key="cf_new_invest")
                _new_fx = st.number_input("美元匯率（1 USD = NTD）", min_value=20.0, max_value=40.0,
                    value=31.5, step=0.1, key="cf_new_fx")
                if _new_invest > 0:
                    _n_annual = 0.0; _n_rows = []
                    for f in _loaded_pf:
                        _inv_f = f.get("invest_twd", 0) or 0
                        _ratio = _inv_f / sum(g.get("invest_twd",1) or 1 for g in _loaded_pf) if _loaded_pf else 0
                        _alloc = _new_invest * _ratio
                        _dy_f  = _get_dy(f); _nav_f = _get_nav(f)
                        _units = (_alloc / _new_fx / _nav_f) if (_nav_f > 0 and _new_fx > 0) else 0
                        _ann_f = _alloc * _dy_f / 100
                        _n_annual += _ann_f
                        _n_rows.append({"基金": (f.get("name") or f["code"])[:22],
                                        "配置(NTD)": f"NT${_alloc:,.0f}",
                                        "配息率": f"{_dy_f:.2f}%",
                                        "預估單位數": f"{_units:,.1f}",
                                        "年配息(NTD)": f"NT${_ann_f:,.0f}"})
                    st.dataframe(pd.DataFrame(_n_rows), use_container_width=True, hide_index=True)
                    _n_monthly = _n_annual / 12
                    nc1, nc2, nc3 = st.columns(3)
                    nc1.metric("投入金額", f"NT${_new_invest:,.0f}")
                    nc2.metric("預估年配息", f"NT${_n_annual:,.0f}")
                    nc3.metric("預估月均配息", f"NT${_n_monthly:,.0f}")

            with _cf_tab_hold:
                st.caption("輸入已持有單位數，精確計算現金流（依最新淨值 × 配息率 × 匯率）")
                _hold_fx = st.number_input("美元匯率（1 USD = NTD）", min_value=20.0, max_value=40.0,
                    value=31.5, step=0.1, key="cf_hold_fx")
                _h_annual = 0.0; _h_rows = []
                for f in _loaded_pf:
                    _nav_f = _get_nav(f); _dy_f = _get_dy(f)
                    _fn    = (f.get("name") or f["code"])[:22]
                    _units_key = f"cf_units_{f['code']}"
                    _units_val = st.number_input(f"持有單位數 — {_fn}", min_value=0.0,
                        step=100.0, value=0.0, key=_units_key)
                    _val_usd   = _units_val * _nav_f
                    _ann_f_usd = _val_usd * _dy_f / 100
                    _ann_f_twd = _ann_f_usd * _hold_fx
                    _h_annual += _ann_f_twd
                    _h_rows.append({"基金": _fn,
                                    "持有單位": f"{_units_val:,.1f}",
                                    "淨值(USD)": f"{_nav_f:.4f}" if _nav_f else "—",
                                    "年配息(USD)": f"{_ann_f_usd:,.2f}",
                                    "年配息(NTD)": f"NT${_ann_f_twd:,.0f}"})
                if _h_rows:
                    st.dataframe(pd.DataFrame(_h_rows), use_container_width=True, hide_index=True)
                    _h_monthly = _h_annual / 12
                    hc1, hc2, hc3 = st.columns(3)
                    hc1.metric("匯率", f"1 USD = NT${_hold_fx}")
                    hc2.metric("預估年配息", f"NT${_h_annual:,.0f}")
                    hc3.metric("預估月均配息", f"NT${_h_monthly:,.0f}")

# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — 回測
# ══════════════════════════════════════════════════════════════════════════════
with tab4:
    st.markdown("## 🔬 歷史回測")
    st.caption("選取組合中已載入的基金，或輸入新基金代碼，模擬歷史績效並計算指標。")

    # ── 選擇回測基金 ──────────────────────────────────────────────────────────
    pf_loaded = [
        f for f in st.session_state.portfolio_funds
        if f.get("loaded") and not f.get("load_error")
    ]

    col_bt_left, col_bt_right = st.columns([3, 2])

    with col_bt_left:
        st.markdown("#### 選取要回測的基金")

        # 從組合選
        if pf_loaded:
            bt_choices = [
                f"{f.get('name','') or f['code']} ({f['code']})" for f in pf_loaded
            ]
            bt_selected = st.multiselect(
                "從組合基金選取（可多選）",
                options=bt_choices,
                default=bt_choices[:min(3, len(bt_choices))],
                key="bt_multi_select",
            )
            bt_codes_from_pf = [
                pf_loaded[bt_choices.index(s)]["code"]
                for s in bt_selected
            ]
        else:
            bt_codes_from_pf = []
            st.info("組合基金尚無已載入資料，請先至「組合基金」頁籤新增並載入基金。")

        # 額外輸入
        bt_extra_raw = st.text_input(
            "額外加入基金代碼（逗號分隔，選填）",
            placeholder="例：BM0019X, BM0021X",
            key="bt_extra_input",
        )
        bt_extra_codes = [
            c.strip() for c in bt_extra_raw.split(",") if c.strip()
        ]
        bt_all_codes = list(dict.fromkeys(bt_codes_from_pf + bt_extra_codes))

    with col_bt_right:
        st.markdown("#### 回測設定")
        bt_period = st.selectbox(
            "回測期間",
            ["近 1 年", "近 2 年", "近 3 年", "近 5 年", "全部"],
            index=2, key="bt_period",
        )
        bt_rebalance = st.selectbox(
            "再平衡頻率",
            ["月底再平衡", "季底再平衡", "買入持有"],
            index=0, key="bt_rebalance",
        )
        rebalance_map = {"月底再平衡": "ME", "季底再平衡": "QE", "買入持有": None}

        if bt_all_codes:
            default_wts = [round(100 / len(bt_all_codes), 1)] * len(bt_all_codes)
            wt_rows = []
            st.markdown("**各基金權重（%）**")
            for idx, code in enumerate(bt_all_codes):
                w_val = st.number_input(
                    code, min_value=0.0, max_value=100.0,
                    value=float(default_wts[idx]),
                    step=5.0, key=f"bt_wt_{code}",
                )
                wt_rows.append(w_val)
        else:
            wt_rows = []

    # ── 執行回測 ─────────────────────────────────────────────────────────────
    st.divider()
    run_bt = st.button("▶ 執行回測", type="primary", key="run_backtest_btn",
                       disabled=len(bt_all_codes) == 0)

    if run_bt and bt_all_codes:
        bt_status = st.empty()
        bt_status.info(f"正在抓取 {len(bt_all_codes)} 支基金淨值…")

        nav_data = {}
        fetch_errors = []
        for code in bt_all_codes:
            # 先嘗試從已載入組合取
            cached = next(
                (f for f in pf_loaded if f["code"] == code), None
            )
            raw_mj = (cached or {}).get("moneydj_raw") if cached else None

            if raw_mj and raw_mj.get("nav_history"):
                nav_hist = raw_mj["nav_history"]
            else:
                # 重新抓取
                url_candidate = f"https://www.moneydj.com/funddj/ya/yp001000.djhtm?a={code}"
                try:
                    res = fetch_fund_from_moneydj_url(url_candidate)
                    nav_hist = (res or {}).get("nav_history", [])
                except Exception as e:
                    fetch_errors.append(f"{code}: {e}")
                    nav_hist = []

            if nav_hist:
                try:
                    _df = pd.DataFrame(nav_hist)
                    _df["date"] = pd.to_datetime(_df["date"])
                    _df = _df.sort_values("date").set_index("date")
                    nav_data[code] = _df["nav"].astype(float)
                except Exception as e:
                    fetch_errors.append(f"{code} 解析失敗: {e}")

        if fetch_errors:
            for err in fetch_errors:
                st.warning(f"⚠️ {err}")

        if not nav_data:
            bt_status.error("無法取得任何基金淨值資料，無法執行回測。")
        else:
            # 建立 NAV DataFrame，對齊日期
            nav_df = pd.DataFrame(nav_data).dropna(how="all")

            # 依期間截取
            _period_months = {
                "近 1 年": 12, "近 2 年": 24, "近 3 年": 36,
                "近 5 年": 60, "全部": None,
            }
            _pm = _period_months.get(bt_period)
            if _pm:
                cutoff = nav_df.index.max() - pd.DateOffset(months=_pm)
                nav_df = nav_df[nav_df.index >= cutoff]

            # 對齊後刪除全 NaN 行，再前向填充
            nav_df = nav_df.ffill().dropna(how="all")

            # 各月底抽樣（統一月頻）
            nav_monthly = nav_df.resample("ME").last().dropna(how="all")

            if len(nav_monthly) < 4:
                bt_status.error("有效月底淨值資料不足（需至少 4 期），請換更長的回測期間。")
            else:
                # 建立組合回報
                codes_avail = [c for c in bt_all_codes if c in nav_monthly.columns]
                if not codes_avail:
                    bt_status.error("所選基金均無月底淨值資料。")
                else:
                    # 權重
                    raw_wts = {
                        code: wt_rows[bt_all_codes.index(code)]
                        for code in codes_avail
                    }
                    total_w = sum(raw_wts.values()) or 1.0
                    wts = pd.Series({c: v / total_w for c, v in raw_wts.items()})

                    bt_result = backtest_portfolio(
                        nav_monthly[codes_avail],
                        wts,
                        rebalance=rebalance_map[bt_rebalance],
                    )
                    metrics = calc_performance_metrics(
                        bt_result["equity_curve"],
                        bt_result["portfolio_return"],
                        rf=0.02, freq=12,
                    )

                    bt_status.success(
                        f"回測完成 — {len(nav_monthly)} 期月底資料 "
                        f"（{nav_monthly.index[0].strftime('%Y-%m')} ~ "
                        f"{nav_monthly.index[-1].strftime('%Y-%m')}）"
                    )

                    # ── 指標卡 ───────────────────────────────────────────────
                    st.markdown("### 📊 績效摘要")
                    m1, m2, m3, m4, m5, m6 = st.columns(6)
                    m1.metric("總報酬", f"{metrics.get('total_return','—')}%")
                    m2.metric("年化報酬", f"{metrics.get('ann_return','—')}%")
                    m3.metric("年化波動", f"{metrics.get('ann_vol','—')}%")
                    m4.metric("Sharpe", f"{metrics.get('sharpe','—')}")
                    m5.metric("Sortino", f"{metrics.get('sortino','—')}")
                    m6.metric("最大回撤", f"{metrics.get('max_drawdown','—')}%")

                    # ── 淨值曲線圖 ───────────────────────────────────────────
                    st.markdown("### 📈 組合淨值曲線")
                    eq = bt_result["equity_curve"].reset_index()
                    eq.columns = ["日期", "淨值指數"]
                    st.line_chart(eq.set_index("日期"))

                    # ── 回撤圖 ───────────────────────────────────────────────
                    st.markdown("### 📉 水下曲線（Drawdown）")
                    dd = bt_result["drawdown"].reset_index()
                    dd.columns = ["日期", "回撤%"]
                    dd["回撤%"] = dd["回撤%"] * 100
                    st.area_chart(dd.set_index("日期"), color="#ff4444")

                    # ── 個別基金快速回測 ─────────────────────────────────────
                    st.markdown("### 🔍 個別基金指標對比")
                    single_rows = []
                    for code in codes_avail:
                        s_metrics = quick_backtest(nav_monthly[code].dropna(), freq=12)
                        single_rows.append({
                            "基金代碼": code,
                            "總報酬(%)": s_metrics.get("total_return", "—"),
                            "年化報酬(%)": s_metrics.get("ann_return", "—"),
                            "年化波動(%)": s_metrics.get("ann_vol", "—"),
                            "Sharpe": s_metrics.get("sharpe", "—"),
                            "Sortino": s_metrics.get("sortino", "—"),
                            "最大回撤(%)": s_metrics.get("max_drawdown", "—"),
                            "Calmar": s_metrics.get("calmar", "—"),
                        })
                    st.dataframe(pd.DataFrame(single_rows), use_container_width=True)

    elif not run_bt and bt_all_codes:
        st.info("設定完成後按「▶ 執行回測」開始分析。")
    else:
        st.info("請先在上方選取基金，再執行回測。")

# ══════════════════════════════════════════════════════
# TAB 5 — 資料診斷
# ══════════════════════════════════════════════════════
with tab5:
    _d5_hdr, _d5_btn = st.columns([3, 1])
    with _d5_hdr:
        st.markdown("## 🔬 資料診斷")
        st.caption("確認所有數據來源是否成功下載，方便排查問題")
    with _d5_btn:
        st.markdown("<div style='margin-top:20px'></div>", unsafe_allow_html=True)
        if st.button("🔄 重新載入總經", key="btn_d5_refresh"):
            st.session_state.macro_done = False
            st.rerun()

    # ── Section 1: 總經指標健康燈號 ──────────────────────────────
    st.markdown("### 🌐 總經指標（FRED / yfinance）")
    _d5_ind = st.session_state.get("indicators", {})
    _d5_phase = st.session_state.get("phase_info", {})

    _D5_EXPECTED = [
        ("PMI",          "ISM製造業PMI",        "FRED",     "NAPM",           ">50擴張"),
        ("CPI",          "CPI年增率",            "FRED",     "CPIAUCSL",       "<2%理想"),
        ("UNEMPLOYMENT", "失業率",               "FRED",     "UNRATE",         "<4.5%"),
        ("YIELD_10Y2Y",  "殖利率利差(10Y-2Y)",   "計算",     "DGS10-DGS2",     "倒掛=衰退"),
        ("YIELD_10Y3M",  "殖利率利差(10Y-3M)",   "計算",     "DGS10-TB3MS",    "最強衰退指標"),
        ("HY_SPREAD",    "高收益債利差",          "FRED",     "BAMLH0A0HYM2",   "<4%樂觀"),
        ("M2",           "M2貨幣供給YoY",        "FRED",     "M2SL",           ">5%寬鬆"),
        ("FED_BS",       "Fed資產負債表YoY",     "FRED",     "WALCL",          "擴表=利多"),
        ("FED_RATE",     "聯準會利率",           "FRED",     "FEDFUNDS",       "升/降息"),
        ("PPI",          "PPI生產者物價YoY",     "FRED",     "PPIACO",         "通膨上游"),
        ("VIX",          "VIX恐慌指數",          "yfinance", "^VIX",           "<18平靜"),
        ("DXY",          "美元指數",             "yfinance", "DX-Y.NYB",       "月漲跌"),
        ("ADL",          "市場廣度RSP/SPY",      "yfinance", "RSP/SPY",        "多頭健康度"),
        ("COPPER",       "銅博士月漲跌",         "yfinance", "HG=F",           "景氣領先"),
    ]

    _d5_cols_hdr = st.columns([2, 2, 1, 2, 2, 1])
    for _d5_ch, _d5_hd in zip(_d5_cols_hdr,
                               ["指標代碼", "中文名稱", "來源", "Ticker/計算式", "數值", "狀態"]):
        _d5_ch.markdown(
            f"<div style='font-size:11px;color:#888;font-weight:700'>{_d5_hd}</div>",
            unsafe_allow_html=True)
    st.markdown("<hr style='margin:4px 0;border-color:#30363d'>", unsafe_allow_html=True)

    _d5_ok = _d5_fail = _d5_na = 0
    for _d5_key, _d5_name, _d5_src, _d5_ticker, _d5_note in _D5_EXPECTED:
        _d5_d   = _d5_ind.get(_d5_key, {})
        _d5_val = _d5_d.get("value") if _d5_d else None
        _d5_err = _d5_d.get("error", "") if _d5_d else ""
        if _d5_val is not None and str(_d5_val) != "" and _d5_val == _d5_val:
            _d5_ok += 1
            _d5_ic, _d5_vc = "✅", "#00c853"
            _d5_unit = _d5_d.get("unit", "") or ""
            _d5_date = f" ({_d5_d.get('date','')})" if _d5_d.get("date") else ""
            try:
                _d5_vstr = f"{float(_d5_val):.2f}{_d5_unit}{_d5_date}"
            except Exception:
                _d5_vstr = str(_d5_val)[:14]
        elif _d5_err:
            _d5_fail += 1
            _d5_ic, _d5_vc = "❌", "#f44336"
            _d5_vstr = str(_d5_err)[:35]
        elif not _d5_ind:
            _d5_na += 1
            _d5_ic, _d5_vc = "⬜", "#555"
            _d5_vstr = "尚未載入"
        else:
            _d5_na += 1
            _d5_ic, _d5_vc = "⚠️", "#ff9800"
            _d5_vstr = "⚠️ 無資料"

        _d5_row = st.columns([2, 2, 1, 2, 2, 1])
        _d5_row[0].markdown(f"<code style='font-size:11px'>{_d5_key}</code>",
                            unsafe_allow_html=True)
        _d5_row[1].markdown(f"<span style='font-size:11px;color:#ccc'>{_d5_name}</span>",
                            unsafe_allow_html=True)
        _d5_row[2].markdown(f"<span style='font-size:10px;color:#888'>{_d5_src}</span>",
                            unsafe_allow_html=True)
        _d5_row[3].markdown(f"<code style='font-size:9px;color:#555'>{_d5_ticker}</code>",
                            unsafe_allow_html=True)
        _d5_row[4].markdown(
            f"<span style='font-size:11px;color:{_d5_vc}'>{_d5_vstr}</span>",
            unsafe_allow_html=True)
        _d5_row[5].markdown(
            f"<span style='font-size:14px'>{_d5_ic}</span>"
            f"<span style='font-size:9px;color:#555;display:block'>{_d5_note}</span>",
            unsafe_allow_html=True)

    # 完整率進度條
    _d5_total = len(_D5_EXPECTED)
    _d5_pct   = round(_d5_ok / _d5_total * 100) if _d5_total else 0
    _d5_bar_c = "#00c853" if _d5_pct >= 80 else ("#ff9800" if _d5_pct >= 50 else "#f44336")
    _d5_upd   = st.session_state.get("macro_last_update")
    _d5_upd_s = _d5_upd.strftime("%H:%M") if hasattr(_d5_upd, "strftime") else "未更新"
    st.markdown(
        f"<div style='background:#1a1f2e;border-radius:8px;padding:10px 14px;margin-top:8px'>"
        f"<div style='display:flex;justify-content:space-between;font-size:12px;margin-bottom:6px'>"
        f"<span>"
        f"<span style='color:#00c853'>✅ 成功 {_d5_ok}</span>　"
        f"<span style='color:#f44336'>❌ 失敗 {_d5_fail}</span>　"
        f"<span style='color:#ff9800'>⚠️ 缺漏 {_d5_na}</span>　"
        f"<span style='color:#888'>/ 共 {_d5_total} 項</span>"
        f"</span>"
        f"<span style='color:#888;font-size:11px'>最後更新：{_d5_upd_s}</span>"
        f"</div>"
        f"<div style='height:8px;background:#0d1117;border-radius:4px;overflow:hidden'>"
        f"<div style='height:100%;width:{_d5_pct}%;background:{_d5_bar_c};border-radius:4px'></div>"
        f"</div>"
        f"<div style='font-size:10px;color:{_d5_bar_c};margin-top:3px;text-align:right'>"
        f"資料完整率 {_d5_pct}%</div>"
        f"</div>", unsafe_allow_html=True)

    if _d5_ind and not _d5_ind.get("PMI"):
        st.warning("⚠️ **PMI** 暫無資料 — FRED NAPM 系列通常延遲 1-2 個月發布，非抓取錯誤。")

    if _d5_phase:
        st.markdown(
            f"<div style='font-size:12px;color:#888;margin-top:6px'>"
            f"景氣位階：<b style='color:#e6edf3'>{_d5_phase.get('phase','?')}</b>　"
            f"評分：<b style='color:#e6edf3'>{_d5_phase.get('score','?')}/10</b>　"
            f"衰退率：<b style='color:#e6edf3'>{_d5_phase.get('rec_prob','?')}%</b>"
            f"</div>", unsafe_allow_html=True)

    # ── 三色燈號：存入 session_state 供 AI 阻斷機制使用 ──────────
    _d5_traffic = "🔴" if _d5_pct < 50 else ("🟡" if _d5_pct < 80 else "🟢")
    st.session_state["data_health_pct"]     = _d5_pct
    st.session_state["data_health_traffic"] = _d5_traffic

    # ── 資料完整度熱力圖（Core Protocol v2.0 Ch.1）────────────────
    if _d5_ind:
        with st.expander("🌡️ 資料完整度熱力圖（近30日 × 14指標）", expanded=True):
            import pandas as _pd_hm
            import numpy as _np_hm
            from datetime import datetime as _dt_hm, timedelta as _td_hm
            _today_hm = _dt_hm.today().date()
            _days_hm  = [(_today_hm - _td_hm(days=i)) for i in range(29, -1, -1)]
            _ind_keys = [r[0] for r in _D5_EXPECTED]
            _ind_lbls = [r[1][:12] for r in _D5_EXPECTED]

            # 建立 30×N 矩陣：0=缺失, 0.5=資料陳舊(>14天), 1=正常
            _hm_z   = []
            _hm_txt = []
            for _ik, _inm in zip(_ind_keys, _ind_lbls):
                _iv  = _d5_ind.get(_ik, {}) or {}
                _row_z, _row_t = [], []
                # 取指標最新資料日期
                _idate_raw = _iv.get("date", "")
                _idate = None
                if _idate_raw:
                    try:
                        _idate = _pd_hm.to_datetime(str(_idate_raw)).date()
                    except Exception:
                        _idate = None
                # 若有 series，取最後有效日
                _iser = _iv.get("series")
                if _iser is not None:
                    try:
                        _s_pd = _pd_hm.Series(_iser).dropna()
                        if len(_s_pd) > 0:
                            _idate = _pd_hm.to_datetime(_s_pd.index[-1]).date()
                    except Exception:
                        pass
                for _d in _days_hm:
                    if _idate is None or _iv.get("value") is None:
                        _row_z.append(0.0)
                        _row_t.append("缺失")
                    else:
                        _age = (_today_hm - _idate).days  # 最新資料距今
                        # 對於「歷史日 d」而言：若 _idate >= d，代表那天之後有資料
                        if _idate >= _d:
                            _row_z.append(1.0)
                            _row_t.append(f"有資料\n(截至{_idate})")
                        elif (_today_hm - _d).days <= 14 and _age <= 45:
                            # 近14天且資料不超過45天舊 → 橙色（資料合理但非即時）
                            _row_z.append(0.5)
                            _row_t.append(f"延遲更新\n(上次{_idate})")
                        else:
                            _row_z.append(0.0)
                            _row_t.append(f"缺失\n(上次{_idate or '無'})")
                _hm_z.append(_row_z)
                _hm_txt.append(_row_t)

            _hm_x = [str(_d) for _d in _days_hm]
            _fig_hm = go.Figure(go.Heatmap(
                z         = _hm_z,
                x         = _hm_x,
                y         = _ind_lbls,
                text      = _hm_txt,
                texttemplate = "",
                colorscale= [[0.0, "#f44336"], [0.5, "#ff9800"], [1.0, "#00c853"]],
                zmin=0, zmax=1,
                showscale = True,
                colorbar  = dict(
                    tickvals=[0, 0.5, 1],
                    ticktext=["缺失", "延遲", "正常"],
                    len=0.6, thickness=10,
                    title=dict(text="狀態", side="right")),
                hovertemplate="指標: %{y}<br>日期: %{x}<br>%{text}<extra></extra>",
            ))
            _fig_hm.update_layout(
                paper_bgcolor="#0e1117", plot_bgcolor="#161b22",
                font_color="#e6edf3",
                height=max(280, len(_ind_keys) * 22 + 80),
                margin=dict(t=10, b=60, l=110, r=80),
                xaxis=dict(tickangle=-45, tickfont_size=9,
                           nticks=10, gridcolor="#1e2a3a"),
                yaxis=dict(tickfont_size=10, autorange="reversed"),
            )
            st.plotly_chart(_fig_hm, use_container_width=True)

            # 三色燈號說明
            _tl_color = "#f44336" if _d5_pct < 50 else ("#ff9800" if _d5_pct < 80 else "#00c853")
            _tl_msg   = (
                "🔴 **紅燈（< 50%）**：資料嚴重不足，AI 分析已停用。請先於 Tab1 載入總經資料。"
                if _d5_pct < 50 else (
                "🟡 **黃燈（50-79%）**：部分指標缺失，AI 分析仍可執行，但結果參考性降低。"
                if _d5_pct < 80 else
                "🟢 **綠燈（≥ 80%）**：資料完整，AI 分析正常啟用。"
            ))
            st.markdown(
                f"<div style='border-left:4px solid {_tl_color};"
                f"background:#1a1f2e;border-radius:0 8px 8px 0;padding:10px 14px;"
                f"margin-top:8px;font-size:13px'>{_tl_msg}</div>",
                unsafe_allow_html=True)

    # ── Section 1b: API 延遲趨勢圖（Core Protocol v2.0 Ch.1）────────
    with st.expander("📡 API 連線延遲趨勢（近24次）", expanded=False):
        import requests as _req_lat
        # 手動測速按鈕
        if st.button("🕐 立即測試三源連線速度", key="btn_d5_ping"):
            _proxy = get_proxy_config() or {}
            _kw    = dict(proxies=_proxy, timeout=8, verify=False,
                          headers={"User-Agent": "Mozilla/5.0"})
            _ping_results: dict = {}
            for _src, _url in [
                ("FRED",     "https://fred.stlouisfed.org/"),
                ("MoneyDJ",  "https://www.moneydj.com/"),
                ("Yahoo/yf", "https://finance.yahoo.com/"),
            ]:
                try:
                    _t0p = _time_mod.time()
                    _req_lat.get(_url, **_kw)
                    _ping_results[_src] = round((_time_mod.time() - _t0p) * 1000)
                except Exception as _pe:
                    _ping_results[_src] = None  # 無法連線
            _lat_log_p = st.session_state.get("api_latency_log", [])
            _lat_log_p.append({
                "label":      _now_tw().strftime("%H:%M"),
                "macro_ms":   _ping_results.get("FRED"),
                "moneydj_ms": _ping_results.get("MoneyDJ"),
                "yf_ms":      _ping_results.get("Yahoo/yf"),
            })
            st.session_state["api_latency_log"] = _lat_log_p[-24:]
            # 即時顯示結果
            _pcols = st.columns(3)
            for _ci, (_sn, _ms) in enumerate(_ping_results.items()):
                _col_c = "#00c853" if (_ms and _ms < 1000) else ("#ff9800" if (_ms and _ms < 3000) else "#f44336")
                _pcols[_ci].markdown(
                    f"<div style='background:#1a1f2e;border-radius:8px;padding:10px;text-align:center'>"
                    f"<div style='font-size:11px;color:#888'>{_sn}</div>"
                    f"<div style='font-size:20px;font-weight:700;color:{_col_c}'>"
                    f"{'N/A' if _ms is None else f'{_ms} ms'}</div></div>",
                    unsafe_allow_html=True)

        # 延遲折線圖
        _lat_hist = st.session_state.get("api_latency_log", [])
        if len(_lat_hist) >= 2:
            _lh_x    = [r.get("label","") for r in _lat_hist]
            _lh_fred = [r.get("macro_ms")   for r in _lat_hist]
            _lh_mj   = [r.get("moneydj_ms") for r in _lat_hist]
            _lh_yf   = [r.get("yf_ms")      for r in _lat_hist]
            _fig_lat  = go.Figure()
            for _lt_name, _lt_y, _lt_color in [
                ("FRED/yfinance(載入)", _lh_fred, "#64b5f6"),
                ("MoneyDJ(測速)",       _lh_mj,   "#ff9800"),
                ("Yahoo/yf(測速)",      _lh_yf,   "#ce93d8"),
            ]:
                if any(v is not None for v in _lt_y):
                    _fig_lat.add_trace(go.Scatter(
                        x=_lh_x, y=_lt_y, name=_lt_name, mode="lines+markers",
                        line=dict(color=_lt_color, width=1.8),
                        marker=dict(size=5),
                        connectgaps=True,
                        hovertemplate="%{y} ms<extra>" + _lt_name + "</extra>"))
            # 警戒線：1000ms 黃 / 3000ms 紅
            _fig_lat.add_hline(y=1000, line_color="#ff9800", line_dash="dot",
                               line_width=1, annotation_text="1s 警示",
                               annotation_font_color="#ff9800",
                               annotation_position="bottom right")
            _fig_lat.add_hline(y=3000, line_color="#f44336", line_dash="dash",
                               line_width=1, annotation_text="3s 警戒",
                               annotation_font_color="#f44336",
                               annotation_position="bottom right")
            _fig_lat.update_layout(
                paper_bgcolor="#0e1117", plot_bgcolor="#161b22",
                font_color="#e6edf3", height=260,
                margin=dict(t=10, b=40, l=60, r=20),
                xaxis=dict(tickangle=-30, tickfont_size=9, gridcolor="#1e2a3a"),
                yaxis=dict(title="回應時間 (ms)", gridcolor="#1e2a3a"),
                legend=dict(orientation="h", font_size=10, y=1.05),
                hovermode="x unified")
            st.plotly_chart(_fig_lat, use_container_width=True)
        else:
            st.info("尚無延遲記錄。點擊「立即測試」或先於 Tab1 載入總經資料，系統將自動記錄 FRED/yfinance 回應時間。")

    st.divider()

    # ── Section 2: API Key 狀態 ───────────────────────────────────
    st.markdown("### 🔑 API 金鑰狀態")
    _d5_k1, _d5_k2 = st.columns(2)
    with _d5_k1:
        _d5_fred_ok = bool(FRED_KEY)
        st.markdown(
            f"<div style='background:#1a1f2e;border-radius:8px;padding:12px'>"
            f"<div style='font-size:11px;color:#888'>FRED API Key</div>"
            f"<div style='font-size:16px;font-weight:700;"
            f"color:{'#00c853' if _d5_fred_ok else '#f44336'}'>"
            f"{'✅ 已設定' if _d5_fred_ok else '❌ 未填寫'}</div>"
            f"<div style='font-size:10px;color:#555'>"
            f"{'...' + FRED_KEY[-6:] if _d5_fred_ok and len(FRED_KEY) > 6 else '請在 secrets.toml 填入'}"
            f"</div></div>", unsafe_allow_html=True)
    with _d5_k2:
        _d5_gem_ok = bool(GEMINI_KEY)
        st.markdown(
            f"<div style='background:#1a1f2e;border-radius:8px;padding:12px'>"
            f"<div style='font-size:11px;color:#888'>Gemini API Key</div>"
            f"<div style='font-size:16px;font-weight:700;"
            f"color:{'#00c853' if _d5_gem_ok else '#f44336'}'>"
            f"{'✅ 已設定' if _d5_gem_ok else '❌ 未填寫'}</div>"
            f"<div style='font-size:10px;color:#555'>"
            f"{'...' + GEMINI_KEY[-6:] if _d5_gem_ok and len(GEMINI_KEY) > 6 else '請在 secrets.toml 填入'}"
            f"</div></div>", unsafe_allow_html=True)

    st.divider()

    # ── Section 3: 基金逐筆診斷 ───────────────────────────────────
    st.markdown("### 📊 基金資料診斷")
    _d5_pf   = st.session_state.get("portfolio_funds", []) or []
    _d5_cf   = st.session_state.get("current_fund")

    # 合併組合基金 + 個別基金（去重）
    _d5_list = list(_d5_pf)
    if _d5_cf:
        _d5_cf_code = _d5_cf.get("fund_code", "") or _d5_cf.get("full_key", "")
        if not any(f.get("code") == _d5_cf_code for f in _d5_list):
            _d5_list.append({
                "code": _d5_cf_code,
                "name": _d5_cf.get("fund_name", "") or _d5_cf_code,
                "loaded": True,
                "metrics": _d5_cf.get("metrics", {}),
                "moneydj_raw": _d5_cf,
                "dividends": _d5_cf.get("dividends", []),
                "series": _d5_cf.get("series"),
                "_source": "個別基金分析",
            })

    if not _d5_list:
        st.info("尚未載入任何基金。請至「單一基金」或「組合基金」Tab 載入後再查看。")
    else:
        def _d5_cell(col, label, value, ok_cond=True, fmt=None):
            _empty = (value is None or value == "" or
                      (isinstance(value, (dict, list)) and not value))
            if _empty:
                _ic, _vc, _vs = "⚠️", "#ff9800", "無資料"
            else:
                try:
                    _ic  = "✅" if bool(ok_cond) else "⚠️"
                    _vc  = "#00c853" if bool(ok_cond) else "#ff9800"
                    _vs  = fmt(value) if fmt else str(value)[:60]
                except Exception:
                    _ic, _vc, _vs = "⚠️", "#ff9800", str(value)[:30]
            col.markdown(
                f"<div style='background:#1a1f2e;border-radius:6px;padding:6px 8px'>"
                f"<div style='font-size:9px;color:#666'>{label}</div>"
                f"<div style='font-size:13px;color:{_vc};font-weight:700'>{_ic} {_vs}</div>"
                f"</div>", unsafe_allow_html=True)

        for _d5_fd in _d5_list:
            _d5_code  = _d5_fd.get("code", "?")
            _d5_fname = _d5_fd.get("name", "") or _d5_code
            _d5_mj    = _d5_fd.get("moneydj_raw", {}) or {}
            _d5_m     = _d5_fd.get("metrics", {}) or {}
            _d5_err   = _d5_fd.get("error", "") or _d5_mj.get("error", "")
            _d5_nav   = _d5_m.get("nav") or _d5_mj.get("nav")
            _d5_adr   = _d5_mj.get("moneydj_div_yield") or _d5_m.get("annual_div_rate")
            _d5_perf  = _d5_mj.get("perf", {}) or {}
            _d5_risk  = (_d5_mj.get("risk_metrics", {}) or {})
            _d5_r1y   = (_d5_risk.get("risk_table") or {}).get("一年", {}) or {}
            _d5_divs  = _d5_fd.get("dividends") or _d5_mj.get("dividends") or []
            _d5_divs  = _d5_divs if isinstance(_d5_divs, list) else []
            _d5_hold  = (_d5_mj.get("holdings") or {})
            _d5_sects = _d5_hold.get("sector_alloc", []) or []
            _d5_tops  = _d5_hold.get("top_holdings", []) or []

            _d5_raw_s = _d5_fd.get("series")
            if _d5_raw_s is None:
                _d5_raw_s = _d5_mj.get("series")
            try:
                import pandas as _pd_d5
                _d5_slen = len(_d5_raw_s) if isinstance(_d5_raw_s, _pd_d5.Series) else 0
            except Exception:
                _d5_slen = 0

            _d5_ok_icon = "✅" if _d5_fd.get("loaded") and not _d5_err else ("❌" if _d5_err else "⬜")
            with st.expander(f"{_d5_ok_icon} {_d5_fname[:35]} ({_d5_code})",
                             expanded=bool(_d5_err)):
                # Row 1: NAV / 配息率 / 1Y報酬 / 淨值筆數
                _r1 = st.columns(4)
                _d5_cell(_r1[0], "最新淨值 NAV",   _d5_nav,
                         ok_cond=(_d5_nav is not None and float(_d5_nav or 0) > 0),
                         fmt=lambda v: f"{float(v):.4f}")
                _d5_cell(_r1[1], "年化配息率",      _d5_adr,
                         ok_cond=(_d5_adr is not None and float(_d5_adr or 0) > 0),
                         fmt=lambda v: f"{float(v):.2f}%")
                _d5_cell(_r1[2], "1Y含息報酬",      _d5_perf.get("1Y"),
                         ok_cond=(_d5_perf.get("1Y") is not None),
                         fmt=lambda v: f"{v:.2f}%")
                _d5_cell(_r1[3], "淨值歷史筆數",    _d5_slen if _d5_slen > 0 else None,
                         ok_cond=(_d5_slen >= 30),
                         fmt=lambda v: f"{v} 筆")
                st.markdown("<div style='margin:4px 0'></div>", unsafe_allow_html=True)
                # Row 2: 配息筆數 / 標準差 / Sharpe / MoneyDJ wb01
                _r2 = st.columns(4)
                _d5_cell(_r2[0], "配息記錄筆數",    len(_d5_divs) if _d5_divs else None,
                         ok_cond=(len(_d5_divs) >= 1),
                         fmt=lambda v: f"{v} 筆")
                _d5_cell(_r2[1], "標準差(1Y)",      _d5_r1y.get("標準差"),
                         ok_cond=(_d5_r1y.get("標準差") is not None),
                         fmt=lambda v: f"{v}%")
                _d5_cell(_r2[2], "Sharpe(1Y)",      _d5_r1y.get("Sharpe"),
                         ok_cond=(_d5_r1y.get("Sharpe") is not None),
                         fmt=lambda v: str(v))
                _d5_cell(_r2[3], "wb01報酬資料",    _d5_perf.get("1Y"),
                         ok_cond=(_d5_perf.get("1Y") is not None),
                         fmt=lambda v: "已取得 ✓")
                st.markdown("<div style='margin:4px 0'></div>", unsafe_allow_html=True)
                # Row 3: holdings
                _r3 = st.columns(4)
                _d5_cell(_r3[0], "holdings物件",    _d5_hold or None,
                         ok_cond=bool(_d5_hold),
                         fmt=lambda v: "有資料 ✓")
                _d5_cell(_r3[1], "產業配置筆數",    len(_d5_sects) if _d5_sects else None,
                         ok_cond=(len(_d5_sects) >= 3),
                         fmt=lambda v: f"{v} 項")
                _d5_cell(_r3[2], "前10大持股",      len(_d5_tops) if _d5_tops else None,
                         ok_cond=(len(_d5_tops) >= 5),
                         fmt=lambda v: f"{v} 檔")
                _d5_cell(_r3[3], "基本資料",        _d5_mj.get("investment_target"),
                         ok_cond=bool(_d5_mj.get("investment_target")),
                         fmt=lambda v: "已取得 ✓")

                st.markdown(
                    f"<span style='font-size:10px;color:#555'>"
                    f"來源：{_d5_fd.get('_source','投資組合')} | "
                    f"is_core: {_d5_fd.get('is_core','?')} | "
                    f"currency: {_d5_fd.get('currency', _d5_mj.get('currency','?'))}"
                    f"</span>", unsafe_allow_html=True)
                if _d5_err:
                    st.error(f"❌ 錯誤：{str(_d5_err)[:200]}")

# ══════════════════════════════════════════════════════
# TAB 6 — 說明書
# ══════════════════════════════════════════════════════
with tab6:
    st.markdown("## 📖 系統說明書 — 公式與判斷標準完整說明")
    st.caption("解釋所有評分模型、公式與指標的計算方式，方便進階使用者理解決策邏輯。")

    _t6 = st.tabs([
        "🧮 1. Macro Score",
        "🌤️ 2. 景氣天氣",
        "🏆 3. 六因子評分",
        "🔴 4. 吃本金診斷",
        "⚖️ 5. 再平衡公式",
        "🇹🇼 6. 台股TPI",
        "🛡️⚡ 7. 核心衛星",
        "🔄 8. 汰弱留強",
    ])

    # ── 1. Macro Score ────────────────────────────────────────────
    with _t6[0]:
        st.markdown("### 🧮 AI Macro Score — 加權景氣評分")
        st.markdown("""
**公式：**
```
Macro_Score = Σ(wᵢ × sᵢ) / Σ(wᵢ)  →  正規化到 0~10

score_normalized = (earned_score + total_weight) / (2 × total_weight) × 10
```
""")
        st.dataframe(pd.DataFrame([
            ["殖利率利差 10Y-2Y", "DGS10-DGS2",   2,   "±2",   "倒掛(<0)=-2，翻正=+2，>0.5=+1"],
            ["殖利率利差 10Y-3M", "DGS10-TB3MS",  2,   "±2",   "倒掛=-2，翻正=+3（降息確認）"],
            ["PMI 製造業",        "NAPM",          2,   "±2",   ">50=+2，45~50=-1，<45=-2"],
            ["HY 信用利差",       "BAMLH0A0HYM2", 2,   "±2",   "<4%=+2，4~6%=0，>6%=-2"],
            ["M2 流動性",         "M2SL",          1,   "±1",   ">5%=+1，<0%=-1"],
            ["市場廣度 RSP/SPY",  "RSP/SPY",       1,   "±1",   "月漲>0.5%=+1，月跌>1%=-1"],
            ["DXY 美元指數",      "DX-Y.NYB",      1,   "±1",   "月跌>1%=+1（弱美元利多），月漲>2%=-1"],
            ["Fed 資產負債表",    "WALCL",          1,   "±1",   "擴表>5%=+1，縮表<-5%=-1"],
            ["VIX 恐慌指數",      "^VIX",           1,   "±1",   "<18=+1（平靜），>30=-1（恐慌）"],
            ["CPI 通膨率",        "CPIAUCSL",      0.5, "±0.5", "1~2.5%=+0.5，>4%=-0.5"],
            ["Fed Rate",          "FEDFUNDS",      0.5, "±0.5", "降息=+0.5，>5%=-0.5"],
            ["失業率",             "UNRATE",        0.5, "±0.5", "<4.5%=+0.5，>6%=-1"],
            ["PPI 生產者物價",    "PPIACO",         0.5, "±0.5", "0~3%=+0.5，>5%=-0.5"],
            ["銅博士",             "HG=F",           0.5, "±0.5", "月漲>2%=+0.5，月跌>5%=-0.5"],
        ], columns=["指標", "FRED/Ticker", "權重(w)", "分值範圍", "評分邏輯"]),
            use_container_width=True, hide_index=True)
        st.markdown("""
**景氣位階對應：**
| Score | 位階 | 建議股債現金 |
|-------|------|------------|
| 8~10  | 🔴 高峰 | 股 35% / 債 45% / 現金 20% |
| 5~7   | 🟢 擴張 | 股 60% / 債 30% / 現金 10% |
| 3~4   | 🔵 復甦 | 股 40% / 債 40% / 現金 20% |
| 0~2   | 🟡 衰退 | 股 20% / 債 50% / 現金 30% |
""")

    # ── 2. 景氣天氣 ───────────────────────────────────────────────
    with _t6[1]:
        st.markdown("### 🌤️ 總經天氣預報 — Score → 天氣映射")
        st.markdown("""
**公式：**
```
Score ≥ 7  → ☀️ 晴天（建議股票為主）
4 ≤ Score < 7 → ⛅ 多雲（均衡配置）
Score < 4  → ⛈️ 暴雨（防禦為主）
```

| 天氣 | Score 範圍 | 建議配置 | 行動 |
|------|----------|---------|------|
| ☀️ 晴天 | ≥ 7 | 股多債少 | 增加衛星部位，持有成長型基金 |
| ⛅ 多雲 | 4~6 | 股債均衡 | 維持核心配置，輕倉衛星 |
| ⛈️ 暴雨 | < 4 | 債多現金多 | 啟動防禦，核心配息資產優先 |
""")

    # ── 3. 六因子評分 ─────────────────────────────────────────────
    with _t6[2]:
        st.markdown("### 🏆 基金六因子評分（Fund Factor Model）")
        st.markdown("""
**公式：**
```
Fund_Score = Σ(因子得分ᵢ × 權重ᵢ) / Σ(權重ᵢ)    範圍：0~100
```
""")
        st.dataframe(pd.DataFrame([
            ["1. Sharpe Ratio",  "每單位風險的超額報酬",       "25%",
             "min(max((Sharpe+1)/2×100, 0), 100)", "Sharpe=-1→0分；=0→50分；=+1→100分",  "MoneyDJ wb07"],
            ["2. Sortino Ratio", "只懲罰下行波動",             "15%",
             "min(max((Sortino+1)/2×100, 0), 100)", "同 Sharpe 但只計負報酬標準差",       "calc_metrics()"],
            ["3. Max Drawdown",  "歷史最慘跌幅（越小越好）",   "20%",
             "min(max((1-|MaxDD|/30)×100, 0), 100)", "MaxDD=0%→100分；=-30%→0分",        "淨值歷史計算"],
            ["4. Calmar Ratio",  "年化報酬/最大回撤",          "10%",
             "min(max(Calmar/2×100, 0), 100)", "Calmar=0→0分；=2→100分",                 "calc_metrics()"],
            ["5. Alpha",         "含息報酬率 - 配息年化率",    "20%",
             "min(max((Alpha+10)/20×100, 0), 100)", "Alpha=-10%→0分；=0→50分；=+10%→100分", "wb01-wb05"],
            ["6. 費用率",        "年度管理費用（越低越好）",   "10%",
             "min(max((3-費用率)/3×100, 0), 100)", "0%→100分；3%→0分",                   "MoneyDJ 基金資料"],
        ], columns=["因子", "說明", "權重", "計算公式", "數值對應", "資料來源"]),
            use_container_width=True, hide_index=True)
        st.markdown("""
**Grade 等級：**
| Score | Grade | 說明 |
|-------|-------|------|
| 75~100 | **A** | 優秀：風險調整後表現卓越 |
| 55~74  | **B** | 良好：整體表現在平均以上 |
| 40~54  | **C** | 普通：考慮是否汰換 |
| 0~39   | **D** | 待改善：建議評估替代標的 |

⚠️ 缺乏資料的因子不計入加權總分，最少需 Sharpe + Alpha 兩項。
""")

    # ── 4. 吃本金診斷 ─────────────────────────────────────────────
    with _t6[3]:
        st.markdown("### 🔴 吃本金診斷（Capital Return Detection）")
        st.markdown("""
**MK 以息養股核心公式：**
```
吃本金判斷：含息總報酬(wb01 1Y) < 年化配息率(wb05)
```

**資料來源優先序：**
| 數據 | 優先來源 | 備援 |
|------|---------|------|
| 含息報酬率 | MoneyDJ **wb01**（含息實績） | 淨值漲跌% + 配息率 |
| 年化配息率 | MoneyDJ **wb05**（官方值） | 自算：近12月配息/平均淨值 |

**燈號：**
- 🟢 **健康**：含息報酬率 ≥ 配息率（有淨值成長作支撐）
- 🟡 **警示**：含息報酬率略低於配息率（正在侵蝕本金）
- 🔴 **吃本金**：含息報酬率 << 配息率（配息主要來自本金返還）

**實例：**
```
安聯收益成長：含息1Y = +5.2%，配息率 = 9.6%
  → 差距 -4.4%，代表每年淨值被侵蝕 4.4%
  → 繼續持有10年後，本金將大幅減損
```
""")

    # ── 5. 再平衡公式 ─────────────────────────────────────────────
    with _t6[4]:
        st.markdown("### ⚖️ 再平衡公式（One-Click Rebalance）")
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

> 0 → 核心太多：從「最大衛星基金」贖回 ΔNT$，轉入「最小核心基金」
< 0 → 衛星太多：從「最大核心基金」獲利了結 ΔNT$，轉入「最小衛星基金」
```
偏離金額 = |偏移%| × 總投入金額
""")

    # ── 6. 台股TPI ────────────────────────────────────────────────
    with _t6[5]:
        st.markdown("### 🇹🇼 台灣市場轉折點指標（TPI v15.1）")
        st.markdown("""
**公式：**
```
TPI = Z(Breadth) × 0.4 + Z(FII) × 0.3 + Z(M1B/M2) × 0.3
```

| 因子 | 說明 | 資料來源 |
|------|------|---------|
| **Z(Breadth)** 市場寬度 | (上漲家數-下跌家數)/(上漲+下跌)×100 ÷20 | TWSE MI_INDEX |
| **Z(FII)** 外資淨買 | 外資買超-賣超（元）÷50億 | FinMind API |
| **Z(M1B/M2)** 貨幣動能 | M1B成長率 vs M2成長率交叉 | 央行 ms1.json |

**水溫對應：**
| TPI | 水溫 | 訊號 | 建議行動 |
|-----|------|------|---------|
| ≥ +1.5 | 🥵 沸點 | 🔴 | 上漲家數銳減，啟動獲利了結 |
| +0.5~+1.5 | 🌡️ 溫熱 | 🟡 | 持續觀察，衛星設停利 |
| -0.5~+0.5 | ⚖️ 常溫 | ⚪ | 維持配置，觀察變化 |
| -1.5~-0.5 | 🌡️ 偏冷 | 🟡 | 外資轉弱，降低台股部位 |
| ≤ -1.5 | 🥶 冰點 | 🟢 | 散戶絕望期，分批建倉訊號 |

⚠️ TPI 為輔助參考指標，需配合景氣位階綜合判斷。
""")

    # ── 7. 核心衛星分類 ──────────────────────────────────────────
    with _t6[6]:
        st.markdown("### 🛡️⚡ 核心/衛星分類邏輯")
        st.markdown("**優先序：手動設定 > 關鍵字比對 > 預設（衛星）**")
        st.dataframe(pd.DataFrame([
            ["🛡️ 核心", "債、收益、配息、平衡、高息、公用、多元、income、bond、dividend、balanced"],
            ["⚡ 衛星", "AI、科技、半導體、成長、主題、印度、越南、生技、醫療、能源、tech、growth"],
        ], columns=["分類", "觸發關鍵字（基金名稱含有任一）"]),
            use_container_width=True, hide_index=True)
        st.markdown("""
**β 係數分類：**
| β 值 | 標籤 | 建議比重 |
|------|------|---------|
| < 0.8 | 🛡️ 定海神針 | 核心部位 60~80% |
| 0.8~1.2 | ⚖️ 市場同步 | 視景氣位階調整 |
| > 1.2 | 🚀 衝鋒陷陣 | 衛星部位 10~20% |

**MK 核心/衛星比例目標（預設 80/20）：**
```
核心資產：提供穩定現金流（每月配息），作為「養」衛星的資金來源
衛星資產：追求價差成長，由核心配息「養」，不動用本金
```
偏離 >5% → ⚠️ 建議再平衡　|　偏離 >10% → 🚨 必須執行
""")

    # ── 8. 汰弱留強評分 ──────────────────────────────────────────
    with _t6[7]:
        st.markdown("### 🔄 汰弱留強評分（Security Ranking）")
        st.markdown("""
**核心邏輯：定期汰換績效落後的基金，換入同類前段班**

**觸發條件（任一滿足即亮警示）：**
| 條件 | 建議行動 |
|------|---------|
| 同類四分位連續 ≥2季 第3或4分位 | ⚠️ 追蹤；第3季仍落後 → 換 |
| 同類四分位連續 ≥2季 第4分位（後25%）| 🚨 跨行轉存至前25%標的 |
| 吃本金連續發生（含息報酬 < 配息率）| 🔴 優先汰換 |
| MaxDrawdown 超過同類平均 1.5x | ⚠️ 評估是否替換 |

**汰弱留強評分公式（60分及格）：**
```
汰弱分數 = 含息報酬率 × 40%
         + Sharpe 比率 × 30%
         + (費用率 vs 同類均值) × 30%

< 60分 → 考慮汰換　|　≥ 75分 → 保留
```

**四分位等級：**
| 等級 | 排名 | 含義 |
|------|------|------|
| 第1四分位 | 前25% | 同類最強，優先持有 |
| 第2四分位 | 26~50% | 中上，繼續持有 |
| 第3四分位 | 51~75% | 中下，開始觀察 |
| 第4四分位 | 後25% | 最弱，考慮汰換 |

**實際操作原則：**
1. 每季（3個月）看一次同類排名
2. 連續2季後25% → 啟動汰換計畫（給它一次機會）
3. 找好替換標的後，在「買點」時換（避免在高點換進）
4. 核心資產不輕易換（穩定配息 > 短期績效排名）
""")
