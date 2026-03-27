# 基金監控儀表板 — Claude 行為規則

## 專案資訊
- 主要工作目錄：/home/user/my-fund-dashboard
- Repo：chenlin1984/my-fund-dashboard
- 平台：Streamlit Cloud

## Git 規則
- 不要推送到 main/master，除非用戶明確要求
- 不要建立 PR，除非用戶明確要求
- 所有開發在指定功能分支進行
- 推送前確認分支名稱正確

## 程式碼修改規則
- 修改任何檔案前必須先 Read 讀取
- 修改 app.py 或 macro_engine.py 前，確認版本號已更新
- 版本格式：ENGINE_VERSION 在 macro_engine.py 頂部，APP_VERSION 在 app.py 頂部

## 核心架構原則
- 快取清除：手動觸發時必須呼叫 fetch_all_indicators.clear()
- 趨勢計算：使用 np.polyfit Smart Slope，禁止用 diff()
- UI 渲染順序：所有 banner/alert 必須在 fetch 完成後才顯示
- 指標後處理：每個指標 dict 需含 z_score, trend_slope, days_stale, is_stale

## 禁止行為
- 不要新增不必要的抽象層或 helper
- 不要為假設性未來需求設計
- 不要在未讀取檔案的情況下提議修改
