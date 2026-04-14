# 專案戰情室 (Project State)
> _最後更新：2026-04-14_

## 📌 當前狀態
- **環境**: Streamlit Cloud + GitHub
- **進度**: 重構進行中（Step 1-3 完成；Step 4 app.py 全新改寫待執行）
- **工作分支**: `claude/system-detox-upgrade-ra7Tp`

## 🔄 重構任務進度

| 步驟 | 內容 | 狀態 |
|------|------|------|
| Step 1 | 刪除 pages/（3 個股票/ETF 頁面）+ app_backup | ✅ 完成 |
| Step 2 | macro_engine.py：移除 @st.cache_data（fetch_all_indicators）| ✅ 完成 |
| Step 3 | fund_fetcher.py：移除 @st.cache_data（2 個函式）+ 刪除 fetch_etf_market_price / calc_vcp_signal | ✅ 完成 |
| Step 4 | app.py：全新改寫（4 tabs：總經/單一基金/組合基金/回測，零快取，移除 ETF/個股）| ⏳ 進行中 |

## 🛠️ 檔案結構與核心組件（重構後目標）

| 檔案 | 說明 | 狀態 |
|------|------|------|
| `app.py` | 主程式（4 tabs：總經/單一基金/組合基金/回測）零快取版 | ⏳ 改寫中 |
| `macro_engine.py` | 總經引擎（已移除 @st.cache_data）| ✅ 完成 |
| `fund_fetcher.py` | 基金抓取（已移除快取裝飾器、ETF/VCP 函式）| ✅ 完成 |
| `ai_engine.py` | Gemini AI 分析（無需修改）| ✅ 保留 |
| `portfolio_engine.py` | 組合評分引擎（無需修改）| ✅ 保留 |
| `backtest_engine.py` | 回測引擎（無需修改）| ✅ 保留 |

## 🔑 Proxy 設定（已完成）

| 項目 | 狀態 |
|------|------|
| NAS Proxy 連線測試 MoneyDJ HTTP 200 | ✅ |
| NAS Proxy 連線測試 TDCC HTTP 302 | ✅ |
| fund_fetcher.py SSL verify=False（proxy 模式）| ✅ |

## 🎯 重構三大目標（需求）

1. **無快取**：移除所有 @st.cache_data / @st.cache_resource / 自建 dict cache
2. **四模組**：總經 / 單一基金 / 組合基金 / 回測（僅此四個 Tab）
3. **範圍限縮**：清除所有 ETF、個股（台股/美股）相關代碼

## 📋 PR 歷史
| PR | 標題 | 狀態 |
|----|------|------|
| #30 | fix: proxy 模式下補回 HTTPS 基金資料抓取支援 | ✅ merged |
| #26 | CLAUDE.md v2.0 + STATE.md 全面更新 | ✅ merged |
| #25 | Proxy 狀態指示器 + 測試連線按鈕 | ✅ merged |
| #24 | Python 3.9 型別相容修復 | ✅ merged |
| #21 | NAS Proxy 全站注入 v6.24 | ✅ merged |
