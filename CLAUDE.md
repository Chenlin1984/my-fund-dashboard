# 基金監控儀表板 — Claude 行為規則

## 專案資訊
- 主要工作目錄：/home/user/my-fund-dashboard
- Repo：chenlin1984/my-fund-dashboard
- 平台：Streamlit Cloud

## 一、開發與自省 (Self-Audit)
每次撰寫或修改代碼後，必須自動執行以下自我審核：
- **邏輯審查**：確認實作符合需求，無邏輯斷層
- **邊界測試**：主動考慮 2-3 個異常場景（空輸入、極大/極小值、異常型別）
- **效能評估**：估計時間/空間複雜度，說明是否可接受
- **Debug 與修正**：若發現潛在 Bug，直接在最終代碼中標註並修正，不留待後續

## 二、PR 工作流程（User-Merge Policy）
- 所有代碼變動**禁止**直接推送到 master/main 分支
- 修改後在新分支提交，發起 PR（Pull Request）即可
- **不得自動 Merge PR**，由使用者自行在 GitHub 決定何時合併
- **代碼變更任務完成的定義**：PR 已開啟，提供 GitHub PR 連結給使用者，等待使用者 Merge

## 三、精簡原則
- 優先使用推理而非冗餘指令；規則應清爽、無重複
- 不要新增不必要的抽象層或 helper
- 不要為假設性未來需求設計

## 四、UI 強制更新邏輯（Streamlit 專用）
側邊欄必須包含以下快取清除按鈕，確保使用者可強制載入最新 GitHub 邏輯：
```python
if st.sidebar.button("♻️ 強制同步 GitHub 最新邏輯"):
    st.cache_data.clear()
    st.cache_resource.clear()
    st.success("已清除緩存，請重新整理網頁")
    st.rerun()
```

## 核心架構規則（基金儀表板專用）
- 修改 app.py 或 macro_engine.py 前，確認版本號已更新
  - ENGINE_VERSION 在 macro_engine.py 頂部，APP_VERSION 在 app.py 頂部
- 快取清除：手動觸發時必須呼叫 fetch_all_indicators.clear()
- 趨勢計算：使用 np.polyfit Smart Slope，禁止用 diff()
- UI 渲染順序：所有 banner/alert 必須在 fetch 完成後才顯示
- 指標後處理：每個指標 dict 需含 z_score, trend_slope, days_stale, is_stale
