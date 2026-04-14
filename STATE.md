# 專案戰情室 (Project State)
> _最後更新：2026-04-14_

## 📌 當前狀態
- **環境**: Streamlit Cloud + GitHub
- **進度**: Tab5/Tab6 新增中（分段執行模式）
- **工作分支**: `claude/system-detox-upgrade-ra7Tp`
- **⚠️ 注意**: tab5/tab6 定義已加入 app.py line 208，`with tab5:` / `with tab6:` 區塊尚待新增

## 🎯 當前任務 A：產出 ARCHITECTURE.md 技術規格書（分段執行計畫）

| 步驟 | 內容 | 狀態 |
|------|------|------|
| Arch-1 | ARCHITECTURE.md §1 專案概覽 + §2 目錄結構（含每檔說明與行數） | ✅ 完成 |
| Arch-2 | ARCHITECTURE.md §3 資料流向圖（使用者操作 → Session State → 各模組呼叫鏈） | ✅ 完成 |
| Arch-3 | ARCHITECTURE.md §4 核心函式 I/O 定義（macro_engine + fund_fetcher 主要函式） | ✅ 完成 |
| Arch-4 | ARCHITECTURE.md §5 核心函式 I/O 定義（ai_engine + portfolio_engine + backtest_engine） | ✅ 完成 |
| Arch-5 | ARCHITECTURE.md §6 Session State schema + §7 部署與外部依賴表，commit & push | ✅ 完成 |

## 📐 ARCHITECTURE.md 規範
- **純規格書**：不含任何實作程式碼
- **函式格式**：`函式名(input_type) → output_type`，附一行說明
- **資料流**：以文字箭頭圖（`→`）描述，不用 Mermaid（避免渲染問題）
- **檔案位置**：`/home/user/my-fund-dashboard/ARCHITECTURE.md`

## 🎯 次要任務 B：新增 Tab5 資料診斷 + Tab6 說明書（暫緩，待 Arch 完成後繼續）

| 步驟 | 內容 | 狀態 |
|------|------|------|
| Tab-A | app.py 末端新增 `with tab5:` — 總經 14 指標健康燈號表 + API Key 狀態 | ⏳ 暫緩 |
| Tab-B | app.py 末端 tab5 補充 — 基金診斷擴展欄（NAV/配息/持股/Sharpe 逐基金） | ⏳ 暫緩 |
| Tab-C | app.py 末端新增 `with tab6:` — 說明書 8 子頁 | ⏳ 暫緩 |
| Tab-D | AST 驗證 → commit → push | ⏳ 暫緩 |

---

## 🔄 歷史重構任務進度

| 步驟 | 內容 | 狀態 |
|------|------|------|
| Step 1 | 刪除 pages/（3 個股票/ETF 頁面）+ app_backup | ✅ 完成 (commit 410f60a) |
| Step 2 | macro_engine.py：移除 @st.cache_data（fetch_all_indicators）| ✅ 完成 (commit 410f60a) |
| Step 3 | fund_fetcher.py：移除 @st.cache_data + 刪除 fetch_etf_market_price / calc_vcp_signal | ✅ 完成 (commit 410f60a) |
| **Step 4** | **app.py：全新改寫（4 tabs：總經/單一基金/組合基金/回測）** | ✅ 完成 (commit de76c8b) |

## ✅ 破損點已全數清除（Step 4 完成）

全部 ImportError / NameError / AttributeError 已消除。app.py 通過 AST 語法驗證。

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
