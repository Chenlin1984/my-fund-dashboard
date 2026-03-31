# STATE.md — 專案狀態快照

_最後更新：2026-03-30_

## 核心檔案簡介
| 檔案 | 說明 |
|------|------|
| `app.py` | Streamlit 前端主程式，含儀表板、基金分析、投資組合、AI 對話等頁面 |
| `macro_engine.py` | 總經位階分析引擎，含 FRED 資料抓取、PMI/CPI/利差偵測、景氣位階計算 |
| `ai_engine.py` | AI 分析引擎，提供基金評分與對話功能 |
| `fund_fetcher.py` | 基金資料抓取模組，負責從外部 API 取得 NAV 等數據 |
| `portfolio_engine.py` | 投資組合計算引擎，含績效與風險指標計算 |
| `backtest_engine.py` | 回測引擎，支援歷史績效模擬 |
| `requirements.txt` | Python 套件依賴清單 |
| `CLAUDE.md` | Claude AI 核心開發與治理協議（§1~§6） |

## 目前開發進度
- [2026-03-31] **[完成] 新版 UI 三分頁開發**：建立 pages/ 目錄，新增三個全新 Streamlit 分頁（策略選股/深度診斷/庫存損益），規格來源：UI/UX 規格書 v1。備份 app.py → app_backup_20260331.py。PR #19 待 merge。
- [2026-03-31] **ETF 追蹤 Tab 新增完成**：app.py 新增 tab6「🏦 ETF 追蹤」，預設台灣 7 檔 + 海外 8 檔 ETF，yfinance 即時報價，TTL 15 分鐘快取，支援自訂新增/移除。PR #19 待 merge。
- [2026-03-31] **虛假資料掃描**：全庫無硬寫假數據，所有資料來源均為真實 API（FRED / yfinance / MoneyDJ）。
- [2026-03-30] 系統排毒與協議升級完成：CLAUDE.md 已更新為終極版 6 板塊協議，STATE.md 防斷線機制已啟動。

## 待修復 Bug 清單
- 台股法人籌碼：yfinance 對台股覆蓋有限，pages/2_🔬_深度診斷.py 已加說明提示，未來可接 FinMind API
