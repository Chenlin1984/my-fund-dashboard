# STATE.md — 專案狀態快照

_最後更新：2026-04-13_

## 核心檔案簡介
| 檔案 | 說明 |
|------|------|
| `app.py` | Streamlit 主頁，含總經儀表板、基金分析（MoneyDJ）、投資組合、AI 對話、ETF 追蹤（tab6）|
| `macro_engine.py` | 總經位階引擎：FRED API（FEDFUNDS/CPI/UNRATE/T10Y2Y）+ Yahoo Finance（VIX/TWII）+ CBC M1B/M2 |
| `fund_fetcher.py` | 基金資料抓取 v6.24：MoneyDJ/TDCC/FundClear，NAS Proxy 注入，GitHub Actions 快取讀取 |
| `ai_engine.py` | Gemini AI 分析：基金評分、景氣解讀、投資建議 |
| `portfolio_engine.py` | 投資組合計算：因子評分、股息安全性、風險警示、最佳化 |
| `backtest_engine.py` | 回測引擎：歷史績效模擬、績效指標計算 |
| `pages/1_📊_策略選股.py` | 策略選股：景氣 KPI + 6 大策略 pills + yfinance 即時報價（不需 Proxy）|
| `pages/2_🔬_深度診斷.py` | 深度診斷：K線/均線/布林/MACD + 法人籌碼 + 財報三率 + PE/PB 河流圖 |
| `pages/3_💼_庫存損益.py` | 庫存損益：持倉管理 + 甜甜圈分佈 + 損益表 + MDD + 報酬率長條圖 |
| `SPEC.md` | 系統規格說明書 v2.0（業務邏輯 + 防呆機制 + 工程架構） |
| `scripts/fetch_nav_cache.py` | GitHub Actions 每日 00:30 抓取 NAV 存入 cache/nav/*.json |
| `.github/workflows/fetch_nav_cache.yml` | 自動化快取 workflow |

## 目前開發進度
- [2026-04-13] **[本次]** CLAUDE.md 升級至 Core Protocol v2.0（5 板塊精簡版）
- [2026-04-13] **[本次]** STATE.md 全面更新
- [2026-04-13] **[完成]** PR #25 merged：側邊欄 Proxy 狀態指示器 + 🔍「測試 Proxy 連線」按鈕
- [2026-04-01] **[完成]** PR #24 merged：移除 Python 3.10+ 型別語法，相容 Streamlit Cloud Python 3.9
- [2026-04-01] **[完成]** PR #23 merged：STATE.md 進度更新
- [2026-04-01] **[完成]** PR #22 merged：proxy endpoint 修正為 `chen10021.synology.me:3128`
- [2026-03-31] **[完成]** PR #21 merged：fund_fetcher v6.24 NAS Proxy 全站注入
- [2026-03-31] **[完成]** PR #20 merged：ETF 追蹤 tab6 + pages/ 三分頁
- [2026-03-31] **[完成]** PR #18 merged：Sharpe rf 動態化 + ETF 折溢價 + VCP 訊號

## 待完成（需人工操作）
- **NAS Proxy 設定**（影響 MoneyDJ 基金資料抓取）：
  1. Synology Proxy Server → Access Control → 新增 `0.0.0.0/0`
  2. Proxy Server → Authentication → 設定帳密
  3. 路由器 Port Forwarding：外網 TCP 3128 → NAS 內網 IP:3128
  4. Streamlit Cloud Secrets 填入 `[proxy]` username / password
  5. 側邊欄按「🔍 測試 Proxy 連線」確認 HTTP 200

## 待修復 Bug 清單
- 台股法人籌碼：yfinance 覆蓋率低，待接 FinMind API
- 策略選股 `dict[str, list[str]]` 型別標注：需確認 Python 3.9 相容性（`from __future__ import annotations` 或改寫）
