#!/usr/bin/env python3
"""
fetch_nav_cache.py — GitHub Actions 每日淨值快取抓取器 v1.0

執行環境：GitHub Actions (ubuntu-latest, Azure IP)
目的：繞過 Streamlit Cloud US IP 被台灣財務網站封鎖的問題。
     每日定時抓取 TDCC/MoneyDJ 淨值，存入 cache/nav/{CODE}.json，
     Streamlit Cloud 再從快取讀取。

快取格式（cache/nav/TLZF9.json）：
{
  "code": "TLZF9",
  "updated_at": "2026-03-29T00:30:00",
  "source": "tdcc",
  "count": 365,
  "history": [{"date": "2026-03-28", "nav": 12.34}, ...]
}
"""
import json, time, datetime, re, os, sys
from pathlib import Path

try:
    import requests
except ImportError:
    os.system(f"{sys.executable} -m pip install requests -q")
    import requests

# ── 目標基金代碼 ─────────────────────────────────────────────────────
FUND_CODES = [
    "TLZF9", "ACTI71", "ACTI98", "FLFM1", "CTZP0",
    "ANZ89", "JFZN3",  "ACTI94", "ACCP138", "ACDD19",
]

# 境內基金代碼（安聯台灣境內，走 SITCA 而非 TDCC 境外 API）
DOMESTIC_PREFIXES = ("ACTI", "ACCP", "ACDD", "ACTT")

def is_domestic_code(code: str) -> bool:
    return any(code.upper().startswith(p) for p in DOMESTIC_PREFIXES)

CACHE_DIR = Path(__file__).parent.parent / "cache" / "nav"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ══════════════════════════════════════════════════════════════════════
# 資料來源 1：TDCC OpenAPI 3-4（政府開放 API，最穩）
# ══════════════════════════════════════════════════════════════════════
def fetch_tdcc_all() -> dict:
    """從 TDCC OpenAPI 3-4 取得所有境外基金最新淨值。回傳 {代碼: item_dict}"""
    url = "https://openapi.tdcc.com.tw/v1/opendata/3-4"
    try:
        r = SESSION.get(url, timeout=45)
        r.raise_for_status()
        items = r.json()
        result = {}
        for item in items:
            code = (item.get("基金代號") or item.get("境外基金代碼") or "").strip().upper()
            if code:
                result[code] = item
        print(f"[TDCC 3-4] 取得 {len(result)} 筆最新淨值")
        return result
    except Exception as e:
        print(f"[TDCC 3-4] 失敗: {e}")
        return {}


def fetch_tdcc_basic() -> dict:
    """從 TDCC OpenAPI 3-2 取得境外基金基本資料（含中文名稱）。"""
    url = "https://openapi.tdcc.com.tw/v1/opendata/3-2"
    try:
        r = SESSION.get(url, timeout=45)
        r.raise_for_status()
        items = r.json()
        result = {}
        for item in items:
            code = (item.get("基金代號") or item.get("境外基金代碼") or "").strip().upper()
            if code:
                result[code] = item
        print(f"[TDCC 3-2] 取得 {len(result)} 筆基本資料")
        return result
    except Exception as e:
        print(f"[TDCC 3-2] 失敗: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════════
# 資料來源 2：MoneyDJ yp004002（400 日歷史淨值）
# ══════════════════════════════════════════════════════════════════════
def fetch_moneydj_history(code: str, domain: str = "www.moneydj.com") -> list:
    """從 MoneyDJ yp004002 抓 400 日歷史淨值。回傳 [{"date": "YYYY-MM-DD", "nav": float}]"""
    end = datetime.date.today()
    start = end - datetime.timedelta(days=420)
    url = (
        f"https://{domain}/funddj/yf/yp004002.djhtm"
        f"?A={code}&B={start.strftime('%Y/%m/%d')}&C={end.strftime('%Y/%m/%d')}"
    )
    try:
        r = SESSION.get(
            url, timeout=30,
            headers={**HEADERS, "Referer": f"https://{domain}/"}
        )
        r.raise_for_status()
        html = r.text
        # 解析表格：日期 YYYY/MM/DD 與淨值
        rows = []
        matches = re.findall(
            r"(\d{4}/\d{2}/\d{2})[^<]*</td>[^<]*<td[^>]*>([0-9]+\.[0-9]+)",
            html
        )
        if not matches:
            # fallback: 更寬鬆的 regex
            matches = re.findall(r"(\d{4}/\d{2}/\d{2}).*?(\d+\.\d{2,4})", html[:50000])
        seen = set()
        for date_str, nav_str in matches:
            d = date_str.replace("/", "-")
            if d not in seen:
                seen.add(d)
                try:
                    rows.append({"date": d, "nav": float(nav_str)})
                except ValueError:
                    pass
        rows.sort(key=lambda x: x["date"], reverse=True)
        print(f"[MoneyDJ] {code}@{domain}: {len(rows)} 筆")
        return rows
    except Exception as e:
        print(f"[MoneyDJ] {code}@{domain} 失敗: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════
# 資料來源 3：MoneyDJ wb01（近 30 日 fallback）
# ══════════════════════════════════════════════════════════════════════
def fetch_moneydj_30day(code: str, domain: str = "www.moneydj.com") -> list:
    url = f"https://{domain}/w/wb/wb01.djhtm?a={code}"
    try:
        r = SESSION.get(url, timeout=25, headers={**HEADERS, "Referer": f"https://{domain}/"})
        r.raise_for_status()
        html = r.text
        rows = []
        seen = set()
        matches = re.findall(
            r"(\d{4}/\d{2}/\d{2})[^<]*</td>[^<]*<td[^>]*>([0-9]+\.[0-9]+)", html
        )
        for date_str, nav_str in matches:
            d = date_str.replace("/", "-")
            if d not in seen:
                seen.add(d)
                try:
                    rows.append({"date": d, "nav": float(nav_str)})
                except ValueError:
                    pass
        rows.sort(key=lambda x: x["date"], reverse=True)
        print(f"[MoneyDJ-30] {code}@{domain}: {len(rows)} 筆")
        return rows
    except Exception as e:
        print(f"[MoneyDJ-30] {code}@{domain} 失敗: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════
# 資料來源 4：SITCA（境內基金，適用 ACTI/ACCP/ACDD 代碼）
# ══════════════════════════════════════════════════════════════════════
def fetch_sitca_history(code: str) -> list:
    """從 SITCA 境內基金歷史淨值 API 取資料。適用 ACTI71/ACTI98/ACTI94/ACCP138/ACDD19。
    SITCA 為台灣公開政府資料，不應封鎖 GitHub Actions IP。
    """
    import datetime
    today = datetime.date.today()
    start = today - datetime.timedelta(days=420)
    url = (
        f"https://www.sitca.org.tw/ROC/Industry/IN2213.aspx"
        f"?txtFundCode={code}"
        f"&txtBeginDate={start.strftime('%Y/%m/%d')}"
        f"&txtEndDate={today.strftime('%Y/%m/%d')}"
    )
    try:
        r = SESSION.get(url, timeout=25)
        r.raise_for_status()
        html = r.text
        rows = []
        seen = set()
        # 解析日期 YYYY/MM/DD 與淨值
        matches = re.findall(
            r"(\d{4}/\d{2}/\d{2})[^<]*</td>[^<]*<td[^>]*>([0-9]+\.[0-9]+)", html
        )
        if not matches:
            matches = re.findall(r"(\d{4}/\d{2}/\d{2}).*?([0-9]+\.[0-9]{2,4})", html[:100000])
        for date_str, nav_str in matches:
            d = date_str.replace("/", "-")
            if d not in seen:
                seen.add(d)
                try:
                    rows.append({"date": d, "nav": float(nav_str)})
                except ValueError:
                    pass
        rows.sort(key=lambda x: x["date"], reverse=True)
        print(f"[SITCA] {code}: {len(rows)} 筆")
        return rows
    except Exception as e:
        print(f"[SITCA] {code} 失敗: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════
# 資料來源 5：Yahoo Finance（境外基金，需 Morningstar secId 映射）
# ══════════════════════════════════════════════════════════════════════
# 已知 Morningstar secId 映射
MORNINGSTAR_SECID_MAP = {
    "TLZF9": "0P0001J5YG",  # Allianz Income and Growth AMg7 USD
    "ANZ89": "0P0000X7WR",  # Allianz Income and Growth AM USD
    "JFZN3": "0P0001N4II",  # JPMorgan Global Income A icdiv USD hedged
}

def fetch_yahoo_finance_history(code: str) -> list:
    """從 Yahoo Finance 取歷史淨值（使用 Morningstar secId.F 格式）。
    Yahoo Finance 為美國服務，GitHub Actions 可存取。
    """
    sec_id = MORNINGSTAR_SECID_MAP.get(code, "")
    if not sec_id:
        return []
    yf_symbol = f"{sec_id}.F"
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/{yf_symbol}"
        f"?interval=1d&range=2y&includePrePost=false"
    )
    hdrs = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
    }
    try:
        import json
        r = SESSION.get(url, headers=hdrs, timeout=20)
        r.raise_for_status()
        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            print(f"[Yahoo] {code} ({yf_symbol}): 無結果")
            return []
        r0 = result[0]
        timestamps = r0.get("timestamp", [])
        closes = (r0.get("indicators", {}).get("quote", [{}])[0].get("close", []))
        rows = []
        seen = set()
        for ts, cl in zip(timestamps, closes):
            if ts and cl:
                try:
                    import datetime
                    d = datetime.date.fromtimestamp(ts).isoformat()
                    if d not in seen:
                        seen.add(d)
                        rows.append({"date": d, "nav": float(cl)})
                except Exception:
                    pass
        rows.sort(key=lambda x: x["date"], reverse=True)
        print(f"[Yahoo] {code} ({yf_symbol}): {len(rows)} 筆")
        return rows
    except Exception as e:
        print(f"[Yahoo] {code} ({yf_symbol}) 失敗: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════
# 銀行平台代碼對應（可提供更高存活率）
# ══════════════════════════════════════════════════════════════════════
BANK_PLATFORM_CODES = {
    "TLZF9":  [("fund.hncb.com.tw", "TLZF9-1180"), ("fundrwd.entiebank.com.tw", "TLZF9-24A7")],
    "ANZ89":  [("fund.megabank.com.tw", "ANZ89-1G11")],
    "ACTI94": [("fund.megabank.com.tw", "ACTI94-8A22")],
}

def fetch_bank_platform_history(base_code: str) -> list:
    """試用銀行平台代碼從 MoneyDJ yp004002 取歷史淨值。"""
    platforms = BANK_PLATFORM_CODES.get(base_code, [])
    for domain, full_code in platforms:
        rows = fetch_moneydj_history(full_code, domain=domain)
        if len(rows) >= 10:
            return rows
        rows = fetch_moneydj_30day(full_code, domain=domain)
        if len(rows) >= 5:
            return rows
    return []


# ══════════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════════
def load_cache(code: str) -> dict:
    cache_file = CACHE_DIR / f"{code}.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cache(code: str, history: list, source: str, fund_name: str = "") -> None:
    cache_file = CACHE_DIR / f"{code}.json"
    data = {
        "code": code,
        "fund_name": fund_name,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": source,
        "count": len(history),
        "history": history,
    }
    cache_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[cache] ✅ {code}: 已儲存 {len(history)} 筆 → cache/nav/{code}.json")


def merge_history(existing: list, new_rows: list) -> list:
    """合併新舊歷史，去重排序，最多保留 750 筆。"""
    merged = {r["date"]: r["nav"] for r in existing}
    for r in new_rows:
        merged[r["date"]] = r["nav"]
    result = [{"date": d, "nav": v} for d, v in sorted(merged.items(), reverse=True)]
    return result[:750]


def main():
    print(f"\n{'='*60}")
    print(f"NAV Cache Fetcher — {datetime.datetime.now().isoformat()}")
    print(f"{'='*60}\n")

    # 一次性取得 TDCC 全量資料（含最新淨值 + 基本資料）
    tdcc_nav  = fetch_tdcc_all()
    tdcc_meta = fetch_tdcc_basic()
    time.sleep(1)

    for code in FUND_CODES:
        print(f"\n── {code} ──────────────────────────────")
        existing_cache = load_cache(code)
        existing_history = existing_cache.get("history", [])
        fund_name = existing_cache.get("fund_name", "")

        # 更新基金名稱（從 TDCC 取）
        if not fund_name and code in tdcc_meta:
            meta = tdcc_meta[code]
            fund_name = (
                meta.get("基金中文名稱") or meta.get("基金名稱") or
                meta.get("基金簡稱") or ""
            )

        new_rows = []
        source_used = "cache_only"
        _is_domestic = is_domestic_code(code)

        if _is_domestic:
            # ── 境內基金（ACTI/ACCP/ACDD）：走 SITCA，不走 TDCC 境外 API ──
            print(f"  [境內基金] 走 SITCA 路徑")
            # 1d. SITCA 境內基金歷史淨值
            if len(existing_history) + len(new_rows) < 30:
                hist = fetch_sitca_history(code)
                if hist:
                    new_rows = merge_history(new_rows, hist)
                    source_used = "sitca"
                time.sleep(0.5)
        else:
            # ── 境外基金：走 TDCC + MoneyDJ + Yahoo Finance ──
            # 1. TDCC 最新淨值（只有一筆，但每天都更新）
            if code in tdcc_nav:
                item = tdcc_nav[code]
                date_raw = item.get("淨值日期") or item.get("最新淨值日期") or ""
                nav_raw  = item.get("單位淨值") or item.get("最新淨值") or ""
                if not fund_name:
                    fund_name = item.get("基金中文名稱") or item.get("基金名稱") or ""
                try:
                    d = date_raw.strip().replace("/", "-")
                    n = float(str(nav_raw).replace(",", ""))
                    if d and n > 0:
                        new_rows.append({"date": d, "nav": n})
                        source_used = "tdcc"
                        print(f"  TDCC: {d} → {n}")
                except (ValueError, AttributeError):
                    pass

            # 2. Yahoo Finance（Morningstar secId.F，GitHub Actions 可存取）
            if len(existing_history) + len(new_rows) < 30 and code in MORNINGSTAR_SECID_MAP:
                hist = fetch_yahoo_finance_history(code)
                if hist:
                    new_rows = merge_history(new_rows, hist)
                    source_used = "yahoo_finance"
                time.sleep(0.5)

            # 3. MoneyDJ 歷史（直接用基金代碼）
            if len(existing_history) + len(new_rows) < 30:
                hist = fetch_moneydj_history(code)
                if hist:
                    new_rows = merge_history(new_rows, hist)
                    source_used = "moneydj"
                time.sleep(0.8)

            # 4. 銀行平台代碼 fallback
            if len(existing_history) + len(new_rows) < 10 and code in BANK_PLATFORM_CODES:
                hist = fetch_bank_platform_history(code)
                if hist:
                    new_rows = merge_history(new_rows, hist)
                    source_used = "bank_platform"
                time.sleep(0.8)

            # 5. MoneyDJ 30 日 fallback
            if len(existing_history) + len(new_rows) < 10:
                hist = fetch_moneydj_30day(code)
                if hist:
                    new_rows = merge_history(new_rows, hist)
                    source_used = "moneydj_30d"
                time.sleep(0.5)

        # 合併並儲存
        final_history = merge_history(existing_history, new_rows)
        if final_history:
            save_cache(code, final_history, source_used, fund_name)
        else:
            print(f"  ⚠️  {code}: 本次無任何資料（保留既有快取 {len(existing_history)} 筆）")
            if existing_history:
                save_cache(code, existing_history, existing_cache.get("source", "cache_only"), fund_name)

        time.sleep(0.5)

    print(f"\n{'='*60}")
    print("完成！")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
