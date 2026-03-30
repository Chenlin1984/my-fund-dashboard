#!/usr/bin/env python3
"""
fetch_nav_cache.py — GitHub Actions 每日淨值快取抓取器 v2.0

執行環境：GitHub Actions (ubuntu-latest)
目的：繞過 Streamlit Cloud US IP 被台灣財務網站封鎖的問題。

資料來源優先順序：
  境外基金（TLZF9/ANZ89/JFZN3/FLFM1/CTZP0）：
    1. TDCC OpenAPI 3-4（政府開放資料）
    2. FundClear SmartFundAPI（基金結算中心，JSON API）
    3. yfinance（Yahoo Finance，需 Morningstar secId 映射）
    4. MoneyDJ 歷史（可能被封鎖）
    5. 銀行平台 fallback

  境內基金（ACTI71/ACTI98/ACTI94/ACCP138/ACDD19）：
    1. FundClear SmartFundAPI（境內也支援）
    2. SITCA 境內基金歷史（政府資料）
    3. Allianz Taiwan ifund API
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

# 境內基金代碼（安聯台灣境內）
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
# 資料來源 1：TDCC OpenAPI 3-4（政府開放 API）
# ══════════════════════════════════════════════════════════════════════
def fetch_tdcc_all() -> dict:
    """從 TDCC OpenAPI 3-4 取得所有境外基金最新淨值。"""
    url = "https://openapi.tdcc.com.tw/v1/opendata/3-4"
    try:
        r = SESSION.get(url, timeout=45)
        print(f"[TDCC 3-4] HTTP {r.status_code}")
        r.raise_for_status()
        items = r.json()
        result = {}
        for item in items:
            code = (item.get("基金代號") or item.get("境外基金代碼") or "").strip().upper()
            if code:
                result[code] = item
        print(f"[TDCC 3-4] 取得 {len(result)} 筆最新淨值")
        # 顯示目標代碼是否命中
        for c in FUND_CODES:
            if c in result:
                print(f"  ✓ {c}: {result[c]}")
        return result
    except Exception as e:
        print(f"[TDCC 3-4] 失敗: {e}")
        return {}


def fetch_tdcc_basic() -> dict:
    """從 TDCC OpenAPI 3-2 取得境外基金基本資料（含中文名稱）。"""
    url = "https://openapi.tdcc.com.tw/v1/opendata/3-2"
    try:
        r = SESSION.get(url, timeout=45)
        print(f"[TDCC 3-2] HTTP {r.status_code}")
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
# 資料來源 2：FundClear SmartFundAPI（基金結算中心，源頭直連）
# ══════════════════════════════════════════════════════════════════════
def fetch_fundclear_history(code: str) -> list:
    """
    從 FundClear 基金結算中心取歷史淨值。
    FundClear 是台灣基金交易結算機構，比 MoneyDJ 更接近源頭。
    境內境外基金均支援。
    API: https://www.fundclear.com.tw/SmartFundAPI/api/FundAjax/GetFundNAV
    """
    end = datetime.date.today()
    start = end - datetime.timedelta(days=420)
    url = (
        f"https://www.fundclear.com.tw/SmartFundAPI/api/FundAjax/GetFundNAV"
        f"?FundCode={code}"
        f"&StartDate={start.strftime('%Y/%m/%d')}"
        f"&EndDate={end.strftime('%Y/%m/%d')}"
    )
    try:
        r = SESSION.get(
            url, timeout=30,
            headers={**HEADERS, "Referer": "https://www.fundclear.com.tw/"}
        )
        print(f"[FundClear] {code} HTTP {r.status_code}")
        r.raise_for_status()
        data = r.json()
        # FundClear 回應格式：[{"NavDate": "2026/03/28", "Nav": "12.3400"}, ...]
        # 或 {"Data": [...]} 或直接 list
        items = data if isinstance(data, list) else data.get("Data", data.get("data", []))
        rows = []
        seen = set()
        for item in items:
            date_raw = (item.get("NavDate") or item.get("navDate") or
                        item.get("Date") or item.get("date") or "")
            nav_raw  = (item.get("Nav") or item.get("nav") or
                        item.get("NavValue") or item.get("navValue") or "")
            if date_raw and nav_raw:
                d = str(date_raw).strip().replace("/", "-")
                try:
                    n = float(str(nav_raw).replace(",", ""))
                    if n > 0 and d not in seen:
                        seen.add(d)
                        rows.append({"date": d, "nav": n})
                except (ValueError, TypeError):
                    pass
        rows.sort(key=lambda x: x["date"], reverse=True)
        print(f"[FundClear] {code}: {len(rows)} 筆")
        return rows
    except Exception as e:
        print(f"[FundClear] {code} 失敗: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════
# 資料來源 3：yfinance（Yahoo Finance，正確處理 crumb token）
# ══════════════════════════════════════════════════════════════════════
MORNINGSTAR_SECID_MAP = {
    "TLZF9": "0P0001J5YG",  # Allianz Income and Growth AMg7 USD
    "ANZ89": "0P0000X7WR",  # Allianz Income and Growth AM USD
    "JFZN3": "0P0001N4II",  # JPMorgan Global Income A icdiv USD hedged
}

def fetch_yfinance_history(code: str) -> list:
    """
    用 yfinance 套件取歷史淨值（自動處理 Yahoo Finance crumb token）。
    適用有 Morningstar secId 的境外基金。
    """
    try:
        import yfinance as yf
    except ImportError:
        print(f"[yfinance] 套件未安裝，跳過")
        return []

    sec_id = MORNINGSTAR_SECID_MAP.get(code, "")
    if not sec_id:
        return []

    symbol = f"{sec_id}.F"
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2y", auto_adjust=False)
        if hist.empty:
            print(f"[yfinance] {code} ({symbol}): 無資料")
            return []
        rows = []
        seen = set()
        for ts, row in hist.iterrows():
            cl = row.get("Close")
            if cl and not (cl != cl):  # skip NaN
                d = ts.strftime("%Y-%m-%d")
                if d not in seen:
                    seen.add(d)
                    rows.append({"date": d, "nav": float(cl)})
        rows.sort(key=lambda x: x["date"], reverse=True)
        print(f"[yfinance] {code} ({symbol}): {len(rows)} 筆")
        return rows
    except Exception as e:
        print(f"[yfinance] {code} ({symbol}) 失敗: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════
# 資料來源 4：MoneyDJ yp004002（400 日歷史）
# ══════════════════════════════════════════════════════════════════════
def fetch_moneydj_history(code: str, domain: str = "www.moneydj.com") -> list:
    end = datetime.date.today()
    start = end - datetime.timedelta(days=420)
    url = (
        f"https://{domain}/funddj/yf/yp004002.djhtm"
        f"?A={code}&B={start.strftime('%Y/%m/%d')}&C={end.strftime('%Y/%m/%d')}"
    )
    try:
        r = SESSION.get(url, timeout=30,
                        headers={**HEADERS, "Referer": f"https://{domain}/"})
        print(f"[MoneyDJ] {code}@{domain} HTTP {r.status_code}")
        r.raise_for_status()
        html = r.text
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            rows = []
            seen = set()
            for tbl in soup.find_all("table"):
                txt = tbl.get_text()
                if "淨值" not in txt and "NAV" not in txt.upper():
                    continue
                for tr in tbl.find_all("tr"):
                    tds = tr.find_all("td")
                    if len(tds) >= 2:
                        dt = tds[0].get_text(strip=True)
                        nv = tds[1].get_text(strip=True).replace(",", "")
                        m = re.match(r"(\d{4})[/-](\d{2})[/-](\d{2})", dt)
                        if m:
                            d = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                            v = None
                            try:
                                v = float(nv)
                            except ValueError:
                                pass
                            if v and v > 0 and d not in seen:
                                seen.add(d)
                                rows.append({"date": d, "nav": v})
            rows.sort(key=lambda x: x["date"], reverse=True)
            if rows:
                print(f"[MoneyDJ] {code}@{domain}: {len(rows)} 筆 (BS4)")
                return rows
        except Exception:
            pass
        # BeautifulSoup fallback → regex（只比對 table 內容）
        rows = []
        seen = set()
        matches = re.findall(
            r"(\d{4}/\d{2}/\d{2})[^<]*</td>[^<]*<td[^>]*>([0-9]+\.[0-9]+)",
            html
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
        print(f"[MoneyDJ] {code}@{domain}: {len(rows)} 筆 (regex)")
        return rows
    except Exception as e:
        print(f"[MoneyDJ] {code}@{domain} 失敗: {e}")
        return []


def fetch_moneydj_30day(code: str, domain: str = "www.moneydj.com") -> list:
    url = f"https://{domain}/w/wb/wb01.djhtm?a={code}"
    try:
        r = SESSION.get(url, timeout=25,
                        headers={**HEADERS, "Referer": f"https://{domain}/"})
        print(f"[MoneyDJ-30] {code}@{domain} HTTP {r.status_code}")
        r.raise_for_status()
        html = r.text
        rows = []
        seen = set()
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            for tbl in soup.find_all("table"):
                if "淨值" not in tbl.get_text() and "NAV" not in tbl.get_text().upper():
                    continue
                for tr in tbl.find_all("tr"):
                    tds = tr.find_all("td")
                    if len(tds) >= 2:
                        dt = tds[0].get_text(strip=True)
                        nv = tds[1].get_text(strip=True).replace(",", "")
                        m = re.match(r"(\d{4})[/-](\d{2})[/-](\d{2})", dt)
                        if m:
                            d = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                            try:
                                v = float(nv)
                                if v > 0 and d not in seen:
                                    seen.add(d)
                                    rows.append({"date": d, "nav": v})
                            except ValueError:
                                pass
        except Exception:
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
# 資料來源 5：SITCA（境內基金）
# ══════════════════════════════════════════════════════════════════════
def fetch_sitca_history(code: str) -> list:
    today = datetime.date.today()
    start = today - datetime.timedelta(days=420)
    url = (
        f"https://www.sitca.org.tw/ROC/Industry/IN2213.aspx"
        f"?txtFundCode={code}"
        f"&txtBeginDate={start.strftime('%Y/%m/%d')}"
        f"&txtEndDate={today.strftime('%Y/%m/%d')}"
    )
    try:
        r = SESSION.get(url, timeout=30)
        print(f"[SITCA] {code} HTTP {r.status_code}")
        r.raise_for_status()
        html = r.text
        rows = []
        seen = set()
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            for tbl in soup.find_all("table"):
                txt = tbl.get_text()
                if "淨值" not in txt:
                    continue
                for tr in tbl.find_all("tr"):
                    tds = tr.find_all("td")
                    if len(tds) >= 2:
                        dt = tds[0].get_text(strip=True)
                        nv = tds[1].get_text(strip=True).replace(",", "")
                        m = re.match(r"(\d{4})[/-](\d{2})[/-](\d{2})", dt)
                        if m:
                            d = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                            try:
                                v = float(nv)
                                if v > 0 and d not in seen:
                                    seen.add(d)
                                    rows.append({"date": d, "nav": v})
                            except ValueError:
                                pass
        except Exception:
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
        print(f"[SITCA] {code}: {len(rows)} 筆")
        return rows
    except Exception as e:
        print(f"[SITCA] {code} 失敗: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════
# 資料來源 6：銀行平台（MoneyDJ 子網域）
# ══════════════════════════════════════════════════════════════════════
BANK_PLATFORM_CODES = {
    "TLZF9":  [("fund.hncb.com.tw", "TLZF9-1180"), ("fundrwd.entiebank.com.tw", "TLZF9-24A7")],
    "ANZ89":  [("fund.megabank.com.tw", "ANZ89-1G11")],
    "ACTI94": [("fund.megabank.com.tw", "ACTI94-8A22")],
}

def fetch_bank_platform_history(base_code: str) -> list:
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
    print(f"[cache] ✅ {code}: 已儲存 {len(history)} 筆 ({source}) → cache/nav/{code}.json")


def merge_history(existing: list, new_rows: list) -> list:
    merged = {r["date"]: r["nav"] for r in existing}
    for r in new_rows:
        merged[r["date"]] = r["nav"]
    result = [{"date": d, "nav": v} for d, v in sorted(merged.items(), reverse=True)]
    return result[:750]


def main():
    print(f"\n{'='*60}")
    print(f"NAV Cache Fetcher v2.0 — {datetime.datetime.now().isoformat()}")
    print(f"{'='*60}\n")

    # 一次性取得 TDCC 全量資料
    tdcc_nav  = fetch_tdcc_all()
    tdcc_meta = fetch_tdcc_basic()
    time.sleep(1)

    for code in FUND_CODES:
        print(f"\n── {code} ──────────────────────────────")
        existing_cache = load_cache(code)
        existing_history = existing_cache.get("history", [])
        fund_name = existing_cache.get("fund_name", "")

        if not fund_name and code in tdcc_meta:
            meta = tdcc_meta[code]
            fund_name = (meta.get("基金中文名稱") or meta.get("基金名稱") or
                         meta.get("基金簡稱") or "")

        new_rows = []
        source_used = "cache_only"
        _is_domestic = is_domestic_code(code)

        # ── 所有基金：先試 TDCC 最新 NAV（境外）──────────────────────
        if not _is_domestic and code in tdcc_nav:
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

        # ── 所有基金：FundClear（源頭 JSON API）────────────────────────
        if len(existing_history) + len(new_rows) < 30:
            hist = fetch_fundclear_history(code)
            if len(hist) >= 5:
                new_rows = merge_history(new_rows, hist)
                source_used = "fundclear"
            time.sleep(0.8)

        # ── 境外基金：yfinance（需 Morningstar secId）──────────────────
        if not _is_domestic and len(existing_history) + len(new_rows) < 30 and code in MORNINGSTAR_SECID_MAP:
            hist = fetch_yfinance_history(code)
            if len(hist) >= 10:
                new_rows = merge_history(new_rows, hist)
                source_used = "yfinance"
            time.sleep(0.5)

        # ── 境內基金：SITCA ──────────────────────────────────────────────
        if _is_domestic and len(existing_history) + len(new_rows) < 30:
            hist = fetch_sitca_history(code)
            if len(hist) >= 5:
                new_rows = merge_history(new_rows, hist)
                source_used = "sitca"
            time.sleep(0.5)

        # ── 境外 fallback：MoneyDJ 歷史 ────────────────────────────────
        if len(existing_history) + len(new_rows) < 30:
            hist = fetch_moneydj_history(code)
            if hist:
                new_rows = merge_history(new_rows, hist)
                source_used = "moneydj"
            time.sleep(0.8)

        # ── 銀行平台 fallback ──────────────────────────────────────────
        if len(existing_history) + len(new_rows) < 10 and code in BANK_PLATFORM_CODES:
            hist = fetch_bank_platform_history(code)
            if hist:
                new_rows = merge_history(new_rows, hist)
                source_used = "bank_platform"
            time.sleep(0.8)

        # ── MoneyDJ 30 日 fallback ─────────────────────────────────────
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
                save_cache(code, existing_history,
                           existing_cache.get("source", "cache_only"), fund_name)

        time.sleep(0.5)

    print(f"\n{'='*60}")
    print("完成！")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
