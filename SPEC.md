# 國內外基金與 ETF AI 儀表板系統 — 終極版開發規格書
> **版本**: v16.0 Merged | **最後更新**: 2026-03-30
> 本文件由原始需求說明書與現行 v16.0 程式碼實際審核後合併，以現行程式碼為準，修正舊版錯誤並補充更先進的架構描述。

---

## §0 系統概述與 AI 開發者守則

本系統為專注於「國內外共同基金與 ETF」的專業量化 AI 儀表板，**嚴格排除單一個股與非基金標的**。

### ⚠️ AI 開發者強制遵守事項

1. **資料對齊 (Data Alignment)**：遇到台美股休市或頻率落差，全面採用 `df.ffill()`（向後填補最新可用數據），**嚴禁使用 `dropna()` 刪除列**導致資料斷層。

2. **拒絕黑箱與虛擬資料**：所有 DataFrame 必須連接外部 API 實時產生，發生網路錯誤時必須有 `try-except` 報錯機制並寫入 `AI_Error_Ledger.md`。

3. **效能與防封鎖 (Rate Limit)**：
   - 所有拉取外部資料的函數必須加上 `@st.cache_data(ttl=3600)` 進行快取。
   - MoneyDJ 系列請求：必須加入 `time.sleep` 與 `User-Agent` 偽裝，使用 `fetch_url_with_retry()` 統一入口。
   - ⚠️ **Streamlit Cloud IP 封鎖備案**：若 MoneyDJ 封鎖，需提示使用者改在 Local 端執行或自備 Proxy。

4. **自我審核機制 (Ironclad Self-Audit)**：每次撰寫完功能後，輸出：
   - **[邏輯]**：是否符合需求，無邏輯斷層。
   - **[邊界]**：空值、極大值、異常輸入（如輸入股票代碼而非基金）3 個測試場景。
   - **[效能]**：時間/空間複雜度與優化點。
   - **[Debug]**：發現 Bug 直接修正並加註解。

5. **UI 數據同步**：涉及數據更新的 UI 操作，必須具備 `st.cache_data.clear()` 強制清除緩存機制。

---

## §1 數據來源對照表（已驗證版）

| 數據類別 | 具體指標 | FRED Series ID / API Endpoint | 抓取套件/函數 |
| :--- | :--- | :--- | :--- |
| **總經領先** | ISM 製造業 PMI | `NAPM`（主）/ `ISPMANPMI`（備） | `fredapi` → `_fred()` |
| **總經領先** | 10Y-2Y 公債利差 | `DGS10` - `DGS2` | `fredapi` → `_fred()` |
| **總經領先** | 10Y-3M 公債利差 | `DGS10` - `TB3MS` | `fredapi` → `_fred()` |
| **總經領先** | HY 信用利差 (OAS) | `BAMLH0A0HYM2` | `fredapi` → `_fred()` |
| **總經同步** | 市場廣度 (ADL) | SPY vs RSP 比值 | `yfinance` → `_yf_s()` |
| **總經同步** | 美元指數 (DXY) | `DX-Y.NYB` | `yfinance` → `_yf_s()` |
| **總經同步** | 恐慌指數 (VIX) | `^VIX` | `yfinance` → `_yf_s()` |
| **總經落後** | CPI 通膨年增率 | `CPIAUCSL` | `fredapi` → `_fred()` |
| **總經落後** | 聯準會基準利率 | `FEDFUNDS` | `fredapi` → `_fred()` |
| **總經落後** | M2 貨幣供給年增率 | `M2SL` | `fredapi` → `_fred()` |
| **ETF/指數** | ETF 淨值/市價 | Yahoo Finance (`^GSPC`, `0050.TW` 等) | `yfinance` |
| **國內基金** | NAV / 績效 / 風險 | `tcbbankfund.moneydj.com` | `requests` + `bs4` → `fetch_url_with_retry()` |
| **國內基金** | 配息記錄 / 搜尋 | FundClear API (`fundclear.com.tw`) | `requests` → `_src_fundclear_*()` |

> ⚠️ 舊版規格 `ISM/MAN_PMI` 為錯誤 endpoint，`yp011000.djhtm` 為舊版 MoneyDJ URL，均已廢棄。

---

## §2 Tab 1：總體經濟位階與拐點判定 (Macro Dashboard)

### 核心演算法：拐點偵測與景氣位階評分

系統透過 `macro_engine.fetch_all_indicators()` 拉取 10 項指標並計算綜合分數（`infl_score`）：

**指標評分與加權（共 10 組）：**
| 指標 | 加權 | 綠燈條件 | 紅燈條件 |
|------|------|---------|---------|
| PMI | 2 | > 50 且上升 | < 50 且下降 |
| 10Y-2Y 利差 | 2 | > 0.5% | < 0（倒掛） |
| 10Y-3M 利差 | 2 | > 0.5% | < 0（最強衰退訊號） |
| HY 信用利差 | 2 | < 4% | > 6% |
| CPI YoY | 1 | 下降趨勢 | 持續上升 |
| FEDFUNDS | 1 | 降息循環 | 升息 > 5% |
| M2 YoY | 1 | > 5% | < 0% |
| ADL（廣度） | 1 | RSP/SPY 上升 | RSP/SPY 下降 |
| DXY | 1 | 弱勢 | 強勢突破 |
| VIX | 1 | < 20 | > 30 |

**景氣位階判定閾值：**
- `infl_score ≥ 8`：🟢 強烈買進訊號（切入復甦）
- `infl_score ≥ 4`：🟡 觀察期
- `infl_score ≥ -2`：🟠 謹慎
- `infl_score ≥ -5`：🔴 空倉
- `infl_score < -5`：💀 衰退警戒

### 美林時鐘四大階段資產建議

| 階段 | 條件 | 建議配置 |
|------|------|---------|
| 🌸 **復甦期** | PMI > 50 ↑，CPI ↓ | 股票型 70%（科技/半導體）｜投資等級債 30% |
| 🌞 **過熱期** | PMI > 50 ↑，CPI ↑ | 股票型 50%（金融/抗通膨）｜原物料 ETF 30%｜債 20% |
| 🍂 **滯脹期** | PMI < 50 ↓，CPI ↑ | 貨幣市場/現金 40%｜防禦型債券 40%｜防禦股 20% |
| ❄️ **衰退期** | PMI < 50 ↓，CPI ↓ | 美國公債/投等債 60%｜現金 20%｜低波高息股 20% |

---

## §3 Tab 2：單一標的深度診斷與四大師策略 (Fund Deep Dive)

所有運算必須使用 `Adj Close`（還原權息價）。

### 大師策略實作規格

**1. 郭俊宏：以息養股避雷策略**
- 計算近一年「含息總報酬率」與「現金殖利率」。
- `dividend_safety()` 函數：若 `含息總報酬率 < 現金殖利率` → 🔴 強制警示：「⚠️ 賺股息賠價差，侵蝕本金中，不宜作為核心資產」。
- 資料來源：MoneyDJ `tcbbankfund` 配息記錄 + FundClear API。

**2. 孫慶龍：7% 存股聖經（殖利率燈號）**
- 計算近 5 年平均現金殖利率。
- 燈號閾值：
  - `殖利率 ≥ 7%` → 🟢 強烈買進（特價）
  - `殖利率 5~7%` → 🟡 適度減碼（合理）
  - `殖利率 ≤ 3%` → 🔴 獲利了結（昂貴）

**3. 春哥（Stan Weinstein）：VCP 波幅收縮與均線保護**
- 計算 50MA 與 200MA。計算近 5 週每週波幅。
- **買進條件（同時滿足）**：
  1. 股價 > 50MA **且** > 200MA
  2. 週波幅呈現遞減：20% → 10% → 5%（最後一週收縮 ≥ 50%）
  3. 當日成交量 > 50 日均量
- 發出「✅ VCP 突破買訊」，並自動設定 **8% 停損線**（入場價 × 0.92）。

**4. 財經 M 平方：總經循環位階調控（防禦機制）**
- 串接 §2 景氣位階：若當下為「衰退期（infl_score < -5）」，自動隱藏科技/成長型 ETF 買進訊號，提示「⚠️ 總經下行，請轉向高息低波或債券資產」。

**ETF 專屬防呆：折溢價率警示**
- 公式：`折溢價率 = (市價 - NAV) / NAV × 100%`
- 若 `> 1%` → 🔴 跳出警示「溢價過高，拒絕追高」
- 若 `< -1%` → 🟢 「折價買入機會」

---

## §4 Tab 3：多檔組合配置與壓力測試 (Portfolio)

### 核心演算法

**六因子評分模型** (`calc_fund_factor_score()` in `portfolio_engine.py`)：
| 因子 | 加權 |
|------|------|
| Sharpe Ratio | 25% |
| Sortino Ratio | 15% |
| Max Drawdown | 20% |
| Calmar Ratio | 15% |
| Alpha | 15% |
| 費用率 (Expense Ratio) | 10% |

**相關係數矩陣**：
- Pearson Correlation 計算近 1 年各標的報酬率。
- 若任兩檔相關係數 `> 0.85` → 🔴 標示「資產同質性過高，無法有效分散風險」。

**最大回撤與壓力測試**：
- `MDD = (谷底價值 - 峰值價值) / 峰值價值`
- **股災模擬器**（S&P 500 下跌 -20% 情境）：
  - `預估虧損 = Σ(權重_i × Beta_i × -20%)`
  - 若總預估虧損 `> -20%` → 強制建議增加公債/現金比例。

**Core / Satellite 分類系統** (`assign_asset_role()`)：
- Core 關鍵字：「配息」「高股息」「投資等級債」「基建」「特別股」「債」「收益」「平衡」等
- Satellite 關鍵字：「科技」「AI」「半導體」「生技」「電動車」「成長」等
- 支援 `manual_override` 強制覆蓋分類

---

## §5 Tab 4：歷史回測與績效視覺化 (Backtesting)

函數入口：`backtest_engine.py`

### 核心指標計算

| 指標 | 公式 | 備註 |
|------|------|------|
| **年化報酬率 (CAGR)** | `(最終價值/初始價值)^(1/年數) - 1` | |
| **夏普值 (Sharpe)** | `(R_p - R_f) / σ_p` | ⚠️ `R_f` 目前硬寫 2%，待修正為即時 FRED FEDFUNDS |
| **Sortino Ratio** | `(R_p - R_f) / σ_downside` | 僅計算下行波動 |
| **Calmar Ratio** | `年化報酬率 / abs(MDD)` | |
| **最大回撤 (MDD)** | `(谷底 - 峰值) / 峰值` | |

**Benchmark 對比**：`compare_with_benchmark()` 計算超額報酬、追蹤誤差、資訊比率。

**再平衡選項**：`"ME"`（月）/ `"QE"`（季）/ `None`（買入持有）。

**動態資金成長曲線**：Plotly 繪製初始 10,000 元累積報酬率，對比 `^GSPC` 或 `0050.TW` 大盤基準。

---

## §6 Tab 5：AI 智能總結 (analyze_global 架構)

### AI 模型規格
- **模型**：Gemini 2.5 Flash
- **Endpoint**：`https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent`
- **thinkingBudget**：`0`（關閉內部思考，全力輸出報告）
- **重試機制**：最多 2 次重試，429 等待 20×(n+1) 秒

### 輸入快照架構（< 800 tokens）
所有 AI 分析統一透過 `analyze_global()` 入口，輸入為文字快照，**嚴禁傳入原始歷史數組**：
```
[總經位階] phase / score / trend / recession_rate
[最新新聞] 最多 3 則標題
[投資組合] 每基金一行：名稱 | 配息% TR1Y% 吃本金check | σ Sharpe DD NAV位置
[個別基金] NAV、波動度、配息率、總報酬、吃本金檢查
[風險預警] 僅紅色警示
```

### 強制輸出格式（4 大章節，缺一不可）
```markdown
### 📍 一、景氣位階判讀
（3 個關鍵指標 + 拐點觸發條件）

### ⚖️ 二、資產配置建議
（現況 vs 目標比例 + 調整幅度）

### 🔴 三、持倉警示
（逐基金：名稱 → 🔴/🟡/🟢 + 原因，標示吃本金與 NAV 位置）

### 🔄 四、本週操作待辦清單
- [ ] 哪檔需減碼
- [ ] 等待哪個 -1σ 買點
- [ ] 吃本金問題處理
- [ ] 定期定額是否繼續
```

### LLM 提示工程約束（寫死於程式碼）
1. 逐項評斷，拒絕空泛問候語
2. 每節不超過 300 字，條列式輸出
3. 套用四大師視角：春哥 VCP / 郭俊宏以息養股 / 孫慶龍 7% 估值 / M 平方總經循環
4. **禁止幻覺**：只能依據傳入的 Python 運算數值解讀，嚴禁捏造財報或未發生的市場新聞

### 錯誤自動診斷（AI Error Ledger）
任何例外觸發 `_write_error_ledger()` → Gemini 反思 → 寫入 `AI_Error_Ledger.md`，記錄根本原因 + 防範規則 + 快速修法。

---

## §7 待補實作功能清單 (Backlog)

以下功能已在規格中定義，尚需確認或補充實作：

| 優先度 | 功能 | 說明 | 狀態 |
|--------|------|------|------|
| ✅ 已修 | Sharpe rf 動態化 | `fund_fetcher.py` `_RF_ANNUAL` 模組變數 + `set_risk_free_rate()` 注入；`app.py` 在 `fetch_all_indicators()` 後呼叫 | 已完成 |
| ✅ 已修 | ETF 折溢價警示 | `fetch_etf_market_price(ticker)` via yfinance；`app.py` `_render_fund_analysis()` 顯示 🔴/🟢 | 已完成 |
| ✅ 已修 | 春哥 VCP 訊號 | `calc_vcp_signal(ticker)`：50MA/200MA + 5週波幅遞減 + 成交量；僅 ETF 觸發 | 已完成 |
| 🟢 低 | 郭俊宏紅燈完整觸發確認 | `dividend_safety()` 已存在，確認是否在 UI 顯示紅燈文字 | 待確認 |
