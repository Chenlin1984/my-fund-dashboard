# 專案戰情室 (Project State)
> _最後更新：2026-04-18_

## 📌 當前狀態
- **環境**: Streamlit Cloud + GitHub
- **進度**: ✅ V6.0 Pro 三件套 **完成並推送**
- **工作分支**: `claude/system-detox-upgrade-ra7Tp`（commit 9370775）
- **app.py**: 3141 行，6 tabs，AST OK
- **precision_engine.py**: 347 行

---

## ✅ V6.0 Pro（2026-04-18 完成）

| 步驟 | 內容 | Commit | 狀態 |
|------|------|--------|------|
| V6-1 | Tab1 L3 60/40 雙欄佈局 — `with _main_ctx:` + `_col_l3/_col_r3` | 9370775 | ✅ |
| V6-2 | L3 Z-Score 矩陣（14 指標 × Z-Score，\|Z\|≥2 = ⚠️ 歷史極端值） | 9370775 | ✅ |
| V6-3 | L3 情境判斷卡 A/B（PMI+薩姆 / ADL 觸發） | 9370775 | ✅ |
| V6-4 | L3 資本防線圖（go.Bar TR1Y vs 配息率，🔴本金侵蝕警示） | 9370775 | ✅ |

### V6.0 架構說明
- **60/40 佈局**: L3 時 `st.columns([3,2])` → `_main_ctx=_col_l3`（War Room+清單），`_col_r3`（Z-Score矩陣）
- **Z-Score 矩陣**: 14 指標 × (當前值, Z-Score, 狀態)；Z = (值-均值)/標準差；|Z|≥2 = 極端值
- **Situation A**: PMI<50 且 Sahm<0.5 → 庫存調整非衰退（黃色卡）
- **Situation B**: ADL < -2% → 極端乖離警報（紅色卡）
- **資本防線**: 有 portfolio_funds 時顯示，紅柱 = TR1Y < 配息率

---

## ✅ V5 Bug Fix + UX（2026-04-16 完成）

| 步驟 | 內容 | Commit | 狀態 |
|------|------|--------|------|
| Fix-1 | `classify_fetch_status()` v13.6 — complete 需 series≥10 + metrics | 3c0035e | ✅ |
| Fix-2 | Tab2 partial data view — 橘色診斷卡 + 已有資訊三欄顯示 | 3c0035e | ✅ |
| UX-1 | Tab2 境內/境外 radio button + `_build_moneydj_url()` helper | cdf707f | ✅ |

### UX-1 境內/境外切換設計
- Radio：`["🏠 境內", "🌐 境外"]` → `_t2_page_type = "yp010000" / "yp010001"`
- `_build_moneydj_url(raw_input, page_type)`：完整 URL 直通；純代碼按 page_type 組建
- 消除 TLZF9 等境外基金因 auto-detection 誤用 yp010000 導致 series=None 的問題

---

## ✅ V5 視覺化導航說明書 v3.0（2026-04-16 完成）

| 步驟 | 內容 | Commit | 狀態 |
|------|------|--------|------|
| V5-1 | macro_engine.py — SAHM（SAHMREALTIME）+ SLOOS（DRTSCILM）指標 | a761469 | ✅ |
| V5-2 | Tab1 War Room — Sahm/SLOOS/ADL 三 Gauge 圖 + 組合基金紅綠燈 + AI 每日一句 | a761469 | ✅ |
| V5-3 | Tab1 景氣循環羅盤 — Sahm+RSP/SPY Shadow+FedRate 多軸圖 | a761469 | ✅ |
| V5-4 | Tab2 面積圖 + 微觀防護盾 mini bar | a761469 | ✅ |
| V5-5 | Tab5 Data Guard — API 時戳表 + 資料筆數 bar + 0筆斷裂警示 | a761469 | ✅ |

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

---

## ✅ Core Protocol v3.0 AI Fund Coach（2026-04-16 完成）

| 步驟 | 內容 | Commit | 狀態 |
|------|------|--------|------|
| V3-1 | `ai_engine.py` — `analyze_fund_json` 改為 Markdown 輸出，四節教練結構 | aa0d55a | ✅ |
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
| `app.py` | 主程式（6 tabs：總經/單一基金/組合基金/回測/資料診斷/說明書） | 2854 |
| `precision_engine.py` | V4 精準策略引擎（複合風險溫度計 + 微觀防護盾） | 347 |
| `macro_engine.py` | 總經引擎（零快取版）+ SAHM + SLOOS | — |
| `fund_fetcher.py` | 基金抓取（零快取、零 ETF）+ classify_fetch_status v13.6 | — |
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
| 9370775 | feat(V6.0): Pro 三件套 — 60/40雙欄+Z-Score矩陣+情境卡+資本防線圖 |
| cdf707f | UX: Tab2 境內/境外 radio + _build_moneydj_url() |
| 3c0035e | Fix: classify_fetch_status v13.6 + partial data view |
| a761469 | V5: 視覺化導航 v3.0 全域導航塔 + Data Guard |
| eda4128 | V4: 精準策略引擎 |
| 26010c3 | V3-3: Tab2 三項升級 |
| aa0d55a | V3-2: 美林時鐘卡片 |
