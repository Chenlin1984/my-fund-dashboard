# STATE.md — 專案狀態快照

_最後更新：2026-03-31_

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
| `SPEC.md` | 終極版系統開發規格書 v16.0 |

## 目前開發進度
- [2026-03-31] **[完成] NAS Proxy 串接**：fund_fetcher.py 全部 requests 注入 NAS Proxy，407/403/timeout 分層處理。PR #21 待 merge。
- [2026-03-31] **[完成] Bug 修復**：Sharpe rf 動態化 + ETF 折溢價警示 + 春哥 VCP 訊號。已 merged (PR #18)。
- [2026-03-31] **[完成] 新版 UI 三分頁**：pages/ 目錄，策略選股/深度診斷/庫存損益。已 merged (PR #20)。
- [2026-03-31] **[完成] ETF 追蹤 Tab**：app.py tab6，國內外 ETF 即時報價。已 merged (PR #20)。
- [2026-03-31] **虛假資料掃描**：全庫無硬寫假數據。
- [2026-03-30] 系統排毒與協議升級完成：CLAUDE.md 終極版 6 板塊協議。

## 待 Merge PR
- PR #21：NAS Proxy 串接（fund_fetcher v6.24）

## 待修復 Bug 清單
- 台股法人籌碼：yfinance 覆蓋有限，未來可接 FinMind API
