# 專案戰情室 (Project State)
> _最後更新：2026-04-13_

## 📌 當前狀態
- **環境**: Streamlit Cloud + GitHub
- **進度**: Core Protocol v2.0 部署完成；Requirements.md 已建立
- **工作分支**: `claude/system-detox-upgrade-ra7Tp`（待 merge 至 main）

## 🛠️ 檔案結構與核心組件

| 檔案 | 說明 |
|------|------|
| `app.py` | Streamlit 主程式（Tab 1~6：總經/基金/組合/回測/AI/ETF）|
| `macro_engine.py` | 總經引擎：FRED 10 指標 + VIX/TWII + CBC M1B/M2 |
| `fund_fetcher.py` | 基金抓取 v6.24：MoneyDJ/TDCC/FundClear + NAS Proxy + NAV 快取 |
| `ai_engine.py` | Gemini 2.5 Flash：四章 AI 報告 |
| `portfolio_engine.py` | 六因子評分 + MDD + 壓力測試 + Core/Satellite 分類 |
| `backtest_engine.py` | CAGR / Sharpe / Sortino / Calmar / MDD 回測 |
| `pages/1_📊_策略選股.py` | 6 大策略 pills + yfinance 即時報價（無需 Proxy）|
| `pages/2_🔬_深度診斷.py` | K線 / 法人籌碼 / 財報三率 / PE-PB 河流圖 |
| `pages/3_💼_庫存損益.py` | 持倉管理 + 甜甜圈 + 損益表 + MDD |
| `Requirements.md` | ✅ 用戶故事 + 驗收標準（真理來源）|
| `SPEC.md` | 工程規格 v16.0 / v2.0（技術細節）|
| `CLAUDE.md` | Core Protocol v2.0（5 板塊）|
| `scripts/fetch_nav_cache.py` | GitHub Actions 每日 NAV 快取腳本 |
| `requirements.txt` | Python 套件依賴清單 |

## 🐞 待辦與已知 Bug

| 優先度 | 項目 | 狀態 |
|--------|------|------|
| 🔴 高 | NAS Proxy 人工設定（5 步驟）→ 影響 MoneyDJ 資料抓取 | 待用戶操作 |
| 🔴 高 | Streamlit Cloud Secrets `[proxy]` 填寫完整帳密 | 待用戶操作 |
| 🟡 中 | `pages/1` 的 `dict[str, list[str]]` 型別標注：確認 Python 3.9 相容性 | 待確認 |
| 🟢 低 | 台股法人籌碼：接入 FinMind API | 未來功能 |
| 🟢 低 | 郭俊宏紅燈 `dividend_safety()` UI 觸發確認 | 待確認 |

## 📋 PR 歷史
| PR | 標題 | 狀態 |
|----|------|------|
| #26 | CLAUDE.md v2.0 + STATE.md 全面更新 | ⏳ 待 merge |
| #25 | Proxy 狀態指示器 + 測試連線按鈕 | ✅ merged |
| #24 | Python 3.9 型別相容修復 | ✅ merged |
| #21 | NAS Proxy 全站注入 v6.24 | ✅ merged |
| #20 | ETF tab6 + pages/ 三分頁 | ✅ merged |
| #18 | Sharpe rf / 折溢價 / VCP 訊號 | ✅ merged |
