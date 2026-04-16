"""
總經位階 + 拐點偵測 v7
修正：殖利率利差使用 merge_asof（日頻 vs 月頻對齊）
新增：指標加權評分、衰退機率、景氣時鐘
"""
import requests, yfinance as yf, pandas as pd, numpy as np, streamlit as st, math

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
ENGINE_VERSION = "v18.0_Refactor"
_INDICATOR_SNAPSHOT: dict = {}

def _fred(sid, key, n=250):
    if not key: return pd.DataFrame()
    try:
        r = requests.get(FRED_BASE, params={
            "series_id":sid,"api_key":key,
            "file_type":"json","sort_order":"desc","limit":n,
        }, timeout=15)
        df = pd.DataFrame(r.json().get("observations",[]))
        if df.empty: return pd.DataFrame()
        df = df[df["value"] != "."].copy()
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df["date"]  = pd.to_datetime(df["date"])
        return df.dropna(subset=["value"]).sort_values("date").reset_index(drop=True)
    except Exception as e:
        print(f"[FRED {sid}] {e}"); return pd.DataFrame()

def _yf_s(ticker, period="2y"):
    try:
        h = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        return h["Close"].dropna() if not h.empty else pd.Series(dtype=float)
    except: return pd.Series(dtype=float)

def _trend(vals):
    if len(vals) < 3: return ""
    diffs = [vals[i]-vals[i-1] for i in range(1, len(vals))]
    pos = sum(1 for d in diffs if d > 0); neg = sum(1 for d in diffs if d < 0)
    if pos >= len(diffs)-1: return "持續上升 ↑"
    if neg >= len(diffs)-1: return "持續下降 ↓"
    return "最近反彈 ↗" if diffs[-1] > 0 else "最近回落 ↘"

def _safe_last(df, n=2):
    if df.empty or len(df) < n: return [None]*n
    v = df["value"].tolist()
    return [v[-i] for i in range(1, n+1)]

def _spread_series(df_long, df_short, n_pts=60):
    if df_long.empty or df_short.empty: return pd.Series(dtype=float)
    dl = df_long[["date","value"]].set_index("date").rename(columns={"value":"v_l"}).copy()
    ds = df_short[["date","value"]].set_index("date").rename(columns={"value":"v_s"}).copy()
    dl_m = dl.resample("ME").last().ffill()
    ds_m = ds.resample("ME").last().ffill()
    merged = dl_m.join(ds_m, how="inner").dropna()
    if merged.empty:
        dl2 = df_long[["date","value"]].rename(columns={"value":"v_l"}).sort_values("date")
        ds2 = df_short[["date","value"]].rename(columns={"value":"v_s"}).sort_values("date")
        m = pd.merge_asof(dl2, ds2, on="date", tolerance=pd.Timedelta("40d"), direction="backward").dropna()
        m = m.set_index("date")
        return (m["v_l"] - m["v_s"]).tail(n_pts)
    return (merged["v_l"] - merged["v_s"]).tail(n_pts)

def recession_probability(spread_10y3m):
    """用 10Y-3M 利差做 logistic 回歸估算衰退機率"""
    if spread_10y3m is None: return None
    logit = -1.5 * spread_10y3m - 0.8
    return round(1 / (1 + math.exp(-logit)) * 100, 1)

def _detect_inflection(indicators):
    signals = []; score = 0
    def _chk(key, attr="value"): return indicators.get(key,{}).get(attr)

    pmi_v = _chk("PMI"); pmi_p = _chk("PMI","prev")
    if pmi_v and pmi_p:
        if pmi_v < 50 and pmi_v > pmi_p:
            signals.append({"type":"buy","text":f"PMI {pmi_v:.1f} 收縮區但止跌反彈（+{pmi_v-pmi_p:.1f}）— 復甦訊號"}); score += 2
        elif pmi_v >= 50 and pmi_v > pmi_p:
            signals.append({"type":"bull","text":f"PMI {pmi_v:.1f} 擴張且上升"}); score += 1
        elif pmi_v >= 55 and pmi_v < pmi_p:
            signals.append({"type":"warn","text":f"PMI {pmi_v:.1f} 高位回落，景氣可能見頂"}); score -= 1

    y22 = indicators.get("YIELD_10Y2Y",{})
    v22 = y22.get("value"); p22 = y22.get("prev")
    if v22 is not None:
        if v22 < 0: signals.append({"type":"warn","text":f"10Y-2Y 倒掛 {v22:.3f}%，衰退信號"}); score -= 2
        elif v22 >= 0 and p22 is not None and p22 < 0:
            signals.append({"type":"buy","text":f"⚡ 10Y-2Y 由負翻正（{v22:.3f}%）— MK 最強黃金買點！"}); score += 4
        elif v22 > 0.5: signals.append({"type":"bull","text":f"10Y-2Y 正斜率 {v22:.3f}%"}); score += 1

    y3m = indicators.get("YIELD_10Y3M",{})
    v3m = y3m.get("value"); p3m = y3m.get("prev")
    if v3m is not None:
        if v3m < 0: signals.append({"type":"warn","text":f"10Y-3M 倒掛 {v3m:.3f}%"}); score -= 2
        elif v3m >= 0 and p3m is not None and p3m < 0:
            signals.append({"type":"buy","text":f"⚡ 10Y-3M 翻正（{v3m:.3f}%）— 降息確認"}); score += 3

    cpi_v = _chk("CPI"); cpi_t = indicators.get("CPI",{}).get("trend","")
    if cpi_v:
        if cpi_v > 4.0 and "下降" in cpi_t: signals.append({"type":"buy","text":f"⚡ CPI {cpi_v:.1f}% 高位但回落 — 落後指標見頂"}); score += 3
        elif cpi_v > 4.0: signals.append({"type":"warn","text":f"CPI {cpi_v:.1f}% 高位未降，緊縮壓力"}); score -= 2
        elif 1.5 <= cpi_v <= 3.0: signals.append({"type":"bull","text":f"CPI {cpi_v:.1f}% 回落至合理區間"}); score += 2

    fed_v = _chk("FED_RATE"); fed_p = _chk("FED_RATE","prev")
    if fed_v is not None and fed_p is not None:
        if fed_v < fed_p: signals.append({"type":"buy","text":f"⚡ 降息（{fed_p:.2f}%→{fed_v:.2f}%）— 資金行情"}); score += 3
        elif fed_v > fed_p: signals.append({"type":"warn","text":f"升息（{fed_p:.2f}%→{fed_v:.2f}%）"}); score -= 2

    vix_v = _chk("VIX")
    if vix_v:
        if vix_v > 30: signals.append({"type":"buy","text":f"VIX {vix_v:.1f} 恐慌高位 — 逢低加碼時機"}); score += 2
        elif vix_v < 15: signals.append({"type":"warn","text":f"VIX {vix_v:.1f} 過低，市場過樂觀"}); score -= 1

    jb_v = _chk("JOBLESS"); jb_p = _chk("JOBLESS","prev")
    if jb_v and jb_p:
        if jb_v < jb_p and jb_v < 250000: signals.append({"type":"bull","text":f"初領失業金 {jb_v:,.0f} 改善"}); score += 1
        elif jb_v > 300000: signals.append({"type":"warn","text":f"初領失業金 {jb_v:,.0f} 高位"}); score -= 1

    if fed_v is not None and fed_p is not None and fed_v <= fed_p and fed_p > 0 and \
       cpi_v and cpi_v < 3.5 and "下降" in cpi_t:
        signals.append({"type":"buy","text":"⭐ MK黃金拐點：CPI+Fed Rate 雙雙見頂回落，勝率最高！"}); score += 5

    if score >= 8:   infl = {"label":"🚀 強力買進拐點","color":"#00c853","desc":"多項指標同時確認，景氣最佳買點"}
    elif score >= 4: infl = {"label":"✅ 買進拐點形成","color":"#69f0ae","desc":"落後見頂 + 領先反彈，建議逢低布局"}
    elif score >= 1: infl = {"label":"👀 觀察（偏多）","color":"#ff9800","desc":"部分訊號出現，持續觀察"}
    elif score >= -2:infl = {"label":"⚖️ 中性整理","color":"#888888","desc":"指標分歧，維持資產配置"}
    elif score >= -5:infl = {"label":"⚠️ 謹慎偏空","color":"#ff7043","desc":"落後指標未見頂，降低股票型比重"}
    else:            infl = {"label":"🔴 空頭拐點","color":"#f44336","desc":"確認衰退，優先貨幣型與投資等級債"}
    return {"inflection":infl,"signals":signals,"infl_score":score}


def fetch_all_indicators(fred_api_key):
    R = {}

    # ── PMI ─────────────────────────────────────────────────────────
    df = _fred("NAPM", fred_api_key, 60)
    if df.empty or len(df) < 2:
        df = _fred("ISPMANPMI", fred_api_key, 60)
    if len(df) >= 2:
        v = float(df.iloc[-1]["value"]); p = float(df.iloc[-2]["value"])
        s = df.set_index("date")["value"].tail(24)
        R["PMI"] = dict(name="ISM 製造業 PMI", value=v, prev=p, unit="", type="領先",
            date=str(df.iloc[-1]["date"])[:7], desc="50為榮枯線，>50擴張，<50收縮 | 最核心領先指標",
            trend=_trend(df["value"].tolist()[-6:]),
            signal="🟢" if v>50 else "🔴", color="#00c853" if v>50 else "#f44336",
            score=2 if v>=50 else (-2 if v<45 else -1),
            weight=2, series=s)

    # ── 殖利率利差 ──────────────────────────────────────────────────
    df10 = _fred("DGS10", fred_api_key, 250)
    df2  = _fred("DGS2",  fred_api_key, 250)
    df3m = _fred("TB3MS", fred_api_key, 60)

    if not df10.empty and not df2.empty:
        sp22 = _spread_series(df10, df2, 60)
        if len(sp22) >= 2:
            v = float(sp22.iloc[-1]); p = float(sp22.iloc[-2])
            R["YIELD_10Y2Y"] = dict(name="殖利率利差 10Y-2Y", value=round(v,3), prev=round(p,3),
                unit="%", type="領先", date=str(sp22.index[-1])[:7],
                desc="倒掛(<0)=衰退 | 由負翻正=MK黃金買點",
                trend=_trend(sp22.tolist()[-6:]),
                signal="🟢" if v>0.5 else ("🔴" if v<0 else "🟡"),
                color="#00c853" if v>0.5 else ("#f44336" if v<0 else "#ff9800"),
                score=2 if v>0.5 else (-2 if v<0 else 0),
                weight=2, series=sp22)

    if not df10.empty and not df3m.empty:
        sp3m = _spread_series(df10, df3m, 60)
        if len(sp3m) >= 2:
            v = float(sp3m.iloc[-1]); p = float(sp3m.iloc[-2])
            R["YIELD_10Y3M"] = dict(name="殖利率利差 10Y-3M", value=round(v,3), prev=round(p,3),
                unit="%", type="領先", date=str(sp3m.index[-1])[:7],
                desc="倒掛解除=降息確認 | 最強衰退預測指標",
                trend=_trend(sp3m.tolist()[-6:]),
                signal="🟢" if v>0.5 else ("🔴" if v<0 else "🟡"),
                color="#00c853" if v>0.5 else ("#f44336" if v<0 else "#ff9800"),
                score=2 if v>0 else -2,
                weight=2, series=sp3m)

    # ── HY 信用利差 ──────────────────────────────────────────────────
    df = _fred("BAMLH0A0HYM2", fred_api_key, 120)
    if len(df) >= 2:
        s = df.set_index("date")["value"].tail(60)
        v = float(df.iloc[-1]["value"]); p = float(df.iloc[-2]["value"])
        R["HY_SPREAD"] = dict(
            name="HY 信用利差 (OAS)", value=round(v,2), prev=round(p,2),
            unit="%", type="金融壓力", date=str(df.iloc[-1]["date"])[:7],
            desc="<4%樂觀 | 4~6%中性 | >6%風險 | 擴大=逃離高風險資產",
            trend=_trend(s.tolist()[-6:]),
            signal="🟢" if v<4 else ("🔴" if v>6 else "🟡"),
            color="#00c853" if v<4 else ("#f44336" if v>6 else "#ff9800"),
            score=2 if v<4 else (-2 if v>6 else 0),
            weight=2, series=s)

    # ── M2 ───────────────────────────────────────────────────────────
    df = _fred("M2SL", fred_api_key, 60)
    if len(df) >= 13:
        s = df.set_index("date")["value"]
        yoy = (s / s.shift(12) - 1) * 100
        s24 = yoy.dropna().tail(36)
        v = float(s24.iloc[-1]); p = float(s24.iloc[-2]) if len(s24)>=2 else v
        R["M2"] = dict(
            name="M2 貨幣供給 (YoY)", value=round(v,2), prev=round(p,2),
            unit="%", type="流動性", date=str(df.iloc[-1]["date"])[:7],
            desc=">5%流動性寬鬆→利多 | <0%緊縮→壓力",
            trend=_trend(s24.tolist()[-6:]),
            signal="🟢" if v>5 else ("🔴" if v<0 else "🟡"),
            color="#00c853" if v>5 else ("#f44336" if v<0 else "#ff9800"),
            score=1 if v>5 else (-1 if v<0 else 0),
            weight=1, series=s24)

    # ── 市場廣度 RSP/SPY ─────────────────────────────────────────────
    try:
        s_spy = _yf_s("SPY","1y"); s_rsp = _yf_s("RSP","1y")
        if len(s_spy)>=22 and len(s_rsp)>=22:
            ratio = (s_rsp / s_spy).dropna()
            ratio = ratio.reindex(s_spy.index, method="ffill").dropna()
            v = round(float(ratio.iloc[-1]),4); m1 = round(float(ratio.iloc[-22]),4)
            chg = round((v-m1)/m1*100,2)
            s_w = ratio.resample("W").last().tail(52)
            R["ADL"] = dict(
                name="市場廣度 RSP/SPY", value=round(v,4), prev=round(chg,2),
                unit="", type="市場廣度", date="即時",
                desc=f"等/市值比率月變{chg:+.2f}% | 上升=多頭健康 | 下降=僅七巨頭撐盤",
                trend="up" if chg>0.5 else ("down" if chg<-0.5 else "flat"),
                signal="🟢" if chg>0.5 else ("🔴" if chg<-1 else "🟡"),
                color="#00c853" if chg>0.5 else ("#f44336" if chg<-1 else "#ff9800"),
                score=1 if chg>0.5 else (-1 if chg<-1 else 0),
                weight=1, series=s_w)
    except Exception as e:
        print(f"[ADL] {e}")

    # ── DXY ──────────────────────────────────────────────────────────
    s_dxy = _yf_s("DX-Y.NYB", "2y")
    if len(s_dxy) >= 22:
        v = round(float(s_dxy.iloc[-1]),2); m1 = round(float(s_dxy.iloc[-22]),2)
        chg_m = round((v-m1)/m1*100, 2)
        s_w = s_dxy.resample("W").last().tail(52)
        R["DXY"] = dict(
            name="美元指數 DXY", value=v, prev=round(chg_m,2),
            unit="", type="資金流向", date="即時",
            desc=f"月漲跌 {chg_m:+.2f}% | 弱美元→新興市場利多 | 強美元→壓縮",
            trend="up" if chg_m>1 else ("down" if chg_m<-1 else "flat"),
            signal="🟡" if abs(chg_m)<1 else ("🟢" if chg_m<-1 else "🔴"),
            color="#ff9800" if abs(chg_m)<1 else ("#00c853" if chg_m<-1 else "#f44336"),
            score=1 if chg_m<-1 else (-1 if chg_m>2 else 0),
            weight=1, series=s_w)

    # ── Fed 資產負債表 ────────────────────────────────────────────────
    df = _fred("WALCL", fred_api_key, 104)
    if len(df) >= 13:
        s = df.set_index("date")["value"]
        yoy = (s / s.shift(52) - 1) * 100
        s24 = yoy.dropna().tail(52)
        v = float(s24.iloc[-1]); p = float(s24.iloc[-2]) if len(s24)>=2 else v
        R["FED_BS"] = dict(
            name="Fed 資產負債表 (YoY)", value=round(v,2), prev=round(p,2),
            unit="%", type="流動性", date=str(df.iloc[-1]["date"])[:7],
            desc="擴表=注入流動性→利多 | 縮表=抽走流動性→壓力",
            trend=_trend(s24.tolist()[-6:]),
            signal="🟢" if v>5 else ("🔴" if v<-5 else "🟡"),
            color="#00c853" if v>5 else ("#f44336" if v<-5 else "#ff9800"),
            score=1 if v>5 else (-1 if v<-5 else 0),
            weight=1, series=s24)

    # ── VIX ──────────────────────────────────────────────────────────
    s_vix = _yf_s("^VIX","1y")
    if len(s_vix) >= 6:
        v = round(float(s_vix.iloc[-1]),2); p = round(float(s_vix.iloc[-6]),2)
        s_m = s_vix.resample("W").last().tail(52)
        R["VIX"] = dict(name="VIX 恐慌指數", value=v, prev=p, unit="", type="同時",
            date="即時", desc="<18平靜 | >30恐慌=逢低加碼時機",
            signal="🟢" if v<18 else ("🔴" if v>30 else "🟡"),
            color="#00c853" if v<18 else ("#f44336" if v>30 else "#ff9800"),
            score=1 if v<18 else (-1 if v>30 else 0),
            weight=1, series=s_m)

    # ── CPI ──────────────────────────────────────────────────────────
    df = _fred("CPIAUCSL", fred_api_key, 120)
    if len(df) >= 14:
        s = df.set_index("date")["value"]
        yoy = (s / s.shift(12) - 1) * 100
        s24 = yoy.dropna().tail(36)
        v = float(s24.iloc[-1]); p = float(s24.iloc[-2])
        t = _trend(s24.tolist()[-6:])
        R["CPI"] = dict(name="CPI 通膨率 (YoY)", value=round(v,2), prev=round(p,2),
            unit="%", type="落後", date=str(df.iloc[-1]["date"])[:7],
            desc="目標2% | 高位回落=利多拐點", trend=t,
            signal="🟢" if 1<v<2.5 else ("🔴" if v>4 else "🟡"),
            color="#00c853" if 1<v<2.5 else ("#f44336" if v>4 else "#ff9800"),
            score=1 if 1<v<2.5 else (-1 if v>4 else 0),
            weight=0.5, series=s24)

    # ── Fed Rate ──────────────────────────────────────────────────────
    df = _fred("FEDFUNDS", fred_api_key, 60)
    if len(df) >= 2:
        s = df.set_index("date")["value"].tail(36)
        v = float(df.iloc[-1]["value"]); p = float(df.iloc[-2]["value"])
        R["FED_RATE"] = dict(name="聯準會利率", value=v, prev=p, unit="%", type="落後",
            date=str(df.iloc[-1]["date"])[:7], desc="降息=利多 | 升息=緊縮",
            trend=_trend(df["value"].tolist()[-8:]),
            signal="🟢" if v<p else ("🔴" if v>5 else "🟡"),
            color="#00c853" if v<p else ("#f44336" if v>5 else "#ff9800"),
            score=1 if v<p else (-1 if v>5 else 0),
            weight=0.5, series=s)

    # ── 失業率 ───────────────────────────────────────────────────────
    df = _fred("UNRATE", fred_api_key, 60)
    if len(df) >= 2:
        s = df.set_index("date")["value"].tail(36)
        v = float(df.iloc[-1]["value"]); p = float(df.iloc[-2]["value"])
        R["UNEMPLOYMENT"] = dict(name="失業率", value=v, prev=p, unit="%", type="落後",
            date=str(df.iloc[-1]["date"])[:7], desc="<4.5%健康 | 上升=景氣轉差",
            trend=_trend(df["value"].tolist()[-6:]),
            signal="🟢" if v<4.5 else ("🔴" if v>6 else "🟡"),
            color="#00c853" if v<4.5 else ("#f44336" if v>6 else "#ff9800"),
            score=1 if v<4.5 else (-2 if v>6 else 0),
            weight=0.5, series=s)

    # ── PPI ──────────────────────────────────────────────────────────
    df = _fred("PPIACO", fred_api_key, 36)
    if len(df) >= 13:
        s = df.set_index("date")["value"]
        yoy = (s / s.shift(12) - 1) * 100
        s24 = yoy.dropna().tail(24)
        v = float(s24.iloc[-1]) if len(s24) >= 1 else 0
        p = float(s24.iloc[-2]) if len(s24) >= 2 else None
        R["PPI"] = dict(name="PPI 生產者物價 (YoY)", value=round(v,2),
            prev=round(p,2) if p else None,
            unit="%", type="領先", date=str(df.iloc[-1]["date"])[:7],
            desc="領先CPI，0~3%溫和，>5%過熱",
            trend=_trend(s24.tolist()[-6:]),
            signal="🟢" if 0<v<3 else ("🔴" if v>5 or v<-1 else "🟡"),
            color="#00c853" if 0<v<3 else ("#f44336" if v>5 or v<-1 else "#ff9800"),
            score=0.5 if 0<v<3 else (-0.5 if v>5 else 0),
            weight=0.5, series=s24)

    # ── 銅博士 ────────────────────────────────────────────────────────
    s_cu = _yf_s("HG=F","2y")
    if len(s_cu) >= 22:
        now = float(s_cu.iloc[-1]); prev = float(s_cu.iloc[-22])
        chg = round((now-prev)/prev*100, 2) if prev else 0
        monthly = s_cu.resample("ME").last().pct_change()*100
        R["COPPER"] = dict(name="銅博士（月漲跌）", value=chg, prev=None,
            unit="% MoM", type="領先", date="即時",
            desc=f"現價 {now:.3f} USD/lb | 漲=工業需求增",
            signal="🟢" if chg>2 else ("🔴" if chg<-5 else "🟡"),
            color="#00c853" if chg>2 else ("#f44336" if chg<-5 else "#ff9800"),
            score=0.5 if chg>2 else (-0.5 if chg<-5 else 0),
            weight=0.5, series=monthly.dropna().tail(24))

    # ── 消費者信心 ────────────────────────────────────────────────────
    df = _fred("UMCSENT", fred_api_key, 36)
    if len(df) >= 2:
        s = df.set_index("date")["value"].tail(24)
        v = float(df.iloc[-1]["value"]); p = float(df.iloc[-2]["value"])
        R["CONSUMER_CONF"] = dict(name="消費者信心 (Michigan)", value=v, prev=p,
            unit="", type="領先", date=str(df.iloc[-1]["date"])[:7],
            desc="上升=消費回升，>85樂觀，<60悲觀",
            trend=_trend(df["value"].tolist()[-6:]),
            signal="🟢" if v>80 else ("🔴" if v<60 else "🟡"),
            color="#00c853" if v>80 else ("#f44336" if v<60 else "#ff9800"),
            score=0.5 if v>80 else (-0.5 if v<60 else 0),
            weight=0.5, series=s)

    # ── 初領失業金 ────────────────────────────────────────────────────
    df = _fred("ICSA", fred_api_key, 52)
    if len(df) >= 2:
        s = df.set_index("date")["value"].tail(52)
        v = float(df.iloc[-1]["value"]); p = float(df.iloc[-2]["value"])
        R["JOBLESS"] = dict(name="初領失業金 (週)", value=int(v), prev=int(p),
            unit="萬人", type="領先", date=str(df.iloc[-1]["date"])[:10],
            desc="下降=就業好轉，<23萬健康，>30萬警戒",
            trend=_trend(df["value"].tolist()[-8:]),
            signal="🟢" if v<230000 else ("🔴" if v>300000 else "🟡"),
            color="#00c853" if v<230000 else ("#f44336" if v>300000 else "#ff9800"),
            score=0.5 if v<230000 else (-0.5 if v>300000 else 0),
            weight=0.5, series=s/10000)

    # ── 新屋銷售 ──────────────────────────────────────────────────────
    df = _fred("HSN1F", fred_api_key, 36)
    if len(df) >= 2:
        s = df.set_index("date")["value"].tail(24)
        v = float(df.iloc[-1]["value"]); p = float(df.iloc[-2]["value"])
        R["NEW_HOME"] = dict(name="新屋銷售", value=v, prev=p, unit="千戶", type="領先",
            date=str(df.iloc[-1]["date"])[:7], desc=f"月增{v-p:+.0f}k | 增加=房市回升",
            trend=_trend(df["value"].tolist()[-6:]),
            signal="🟢" if v>p else "🔴", color="#00c853" if v>p else "#f44336",
            score=0.5 if v>p else -0.5,
            weight=0.5, series=s)

    # ── 薩姆規則（Sahm Rule Recession Indicator）──────────────────────
    df = _fred("SAHMREALTIME", fred_api_key, 60)
    if len(df) >= 2:
        s = df.set_index("date")["value"].tail(36)
        v = float(df.iloc[-1]["value"]); p = float(df.iloc[-2]["value"])
        R["SAHM"] = dict(name="薩姆規則", value=v, prev=p, unit="pp", type="領先",
            date=str(df.iloc[-1]["date"])[:7],
            desc="≥0.5 觸發衰退警報 | <0.3 安全 | 3月失業率均值-12月最低",
            trend=_trend(df["value"].tolist()[-6:]),
            signal="🔴" if v >= 0.5 else ("🟡" if v >= 0.3 else "🟢"),
            color="#f44336" if v >= 0.5 else ("#ff9800" if v >= 0.3 else "#00c853"),
            score=-2 if v >= 0.5 else (-0.5 if v >= 0.3 else 1),
            weight=1.5, series=s)

    # ── SLOOS 銀行放貸標準（Senior Loan Officer Survey）──────────────
    df = _fred("DRTSCILM", fred_api_key, 40)
    if len(df) >= 2:
        s = df.set_index("date")["value"].tail(24)
        v = float(df.iloc[-1]["value"]); p = float(df.iloc[-2]["value"])
        # 正值=銀行收緊放貸(壞)，負值=放寬(好)
        R["SLOOS"] = dict(name="SLOOS 放貸標準", value=v, prev=p, unit="%", type="領先",
            date=str(df.iloc[-1]["date"])[:7],
            desc=">20% 銀行大幅緊縮信貸（衰退前兆）| <0% 信貸寬鬆",
            trend=_trend(df["value"].tolist()[-4:]),
            signal="🔴" if v > 20 else ("🟡" if v > 0 else "🟢"),
            color="#f44336" if v > 20 else ("#ff9800" if v > 0 else "#00c853"),
            score=-2 if v > 30 else (-1 if v > 20 else (0.5 if v < 0 else -0.5)),
            weight=1.5, series=s)

    return R
def get_market_phase(indicators: dict) -> dict:
    """
    二維景氣位階判定（說明書 §3）：Z-Score 位階 × 線性斜率方向
    ─────────────────────────────────────────────────────────────
    以 PMI 為主要代表指標（最高權重領先指標），結合 Z-Score 與 trend_slope：

      復甦 (Recovery) : Z 低位(< -0.5) + Slope 轉正(> +0.05)
      擴張 (Expansion): Z 中位         + Slope 為正(> 0)
      減速 (Slowdown) : Z 高位(> +0.5) + Slope 轉負(< -0.05) ← 關鍵拐點
      衰退 (Recession): Z 低位         + Slope 為負(< 0)

    回傳字典可直接補充至 calc_macro_phase() 輸出，作為第二層確認。
    """
    def _get(key, attr): return (indicators.get(key) or {}).get(attr)

    # ── 以 PMI + YIELD_10Y2Y + HY_SPREAD 三個領先指標投票
    _phases = []
    for _key in ("PMI", "YIELD_10Y2Y", "HY_SPREAD"):
        _z  = _get(_key, "z_score")
        _sl = _get(_key, "trend_slope")
        if _z is None or _sl is None:
            continue
        # 反向指標（HY 利差越大越壞）
        _inv = -1 if _key == "HY_SPREAD" else 1
        _z_adj  = _z  * _inv
        _sl_adj = _sl * _inv

        if _z_adj < -0.5 and _sl_adj > 0.05:
            _phases.append("復甦")
        elif _z_adj > 0.5 and _sl_adj < -0.05:
            _phases.append("減速")   # 最重要的高位轉負訊號
        elif _sl_adj > 0:
            _phases.append("擴張")
        else:
            _phases.append("衰退")

    if not _phases:
        return {"phase2d": "未知", "phase2d_color": "#888", "phase2d_desc": "資料不足"}

    # 多數決
    from collections import Counter
    _winner = Counter(_phases).most_common(1)[0][0]
    _vote_ratio = Counter(_phases).most_common(1)[0][1] / len(_phases)

    _map = {
        "復甦": ("#64b5f6", "Z 低位 + 斜率翻正，景氣底部確認，逢低布局機會"),
        "擴張": ("#00c853", "Z 中位 + 斜率向上，成長動能充足，持有風險資產"),
        "減速": ("#ff9800", "Z 高位 + 斜率轉負，擴張減速拐點！考慮調降衛星比重"),
        "衰退": ("#f44336", "Z 低位 + 斜率向下，景氣收縮，轉向防禦配置"),
    }
    _color, _desc = _map.get(_winner, ("#888", ""))
    return {
        "phase2d":        _winner,
        "phase2d_color":  _color,
        "phase2d_desc":   _desc,
        "phase2d_votes":  dict(Counter(_phases)),
        "phase2d_conf":   round(_vote_ratio * 100),
    }


def get_synced_dashboard_data(raw_data_dict: dict, lookback_days: int = 30) -> pd.DataFrame:
    """
    數據對齊補丁 v1：統一時間軸 + 假日前向填充（文件建議 §2）
    1. 建立完整日曆時間索引
    2. 自動填充假日空值（前向填充，上限 5 天）
    3. 若仍有空值 → 資料嚴重斷連，透過 Streamlit 警告
    """
    full_idx = pd.date_range(
        end=pd.Timestamp.now().normalize(), periods=lookback_days, freq='D'
    )
    main_df = pd.DataFrame(index=full_idx)

    for name, data in raw_data_dict.items():
        if isinstance(data, pd.Series):
            s = data.copy()
        elif isinstance(data, (list,)):
            s = pd.Series(data)
        else:
            continue
        s.index = pd.to_datetime(s.index).normalize()
        main_df[name] = s

    # 核心修正：前向填充假日數據，限制回溯 5 天
    main_df = main_df.ffill(limit=5)

    # 若仍有空值，代表數據嚴重斷連
    if main_df.iloc[-1].isnull().any():
        missing = main_df.columns[main_df.iloc[-1].isnull()].tolist()
        st.warning(f"⚠️ 部分數據源連線不穩：{', '.join(missing)}，請檢查網路或 API 配額")

    return main_df


def calc_growth_inflation_axis(indicators: dict) -> dict:
    """
    成長/通膨雙軸分析（文件建議 §1：二象限循環判定）
    ─────────────────────────────────────────────────
    Growth Axis  : PMI, 殖利率曲線, M2, 市場廣度, 消費者信心, 初領失業金, 銅博士
    Inflation Axis: CPI, PPI, Fed Rate
    ─────────────────────────────────────────────────
    四象限:
      復甦/擴張 (Goldilocks): 成長↑ 通膨↓
      過熱 (Overheat)       : 成長↑ 通膨↑
      滯脹 (Stagflation)    : 成長↓ 通膨↑
      衰退 (Recession)      : 成長↓ 通膨↓
    """
    def _get(key, attr="value"):
        return (indicators.get(key) or {}).get(attr)

    # ── Growth signals（正=成長向上，負=成長向下）
    growth_signals = []
    pmi_v = _get("PMI")
    if pmi_v is not None:
        growth_signals.append(1 if pmi_v >= 50 else -1)

    y22 = _get("YIELD_10Y2Y")
    if y22 is not None:
        growth_signals.append(1 if y22 >= 0 else -1)

    m2_v = _get("M2")
    if m2_v is not None:
        growth_signals.append(1 if m2_v >= 3 else -1)

    adl_chg = _get("ADL", "prev")  # prev = monthly change %
    if adl_chg is not None:
        growth_signals.append(1 if adl_chg >= 0 else -1)

    conf_v = _get("CONSUMER_CONF")
    if conf_v is not None:
        growth_signals.append(1 if conf_v >= 70 else -1)

    jobless_v = _get("JOBLESS")
    if jobless_v is not None:
        growth_signals.append(1 if jobless_v < 280000 else -1)

    copper_v = _get("COPPER")  # monthly change %
    if copper_v is not None:
        growth_signals.append(1 if copper_v >= 0 else -1)

    # ── Inflation signals（正=通膨偏高，負=通膨受控）
    inflation_signals = []
    cpi_v = _get("CPI")
    if cpi_v is not None:
        inflation_signals.append(1 if cpi_v >= 3.0 else -1)

    ppi_v = _get("PPI")
    if ppi_v is not None:
        inflation_signals.append(1 if ppi_v >= 3.0 else -1)

    fed_v = _get("FED_RATE")
    if fed_v is not None:
        inflation_signals.append(1 if fed_v >= 4.0 else -1)

    # ── 計算平均訊號分數（-1 ~ +1）
    growth_score    = sum(growth_signals)    / max(len(growth_signals), 1)
    inflation_score = sum(inflation_signals) / max(len(inflation_signals), 1)
    growth_up    = growth_score > 0
    inflation_up = inflation_score > 0

    # ── 四象限映射
    if growth_up and not inflation_up:
        quadrant    = "復甦/擴張"; quadrant_en = "Goldilocks"
        quad_color  = "#00c853";   quad_icon   = "🌱"
        quad_desc   = "成長↑ 通膨↓ — 黃金期，積極持有風險資產"
        quad_alloc  = "衛星成長型↑  核心配息↑  現金↓"
    elif growth_up and inflation_up:
        quadrant    = "過熱";      quadrant_en = "Overheat"
        quad_color  = "#ff9800";   quad_icon   = "🔥"
        quad_desc   = "成長↑ 通膨↑ — 景氣高峰，注意泡沫與緊縮風險"
        quad_alloc  = "實物資產↑  高息防禦↑  成長型↓"
    elif not growth_up and inflation_up:
        quadrant    = "滯脹";      quadrant_en = "Stagflation"
        quad_color  = "#f44336";   quad_icon   = "⚠️"
        quad_desc   = "成長↓ 通膨↑ — 最惡劣環境，降低股票，持有商品與短債"
        quad_alloc  = "商品/黃金↑  短天期債↑  成長股↓↓"
    else:
        quadrant    = "衰退";      quadrant_en = "Recession"
        quad_color  = "#ff9800";   quad_icon   = "🌧️"
        quad_desc   = "成長↓ 通膨↓ — 景氣收縮，轉向長債與防禦型配置"
        quad_alloc  = "長天期債↑↑  防禦股息↑  現金↑  成長股↓"

    return {
        "growth_score":     round(growth_score, 2),
        "inflation_score":  round(inflation_score, 2),
        "growth_up":        growth_up,
        "inflation_up":     inflation_up,
        "quadrant":         quadrant,
        "quadrant_en":      quadrant_en,
        "quad_color":       quad_color,
        "quad_icon":        quad_icon,
        "quad_desc":        quad_desc,
        "quad_alloc":       quad_alloc,
        "n_growth":         len(growth_signals),
        "n_inflation":      len(inflation_signals),
    }


def calc_macro_phase(indicators: dict) -> dict:
    """
    AI Macro Score 加權評分（機構級 v7）
    ─────────────────────────────────────────────────
    指標                    weight    分值
    殖利率曲線 10Y-2Y          2      ±2
    殖利率曲線 10Y-3M          2      ±2
    PMI                       2      ±2
    HY 信用利差                2      ±2
    M2 流動性                  1      ±1
    市場廣度 RSP/SPY           1      ±1
    Fed 資產負債表             1      ±1
    DXY 美元指數               1      ±1
    VIX 恐慌指數               1      ±1
    CPI 通膨                  0.5     ±0.5
    Fed Rate                 0.5     ±0.5
    失業率                    0.5     ±0.5
    ─────────────────────────────────────────────────
    最大可能 ≈ 14 → 正規化到 0~10
    景氣判斷：0~2衰退 | 3~4復甦 | 5~7擴張 | 8~10高峰
    """
    # 加權加總
    total_w = 0; earned_w = 0
    for key, ind in indicators.items():
        w = ind.get("weight", 1)
        s = ind.get("score", 0)
        # 確保 score 不超過 weight
        s = max(-w, min(w, s))
        total_w += w
        earned_w += s

    # 正規化：把 [-total_w, +total_w] 映射到 [0, 10]
    if total_w > 0:
        norm = (earned_w + total_w) / (2 * total_w) * 10
    else:
        norm = 5
    score = round(max(0, min(10, norm)), 1)

    # ─── 修正後的景氣門檻 ───
    if score >= 8:
        phase = "高峰"; phase_en = "Peak"; phase_color = "#f44336"
        alloc = dict(股票=35, 債券=45, 現金=20)
        advice = "高峰期：適度獲利了結，轉向防禦型資產"
        strategy = "逐步減碼高估值成長股，增加投資等級債與黃金"
    elif score >= 5:
        phase = "擴張"; phase_en = "Expansion"; phase_color = "#00c853"
        alloc = dict(股票=60, 債券=30, 現金=10)
        advice = "股優於債：核心高股息ETF + 衛星AI/半導體，設嚴格停利點"
        strategy = "持有核心配息資產，衛星資產設15%停利出場"
    elif score >= 3:
        phase = "復甦"; phase_en = "Recovery"; phase_color = "#64b5f6"
        alloc = dict(股票=40, 債券=40, 現金=20)
        advice = "復甦期：最高勝率買點！逐步加碼，優先佈局高股息與平衡型"
        strategy = "積極佈局中小型成長股、非必需消費、金融股底部"
    else:
        phase = "衰退"; phase_en = "Recession"; phase_color = "#ff9800"
        alloc = dict(股票=20, 債券=50, 現金=30)
        advice = "衰退期：保守為主，等待落後指標見頂為進場訊號"
        strategy = "保留現金，等待PMI落底與殖利率曲線翻正"

    # 衰退機率
    sp3m = indicators.get("YIELD_10Y3M", {}).get("value")
    rec_prob = None
    if sp3m is not None:
        import math
        logit = -1.5 * sp3m - 0.8
        rec_prob = round(1 / (1 + math.exp(-logit)) * 100, 1)

    # 風險警報
    alerts = []
    if indicators.get("YIELD_10Y2Y",{}).get("value", 1) < 0:
        alerts.append("⚠️ 殖利率曲線倒掛（衰退前兆）")
    if indicators.get("HY_SPREAD",{}).get("value", 4) > 6:
        alerts.append("⚠️ 信用利差>6% — 市場恐慌升溫")
    if indicators.get("PMI",{}).get("value", 50) < 50:
        alerts.append("⚠️ PMI 跌破 50 — 製造業收縮")
    if indicators.get("VIX",{}).get("value", 18) > 25:
        alerts.append("⚠️ VIX>25 — 市場恐慌，注意波動")
    if indicators.get("CPI",{}).get("value", 2) > 4:
        alerts.append("⚠️ 通膨偏高 — Fed 緊縮壓力")
    if indicators.get("M2",{}).get("value", 3) < 0:
        alerts.append("⚠️ M2 負成長 — 流動性緊縮")
    if indicators.get("ADL",{}).get("prev", 0) < -1:
        alerts.append("⚠️ 市場廣度惡化 — 僅少數股支撐指數")
    if rec_prob and rec_prob > 60:
        alerts.append(f"🔴 衰退機率 {rec_prob:.0f}% — 高度警戒")

    # MK 拐點偵測
    mk_signals = _detect_inflection(indicators)

    # ── 拐點轉向判斷 ─────────────────────────────────────
    PHASE_ORDER = ["衰退", "復甦", "擴張", "高峰"]
    infl_score = mk_signals.get("infl_score", 0)
    ph_idx = PHASE_ORDER.index(phase)

    if infl_score >= 5:         # 多項買進訊號齊發 → 向上轉
        next_phase = PHASE_ORDER[(ph_idx + 1) % 4]
        trend_arrow = "↗"
        trend_label = "向上轉折（加速）"
        trend_color = "#00c853"
    elif infl_score >= 2:       # 偏多觀察 → 偏向上
        next_phase = PHASE_ORDER[(ph_idx + 1) % 4]
        trend_arrow = "→↗"
        trend_label = "偏向上（觀察中）"
        trend_color = "#69f0ae"
    elif infl_score <= -5:      # 多項空頭訊號 → 向下轉
        next_phase = PHASE_ORDER[(ph_idx - 1) % 4]
        trend_arrow = "↘"
        trend_label = "向下轉折（警示）"
        trend_color = "#f44336"
    elif infl_score <= -2:      # 偏空謹慎 → 偏向下
        next_phase = PHASE_ORDER[(ph_idx - 1) % 4]
        trend_arrow = "→↘"
        trend_label = "偏向下（謹慎）"
        trend_color = "#ff7043"
    else:                       # 中性整理
        next_phase = phase
        trend_arrow = "→"
        trend_label = "持穩整理"
        trend_color = "#888888"

    # ── 各景氣位階配置 Map（供拐點轉換顯示）────────────────
    ALLOC_MAP = {
        "復甦": dict(股票=40, 債券=40, 現金=20),
        "擴張": dict(股票=60, 債券=30, 現金=10),
        "高峰": dict(股票=35, 債券=45, 現金=20),
        "衰退": dict(股票=20, 債券=50, 現金=30),
    }
    cur_idx  = ph_idx  # 複用已計算的 ph_idx，消除重複定義
    next_p   = PHASE_ORDER[(cur_idx + 1) % 4]
    prev_p   = PHASE_ORDER[(cur_idx - 1) % 4]
    next_alloc = ALLOC_MAP[next_p]
    cur_alloc  = ALLOC_MAP[phase] if phase in ALLOC_MAP else alloc

    # 拐點發生時的配置變更說明
    alloc_transition = {
        k: {"from": cur_alloc.get(k,0), "to": next_alloc.get(k,0)}
        for k in ["股票","債券","現金"]
    }

    # v15: Weather metaphor (before return dict)
    _weather_tup = (
        ("☀️", "晴天", "#ffd54f",
         "股 {}% / 債 {}% / 現金 {}%".format(alloc.get("股票",60),alloc.get("債券",30),alloc.get("現金",10)))
        if score >= 7 else
        ("⛅", "多雲", "#90caf9",
         "股 {}% / 債 {}% / 現金 {}%".format(alloc.get("股票",50),alloc.get("債券",40),alloc.get("現金",10)))
        if score >= 4 else
        ("⛈️", "暴雨", "#ef9a9a",
         "股 {}% / 債 {}% / 現金 {}%".format(alloc.get("股票",30),alloc.get("債券",50),alloc.get("現金",20)))
    )
    _w_icon, _w_label, _w_color, _w_alloc_str = _weather_tup

    # 成長/通膨雙軸分析（文件建議 §1 二象限循環判定）
    growth_inflation = calc_growth_inflation_axis(indicators)
    # Z-Score × Slope 二維景氣位階（說明書 §3）
    market_phase_2d  = get_market_phase(indicators)

    return dict(
        score=score, phase=phase, phase_en=phase_en,
        phase_color=phase_color, alloc=alloc,
        weather_icon=_w_icon, weather_label=_w_label,
        weather_color=_w_color, weather_alloc_str=_w_alloc_str,
        advice=advice, strategy=strategy,
        alerts=alerts, mk_signals=mk_signals,
        rec_prob=rec_prob,
        # 拐點轉向
        next_phase=next_phase,
        next_phase_name=next_p,
        trend_arrow=trend_arrow,
        trend_label=trend_label,
        trend_color=trend_color,
        alloc_transition=alloc_transition,
        # 雙軸分析
        growth_inflation=growth_inflation,
        market_phase_2d=market_phase_2d,
        # 保留舊 key 供 AI engine 使用
        inflection=mk_signals.get("inflection",{}),
        signals=mk_signals.get("signals",[]),
        allocation=alloc,
    )


# ══════════════════════════════════════════════════════════════
# v13 新增：Z-Score 工具 & 景氣循環辨識模型（Regime Model）
# ══════════════════════════════════════════════════════════════

def zscore(series: pd.Series) -> pd.Series:
    """標準化 Z-Score，用於指標估值判斷"""
    if series.std() == 0:
        return pd.Series([0.0] * len(series), index=series.index)
    return (series - series.mean()) / series.std()


def identify_regime(indicators: dict) -> dict:
    """
    景氣循環辨識模型（v13）
    依 PMI、CPI、FED_RATE 四象限判斷：
      復甦 / 成長 / 過熱 / 衰退
    額外輸出 Z-Score 估值與配置建議
    """
    pmi_v   = (indicators.get("PMI")      or {}).get("value")
    cpi_v   = (indicators.get("CPI")      or {}).get("value")
    fed_v   = (indicators.get("FED_RATE") or {}).get("value")
    fed_p   = (indicators.get("FED_RATE") or {}).get("prev")
    hy_v    = (indicators.get("HY_SPREAD") or {}).get("value")

    # ── 四象限判斷 ────────────────────────────────────────
    if pmi_v is None:
        regime = "未知"; regime_color = "#888888"
    elif pmi_v >= 52 and (cpi_v or 0) < 3.5:
        regime = "🟢 成長期"; regime_color = "#00c853"
    elif pmi_v >= 52 and (cpi_v or 0) >= 3.5:
        regime = "🟡 過熱期"; regime_color = "#ff9800"
    elif pmi_v < 50 and (fed_v or 5) <= (fed_p or 5):
        regime = "🔵 復甦期"; regime_color = "#2196f3"
    else:
        regime = "🔴 衰退期"; regime_color = "#f44336"

    # ── Z-Score 估值判斷（PMI / HY_SPREAD）──────────────
    pmi_series = (indicators.get("PMI") or {}).get("series")
    zscore_pmi = None
    if pmi_series is not None and len(pmi_series) >= 12:
        z = float(zscore(pmi_series).iloc[-1])
        if z < -1.5:   zscore_pmi = {"label": "PMI 低估（買進訊號）", "z": round(z,2), "signal": "🟢"}
        elif z > 1.5:  zscore_pmi = {"label": "PMI 高估（過熱警告）", "z": round(z,2), "signal": "🔴"}
        else:          zscore_pmi = {"label": "PMI 中性",             "z": round(z,2), "signal": "🟡"}

    # ── 配置建議（依循環調整）────────────────────────────
    alloc_by_regime = {
        "🟢 成長期": {"股票型": 50, "核心債券": 30, "衛星主題": 20},
        "🟡 過熱期": {"股票型": 30, "核心債券": 40, "實物資產": 20, "現金": 10},
        "🔵 復甦期": {"股票型": 45, "核心債券": 35, "衛星主題": 15, "現金": 5},
        "🔴 衰退期": {"投資等級債": 50, "貨幣型": 30, "防禦股息": 20},
        "未知":      {"核心債券": 40, "股票型": 40, "現金": 20},
    }
    alloc = alloc_by_regime.get(regime, alloc_by_regime["未知"])

    return {
        "regime":        regime,
        "regime_color":  regime_color,
        "zscore_pmi":    zscore_pmi,
        "hy_spread":     hy_v,
        "alloc_suggest": alloc,
        "note": f"PMI:{pmi_v} CPI:{cpi_v} FedRate:{fed_v}",
    }


# ══════════════════════════════════════════════════════════════════
# v15: 台灣市場轉折點指標 (TPI — Three-Factor Resonance)
# TPI = Z(M1B/M2) × 0.3 + Z(Breadth) × 0.4 + Z(FII) × 0.3
# 資料來源：證交所 OpenAPI（免費，無需 Key）
# ══════════════════════════════════════════════════════════════════
def fetch_tw_market_tpi(fred_api_key: str = "") -> dict:
    """
    台股三因子轉折指標 (TPI v15.2)
    TPI = Z(市場寬度)×0.4 + Z(外資淨買)×0.3 + Z(M1B/M2)×0.3
    資料：TWSE MI_INDEX + FinMind API + FRED Taiwan M1/M2
    """
    import requests, re as _re, datetime as _dt

    result = {
        "tpi": None, "z_breadth": None, "z_fii": None, "z_m1b_m2": 0.0,
        "fii_net": None, "breadth": None,
        "water_label": "資料取得中", "color": "#888",
        "signal": "⬜", "advice": "", "date": "", "error": None,
        "_fred_api_key": fred_api_key,  # passed through for Factor C
    }
    _HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    today = _dt.date.today()

    # ── Factor A: 市場寬度（TWSE MI_INDEX 漲跌家數）─────────────────
    try:
        url_mi = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?response=json&type=MS"
        r_mi = requests.get(url_mi, headers=_HDR, timeout=12)
        if r_mi.status_code == 200:
            d_mi = r_mi.json()
            result["date"] = d_mi.get("date", "")
            for tbl in (d_mi.get("tables") or []):
                if not isinstance(tbl, dict): continue
                rows = tbl.get("data", [])
                if not any("上漲" in str(row) for row in rows): continue
                adv = dec = 0
                for row in rows:
                    row_s = str(row[0]) if row else ""
                    mkt_s = str(row[1]) if len(row) > 1 else ""
                    nums  = _re.findall(r"[\d,]+", mkt_s)
                    val   = int(nums[0].replace(",", "")) if nums else 0
                    if "上漲" in row_s:   adv = val
                    elif "下跌" in row_s: dec = val
                if adv + dec > 0:
                    result["breadth"]   = round((adv - dec) / (adv + dec) * 100, 2)
                    result["z_breadth"] = max(-3.0, min(3.0, result["breadth"] / 20.0))
                    print(f"[TPI] 上漲:{adv} 下跌:{dec} Breadth:{result['breadth']:.1f}% Z:{result['z_breadth']:.3f}")
                break
    except Exception as e:
        result["error"] = f"MI_INDEX: {str(e)[:60]}"
        print(f"[TPI] MI_INDEX err: {e}")

    # ── Factor B: 外資籌碼（FinMind API，免費無需 Key）──────────────
    try:
        end_dt   = today.strftime("%Y-%m-%d")
        start_dt = (today - _dt.timedelta(days=7)).strftime("%Y-%m-%d")
        url_fm   = (
            "https://api.finmindtrade.com/api/v4/data"
            "?dataset=TaiwanStockTotalInstitutionalInvestors"
            f"&start_date={start_dt}&end_date={end_dt}"
        )
        r_fm = requests.get(url_fm, headers=_HDR, timeout=12)
        if r_fm.status_code == 200:
            fi_rows = [r for r in r_fm.json().get("data", [])
                       if r.get("name") == "Foreign_Investor"]
            if fi_rows:
                fi_rows.sort(key=lambda x: x.get("date", ""), reverse=True)
                latest  = fi_rows[0]
                fii_net = int(latest.get("buy", 0)) - int(latest.get("sell", 0))
                result["fii_net"]  = fii_net
                result["z_fii"]    = max(-3.0, min(3.0, fii_net / 5_000_000_000))
                print(f"[TPI] FII {latest['date']} net:{fii_net:+,} Z:{result['z_fii']:.3f}")
    except Exception as e:
        result["error"] = (result.get("error") or "") + f" | FII:{str(e)[:50]}"
        print(f"[TPI] FinMind err: {e}")

    # ── Factor C: 台灣 M1B/M2（三層備援）─────────────────────
    # Tier 1: https://www.cbc.gov.tw/public/data/ms1.json
    # Tier 2: https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=EF15M01
    # Tier 3: yfinance ^TWII 動能代理（保底）
    _m1b_yoy = _m2_yoy = None
    _m1b_is_proxy = False

    # ── Tier 1: CBC 官方公開 JSON ──────────────────────────────
    for _cbc_url in [
        'https://www.cbc.gov.tw/public/data/ms1.json',
        'https://www.cbc.gov.tw/tw/public/data/ms1.json',
    ]:
        if _m1b_yoy is not None: break
        try:
            import pandas as _pd_cbc
            _r1 = requests.get(_cbc_url, headers=_HDR, timeout=12)
            print(f'[M1B] Tier1 {_cbc_url.split("/")[-1]}: HTTP {_r1.status_code}')
            if _r1.status_code == 200:
                _d1 = _r1.json()
                if isinstance(_d1, list) and len(_d1) >= 13:
                    _df1 = _pd_cbc.DataFrame(_d1)
                    print(f'[M1B] Tier1 cols={list(_df1.columns)[:8]}')
                    _c1 = next((c for c in _df1.columns
                                if 'M1B' in str(c).upper() or '貨幣供給額M1B' in str(c)), None)
                    _c2 = next((c for c in _df1.columns
                                if str(c).strip().upper() == 'M2' or '貨幣供給額M2' in str(c)), None)
                    if _c1 and _c2:
                        _s1 = _pd_cbc.to_numeric(_df1[_c1], errors='coerce').dropna()
                        _s2 = _pd_cbc.to_numeric(_df1[_c2], errors='coerce').dropna()
                        if len(_s1) >= 13:
                            _m1b_yoy = round((_s1.iloc[-1] / _s1.iloc[-13] - 1) * 100, 2)
                            _m2_yoy  = round((_s2.iloc[-1] / _s2.iloc[-13] - 1) * 100, 2)
                            print(f'[M1B] Tier1 ✅ M1B:{_m1b_yoy:.2f}% M2:{_m2_yoy:.2f}%')
                    else:
                        print(f'[M1B] Tier1 欄位找不到 M1B={_c1} M2={_c2} | 所有欄={list(_df1.columns)}')
        except Exception as _e1:
            print(f'[M1B] Tier1 ❌ {_e1}')

    # ── Tier 2: CBC SDMX API (EF15M01) ────────────────────────
    if _m1b_yoy is None:
        try:
            import pandas as _pd_cbc
            _r2 = requests.get(
                'https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=EF15M01',
                headers=_HDR, timeout=15)
            print(f'[M1B] Tier2 EF15M01: HTTP {_r2.status_code}')
            if _r2.status_code == 200:
                _d2    = _r2.json()
                _rows2 = _d2.get('DataSet', [])
                _dims2 = (_d2.get('Structure') or {}).get('Dimensions', [])
                _cmap2 = {}
                for _dim in (_dims2 if isinstance(_dims2, list) else []):
                    if isinstance(_dim, dict):
                        _cmap2[str(_dim.get('id',''))] = str(_dim.get('name',''))
                # If no Structure, try keys directly
                if not _cmap2 and _rows2:
                    _cmap2 = {k: k for k in (_rows2[0] if isinstance(_rows2[0], dict) else {})}
                _ck1 = next((k for k,v in _cmap2.items() if 'M1B' in v.upper()), None)
                _ck2 = next((k for k,v in _cmap2.items() if v.strip().upper() in ('M2','M2 ')), None)
                if not _ck1 and _rows2:
                    _ck1 = next((k for k in (_rows2[0] if isinstance(_rows2[0],dict) else {}) if 'M1B' in k.upper()), None)
                if not _ck2 and _rows2:
                    _ck2 = next((k for k in (_rows2[0] if isinstance(_rows2[0],dict) else {}) if k.strip().upper()=='M2'), None)
                print(f'[M1B] Tier2 rows={len(_rows2)} m1b_col={_ck1} m2_col={_ck2}')
                if _ck1 and _ck2 and len(_rows2) >= 13:
                    _sv1, _sv2 = [], []
                    for _row in _rows2:
                        if not isinstance(_row, dict): continue
                        try:
                            _sv1.append(float(str(_row.get(_ck1,'')).replace(',','')))
                            _sv2.append(float(str(_row.get(_ck2,'')).replace(',','')))
                        except Exception: pass
                    if len(_sv1) >= 13:
                        _m1b_yoy = round((_sv1[-1]/_sv1[-13]-1)*100, 2)
                        _m2_yoy  = round((_sv2[-1]/_sv2[-13]-1)*100, 2)
                        print(f'[M1B] Tier2 ✅ M1B:{_m1b_yoy:.2f}% M2:{_m2_yoy:.2f}%')
        except Exception as _e2:
            print(f'[M1B] Tier2 ❌ {_e2}')

    # ── Tier 3: yfinance ^TWII 動能代理（保底）────────────────
    if _m1b_yoy is None:
        try:
            import yfinance as _yf3, pandas as _pd3t
            _twii_s = _yf3.Ticker("^TWII").history(period="6mo", auto_adjust=True)["Close"].dropna()
            if len(_twii_s) >= 60:
                _chg20 = round((_twii_s.iloc[-1]/_twii_s.iloc[-20]-1)*100, 2)
                _chg60 = round((_twii_s.iloc[-1]/_twii_s.iloc[-60]-1)*100, 2)
                _m1b_yoy = _chg20
                _m2_yoy  = round(_chg60/3, 2)
                _m1b_is_proxy = True
                print(f'[M1B] Tier3 proxy ✅ chg20={_chg20:.2f}% chg60={_chg60:.2f}%')
        except Exception as _e3:
            print(f'[M1B] Tier3 ❌ {_e3}')

    # ── 結果寫入 ──────────────────────────────────────────────
    if _m1b_yoy is not None:
        _gap = _m1b_yoy - _m2_yoy
        result["z_m1b_m2"]      = max(-3.0, min(3.0, _gap / 5.0))
        result["m1b_yoy"]       = _m1b_yoy
        result["m2_yoy"]        = _m2_yoy
        result["m1b_m2_gap"]    = round(_gap, 2)
        result["m1b_is_proxy"]  = _m1b_is_proxy
        _cross = "黃金" if _gap > 0 else "死亡"
        _src = "(代理估算)" if _m1b_is_proxy else ""
        print(f'[M1B] final ✅ M1B:{_m1b_yoy:.2f}% M2:{_m2_yoy:.2f}% Gap:{_gap:+.2f}% → {_cross}交叉 {_src}')
    else:
        result["z_m1b_m2"]     = 0.0
        result["m1b_is_proxy"] = False
        print('[M1B] ⚠️ 全部失敗，M1B/M2 設為 0')

    # ── Composite TPI ────────────────────────────────────────────
    z_b = result["z_breadth"] or 0.0
    z_f = result["z_fii"]     or 0.0
    z_m = result["z_m1b_m2"]
    tpi = z_b * 0.4 + z_f * 0.3 + z_m * 0.3
    result["tpi"] = round(tpi, 3)

    if tpi >= 1.5:
        result.update(water_label="🥵 沸點（市場過熱）", color="#f44336", signal="🔴",
                      advice="上漲家數銳減，外資持續賣超，建議啟動獲利了結機制")
    elif tpi >= 0.5:
        result.update(water_label="🌡️ 溫熱（偏多）", color="#ff9800", signal="🟡",
                      advice="市場動能良好，持續觀察是否過熱，衛星部位可設停利")
    elif tpi >= -0.5:
        result.update(water_label="⚖️ 常溫（中性）", color="#888888", signal="⚪",
                      advice="市場趨向均衡，維持既有配置，觀察漲跌家數變化")
    elif tpi >= -1.5:
        result.update(water_label="🌡️ 偏冷（謹慎）", color="#64b5f6", signal="🟡",
                      advice="外資轉弱、漲跌家數惡化，考慮降低台股部位")
    else:
        result.update(water_label="🥶 冰點（底部特徵）", color="#9c27b0", signal="🟢",
                      advice="散戶絕望期，偵測到底部特徵，準備分批建倉")

    return result


# ══════════════════════════════════════════════════════════════════
# v18.1 新聞系統性風險偵測（關鍵字加權評分）
# ══════════════════════════════════════════════════════════════════
_RISK_KEYWORDS = {
    # ── 流動性危機（最高風險）
    "default":       4, "debt crisis":   4, "bank run":       4,
    "bankruptcy":    4, "contagion":      4, "lehman":         4,
    "systemic":      3, "liquidity":      3, "credit crunch":  3,
    "違約":          4, "崩盤":           4, "擠兌":           4,
    "金融危機":      4, "系統性風險":     4, "破產":           3,
    # ── 衰退 / 停滯
    "recession":     3, "stagflation":    3, "depression":     3,
    "slowdown":      2, "contraction":    2, "gdp decline":    2,
    "衰退":          3, "滯脹":           3, "蕭條":           3,
    "負成長":        2, "景氣惡化":       2,
    # ── 央行緊急行動
    "emergency cut": 3, "rate hike":      2, "tightening":     2,
    "暴力升息":      3, "緊急降息":       3, "意外升息":       3,
    # ── 地緣政治 / 貿易
    "war":           2, "sanction":       2, "tariff":         2,
    "trade war":     2, "escalation":     2,
    "戰爭":          2, "制裁":           2, "關稅":           2,
    "脫鉤":          2, "升級":           1,
}

def detect_systemic_risk(news_items: list) -> dict:
    """
    對新聞列表做關鍵字加權掃描，回傳系統性風險評估。

    回傳格式：
    {
      "risk_level":  "HIGH" | "MEDIUM" | "LOW",
      "risk_score":  int,
      "risk_color":  str (hex),
      "risk_icon":   str,
      "triggered":   [{"keyword": str, "count": int, "weight": int, "sub_score": int}],
      "headlines":   [str],  ← 命中關鍵字的新聞標題
      "advice":      str,
    }
    算法：
      sub_score_i = keyword_weight_i × hit_count_i
      total_score = Σ sub_score_i
      HIGH   : score ≥ 10（多重高危信號，建議立即降低風險暴露）
      MEDIUM : score ≥ 5 （警示狀態，密切追蹤）
      LOW    : score <  5 （暫無系統性異常）
    """
    import re as _re

    all_text   = []
    title_map  = {}   # keyword → list of matching titles

    for item in (news_items or []):
        title   = str(item.get("title",   ""))
        summary = str(item.get("summary", ""))
        combined = (title + " " + summary).lower()
        all_text.append(combined)
        # 建立 keyword → title 映射（供展示用）
        for kw in _RISK_KEYWORDS:
            if kw in combined:
                title_map.setdefault(kw, []).append(title[:80])

    full_corpus = " ".join(all_text)
    triggered   = []
    total_score = 0

    for kw, weight in sorted(_RISK_KEYWORDS.items(), key=lambda x: -x[1]):
        # 使用 word boundary 避免誤判（e.g. "war" in "forward"）
        count = len(_re.findall(r'\b' + _re.escape(kw) + r'\b', full_corpus))
        if count > 0:
            sub = weight * min(count, 3)   # 同一關鍵字最多計 3 次，避免單篇洗版
            total_score += sub
            triggered.append({
                "keyword":   kw,
                "count":     count,
                "weight":    weight,
                "sub_score": sub,
            })

    # 命中關鍵字對應的標題（最多 5 則）
    hit_titles = []
    for kw in [t["keyword"] for t in triggered[:5]]:
        for title in title_map.get(kw, []):
            if title not in hit_titles:
                hit_titles.append(title)
    hit_titles = hit_titles[:5]

    # 風險等級判定
    if total_score >= 10:
        level  = "HIGH"
        color  = "#f44336"
        icon   = "🚨"
        advice = "偵測到多重高危信號，建議立即提高現金比重，核心部位 ≥80%，衛星部位設停損"
    elif total_score >= 5:
        level  = "MEDIUM"
        color  = "#ff9800"
        icon   = "⚠️"
        advice = "市場存在潛在壓力訊號，密切追蹤 VIX 與 HY 利差，衛星部位設停利"
    else:
        level  = "LOW"
        color  = "#00c853"
        icon   = "✅"
        advice = "新聞面暫無系統性異常，維持既有配置策略"

    return {
        "risk_level":  level,
        "risk_score":  total_score,
        "risk_color":  color,
        "risk_icon":   icon,
        "triggered":   triggered[:10],
        "headlines":   hit_titles,
        "advice":      advice,
    }
