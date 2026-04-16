# 專案戰情室 (Project State)
> _最後更新：2026-04-16_

## 📌 當前狀態
- **環境**: Streamlit Cloud + GitHub（branch: `main`）
- **進度**: ✅ Core Protocol v3.0 + V4 精準策略引擎 **全部完成**
- **工作分支**: `main`（所有 PR 已 merge）
- **app.py**: 2410 行，6 tabs，AST OK
- **precision_engine.py**: 240 行（新增）

---

## ✅ V4 精準策略引擎（2026-04-16 完成）

| 步驟 | 內容 | Commit | 狀態 |
|------|------|--------|------|
| V4 | `precision_engine.py` — 複合風險溫度計 + 微觀防護盾 | eda4128 | ✅ |

### 核心模組
- **`PrecisionStrategyEngine.calculate_composite_risk(df)`**
  - `Risk_Score = Z_VIX×0.3 + Z_HY×0.4 + Z_YC×0.3`
  - std=0 自動轉 NaN，df<20筆→中性值 0.0
- **`build_macro_df(indicators)`**: VIX(週)→月重採樣後與 HY_SPREAD/YIELD_10Y2Y 對齊
- **`risk_score_strategy(score)`**: 5級策略（>1.5現金50% / >0.8現金30% / ... / <-0.5現金5%）
- **`fetch_stock_three_ratios(name)`**: yfinance 季度財報，毛利率/營益率/淨利率 QoQ diff
- **`_resolve_ticker(name)`**: 台股4碼 → 中文名 → 英文名 → 純字母三層解析

### app.py 整合位置
- **Tab1**（宏觀風險溫度計 expander 末端）：漸層 gauge bar + 策略研判 + 三欄 metric delta
- **Tab2**（持股分析 expander 之後）：🛡️ 微觀防護盾 expander，按鈕觸發 + session_state 快取

---

## ✅ Core Protocol v3.0 AI Fund Coach（2026-04-16 完成）

| 步驟 | 內容 | Commit | 狀態 |
|------|------|--------|------|
| V3-1 | `ai_engine.py` — `analyze_fund_json` 改為 Markdown 輸出，四節教練結構 | aa0d55a(rebase) | ✅ |
| V3-2 | Tab1 美林時鐘老師語音卡片（4象限 + VIX>30 超跌警示） | aa0d55a | ✅ |
| V3-3 | Tab2 -2σ超跌機會卡 + Sharpe持久性 + TER費用率卡 | 26010c3 | ✅ |

---

## ✅ Core Protocol v2.0 視覺化升級（全部完成）

| 步驟 | 內容 | 狀態 |
|------|------|------|
| V2-0 | fund_fetcher.py 買賣點公式升級（年最低±σ / 年最高±σ） | ✅ |
| V2-1 | Tab2 Bollinger Bands（MA20±2σ）+ 配息標記💰 | ✅ |
| V2-2 | Tab3 真實收益長條圖（含息報酬 bar + 配息率紅虛線） | ✅ |
| V2-3 | Tab1 宏觀風險溫度計（Macro Score bar + 利差/VIX/PMI 多軸） | ✅ |
| V2-4 | Tab5 資料完整度熱力圖 + 三色燈號阻斷 AI 分析 | ✅ |
| V2-5 | Tab5 API 延遲趨勢圖（FRED/MoneyDJ/Yahoo 三源折線） | ✅ |
| V2-6 | Tab3 核心/衛星甜甜圈圖 + 偏移>10% 紅色閃爍警告 | ✅ |

---

## 🛠️ 檔案結構（最新）

| 檔案 | 說明 | 行數 |
|------|------|------|
| `app.py` | 主程式（6 tabs：總經/單一基金/組合基金/回測/資料診斷/說明書） | 2410 |
| `precision_engine.py` | V4 精準策略引擎（複合風險溫度計 + 微觀防護盾） | 240 |
| `macro_engine.py` | 總經引擎（零快取版） | — |
| `fund_fetcher.py` | 基金抓取（零快取、零 ETF） | — |
| `ai_engine.py` | Gemini AI 分析（v3.0 Markdown 輸出） | — |
| `portfolio_engine.py` | 組合評分引擎 | — |
| `backtest_engine.py` | 回測引擎 | — |
| `ARCHITECTURE.md` | 技術規格書 | — |

## 📐 設計約束（不可違反）
- **禁止**：`@st.cache_data`、ETF 相關模組、虛擬測試數值
- **圖表庫**：Plotly（不引入新依賴）
- **邊界防呆**：< 20筆 → N/A；API null → 警告不崩潰
- **每步驟**：僅動特定函式區塊，AST 驗證後 commit

## 📋 Commit 歷史（關鍵）
| Commit | 內容 |
|--------|------|
| eda4128 | V4 精準策略引擎 |
| 26010c3 | V3-3 Tab2 三項升級 |
| aa0d55a | V3-2 美林時鐘卡片 |
| 8401035 | Bug fix：proxy test URL + ACTI94/ACCP138/ACDD19 URL |
| 0f51847 | NAV cache auto-update |
