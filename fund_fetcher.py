# =================================================
# 【Cell 7】寫入 fund_fetcher.py（基金資料抓取引擎）
# 說明：生成基金資料抓取引擎，負責從 MoneyDJ/FundClear 抓取
#        淨值、配息、績效、風險指標等資料。
# 新手提示：直接執行即可，不需要修改。
#            若特定基金無法抓到資料，通常是網站結構變更，
#            請回報給開發者更新解析邏輯。
# =================================================
#!/usr/bin/env python3
"""fund_fetcher.py v6.13
v6.4 修正:
- fetch_performance_wb01(): 雙策略解析，多 URL fallback
- fetch_risk_metrics(): 更強健的欄位偵測，多 URL fallback
v6.3 修正:
- fetch_risk_metrics(): 修正 wb07 row-offset bug（row0=標題, row1=欄頭, row2+=資料）
- peer_compare / yearly_stats 同步修正
v6.2 修正:
- fetch_performance_wb01(): 從績效頁(wb01)取「含息報酬率」(1Y/3Y/5Y)
- fetch_risk_metrics(): 正確解析wb07全欄位(Alpha/Beta/R²/TE/Variance/同類排名/年度比較)
- wb05: 直接讀取「年化配息率%」欄存入 moneydj_div_yield
資料來源：
  搜尋：fundclear.com.tw REST API → MoneyDJ option 選單
  淨值：allianz/chubb子網域 → tcbbankfund.moneydj.com（公開可存取）
  結構：tcbbankfund.moneydj.com（持股/配置/績效）
"""
import requests, re, time

# ══════════════════════════════════════════════════════════════════
# v13 排錯補強：統一安全工具函式
# ══════════════════════════════════════════════════════════════════

def safe_float(value, default=None):
    """
    安全把字串轉 float，避免 N/A / -- / 空值 / % 造成 ValueError。
    所有從 MoneyDJ 抓回的欄位一律先走此函式，不要裸 float()。
    """
    if value is None:
        return default
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in ("", "N/A", "n/a", "--", "－", "None", "null", "nan"):
        return default
    try:
        return float(text)
    except Exception:
        return default


def clean_risk_table(risk_table: dict) -> dict:
    """
    清洗 MoneyDJ 風險指標表：
    把 標準差/Sharpe/Alpha/Beta/R-squared/Tracking Error/Variance
    全部轉成 float or None，避免 N/A 字串流入計算。
    """
    NUMERIC = {"標準差", "Sharpe", "Alpha", "Beta",
               "R-squared", "Tracking Error", "Variance", "夏普值"}
    cleaned = {}
    for period, metrics in (risk_table or {}).items():
        cleaned[period] = {}
        for k, v in (metrics or {}).items():
            cleaned[period][k] = safe_float(v) if k in NUMERIC else v
    return cleaned


def fetch_url_with_retry(url, headers=None, params=None,
                         timeout=20, retries=3, sleep_sec=2):
    """
    帶重試機制的 requests.get（統一 Referer + User-Agent）。
    v13.9 修正：MoneyDJ 全站 Big5 編碼，強制正確解碼後才回傳。
    """
    import time as _t
    _headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.moneydj.com/",
        "Accept-Language": "zh-TW,zh;q=0.9",
    }
    if headers:
        _headers.update(headers)
    for _ in range(retries):
        try:
            resp = requests.get(url, headers=_headers,
                                params=params, timeout=timeout)
            if resp.status_code == 200:
                # v13.9 關鍵修正：MoneyDJ 全站 Big5，強制解碼
                # 若讓 requests 自行猜測編碼（通常猜 UTF-8）會全部亂碼
                if "moneydj.com" in url:
                    resp.encoding = "big5"
                else:
                    resp.encoding = resp.apparent_encoding or "utf-8"
                if resp.text.strip():
                    return resp
        except Exception as e:
            print(f"錯誤：{e}")
        _t.sleep(sleep_sec)
    return None


def is_valid_moneydj_page(html: str) -> bool:
    """
    v14.2: 驗證頁面是否為有效的 MoneyDJ 基金頁面。
    MoneyDJ 全站 Big5 編碼，正確解碼後中文可用；
    若編碼有問題則退回數字/日期 pattern 判斷。
    """
    if not html or len(html) < 500:
        return False
    import re as _re_v
    # 中文關鍵字（Big5 正確解碼後）
    keywords = ["淨值", "基金", "日期", "績效", "配息", "除息"]
    if sum(1 for k in keywords if k in html) >= 2:
        return True
    # 備用1：YYYY/MM/DD 日期 + 數字（淨值表格）
    if _re_v.search(r"\d{4}/\d{2}/\d{2}", html) and _re_v.search(r"[\d]{2}\.\d{4}", html):
        return True
    # 備用2：MoneyDJ URL pattern（確認是真正的基金頁）
    if "moneydj.com/funddj" in html and len(html) > 2000:
        return True
    return False


def classify_fetch_status(fund_data: dict) -> str:
    """
    v13.5: 依資料完整度分類抓取結果。
    判斷依據擴大：有 metrics 或 risk_metrics 都算有指標。
      'complete' → 名稱 + (淨值|序列) + 指標
      'partial'  → 有名稱 或 有淨值 或 有指標（任一）
      'failed'   → 幾乎什麼都沒有
    """
    has_name    = bool(fund_data.get("fund_name"))
    has_nav     = fund_data.get("nav_latest") is not None
    s = fund_data.get("series")
    has_series  = s is not None and hasattr(s, "__len__") and len(s) >= 10
    # 有 metrics 或 risk_metrics 都算有指標
    has_metrics = (bool(fund_data.get("metrics")) or
                   bool(fund_data.get("risk_metrics")))

    if has_name and (has_nav or has_series) and has_metrics:
        return "complete"
    if has_name or has_nav or has_metrics:
        return "partial"
    return "failed"


def merge_non_empty(dst: dict, src: dict) -> dict:
    """
    v13.5: 欄位級合併，只用 src 中真正有值的欄位更新 dst。
    避免空字串、None、空陣列把已成功抓到的資料覆蓋掉。
    """
    if dst is None:
        dst = {}
    if not src:
        return dst
    for k, v in src.items():
        if v in (None, "", [], {}):
            continue
        dst[k] = v
    return dst


def normalize_result_state(result: dict) -> dict:
    """
    v13.5: 根據實際資料狀態修正 error / warning / status 欄位。
    核心邏輯：
      - complete → 清除所有錯誤
      - partial  → 把「全失敗」改為 warning（不顯示紅字）
      - failed   → 確保有 error 訊息
    """
    status = classify_fetch_status(result)
    _FULL_FAIL_MSG = "❌ 所有來源均無法取得資料"

    if status == "complete":
        result["error"]   = None
        result["warning"] = None
    elif status == "partial":
        # 有資料就不應顯示「全失敗」紅字，改為黃色 warning
        err = result.get("error") or ""
        if "所有來源" in err or err.startswith("❌"):
            result["error"] = None
        if not result.get("warning"):
            result["warning"] = "⚠️ 部分資料取得成功（淨值歷史或風險指標不完整）"
    else:  # failed
        if not result.get("error"):
            result["error"] = _FULL_FAIL_MSG

    result["status"] = status
    return result


# ═════════════════════════════════════════════════════════
# TDCC OpenAPI 整合
# https://openapi.tdcc.com.tw/swagger-ui/index.html
# 3-1 境外基金總代理資訊 ✅（可用）
# 3-2 境外基金基本資料  （視資料更新而定）
# 3-4 境外基金淨值      （視資料更新而定）
# ═════════════════════════════════════════════════════════
import threading as _th
_tdcc_cache = {}
_tdcc_lock  = _th.Lock()

def _tdcc_get(ep: str) -> list:
    """GET https://openapi.tdcc.com.tw/v1/opendata/{ep}"""
    with _tdcc_lock:
        if ep in _tdcc_cache:
            return _tdcc_cache[ep]
    try:
        url  = f"https://openapi.tdcc.com.tw/v1/opendata/{ep}"
        hdrs = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://openapi.tdcc.com.tw/swagger-ui/index.html",
        }
        import urllib.request as _ur, json as _j
        req  = _ur.Request(url, headers=hdrs)
        with _ur.urlopen(req, timeout=8) as r:
            data = _j.loads(r.read())
        with _tdcc_lock:
            _tdcc_cache[ep] = data if isinstance(data, list) else []
        return _tdcc_cache[ep]
    except Exception as e:
        return []


def _src_tdcc_meta(code: str) -> dict:
    """
    TDCC OpenAPI 境外基金 metadata（3-2 + 3-4）。
    提供：基金名稱、計價幣別、最新淨值、淨值日期。
    注意：僅有最新淨值，無歷史序列。
    """
    meta = {}
    _c = code.upper().strip()
    try:
        # 3-2 基本資料（名稱、幣別）
        basic = _tdcc_get("3-2")
        for item in basic:
            item_code = (item.get("基金代碼") or item.get("境外基金代碼") or "").upper()
            if item_code == _c:
                meta["fund_name"] = item.get("基金名稱", "")
                meta["currency"]  = item.get("計價幣別", "USD")
                print(f"[src_tdcc_meta] 3-2 ✅ {_c}: {meta['fund_name'][:25]}")
                break
    except Exception as _e:
        print(f"[src_tdcc_meta] 3-2 {_c}: {_e}")
    try:
        # 3-4 最新淨值
        navs = _tdcc_get("3-4")
        for item in navs:
            item_code = (item.get("基金代碼") or "").upper()
            if item_code == _c:
                nav = safe_float(item.get("基金淨值"))
                date_str = str(item.get("日期", ""))[:10]
                if nav:
                    meta["nav_latest"] = nav
                    meta["nav_date"]   = date_str
                if not meta.get("fund_name"):
                    meta["fund_name"] = item.get("基金名稱", "")
                print(f"[src_tdcc_meta] 3-4 ✅ {_c}: nav={nav} @ {date_str}")
                break
    except Exception as _e:
        print(f"[src_tdcc_meta] 3-4 {_c}: {_e}")
    return meta


def tdcc_search_fund(keyword: str) -> list:
    """
    搜尋境外基金，整合三個 TDCC endpoint：
    3-1 總代理資訊 → 確認基金機構
    3-2 基金基本資料 → 搜尋基金名稱
    3-4 淨值 → 最新淨值
    
    回傳格式：
    [{"基金名稱": "...", "基金代碼": "...", "總代理": "...", "淨值": "...", "日期": "..."}]
    """
    results = []
    seen    = set()

    # ── 3-2 基金基本資料 ──────────────────────────────────
    basic = _tdcc_get("3-2")
    if basic:
        for item in basic:
            name = item.get("基金名稱","")
            code = item.get("基金代碼","") or item.get("境外基金代碼","")
            if keyword.lower() in name.lower() or keyword.lower() in code.lower():
                key  = name or code
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "基金名稱": name,
                        "基金代碼": code,
                        "總代理":   item.get("總代理名稱",""),
                        "淨值":     "",
                        "日期":     "",
                        "來源":     "TDCC-3-2",
                    })

    # ── 3-4 淨值（補充淨值欄位）────────────────────────────
    navs = _tdcc_get("3-4")
    nav_map = {}
    if navs:
        for item in navs:
            code = item.get("基金代碼","")
            name = item.get("基金名稱","")
            if code: nav_map[code] = item
            if name: nav_map[name] = item

    for r in results:
        key = r["基金代碼"] or r["基金名稱"]
        if key in nav_map:
            r["淨值"] = nav_map[key].get("基金淨值","")
            r["日期"] = nav_map[key].get("日期","")

    # 若 3-2 沒資料，嘗試從 3-4 直接搜尋
    if not results and navs:
        for item in navs:
            name = item.get("基金名稱","")
            code = item.get("基金代碼","")
            if keyword.lower() in name.lower() or keyword.lower() in code.lower():
                key  = name or code
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "基金名稱": name,
                        "基金代碼": code,
                        "總代理":   "",
                        "淨值":     item.get("基金淨值",""),
                        "日期":     item.get("日期",""),
                        "來源":     "TDCC-3-4",
                    })

    # ── 3-1 總代理（補充機構資訊）──────────────────────────
    agents = _tdcc_get("3-1")
    if agents and results:
        agent_map = {a.get("境外基金機構名稱","").upper(): a.get("總代理名稱","")
                     for a in agents}
        for r in results:
            if not r["總代理"]:
                for org, agent in agent_map.items():
                    if org and org[:6] in r.get("基金名稱","").upper():
                        r["總代理"] = agent
                        break

    # ── Fundclear 備援搜尋（當 TDCC 3-2 無資料時）──────────────────
    if not results:
        try:
            import urllib.request as _ur2, json as _j2, urllib.parse as _up
            fc_url = (
                "https://www.fundclear.com.tw/investBase/goGetSearchFundList.action"
                f"?keyword={_up.quote(keyword)}&fundType=2"
            )
            hdrs2 = {
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": "https://www.fundclear.com.tw/",
            }
            req2 = _ur2.Request(fc_url, headers=hdrs2)
            with _ur2.urlopen(req2, timeout=8) as resp:
                fc_data = _j2.loads(resp.read())
            # fundclear returns: [{fundName, fundCode, nav, navDate, ...}]
            items = fc_data if isinstance(fc_data, list) else fc_data.get("list", [])
            for item in items[:20]:
                name = item.get("fundName", item.get("基金名稱", ""))
                code = item.get("fundCode", item.get("基金代碼", ""))
                nav  = str(item.get("nav", item.get("淨值", "")))
                date = str(item.get("navDate", item.get("日期", "")))
                agent= item.get("agentName", item.get("總代理名稱", ""))
                if name and name not in seen:
                    seen.add(name)
                    results.append({
                        "基金名稱": name,
                        "基金代碼": code,
                        "總代理":   agent,
                        "淨值":     nav,
                        "日期":     date,
                        "來源":     "FundClear",
                    })
        except Exception:
            pass

    return results


def tdcc_get_agents() -> list:
    """取得所有境外基金總代理列表（3-1）"""
    data = _tdcc_get("3-1")
    return [{"機構": d.get("境外基金機構名稱",""),
             "總代理": d.get("總代理名稱",""),
             "核准基金數": d.get("核准基金筆數",""),
             "類股數": d.get("申報基金總類股數",""),
             "網址": d.get("總代理網址","")}
            for d in data]


def _tdcc_resolve_fund_name(code: str) -> str:
    """
    v6.11: 從 TDCC 3-2 查詢境外基金中文名稱。
    保險平台代碼（如 TLZF9）在 TDCC 登記為境外基金，可找到完整名稱。
    """
    _c = code.upper().strip()
    try:
        basic = _tdcc_get("3-2")
        for item in basic:
            item_code = (item.get("基金代碼") or item.get("境外基金代碼") or "").upper()
            if item_code == _c:
                name = item.get("基金名稱", "")
                if name:
                    print(f"[tdcc_resolve_name] {_c} → {name[:40]}")
                    return name
    except Exception as _e:
        print(f"[tdcc_resolve_name] {_c}: {_e}")
    return ""
import pandas as pd
import numpy as np
import streamlit as st
from bs4 import BeautifulSoup

HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
    "Accept-Language": "zh-TW,zh;q=0.9",
    "Referer": "https://www.moneydj.com/",     # v13 排錯：加 Referer 降低被擋機率
}
HDR_JSON = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Referer": "https://www.fundclear.com.tw/",
}

PORTAL_CFG = {
    "allianz": {
        "base_url":  "https://tcbbankfund.moneydj.com",  # Fix: allianz→tcbbankfund
        "nav_path":  "/w/wf/wf01.djhtm?a={fk}",
        "div_path":  "/w/wh/wh06_4.djhtm?a={fk}",
    },
    "chubb": {
        "base_url":  "https://chubb.moneydj.com",
        "nav_path":  "/w/wf/wf01.djhtm?a={fk}",
        "div_path":  "/w/wh/wh06_4.djhtm?a={fk}",
    },
}
# 台灣合作金庫 MoneyDJ 子網域（公開可存取，同架構）
TCB_BASE = "https://tcbbankfund.moneydj.com"

# v6.8: 保險公司 MoneyDJ 子網域推測（依代碼前綴）
# 當 tcbbankfund 無此基金時，嘗試對應保險公司專屬子網域
_INSURANCE_SUBDOMAIN_HINTS = {
    "TL":  ["tlife", "twlife", "taiwanlife", "tlins", "tlinsfund"],   # 台灣人壽
    "FL":  ["franklintem", "franklin", "fltempleton", "flintl"],       # 富蘭克林坦伯頓
    "CT":  ["cathaylife", "ctbclife", "ctlife"],                        # 國泰/中信人壽
    "ANZ": ["anz", "anzfund"],                                          # ANZ 銀行
    "JF":  ["jpmorgan", "jpmf", "jpmfund"],                            # JP Morgan
    "NN":  ["ing", "nnfund", "nnip"],                                   # NN Investment
    "FS":  ["fslife", "fubon", "fubonlife"],                            # 富邦人壽
    "NS":  ["nanshan", "nanshanlife"],                                  # 南山人壽
    "CH":  ["chinalife", "chinalifeins"],                               # 中國人壽
    "SN":  ["sinon", "sinonlife"],                                      # 新光人壽
}


# ══════════════════════════════════════════════════════════════════════
# 多來源抓取架構 v13.2
# 依照《基金多來源抓取架構說明書》：
#   來源1 → FundClear / TDCC API（最穩，Colab 友善）
#   來源2 → 鉅亨網 cnyes（Colab 友善）
#   來源3 → tcbbankfund.moneydj.com（MoneyDJ 子網域）
#   來源4 → www.moneydj.com（主站）
#   來源5 → SITCA 公開資料（境內基金）
#   本地快取 → 每日快取，避免重複失敗請求
# ══════════════════════════════════════════════════════════════════════

import os as _os
import datetime as _datetime
import json as _json_mod

# ── 本地快取路徑（環境自適應：Colab → /content, Streamlit Cloud → /tmp）──
_CACHE_DIR = "/content/fund_cache" if _os.path.isdir("/content") else "/tmp/fund_cache"

# ── 記憶體快照：網路與檔案快取均失效時的最後一道防線（同 macro_engine._INDICATOR_SNAPSHOT）
_FUND_SNAPSHOT: dict = {}  # key=code.upper(), value=完整 result dict（不含 series）

def _cache_path(code: str, dtype: str) -> str:
    _os.makedirs(_CACHE_DIR, exist_ok=True)
    return f"{_CACHE_DIR}/{code.upper()}_{dtype}.csv"


def _cache_load_nav(code: str, max_age_hours: int = 20) -> "pd.Series | None":
    """
    讀取本地 NAV 快取。
    若快取不存在或超過 max_age_hours，回傳 None（需重新抓取）。
    """
    fp = _cache_path(code, "nav")
    if not _os.path.exists(fp):
        return None
    try:
        mtime = _os.path.getmtime(fp)
        age_h = (_datetime.datetime.now().timestamp() - mtime) / 3600
        if age_h > max_age_hours:
            return None
        df = pd.read_csv(fp, index_col=0, parse_dates=True)
        if df.empty or len(df) < 5:
            return None
        s = df.iloc[:, 0].dropna()
        s.index = pd.to_datetime(s.index)
        print(f"[cache] ✅ {code} NAV 快取命中 {len(s)} 筆（{age_h:.1f}小時前）")
        return s.sort_index()
    except Exception as e:
        print(f"[cache] load_nav 失敗: {e}")
        return None


def _cache_save_nav(code: str, s: pd.Series):
    """儲存 NAV 序列到本地快取"""
    if s is None or len(s) < 5:
        return
    try:
        fp = _cache_path(code, "nav")
        s.to_csv(fp, header=["nav"])
        print(f"[cache] 💾 {code} NAV {len(s)} 筆已快取")
    except Exception as e:
        print(f"[cache] save_nav 失敗: {e}")


def _cache_load_div(code: str, max_age_hours: int = 48) -> "list | None":
    """讀取配息快取"""
    fp = _cache_path(code, "div")
    if not _os.path.exists(fp):
        return None
    try:
        age_h = (_datetime.datetime.now().timestamp() - _os.path.getmtime(fp)) / 3600
        if age_h > max_age_hours:
            return None
        with open(fp, "r", encoding="utf-8") as fh:
            data = _json_mod.load(fh)
        if data:
            print(f"[cache] ✅ {code} 配息快取命中 {len(data)} 筆")
            return data
    except Exception as e:
        print(f"[cache] load_div 失敗: {e}")
    return None


def _cache_save_div(code: str, divs: list):
    """儲存配息資料到本地快取"""
    if not divs:
        return
    try:
        fp = _cache_path(code, "div")
        with open(fp, "w", encoding="utf-8") as fh:
            _json_mod.dump(divs, fh, ensure_ascii=False, default=str)
        print(f"[cache] 💾 {code} 配息 {len(divs)} 筆已快取")
    except Exception as e:
        print(f"[cache] save_div 失敗: {e}")


def _cache_load_meta(code: str, max_age_hours: int = 48) -> "dict | None":
    """讀取基金基本資料快取"""
    fp = _cache_path(code, "meta")
    if not _os.path.exists(fp):
        return None
    try:
        age_h = (_datetime.datetime.now().timestamp() - _os.path.getmtime(fp)) / 3600
        if age_h > max_age_hours:
            return None
        with open(fp, "r", encoding="utf-8") as fh:
            data = _json_mod.load(fh)
        if data.get("fund_name"):
            print(f"[cache] ✅ {code} 基本資料快取命中: {data['fund_name'][:20]}")
            return data
    except Exception as e:
        print(f"[cache] load_meta 失敗: {e}")
    return None


def _cache_save_meta(code: str, meta: dict):
    """儲存基金基本資料到快取"""
    save_keys = ["fund_name","currency","risk_level","dividend_freq",
                 "fund_scale","category","fund_region","nav_latest",
                 "nav_date","year_high_nav","year_low_nav",
                 "moneydj_div_yield","mgmt_fee"]
    try:
        fp = _cache_path(code, "meta")
        slim = {k: meta.get(k) for k in save_keys if meta.get(k) is not None}
        with open(fp, "w", encoding="utf-8") as fh:
            _json_mod.dump(slim, fh, ensure_ascii=False, default=str)
    except Exception as e:
        print(f"[cache] save_meta 失敗: {e}")


# ── 來源1：FundClear API（境外基金，Colab 最穩定）─────────────────────
def _src_fundclear_nav(code: str) -> pd.Series:
    """
    從 FundClear REST API 取歷史淨值。
    境外基金（6位英數代碼）效果最佳，Colab IP 不會被擋。
    """
    try:
        import datetime as _dt
        end_d = _dt.date.today()
        start_d = end_d - _dt.timedelta(days=400)
        url = (
            f"https://www.fundclear.com.tw/SmartFundAPI/api/FundAjax/GetFundNAV"
            f"?FundCode={code}&StartDate={start_d.strftime('%Y/%m/%d')}"
            f"&EndDate={end_d.strftime('%Y/%m/%d')}"
        )
        r = fetch_url_with_retry(url, timeout=15, retries=2)
        if r is None:
            return pd.Series(dtype=float)
        data = r.json()
        rows = {}
        nav_list = (data.get("Data") or data.get("data") or
                    data.get("NAVList") or data.get("navList") or [])
        if not nav_list and isinstance(data, list):
            nav_list = data
        for item in nav_list:
            if isinstance(item, dict):
                d_val = (item.get("Date") or item.get("date") or
                         item.get("NavDate") or item.get("navDate") or "")
                n_val = safe_float(
                    item.get("NAV") or item.get("nav") or
                    item.get("NetAssetValue") or item.get("latestNav"))
                if d_val and n_val is not None:
                    try:
                        rows[pd.Timestamp(str(d_val)[:10])] = n_val
                    except Exception:
                        pass
        if rows:
            s = pd.Series(rows).sort_index()
            print(f"[src_fundclear] ✅ {code} {len(s)} 筆")
            return s
    except Exception as e:
        print(f"[src_fundclear] {code}: {e}")
    return pd.Series(dtype=float)


def _src_fundclear_meta(code: str) -> dict:
    """從 FundClear 取基金基本資料"""
    meta = {}
    try:
        url = (f"https://www.fundclear.com.tw/SmartFundAPI/api/FundAjax"
               f"/GetFundBasicInfo?FundCode={code}")
        r = fetch_url_with_retry(url, timeout=12, retries=2)
        if r is None:
            return meta
        data = r.json()
        info = (data.get("Data") or data.get("data") or
                (data if isinstance(data, dict) else {}))
        if isinstance(info, list) and info:
            info = info[0]
        if isinstance(info, dict):
            meta["fund_name"]   = (info.get("FundName") or info.get("fundName") or
                                    info.get("ChtName") or "")
            meta["currency"]    = (info.get("Currency") or info.get("currency") or "USD")
            meta["risk_level"]  = str(info.get("RiskLevel") or info.get("riskLevel") or "")
            meta["category"]    = (info.get("FundType") or info.get("fundType") or "")
            meta["nav_latest"]  = safe_float(info.get("LatestNAV") or info.get("latestNav"))
            nav_d = (info.get("LatestNAVDate") or info.get("navDate") or "")
            meta["nav_date"]    = str(nav_d)[:10] if nav_d else ""
            if meta.get("fund_name"):
                print(f"[src_fundclear_meta] ✅ {code}: {meta['fund_name'][:20]}")
    except Exception as e:
        print(f"[src_fundclear_meta] {code}: {e}")
    return meta


def _src_fundclear_div(code: str) -> list:
    """從 FundClear 取配息資料"""
    divs = []
    try:
        url = (f"https://www.fundclear.com.tw/SmartFundAPI/api/FundAjax"
               f"/GetFundDividend?FundCode={code}")
        r = fetch_url_with_retry(url, timeout=12, retries=2)
        if r is None:
            return divs
        data = r.json()
        items = (data.get("Data") or data.get("data") or
                 (data if isinstance(data, list) else []))
        for item in (items or []):
            amt = safe_float(item.get("DividendAmount") or
                             item.get("dividendAmount") or
                             item.get("Amount") or item.get("amount"))
            if amt is None or amt <= 0:
                continue
            d_str = (item.get("ExDividendDate") or item.get("exDividendDate") or
                     item.get("Date") or item.get("date") or "")
            divs.append({
                "date":      str(d_str)[:10],
                "ex_date":   str(d_str)[:10],
                "pay_date":  str(d_str)[:10],
                "amount":    amt,
                "yield_pct": safe_float(
                    item.get("DividendRate") or item.get("dividendRate"), 0) or 0,
                "currency":  item.get("Currency") or item.get("currency") or "USD",
            })
        if divs:
            print(f"[src_fundclear_div] ✅ {code} {len(divs)} 筆配息")
    except Exception as e:
        print(f"[src_fundclear_div] {code}: {e}")
    return divs


# ══════════════════════════════════════════════════════════════════════
# v13.7 替代資料來源：基金公司官網 Adapters
# 網路確認可存取：安聯投信 tw.allianzgi.com 對 Colab IP 無限制
# ══════════════════════════════════════════════════════════════════════

# ── 基金公司官網 URL 映射表 ───────────────────────────────────────────
_FUND_COMPANY_URLS = {
    # 安聯投信境內基金（ACTI/ACCP/ACDD 前綴）
    "ACTI71":  "https://tw.allianzgi.com/zh-tw/products-solutions/taiwan-onshore/allianz-global-investors-income-and-growth-balanced-fund-a1-twd",
    "ACTI98":  "https://tw.allianzgi.com/zh-tw/products-solutions/taiwan-onshore/allianz-global-investors-income-and-growth-balanced-fund-a-twd",
    "ACTI94":  "https://tw.allianzgi.com/zh-tw/products-solutions/taiwan-onshore/",
    "ACCP138": "https://tw.allianzgi.com/zh-tw/products-solutions/taiwan-onshore/",
    "ACDD19":  "https://tw.allianzgi.com/zh-tw/products-solutions/taiwan-onshore/",
}

# 安聯投信境內基金「ifund」電子交易平台淨值查詢（HTML 可抓）
_ALLIANZ_NAV_ENDPOINT = "https://ifund.allianzgi.com.tw/WebNav.aspx"
# 安聯投信 JSON 淨值 API（部分基金有效）
_ALLIANZ_NAV_API = "https://tw.allianzgi.com/api/sitecore/fund/GetFundNav"


def _src_allianzgi_nav(code: str) -> pd.Series:
    """
    安聯投信官網歷史淨值抓取。
    Colab IP 對 allianzgi.com 無限制，是 ACTI 系列最可靠的來源。
    路徑：tw.allianzgi.com → ifund.allianzgi.com.tw
    """
    import re as _re
    rows = {}
    # 優先從 ifund 平台抓淨值表（HTML 表格）
    for base_url in [
        _ALLIANZ_NAV_ENDPOINT,
        "https://tw.allianzgi.com/zh-tw/tools/fund-nav-search",
    ]:
        try:
            r = fetch_url_with_retry(base_url, timeout=15, retries=2)
            if r is None:
                continue
            soup = BeautifulSoup(r.text, "lxml")
            import re as _re2
            for tbl in soup.find_all("table"):
                txt = tbl.get_text()
                if "淨值" not in txt and "NAV" not in txt.upper():
                    continue
                for row in tbl.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        dt_txt  = cells[0].get_text(strip=True)
                        nav_txt = cells[1].get_text(strip=True).replace(",", "")
                        if _re2.match(r"\d{4}[/-]\d{2}[/-]\d{2}", dt_txt):
                            v = safe_float(nav_txt)
                            if v and v > 0:
                                try:
                                    rows[pd.Timestamp(dt_txt.replace("/", "-"))] = v
                                except Exception:
                                    pass
                        elif _re2.match(r"\d{2}/\d{2}$", dt_txt):
                            # MM/DD 格式（近30日頁面）補年份
                            import datetime as _dtt2
                            _td2 = _dtt2.date.today()
                            try:
                                _mo2 = int(dt_txt.split("/")[0])
                                _da2 = int(dt_txt.split("/")[1])
                                _yr2 = _td2.year if (_mo2, _da2) <= (_td2.month, _td2.day) else _td2.year - 1
                                _v2  = safe_float(nav_txt)
                                if _v2 and _v2 > 0:
                                    rows[pd.Timestamp(_dtt2.date(_yr2, _mo2, _da2))] = _v2
                            except Exception:
                                pass
            if len(rows) >= 5:
                s = pd.Series(rows).sort_index()
                print(f"[src_allianz] ✅ {code} {len(s)} 筆（{base_url[:40]}）")
                return s
        except Exception as e:
            print(f"[src_allianz] {base_url[:40]}: {e}")
    return pd.Series(dtype=float)


def _src_allianzgi_meta(code: str) -> dict:
    """
    安聯投信官網基本資料 + 最新淨值。
    tw.allianzgi.com 對 Colab 可用。
    """
    meta = {}
    # 優先 ifund 平台
    try:
        r = fetch_url_with_retry(_ALLIANZ_NAV_ENDPOINT, timeout=15, retries=2)
        if r and is_valid_moneydj_page(r.text):
            soup = BeautifulSoup(r.text, "lxml")
            for tbl in soup.find_all("table"):
                txt = tbl.get_text()
                if "基金名稱" not in txt and "淨值" not in txt:
                    continue
                rows_map = {}
                for row in tbl.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        rows_map[cells[0].get_text(strip=True)] = cells[1].get_text(strip=True)
                if rows_map:
                    meta["fund_name"] = rows_map.get("基金名稱", "")
                    meta["nav_latest"] = safe_float(rows_map.get("最新淨值") or rows_map.get("淨值"))
                    meta["currency"] = rows_map.get("計價幣別", "TWD")
                    if meta.get("fund_name"):
                        print(f"[src_allianz_meta] ✅ {code}: {meta['fund_name'][:20]}")
                        return meta
    except Exception as e:
        print(f"[src_allianz_meta] {e}")
    return meta


def calc_health_from_manual(
    nav_current: float,
    nav_1y_ago: float,
    div_per_unit: float,
    div_freq: int = 12,
    fund_name: str = "",
) -> dict:
    """
    v13.7 手動輸入降級計算模式。
    當無法自動抓取時，只需 4 個數字就能完成健康診斷：
      nav_current  : 目前淨值
      nav_1y_ago   : 一年前淨值（或去年同期）
      div_per_unit : 最近一期每單位配息金額
      div_freq     : 配息頻率（月配=12, 季配=4, 半年=2, 年配=1）

    計算：
      配息年化率 = 單次配息 × 年配次數 / 目前淨值 × 100%
      含息報酬率 = (目前淨值 - 一年前淨值 + 單次配息 × 年配次數) / 一年前淨值 × 100%
      真實收益   = 含息報酬率 - 配息年化率
      吃本金     = 含息報酬率 < 配息年化率
    """
    if nav_current <= 0 or nav_1y_ago <= 0:
        return {"error": "淨值不可為 0 或負數"}

    annual_div      = div_per_unit * div_freq
    div_yield_pct   = round(annual_div / nav_current * 100, 2)
    nav_change_pct  = round((nav_current - nav_1y_ago) / nav_1y_ago * 100, 2)
    total_return_pct = round(nav_change_pct + div_yield_pct, 2)
    real_return_pct  = round(total_return_pct - div_yield_pct, 2)
    eating_principal = total_return_pct < div_yield_pct

    # 健康評級
    if eating_principal:
        health = "🔴 吃本金"
        health_color = "#f44336"
        advice = f"含息報酬({total_return_pct:.2f}%) < 配息率({div_yield_pct:.2f}%)，配息部分來自本金，長期持有本金縮水"
    elif real_return_pct >= 3:
        health = "🟢 健康成長"
        health_color = "#00c853"
        advice = f"真實收益 +{real_return_pct:.2f}%，淨值成長有餘力支撐配息"
    elif real_return_pct >= 0:
        health = "🟡 邊緣健康"
        health_color = "#ff9800"
        advice = f"真實收益 +{real_return_pct:.2f}%，勉強打平，建議持續觀察"
    else:
        health = "🟠 淨值下滑"
        health_color = "#ff7043"
        advice = f"淨值下滑 {real_return_pct:.2f}%，配息雖充足但需注意本金侵蝕趨勢"

    return {
        "fund_name":        fund_name,
        "nav_current":      nav_current,
        "nav_1y_ago":       nav_1y_ago,
        "nav_change_pct":   nav_change_pct,
        "div_per_unit":     div_per_unit,
        "div_freq":         div_freq,
        "annual_div":       round(annual_div, 4),
        "div_yield_pct":    div_yield_pct,
        "total_return_pct": total_return_pct,
        "real_return_pct":  real_return_pct,
        "eating_principal": eating_principal,
        "health":           health,
        "health_color":     health_color,
        "advice":           advice,
        "calc_mode":        "manual",
    }


# ── 來源2：鉅亨網 API（無 IP 限制，伺服器可用）────────────────────────
def _cnyes_parse_navs(navs: list) -> dict:
    """解析 cnyes NAV 列表，回傳 {timestamp: float}"""
    rows = {}
    for item in navs:
        try:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                ts = pd.Timestamp(int(item[0]), unit="ms")
                v  = safe_float(item[1])
                if v and v > 0:
                    rows[ts.normalize()] = v
            elif isinstance(item, dict):
                d_val = (item.get("date") or item.get("Date")
                         or item.get("nav_date") or "")
                n_val = safe_float(item.get("nav") or item.get("NAV")
                                   or item.get("value"))
                if d_val and n_val:
                    rows[pd.Timestamp(str(d_val)[:10])] = n_val
        except Exception:
            pass
    return rows


def _cnyes_resolve_code(moneydj_code: str) -> list:
    """
    v6.11: 透過 cnyes search API 找出對應的 cnyes 基金代碼列表。
    新增 TDCC→cnyes 名稱橋接：保險平台代碼（如 TLZF9）在 cnyes 無法直接搜到，
    改用 TDCC 3-2 取得基金中文名稱，再用名稱搜 cnyes。
    回傳所有候選 cnyes 代碼，首位最優先。
    """
    from urllib.parse import quote as _uquote
    _code = moneydj_code.upper().strip()
    candidates = [_code, _code.lower()]   # 先試原始代碼
    _hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
        "Referer": "https://fund.cnyes.com/",
    }

    def _cnyes_search(key: str, limit: int = 10) -> list:
        """呼叫 cnyes search API，回傳 fundCode 列表"""
        try:
            url = (f"https://fund.api.cnyes.com/fund/api/v2/funds/search"
                   f"?key={_uquote(key)}&limit={limit}")
            r = requests.get(url, headers=_hdrs, timeout=10)
            if r.status_code == 200:
                data = r.json()
                items = (data.get("data", {}).get("list")
                         or data.get("data")
                         or data.get("items")
                         or [])
                if isinstance(items, list):
                    return [
                        (item.get("fundCode") or item.get("code")
                         or item.get("id") or "")
                        for item in items
                        if (item.get("fundCode") or item.get("code") or item.get("id"))
                    ]
        except Exception as _e:
            print(f"[cnyes_search] key={key!r}: {_e}")
        return []

    # Step 1: 直接用原始代碼搜
    found = _cnyes_search(_code)
    for c in found:
        if c and c not in candidates:
            candidates.append(c)
    print(f"[cnyes_search] {_code} 直接搜 → 候選: {candidates[:5]}")

    # Step 2: 若直接搜無新代碼，嘗試 TDCC 3-2 名稱橋接（適用保險平台代碼）
    if len(candidates) <= 2:
        tdcc_name = _tdcc_resolve_fund_name(_code)
        if tdcc_name:
            # 用基金名稱前 20 字元搜 cnyes（避免過長關鍵字無結果）
            key_short = tdcc_name[:20]
            found_by_name = _cnyes_search(key_short, limit=5)
            for c in found_by_name:
                if c and c not in candidates:
                    candidates.append(c)
            print(f"[cnyes_search] {_code} 名稱橋接 '{key_short}' → 候選: {candidates[:8]}")

    return candidates


def fetch_nav_cnyes(code: str) -> pd.Series:
    """
    鉅亨網歷史淨值（v6.7）。
    新增：search API 先找正確的 cnyes 代碼，再用代碼取歷史淨值。
    不依賴 MoneyDJ，Streamlit Cloud 可存取。
    """
    import datetime as _dt2
    import time as _time2
    end_d    = _dt2.date.today()
    start_d  = end_d - _dt2.timedelta(days=400)
    end_ms   = int(_time2.mktime(end_d.timetuple())) * 1000
    start_ms = int(_time2.mktime(start_d.timetuple())) * 1000

    # Step 1: 解析候選代碼（含 search fallback）
    candidates = _cnyes_resolve_code(code)

    _hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json",
        "Referer": "https://fund.cnyes.com/",
    }
    for _cand in candidates:
        _url = (f"https://fund.api.cnyes.com/fund/api/v2/funds/{_cand}"
                f"/nav?start={start_ms}&end={end_ms}")
        try:
            r = requests.get(_url, headers=_hdrs, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            navs = (data.get("data", {}).get("nav")
                    or data.get("data", {}).get("navs")
                    or data.get("items")
                    or [])
            if not navs and isinstance(data, list):
                navs = data
            rows = _cnyes_parse_navs(navs)
            if rows:
                print(f"[cnyes_nav] ✅ {code}→{_cand} {len(rows)} 筆")
                return pd.Series(rows).sort_index()
        except Exception as _e:
            print(f"[cnyes_nav] {_cand}: {_e}")

    return pd.Series(dtype=float)


def fetch_div_cnyes(code: str) -> list:
    """
    鉅亨網配息資料（REST API）。
    """
    divs = []
    _code = code.upper().strip()
    try:
        url = f"https://fund.api.cnyes.com/fund/api/v2/funds/{_code}/dividend"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
            "Referer": "https://fund.cnyes.com/",
        }, timeout=15)
        if r.status_code == 200:
            data = r.json()
            items = (data.get("data") or data.get("items") or [])
            if isinstance(items, list):
                for item in items:
                    d = (item.get("date") or item.get("exDate") or "")
                    amt = safe_float(item.get("dividend") or item.get("amount"))
                    if d and amt is not None:
                        divs.append({
                            "date": str(d)[:10],
                            "amount": amt,
                            "yield_pct": safe_float(item.get("yieldRate") or item.get("yield_pct"), 0),
                        })
    except Exception as _e:
        print(f"[cnyes_div] {_code}: {_e}")
    return divs


def _src_cnyes_nav(code: str) -> pd.Series:
    """鉅亨網歷史淨值（REST API，無 IP 封鎖）"""
    try:
        s = fetch_nav_cnyes(code)
        if len(s) >= 10:
            print(f"[src_cnyes] ✅ {code} {len(s)} 筆")
            return s
    except Exception as e:
        print(f"[src_cnyes] {code}: {e}")
    return pd.Series(dtype=float)


def _src_cnyes_div(code: str) -> list:
    """鉅亨網配息（REST API，無 IP 封鎖）"""
    try:
        divs = fetch_div_cnyes(code)
        if divs:
            print(f"[src_cnyes_div] ✅ {code} {len(divs)} 筆")
            return divs
    except Exception as e:
        print(f"[src_cnyes_div] {code}: {e}")
    return []


# ══════════════════════════════════════════════════════════════════════
# v13.4 境內基金 page-aware 路由工具
# ══════════════════════════════════════════════════════════════════════

# ── 預設代碼映射表（內建，不需外部檔案）────────────────────────────
_DEFAULT_MAPPING = {
    "ACTI171": {"public_code": "ACTI71",  "page_type": "yp010000", "note": "平台碼→公開碼"},
    "ACTI71":  {"public_code": "ACTI71",  "page_type": "yp010000", "note": "境內基金"},
    "ACTI7":   {"public_code": "ACTI71",  "page_type": "yp010000", "note": "ACTI7→ACTI71"},  # v6.9
    "ACTI98":  {"public_code": "ACTI98",  "page_type": "yp010000", "note": "境內基金"},
    "ACTI94":  {"public_code": "ACTI94",  "page_type": "yp010000", "note": "境內基金"},
    "ACCP138": {"public_code": "ACCP138", "page_type": "yp010000", "note": "境內基金"},
    "ACDD19":  {"public_code": "ACDD19",  "page_type": "yp010000", "note": "境內基金"},
    "TLZF9":   {"public_code": "TLZF9",   "page_type": "yp010001", "note": "境外基金(台灣人壽)"},  # v6.9
    "FLFM1":   {"public_code": "FLFM1",   "page_type": "yp010001", "note": "境外基金"},
    "CTZP0":   {"public_code": "CTZP0",   "page_type": "yp010001", "note": "境外基金"},
    "ANZ89":   {"public_code": "ANZ89",   "page_type": "yp010001", "note": "境外基金"},
    "JFZN3":   {"public_code": "JFZN3",   "page_type": "yp010001", "note": "境外基金"},
}

def load_fund_code_mapping(path: str = "fund_code_mapping.csv") -> dict:
    """
    載入基金代碼映射表（CSV），不存在時回傳內建預設表。
    CSV 格式：input_code, public_code, page_type, note
    """
    import os as _os
    mapping = dict(_DEFAULT_MAPPING)   # 先用內建預設
    if _os.path.exists(path):
        try:
            df_map = pd.read_csv(path)
            for _, row in df_map.iterrows():
                k = str(row.get("input_code", "")).upper().strip()
                if k:
                    mapping[k] = {
                        "public_code": str(row.get("public_code", k)).upper().strip(),
                        "page_type":   str(row.get("page_type", "yp010001")).lower().strip(),
                        "note":        str(row.get("note", "")),
                    }
            print(f"[mapping] ✅ 載入 {path}：{len(df_map)} 筆（+內建 {len(_DEFAULT_MAPPING)} 筆）")
        except Exception as _e:
            print(f"[mapping] {path} 讀取失敗：{_e}，使用內建預設")
    return mapping


def parse_moneydj_input(user_input: str) -> dict:
    """
    v13.6: 解析使用者輸入，保留 code / page_type / full_url。
    同時支援：
      - 完整 URL（https://www.moneydj.com/funddj/ya/yp010001.djhtm?a=tlzf9）
      - 純代碼（tlzf9 / TLZF9 / acdd19）
      - 短碼（大小寫均可）
    """
    import re as _re_pi
    text = (user_input or "").strip()
    info = {
        "raw_input":  text,
        "code":       "",
        "page_type":  "",
        "full_url":   "",
        "is_url":     False,
    }
    if text.lower().startswith("http"):
        info["is_url"]   = True
        info["full_url"] = text
        # 支援 ?a= 和 &a= 參數，代碼包含字母+數字+dash，長度放寬到 30
        m_code = _re_pi.search(
            r"[?&][aA]=([A-Z0-9a-z][A-Z0-9a-z\-]{1,29})", text, _re_pi.I)
        if m_code:
            info["code"] = m_code.group(1).upper()
        # 保留 page type（境內 yp010000 / 境外 yp010001 / yp081000 等）
        m_page = _re_pi.search(r"/([Yy][Pp]\d{6})\.djhtm", text, _re_pi.I)
        if m_page:
            info["page_type"] = m_page.group(1).lower()
    else:
        # 純代碼輸入：直接 upper，允許大小寫混合
        _raw = text.upper().strip()
        # 只取 code 部分（去掉多餘空白或後綴）
        _m_pure = _re_pi.match(r"^([A-Z0-9]{3,30}(?:-[A-Z0-9]{2,20})?)$", _raw)
        if _m_pure:
            info["code"] = _m_pure.group(1)
        else:
            info["code"] = _raw[:30]   # 兜底：最多 30 字元
    return info




def _src_direct_moneydj_url(full_url: str) -> dict:
    """
    直接抓使用者提供的完整 MoneyDJ 頁面。
    優先解析：基金名稱、最新淨值、淨值日期、年高/年低。
    即使沒有完整歷史資料，meta 資料本身就很有價值。
    """
    import re as _re_dm
    out = {
        "fund_name":    "",
        "nav_latest":   None,
        "nav_date":     "",
        "year_high_nav": None,
        "year_low_nav":  None,
        "currency":     "USD",
        "risk_level":   "",
        "dividend_freq": "",
        "fund_scale":   "",
        "category":     "",
        "mgmt_fee":     "",
        "error":        None,
        "data_source":  "direct_url",
    }
    try:
        r = fetch_url_with_retry(full_url, timeout=20, retries=2)
        if r is None or not is_valid_moneydj_page(r.text):
            out["error"] = "direct_url_invalid"
            return out

        soup = BeautifulSoup(r.text, "lxml")
        for tbl in soup.find_all("table"):
            txt = tbl.get_text(" ", strip=True)
            if "基金名稱" not in txt and "淨值" not in txt:
                continue
            rows_map = {}
            for row in tbl.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) == 2:
                    k = cells[0].get_text(strip=True)
                    v = cells[1].get_text(strip=True)
                    if k:
                        rows_map[k] = v
                elif len(cells) >= 4:
                    for i in range(0, len(cells)-1, 2):
                        k = cells[i].get_text(strip=True)
                        v = cells[i+1].get_text(strip=True)
                        if k:
                            rows_map[k] = v
            # 基本資料
            if rows_map.get("基金名稱"):
                out["fund_name"]    = rows_map.get("基金名稱", "")
                out["currency"]     = rows_map.get("計價幣別", "USD").replace(" ", "")
                out["risk_level"]   = rows_map.get("風險報酬等級", "").replace(" ", "")
                out["dividend_freq"]= rows_map.get("配息頻率", "").replace(" ", "")
                out["fund_scale"]   = rows_map.get("基金規模", "")
                out["category"]     = rows_map.get("投資標的", rows_map.get("基金類型", ""))
                out["mgmt_fee"]     = rows_map.get("最高經理費(%)", "")
            # 最新淨值 + 年高低（日期格式行）
            for row in tbl.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) >= 2:
                    dt = cells[0].get_text(strip=True)
                    if _re_dm.match(r"\d{4}/\d{2}/\d{2}", dt):
                        out["nav_date"]   = dt
                        out["nav_latest"] = safe_float(cells[1].get_text(strip=True))
                        if len(cells) >= 4:
                            out["year_high_nav"] = safe_float(cells[2].get_text(strip=True))
                            out["year_low_nav"]  = safe_float(cells[3].get_text(strip=True))
            if out["fund_name"] or out["nav_latest"]:
                print(f"[direct_url] ✅ {out['fund_name'][:20]} NAV={out['nav_latest']}")
                return out
    except Exception as e:
        out["error"] = str(e)
        print(f"[direct_url] ❌ {e}")
    return out


# ── 境內基金代碼正規化（v13.3）──────────────────────────────────────
def normalize_domestic_code(code: str) -> list:
    """
    v13.4: 境內基金代碼候選清單。
    1. 先查 mapping table（最可靠）
    2. ACTI1XX → 嘗試去掉 '1'（ACTI171→ACTI71）
    3. 回傳候選清單，由 orchestrator 逐一嘗試
    """
    c = (code or "").upper().strip()
    candidates = [c]
    # 1. mapping table 直接給答案
    mapping = load_fund_code_mapping()
    if c in mapping:
        pub = mapping[c].get("public_code", c)
        if pub != c:
            candidates.insert(0, pub)   # 公開碼優先
    # 2. ACTI1XX → 去掉第五位 '1'
    if c.startswith("ACTI") and len(c) >= 7 and c[4] == "1":
        alt = "ACTI" + c[5:]
        if alt not in candidates:
            candidates.append(alt)
    return list(dict.fromkeys(candidates))


# 境內基金前綴清單（從已知投信代碼整理）
_DOMESTIC_PREFIXES = (
    "ACTI", "ACTT", "ACCP", "ACDD",  # 安聯投信
    "BFAB", "BFAC", "BFAD",           # 部分境內 BF 前綴
    "ICPF", "ICPD",                    # 中國信託
    "JFPF", "JFPD",                    # 摩根
    "SCAP", "SCAD",                    # 富蘭克林華美
)

def _is_domestic_code(code: str, page_type: str = "") -> bool:
    """
    v14.4: page-aware 境內基金判斷（擴充版）。
    優先順序：
      1. page_type == "yp010000" → 直接確認境內
      2. page_type == "yp010001" → 直接確認境外
      3. mapping table 查詢
      4. code 前綴規則（擴充清單）
      5. 預設：境外（保守）
    """
    if page_type == "yp010000":
        return True
    if page_type == "yp010001":
        return False
    c = (code or "").upper().strip()
    # mapping table 優先查
    mapping = load_fund_code_mapping()
    if c in mapping:
        return mapping[c].get("page_type", "") == "yp010000"
    # 前綴規則（境內投信代碼格式：ACXX + 數字）
    return c.startswith(_DOMESTIC_PREFIXES)


# ── v13.8 頁型互換工具 ──────────────────────────────────────────────
def get_page_types_to_try(primary_page: str) -> list:
    """
    回傳 [首選頁型, 備用頁型]。
    若首選失敗，自動互換 yp010000 ↔ yp010001 重試。
    """
    alt = {"yp010000": "yp010001", "yp010001": "yp010000"}
    primary = primary_page or "yp010001"
    fallback = alt.get(primary, "yp010001")
    return [primary, fallback]


# ── 來源3：tcbbankfund.moneydj.com（子網域，限制較少）──────────────
def _src_nav_30day(code: str, page_type: str = "") -> pd.Series:
    """
    v14.3: 從 MoneyDJ 主淨值頁直接解析近30日淨值表。

    MoneyDJ 主 nav 頁（ya/yp010001 或 ya/yp010000）上永遠有
    近30日淨值表，格式為 MM/DD | 淨值，不需要帶 params。
    這是 yf/yp004002.djhtm 被 Colab IP 封鎖時的關鍵 fallback。

    URL 結構（確認）：
      境外: https://www.moneydj.com/funddj/ya/yp010001.djhtm?a=FLFM1
      境內: https://www.moneydj.com/funddj/ya/yp010000.djhtm?a=ACTI98
      兩者都在同頁面含近30日淨值表，MM/DD 格式
    """
    import re as _re_n30
    import datetime as _dtt
    rows = {}

    _page = page_type or ("yp010000" if _is_domestic_code(code) else "yp010001")
    _pages = get_page_types_to_try(_page)

    bases = [
        "https://tcbbankfund.moneydj.com/funddj",
        "https://chubb.moneydj.com/funddj",
        "https://www.moneydj.com/funddj",
    ]

    for _pg in _pages:
        if len(rows) >= 10:
            break
        for base in bases:
            try:
                url = f"{base}/ya/{_pg}.djhtm?a={code}"
                r = fetch_url_with_retry(url, timeout=20, retries=2)
                if r is None:
                    continue
                soup = BeautifulSoup(r.text, "lxml")
                _today = _dtt.date.today()
                _tmp = {}
                for tbl in soup.find_all("table"):
                    for row in tbl.find_all("tr"):
                        cells = row.find_all("td")
                        if len(cells) < 2:
                            continue
                        dt_txt  = cells[0].get_text(strip=True)
                        nav_txt = cells[1].get_text(strip=True).replace(",", "")
                        # YYYY/MM/DD 格式
                        if _re_n30.match(r"\d{4}/\d{2}/\d{2}", dt_txt):
                            v = safe_float(nav_txt)
                            if v and v > 0:
                                try:
                                    _tmp[pd.Timestamp(dt_txt.replace("/", "-"))] = v
                                except Exception:
                                    pass
                        # MM/DD 格式（近30日表格）
                        elif _re_n30.match(r"\d{2}/\d{2}$", dt_txt):
                            v = safe_float(nav_txt)
                            if v and v > 0:
                                try:
                                    _mo = int(dt_txt.split("/")[0])
                                    _da = int(dt_txt.split("/")[1])
                                    _yr = _today.year if (_mo, _da) <= (_today.month, _today.day) else _today.year - 1
                                    _tmp[pd.Timestamp(_dtt.date(_yr, _mo, _da))] = v
                                except Exception:
                                    pass
                if len(_tmp) >= 10:
                    rows = _tmp
                    print(f"[src_nav30] ✅ {code} {len(rows)} 筆 (page={_pg}, base={base[:30]})")
                    break
            except Exception as e:
                print(f"[src_nav30] {code} {_pg}: {e}")
        if len(rows) >= 10:
            break

    if rows:
        return pd.Series(rows).sort_index()
    return pd.Series(dtype=float)


def _src_tcb_nav(code: str) -> pd.Series:
    """
    TCB / MoneyDJ 子網域歷史淨值。
    依照原始 fetch_nav 順序，逐一嘗試各子網域與端點。
    """
    import datetime as _dt
    import re as _re2
    today = _dt.date.today()
    start = today - _dt.timedelta(days=400)

    # ── 優先嘗試原始 wf01/wb02 路徑（境內/境外通用，子網域限制最少）
    _dom = _is_domestic_code(code)
    _simple_urls = [
        f"https://tcbbankfund.moneydj.com/w/wf/wf01.djhtm?a={code}",
        f"https://tcbbankfund.moneydj.com/w/wb/wb02.djhtm?a={code}",
        f"https://chubb.moneydj.com/w/wf/wf01.djhtm?a={code}",
    ]
    if not _dom:
        # v6.10: 境外基金先試子網域的 yp004001（Streamlit Cloud 封鎖 www 但子網域可存取）
        _simple_urls.extend([
            f"https://tcbbankfund.moneydj.com/funddj/yf/yp004001.djhtm?a={code}",
            f"https://chubb.moneydj.com/funddj/yf/yp004001.djhtm?a={code}",
            f"https://www.moneydj.com/funddj/yf/yp004001.djhtm?a={code}",  # fallback（本地/Colab 可用）
        ])
    for _url in _simple_urls:
        try:
            hdr = {**HDR, "Referer": "https://www.moneydj.com/"}
            r = fetch_url_with_retry(_url, headers=hdr, timeout=20, retries=2)
            if r is None:
                continue
            s = _parse_nav_html(r.text)
            if len(s) >= 10:
                print(f"[src_tcb] ✅ {code} {len(s)} 筆（{_url[:55]}）")
                return s
            print(f"[src_tcb] {code} → {len(s)} 筆 ({_url[:45]})")
        except Exception as e:
            print(f"[src_tcb] {code} {_url[:45]}: {e}")

    # ── 次要：yp004002 帶日期區間（需 A/B/C params）
    base  = "https://tcbbankfund.moneydj.com/funddj"
    params = {
        "A": code,
        "B": start.strftime("%Y%m%d"),
        "C": today.strftime("%Y%m%d"),
    }
    _primary_page = "yp010000" if _is_domestic_code(code) else "yp010001"
    for _page in get_page_types_to_try(_primary_page):
        hdr = {**HDR,
               "Referer": f"https://tcbbankfund.moneydj.com/funddj/ya/{_page}.djhtm?a={code}"}
        try:
            r = fetch_url_with_retry(
                f"{base}/yf/yp004002.djhtm",
                headers=hdr, params=params, timeout=25
            )
            if r is None:
                continue
            rows = {}
            soup = BeautifulSoup(r.text, "lxml")
            for tbl in soup.find_all("table"):
                for row in tbl.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        dt_txt  = cells[0].get_text(strip=True)
                        nav_txt = cells[1].get_text(strip=True).replace(",", "")
                        if _re2.match(r"\d{4}/\d{2}/\d{2}", dt_txt):
                            v = safe_float(nav_txt)
                            if v is not None:
                                rows[pd.Timestamp(dt_txt)] = v
            if len(rows) >= 10:
                s = pd.Series(rows).sort_index()
                print(f"[src_tcb] ✅ {code} {len(s)} 筆（yp004002 page={_page}）")
                return s
        except Exception as e:
            print(f"[src_tcb] {code} yp004002 page={_page}: {e}")

    # ── 最終 fallback：近30日
    s30 = _src_nav_30day(code)
    if len(s30) >= 10:
        print(f"[src_tcb] ⤵ {code} 改用近30日 ({len(s30)}筆)")
        return s30
    return pd.Series(dtype=float)


def _src_tcb_meta(code: str) -> dict:
    """
    TCB MoneyDJ 子網域基本資料（含年高/年低）。
    v14.0: 從境內基金導覽列解析績效公司代碼(BFxxxx)
    """
    import re as _re2
    base = "https://tcbbankfund.moneydj.com/funddj"
    # v13.8: 首選頁型 + 自動互換備用頁型
    _dom    = _is_domestic_code(code)
    _pages  = get_page_types_to_try("yp010000" if _dom else "yp010001")
    # v14.2: 境內用 yp011000，境外用 yp011001（確認自實際頁面）
    _info_page = "yp011000" if _dom else "yp011001"
    _meta_paths = [
        f"/ya/{_pages[0]}.djhtm?a={code}",
        f"/yp/{_info_page}.djhtm?a={code}",
        f"/ya/{_pages[1]}.djhtm?a={code}",    # 備用：換頁型重試
    ]
    meta = {}  # Bug fix: 初始化 meta，避免後續 meta["fund_name"] 拋 NameError
    for path in _meta_paths:
        try:
            r = fetch_url_with_retry(f"{base}{path}", timeout=20)
            if r is None or not is_valid_moneydj_page(r.text):
                continue
            soup = BeautifulSoup(r.text, "lxml")
            for tbl in soup.find_all("table"):
                txt = tbl.get_text()
                if "基金名稱" in txt or "淨值" in txt:
                    rows_map = {}
                    for row in tbl.find_all("tr"):
                        cells = row.find_all("td")
                        if len(cells) == 2:
                            rows_map[cells[0].get_text(strip=True)] = cells[1].get_text(strip=True)
                        elif len(cells) >= 4:
                            for i in range(0, len(cells)-1, 2):
                                k = cells[i].get_text(strip=True)
                                if k: rows_map[k] = cells[i+1].get_text(strip=True)
                    if rows_map.get("基金名稱"):
                        meta["fund_name"]   = rows_map.get("基金名稱", "")
                        meta["currency"]    = rows_map.get("計價幣別", "USD").replace(" ", "")
                        meta["risk_level"]  = rows_map.get("風險報酬等級", "").replace(" ", "")
                        meta["dividend_freq"] = rows_map.get("配息頻率", "").replace(" ", "")
                        meta["fund_scale"]  = rows_map.get("基金規模", "")
                        meta["category"]    = rows_map.get("投資標的", rows_map.get("基金類型", ""))
                        meta["mgmt_fee"]    = rows_map.get("最高經理費(%)", "")
                    # v14.0: 從導覽列超連結抓境內基金的「績效公司代碼」(BFxxxx)
                    # 境內基金績效頁 yp020000?a=BFxxxx 用的是公司代碼而非基金代碼
                    for a_tag in tbl.find_all("a", href=True):
                        href = a_tag.get("href", "")
                        _bf = _re2.search(r"yp020000\.djhtm\?a=([A-Z0-9]+)", href, _re2.I)
                        if _bf:
                            meta["perf_company_code"] = _bf.group(1).upper()
                            break
                    # 年高低點
                    for row in tbl.find_all("tr"):
                        cells = row.find_all("td")
                        if len(cells) >= 4:
                            dt = cells[0].get_text(strip=True)
                            if _re2.match(r"\d{4}/\d{2}/\d{2}", dt):
                                meta["nav_date"]     = dt
                                meta["nav_latest"]   = safe_float(cells[1].get_text(strip=True))
                                meta["year_high_nav"] = safe_float(cells[2].get_text(strip=True))
                                meta["year_low_nav"]  = safe_float(cells[3].get_text(strip=True))
                    if meta.get("fund_name"):
                        print(f"[src_tcb_meta] ✅ {code}: {meta['fund_name'][:20]}")
                        return meta
        except Exception as e:
            print(f"[src_tcb_meta] {code} {path}: {e}")
    return meta


def _src_tcb_div(code: str) -> list:
    """
    TCB MoneyDJ 配息資料。
    v13.9: 境內基金用 yp013000，境外基金用 wb05（路徑不同）
    """
    divs = []
    base = "https://tcbbankfund.moneydj.com/funddj"
    # v14.2 確認配息路徑（從實際 HTML 驗證）：
    # funddividend.djhtm = 境內外都有，col 結構相同
    # wb05.djhtm         = 境外基金專用，含更詳細的年化率資料，優先使用
    # yp013000.djhtm     = 境內基金「持股比例」，非配息，不抓
    _is_dom = _is_domestic_code(code)
    _div_paths = (
        [f"/yp/funddividend.djhtm?a={code}"]                          # 境內：只有 funddividend
        if _is_dom else
        [f"/yp/wb05.djhtm?a={code}", f"/yp/funddividend.djhtm?a={code}"]  # 境外：wb05 優先
    )
    try:
        r = None
        for _dp in _div_paths:
            r = fetch_url_with_retry(f"{base}{_dp}", timeout=20)
            if r is not None:
                break
        if r is None:
            return divs
        soup = BeautifulSoup(r.text, "lxml")
        for tbl in soup.find_all("table"):
            txt = tbl.get_text()
            if "配息基準日" not in txt and "除息日" not in txt:
                continue
            # v14.2 確認結構（直接從 HTML 驗證）：
            # wb05 & funddividend 欄位完全相同：
            #   col[0]=配息基準日  col[1]=除息日  col[2]=發放日
            #   col[3]=TEXT"配息"(非數字!)  col[4]=每單位配息額  col[5]=年化配息率%  col[6]=幣別
            # 範例(ACTI98): 2026/02/25|2026/02/26|2026/03/05|配息|0.0898|9.54|台幣
            # 範例(FLFM1):  2026/02/27|2026/03/02|2026/03/05|配息|0.56|9.26|美元
            for row in tbl.find_all("tr")[1:60]:
                cols = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cols) < 6:
                    continue
                # col[3] 是文字 "配息"，col[4] 才是配息金額
                if not cols[0] or "/" not in cols[0]:
                    continue   # 跳過無效日期行
                amt = safe_float(cols[4])
                if amt is None or amt <= 0 or amt > 1000:
                    continue
                yld = safe_float(cols[5]) or 0
                cur = cols[6].strip() if len(cols) > 6 and cols[6].strip() else (
                    "TWD" if _is_domestic_code(code) else "USD")
                divs.append({
                    "date": cols[0], "ex_date": cols[1], "pay_date": cols[2],
                    "amount": amt, "yield_pct": yld, "currency": cur,
                })
            if divs:
                print(f"[src_tcb_div] ✅ {code} {len(divs)} 筆")
            break
    except Exception as e:
        print(f"[src_tcb_div] {code}: {e}")
    return divs


# ── 來源4：SITCA（境內基金基本資料）───────────────────────────────────
def _src_sitca_meta(code: str) -> dict:
    """
    SITCA 投信投顧公會公開查詢（境內基金）。
    適用於 ACTI71, ACTI98 等境內基金代碼。
    """
    meta = {}
    try:
        # SITCA 境內基金淨值查詢
        url = f"https://www.sitca.org.tw/ROC/Industry/IN2213.aspx?txtFundCode={code}"
        r = fetch_url_with_retry(url, timeout=15, retries=2)
        if r is None:
            return meta
        soup = BeautifulSoup(r.text, "lxml")
        # 找基金名稱
        for tag in soup.find_all(["h1","h2","h3","td","th","title"]):
            txt = tag.get_text(strip=True)
            if len(txt) > 4 and "基金" in txt and len(txt) < 60:
                meta["fund_name"] = txt
                break
        # 找最新淨值表格
        for tbl in soup.find_all("table"):
            txt = tbl.get_text()
            if "淨值" in txt or "NAV" in txt.upper():
                for row in tbl.find_all("tr"):
                    cells = [td.get_text(strip=True) for td in row.find_all("td")]
                    if len(cells) >= 2:
                        nav_v = safe_float(cells[-1])
                        if nav_v and nav_v > 0:
                            meta["nav_latest"] = nav_v
                            break
                break
        if meta.get("fund_name"):
            print(f"[src_sitca] ✅ {code}: {meta['fund_name'][:20]}")
    except Exception as e:
        print(f"[src_sitca] {code}: {e}")
    return meta


def _src_sitca_nav(code: str) -> pd.Series:
    """SITCA 境內基金歷史淨值（若有公開資料）"""
    rows = {}
    import re as _re3
    try:
        import datetime as _dt
        today = _dt.date.today()
        start = today - _dt.timedelta(days=400)
        url = (f"https://www.sitca.org.tw/ROC/Industry/IN2213.aspx"
               f"?txtFundCode={code}"
               f"&txtBeginDate={start.strftime('%Y/%m/%d')}"
               f"&txtEndDate={today.strftime('%Y/%m/%d')}")
        r = fetch_url_with_retry(url, timeout=20)
        if r is None:
            return pd.Series(dtype=float)
        soup = BeautifulSoup(r.text, "lxml")
        for tbl in soup.find_all("table"):
            for row in tbl.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if len(cells) >= 2:
                    dt_txt  = cells[0]
                    nav_txt = cells[1].replace(",", "")
                    if _re3.match(r"\d{4}[/-]\d{2}[/-]\d{2}", dt_txt):
                        v = safe_float(nav_txt)
                        if v is not None and v > 0:
                            try:
                                rows[pd.Timestamp(dt_txt.replace("/", "-"))] = v
                            except Exception:
                                pass
        if len(rows) >= 10:
            s = pd.Series(rows).sort_index()
            print(f"[src_sitca_nav] ✅ {code} {len(s)} 筆")
            return s
    except Exception as e:
        print(f"[src_sitca_nav] {code}: {e}")
    return pd.Series(dtype=float)


# ── 主 Orchestrator：統一入口 ──────────────────────────────────────────
def fetch_fund_multi_source(code: str,
                             force_refresh: bool = False,
                             page_type: str = "") -> dict:
    """
    多來源基金資料抓取主函式（v13.4）。

    v13.4 新增：
      - page_type 參數：從 parse_moneydj_input() 保留的頁型直接傳入
      - normalize_domestic_code()：含 mapping table 優先查詢
      - 境內/境外路由完全分流

    抓取優先順序：
      NAV：快取 → FundClear → 鉅亨網 → TCB MoneyDJ → SITCA
      Meta：快取 → TCB MoneyDJ → FundClear → SITCA
      配息：快取 → TCB MoneyDJ → FundClear → 鉅亨網
    """
    # ── 候選代碼清單（mapping table + ACTI 系列展開）────────────────
    _is_dom = _is_domestic_code(code, page_type)
    code_candidates = (
        normalize_domestic_code(code)
        if _is_dom
        else [code.upper().strip()]
    )
    best_result = None

    for _candidate in code_candidates:
        _result = _fetch_fund_single(
            _candidate, force_refresh=force_refresh,
            page_type=page_type    # ← v13.4: 保留原始 page_type 傳遞
        )
        _status = classify_fetch_status(_result)
        print(f"[orchestrator] {_candidate} → {_status} (err:{_result.get('error','')[:40]})")
        if _status == "complete":
            return _result
        if best_result is None:
            best_result = _result
        elif (classify_fetch_status(best_result) == "failed"
              and _status == "partial"):
            best_result = _result

    return best_result or {
        "fund_code": code, "error": f"所有候選代碼均無資料：{code_candidates}",
        "series": None, "fund_name": "", "nav_latest": None,
        "dividends": [], "metrics": {}, "perf": {}, "risk_metrics": {},
    }


def _src_insurance_subdomain_nav(code: str) -> pd.Series:
    """
    v6.8: 根據代碼前綴推測保險公司 MoneyDJ 子網域，逐一嘗試。
    當 tcbbankfund 無此基金時（如 TLZF9 屬台灣人壽、FLFM1 屬富蘭克林）才啟動。
    """
    _code = code.upper().strip()
    portals = []
    for prefix, names in _INSURANCE_SUBDOMAIN_HINTS.items():
        if _code.startswith(prefix):
            portals.extend(names)
    if not portals:
        return pd.Series(dtype=float)

    import datetime as _dt
    today = _dt.date.today()
    start = today - _dt.timedelta(days=400)

    for portal in portals:
        base = f"https://{portal}.moneydj.com"
        # 先試簡單的 wf01/wb02（無需日期參數）
        for path in [f"/w/wf/wf01.djhtm?a={_code}",
                     f"/w/wb/wb02.djhtm?a={_code}"]:
            try:
                r = fetch_url_with_retry(base + path, timeout=6, retries=1)
                if r is None:
                    continue
                s = _parse_nav_html(r.text)
                if len(s) >= 10:
                    print(f"[src_ins] ✅ {_code} @ {portal} wf01/wb02 → {len(s)} 筆")
                    return s
            except Exception as _e:
                print(f"[src_ins] {portal} {path}: {_e}")
        # 再試 yp004002（帶日期）
        params = {"A": _code, "B": start.strftime("%Y%m%d"), "C": today.strftime("%Y%m%d")}
        for page in ["yp010001", "yp010000"]:
            hdr = {**HDR, "Referer": f"{base}/funddj/ya/{page}.djhtm?a={_code}"}
            try:
                r = fetch_url_with_retry(f"{base}/funddj/yf/yp004002.djhtm",
                                         headers=hdr, params=params, timeout=8, retries=1)
                if r is None:
                    continue
                import re as _re_ins
                rows = {}
                soup = BeautifulSoup(r.text, "lxml")
                for tbl in soup.find_all("table"):
                    for row in tbl.find_all("tr"):
                        cells = row.find_all("td")
                        if len(cells) >= 2:
                            dt_t = cells[0].get_text(strip=True)
                            nv_t = cells[1].get_text(strip=True).replace(",", "")
                            if _re_ins.match(r"\d{4}/\d{2}/\d{2}", dt_t):
                                v = safe_float(nv_t)
                                if v:
                                    rows[pd.Timestamp(dt_t)] = v
                if len(rows) >= 10:
                    s = pd.Series(rows).sort_index()
                    print(f"[src_ins] ✅ {_code} @ {portal} yp004002 → {len(s)} 筆")
                    return s
            except Exception as _e:
                print(f"[src_ins] {portal} yp004002: {_e}")
    return pd.Series(dtype=float)


def _fetch_fund_single(code: str, force_refresh: bool = False,
                       page_type: str = "") -> dict:
    """單一代碼的多來源抓取（由 fetch_fund_multi_source 呼叫）"""
    _code = code.upper().strip()
    _page_type = page_type or (
        "yp010000" if _is_domestic_code(_code) else "yp010001"
    )
    result = dict(
        fund_name="", full_key=_code, fund_code=_code,
        category="", risk_level="", dividend_freq="", currency="USD",
        fund_scale="", fund_region="", fund_type="",
        moneydj_div_yield=None,
        investment_target="", fund_rating="", umbrella_fund="",
        mgmt_fee="", is_esg="",
        nav_latest=None, nav_date="",
        year_high_nav=None, year_low_nav=None,
        series=None, dividends=[], perf={}, metrics={},
        risk_metrics={}, holdings={}, error=None,
        data_source="",     # 記錄實際使用的來源
    )

    # ── Step 1: 本地快取（最快，離線也能跑）──────────────────────────
    if not force_refresh:
        cached_s = _cache_load_nav(_code)
        if cached_s is not None and len(cached_s) >= 10:
            result["series"] = cached_s
            result["data_source"] = "cache"

        cached_div = _cache_load_div(_code)
        if cached_div:
            result["dividends"] = cached_div

        cached_meta = _cache_load_meta(_code)
        if cached_meta:
            for k, v in cached_meta.items():
                if v is not None:
                    result[k] = v

        # 快取完整 → 直接計算並回傳
        if (result["series"] is not None and
                len(result["series"]) >= 10 and
                result.get("fund_name")):
            _finish_metrics(result)
            print(f"[orchestrator] 🚀 {_code} 完整快取命中，直接回傳")
            return result

    # ── Step 2: 並行嘗試多來源（NAV）────────────────────────────────
    nav_s = pd.Series(dtype=float)
    nav_source = ""

    # 0. 安聯投信官網（ACTI/ACCP/ACDD 境內基金首選，Colab 友善）
    if _is_domestic_code(_code) and any(_code.startswith(p) for p in ("ACTI","ACCP","ACDD","ACTT")):
        nav_s = _src_allianzgi_nav(_code)
        if len(nav_s) >= 5:
            nav_source = "allianzgi_tw"

    # 2a. FundClear（境外最穩）
    if len(nav_s) < 20:
        nav_s = _src_fundclear_nav(_code)
        if len(nav_s) >= 20:
            nav_source = "FundClear"

    # 2b. 鉅亨網
    if len(nav_s) < 20:
        nav_s = _src_cnyes_nav(_code)
        if len(nav_s) >= 20:
            nav_source = "cnyes"

    # 2c. TCB MoneyDJ（子網域）
    if len(nav_s) < 20:
        nav_s = _src_tcb_nav(_code)
        if len(nav_s) >= 10:
            nav_source = "tcb_moneydj"

    # 2c2. v6.8: 保險公司專屬 MoneyDJ 子網域（TL=台灣人壽, FL=富蘭克林 等）
    if len(nav_s) < 10:
        _ins_s = _src_insurance_subdomain_nav(_code)
        if len(_ins_s) >= 10:
            nav_s = _ins_s
            nav_source = "insurance_subdomain"
            result.setdefault("source_trace", []).append(
                {"source": "insurance_subdomain", "success": True, "nav_count": len(_ins_s)})

    # 2d. www.moneydj.com（主站，最後才試，IP 限制多）
    if len(nav_s) < 10:
        try:
            import datetime as _dt2
            import re as _re4
            base_www = "https://www.moneydj.com/funddj"
            today2 = _dt2.date.today()
            st2 = today2 - _dt2.timedelta(days=400)
            # v13.8: page_type 互換 — 首選失敗自動換頁型重試
            _pages2 = get_page_types_to_try(
                "yp010000" if _is_domestic_code(_code) else "yp010001"
            )
            params_www = {
                "A": _code,
                "B": st2.strftime("%Y%m%d"),
                "C": today2.strftime("%Y%m%d"),
            }
            rw = None
            for _pg2 in _pages2:
                hdr2 = {**HDR,
                        "Referer": f"https://www.moneydj.com/funddj/ya/{_pg2}.djhtm?a={_code}"}
                rw = fetch_url_with_retry(
                    f"{base_www}/yf/yp004002.djhtm",
                    headers=hdr2, params=params_www, timeout=25, retries=2
                )
                if rw and is_valid_moneydj_page(rw.text):
                    print(f"[www_fallback] ✅ {_code} page={_pg2}")
                    break
                print(f"[www_fallback] {_code} page={_pg2} → 無效，換頁型")
                rw = None
            if rw and is_valid_moneydj_page(rw.text):
                soup_w = BeautifulSoup(rw.text, "lxml")
                _www_rows = {}
                for tbl in soup_w.find_all("table"):
                    for row in tbl.find_all("tr"):
                        cells = row.find_all("td")
                        if len(cells) >= 2:
                            dt_t = cells[0].get_text(strip=True)
                            nv_t = cells[1].get_text(strip=True).replace(",", "")
                            if _re4.match(r"\d{4}/\d{2}/\d{2}", dt_t):
                                v = safe_float(nv_t)
                                if v: _www_rows[pd.Timestamp(dt_t)] = v
                if len(_www_rows) >= 10:
                    nav_s = pd.Series(_www_rows).sort_index()
                    nav_source = "moneydj_www"
                    print(f"[src_www] ✅ {_code} {len(nav_s)} 筆")
        except Exception as _we:
            print(f"[src_www] {_code}: {_we}")

    # 2e. SITCA（境內基金備援）
    if len(nav_s) < 10:
        nav_s = _src_sitca_nav(_code)
        if len(nav_s) >= 10:
            nav_source = "sitca"

    # 2f. 近30日 nav 頁直接解析（最終 fallback，yf/yp004002 全被封鎖時使用）
    # 近30日雖然只有約25~30筆，足以計算 Sharpe/標準差
    if len(nav_s) < 10:
        nav_s = _src_nav_30day(_code, _page_type)
        if len(nav_s) >= 10:
            nav_source = "moneydj_nav30"
            print(f"[orchestrator] 📅 {_code} 使用近30日淨值（{len(nav_s)}筆）")

    if len(nav_s) >= 10:
        result["series"]      = nav_s
        result["data_source"] = nav_source
        result.setdefault("source_trace", []).append(
            {"source": nav_source, "success": True, "nav_count": len(nav_s)})
        _cache_save_nav(_code, nav_s)
    else:
        result.setdefault("source_trace", []).append(
            {"source": "nav_all", "success": False,
             "error": f"所有來源均不足10筆（最多:{len(nav_s)}）"})

    # ── Step 3: 基本資料（Meta）─────────────────────────────────────
    meta = {}
    # 境內基金優先安聯投信官網
    if not meta.get("fund_name") and _is_domestic_code(_code):
        meta = _src_allianzgi_meta(_code)
        if meta.get("fund_name"):
            result["source_trace"].append({"source": "allianzgi_meta", "success": True})
    # 優先 TCB MoneyDJ（含年高/年低）
    if not meta.get("fund_name"):
        meta = _src_tcb_meta(_code)
        if meta.get("fund_name"):
            result["source_trace"].append({"source": "tcb_meta", "success": True})
    # 再試 FundClear
    if not meta.get("fund_name"):
        meta = merge_non_empty(meta, _src_fundclear_meta(_code))
        if meta.get("fund_name"):
            result["source_trace"].append({"source": "fundclear_meta", "success": True})
    # 最後 SITCA（境內基金）
    if not meta.get("fund_name"):
        meta = merge_non_empty(meta, _src_sitca_meta(_code))
        if meta.get("fund_name"):
            result["source_trace"].append({"source": "sitca_meta", "success": True})
    # 最終備援：TDCC OpenAPI（境外基金官方登記，MoneyDJ 被封鎖時仍可存取）
    if not meta.get("fund_name") and not _is_domestic_code(_code):
        _tdcc_m = _src_tdcc_meta(_code)
        if _tdcc_m.get("fund_name") or _tdcc_m.get("nav_latest"):
            meta = merge_non_empty(meta, _tdcc_m)
            result["source_trace"].append({"source": "tdcc_meta", "success": True})
            print(f"[orchestrator] 🏛 {_code} TDCC metadata 命中: {_tdcc_m.get('fund_name','')[:25]}")

    if meta:
        # v13.5: 用 merge_non_empty，不讓空值覆蓋前面成功的資料
        result = merge_non_empty(result, meta)
        _cache_save_meta(_code, meta)

    # ── Step 4: 配息資料 ───────────────────────────────────────────
    divs = result.get("dividends") or []
    if not divs:
        divs = _src_tcb_div(_code)
    if not divs:
        divs = _src_fundclear_div(_code)
    if not divs:
        divs = _src_cnyes_div(_code)
    if divs:
        result["dividends"] = divs
        _cache_save_div(_code, divs)
        latest_yield = divs[0].get("yield_pct", 0)
        if latest_yield > 0:
            result["moneydj_div_yield"] = round(latest_yield, 2)

    # ── Step 5: 風險指標（wb07，MoneyDJ 才有）───────────────────────
    try:
        risk_data = fetch_risk_metrics(_code)
        if risk_data:
            result["risk_metrics"] = risk_data
            perf_wb01 = fetch_performance_wb01(_code)
            if perf_wb01:
                result["perf"].update(perf_wb01)
                result["perf_source"] = "wb01"
    except Exception as _re5:
        print(f"[orchestrator] risk_metrics: {_re5}")

    # ── Step 6: 計算 MK 指標 ────────────────────────────────────────
    _finish_metrics(result)

    return result


def _finish_metrics(result: dict):
    """
    v13.5: 計算 calc_metrics 並正確設置最終狀態。
    使用 normalize_result_state() 確保有資料時不顯示全失敗紅字。
    """
    s    = result.get("series")
    divs = result.get("dividends", [])
    code = result.get("fund_code", "?")
    src  = result.get("data_source", "")

    # ── 初始化 source_trace（若無則建立）─────────────────────────────
    if "source_trace" not in result:
        result["source_trace"] = []

    if s is not None and len(s) >= 10:
        try:
            combined_override = dict(result.get("risk_metrics") or {})
            if result.get("year_high_nav"):
                combined_override["year_high_nav"] = result["year_high_nav"]
            if result.get("year_low_nav"):
                combined_override["year_low_nav"]  = result["year_low_nav"]
            result["metrics"] = calc_metrics(s, divs, risk_override=combined_override)
            result["source_trace"].append({"source": "calc_metrics", "success": True})
            print(f"[metrics] ✅ {code} 指標計算完成（{len(s)} 筆，src:{src}）")
        except Exception as _ce:
            result["source_trace"].append(
                {"source": "calc_metrics", "success": False, "error": str(_ce)[:60]})
            result["error"] = f"指標計算異常：{str(_ce)[:80]}"
            print(f"[metrics] ❌ calc_metrics: {_ce}")
    elif s is not None:
        result["source_trace"].append(
            {"source": "nav_series", "success": False,
             "error": f"只有 {len(s)} 筆（需≥10）"})
    else:
        result["source_trace"].append(
            {"source": "nav_series", "success": False, "error": "無淨值序列"})

    # ── 用 normalize_result_state 統一決定最終狀態 ────────────────────
    # 這是關鍵：有任何資料就不應顯示全失敗
    result = normalize_result_state(result)
    print(f"[metrics] {code} → status={result.get('status')} "
          f"error={str(result.get('error',''))[:40]} "
          f"warning={str(result.get('warning',''))[:40]}")


# ═══════════════════════════════════════════════════════
# MoneyDJ URL 一站式爬蟲（主要入口）
# 只需要貼網址即可取得所有 MK 分析所需資料
# ═══════════════════════════════════════════════════════
def fetch_fund_from_moneydj_url(url: str) -> dict:
    """
    輸入任何 MoneyDJ 基金頁面網址（或純代碼如 tlzf9），
    自動抓取：基本資料、近一年淨值歷史、績效、配息。

    回傳格式（與 fetch_fund_by_key 相容）：
    {
      "fund_name": str, "full_key": str, "fund_code": str,
      "category": str, "risk_level": str, "dividend_freq": str,
      "currency": str, "fund_scale": str,
      "nav_latest": float, "nav_date": str,
      "series": pd.Series,        # 日期→淨值，可直接給 calc_metrics
      "dividends": list,           # [{date, amount, yield_pct}]
      "perf": dict,                # {1M, 3M, 6M, 1Y, 3Y, 5Y, sharpe, beta, std}
      "metrics": dict,             # calc_metrics 結果
      "error": str or None,
    }
    """
    import re as _re

    # ── 1. 解析代碼 ──────────────────────────────────────
    result = dict(fund_name="", full_key="", fund_code="", category="",
                  risk_level="", dividend_freq="", currency="USD",
                  fund_scale="", fund_region="", fund_type="",
                  moneydj_div_yield=None,
                  investment_target="", fund_rating="", umbrella_fund="",
                  mgmt_fee="", is_esg="",
                  nav_latest=None, nav_date="",
                  year_high_nav=None, year_low_nav=None,
                  series=None, dividends=[], perf={}, metrics={},
                  risk_metrics={}, holdings={}, error=None)

    # ── v13.4: parse_moneydj_input — 保留 page_type，不再丟失境內路由資訊 ──
    _input_info = parse_moneydj_input(url)
    code = _input_info.get("code", "")

    # 若輸入只是純代碼（非 URL），嘗試 regex 補救
    if not code:
        import re as _re
        m = _re.search(r"[?&][aA]=([A-Z0-9]{3,25}(?:-[A-Z0-9]{3,20})?)", url, _re.I)
        if m:
            code = m.group(1).upper()
        elif _re.match(r"^[A-Z0-9]{3,25}(-[A-Z0-9]{3,20})?$", url.strip(), _re.I):
            code = url.strip().upper()
    if not code:
        result["error"] = "無法解析代碼，請輸入 MoneyDJ 網址或代碼（如 tlzf9）"
        return result

    _page_type = _input_info.get("page_type", "")   # 保留原始頁型（URL 輸入最準）

    # 查 mapping table：補全 public_code 與 page_type
    _mapping = load_fund_code_mapping()
    if code in _mapping:
        _m = _mapping[code]
        code       = _m.get("public_code", code)
        _page_type = _m.get("page_type", _page_type)
        print(f"[fetch] mapping 命中：{_input_info['code']} → {code} (page:{_page_type})")

    # 若 page_type 仍空白（純代碼輸入），自動推斷
    if not _page_type:
        _page_type = "yp010000" if _is_domestic_code(code) else "yp010001"
        print(f"[fetch] page_type 自動推斷：{code} → {_page_type}")

    result["full_key"]  = code
    result["fund_code"] = code

    # ── Step 1: 直接抓使用者提供的原始 URL（最高優先）───────────────
    if _input_info.get("is_url") and _input_info.get("full_url"):
        _direct = _src_direct_moneydj_url(_input_info["full_url"])
        if _direct.get("fund_name") or _direct.get("nav_latest"):
            for _k, _v in _direct.items():
                if _v not in (None, "", {}, []):
                    result[_k] = _v
            print(f"[fetch] direct_url meta: {result.get('fund_name','')[:20]} NAV={result.get('nav_latest')}")

    # ── Step 2: 多來源 Orchestrator（帶 page_type）──────────────────
    try:
        _ms_result = fetch_fund_multi_source(
            code, force_refresh=False, page_type=_page_type
        )
        # 合併策略：series/metrics/perf 優先用 multi_source，meta 保留 direct_url 結果
        # v13.5: merge_non_empty，保留 direct_url meta，補入 multi_source 的 series/metrics
        if _ms_result:
            _protect = ("fund_name","currency","risk_level","nav_latest","nav_date",
                        "year_high_nav","year_low_nav")
            _saved_meta = {k: result[k] for k in _protect if result.get(k)}
            result = merge_non_empty(result, _ms_result)
            for _k, _v in _saved_meta.items():
                if _v:
                    result[_k] = _v
            result = normalize_result_state(result)

        _s_ok = result.get("series") is not None and len(result.get("series", [])) >= 10
        _m_ok = result.get("fund_name") and result.get("nav_latest")
        if _s_ok or _m_ok:
            print(f"[fetch] ✅ 成功（src:{result.get('data_source','')} "
                  f"status:{result.get('status','')} page:{_page_type}）")
            return result
        else:
            print(f"[fetch] ⚠️ 不足，繼續原始流程（page:{_page_type}）")
    except Exception as _ms_e:
        print(f"[fetch] 多來源異常: {_ms_e}，繼續原始流程")

    # ── 判斷境內/境外基金（影響爬蟲路徑）──────────────────
    # 境內基金（投信，如聯博/安聯投信/富達投信）：
    #   www.moneydj.com/funddj/ya/yp010000.djhtm?a=ACTI71
    # 境外基金（ISIN/境外代碼）：
    #   www.moneydj.com/funddj/ya/YP081000.djhtm?a=TLZF9
    # tcbbankfund 子網域對境內/境外都相容，且 Colab IP 封鎖較少

    _portal_auto = ""
    _code_upper = code.upper()

    # BASE 一律優先走 tcbbankfund（對 Colab IP 最友善）
    BASE = "https://tcbbankfund.moneydj.com/funddj"
    BASE_LIST = [
        "https://tcbbankfund.moneydj.com/funddj",
        "https://www.moneydj.com/funddj",
    ]
    print(f"[fetch] code={code}  BASE={BASE}")

    # ── 2. 基本資料 yp011001（tcbbankfund 優先，www fallback）──
    try:
        # v14.4: 境內用 yp011000，境外用 yp011001（從實際 HTML 確認）
        _info_page_type = "yp011000" if _is_domestic_code(code, _page_type) else "yp011001"
        _info_pages_try = [_info_page_type]
        # 互換備用：yp011000 失敗試 yp011001，反之亦然
        _info_pages_try.append("yp011001" if _info_page_type == "yp011000" else "yp011000")

        _info_urls = []
        for _ip in _info_pages_try:
            _info_urls.extend([
                f"https://tcbbankfund.moneydj.com/funddj/yp/{_ip}.djhtm?a={code}",
                f"https://www.moneydj.com/funddj/yp/{_ip}.djhtm?a={code}",
            ])
        r = None
        for _iu in _info_urls:
            # v14.4: fetch_url_with_retry (Big5 統一解碼)
            r = fetch_url_with_retry(_iu, timeout=20, retries=1)
            if r is not None and len(r.text) > 500:
                break
        if r is not None and len(r.text) > 500:
            soup = BeautifulSoup(r.text, "lxml")
            for tbl in soup.find_all("table"):
                txt = tbl.get_text()
                if "基金名稱" not in txt: continue
                # 建立 key→value map（支援多欄 row: col0=key1, col1=val1, col2=key2, col3=val2）
                rows_map = {}
                for row in tbl.find_all("tr"):
                    cells = row.find_all("td")
                    # 單欄對 (key, value)
                    if len(cells) == 2:
                        k = cells[0].get_text(strip=True)
                        v = cells[1].get_text(strip=True)
                        rows_map[k] = v
                    # 雙欄對 (key1, val1, key2, val2)
                    elif len(cells) >= 4:
                        for i in range(0, len(cells)-1, 2):
                            k = cells[i].get_text(strip=True)
                            v = cells[i+1].get_text(strip=True)
                            if k: rows_map[k] = v
                result["fund_name"]       = rows_map.get("基金名稱", "")
                result["currency"]        = rows_map.get("計價幣別", "USD").replace(" ","")
                result["risk_level"]      = rows_map.get("風險報酬等級", "").replace(" ","")
                result["dividend_freq"]   = rows_map.get("配息頻率", "").replace(" ","")
                result["fund_scale"]      = rows_map.get("基金規模", "")
                result["category"]        = rows_map.get("投資標的", rows_map.get("基金類型", "")).replace(" ","")
                result["fund_region"]     = rows_map.get("投資區域", "").replace(" ","")
                result["fund_type"]       = rows_map.get("基金類型", "").replace(" ","")
                result["investment_target"]= rows_map.get("投資標的", "").replace(" ","")
                result["fund_rating"]     = rows_map.get("基金評等", "")
                result["umbrella_fund"]   = rows_map.get("傘型架構", "").replace(" ","")
                result["mgmt_fee"]        = rows_map.get("最高經理費(%)", "")
                result["is_esg"]          = rows_map.get("是否為ESG", "")
                # latest NAV + 年度高低點 from this page
                for row in tbl.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 4:
                        # 行格式: 淨值日期 | 淨值 | 最高淨值(年) | 最低淨值(年)
                        dt = cells[0].get_text(strip=True)
                        if _re.match(r"\d{4}/\d{2}/\d{2}", dt):
                            try:
                                result["nav_date"]    = dt
                                result["nav_latest"]   = safe_float(cells[1].get_text(strip=True))
                                result["year_high_nav"] = safe_float(cells[2].get_text(strip=True))
                                result["year_low_nav"]  = safe_float(cells[3].get_text(strip=True))
                                print(f"[fetch_basic] 年高={result['year_high_nav']} 年低={result['year_low_nav']}")
                            except: pass
                    elif len(cells) >= 2:
                        dt = cells[0].get_text(strip=True)
                        if _re.match(r"\d{4}/\d{2}/\d{2}", dt):
                            try:
                                result["nav_date"]   = dt
                                result["nav_latest"] = float(cells[1].get_text(strip=True).replace(",",""))
                            except: pass
                break
    except Exception as e:
        print(f"[fetch_basic] {e}")

    # ── 3. 淨值歷史（近30日）→ 再查詢歷史區間 ──
    # v13.8: page_type 互換 — 首選失敗自動換 yp010000 ↔ yp010001
    try:
        _primary_nav_page = _page_type if _page_type else (
            "yp010000" if _is_domestic_code(code) else "yp010001"
        )
        _nav_page_candidates = get_page_types_to_try(_primary_nav_page)
        _nav_bases = [
            BASE,
            TCB_BASE + "/funddj",
            "https://www.moneydj.com/funddj",
            "https://tcbbankfund.moneydj.com/funddj",
        ]
        _nav_r = None
        # 外層：依次嘗試各 base；若回傳無效，換頁型再試一遍
        for _nav_pg in _nav_page_candidates:
            if _nav_r is not None:
                break
            for _nb_url in _nav_bases:
                try:
                    _nav_r = fetch_url_with_retry(
                        f"{_nb_url}/ya/{_nav_pg}.djhtm?a={code}",
                        timeout=25, retries=1
                    )
                    if _nav_r is not None:
                        print(f"[nav30] ✅ {code} page={_nav_pg} base={_nb_url[:30]}")
                        break
                except Exception as _ne:
                    print(f"[nav fallback] {_nb_url}: {_ne}")
                    continue
        r = _nav_r
        # v13.6: fetch_url_with_retry 不回 status_code，直接判斷 r is not None
        if r is not None:
            soup = BeautifulSoup(r.text, "lxml")
            nav_rows = {}
            for tbl in soup.find_all("table"):
                for row in tbl.find_all("tr"):
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        date_txt = cells[0].get_text(strip=True)
                        nav_txt  = cells[1].get_text(strip=True).replace(",","")
                        if _re.match(r"\d{2}/\d{2}", date_txt) and _re.match(r"[\d.]+$", nav_txt):
                            try: nav_rows[date_txt] = float(nav_txt)
                            except: pass
            # 轉換日期（MoneyDJ 近期只顯示 MM/DD，需補年份）
            import datetime as _dt
            today = _dt.date.today()
            parsed = {}
            for mmdd, v in nav_rows.items():
                try:
                    mo, da = int(mmdd.split("/")[0]), int(mmdd.split("/")[1])
                    yr = today.year if (mo, da) <= (today.month, today.day) else today.year - 1
                    parsed[_dt.date(yr, mo, da)] = v
                except: pass

            # 再查詢整年歷史（使用查詢 endpoint）
            end_dt   = today
            start_dt = today - _dt.timedelta(days=400)
            # v13.8: page_type 互換 — 首選失敗自動換頁型重試
            _hist_pages = get_page_types_to_try(
                "yp010000" if _is_domestic_code(code) else "yp010001"
            )
            _hist_params = {
                "A": code, "B": start_dt.strftime("%Y%m%d"), "C": end_dt.strftime("%Y%m%d")
            }
            _hist_urls = [
                f"{BASE}/yf/yp004002.djhtm",
                f"{TCB_BASE}/funddj/yf/yp004002.djhtm",
                f"https://www.moneydj.com/funddj/yf/yp004002.djhtm",
                f"https://tcbbankfund.moneydj.com/funddj/yf/yp004002.djhtm",
            ]
            r2 = None
            for _hpage in _hist_pages:
                if r2 is not None:
                    break
                hdr_ext = {**HDR,
                           "Referer": f"https://www.moneydj.com/funddj/ya/{_hpage}.djhtm?a={code}"}
                for _hu in _hist_urls:
                    try:
                        _r2_try = fetch_url_with_retry(
                            _hu, headers=hdr_ext,
                            params=_hist_params, timeout=25, retries=2
                        )
                        if _r2_try is not None:
                            r2 = _r2_try
                            print(f"[hist] ✅ {code} page={_hpage}")
                            break
                    except Exception as _he:
                        print(f"[hist fallback] {_hu}: {_he}")
                        continue
            if r2 is not None:
                soup2 = BeautifulSoup(r2.text, "lxml")
                hist_rows = {}
                for tbl in soup2.find_all("table"):
                    for row in tbl.find_all("tr"):
                        cells = row.find_all("td")
                        if len(cells) >= 2:
                            dt_txt  = cells[0].get_text(strip=True)
                            nav_txt = cells[1].get_text(strip=True).replace(",","")
                            if _re.match(r"\d{4}/\d{2}/\d{2}", dt_txt) and _re.match(r"[\d.]+$", nav_txt):
                                try:
                                    import pandas as _pd
                                    hist_rows[_pd.to_datetime(dt_txt)] = float(nav_txt)
                                except: pass
                if len(hist_rows) >= 20:
                    import pandas as _pd
                    result["series"] = _pd.Series(hist_rows).sort_index()
                    print(f"[fetch_nav_hist] ✅ {len(result['series'])} 筆")

            # v14.4: 若歷史查詢失敗，用近30日資料（parsed 已是 date key）
            if result["series"] is None and len(parsed) >= 5:
                import pandas as _pd
                try:
                    _s30 = _pd.Series({_pd.Timestamp(k): v for k,v in parsed.items()}).sort_index()
                    result["series"] = _s30
                    print(f"[fetch_nav_30] ✅ {len(result['series'])} 筆（近30日）")
                except Exception as _ps_e:
                    print(f"[fetch_nav_30] 轉換失敗: {_ps_e}")
                    # 最後備援：呼叫 _src_nav_30day
                    _s30b = _src_nav_30day(code, _page_type)
                    if len(_s30b) >= 5:
                        result["series"] = _s30b
                        print(f"[fetch_nav_30b] ✅ {len(_s30b)} 筆")
    except Exception as e:
        print(f"[fetch_nav] {e}")

    # ── 4. 績效評比 wb07 (標準差/Sharpe/Alpha/Beta/R²/Tracking Error) ──
    try:
        risk_data = fetch_risk_metrics(code)
        result["risk_metrics"] = risk_data
        # 從 peer_compare 取年報酬率 → perf["1Y"]（備援）
        peer = risk_data.get("peer_compare", {})
        for row_name, row_vals in peer.items():
            if "基金" in row_name or (code.upper() in row_name.upper()):
                try:
                    yr_txt = str(list(row_vals.values())[0]).replace("%","")
                    result["perf"]["1Y"] = float(yr_txt)
                except: pass
                break
    except Exception as e:
        print(f"[fetch_risk] {e}")

    # ── 4c. 含息總報酬率 wb01（優先使用，MoneyDJ 說明：已考慮配息）─────
    try:
        perf_wb01 = fetch_performance_wb01(code)
        if perf_wb01:
            # wb01 資料覆蓋 peer_compare 估算值（更精確）
            for k, v in perf_wb01.items():
                result["perf"][k] = v
            result["perf_source"] = "wb01"
    except Exception as e:
        print(f"[fetch_perf_wb01] {e}")

    # ── 4b. 持股（產業配置 + 前10大持股）─────────────────
    try:
        holdings_data = fetch_holdings(code)
        result["holdings"] = holdings_data
    except Exception as e:
        print(f"[fetch_holdings] {e}")

    # ── 5. 配息 wb05 ──────────────────────────────────────
    try:
        # v14.4: 境內用 funddividend，境外用 wb05（與 _src_tcb_div 邏輯一致）
        _is_dom_div = _is_domestic_code(code, _page_type)
        _div_page = "funddividend" if _is_dom_div else "wb05"
        _div_fallback = "wb05" if _is_dom_div else "funddividend"
        _wb05_r = None
        for _div_pg in [_div_page, _div_fallback]:
            for _db in [BASE, TCB_BASE + "/funddj",
                        "https://www.moneydj.com/funddj",
                        "https://tcbbankfund.moneydj.com/funddj"]:
                # v14.4: fetch_url_with_retry (Big5)
                _try_r = fetch_url_with_retry(f"{_db}/yp/{_div_pg}.djhtm?a={code}",
                                              timeout=20, retries=1)
                if _try_r is not None:
                    _wb05_r = _try_r
                    print(f"[div] ✅ {code} 配息頁={_div_pg}")
                    break
            if _wb05_r is not None:
                break
        r = _wb05_r
        if r is not None:
            soup = BeautifulSoup(r.text, "lxml")
            for tbl in soup.find_all("table"):
                txt = tbl.get_text()
                if "配息基準日" not in txt and "除息日" not in txt: continue
                rows = tbl.find_all("tr")[1:]
                for row in rows[:36]:  # 最多3年
                    cols = [td.get_text(strip=True) for td in row.find_all("td")]
                    if len(cols) < 5: continue
                    try:
                        # v14.4: col[3]=TEXT"配息", col[4]=配息金額, col[5]=年化率, col[6]=幣別
                        if len(cols) < 5: continue
                        if "/" not in cols[0]: continue   # 跳過非日期行
                        _amt = safe_float(cols[4])
                        if _amt is None or _amt <= 0 or _amt > 1000: continue
                        _yld = safe_float(cols[5]) or 0.0 if len(cols) > 5 else 0.0
                        _cur = (cols[6].strip() if len(cols) > 6 and cols[6].strip()
                                else result.get("currency", "USD"))
                        result["dividends"].append({
                            "date":      cols[0],
                            "ex_date":   cols[1],
                            "pay_date":  cols[2],
                            "amount":    _amt,
                            "yield_pct": _yld,
                            "currency":  _cur,
                        })
                    except: pass
                # v10: 取最新一筆 年化配息率% 作為 MoneyDJ 官方值
                if result["dividends"]:
                    latest_yield = result["dividends"][0].get("yield_pct", 0)
                    if latest_yield > 0:
                        result["moneydj_div_yield"] = round(latest_yield, 2)
                        print(f"[wb05] MoneyDJ 年化配息率: {latest_yield:.2f}%")
                break
    except Exception as e:
        print(f"[fetch_div] {e}")

    # ── 6. 計算 MK 指標（優先使用 wb07 標準差）────────────
    # ── 最終備援：使用 fetch_nav() 完整 TCB 多路徑爬取 ─────────────
    if result["series"] is None or len(result["series"]) < 10:
        try:
            _fallback_s = fetch_nav(code, portal="")
            if len(_fallback_s) >= 10:
                result["series"] = _fallback_s
                result["error"] = None
                print(f"[fetch_nav_fallback] \u2705 {len(_fallback_s)} \u7b46")
        except Exception as _fnf_e:
            print(f"[fetch_nav_fallback] {_fnf_e}")

    if result["series"] is not None and len(result["series"]) >= 10:
        # 合併 risk_metrics 與 year_high/low 一起傳入
        try:
            combined_override = dict(result.get("risk_metrics") or {})
            if result.get("year_high_nav"): combined_override["year_high_nav"] = result["year_high_nav"]
            if result.get("year_low_nav"):  combined_override["year_low_nav"]  = result["year_low_nav"]
            result["metrics"] = calc_metrics(
                result["series"], result["dividends"],
                risk_override=combined_override
            )
        except Exception as _cm_e:
            print(f"[calc_metrics] {_cm_e}")
            result["error"] = f"指標計算異常：{str(_cm_e)[:60]}"
    elif result["series"] is not None:
        result["error"] = f"只取到 {len(result['series'])} 筆淨值（建議≥10）"
    else:
        # v10.7.1 改善：淨值歷史失敗時，嘗試從 perf/risk 數據重建部分指標
        # 並給出明確的操作指引
        _has_perf = bool(result.get("perf"))
        _has_risk = bool(result.get("risk_metrics"))
        if _has_perf or _has_risk:
            result["error"] = (
                "⚠️ 淨值歷史抓取失敗（MoneyDJ 可能封鎖伺服器 IP）\n"
                "但已取得部分績效/風險數據，可繼續查看。\n"
                "💡 建議：直接貼 MoneyDJ 完整網址以取得最佳結果"
            )
        else:
            # ── 記憶體快照 fallback（網路斷線時顯示上次成功資料）──
            _snap_key = (code or result.get("full_key", "")).upper()
            if _snap_key and _snap_key in _FUND_SNAPSHOT:
                _snap = _FUND_SNAPSHOT[_snap_key]
                result.update({k: v for k, v in _snap.items() if v})
                result["error"]   = None
                result["warning"] = "⚠️ 網路暫時無法連線，顯示上次快照資料（數值可能稍舊）"
                print(f"[snapshot] ✅ {_snap_key} 使用記憶體快照")
            else:
                result["error"] = (
                    "❌ 無法取得基金資料（所有來源均失敗）\n"
                    "💡 解決方案：\n"
                    "① 直接貼 MoneyDJ 完整網址（比代碼更準確）：\n"
                    "   境內基金：www.moneydj.com/funddj/ya/yp010000.djhtm?a={code}\n"
                    "   境外基金：www.moneydj.com/funddj/ya/yp010001.djhtm?a={code}\n"
                    "② 於下方手動填入淨值、配息數據"
                ).format(code=result.get("full_key","???"))

    # ── 成功取得資料時更新記憶體快照 ──────────────────────────────────
    _snap_key = (code or result.get("full_key", "")).upper()
    if _snap_key and result.get("series") is not None and result.get("error") is None:
        # 快照不含 series（節省記憶體），保留 metrics/perf/fund_name 等輕量欄位
        _FUND_SNAPSHOT[_snap_key] = {
            k: v for k, v in result.items()
            if k not in ("series",) and v not in (None, [], {})
        }
        print(f"[snapshot] 💾 {_snap_key} 快照已更新")

    return result


def search_fundclear(keyword: str) -> list:
    """用 fundclear REST API 搜尋境外基金（Colab 可存取）"""
    results = []
    seen = set()
    kw = keyword.strip()
    try:
        body = {
            "fundName": kw, "fundCode": "",
            "fundAsset": "all", "fundAssetSub": "all",
            "fundInv": "all", "invArea": "all", "invAreaSub": "all",
            "agentCode": "all", "orgCode": "all",
            "pageNum": 1, "pageSize": 20,
        }
        r = requests.post(
            "https://www.fundclear.com.tw/api/offshore/fund-info/fund-search/query",
            json=body, headers=HDR_JSON, timeout=20
        )
        print(f"[fundclear] status={r.status_code}")
        if r.status_code == 200:
            d = r.json()
            # 嘗試各種回傳結構
            raw = d.get("data") or {}
            fund_list = (raw.get("list") or raw.get("fundList") or
                         (raw if isinstance(raw, list) else []))
            for item in fund_list:
                code = str(item.get("fundCode") or item.get("code") or "")
                name = str(item.get("fundName") or item.get("name") or "")
                nav  = float(item.get("nav") or item.get("latestNav") or 0)
                if not code or not name or code in seen: continue
                seen.add(code)
                portal = "allianz" if ("安聯" in name or "AGIF" in code) else ""
                results.append({"full_key": code, "name": name,
                                 "portal": portal, "nav": nav, "source": "fundclear"})
            print(f"[fundclear] {len(results)} 筆")
    except Exception as e:
        print(f"[fundclear] ERR: {e}")
    return results


def search_moneydj_by_name(keyword: str) -> list:
    """搜尋基金：① fundclear API → ② MoneyDJ 選單 → ③ fundsearch"""
    kw = keyword.strip()
    kw_up = kw.upper()
    results = []
    seen = set()

    # ① fundclear
    for r in search_fundclear(kw):
        if r["full_key"] not in seen:
            seen.add(r["full_key"]); results.append(r)

    # ② MoneyDJ fund-page.html 選單
    for portal_name, url in [
        ("allianz", "https://tcbbankfund.moneydj.com/fund-page.html")  # Fix: correct subdomain,
        ("chubb",   "https://chubb.moneydj.com/fund-page.html?sUrl=$W$HTML$SELECT]DJHTM"),
    ]:
        try:
            r = requests.get(url, headers=HDR, timeout=20)
            if r.status_code != 200: continue
            # v13.3: 同時支援 TLZF9 / ACTI98（無 dash）和 ABC-XYZ123（有 dash）
            for val, text in re.findall(
                r'value="([A-Z0-9a-z]{4,25}(?:-[A-Z0-9a-z]{3,20})?)"[^>]*>([^<]+)<',
                r.text, re.IGNORECASE
            ):
                if kw_up in text.upper():
                    fk = val.upper()
                    if fk in seen: continue
                    seen.add(fk)
                    name = re.sub(r"^[A-Z0-9]{3,20}\s*[-\u2013]\s*", "",
                                  text.strip(), flags=re.IGNORECASE).strip()
                    results.append({"full_key": fk, "name": name or text.strip(),
                                    "portal": portal_name, "nav": 0.0, "source": "moneydj_menu"})
        except Exception as e:
            print(f"[fund_page {portal_name}] ERR: {e}")

    # ③ fundsearch 備援
    if not results:
        try:
            url = f"https://www.moneydj.com/funddjx/fundsearch.xdjhtm?keyword={requests.utils.quote(kw)}"
            r = requests.get(url, headers=HDR, timeout=15)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, "lxml")
                for tbl in soup.find_all("table"):
                    for row in tbl.find_all("tr")[1:]:
                        fk = ""; name = ""
                        for a in row.find_all("a", href=True):
                            mx = re.search(r"[aA]=([A-Z0-9a-z]{3,25})", a["href"])
                            if mx: fk = mx.group(1); name = a.get_text(strip=True); break
                        if fk and len(name) >= 3 and fk not in seen:
                            seen.add(fk)
                            results.append({"full_key": fk, "name": name,
                                            "portal": "", "nav": 0.0, "source": "mj_search"})
        except Exception as e:
            print(f"[fundsearch] ERR: {e}")

    print(f"[search_total] {kw!r} → {len(results)} 筆")
    return results[:15]


def _parse_nav_html(html: str) -> pd.Series:
    """解析 MoneyDJ 淨值 HTML，回傳 pd.Series (date→float)"""
    soup = BeautifulSoup(html, "lxml")
    rows_data = []
    for tbl in soup.find_all("table"):
        txt = tbl.get_text()
        if not re.search(r"\d{2}/\d{2}", txt):
            continue
        for row in tbl.find_all("tr"):
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cols) < 2: continue
            try:
                ds = cols[0].strip()
                if re.match(r"^\d{2}/\d{2}$", ds):
                    import datetime
                    ds = f"{datetime.date.today().year}/{ds}"
                d = pd.to_datetime(ds)
                v = float(cols[1].replace(",", ""))
                if 0.01 < v < 100000:
                    rows_data.append((d, v))
            except:
                pass
    if rows_data:
        return pd.Series({r[0]: r[1] for r in rows_data}).sort_index().dropna()
    return pd.Series(dtype=float)


def fetch_nav(full_key: str, portal: str = "") -> pd.Series:
    """
    取基金淨值歷史。
    portal 子網域 → tcbbankfund（境內/境外通用）→ moneydj 主站（境外用 yp004001）
    """
    mj_short = full_key.split("-")[-1] if "-" in full_key else full_key
    _is_dom = _is_domestic_code(full_key)
    urls = []
    if portal in PORTAL_CFG:
        base = PORTAL_CFG[portal]["base_url"]
        urls.append(f"{base}/w/wf/wf01.djhtm?a={full_key}")
    urls += [
        f"{TCB_BASE}/w/wb/wb02.djhtm?a={full_key}",
        f"{TCB_BASE}/w/wf/wf01.djhtm?a={full_key}",
    ]
    # yp004001 = 境外基金淨值歷史頁（無日期 param 的簡單路徑）
    # 境內基金無此頁，靠 wf01/wb02 子網域 或 _src_tcb_nav 的 yp004002 段
    if not _is_dom:
        urls += [
            f"https://www.moneydj.com/funddj/yf/yp004001.djhtm?a={full_key}",
            f"https://www.moneydj.com/funddj/yf/yp004001.djhtm?a={mj_short}",
        ]
    for url in urls:
        try:
            r = requests.get(url, headers=HDR, timeout=25)
            print(f"[fetch_nav] {url[:65]} → {r.status_code}")
            if r.status_code != 200: continue
            s = _parse_nav_html(r.text)
            if len(s) >= 10:
                print(f"[fetch_nav] ✅ {len(s)} 筆")
                return s
        except Exception as e:
            print(f"[fetch_nav] ERR: {e}")
    return pd.Series(dtype=float)

def fetch_div(full_key: str, portal: str = "") -> list:
    divs = []
    urls = []
    if portal in PORTAL_CFG:
        base = PORTAL_CFG[portal]["base_url"]
        urls.append(base + PORTAL_CFG[portal]["div_path"].format(fk=full_key))
    mj = full_key.split("-")[-1] if "-" in full_key else full_key
    _is_dom = _is_domestic_code(full_key)
    # yp004003 = 境外基金配息頁；境內基金使用 funddividend（子網域通用）
    if not _is_dom:
        urls += [
            f"https://www.moneydj.com/funddj/yf/yp004003.djhtm?a={full_key}",
            f"https://www.moneydj.com/funddj/yf/yp004003.djhtm?a={mj}",
        ]
    else:
        urls += [
            f"https://tcbbankfund.moneydj.com/funddj/yp/funddividend.djhtm?a={full_key}",
            f"https://www.moneydj.com/funddj/yp/funddividend.djhtm?a={full_key}",
        ]
    for url in urls:
        try:
            r = requests.get(url, headers=HDR, timeout=20)
            if r.status_code != 200: continue
            soup = BeautifulSoup(r.text, "lxml")
            for tbl in soup.find_all("table"):
                if not any(k in tbl.get_text() for k in ["配息","除息","配發"]): continue
                for row in tbl.find_all("tr")[1:]:
                    cols = [td.get_text(strip=True) for td in row.find_all("td")]
                    if len(cols) >= 2:
                        try:
                            d = pd.to_datetime(cols[0])
                            amt = 0.0
                            for c in cols[1:]:
                                nums = re.findall(r"[\d.]+",c.replace(",",""))
                                if nums:
                                    v = float(nums[0])
                                    if 0.0001 < v < 100: amt=v; break
                            if amt > 0: divs.append({"date":str(d)[:10],"amount":amt})
                        except: pass
            if divs: break
        except Exception as e:
            print(f"[div] {e}")
    seen=set(); out=[]
    for d in sorted(divs, key=lambda x:x["date"], reverse=True):
        if d["date"] not in seen: seen.add(d["date"]); out.append(d)
    return out[:24]


# ════════════════════════════════════════════════════════════
# MK 指標計算
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
# 績效評比（wb07.djhtm）: 標準差/Sharpe/Beta/同類排名
# ════════════════════════════════════════════════════════════
def _fetch_domestic_perf(code: str) -> dict:
    """
    v14.0: 境內基金績效資料取得。

    【重要發現】境內基金 MoneyDJ 頁面結構：
    - yp020000.djhtm?a=BFxxxx  → 整家公司旗下所有基金清單（Sharpe 全顯示 N/A）
    - 根本沒有 wb01/wb05/wb07  → 含息報酬率/Sharpe 不存在於境內頁面
    - 唯一有意義的績效資料：從淨值序列自行計算（calc_metrics 會處理）

    因此這個函式改為：嘗試抓 yp020000 的績效摘要表，
    若抓不到有效數字（N/A），則回傳空 dict（讓 calc_metrics 自己算）。
    """
    perf = {}
    import re as _re_dp
    # 嘗試從 yp020000 抓績效（注意：需要公司代碼 BFxxxx 而非基金代碼）
    # 實際上境內基金的 Sharpe/含息報酬 在 MoneyDJ 顯示 N/A
    # 程式會從淨值序列自動計算，所以直接回傳空值即可
    for base in ["https://tcbbankfund.moneydj.com/funddj",
                 "https://www.moneydj.com/funddj"]:
        try:
            r = fetch_url_with_retry(
                f"{base}/yp/yp020000.djhtm?a={code}", timeout=15)
            if r is None:
                continue
            soup = BeautifulSoup(r.text, "lxml")
            for tbl in soup.find_all("table"):
                txt = tbl.get_text()
                if "報酬" not in txt and "績效" not in txt:
                    continue
                for row in tbl.find_all("tr"):
                    cells = [td.get_text(strip=True) for td in row.find_all("td")]
                    if len(cells) < 2:
                        continue
                    for key, names in [
                        ("1M", ["1個月","近1月"]),
                        ("3M", ["3個月","近3月"]),
                        ("6M", ["6個月","近6月","半年"]),
                        ("1Y", ["1年","近1年","今年"]),
                        ("3Y", ["3年","近3年"]),
                    ]:
                        if any(n in cells[0] for n in names):
                            v = safe_float(cells[1])
                            if v is not None:   # N/A → None → 跳過
                                perf[key] = v
            if perf:
                print(f"[domestic_perf] ✅ {code} {list(perf.keys())}")
                return perf
        except Exception as e:
            print(f"[domestic_perf] {code}: {e}")
    # 境內基金績效需從淨值序列計算，此處不強制要求
    print(f"[domestic_perf] {code} → 無法從頁面取得，將從淨值序列計算")
    return perf


def fetch_performance_wb01(code: str) -> dict:
    """
    v13.9: 境外基金用 wb01（含息報酬率），境內基金用 yp020000（績效頁）。
    境內基金 MoneyDJ 根本沒有 wb01 頁面，必須改走不同路徑。
    """
    # 境內基金：用 yp020000 績效頁取代 wb01
    if _is_domestic_code(code):
        return _fetch_domestic_perf(code)
    # 境外基金：正常走 wb01（含息報酬率）
    import re as _re
    out = {}
    BASE = "https://www.moneydj.com/funddj"
    TCB  = "https://tcbbankfund.moneydj.com"
    urls = [
        f"{TCB}/w/wb/wb01.djhtm?a={code}",
        f"{BASE}/yp/wb01.djhtm?a={code}",
        f"{TCB}/w/wb/wb01.djhtm?a={code.lower()}",
    ]
    PERIOD_MAP = {
        "一個月":"1M","三個月":"3M","六個月":"6M",
        "一年":"1Y","二年":"2Y","三年":"3Y","五年":"5Y",
        "1個月":"1M","3個月":"3M","6個月":"6M",
        "1M":"1M","3M":"3M","6M":"6M","1Y":"1Y","3Y":"3Y","5Y":"5Y",
        "1月":"1M","3月":"3M","6月":"6M",
    }
    for url in urls:
        try:
            hdr_ref = {**HDR, "Referer": f"{BASE}/yp/wb01.djhtm"}
            # v14.1: 用 fetch_url_with_retry，統一 Big5 解碼
            r = fetch_url_with_retry(url, headers=hdr_ref, timeout=25, retries=2)
            if r is None: continue
            soup = BeautifulSoup(r.text, "lxml")

            # ── Strategy 1: row label contains period name ──────
            for tbl in soup.find_all("table"):
                txt = tbl.get_text()
                if "報酬率" not in txt and "績效" not in txt: continue
                for row in tbl.find_all("tr"):
                    cols = [td.get_text(strip=True) for td in row.find_all(["td","th"])]
                    if len(cols) < 2: continue
                    label = cols[0]
                    for period_cn, period_key in PERIOD_MAP.items():
                        if period_cn in label and period_key not in out:
                            for c in cols[1:]:
                                c_c = c.replace("%","").replace(",","").strip()
                                try:
                                    v = float(c_c)
                                    if -99 < v < 500:
                                        out[period_key] = v; break
                                except: pass

            # ── Strategy 2: column headers contain period names ──
            if not out:
                for tbl in soup.find_all("table"):
                    txt = tbl.get_text()
                    if not any(p in txt for p in ["一年","三年","1Y","六個月"]): continue
                    rows = tbl.find_all("tr")
                    if len(rows) < 2: continue
                    # First try last header row
                    for hi in range(min(3, len(rows))):
                        header = [td.get_text(strip=True) for td in rows[hi].find_all(["th","td"])]
                        period_idx = {}
                        for ci, h in enumerate(header):
                            for period_cn, period_key in PERIOD_MAP.items():
                                if period_cn in h and ci not in period_idx:
                                    period_idx[ci] = period_key
                        if len(period_idx) >= 3:
                            # Next row(s) are data
                            for row in rows[hi+1:hi+4]:
                                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                                for ci, period_key in period_idx.items():
                                    if ci < len(cells) and period_key not in out:
                                        c_c = cells[ci].replace("%","").replace(",","").strip()
                                        try:
                                            v = float(c_c)
                                            if -99 < v < 500:
                                                out[period_key] = v
                                        except: pass
                            if out: break

            if out:
                print(f"[wb01 perf] ✅ {out}")
                break
        except Exception as e:
            print(f"[fetch_perf_wb01] {url[:50]} ERR: {e}")
    return out


def fetch_risk_metrics(code: str) -> dict:
    """
    抓取 MoneyDJ 績效評比頁（wb07.djhtm），回傳：
    {
      "risk_table":   {期間: {標準差, Sharpe, Alpha, Beta, R-squared, Tracking Error, Variance}}
      "peer_compare": {項目: {年平均報酬率, Sharpe, Beta, 標準差, 同類排名...}}
      "yearly_stats": {年份: {年化標準差, Beta, Sharpe Ratio, ...}}
    }
    """
    import re as _re
    try:
        BASE = "https://www.moneydj.com/funddj"
        TCB  = "https://tcbbankfund.moneydj.com"
        urls = [
            f"{BASE}/yp/wb07.djhtm?a={code}",
            f"{TCB}/w/wb/wb07.djhtm?a={code}",
        ]
        out = {}

        for url in urls:
            # v14.2: Big5 統一解碼
            r = fetch_url_with_retry(url, headers={**HDR, "Referer": f"{BASE}/yp/wb07.djhtm"}, timeout=25, retries=2)
            if r is None: continue
            soup = BeautifulSoup(r.text, "lxml")
            tables = soup.find_all("table")

            for tbl in tables:
                txt = tbl.get_text()
                rows = tbl.find_all("tr")
                if len(rows) < 2: continue

                # ─── 主風險指標表（六個月/一年/三年/五年/十年 × 標準差/Sharpe/Alpha…）──
                # v14.3: 條件擴充 — 近三月/近3月 都算，且 Sharpe 是數字不受 Big5 影響
                if ("標準差" in txt or "Sharpe" in txt) and (
                    "一年" in txt or "三年" in txt or
                    "近三月" in txt or "近3月" in txt or "六個月" in txt
                ):
                    # v14.3: 加入所有實際出現的期間名稱（Big5 解碼後確認）
                    PERIODS = [
                        "近三月","近3月","三個月","六個月","近六月",  # 短期
                        "一年","二年","三年","五年","十年",            # 長期
                        "近一年","近三年","近五年",                    # 另一種寫法
                        "一個月","三個月","六個月",                   # 全寫
                    ]
                    # 同時建立 Big5 轉換對照（部分環境解碼不完整時備用）
                    PERIOD_ALIAS = {
                        "近三月":"近三月","近3月":"近三月",
                        "六個月":"六個月","三個月":"三個月",
                        "一年":"一年","三年":"三年","五年":"五年","十年":"十年",
                    }
                    # Find the header row that contains period names
                    hdr_idx = None
                    for ri, row in enumerate(rows):
                        cells_txt = [td.get_text(strip=True) for td in row.find_all(["th","td"])]
                        if any(p in cells_txt for p in PERIODS):
                            hdr_idx = ri; break
                    if hdr_idx is None: continue
                    hdr_cells = [td.get_text(strip=True) for td in rows[hdr_idx].find_all(["th","td"])]
                    # Map column index → period name
                    col_period = {}
                    for ci, h in enumerate(hdr_cells):
                        if h in PERIODS: col_period[ci] = h
                    if not col_period: continue
                    periods_found = list(col_period.values())
                    risk_table = {p: {} for p in periods_found}
                    for row in rows[hdr_idx+1:]:
                        cols = [td.get_text(strip=True) for td in row.find_all("td")]
                        if not cols or len(cols) < 2: continue
                        metric = cols[0]
                        if not metric: continue
                        for ci, period in col_period.items():
                            if ci < len(cols):
                                v_s = cols[ci].replace(",","").strip()
                                # v13: 用 safe_float 取代裸 float()
                                _sf = safe_float(v_s)
                                risk_table[period][metric] = _sf if _sf is not None else cols[ci]
                    if any(risk_table[p] for p in periods_found):
                        out["risk_table"] = risk_table
                        print(f"[risk_metrics] 風險指標 {periods_found}")

                # ─── 同類比較表（peer_compare）─────────────────────────
                elif ("同投資類型" in txt or "同投資區域" in txt or "同類" in txt) and "報酬" in txt:
                    # Try to find header row (3+ cells)
                    hdr_idx = None
                    for ri, row in enumerate(rows):
                        cells = row.find_all(["th","td"])
                        if len(cells) >= 3:
                            hdr_idx = ri; break
                    if hdr_idx is None: continue
                    hdr = [td.get_text(strip=True) for td in rows[hdr_idx].find_all(["th","td"])]
                    peer = {}
                    for row in rows[hdr_idx+1:]:
                        cols = [td.get_text(strip=True) for td in row.find_all("td")]
                        if not cols or len(cols) < 2: continue
                        row_key = cols[0]
                        if not row_key: continue
                        row_data = {}
                        for i in range(1, len(cols)):
                            h = hdr[i] if i < len(hdr) else f"col{i}"
                            v_s = cols[i].replace(",","").strip()
                            try: row_data[h] = float(v_s.replace("%",""))
                            except: row_data[h] = cols[i]
                        if row_data: peer[row_key] = row_data
                    if peer:
                        out["peer_compare"] = peer
                        print(f"[risk_metrics] 同類比較 {list(peer.keys())[:3]}")

                # ─── 年度統計（2020-2025）────────────────────────────
                elif "年化標準差" in txt and any(str(y) in txt for y in range(2019,2027)):
                    hdr_idx = None
                    for ri, row in enumerate(rows):
                        cells = [td.get_text(strip=True) for td in row.find_all(["th","td"])]
                        if any(c.isdigit() and 2018 <= int(c) <= 2030 for c in cells):
                            hdr_idx = ri; break
                    if hdr_idx is None: continue
                    hdr = [td.get_text(strip=True) for td in rows[hdr_idx].find_all(["th","td"])]
                    years = [h for h in hdr if h.isdigit() and 2018 <= int(h) <= 2030]
                    yearly = {}
                    for row in rows[hdr_idx+1:]:
                        cols = [td.get_text(strip=True) for td in row.find_all("td")]
                        if not cols or len(cols) < 2: continue
                        metric_name = cols[0]
                        if not metric_name: continue
                        for i, yr in enumerate(years):
                            if yr not in yearly: yearly[yr] = {}
                            if i+1 < len(cols):
                                try: yearly[yr][metric_name] = float(cols[i+1])
                                except: yearly[yr][metric_name] = cols[i+1]
                    if yearly:
                        out["yearly_stats"] = yearly
                        print(f"[risk_metrics] 年度統計 {list(yearly.keys())}")

            if out: break  # Got data from first working URL
        return out
    except Exception as e:
        print(f"[fetch_risk_metrics] {e}")
        return {}




# ════════════════════════════════════════════════════════════
# 持股（yp013001.djhtm）: 產業配置 + 前10大持股
# ════════════════════════════════════════════════════════════
def fetch_holdings(code: str) -> dict:
    """
    抓取 MoneyDJ 持股頁，回傳：
    {
      "data_date":   "2026/01",
      "sector_alloc": [{"name": str, "pct": float, "amount": float}],
      "top_holdings": [{"name": str, "sector": str, "pct": float}],
    }
    """
    try:
        BASE = "https://www.moneydj.com/funddj"
        # v14.2: 改用 fetch_url_with_retry（Big5）；境內用 yp013000，境外用 yp013001
        _hold_page = "yp013000" if _is_domestic_code(code) else "yp013001"
        r = fetch_url_with_retry(
            f"{BASE}/yp/{_hold_page}.djhtm?a={code}",
            headers=HDR, timeout=20, retries=2
        )
        if r is None or r.status_code != 200:  # Bug fix: r 可能為 None（fetch_url_with_retry 全失敗時）
            return {}
        soup = BeautifulSoup(r.text, "lxml")
        out = {}

        for tbl in soup.find_all("table"):
            txt = tbl.get_text()

            # ── 產業配置 ──
            if "資訊科技" in txt or "工業" in txt or "金融" in txt:
                rows = tbl.find_all("tr")
                sectors = []
                # MoneyDJ table rule: row0=title(colspan), row1=colheader, row2+=data
                # MUST skip row0 (title) AND row1 (column headers) → start from rows[2:]
                _SKIP_KW = ("資料日期","産業","投資名稱","比例","投資金額","名稱",
                            "Fund","月份","持股","類別","資料月份","日期")
                for row in rows[2:]:
                    cols = [td.get_text(strip=True) for td in row.find_all("td")]
                    if len(cols) >= 2:
                        name = cols[0].strip()
                        # Skip header-like or empty rows
                        if not name or any(kw in name for kw in _SKIP_KW): continue
                        if len(name) > 25: continue   # real sector names ≤ 25 chars
                        # Find pct: last column containing a number
                        pct = 0.0
                        amount = 0.0
                        for c in reversed(cols[1:]):
                            try:
                                pct = float(c.replace("%","").replace(",","").strip())
                                if 0 < pct < 100: break
                            except: pass
                        if len(cols) >= 3:
                            try: amount = float(cols[1].replace(",","").replace("%",""))
                            except: pass
                        if pct > 0 and name:
                            sectors.append({"name": name, "amount": amount, "pct": pct})
                if sectors:
                    out["sector_alloc"] = sectors
                    print(f"[holdings] 產業 {len(sectors)} 類")

            # ── 前10大持股 ──
            if "投資名稱" in txt and "比例" in txt:
                rows = tbl.find_all("tr")
                holdings = []
                # MoneyDJ: row0=title, row1=column headers, row2+=data → rows[2:]
                _SKIP_H = ("資料日期","投資名稱","比例","産業","持股","金額","名稱",
                           "月份","資料月份","日期","Fund","순위","排名")
                for row in rows[2:]:
                    cols = [td.get_text(strip=True) for td in row.find_all("td")]
                    if len(cols) >= 2:
                        raw = cols[0].strip()
                        # Skip header-like rows
                        if not raw: continue
                        if any(kw in raw for kw in _SKIP_H): continue
                        if len(raw) > 60: continue    # long = still a header or garbage
                        # Find pct: last numeric column
                        pct_txt = ""
                        for c in reversed(cols):
                            c2 = c.replace("%","").strip()
                            try:
                                v = float(c2)
                                if 0 < v < 100:
                                    pct_txt = c2; break
                            except: pass
                        # 格式: "NVIDIA CORP,資訊科技" or just "NVIDIA CORP"
                        parts = raw.split(",", 1)
                        name   = parts[0].strip()
                        sector = parts[1].strip() if len(parts) > 1 else ""
                        # Also try splitting by known sector keywords for TW domestic funds
                        if not sector and len(parts) == 1:
                            for _sk in ["資訊科技","金融","工業","非必需消費","健康護理",
                                        "通訊服務","能源","必需消費","材料","公用事業",
                                        "房地產","流動資金","原物料","其他"]:
                                if _sk in name:
                                    idx_sk = name.index(_sk)
                                    sector = name[idx_sk:]
                                    name   = name[:idx_sk].strip()
                                    break
                        try:
                            pct = float(pct_txt)
                            if name and pct > 0:
                                holdings.append({"name": name, "sector": sector, "pct": pct})
                        except: pass
                if holdings:
                    out["top_holdings"] = holdings[:10]
                    print(f"[holdings] 前10大持股 {len(out['top_holdings'])} 筆")

        # 資料日期
        import re as _re
        full_txt = soup.get_text()
        dm = _re.search(r"資料月份[:：]\s*(\d{4}/\d{2})", full_txt)
        if dm: out["data_date"] = dm.group(1)

        return out
    except Exception as e:
        print(f"[fetch_holdings] {e}")
        return {}

def calc_metrics(s: pd.Series, divs: list, risk_override: dict = None) -> dict:
    """
    計算 MK 買點指標。
    risk_override: fetch_risk_metrics() 回傳的 dict，
                   若存在則優先使用 wb07 的年化標準差（更精準）。
    """
    if s.empty or len(s) < 5: return {}
    now = float(s.iloc[-1])
    log_ret = np.log(s / s.shift(1)).dropna()

    # ── 年化標準差（各期間）─────────────────────────────
    # MK 方法：最少 20 筆資料即可計算（降低門檻以支援短期資料）
    std_dict = {}
    for yrs, lb in [(1,"1年"),(2,"2年"),(3,"3年"),(5,"5年")]:
        n = yrs * 252
        base = log_ret.tail(n) if len(log_ret) >= n else log_ret
        if len(base) >= 20:  # ← 降低門檻 60→20
            std_dict[lb] = round(base.std() * np.sqrt(252) * 100, 2)
    # 優先用 wb07 績效評比的標準差（最準確）
    # 其次: 2年計算值 > 1年計算值 > 全期計算值
    risk_tbl = (risk_override or {}).get("risk_table", {})
    # ── v13 排錯：先用 safe_float 清洗，再做 N/A 判斷 ──────────────────
    risk_tbl = clean_risk_table(risk_tbl)      # 全表清洗，確保 N/A → None
    std_wb07_1y = safe_float(risk_tbl.get("一年", {}).get("標準差"))
    std_wb07_3y = safe_float(risk_tbl.get("三年", {}).get("標準差"))

    if std_wb07_1y is not None:
        # 將各期 wb07 標準差填入 std_dict（只填轉換成功的數值）
        _wb07_vals = set()
        for period_key, period_name in [("六個月","6M"),("一年","1Y"),("三年","3Y"),("五年","5Y")]:
            raw_v = risk_tbl.get(period_key, {}).get("標準差")
            v = safe_float(raw_v)          # N/A / -- → None，不爆掉
            if v is not None:
                _wb07_vals.add(v)
                std_dict[period_name] = v
        # 若 wb07 所有期間 std 完全相同（資料品質差），補用 nav 計算值
        if len(_wb07_vals) <= 1:
            for yrs, lb in [(1,"1Y"),(2,"2Y"),(3,"3Y"),(5,"5Y")]:
                n = yrs * 252
                base = log_ret.tail(n) if len(log_ret) >= n else log_ret
                if len(base) >= 20:
                    _nav_std = round(base.std() * np.sqrt(252) * 100, 2)
                    std_dict[lb] = _nav_std  # 覆蓋為各期真實計算值
        std_2y = std_dict.get("3Y", std_dict.get("2Y", std_wb07_1y))
        std_1y = std_dict.get("1Y", std_wb07_1y)
        print(f"[calc_metrics] 使用 wb07 標準差: 1Y={std_1y}% 3Y={std_2y}%")
    else:
        std_2y = std_dict.get("2年", std_dict.get("1年",
                 round(log_ret.std() * np.sqrt(252) * 100, 2) if len(log_ret)>=20 else 0))
        std_1y = std_dict.get("1年", std_2y)

    # ── 高低點（MK 買點基準用2年）──────────────────────
    def _hl(n):
        sub = s.tail(n) if len(s) >= n else s
        return (round(float(sub.max()),4), str(sub.idxmax())[:10],
                round(float(sub.min()),4), str(sub.idxmin())[:10])
    h1y,hd1,l1y,ld1 = _hl(252)
    h2y,hd2,l2y,ld2 = _hl(504)   # ← 2年高低點
    h3y,hd3,l3y,ld3 = _hl(756)
    hall = round(float(s.max()),4); hall_d = str(s.idxmax())[:10]
    lall = round(float(s.min()),4); lall_d = str(s.idxmin())[:10]

    # ── MK 標準差加碼買點（以年度最高/最低點為基準）──────
    # 優先使用 fetch_basic 抓到的 年最高/最低淨值
    # σ_amount = (year_high - year_low) / 3
    # Buy3 ≈ year_low，買點三對應歷史最低點
    _yh = risk_override.get("year_high_nav") if risk_override else None
    _yl = risk_override.get("year_low_nav")  if risk_override else None
    use_annual_hl = (_yh and _yl and _yh > _yl and _yh > 0)

    if use_annual_hl:
        # 年度高低點模式（最直觀）
        ref_high  = float(_yh)
        ref_low   = float(_yl)
        std_amt   = round((ref_high - ref_low) / 3, 4)
        buy_basis = ref_high
        buy_mode  = "年度高低點"
        print(f"[calc_metrics] 買點模式=年度高低點 年高={ref_high} 年低={ref_low} σ={std_amt}")
    else:
        # fallback: wb07/NAV σ 模式
        ref_high  = h2y
        std_amt   = round(h2y * std_2y / 100, 4) if std_2y else 0
        buy_basis = h2y
        buy_mode  = "wb07σ" if std_2y else "1年高"
        print(f"[calc_metrics] 買點模式={buy_mode} 基準={ref_high} σ={std_amt}")

    b1 = round(buy_basis - std_amt,   4)
    b2 = round(buy_basis - std_amt*2, 4)
    b3 = round(buy_basis - std_amt*3, 4)
    sell1 = round(buy_basis, 4)           # 回到年高 = 停利1
    sell2 = round(buy_basis + std_amt, 4) # 突破年高 = 停利2

    # 目前倉位判斷
    # 若 std_amt 極小（年高≈年低，年度資料不足），改為「資料待更新」
    if std_amt < now * 0.001:      # σ < 0.1% of nav → 資料不可靠
        pos_l, pos_c = "資料待更新 📡", "#555"
    elif now <= b3:                pos_l, pos_c = "大跌大買 -3σ 🔥",  "#9c27b0"
    elif now <= b2:                pos_l, pos_c = "急跌加碼 -2σ 📈",  "#00c853"
    elif now <= b1:                pos_l, pos_c = "小跌可買 -1σ ✅",   "#69f0ae"
    elif now >= sell2:             pos_l, pos_c = "突破年高 停利2 🔔", "#f44336"
    elif now >= sell1 * 0.98:      pos_l, pos_c = "逼近年高 停利1 ⚠️","#ff7043"
    else:                          pos_l, pos_c = "正常波動區",         "#888888"

    # ── 布林通道（20日 Rolling Band，作為時間序列輸出）──
    bb_period = min(20, len(s))
    bb_ma  = s.rolling(bb_period).mean()
    bb_std = s.rolling(bb_period).std()
    bb_upper_s = (bb_ma + 2 * bb_std).round(4)
    bb_lower_s = (bb_ma - 2 * bb_std).round(4)
    # 最新值（用於訊號判斷）
    bb_u = float(bb_upper_s.iloc[-1]) if not bb_upper_s.isna().all() else None
    bb_d = float(bb_lower_s.iloc[-1]) if not bb_lower_s.isna().all() else None
    bb_m_val = float(bb_ma.iloc[-1]) if not bb_ma.isna().all() else None
    if bb_u and bb_d and (bb_u - bb_d) > 0.0001:
        if   now >= bb_u: bb_sig, bb_c = "碰天花板 停利 📤", "#f44336"
        elif now <= bb_d: bb_sig, bb_c = "碰地板 買進 📥",   "#00c853"
        else:
            p = round((now - bb_d) / (bb_u - bb_d) * 100, 1)
            bb_sig, bb_c = f"通道 {p:.0f}% 位置", "#ff9800"
    else:
        bb_sig, bb_c = "通道過窄（波動低）", "#888"
    # 輸出時間序列供圖表用
    bb_upper_series = bb_upper_s.dropna()
    bb_lower_series = bb_lower_s.dropna()
    rf=0.04/252; r252=log_ret.tail(252) if len(log_ret)>=252 else log_ret
    sharpe=round(float((r252.mean()-rf)/r252.std()*np.sqrt(252)),2) if len(r252)>=60 else None
    cum=(1+log_ret).cumprod()
    max_dd=round(float(((cum-cum.cummax())/cum.cummax()).min())*100,2)
    def _ret(n): return round((now-float(s.iloc[-n]))/float(s.iloc[-n])*100,2) if len(s)>=n else None
    annual_div=monthly_div=div_rate=0; div_stability=None; div_trend=0; div_freq_n=12
    if divs:
        # ── 自動偵測配息頻率（月配/季配/半年/年配）────────
        if len(divs) >= 2:
            import statistics as _st
            _dates = []
            for _d in divs[:13]:
                try: _dates.append(pd.to_datetime(_d["date"]))
                except: pass
            _dates = sorted(_dates, reverse=True)
            if len(_dates) >= 2:
                _gaps = [(_dates[i]-_dates[i+1]).days for i in range(min(len(_dates)-1,6))]
                avg_gap = _st.mean(_gaps) if _gaps else 90
                if avg_gap <= 45:   div_freq_n = 12   # 月配
                elif avg_gap <= 100: div_freq_n = 4   # 季配
                elif avg_gap <= 200: div_freq_n = 2   # 半年配
                else:                div_freq_n = 1   # 年配
        # ── 計算配息年化率（配息年化率 ≠ 含息報酬率！）──────
        # 配息年化率 = 平均單次配息 × 年配次數 / 淨值
        # 含息報酬率 = (淨值漲跌 + 累積配息) / 期初淨值 → 需從 MoneyDJ 取得
        recent=[d["amount"] for d in divs[:div_freq_n]]
        avg_single_div = sum(recent)/len(recent) if recent else 0
        annual_div = avg_single_div * div_freq_n
        monthly_div = annual_div / 12
        div_rate = round(annual_div/now*100, 2) if now>0 else 0
        if len(recent)>=2:
            import statistics
            mn=statistics.mean(recent)
            cv=round(statistics.stdev(recent)/mn*100,1) if mn>0 else 0
            div_stability={"cv":cv,
                "label":"穩定" if cv<10 else("尚可" if cv<25 else "不穩定"),
                "color":"#00c853" if cv<10 else("#ff9800" if cv<25 else "#f44336")}
        recent12=[d["amount"] for d in divs[:12]]
        if len(recent12)>=6:
            div_trend=round((sum(recent12[:3])/3-sum(recent12[3:6])/3)/(sum(recent12[3:6])/3)*100,1) if sum(recent12[3:6])>0 else 0
    return dict(
        nav=now, std_multi=std_dict, std_1y=std_1y, std_2y=std_2y,
        std_multi_cn={
            "1年": std_dict.get("1Y", std_1y),
            "2年": std_dict.get("2Y", std_dict.get("3Y", std_2y)),
            "3年": std_dict.get("3Y", std_2y),
            "5年": std_dict.get("5Y"),
        }, std_amount=std_amt,
        high_1y=h1y,high_date_1y=hd1,low_1y=l1y,low_date_1y=ld1,
        high_2y=h2y,high_date_2y=hd2,low_2y=l2y,low_date_2y=ld2,
        high_3y=h3y,high_date_3y=hd3,low_3y=l3y,low_date_3y=ld3,
        all_high=hall,all_high_date=hall_d,all_low=lall,all_low_date=lall_d,
        buy1=b1,buy2=b2,buy3=b3,sell1=sell1,sell2=sell2,
        buy_basis=buy_basis,buy_mode=buy_mode,
        year_high_nav=float(_yh) if use_annual_hl else None,
        year_low_nav=float(_yl) if use_annual_hl else None,
        pos_label=pos_l,pos_color=pos_c,
        bb_upper=bb_u,bb_mid=round(bb_m_val,4) if bb_m_val else None,
        bb_lower=bb_d,bb_signal=bb_sig,bb_color=bb_c,
        bb_upper_series=bb_upper_series,bb_lower_series=bb_lower_series,
        std_source="wb07" if (risk_override and risk_override.get("risk_table")) else "nav",
        risk_table=risk_tbl,
        # 夏普優先用 wb07（更精確），自算值需要60+筆
        sharpe=(
            safe_float(risk_tbl.get("一年",{}).get("Sharpe")) or
            safe_float(risk_tbl.get("六個月",{}).get("Sharpe")) or
            sharpe
        ),
        max_drawdown=max_dd,
        ma20=round(float(s.tail(20).mean()),4) if len(s)>=20 else None,
        ma60=round(float(s.tail(60).mean()),4) if len(s)>=60 else None,
        ret_1w=_ret(6),ret_1m=_ret(22),ret_3m=_ret(65),
        ret_6m=_ret(130),ret_1y=_ret(252),ret_3y=_ret(756),
        annual_div=round(annual_div,4),monthly_div=round(monthly_div,4),
        annual_div_rate=div_rate,div_stability=div_stability,div_trend=div_trend,
    )


# ════════════════════════════════════════════════════════════
# 主入口：full_key → 淨值 + 配息 + MK
# ════════════════════════════════════════════════════════════
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_fund_by_key(full_key: str, fund_name: str = "",
                      portal: str = "", source: str = "",
                      manual_nav_csv: str = "") -> dict:
    """用已知的 full_key 取完整分析資料"""
    result = dict(
        full_key=full_key, fund_name=fund_name, portal=portal,
        series=None, dividends=[], metrics={}, error=None,
    )
    # 先嘗試鉅亨網（Colab 友善），再 MoneyDJ
    s = pd.Series(dtype=float)
    if (source == 'cnyes') or (len(full_key) < 8 and '-' not in full_key):
        s = fetch_nav_cnyes(full_key)
    if len(s) < 20:
        s = fetch_nav(full_key, portal)
    if len(s) < 20 and manual_nav_csv.strip():
        rows = []
        for line in manual_nav_csv.strip().split("\n"):
            parts = line.strip().split(",")
            if len(parts) >= 2:
                try: rows.append((pd.to_datetime(parts[0].strip()),float(parts[1].strip())))
                except: pass
        if len(rows) >= 20:
            s = pd.Series({r[0]:r[1] for r in rows}).sort_index()
    # 配息：cnyes 或 MoneyDJ
    if (source == 'cnyes') and len(full_key) < 8:
        divs = fetch_div_cnyes(full_key) if len(s) >= 5 else []
    else:
        divs = fetch_div(full_key, portal) if len(s) >= 5 else []
    if not divs and len(s) >= 5:
        divs = fetch_div(full_key, portal)
    if len(s) >= 20:
        result["series"]    = s
        result["dividends"] = divs
        result["metrics"]   = calc_metrics(s, divs)
    else:
        result["error"] = f"{full_key} 只取到 {len(s)} 筆淨值（需≥20）"
    return result


# 保留相容性（舊 main.py 呼叫）
@st.cache_data(ttl=1800, show_spinner=False)
def fetch_fund_by_code(insurance_code: str, gemini_key: str = "",
                       manual_full_key: str = "",
                       manual_nav_csv: str = "") -> dict:
    """相容舊介面：直接用 insurance_code 當 full_key"""
    key = manual_full_key.strip().upper() if manual_full_key.strip() else insurance_code.strip().upper()
    return fetch_fund_by_key(key, manual_nav_csv=manual_nav_csv)




# ════════════════════════════════════════════════════════════
# 基金結構分析（資產配置、持股、地區、績效）
# 從 MoneyDJ 保險子網域直接抓（與淨值/配息相同管道）
# ════════════════════════════════════════════════════════════

STRUCTURE_PAGES = {
    "資產配置": "/w/wh/wh02.djhtm?a={fk}",
    "地區配置": "/w/wh/wh03.djhtm?a={fk}",
    "持股明細": "/w/wq/wq06.djhtm?a={fk}",
    "債券明細": "/w/wq/wq06_bond.djhtm?a={fk}",
    "績效比較": "/w/wb/wb01.djhtm?a={fk}",
    "風險等級": "/w/wr/wr01.djhtm?a={fk}",
    "基金概況": "/w/wf/wf11.djhtm?a={fk}",
}

def _parse_pct_table(soup, keywords=None) -> list:
    """通用：從 HTML 中找含百分比或數字的表格，回傳 [{name, value, pct}]"""
    results = []
    for tbl in soup.find_all("table"):
        txt = tbl.get_text()
        if keywords and not any(k in txt for k in keywords):
            continue
        for row in tbl.find_all("tr")[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cols) < 2:
                continue
            name = cols[0]
            val  = ""
            pct  = 0.0
            for c in cols[1:]:
                # 找百分比
                pm = re.search(r"([\d.]+)\s*%", c)
                if pm:
                    pct = float(pm.group(1))
                    val = c
                    break
                # 找數字
                nm = re.search(r"[\d,]+\.?\d*", c.replace(",",""))
                if nm:
                    val = c
            if name and (pct > 0 or val):
                results.append({"name": name, "value": val, "pct": pct})
    return results


def fetch_fund_structure(full_key: str, portal: str = "") -> dict:
    """
    抓取基金結構分析資料：
    - 資產配置（股/債/現金比例）
    - 地區配置（美國/亞洲/歐洲…）
    - 前10大持股或債券
    - 績效比較
    - 風險等級
    從 MoneyDJ 保險子網域直接存取（Colab 可用）。
    """
    if not full_key:
        return {}

    bases = []
    if portal in PORTAL_CFG:
        bases.append(PORTAL_CFG[portal]["base_url"])
    # 子網域猜測
    mj = full_key.split("-")[-1] if "-" in full_key else full_key
    for p_name, p_cfg in PORTAL_CFG.items():
        if p_cfg["base_url"] not in bases:
            bases.append(p_cfg["base_url"])
    # 通用 MoneyDJ fallback
    bases.append("https://www.moneydj.com/funddj")

    struct = {}

    for page_name, path_tmpl in STRUCTURE_PAGES.items():
        path = path_tmpl.format(fk=full_key)
        for base in bases:
            url = base.rstrip("/") + path
            try:
                r = requests.get(url, headers=HDR, timeout=20)
                if r.status_code != 200:
                    continue
                if len(r.text) < 500:
                    continue
                soup = BeautifulSoup(r.text, "lxml")
                text = soup.get_text()

                # ── 資產配置 ──────────────────────────────────
                if page_name == "資產配置":
                    rows = _parse_pct_table(soup, ["股票","債券","現金","Cash","Bond","Stock"])
                    if rows:
                        struct["asset_allocation"] = rows
                        print(f"[structure 資產配置] {len(rows)} 類 ({url[:50]})")
                        break

                # ── 地區配置 ──────────────────────────────────
                elif page_name == "地區配置":
                    rows = _parse_pct_table(soup, ["美國","歐洲","亞洲","北美","新興"])
                    if rows:
                        struct["geo_allocation"] = rows
                        print(f"[structure 地區配置] {len(rows)} 地區")
                        break

                # ── 持股明細 ──────────────────────────────────
                elif page_name == "持股明細":
                    holdings = []
                    for tbl in soup.find_all("table"):
                        for row in tbl.find_all("tr")[1:16]:  # 前15筆
                            cols = [td.get_text(strip=True) for td in row.find_all("td")]
                            if len(cols) >= 2 and cols[0] and cols[1]:
                                pct = 0.0
                                for c in cols:
                                    pm = re.search(r"([\d.]+)\s*%", c)
                                    if pm: pct = float(pm.group(1)); break
                                holdings.append({"name": cols[0], "ticker": cols[1] if len(cols)>2 else "", "pct": pct})
                        if holdings: break
                    if holdings:
                        struct["top_holdings"] = holdings[:15]
                        print(f"[structure 持股] {len(holdings)} 筆")
                        break

                # ── 債券明細 ──────────────────────────────────
                elif page_name == "債券明細":
                    bonds = []
                    for tbl in soup.find_all("table"):
                        for row in tbl.find_all("tr")[1:16]:
                            cols = [td.get_text(strip=True) for td in row.find_all("td")]
                            if len(cols) >= 2 and cols[0]:
                                pct = 0.0
                                for c in cols:
                                    pm = re.search(r"([\d.]+)\s*%", c)
                                    if pm: pct = float(pm.group(1)); break
                                bonds.append({"name": cols[0], "coupon": cols[2] if len(cols)>2 else "", "pct": pct})
                        if bonds: break
                    if bonds:
                        struct["top_bonds"] = bonds[:15]
                        print(f"[structure 債券] {len(bonds)} 筆")
                        break

                # ── 績效比較 ──────────────────────────────────
                elif page_name == "績效比較":
                    rows = _parse_pct_table(soup, ["1月","3月","6月","1年","3年","基準"])
                    if rows:
                        struct["performance"] = rows
                        print(f"[structure 績效] {len(rows)} 筆")
                        break

                # ── 風險等級 ──────────────────────────────────
                elif page_name == "風險等級":
                    risk_text = ""
                    for tag in soup.find_all(["td","div","span","p"]):
                        t = tag.get_text(strip=True)
                        if re.search(r"[RR][Rr]|風險|等級|[1-7]級", t) and len(t) < 200:
                            risk_text = t; break
                    if risk_text:
                        struct["risk_info"] = risk_text
                        print(f"[structure 風險] {risk_text[:60]}")
                        break

                # ── 基金概況 ──────────────────────────────────
                elif page_name == "基金概況":
                    info = {}
                    for tbl in soup.find_all("table"):
                        for row in tbl.find_all("tr"):
                            cols = [td.get_text(strip=True) for td in row.find_all("td")]
                            if len(cols) >= 2:
                                k, v = cols[0], cols[1]
                                if any(x in k for x in ["成立","規模","基金","經理","計價","費率"]):
                                    info[k] = v
                    if info:
                        struct["fund_info"] = info
                        print(f"[structure 基金概況] {len(info)} 項")
                        break

            except Exception as e:
                print(f"[structure {page_name}] {url[:50]} ERR: {e}")
                continue

    return struct

def calc_dividend_estimate(nav, invest_amount, monthly_div, annual_div,
                           dist_freq, currency, usd_twd=32.0) -> dict:
    if nav<=0 or invest_amount<=0: return {}
    units=invest_amount/nav
    freq_n={"monthly":12,"quarterly":4,"annual":1}.get(dist_freq,12)
    freq_l={"monthly":"每月","quarterly":"每季","annual":"每年"}.get(dist_freq,"每月")
    rate=usd_twd if currency.upper() in ("USD","EUR","AUD") else 1.0
    return dict(
        units=round(units,4), per_dist=round(units*annual_div/freq_n,4),
        freq_label=freq_l, monthly=round(units*monthly_div,4),
        annual=round(units*annual_div,4),
        monthly_twd=round(units*monthly_div*rate,0),
        annual_twd=round(units*annual_div*rate,0),
    )

# ════════════════════════════════════════════════════════════
# 國際財經新聞抓取（RSS 多源整合）
# ════════════════════════════════════════════════════════════
def fetch_market_news(max_per_feed: int = 5) -> list:
    """
    從 RSS 抓取會影響股市、匯率、債券的國際財經新聞。
    回傳: [{title, summary, source, published, url}]
    """
    try:
        import feedparser as _fp
    except ImportError:
        return [{"title": "feedparser 未安裝", "summary": "pip install feedparser",
                 "source": "system", "published": "", "url": ""}]

    FEEDS = [
        ("Reuters Business", "https://feeds.reuters.com/reuters/businessNews"),
        ("Reuters Markets",  "https://feeds.reuters.com/reuters/companyNews"),
        ("MarketWatch",      "https://feeds.content.dowjones.io/public/rss/mw_bulletins"),
        ("FT Markets",       "https://www.ft.com/rss/home/uk"),
        ("Yahoo Finance",    "https://finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US"),
        ("Investing.com",    "https://www.investing.com/rss/news_14.rss"),
        ("CNBC Economy",     "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
        ("CNBC Finance",     "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ]

    KEYWORDS = [
        "Fed", "interest rate", "inflation", "CPI", "GDP", "recession",
        "bond", "treasury", "yield", "currency", "dollar", "yen", "euro",
        "stock market", "S&P", "Nasdaq", "earnings", "trade war", "tariff",
        "PMI", "unemployment", "central bank", "ECB", "BOJ", "PBOC",
        "China", "Taiwan", "semiconductor", "AI", "technology", "emerging market",
        "利率", "通膨", "聯準會", "美元", "匯率", "債券", "股市",
    ]

    results = []
    seen_titles = set()

    for source_name, feed_url in FEEDS:
        try:
            d = _fp.parse(feed_url)
            count = 0
            for entry in d.entries:
                if count >= max_per_feed:
                    break
                title   = getattr(entry, "title", "")
                summary = getattr(entry, "summary", "")[:300]
                url     = getattr(entry, "link", "")
                pub     = getattr(entry, "published", "")[:25]

                # Filter: only finance/market relevant
                text_check = (title + " " + summary).lower()
                if not any(kw.lower() in text_check for kw in KEYWORDS):
                    continue
                if title in seen_titles:
                    continue
                seen_titles.add(title)

                results.append({
                    "title":     title,
                    "summary":   summary,
                    "source":    source_name,
                    "published": pub,
                    "url":       url,
                })
                count += 1
        except Exception as _e:
            pass  # skip failed feeds silently

    # Sort by published date (newest first)
    results.sort(key=lambda x: x.get("published",""), reverse=True)
    return results[:25]

