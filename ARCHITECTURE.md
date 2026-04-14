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

## §4 核心函式 I/O 定義 — macro_engine & fund_fetcher

### 4.1 macro_engine.py

| 函式 | 輸入 | 輸出 | 說明 |
|------|------|------|------|
| `fetch_all_indicators(fred_api_key)` | `str` | `dict[str, dict]` | 抓取 14 項總經指標（FRED + yfinance），每項含 `value/signal/score/series` |
| `calc_macro_phase(indicators)` | `dict` | `dict` | 加權評分 → `{score:0~10, phase, rec_prob, phase_label, alloc}` |
| `identify_regime(indicators)` | `dict` | `dict` | 四象限景氣循環辨識 → `{regime, description, quadrant, pmi_dir, cpi_dir}` |
| `get_market_phase(indicators)` | `dict` | `dict` | Z-Score 二維位階判定 → `{phase, direction, confidence, signals}` |
| `calc_growth_inflation_axis(indicators)` | `dict` | `dict` | 成長/通膨雙軸 → `{growth_score, inflation_score, quadrant, label}` |
| `recession_probability(spread_10y3m)` | `float \| None` | `float \| None` | Logistic 回歸估算衰退機率（0~1） |
| `fetch_tw_market_tpi(fred_api_key)` | `str` | `dict` | 台股三因子 TPI → `{tpi, breadth_z, fii_z, m1b_z, signal, temp_label}` |
| `detect_systemic_risk(news_items)` | `list[dict]` | `dict` | 新聞關鍵字掃描 → `{level, score, triggers:list, summary}` |

**`calc_macro_phase` 輸出結構：**
```
{
  score:       float,        # 0~10，越高越繁榮
  phase:       str,          # "高峰"|"擴張"|"復甦"|"衰退"
  rec_prob:    float,        # 衰退機率 %
  phase_label: str,          # 含 emoji 的顯示標籤
  alloc:       dict,         # {stock, bond, cash} 建議配置 %
  breakdown:   dict,         # 各指標得分明細
}
```

**`fetch_all_indicators` 每項指標結構：**
```
{
  name:   str,     # 中文名稱
  value:  float,   # 最新值
  prev:   float,   # 上期值
  unit:   str,     # 單位（%、點等）
  signal: str,     # "🟢"|"🔴"|"🟡"
  score:  float,   # 加權得分貢獻
  weight: float,   # 該指標權重
  trend:  str,     # "up"|"down"|"flat"
  date:   str,     # 最新資料日期 YYYY-MM
  series: pd.Series,  # 歷史序列（供圖表用）
}
```

---

### 4.2 fund_fetcher.py

| 函式 | 輸入 | 輸出 | 說明 |
|------|------|------|------|
| `parse_moneydj_input(user_input)` | `str` | `dict` | 解析 URL/代碼 → `{code, page_type, full_url, portal}` |
| `fetch_fund_from_moneydj_url(url)` | `str` | `dict` | 主要入口：從 URL 抓完整基金資料（含 NAV/績效/配息/持股） |
| `fetch_fund_by_key(full_key, …)` | `str` | `dict` | 從 full_key 取完整分析資料，供組合基金 Tab 使用 |
| `calc_metrics(s, divs, risk_override)` | `pd.Series, list, dict\|None` | `dict` | 計算 MK 買點（買1/買2/買3）、標準差、年化配息率 |
| `fetch_performance_wb01(code)` | `str` | `dict` | MoneyDJ wb01 含息報酬率 → `{1Y, 3Y, 5Y, YTD}` (%) |
| `fetch_risk_metrics(code)` | `str` | `dict` | MoneyDJ wb07 績效評比 → `{risk_table: {一年:{Sharpe, 標準差, …}}}` |
| `fetch_holdings(code)` | `str` | `dict` | MoneyDJ 持股頁 → `{sector_alloc:list, top_holdings:list}` |
| `tdcc_search_fund(keyword)` | `str` | `list[dict]` | TDCC 搜尋境外基金 → `[{code, name, agent, isin}]` |
| `search_moneydj_by_name(keyword)` | `str` | `list[dict]` | MoneyDJ 模糊搜尋 → `[{code, name, url, portal}]` |
| `fetch_market_news(max_per_feed)` | `int=5` | `list[dict]` | RSS 財經新聞 → `[{title, summary, source, published, url}]` |

**`fetch_fund_from_moneydj_url` / `fetch_fund_by_key` 輸出結構：**
```
{
  fund_name:        str,
  fund_code:        str,
  full_key:         str,        # portal:code
  nav:              float,      # 最新淨值
  series:           pd.Series,  # 每日/月淨值序列，index=datetime
  dividends:        list[dict], # [{date, amount, type}]
  perf:             dict,       # {1Y, 3Y, 5Y, YTD} 含息報酬率 %
  risk_metrics:     dict,       # wb07 Sharpe/標準差/MaxDD
  holdings:         dict,       # sector_alloc + top_holdings
  metrics:          dict,       # calc_metrics() 結果（買點/配息率）
  investment_target:str,        # 投資標的說明
  error:            str|None,   # 錯誤訊息
}
```

**`calc_metrics` 輸出結構：**
```
{
  nav:           float,   # 最新淨值
  std_1y:        float,   # 1年年化標準差 %
  buy1/buy2/buy3:float,   # -1σ/-2σ/-3σ 買點淨值
  sell1:         float,   # +1σ 停利點
  pos_label:     str,     # "超跌區"|"合理區"|"高估區"
  annual_div_rate:float,  # 年化配息率 %
  ret_1y:        float,   # 1年含息報酬率 %（優先 wb01）
  std_source:    str,     # "wb07"|"2年計算"|"1年計算"
}
```

---

## §5 核心函式 I/O 定義 — ai_engine, portfolio_engine, backtest_engine

### 5.1 ai_engine.py

| 函式 | 輸入 | 輸出 | 說明 |
|------|------|------|------|
| `_build_snapshot(indicators, phase_info, portfolio_funds, focus_fund, news_headlines)` | `dict, dict, list, dict, list` | `str` | 壓縮所有數據為純文字快照（目標 < 800 tokens），供 Gemini prompt 使用 |
| `analyze_macro_structured(api_key, indicators, phase_info, news_items, systemic_risk, max_tokens)` | `str, dict, dict, list, dict, int` | `str` | MetaPrompt v18.2 四段結構輸出（景氣判讀/配置建議/持倉警示/待辦清單） |
| `analyze_fund_json(api_key, fund_name, metrics, perf_data, phase_info, risk_metrics, holdings, currency)` | `str, str, dict, dict, dict, dict, dict, str` | `str` | 精簡 JSON 摘要（< 300 tokens 輸入），單一基金 AI 分析 |
| `analyze_portfolio_correlation(api_key, funds_list, phase_info, data_text)` | `str, list, dict, str` | `str` | 組合相關性 + 配置建議，呼叫 analyze_global |
| `analyze_macro(api_key, indicators, phase_info, …)` | `str, dict, dict` | `str` | 薄包裝，轉呼叫 `analyze_global` |

**`analyze_macro_structured` 輸出格式（Markdown）：**
```
### 📍 一、景氣位階判讀
### ⚖️ 二、資產配置建議
### 🔴 三、持倉警示
### 🔄 四、本週操作待辦清單
- [ ] 待辦項目（checkbox 格式）
```

---

### 5.2 portfolio_engine.py

| 函式 | 輸入 | 輸出 | 說明 |
|------|------|------|------|
| `calc_fund_factor_score(fund_data, risk_table, expense_ratio)` | `dict, dict\|None, float\|None` | `dict` | 六因子加權評分（0~100），回傳 grade A/B/C/D |
| `dividend_safety(total_return, dividend_yield, nav_change)` | `float\|None, float, float\|None` | `dict` | 吃本金診斷，回傳燈號與覆蓋率 |
| `optimize_portfolio(returns_df, rf, max_weight, min_weight)` | `pd.DataFrame, float, float, float` | `dict` | Scipy SLSQP 最大化 Sharpe，回傳最佳權重 |
| `risk_alert(drawdown, coverage, regime, fed_direction, hy_spread, vix)` | 各項 `float\|None` | `list[dict]` | 即時風險預警，回傳警示清單（含 level/type/message） |
| `calc_kelly(series, lookback, risk_free)` | `pd.Series, int, float` | `dict` | 凱利公式計算最佳投入比例 |

**`calc_fund_factor_score` 輸出結構：**
```
{
  score:         float,        # 0~100
  grade:         str,          # "A"|"B"|"C"|"D"
  factors_count: int,          # 實際計入因子數
  factors: {
    Sharpe:      {value, score, weight},
    Sortino:     {value, score, weight},
    MaxDrawdown: {value, score, weight},
    Calmar:      {value, score, weight},
    Alpha:       {value, score, weight},
    ExpenseRatio:{value, score, weight},
  }
}
```

**`dividend_safety` 輸出結構：**
```
{
  status:          str,    # "🟢 健康"|"🟡 邊緣"|"🔴 吃本金警示"|"🔴 嚴重吃本金"
  alert_level:     str,    # "green"|"yellow"|"red"
  coverage:        float,  # total_return / dividend_yield
  eating_principal:bool,
  message:         str,
  nav_warning:     str|None,
}
```

---

### 5.3 backtest_engine.py

| 函式 | 輸入 | 輸出 | 說明 |
|------|------|------|------|
| `backtest_portfolio(nav_df, weights, rebalance)` | `pd.DataFrame, pd.Series, str` | `pd.DataFrame` | 定期再平衡回測，回傳每日資產曲線 DataFrame |
| `calc_performance_metrics(equity_curve, returns, rf, freq)` | `pd.Series, pd.Series, float, int` | `dict` | 計算 Sharpe/Sortino/MaxDD/Calmar/年化報酬/年化波動 |
| `compare_with_benchmark(port_curve, bench_curve)` | `pd.Series, pd.Series` | `dict` | 超額報酬 / Tracking Error / Information Ratio |
| `quick_backtest(nav_series, freq)` | `pd.Series, int` | `dict` | 單一基金快速回測，直接回傳績效指標 dict |

**`calc_performance_metrics` 輸出結構：**
```
{
  total_return:  float,  # 累積總報酬 %
  ann_return:    float,  # 年化報酬 %
  ann_vol:       float,  # 年化波動 %
  sharpe:        float,
  sortino:       float,
  max_drawdown:  float,  # 最大回撤 %（負值）
  calmar:        float,
  periods:       int,    # 計算期數
}
```

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

---

## §6 Session State Schema

| Key | 型別 | 預設值 | 寫入方 | 讀取方 |
|-----|------|--------|--------|--------|
| `macro_done` | `bool` | `False` | Tab1 載入後設 `True` | Tab1 防重複呼叫 |
| `indicators` | `dict[str, dict]` | `{}` | Tab1 `fetch_all_indicators` | Tab2/Tab3/Tab5/AI |
| `phase_info` | `dict` | `{}` | Tab1 `calc_macro_phase` | Tab2/Tab3/AI |
| `macro_last_update` | `datetime\|None` | `None` | Tab1 完成時 | Sidebar 顯示時間 |
| `macro_ai` | `str` | `""` | Tab1 AI 按鈕 | Tab1 渲染 Markdown |
| `prev_phase` | `str` | `""` | Tab1 | Tab1 位階變化偵測 |
| `phase_history` | `list[str]` | `[]` | Tab1 | Tab1 歷史記錄 |
| `current_fund` | `dict\|None` | `None` | Tab2 基金載入 | Tab2 渲染 / Tab5 診斷 |
| `fund_data` | `dict\|None` | `None` | Tab2（備用） | Tab2 |
| `tdcc_results` | `list[dict]` | `[]` | Tab2 TDCC 搜尋 | Tab2 選單 |
| `mj_fund_data` | `dict\|None` | `None` | Tab2 MoneyDJ 搜尋 | Tab2 |
| `portfolio_funds` | `list[dict]` | `[]` | Tab3 加入基金 | Tab3 渲染 / Tab5 診斷 |
| `portfolio_core_pct` | `int` | `75` | Tab3 滑桿 | Tab3 再平衡計算 |
| `news_items` | `list[dict]` | `[]` | Tab1 RSS | Tab1 / `detect_systemic_risk` |
| `systemic_risk_data` | `dict\|None` | `None` | Tab1 風險掃描 | Tab1 警示卡 |

**`portfolio_funds` 每筆元素結構：**
```
{
  code:       str,     # 基金代碼
  name:       str,     # 基金名稱
  full_key:   str,     # portal:code
  is_core:    bool,    # True=核心, False=衛星
  currency:   str,     # "USD"|"TWD"
  invest_amt: float,   # 投入金額（NTD）
  weight:     float,   # 目標權重 0~1
  loaded:     bool,    # 是否已抓取資料
  metrics:    dict,    # calc_metrics() 結果
  moneydj_raw:dict,    # fetch_fund_from_moneydj_url() 結果
  dividends:  list,    # 配息記錄
  series:     pd.Series|None,
  error:      str|None,
}
```

---

## §7 外部服務依賴

### 7.1 API 服務

| 服務 | 用途 | Key 位置 | 免費限制 |
|------|------|---------|---------|
| **FRED API** | 14 項總經指標 | `secrets.toml: FRED_API_KEY` | 120 req/min |
| **Gemini API** | AI 分析文字生成 | `secrets.toml: GEMINI_API_KEY` | 免費 tier 有 RPM 限制 |
| **NAS Proxy** | 穿透抓取 MoneyDJ/TDCC | `secrets.toml: PROXY_URL` | 自建，無限制 |

### 7.2 資料來源（爬取，免 Token）

| 來源 | 資料類型 | 須 Proxy |
|------|---------|---------|
| MoneyDJ wb01/wb05/wb07 | 含息報酬率 / 配息率 / 風險評比 | ✅ |
| MoneyDJ NAV 頁 | 每日淨值歷史 | ✅ |
| TDCC openapi | 境外基金搜尋 / 代理機構 | ✅ |
| yfinance | VIX / DXY / ADL / COPPER / 美股 | ❌ |
| Fundclear | 境內基金 NAV / 配息 | ❌ |
| cnyes 鉅亨 | 備援 NAV / 配息 | ❌ |
| Morningstar | 備援 NAV / 元數據 | ❌ |
| RSS（Reuters/Bloomberg/WSJ）| 財經新聞標題 | ❌ |

### 7.3 Python 套件（requirements.txt）

| 套件 | 版本約束 | 用途 |
|------|---------|------|
| `streamlit` | ==1.45.1 | UI 框架 |
| `pandas` | >=2.0.0 | 資料處理 |
| `numpy` | >=1.24.0 | 數值運算 |
| `plotly` | >=5.18.0 | 互動圖表 |
| `yfinance` | >=0.2.36 | 美股行情 |
| `google-generativeai` | >=0.5.0 | Gemini API |
| `requests` | >=2.31.0 | HTTP 爬取 |
| `beautifulsoup4` | >=4.12.0 | HTML 解析 |
| `lxml` / `html5lib` | >=4.9 / >=1.1 | BS4 解析器 |
| `feedparser` | >=6.0.8 | RSS 解析 |
| `scipy` | >=1.11.0 | 投資組合最佳化（SLSQP）|
| `nest-asyncio` | >=1.6.0 | Colab 環境 asyncio 兼容 |

### 7.4 Streamlit Secrets 必填欄位

```toml
# .streamlit/secrets.toml
FRED_API_KEY   = "..."   # 必填，否則總經指標全部失敗
GEMINI_API_KEY = "..."   # 必填，否則 AI 分析無法使用
PROXY_URL      = "http://user:pass@host:port"  # 必填，否則 MoneyDJ 資料無法抓取
```
