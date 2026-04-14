# =================================================
# 【Cell 11】寫入 ai_engine.py（AI 分析引擎）
# 說明：生成 Gemini AI 分析引擎，負責生成總經分析報告、
#        基金個別分析、投資組合 AI 建議。
# 新手提示：直接執行即可，不需要修改。
#            若 GEMINI_API_KEY 未填，AI 功能會顯示提示訊息，
#            但不影響資料查詢和圖表功能。
# =================================================
"""AI 分析引擎 v13 — 單次呼叫 · 含風險預警快照 · 六因子評分輸入 · 容錯降級"""
import requests, json, re as _re

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

# ── 核心/衛星關鍵字分類 ──────────────────────────────────────
_CORE_KW  = ["債", "收益", "配息", "平衡", "高息", "公用", "多元",
             "income", "bond", "dividend", "balanced", "utility"]
_SAT_KW   = ["ai", "科技", "半導體", "成長", "主題", "印度", "越南",
             "生技", "醫療", "能源", "原物料", "中國", "新興",
             "tech", "innovation", "growth", "emerging"]

def assign_asset_role(fund_name: str, manual_override: str = "") -> str:
    """
    優先序：手動設定 > 名稱關鍵字 > 預設衛星
    回傳 'core' 或 'satellite'
    """
    if manual_override in ("core", "satellite"):
        return manual_override
    name_lower = (fund_name or "").lower()
    if any(kw in name_lower for kw in _CORE_KW):
        return "core"
    if any(kw in name_lower for kw in _SAT_KW):
        return "satellite"
    return "satellite"   # 未知預設衛星（較保守）


# ── Gemini API 呼叫（容錯版）───────────────────────────────
def _gemini(api_key: str, prompt: str, max_tokens: int = 2000,
            retry: int = 2, force_json: bool = False):
    """單次 API 呼叫，容錯降級，不崩潰 App"""
    if not api_key:
        return "⚠️ 請先填入 Gemini API Key"
    import time
    for attempt in range(retry + 1):
        try:
            gen_cfg = {
                "temperature": 0.7,       # 較高溫：輸出更完整自然
                "maxOutputTokens": max_tokens,
            }
            # gemini-2.5-flash 是 thinking 模型：thinkingBudget=0 關閉思考鏈
            # 讓全部 token 用於實際輸出而非內部推理
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": gen_cfg,
            }
            if "2.5" in GEMINI_URL or "flash" in GEMINI_URL:
                body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
            if force_json:
                gen_cfg["responseMimeType"] = "application/json"
            r = requests.post(
                f"{GEMINI_URL}?key={api_key}",
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=90,
            )
            if r.status_code == 200:
                cands = r.json().get("candidates", [])
                if cands:
                    parts = cands[0].get("content", {}).get("parts", [])
                    return "\n".join(p.get("text","") for p in parts if "text" in p).strip()
                return "⚠️ Gemini 回傳空結果，請重試"
            elif r.status_code == 429:
                wait = 20 * (attempt + 1)
                if attempt < retry:
                    time.sleep(wait); continue
                return (
                    "❌ **Gemini 配額已達上限（HTTP 429）**\n\n"
                    "請等待 1-2 分鐘後重試，或至 Google AI Studio 確認用量。"
                )
            else:
                if attempt < retry:
                    time.sleep(5); continue
                return f"❌ HTTP {r.status_code}：{r.text[:150]}"
        except requests.exceptions.Timeout:
            if attempt < retry:
                time.sleep(5); continue
            return "❌ 請求逾時，請重試"
        except Exception as e:
            return f"❌ {e}"
    return "❌ 重試次數已達上限"


# ── 數據快照建構（極致精簡，不傳歷史 Array）─────────────────
def _build_snapshot(indicators: dict, phase_info: dict,
                    portfolio_funds: list, focus_fund: dict,
                    news_headlines: list) -> str:
    """
    將所有數據壓縮為純文字快照，不傳歷史淨值數組。
    目標：整個快照 < 800 tokens
    """
    pi = phase_info or {}
    lines = ["【數據快照 — AI 只能根據此快照分析，嚴禁自行搜尋外部資訊】"]

    # ── 1. 總經（只留關鍵 5 指標 + 位階）────────────────
    lines.append("\n[總經位階]")
    lines.append(
        f"位階:{pi.get('phase','?')} 評分:{pi.get('score','?')}/10 "
        f"趨勢:{pi.get('trend_arrow','?')}→{pi.get('next_phase_name','?')} "
        f"衰退率:{pi.get('rec_prob','?')}%"
    )
    alloc = pi.get("allocation", {})
    if alloc:
        lines.append("建議配置:" + " ".join(f"{k}{v}%" for k,v in alloc.items()))
    alloc_t = pi.get("alloc_transition", {})
    if alloc_t:
        lines.append("轉位階後調整:" + " ".join(
            f"{k}:{v['from']}%→{v['to']}%" for k,v in alloc_t.items()))
    alerts = pi.get("alerts", [])
    if alerts:
        lines.append("⚠️ 警報:" + " | ".join(str(a) for a in alerts[:2]))

    # 只傳最關鍵 5 指標數值
    KEY_IND = ["PMI","HY_SPREAD","YIELD_10Y2Y","VIX","CPI"]
    ind_vals = []
    for k in KEY_IND:
        v = (indicators or {}).get(k, {})
        if v:
            ind_vals.append(f"{k}:{v.get('value','?')}{v.get('unit','')} {v.get('signal','')}")
    if ind_vals:
        lines.append("指標:" + " | ".join(ind_vals))

    # ── 2. 最新新聞標題（最多 3 則，只傳標題）───────────
    if news_headlines:
        lines.append("\n[最新新聞（僅標題）]")
        for h in news_headlines[:3]:
            lines.append(f"• {str(h)[:60]}")

    # ── 3. 組合基金（每檔精簡 1 行）────────────────────
    loaded = [f for f in (portfolio_funds or []) if f.get("loaded")]
    if loaded:
        lines.append(f"\n[投資組合 — {len(loaded)} 檔]")
        for f in loaded:
            m   = f.get("metrics", {}) or {}
            mj  = f.get("moneydj_raw", {}) or {}
            rt  = (mj.get("risk_metrics") or {}).get("risk_table", {}) or {}
            yr  = rt.get("一年", {}) or {}
            pf  = mj.get("perf", {}) or {}
            adr = mj.get("moneydj_div_yield") or m.get("annual_div_rate", 0) or 0
            tr1 = pf.get("1Y")
            eat = "🔴吃本金" if (tr1 is not None and tr1 < adr and adr > 0) else "✅"
            role_raw = "core" if f.get("is_core") else "satellite"
            role = assign_asset_role(f.get("name",""), role_raw)
            role_icon = "🛡️核心" if role == "core" else "⚡衛星"
            pos  = m.get("pos_label", "?")
            inv  = f.get("invest_twd", 0) or 0
            name = f.get("name","") or f.get("code","?")
            lines.append(
                f"  {role_icon} {name[:18]} | "
                f"配息{adr:.1f}% TR1Y:{tr1 if tr1 is not None else 'N/A'}% {eat} | "
                f"σ:{yr.get('標準差','?')}% Sharpe:{yr.get('Sharpe','?')} "
                f"DD:{m.get('max_drawdown','?')}% NAV位置:{pos}"
                + (f" NT${inv:,}" if inv else "")
            )

    # ── 4. 個別基金（僅摘要，不傳歷史淨值）─────────────
    if focus_fund:
        m3  = focus_fund.get("metrics", {}) or {}
        mj3 = focus_fund.get("moneydj_raw", {}) or {}
        pf3 = mj3.get("perf", {}) or {}
        adr3 = mj3.get("moneydj_div_yield") or m3.get("annual_div_rate",0) or 0
        tr3  = pf3.get("1Y")
        eat3 = "🔴吃本金" if (tr3 is not None and tr3 < adr3 and adr3>0) else "✅"
        name3 = focus_fund.get("fund_name","") or "?"
        lines.append(f"\n[個別基金診斷 — {name3}]")
        lines.append(
            f"  NAV:{m3.get('nav','?')} 位置:{m3.get('pos_label','?')} | "
            f"買1σ:{m3.get('buy1','')} 買2σ:{m3.get('buy2','')} 停利:{m3.get('sell1','')}"
        )
        lines.append(f"  配息:{adr3:.1f}% TR1Y:{tr3 if tr3 is not None else 'N/A'}% {eat3}")


    # ── 5. 風險預警快照（v13 新增）────────────────────────────────
    try:
        from portfolio_engine import risk_alert as _ra
        _regime_info = pi.get("regime_info", {}) or {}
        _regime      = _regime_info.get("regime", "")
        _hy          = (indicators or {}).get("HY_SPREAD", {}).get("value")
        _vix_v       = (indicators or {}).get("VIX", {}).get("value")
        _fed_v2      = (indicators or {}).get("FED_RATE", {}).get("value")
        _fed_p2      = (indicators or {}).get("FED_RATE", {}).get("prev")
        _fed_dir     = "up" if (_fed_v2 and _fed_p2 and _fed_v2 > _fed_p2) else "down"
        _alerts      = _ra(regime=_regime, hy_spread=_hy, vix=_vix_v, fed_direction=_fed_dir)
        red_alerts = [a for a in _alerts if a["level"] == "red"]
        if red_alerts:
            lines.append("\n[風險預警]")
            for a in red_alerts[:2]:
                lines.append(f"  {a['message']}")
    except Exception:
        pass

    return "\n".join(lines)


# ── 全局投資決策（主函數）───────────────────────────────────
def analyze_global(api_key: str, indicators: dict, phase_info: dict,
                   portfolio_funds: list = None, focus_fund: dict = None,
                   news_headlines: list = None, core_target_pct: int = 80) -> str:
    """
    v12 唯一 AI 入口：單次呼叫，輸出四節投資決策
    - 不自行搜尋任何外部資訊
    - 輸入 < 800 tokens，輸出 < 1500 tokens
    """
    snapshot = _build_snapshot(indicators, phase_info,
                               portfolio_funds, focus_fund, news_headlines)
    pi = phase_info or {}
    phase = pi.get("phase","?")
    alloc = pi.get("allocation", {})
    alloc_str = " / ".join(f"{k}{v}%" for k,v in alloc.items()) if alloc else "未知"

    loaded = [f for f in (portfolio_funds or []) if f.get("loaded")]
    tot_inv = sum(f.get("invest_twd",0) or 0 for f in loaded)

    prompt = f"""你是採用MK（郭俊宏）以息養股方法論的台灣財經顧問。
你必須輸出完整的 4 個段落，缺少任何一段都是錯誤。
⚠️ 嚴格規則：只能根據以下快照分析，禁止搜尋或引用任何外部資訊。

{snapshot}

═══════════════════════════════════════
請用繁體中文，依序輸出以下【全部4節】，每節用 ### 開頭標題：

### 📍 一、景氣位階判讀
- 當前位階：{phase}，說明評分與趨勢方向
- 主要依據：列出3個關鍵指標數值與解讀
- 拐點觸發條件：何時需要調整配置？

### ⚖️ 二、資產配置建議
- 當前建議：{alloc_str}
- 你的目標：核心{core_target_pct}% / 衛星{100-core_target_pct}%
- 轉換位階後，如何調整？（給具體%數字）

### 🔴 三、持倉警示
每檔基金一行，格式：[基金名] → 🔴減碼/🟡持有/🟢加碼 [一句理由]
（必須涵蓋吃本金、NAV位置偏高、低Sharpe等問題）

### 🔄 四、本週操作待辦清單
請用 Markdown checkbox 格式輸出3-5個具體行動項目：
- [ ] 哪檔需要減碼？減多少？轉入什麼？
- [ ] 哪檔接近-1σ買點，等待加碼？
- [ ] 有無吃本金基金需要處理？
- [ ] 每月定期扣款是否繼續執行？
═══════════════════════════════════════
【必須輸出完整4節，不可提前結束。第四節必須使用 - [ ] checkbox 格式】"""

    return _gemini(api_key, prompt, max_tokens=8192)


# ── 向後相容包裝（舊程式碼仍可呼叫）───────────────────────
def analyze_unified(api_key, indicators, phase_info,
                    portfolio_funds=None, focus_fund=None, max_tokens=1500):
    return analyze_global(api_key, indicators, phase_info,
                          portfolio_funds, focus_fund)

def analyze_macro(api_key, indicators, phase_info, news_text="", data_text=""):
    return analyze_global(api_key, indicators, phase_info)

def analyze_fund_pro(api_key, fund_name, portal, full_key, metrics, dividends,
                     phase_info, currency="USD", risk_metrics=None, holdings=None,
                     perf_data=None, data_text=""):
    try:
        import streamlit as st
        _ind = st.session_state.get("indicators", {})
        _ph  = st.session_state.get("phase_info", phase_info)
        _pf  = st.session_state.get("portfolio_funds", [])
    except Exception:
        _ind = {}; _ph = phase_info; _pf = []
    _fd = {"fund_name": fund_name, "metrics": metrics or {},
           "moneydj_raw": {"perf": perf_data or {}, "risk_metrics": risk_metrics or {},
                           "holdings": holdings or {}, "currency": currency,
                           "moneydj_div_yield": (metrics or {}).get("annual_div_rate")}}
    return analyze_global(api_key, _ind, _ph, _pf, _fd)

def analyze_fund_json(api_key, fund_name, metrics, perf_data, phase_info,
                      risk_metrics=None, holdings=None, currency="USD"):
    """精簡 JSON 摘要（<300 tokens 輸入）"""
    m  = metrics  or {}
    pf = perf_data or {}
    pi = phase_info or {}
    rt = ((risk_metrics or {}).get("risk_table") or {})
    adr   = m.get("annual_div_rate", 0) or 0
    tr1y  = pf.get("1Y")
    eating = (tr1y is not None) and (tr1y < adr) and (adr > 0)
    std   = (rt.get("一年") or {}).get("標準差") or m.get("std_1y","N/A")
    sharpe= (rt.get("一年") or {}).get("Sharpe") or m.get("sharpe","N/A")
    prompt = (
        f"基金:{fund_name}|景氣:{pi.get('phase','?')}({pi.get('score',5)}/10)|"
        f"配息:{adr:.1f}%|TR1Y:{tr1y or 'N/A'}%|{'吃本金🔴' if eating else '健康✅'}|"
        f"σ:{std}%|Sharpe:{sharpe}\n"
        "嚴格只輸出JSON，無其他文字：\n"
        "{\"summary\":\"30字\",\"strengths\":[\"優1\"],\"risks\":[\"險1\"],\"action\":\"操作\",\"score\":0}"
    )
    raw = _gemini(api_key, prompt, 300, force_json=True)
    try:
        c = _re.sub(r"```json\s*|```","", raw).strip()
        m_j = _re.search(r"\{[\s\S]+\}", c)
        if m_j:
            return json.loads(m_j.group())
    except Exception:
        pass
    return {"summary": str(raw)[:80], "strengths":[], "risks":["⚠️ 請重試"],
            "action":"重新分析", "score":50}

def analyze_portfolio_correlation(api_key, funds_list, phase_info, data_text=""):
    try:
        import streamlit as st
        _ind = st.session_state.get("indicators", {})
    except Exception:
        _ind = {}
    return analyze_global(api_key, _ind, phase_info, portfolio_funds=funds_list)


# ====================================================
# AI Automated Error Feedback Loop
# Every Streamlit error intercepted -> LLM reflection -> AI_Error_Ledger.md
# [Tutorial] This is the AI memory system. Dashboard errors are auto-analyzed.
# ====================================================
import os as _os_el, traceback as _tb_el, datetime as _dt_el

def _write_error_ledger(error, context, api_key=""):
    _tb_str = _tb_el.format_exc()
    _ts = _dt_el.datetime.now().strftime("%Y-%m-%d %H:%M")
    _ledger_path = "/content/AI_Error_Ledger.md"
    _reflection = "(no API Key, skip AI reflection)"
    if api_key:
        _prompt = (
            "You are a Python Streamlit dashboard debug expert.\n\n"
            f"[Location] {context}\n"
            f"[Error] {type(error).__name__}: {str(error)[:200]}\n"
            f"[Traceback]\n{_tb_str[:600]}\n\n"
            "Output 3 items (Traditional Chinese, concise):\n"
            "**根本原因**：(1 sentence)\n"
            "**防範規則**：(1 rule)\n"
            "**快速修法**：(1-3 lines in ```python ```)\n"
        )
        try:
            _reflection = _gemini(api_key, _prompt, max_tokens=400)
        except Exception:
            _reflection = "(AI reflection failed)"
    _entry = (
        "\n\n---\n"
        f"## [{_ts}] `{type(error).__name__}` in `{context}`\n\n"
        f"**Error:** {str(error)[:300]}\n\n"
        "<details><summary>Traceback</summary>\n\n"
        f"```\n{_tb_str[:800]}\n```\n\n</details>\n\n"
        f"**AI Reflection:**\n\n{_reflection}\n"
    )
    try:
        if not _os_el.path.exists(_ledger_path):
            with open(_ledger_path, "w", encoding="utf-8") as _f:
                _f.write("# AI_Error_Ledger\n\n> Auto-maintained error log.\n")
        with open(_ledger_path, "a", encoding="utf-8") as _f:
            _f.write(_entry)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
# v18.1 三節結構化總經 AI 摘要
# 依需求輸出：【現狀解讀】【潛在系統性風險評估】【未來一週觀察重點】
# ══════════════════════════════════════════════════════════════════
def analyze_macro_structured(
    api_key: str,
    indicators: dict,
    phase_info: dict,
    news_items: list = None,
    systemic_risk: dict = None,
    max_tokens: int = 2000,
) -> str:
    """
    三節結構化總經 AI 摘要（v18.1）

    MetaPrompt 設計思維：
    1. 強制 3 節標題結構，避免 LLM 自由發揮格式
    2. 數字上下文先行（量化 snapshot < 600 tokens）
    3. 禁止幻覺：不允許引用快照以外的資訊
    4. 輸出語言：繁體中文，適合台灣投資人
    5. 系統性風險節要求：必須評級 LOW / MEDIUM / HIGH 並給出具體觸發條件
    """
    if not api_key:
        return "⚠️ 未設定 GEMINI_API_KEY，AI 摘要功能關閉"

    pi  = phase_info or {}
    ind = indicators or {}

    # ── 量化數據快照（精簡版，< 500 tokens）──────────────────
    KEY_FIELDS = [
        ("PMI",          "ISM PMI"),
        ("YIELD_10Y2Y",  "10Y-2Y 利差"),
        ("YIELD_10Y3M",  "10Y-3M 利差"),
        ("HY_SPREAD",    "HY 信用利差"),
        ("VIX",          "VIX"),
        ("CPI",          "CPI YoY"),
        ("FED_RATE",     "Fed Rate"),
        ("M2",           "M2 YoY"),
        ("UNEMPLOYMENT", "失業率"),
        ("JOBLESS",      "初領失業金(萬)"),
        ("CONSUMER_CONF","密大信心"),
        ("DXY",          "美元指數"),
        ("COPPER",       "銅博士 MoM"),
        ("ADL",          "市場廣度 RSP/SPY"),
    ]
    ind_lines = []
    for key, label in KEY_FIELDS:
        v = ind.get(key, {})
        if not v:
            continue
        val  = v.get("value")
        prev = v.get("prev")
        sig  = v.get("signal", "")
        unit = v.get("unit", "")
        if val is None:
            continue
        val_str  = f"{val:.2f}{unit}" if isinstance(val, float) else str(val)
        prev_str = f"（前：{prev:.2f}{unit}）" if isinstance(prev, (int, float)) else ""
        ind_lines.append(f"  {label}: {val_str}{prev_str} {sig}")

    # 殖利率利差公式說明（供 LLM 理解）
    # 利差 = 10年期美債殖利率 - 2年期美債殖利率
    # 公式：Spread = R(10Y) - R(2Y)；倒掛 < 0 = 歷史衰退前兆

    # ── 新聞標題（最多 5 則，含風險評分）───────────────────
    news_section = ""
    if news_items:
        titles = [item.get("title","")[:70] for item in news_items[:5] if item.get("title")]
        if titles:
            news_section = "\n[近期財經新聞標題（最多5則）]\n" + "\n".join(f"• {t}" for t in titles)

    # ── 系統性風險偵測結果 ─────────────────────────────────
    risk_section = ""
    if systemic_risk:
        rl    = systemic_risk.get("risk_level", "LOW")
        rs    = systemic_risk.get("risk_score", 0)
        kws   = [t["keyword"] for t in systemic_risk.get("triggered", [])[:5]]
        risk_section = (
            f"\n[新聞系統性風險偵測]\n"
            f"  評級: {rl}（加權分數: {rs}）\n"
            + (f"  命中關鍵字: {', '.join(kws)}\n" if kws else "")
        )

    # ── 景氣位階摘要 ──────────────────────────────────────
    alloc     = pi.get("allocation", {})
    alloc_str = " / ".join(f"{k}{v}%" for k, v in alloc.items()) if alloc else "未知"
    phase     = pi.get("phase", "未知")
    score     = pi.get("score", "?")
    rec_prob  = pi.get("rec_prob")
    alerts    = pi.get("alerts", [])

    snapshot = f"""
【量化數據快照 — AI 只能依據此快照分析，嚴禁引用外部資訊】

[景氣位階]
  當前位階: {phase}（評分 {score}/10）
  建議配置: {alloc_str}
  衰退機率: {rec_prob if rec_prob is not None else 'N/A'}%
  風險警報: {' | '.join(alerts[:3]) if alerts else '無'}

[量化指標]
{chr(10).join(ind_lines) or '（無資料）'}
{news_section}
{risk_section}
""".strip()

    # ── 三節結構 Prompt（MetaPrompt）─────────────────────
    prompt = f"""你是一位精通景氣循環、MK 以息養股方法論的台灣財經分析師。
⚠️ 嚴格規則：只能根據以下快照分析，禁止搜尋或引用任何外部資訊，禁止杜撰數字。

{snapshot}

═══════════════════════════════════════════
請用繁體中文輸出以下【完整三節】，必須依序且每節使用 ### 開頭標題：

### 📍 一、現狀解讀
- 以 2-3 句話總結當前景氣位階的核心特徵
- 必須引用快照中的至少 3 個指標數值（含單位）
- 說明目前處於景氣循環的哪個象限（復甦/擴張/高峰/衰退），以及與歷史的相比意義
- 殖利率利差（計算式：10Y利率 - 2Y利率）當前讀數代表什麼訊號？

### 🔴 二、潛在系統性風險評估
- 整合新聞面偵測結果，給出本次評級：LOW / MEDIUM / HIGH，並說明理由
- 列出 2-3 個最需關注的具體風險觸發條件（例：若 VIX 突破 X 或 HY 利差擴大至 Y%）
- 若新聞無高危信號，應明確說明「新聞面暫無系統性警示」並解釋量化面為何支撐此結論

### 🔭 三、未來一週觀察重點
- 列出 3-5 個本週需要追蹤的具體數據/事件（含預期發布時間或觸發條件）
- 每個觀察點需說明：若數據好於預期→如何操作；若差於預期→如何因應
- 結尾給出一句「本週核心操作原則」
═══════════════════════════════════════════
【必須輸出完整三節，不可提前結束，每節至少 3 個具體要點】"""

    return _gemini(api_key, prompt, max_tokens=max_tokens)
