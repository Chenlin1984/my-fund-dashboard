# 基金戰情室 — 技術規格書 (ARCHITECTURE.md)
> 版本：v1.0 | 更新：2026-04-14 | 分支：`claude/system-detox-upgrade-ra7Tp`

---

## §1 專案概覽

### 定位
Streamlit Cloud 部署的基金監控儀表板，整合總經指標、單一基金分析、投資組合管理與回測，透過 NAS Proxy 穿透抓取 MoneyDJ / TDCC 境內資料。

### 核心設計原則
| 原則 | 實作方式 |
|------|---------|
| **零快取** | 全域禁用 `@st.cache_data`，每次操作即時抓取 |
| **模組隔離** | UI（app.py）與運算引擎（各 `*_engine.py`）嚴格分離 |
| **多來源容錯** | 每項資料最多 5 層備援來源（MoneyDJ → TCB → Fundclear → cnyes → Morningstar） |
| **零快取 Session** | 狀態僅存 `st.session_state`，不寫磁碟快取（NAV/div 除外，有效期 20~48h） |

### 技術棧
```
Python 3.11+
Streamlit 1.45.1     — UI 框架
Plotly 5.x           — 互動圖表
pandas / numpy       — 資料處理
yfinance             — 美股/ETF 行情
FRED API             — 總經數據（需 API Key）
Gemini API           — AI 分析（需 API Key）
requests + bs4       — MoneyDJ/TDCC 爬取
scipy                — 投資組合最佳化
```

---

## §2 目錄結構

```
my-fund-dashboard/
├── app.py                  # UI 主程式（1091行）— 6 Tabs 入口，呼叫各引擎
├── macro_engine.py         # 總經引擎（1158行）— FRED/yfinance 指標抓取與評分
├── fund_fetcher.py         # 基金抓取（4706行）— 多來源 NAV/配息/持股爬取
├── ai_engine.py            # AI 引擎（506行） — Gemini prompt 建構與呼叫
├── portfolio_engine.py     # 組合引擎（401行）— 六因子評分/配息安全/再平衡
├── backtest_engine.py      # 回測引擎（152行）— Sharpe/Sortino/MaxDD 計算
├── requirements.txt        # 依賴清單（13套件）
├── CLAUDE.md               # AI 協作規範（Core Protocol v2.0）
├── STATE.md                # 專案進度追蹤器
├── ARCHITECTURE.md         # 本文件
├── .streamlit/
│   └── secrets.toml        # API Keys（FRED_API_KEY, GEMINI_API_KEY, PROXY_URL）
├── cache/                  # 本地 NAV/div 磁碟快取（.pkl，有效期 20~48h）
└── scripts/                # 維運腳本（非主程式）
```

### 各模組職責一覽

| 模組 | 對外暴露 | 不做的事 |
|------|---------|---------|
| `app.py` | Tab UI、sidebar、session state 管理 | 不直接呼叫 HTTP |
| `macro_engine.py` | 總經指標 dict、景氣評分、TPI | 不操作 UI |
| `fund_fetcher.py` | NAV Series、配息 list、holdings dict、metrics dict | 不呼叫 AI |
| `ai_engine.py` | Gemini 回傳 markdown 字串 | 不抓資料、不操作 UI |
| `portfolio_engine.py` | 評分 dict、警示 list、最佳權重 dict | 不抓資料 |
| `backtest_engine.py` | 績效指標 dict（Sharpe/MaxDD/…） | 不抓資料 |

---

## §3 資料流向

### 3.1 全域啟動流
```
Streamlit 啟動
  → _load_keys()                    讀取 secrets.toml / env
  → st.session_state 初始化          14 個 key 設預設值
  → sidebar 渲染                     顯示 API 狀態 / Proxy 狀態
  → st.tabs(6)                       分發至各 Tab
```

### 3.2 Tab1 — 總經儀表板
```
使用者按「載入總經」
  → fetch_all_indicators(fred_api_key)          macro_engine
      ├─ FRED API (14 指標)
      └─ yfinance (VIX / DXY / ADL / COPPER)
  → calc_macro_phase(indicators)                macro_engine
  → identify_regime(indicators)                 macro_engine
  → detect_systemic_risk(news_items)            macro_engine
  → st.session_state 寫入:
      indicators, phase_info, macro_last_update
  → UI 渲染: 天氣卡 / 指標表 / 雷達圖
  → [可選] analyze_macro_structured(api_key, …) ai_engine → Gemini API
      → st.session_state.macro_ai 寫入
```

### 3.3 Tab2 — 單一基金分析
```
使用者輸入 MoneyDJ URL / 代碼
  → parse_moneydj_input(user_input)             fund_fetcher
  → fetch_fund_from_moneydj_url(url)            fund_fetcher
      ├─ MoneyDJ wb01/wb05/wb07 (透過 NAS Proxy)
      ├─ fetch_performance_wb01(code)
      ├─ fetch_risk_metrics(code)
      └─ fetch_holdings(code)
  → calc_metrics(nav_series, divs)              fund_fetcher
  → st.session_state.current_fund 寫入
  → UI 渲染:
      ├─ NAV 折線圖 (Plotly)
      ├─ MK 買點卡（-1σ/-2σ/-3σ）
      ├─ 配息記錄 + 吃本金警示
      │     → dividend_safety(total_return, div_yield)  portfolio_engine
      └─ 持股分析 expander（sector_alloc / top_holdings）
  → [可選] analyze_fund_json(api_key, …)        ai_engine → Gemini API
```

### 3.4 Tab3 — 組合基金
```
使用者加入基金（代碼 + 投入金額 + 核心/衛星）
  → fetch_fund_by_key(full_key)                 fund_fetcher
  → assign_asset_role(fund_name)                app.py helper
  → calc_fund_factor_score(fund_data)           portfolio_engine
  → mk_fund_signal(fund_info, phase, score)     app.py helper
  → st.session_state.portfolio_funds 寫入 (list)
  → UI 渲染:
      ├─ 核心/衛星比例圓餅圖
      ├─ 再平衡差額計算
      ├─ risk_alert(drawdown, coverage, …)      portfolio_engine
      └─ 以息養股雙模式試算（🛒新購 / 📦現有持倉）
  → [可選] analyze_portfolio_correlation(…)     ai_engine → Gemini API
```

### 3.5 Tab4 — 回測
```
使用者選取基金 + 時間區間 + 權重
  → fetch_nav(full_key)                         fund_fetcher
  → backtest_portfolio(nav_df, weights)          backtest_engine
  → calc_performance_metrics(equity, returns)   backtest_engine
  → compare_with_benchmark(port_curve, bench)   backtest_engine
  → UI 渲染: 資產曲線 / 績效表 / 個別基金對比
```

### 3.6 Tab5 — 資料診斷（唯讀 Session State）
```
無網路呼叫，純讀取 session_state
  → st.session_state.indicators     → 14 指標健康燈號表
  → st.session_state.portfolio_funds → 基金逐筆診斷欄
  → FRED_KEY / GEMINI_KEY            → API Key 狀態卡
```

### 3.7 Tab6 — 說明書
```
靜態內容，無網路呼叫，無 session_state 讀寫
  → 8 子頁 Markdown 渲染（公式 / 判斷邏輯說明）
```

### 3.8 Session State 中央匯流
```
                   ┌─────────────────────────────┐
                   │      st.session_state        │
                   │  indicators      (dict)      │
                   │  phase_info      (dict)      │
                   │  macro_last_update (datetime)│
                   │  macro_ai        (str)       │
                   │  current_fund    (dict)      │
                   │  portfolio_funds (list[dict])│
                   │  news_items      (list)      │
                   │  systemic_risk_data (dict)   │
                   └─────────────────────────────┘
        ↑ 寫入               ↓ 讀取
  Tab1/Tab2/Tab3        Tab4/Tab5/AI 分析
```
