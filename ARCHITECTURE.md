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
