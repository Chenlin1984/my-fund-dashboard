# 專案戰情室 (Project State)
> _最後更新：2026-04-14_

## 📌 當前狀態
- **環境**: Streamlit Cloud + GitHub
- **進度**: 重構 Step 1-3 完成；Step 4 進行中（app.py 已備份，待全新改寫）
- **工作分支**: `claude/system-detox-upgrade-ra7Tp`
- **⚠️ 注意**: 現有 app.py 處於過渡期破損狀態（import 引用已刪除函式），Step 4 完成前無法正常啟動

## 🔄 重構任務進度

| 步驟 | 內容 | 狀態 |
|------|------|------|
| Step 1 | 刪除 pages/（3 個股票/ETF 頁面）+ app_backup | ✅ 完成 (commit 410f60a) |
| Step 2 | macro_engine.py：移除 @st.cache_data（fetch_all_indicators）| ✅ 完成 (commit 410f60a) |
| Step 3 | fund_fetcher.py：移除 @st.cache_data + 刪除 fetch_etf_market_price / calc_vcp_signal | ✅ 完成 (commit 410f60a) |
| **Step 4** | **app.py：全新改寫（4 tabs：總經/單一基金/組合基金/回測）** | ⏳ 進行中 |

## 🐛 已知破損點（Step 4 完成即解除）

| 行號 | 問題 | 類型 |
|------|------|------|
| 28-29 | 匯入已刪除的 `fetch_etf_market_price`, `calc_vcp_signal` | ImportError |
| 1201, 1219 | `_render_fund_analysis` 呼叫已刪除函式 | NameError |
| 301-302, 1623 | 對非快取函式呼叫 `.clear()` | AttributeError |
| 6288-6334 | `_fetch_etf_batch.clear()`（ETF Tab 殘留）| AttributeError |

## 🛠️ 檔案結構（重構後目標）

| 檔案 | 說明 | 狀態 |
|------|------|------|
| `app.py` | 主程式（4 tabs：總經/單一基金/組合基金/回測）零快取版 | ⏳ 改寫中 |
| `macro_engine.py` | 總經引擎（已移除 @st.cache_data）| ✅ |
| `fund_fetcher.py` | 基金抓取（已移除快取裝飾器、ETF/VCP 函式）| ✅ |
| `ai_engine.py` | Gemini AI 分析 | ✅ |
| `portfolio_engine.py` | 組合評分引擎 | ✅ |
| `backtest_engine.py` | 回測引擎 | ✅ |

## 🎯 新 app.py 四模組規格

| Tab | 模組 | 資料來源 |
|-----|------|---------|
| 1 | 🌐 總經 | macro_engine.fetch_all_indicators |
| 2 | 🔍 單一基金 | fund_fetcher.fetch_fund_from_moneydj_url |
| 3 | 📊 組合基金 | fund_fetcher + portfolio_engine |
| 4 | 🔬 回測 | backtest_engine + fund_fetcher |

## 🔑 Proxy 設定（已完成）
- NAS Proxy：✅ MoneyDJ HTTP 200 / TDCC HTTP 302
- fund_fetcher.py SSL verify=False（proxy 模式）：✅

## 📋 PR 歷史
| PR | 標題 | 狀態 |
|----|------|------|
| #30 | fix: proxy 模式下補回 HTTPS 基金資料抓取支援 | ✅ merged |
| #26 | CLAUDE.md v2.0 + STATE.md | ✅ merged |
| #25 | Proxy 狀態指示器 + 測試連線按鈕 | ✅ merged |
