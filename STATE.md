# 專案戰情室 (Project State)
> _最後更新：2026-04-23_

## 📌 當前狀態
- **環境**: Streamlit Cloud + GitHub
- **進度**: ✅ V7.0 系統強化全數 **合併至 main**
- **工作分支**: `main`（直接推送，無待合併 PR）
- **app.py**: 3195 行，6 tabs，AST OK
- **precision_engine.py**: 347 行

---

## ✅ V7.0 系統強化（2026-04-23 完成）

| Commit | 內容 | 狀態 |
|--------|------|------|
| 25421f7 | Tab3 健康矩陣：背景色塊+⚠️標註+缺口數字+最小長條高度 | ✅ |
| a820521 | 配息覆蓋率講義卡 TypeError fix — `_adr>0` 防護 | ✅ |
| 83bcb1a | 移除 `fetch_all_indicators.clear()` AttributeError | ✅ |
| 8350da8 | 移除所有暫存沙盒（@st.cache_data / 本地檔 / GitHub Actions 快取） | ✅ |
| c1b581e | Proxy fallback 四點強化（TTL/reset/403計數/sleep精準化） | ✅ |
| 51bea84 | `buy_basis` NameError fix + 境內/境外 auto-detect | ✅ |
| 096f604 | urllib3 Retry + `_check_secrets()` + 配息覆蓋率講義卡 | ✅ |
| 89c55c6 | 固定視角等級 L3（移除 L1/L2 選項） | ✅ |

### V7.0 架構重點
- **零快取架構**：移除 `@st.cache_data`，每次按鈕即時抓取 FRED
- **無沙盒**：刪除 `cache/nav/`、`cache/div/`、`cache/meta/` 本地檔案快取
- **Proxy TTL 300s**：`_PROXY_CFG_CACHE` 自動過期重讀，NAS 恢復後無需重啟
- **境內/境外自動偵測**：`_auto_fetch_moneydj()` 境內優先，失敗自動試境外
- **吃本金視覺化**：0% 長條最小高度 + 紅色背景色塊 + `⚠️ 吃本金 缺口 X.X%` 標註


## ✅ V5.0 Master Edition（2026-04-17 完成）

| 步驟 | 內容 | Commit | 狀態 |
|------|------|--------|------|
| Ch1 | `@st.cache_data(ttl=86400)` on `fetch_all_indicators` + `DataValidationError` | fb2b761 | ✅ |
| Ch2 | Tab1 `view_mode` Radio L1/L2/L3 漸進式儀表板 | fb2b761 | ✅ |
| Ch4 | `analyze_fund_json` 動態 Prompt tone 注入 | fb2b761 | ✅ |
| UX | Tab2 境內/境外 Radio + `_build_moneydj_url()` | cdf707f | ✅ |
| Fix | `classify_fetch_status` v13.6 + partial data view | 3c0035e | ✅ |

### 漸進式儀表板層級
| 等級 | 顯示內容 | Prompt 語氣 |
|------|---------|------------|
| 🟢 L1 新手導航 | Gauge × 3 + AI 一句 + Checkbox 待辦清單 | 白話文、天氣比喻、禁 Z-Score |
| 🟡 L2 學徒覆盤 | L1 + 歷史雙 Y 軸圖（2008/2020/2022 紅區） + 景氣時鐘/風險警示/美林時鐘 | 因果邏輯、歷史印證 |
| 🔴 L3 老手沙盤 | L2 + 宏觀溫度計 / 景氣循環羅盤 / Z-Score 明細 / AI 結構化摘要 | 量化、Z-Score、乖離率 |

### Chapter 1 資料流實體鎖
- `macro_engine.py`: `@st.cache_data(ttl=86400)` — 冷資料只在首次或按鈕刷新時呼叫 FRED API
- `fund_fetcher.py`: `DataValidationError` exception class — len < 20 觸發，阻斷後續 AI 推論
- `app.py`: 按鈕強制刷新時 `fetch_all_indicators.clear()`

### Chapter 4 AI 動態 Prompt
- `ai_engine.py::analyze_fund_json(view_mode)`: 依等級注入 `_tone_directive`
- Tab2 AI 呼叫自動讀取 `st.session_state["view_mode"]` 傳入

---

## ✅ V5 視覺化導航說明書 v3.0（2026-04-16 完成）

| 步驟 | 內容 | Commit | 狀態 |
|------|------|--------|------|
| V5-1 | macro_engine.py — SAHM + SLOOS 指標 | a761469 | ✅ |
| V5-2 | Tab1 War Room — Sahm/SLOOS/ADL 三 Gauge + 組合紅綠燈 + AI 每日一句 | a761469 | ✅ |
| V5-3 | Tab1 景氣循環羅盤 — Sahm+RSP/SPY Shadow+FedRate 多軸圖 | a761469 | ✅ |
| V5-4 | Tab2 面積圖 + 微觀防護盾 mini bar | a761469 | ✅ |
| V5-5 | Tab5 Data Guard — API 時戳表 + 資料筆數 bar + 0筆斷裂警示 | a761469 | ✅ |

---

## ✅ V4 精準策略引擎（2026-04-16 完成）

| 步驟 | 內容 | Commit | 狀態 |
|------|------|--------|------|
| V4 | `precision_engine.py` — 複合風險溫度計 + 微觀防護盾 | eda4128 | ✅ |

### 核心模組
- `Risk_Score = Z_VIX×0.3 + Z_HY×0.4 + Z_YC×0.3`
- `risk_score_strategy(score)`: 5 級策略
- `fetch_stock_three_ratios(name)`: yfinance 季度財報三率 QoQ diff
- `_resolve_ticker(name)`: 台股 4 碼 → 中文名 → 英文名三層解析

---

## ✅ Core Protocol v3.0 AI Fund Coach（2026-04-16 完成）

| 步驟 | 內容 | Commit | 狀態 |
|------|------|--------|------|
| V3-1 | `ai_engine.py` — `analyze_fund_json` 四節教練結構 | aa0d55a | ✅ |
| V3-2 | Tab1 美林時鐘老師語音卡片（4象限 + VIX>30 超跌警示） | aa0d55a | ✅ |
| V3-3 | Tab2 -2σ超跌機會卡 + Sharpe持久性 + TER費用率卡 | 26010c3 | ✅ |

---

## 🛠️ 檔案結構（最新）

| 檔案 | 說明 | 行數 |
|------|------|------|
| `app.py` | 主程式（6 tabs：總經/單一基金/組合基金/回測/資料診斷/說明書） | 3141 |
| `precision_engine.py` | V4 精準策略引擎（複合風險溫度計 + 微觀防護盾） | 347 |
| `macro_engine.py` | 總經引擎（@st.cache_data v18.0）+ SAHM + SLOOS | — |
| `fund_fetcher.py` | 基金抓取（v6.23）+ DataValidationError + classify_fetch_status v13.6 | — |
| `ai_engine.py` | Gemini AI 分析（v3.0 + V5.0 動態 Prompt） | — |
| `portfolio_engine.py` | 組合評分引擎 | — |
| `backtest_engine.py` | 回測引擎 | — |

## 📐 設計約束（V5.0 更新）
- **允許**：`@st.cache_data(ttl=86400)` — 冷資料每日更新（V5.0 覆蓋舊約束）
- **禁止**：ETF 相關模組、虛擬測試數值
- **圖表庫**：Plotly（不引入新依賴）
- **邊界防呆**：< 20筆 → DataValidationError；API null → 警告不崩潰

## 📋 Commit 歷史（關鍵）
| Commit | 內容 |
|--------|------|
| 9370775 | feat(V6.0): Pro 三件套 — 60/40雙欄+Z-Score矩陣+情境卡+資本防線圖 |
| 82c9f63 | Merge PR #38 — V5.0 Master Edition |
| fb2b761 | feat(V5.0): Core Protocol Master Edition |
| cdf707f | feat: Tab2 境內/境外 Radio |
| 3c0035e | fix: classify_fetch_status v13.6 |
| a761469 | feat(V5): 視覺化導航 v3.0 |
| eda4128 | feat(V4): 精準策略引擎 |
| 26010c3 | V3-3: Tab2 三項升級 |
| aa0d55a | V3-2: 美林時鐘卡片 |
