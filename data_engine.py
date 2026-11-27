import os
import re
import io
import csv
import ssl
import json
import time
import base64
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
import urllib3
import requests
from requests.adapters import HTTPAdapter
import numpy as np
import pandas as pd
import yfinance as yf
from bs4 import BeautifulSoup
from strategy import (
    DAY_TRADE_BROKERS,
    STRATEGY_PARAMS,
    SCENARIO_MATRIX,
    is_foreign_broker,
    compute_rolling_zscore,
    profile_brokers,
    select_wave_main_forces,
    compute_wave_aggregate,
    compute_broker_breadth,
    evaluate_trust_phase,
    evaluate_chip_scenario,
    evaluate_divergence_alert,
    compute_score_backtest,  # noqa: F401
    build_z_tooltip_html,  # noqa: F401
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

DB_DIR = "database"
os.makedirs(DB_DIR, exist_ok=True)

ORDER_FILE = os.path.join(DB_DIR, "stock_order.json")
START_DATE_FIXED = "2026-01-02"

DJ_MIRRORS = [
    "https://fubon-ebrokerdj.fbs.com.tw",
    "https://djinfo.cathaysec.com.tw",
    "https://newjust.masterlink.com.tw",
    "https://kgieworld.moneydj.com",
    "https://pscnetinvest.moneydj.com",
    "https://5850web.moneydj.com",
]

FINMIND_API_URL = "https://api.finmindtrade.com/api/v4/data"

_TDCC_CACHE: dict = {"ts": 0.0, "map": {}}
_TDCC_CACHE_TTL = 3600
_SBL_OFFICIAL_CACHE: dict[tuple[str, str], dict[str, float]] = {}


class LegacySSLAdapter(HTTPAdapter):
    """解決 Python 3.11 / OpenSSL 3.0 連線台灣券商網站時的舊版 SSL 交握錯誤"""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def _create_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", LegacySSLAdapter())
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    })
    return session


def _get_finmind_token() -> str:
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if not token:
        try:
            import streamlit as st
            token = str(st.secrets.get("FINMIND_TOKEN", "")).strip()
        except Exception:
            pass
    return token


def _fetch_finmind_dataset(
    session: requests.Session,
    dataset: str,
    stock_id: str,
    start_date: str,
    end_date: str,
    timeout: int = 12,
) -> tuple[pd.DataFrame, str]:
    params = {
        "dataset": dataset,
        "data_id": stock_id,
        "start_date": start_date,
        "end_date": end_date,
    }
    token = _get_finmind_token()
    if token:
        params["token"] = token

    try:
        resp = session.get(FINMIND_API_URL, params=params, timeout=timeout, verify=False)
        if resp.status_code != 200:
            return pd.DataFrame(), f"FinMind {dataset}: HTTP {resp.status_code}"
        payload = resp.json()
        if payload.get("status") != 200:
            return pd.DataFrame(), f"FinMind {dataset}: {payload.get('msg', 'API異常')}"
        rows = payload.get("data") or []
        if not rows:
            return pd.DataFrame(), ""
        return pd.DataFrame(rows), ""
    except Exception as e:
        return pd.DataFrame(), f"FinMind {dataset}: {type(e).__name__}"


def _fetch_dj_html(session: requests.Session, path_with_query: str, preferred_mirror: str = None, timeout: int = 6):
    mirrors = [preferred_mirror] + [m for m in DJ_MIRRORS if m != preferred_mirror] if preferred_mirror else DJ_MIRRORS
    errors = []
    for base_url in mirrors:
        url = f"{base_url}{path_with_query}"
        try:
            resp = session.get(url, headers={"Referer": f"{base_url}/"}, timeout=timeout, verify=False)
            if resp.status_code == 200 and len(resp.content) > 500:
                resp.encoding = "cp950"
                return resp.text, base_url, ""
            errors.append(f"{base_url}: HTTP {resp.status_code}")
        except Exception as e:
            errors.append(f"{base_url}: {type(e).__name__}")
    return "", None, " | ".join(errors[:2])


def _parse_dj_date(date_str: str) -> str:
    """支援西元年 (2026/11/20) 與民國年 (115/11/20、含除權息星號)，統一轉為 YYYY-MM-DD"""
    s = str(date_str or "").strip().replace("*", "").replace("-", "/")
    m = re.match(r"^(\d{2,4})/(\d{1,2})/(\d{1,2})$", s)
    if not m:
        return ""
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 1911:
        year += 1911
    return f"{year:04d}-{month:02d}-{day:02d}"


def _format_dj_date(ad_date: str) -> str:
    dt = datetime.strptime(ad_date, "%Y-%m-%d")
    return f"{dt.year}-{dt.month}-{dt.day}"


def _date_diff_days(later_dt: str, earlier_dt: str) -> int | None:
    try:
        d1 = datetime.strptime(str(later_dt)[:10], "%Y-%m-%d")
        d2 = datetime.strptime(str(earlier_dt)[:10], "%Y-%m-%d")
        return max(0, (d1 - d2).days)
    except Exception:
        return None


def _get_tw_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Taipei"))
    except Exception:
        return datetime.utcnow() + timedelta(hours=8)


def _to_clean_float(val) -> float | None:
    s = str(val).replace(",", "").replace("+", "").replace("%", "").strip()
    if s in ("", "--", "-", "N/A", "None", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _fmt_html_num(val: int, suffix: str = " 張") -> str:
    if val > 0:
        return f'<span style="color:#ef5350; font-weight:700;">{val:+,}{suffix}</span>'
    elif val < 0:
        return f'<span style="color:#26a69a; font-weight:700;">{val:+,}{suffix}</span>'
    return f'<span style="color:#cbd5e1; font-weight:700;">0{suffix}</span>'


def _fmt_html_pct(val: float) -> str:
    if val > 0:
        return f'<span style="color:#ef5350; font-weight:700;">{val:+.2f}%</span>'
    elif val < 0:
        return f'<span style="color:#26a69a; font-weight:700;">{val:+.2f}%</span>'
    return '<span style="color:#cbd5e1; font-weight:700;">0.00%</span>'


def _fmt_md_num(val: int, suffix: str = " 張") -> str:
    if val > 0:
        return f":red[**{val:+,}{suffix}**]"
    elif val < 0:
        return f":green[**{val:+,}{suffix}**]"
    return f"**0{suffix}**"


def _calc_streak(series: pd.Series) -> int:
    vals = series.tolist()
    if not vals or vals[-1] == 0:
        return 0
    sign = 1 if vals[-1] > 0 else -1
    streak = 0
    for v in reversed(vals):
        if (sign > 0 and v > 0) or (sign < 0 and v < 0):
            streak += 1
        else:
            break
    return streak * sign


def calculate_main_force_cost(
    df_price: pd.DataFrame,
    daily_net: pd.Series,
    window_days: int | None = None,
) -> tuple[pd.Series, float, float]:
    """移動加權平均成本法（純視覺參考線用，含真實 VWAP 與除權息向下校正）"""
    ohlc_cols = [c for c in ["open", "high", "low", "close"] if c in df_price.columns]
    ohlc_avg = df_price[ohlc_cols].mean(axis=1).fillna(df_price["close"]) if ohlc_cols else df_price["close"]

    if "vwap" in df_price.columns and df_price["vwap"].notna().any():
        avg_prices = pd.to_numeric(df_price["vwap"], errors="coerce")
        if "low" in df_price.columns and "high" in df_price.columns:
            lo_s = pd.to_numeric(df_price["low"], errors="coerce")
            hi_s = pd.to_numeric(df_price["high"], errors="coerce")
            invalid_vwap = avg_prices.isna() | (avg_prices < lo_s * 0.99) | (avg_prices > hi_s * 1.01)
            avg_prices = avg_prices.where(~invalid_vwap, ohlc_avg)
        avg_prices = avg_prices.fillna(ohlc_avg)
    else:
        avg_prices = ohlc_avg

    if "adj_factor" in df_price.columns and df_price["adj_factor"].notna().any():
        adj_series = pd.to_numeric(df_price["adj_factor"], errors="coerce").ffill().bfill().fillna(1.0)
    else:
        adj_series = pd.Series(1.0, index=df_price.index)

    start_idx = max(0, len(df_price) - window_days) if (window_days and window_days > 0) else 0

    inv = 0.0
    avg_cost = 0.0
    prev_adj = None
    cost_list = []

    for i, (p, net, cur_adj) in enumerate(zip(avg_prices, daily_net, adj_series)):
        if i < start_idx:
            cost_list.append(None)
            continue

        if prev_adj is not None and prev_adj > 0 and cur_adj > 0 and inv > 0 and avg_cost > 0:
            ratio = float(prev_adj) / float(cur_adj)
            if 0.5 <= ratio <= 0.995:
                avg_cost = avg_cost * ratio
        prev_adj = float(cur_adj) if pd.notna(cur_adj) and cur_adj > 0 else prev_adj

        if pd.isna(p) or pd.isna(net):
            cost_list.append(round(avg_cost, 2) if inv > 0 and avg_cost > 0 else None)
            continue

        if net > 0:
            if inv <= 0:
                inv = float(net)
                avg_cost = float(p)
            else:
                avg_cost = (inv * avg_cost + float(net) * float(p)) / (inv + float(net))
                inv += float(net)
        elif net < 0:
            inv += float(net)
            if inv <= 0:
                inv = 0.0
                avg_cost = 0.0

        cost_list.append(round(avg_cost, 2) if inv > 0 and avg_cost > 0 else None)

    latest_cost = round(avg_cost, 2) if inv > 0 and avg_cost > 0 else 0.0
    return pd.Series(cost_list, index=df_price.index), latest_cost, inv


# =====================================================================
# 股價與基礎資訊抓取（優先官方真實 VWAP ＋ yfinance 除權息因子）
# =====================================================================
def _safe_vwap(amt: float | None, vol: float | None, op: float, hi: float, lo: float, cl: float) -> float:
    fallback = round((op + hi + lo + cl) / 4.0, 2)
    if amt and vol and vol > 0:
        v = round(amt / vol, 2)
        if hi >= lo > 0 and (lo * 0.99 <= v <= hi * 1.01):
            return v
    return fallback


def _fetch_twse_tpex_price(stock_id: str, start_date: str, end_date: str, preferred_suffix: str = None) -> tuple[pd.DataFrame, str]:
    """向證交所 (TWSE) 或櫃買中心 (TPEx 新版優先、舊版備援) 抓取每日 OHLC、成交量(張)與精準 VWAP"""
    session = _create_session()
    dt_start = datetime.strptime(start_date, "%Y-%m-%d")
    dt_end = datetime.strptime(end_date, "%Y-%m-%d")

    months = []
    cur = datetime(dt_start.year, dt_start.month, 1)
    end_m = datetime(dt_end.year, dt_end.month, 1)
    while cur <= end_m:
        months.append((cur.year, cur.month))
        cur = datetime(cur.year + 1, 1, 1) if cur.month == 12 else datetime(cur.year, cur.month + 1, 1)

    def _try_twse() -> pd.DataFrame:
        rows = []
        for y, m in months:
            ymd = f"{y:04d}{m:02d}01"
            url = f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY?date={ymd}&stockNo={stock_id}&response=json"
            try:
                resp = session.get(url, timeout=8, verify=False)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                if data.get("stat") != "OK" or not data.get("data"):
                    continue
                for item in data["data"]:
                    if len(item) < 7:
                        continue
                    ad_date = _parse_dj_date(str(item[0]))
                    if not ad_date or not (start_date <= ad_date <= end_date):
                        continue
                    vol_shares = _to_clean_float(item[1])
                    amt_ntd = _to_clean_float(item[2])
                    op, hi, lo, cl = _to_clean_float(item[3]), _to_clean_float(item[4]), _to_clean_float(item[5]), _to_clean_float(item[6])
                    if cl is not None:
                        op_v = op if op is not None else cl
                        hi_v = hi if hi is not None else cl
                        lo_v = lo if lo is not None else cl
                        rows.append({
                            "date": ad_date,
                            "open": op_v,
                            "high": hi_v,
                            "low": lo_v,
                            "close": cl,
                            "volume": round(vol_shares / 1000.0) if vol_shares is not None else 0.0,
                            "vwap": _safe_vwap(amt_ntd, vol_shares, op_v, hi_v, lo_v, cl),
                        })
            except Exception:
                continue
        if rows:
            return pd.DataFrame(rows).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
        return pd.DataFrame()

    def _try_tpex() -> pd.DataFrame:
        rows = []
        for y, m in months:
            month_items = []
            new_url = f"https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?date={y:04d}/{m:02d}/01&code={stock_id}&id=&response=json"
            try:
                resp = session.get(new_url, timeout=8, verify=False)
                if resp.status_code == 200:
                    payload = resp.json()
                    tables = payload.get("tables") or []
                    if tables and isinstance(tables[0], dict) and tables[0].get("data"):
                        month_items = tables[0]["data"]
                    elif payload.get("aaData"):
                        month_items = payload["aaData"]
            except Exception:
                pass

            if not month_items:
                roc_ym = f"{y - 1911:03d}/{m:02d}"
                old_url = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php?l=zh-tw&d={roc_ym}&stkno={stock_id}"
                try:
                    resp = session.get(old_url, timeout=8, verify=False)
                    if resp.status_code == 200:
                        data = resp.json()
                        month_items = data.get("aaData") or []
                except Exception:
                    pass

            for item in month_items:
                if len(item) < 7:
                    continue
                ad_date = _parse_dj_date(str(item[0]))
                if not ad_date or not (start_date <= ad_date <= end_date):
                    continue
                vol_k = _to_clean_float(item[1])
                amt_k = _to_clean_float(item[2])
                op, hi, lo, cl = _to_clean_float(item[3]), _to_clean_float(item[4]), _to_clean_float(item[5]), _to_clean_float(item[6])
                if cl is not None:
                    op_v = op if op is not None else cl
                    hi_v = hi if hi is not None else cl
                    lo_v = lo if lo is not None else cl
                    rows.append({
                        "date": ad_date,
                        "open": op_v,
                        "high": hi_v,
                        "low": lo_v,
                        "close": cl,
                        "volume": round(vol_k) if vol_k is not None else 0.0,
                        "vwap": _safe_vwap(amt_k, vol_k, op_v, hi_v, lo_v, cl),
                    })

        if rows:
            return pd.DataFrame(rows).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
        return pd.DataFrame()

    order = [".TWO", ".TW"] if preferred_suffix == ".TWO" else [".TW", ".TWO"]
    for suf in order:
        df = _try_twse() if suf == ".TW" else _try_tpex()
        if not df.empty:
            return df, suf

    return pd.DataFrame(), ""


def _fetch_yf_price_and_adj(stock_id: str, start_date: str, end_date: str, preferred_suffix: str = None) -> tuple[pd.DataFrame, str]:
    end_dt = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    if preferred_suffix in (".TW", ".TWO"):
        other = ".TWO" if preferred_suffix == ".TW" else ".TW"
        suffix_candidates = [preferred_suffix, other]
    else:
        suffix_candidates = [".TW", ".TWO"]

    for suffix in suffix_candidates:
        ticker = f"{stock_id}{suffix}"
        try:
            df = yf.download(ticker, start=start_date, end=end_dt, progress=False, auto_adjust=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.reset_index()
                df["date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
                adj_col = "Adj Close" if "Adj Close" in df.columns else "Close"
                out = pd.DataFrame({
                    "date": df["date"],
                    "open": pd.to_numeric(df["Open"], errors="coerce"),
                    "high": pd.to_numeric(df["High"], errors="coerce"),
                    "low": pd.to_numeric(df["Low"], errors="coerce"),
                    "close": pd.to_numeric(df["Close"], errors="coerce"),
                    "volume": (pd.to_numeric(df["Volume"], errors="coerce") / 1000.0).round(0),
                })
                adj_close = pd.to_numeric(df[adj_col], errors="coerce")
                out["adj_factor"] = (adj_close / out["close"].replace(0, np.nan)).round(6).fillna(1.0)
                out["vwap"] = ((out["open"] + out["high"] + out["low"] + out["close"]) / 4.0).round(2)
                out = out[out["date"] >= start_date].dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
                if not out.empty:
                    return out, suffix
        except Exception:
            continue
    return pd.DataFrame(), ""


def fetch_price_history(stock_id: str, start_date: str, end_date: str, preferred_suffix: str = None) -> tuple[pd.DataFrame, str]:
    df_official, suf_off = _fetch_twse_tpex_price(stock_id, start_date, end_date, preferred_suffix)
    df_yf, suf_yf = _fetch_yf_price_and_adj(stock_id, start_date, end_date, suf_off or preferred_suffix)

    if not df_official.empty:
        if not df_yf.empty and "adj_factor" in df_yf.columns:
            df_merged = df_official.merge(df_yf[["date", "adj_factor"]], on="date", how="left")
            df_merged["adj_factor"] = df_merged["adj_factor"].ffill().bfill().fillna(1.0)
        else:
            df_merged = df_official.copy()
            df_merged["adj_factor"] = 1.0
        return df_merged, suf_off

    if not df_yf.empty:
        return df_yf, suf_yf

    return pd.DataFrame(), ""


def fetch_stock_name(session: requests.Session = None, stock_id: str = "", mirror: str = None) -> str:
    stock_id = stock_id.strip()
    if not stock_id:
        return ""

    sess = session or _create_session()
    try:
        html, _, _ = _fetch_dj_html(sess, f"/z/zc/zco/zco.djhtm?a={stock_id}", mirror, timeout=5)
        if html:
            m_js = re.search(rf"GenLink2stk\([^,]*{re.escape(stock_id)}[^,]*,\s*['\"]([^'\"]+)['\"]\)", html)
            if m_js:
                return m_js.group(1).strip()
            m_paren = re.search(rf"([\u4e00-\u9fffA-Za-z0-9\-]{{2,10}})\s*[\(（]{re.escape(stock_id)}[\)）]", html)
            if m_paren:
                return m_paren.group(1).strip()
    except Exception:
        pass

    try:
        mis_url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{stock_id}.tw|otc_{stock_id}.two&json=1&delay=0"
        resp = sess.get(mis_url, timeout=3, verify=False)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("msgArray", []):
                name = str(item.get("n", "")).strip()
                if name:
                    return name
    except Exception:
        pass

    return ""


def fetch_issued_lots(stock_id: str, preferred_suffix: str = None) -> float:
    suffixes = [preferred_suffix, ".TWO" if preferred_suffix == ".TW" else ".TW"] if preferred_suffix in (".TW", ".TWO") else [".TW", ".TWO"]
    for suf in suffixes:
        try:
            info = yf.Ticker(f"{stock_id}{suf}").fast_info
            shares = getattr(info, "shares", None)
            if shares and float(shares) > 0:
                return round(float(shares) / 1000.0, 1)
        except Exception:
            continue
    return 0.0


# =====================================================================
# 投信（嚴格區分「第 1 個投信=每日買賣超」與「第 2 個投信=總持股」）
# =====================================================================
def fetch_investment_trust_history(session: requests.Session, stock_id: str, start_date: str, end_date: str, mirror: str = None):
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)
    html, ok_mirror, err = _fetch_dj_html(session, f"/z/zc/zcl/zcl.djhtm?a={stock_id}&c={s_fmt}&d={e_fmt}", mirror)
    if not html:
        return pd.DataFrame(columns=["date", "投信", "trust_holding", "trust_holding_pct"]), mirror, err

    soup = BeautifulSoup(html, "html.parser")
    idx_trust_net, idx_trust_hold, idx_trust_pct = 2, 6, None

    for tr in soup.find_all("tr"):
        cells = [c.get_text(strip=True).replace(" ", "") for c in tr.find_all(["th", "td"], recursive=False)]
        if not (4 <= len(cells) <= 20):
            continue
        trust_indices = [i for i, txt in enumerate(cells) if "投信" in txt]
        if not trust_indices:
            continue
        if "日期" in cells[0]:
            offset = 0
        elif "外資" in cells[0]:
            offset = 1
        else:
            continue

        net_cand = hold_cand = pct_cand = None
        plain_trust = []
        for i in trust_indices:
            txt = cells[i]
            col_idx = i + offset
            if "比" in txt or "%" in txt:
                if pct_cand is None:
                    pct_cand = col_idx
            elif "持股" in txt or "庫存" in txt:
                if hold_cand is None:
                    hold_cand = col_idx
            elif "買賣" in txt:
                if net_cand is None:
                    net_cand = col_idx
            else:
                plain_trust.append(col_idx)

        if net_cand is None and plain_trust:
            net_cand = plain_trust[0]
        if hold_cand is None:
            remaining = [c for c in plain_trust if c != net_cand]
            if remaining:
                hold_cand = remaining[0]

        if net_cand is not None:
            idx_trust_net = net_cand
        if hold_cand is not None:
            idx_trust_hold = hold_cand
        idx_trust_pct = pct_cand
        break

    if idx_trust_hold == idx_trust_net:
        idx_trust_net, idx_trust_hold = 2, 6

    def _extract_rows(i_net: int, i_hold: int | None, i_pct: int | None) -> list[dict]:
        recs = []
        for tr in soup.find_all("tr"):
            tds = [td.get_text(strip=True) for td in tr.find_all("td", recursive=False)]
            if len(tds) <= i_net:
                continue
            ad_date = _parse_dj_date(tds[0])
            if not (ad_date and start_date <= ad_date <= end_date):
                continue
            net_val = _to_clean_float(tds[i_net])
            if net_val is None:
                continue

            hold_val = _to_clean_float(tds[i_hold]) if (i_hold is not None and i_hold < len(tds)) else np.nan
            pct_val = _to_clean_float(tds[i_pct]) if (i_pct is not None and i_pct < len(tds)) else np.nan

            if hold_val is not None and not pd.isna(hold_val) and hold_val < 0:
                hold_val = np.nan
            if pct_val is not None and not pd.isna(pct_val) and not (0.0 <= pct_val <= 100.0):
                pct_val = np.nan

            recs.append({
                "date": ad_date,
                "投信": net_val,
                "trust_holding": hold_val if hold_val is not None else np.nan,
                "trust_holding_pct": pct_val if pct_val is not None else np.nan,
            })
        return recs

    records = _extract_rows(idx_trust_net, idx_trust_hold, idx_trust_pct)
    if not records:
        return pd.DataFrame(columns=["date", "投信", "trust_holding", "trust_holding_pct"]), ok_mirror, "投信表格無資料"

    df = pd.DataFrame(records).drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)

    if (
        len(df) >= 3
        and df["trust_holding"].notna().any()
        and df["投信"].abs().sum() > 0
        and (df["投信"].fillna(0) == df["trust_holding"].fillna(0)).all()
    ):
        fallback_recs = _extract_rows(2, 6, None)
        if fallback_recs:
            df = pd.DataFrame(fallback_recs).drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)

    return df, ok_mirror, ""


def _extract_brokers_from_html(html: str) -> list:
    soup = BeautifulSoup(html, "html.parser")
    brokers = {}
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if "zco0" in href.lower():
            parsed = urlparse(href)
            qs = {k.upper(): v[0] for k, v in parse_qs(parsed.query).items()}
            bhid = qs.get("BHID", "")
            b_hex = qs.get("B", "")
            name = a_tag.get_text(strip=True)
            key = f"{bhid}_{b_hex}"
            if name and (bhid or b_hex) and key not in brokers:
                brokers[key] = {"bhid": bhid, "b": b_hex, "name": name}
    return list(brokers.values())


def _parse_zco_totals(html: str) -> tuple[float, float]:
    soup = BeautifulSoup(html, "html.parser")
    buy_total = None
    sell_total = None

    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True).replace(",", "").replace("+", "") for td in tr.find_all("td", recursive=False)]
        for i, text in enumerate(tds):
            if "合計買超" in text and i + 1 < len(tds):
                v = _to_clean_float(tds[i + 1])
                if v is not None:
                    buy_total = abs(v)
            elif "合計賣超" in text and i + 1 < len(tds):
                v = _to_clean_float(tds[i + 1])
                if v is not None:
                    sell_total = abs(v)

    if buy_total is not None and sell_total is not None:
        return buy_total, sell_total

    b_sum = 0.0
    s_sum = 0.0
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) >= 10:
            left_a = tds[0].find("a", href=True)
            right_a = tds[5].find("a", href=True)
            if left_a and "zco0" in left_a["href"].lower():
                v = _to_clean_float(tds[3].get_text(strip=True))
                if v is not None:
                    b_sum += abs(v)
            if right_a and "zco0" in right_a["href"].lower():
                v = _to_clean_float(tds[8].get_text(strip=True))
                if v is not None:
                    s_sum += abs(v)
    return b_sum, s_sum


def fetch_period_concentration(stock_id: str, start_date: str, end_date: str, period_vol: float, mirror: str = None) -> dict:
    session = _create_session()
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)
    html, _, _ = _fetch_dj_html(session, f"/z/zc/zco/zco.djhtm?a={stock_id}&e={s_fmt}&f={e_fmt}", mirror, timeout=6)
    if not html or period_vol <= 0:
        return {"pct": None, "net": 0, "buy15": 0, "sell15": 0}

    buy15, sell15 = _parse_zco_totals(html)
    net_15 = buy15 - sell15
    pct = round((net_15 / period_vol) * 100, 2)
    return {
        "pct": pct,
        "net": int(round(net_15)),
        "buy15": int(round(buy15)),
        "sell15": int(round(sell15)),
    }


def fetch_rolling_concentrations(stock_id: str, df_price: pd.DataFrame, mirror: str = None) -> dict:
    if df_price.empty:
        return {}

    end_date = df_price["date"].max()
    windows = {"d5": 5, "d10": 10, "d20": 20}
    tasks = {}
    for key, n_days in windows.items():
        sub = df_price.tail(n_days)
        s_date = sub["date"].min()
        vol = float(sub["volume"].sum()) if "volume" in sub.columns else 0.0
        tasks[key] = (s_date, end_date, vol)

    results = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {
            executor.submit(fetch_period_concentration, stock_id, s_dt, e_dt, vol, mirror): key
            for key, (s_dt, e_dt, vol) in tasks.items()
        }
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                results[key] = future.result()
            except Exception:
                results[key] = {"pct": None, "net": 0, "buy15": 0, "sell15": 0}

    return results


def fetch_top_brokers(session: requests.Session, stock_id: str, start_date: str, end_date: str, mirror: str = None, limit: int = 30):
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)
    candidate_paths = [
        f"/z/zc/zco/zco.djhtm?a={stock_id}&e={s_fmt}&f={e_fmt}",
        f"/z/zc/zco/zco.djhtm?a={stock_id}&d=240",
        f"/z/zc/zco/zco.djhtm?a={stock_id}",
    ]

    last_err = ""
    for path in candidate_paths:
        html, ok_mirror, err = _fetch_dj_html(session, path, mirror)
        if not html:
            last_err = err
            continue
        broker_list = _extract_brokers_from_html(html)
        if broker_list:
            return broker_list[:limit], ok_mirror, ""
        last_err = f"頁面 {path} 無法解析分點連結 (HTML長度:{len(html)})"

    return [], mirror, last_err


def fetch_broker_daily_history(stock_id: str, broker_info: dict, start_date: str, end_date: str, mirror: str) -> pd.DataFrame:
    session = _create_session()
    bhid = broker_info["bhid"]
    b_hex = broker_info["b"]
    broker_name = broker_info["name"]
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)

    candidate_paths = []
    if bhid and b_hex:
        candidate_paths.append(f"/z/zc/zco/zco0/zco0.djhtm?A={stock_id}&BHID={bhid}&b={b_hex}&C=1&D={s_fmt}&E={e_fmt}")
        candidate_paths.append(f"/z/zc/zco/zco0/zco0.djhtm?a={stock_id}&b={b_hex}&BHID={bhid}")
    if b_hex:
        candidate_paths.append(f"/z/zc/zco/zco0/zco0.djhtm?a={stock_id}&b={b_hex}")
    if bhid and not b_hex:
        candidate_paths.append(f"/z/zc/zco/zco0/zco0.djhtm?a={stock_id}&BHID={bhid}&b={bhid}")

    for path in candidate_paths:
        html, _, _ = _fetch_dj_html(session, path, mirror, timeout=6)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        records = []
        for tr in soup.find_all("tr"):
            tds = [td.get_text(strip=True) for td in tr.find_all("td", recursive=False)]
            if len(tds) >= 5:
                ad_date = _parse_dj_date(tds[0])
                if ad_date and start_date <= ad_date <= end_date:
                    val = _to_clean_float(tds[-1])
                    if val is not None:
                        records.append({"date": ad_date, broker_name: val})

        if records:
            return pd.DataFrame(records).groupby("date", as_index=False)[broker_name].sum()

    return pd.DataFrame(columns=["date", broker_name])


# =====================================================================
# 輔助籌碼抓取：融資融券、借券賣出(官方直抓+FinMind)、TDCC 集保大戶
# =====================================================================
def _fetch_margin_moneydj_fallback(
    session: requests.Session,
    stock_id: str,
    start_date: str,
    end_date: str,
    mirror: str = None,
) -> pd.DataFrame:
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)
    html, _, _ = _fetch_dj_html(session, f"/z/zc/zcn/zcn.djhtm?a={stock_id}&c={s_fmt}&d={e_fmt}", mirror, timeout=6)
    if not html:
        html, _, _ = _fetch_dj_html(session, f"/z/zc/zcn/zcn.djhtm?a={stock_id}", mirror, timeout=6)
    if not html:
        return pd.DataFrame(columns=["date", "margin_bal", "short_bal"])

    soup = BeautifulSoup(html, "html.parser")
    idx_margin, idx_short = 4, 11
    for tr in soup.find_all("tr"):
        cells = [c.get_text(strip=True).replace(" ", "") for c in tr.find_all(["th", "td"], recursive=False)]
        if 10 <= len(cells) <= 18 and any("餘額" in c for c in cells):
            offset = 0 if ("日期" in cells[0]) else 1
            bal_indices = [i + offset for i, txt in enumerate(cells) if "餘額" in txt and "增減" not in txt]
            if len(bal_indices) >= 2:
                idx_margin, idx_short = bal_indices[0], bal_indices[1]
            break

    records = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td", recursive=False)]
        if len(tds) <= max(idx_margin, idx_short):
            continue
        ad_date = _parse_dj_date(tds[0])
        if ad_date and start_date <= ad_date <= end_date:
            m_bal = _to_clean_float(tds[idx_margin])
            s_bal = _to_clean_float(tds[idx_short])
            if m_bal is not None and s_bal is not None and m_bal >= 0 and s_bal >= 0:
                records.append({"date": ad_date, "margin_bal": m_bal, "short_bal": s_bal})

    if not records:
        return pd.DataFrame(columns=["date", "margin_bal", "short_bal"])
    return pd.DataFrame(records).drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)


def fetch_margin_history(session: requests.Session, stock_id: str, start_date: str, end_date: str, mirror: str = None) -> pd.DataFrame:
    df_fm, _ = _fetch_finmind_dataset(session, "TaiwanStockMarginPurchaseShortSale", stock_id, start_date, end_date)
    if not df_fm.empty and "date" in df_fm.columns:
        m_col = "MarginPurchaseTodayBalance" if "MarginPurchaseTodayBalance" in df_fm.columns else None
        s_col = "ShortSaleTodayBalance" if "ShortSaleTodayBalance" in df_fm.columns else None
        if m_col and s_col:
            out = pd.DataFrame({
                "date": df_fm["date"].astype(str).str.slice(0, 10),
                "margin_bal": pd.to_numeric(df_fm[m_col], errors="coerce"),
                "short_bal": pd.to_numeric(df_fm[s_col], errors="coerce"),
            }).dropna(subset=["date", "margin_bal", "short_bal"])
            out = out[(out["date"] >= start_date) & (out["date"] <= end_date) & (out["margin_bal"] >= 0) & (out["short_bal"] >= 0)]
            if not out.empty:
                return out.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)

    return _fetch_margin_moneydj_fallback(session, stock_id, start_date, end_date, mirror)


def _locate_sbl_col_index(fields: list[str], default_idx: int = 12) -> int:
    clean_f = [str(f).replace(" ", "") for f in fields]
    for i, txt in enumerate(clean_f):
        if "借券" in txt and ("當日餘額" in txt or "本日餘額" in txt):
            return i
    bal_idxs = [i for i, txt in enumerate(clean_f) if ("當日餘額" in txt or "本日餘額" in txt) and "前日" not in txt]
    if len(bal_idxs) >= 2:
        return bal_idxs[-1]
    return default_idx


def _fetch_official_sbl_day_map(session: requests.Session, ad_date: str, market: str) -> dict[str, float]:
    cache_key = (market, ad_date)
    if cache_key in _SBL_OFFICIAL_CACHE and _SBL_OFFICIAL_CACHE[cache_key]:
        return _SBL_OFFICIAL_CACHE[cache_key]

    day_map: dict[str, float] = {}

    if market == ".TW":
        ymd = ad_date.replace("-", "")
        urls = [
            f"https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U?date={ymd}&response=json",
            f"https://www.twse.com.tw/exchangeReport/TWT93U?response=json&date={ymd}",
        ]
        for url in urls:
            try:
                resp = session.get(url, timeout=10, verify=False)
                if resp.status_code != 200:
                    continue
                payload = resp.json()
                if payload.get("stat") != "OK" or not payload.get("data"):
                    continue
                fields = payload.get("fields") or []
                idx_sbl = _locate_sbl_col_index(fields, default_idx=12)
                for row in payload["data"]:
                    if len(row) <= idx_sbl:
                        continue
                    code = str(row[0]).strip()
                    val_shares = _to_clean_float(row[idx_sbl])
                    if code and val_shares is not None and val_shares >= 0:
                        day_map[code] = round(val_shares / 1000.0)
                if day_map:
                    break
            except Exception:
                continue

    elif market == ".TWO":
        y_slash = ad_date.replace("-", "/")
        try:
            json_url = f"https://www.tpex.org.tw/www/zh-tw/margin/sbl?date={y_slash}&id=&response=json"
            resp = session.get(json_url, timeout=10, verify=False)
            if resp.status_code == 200:
                payload = resp.json()
                tables = payload.get("tables") or []
                rows, fields = [], []
                if tables and isinstance(tables[0], dict):
                    fields = tables[0].get("fields") or []
                    rows = tables[0].get("data") or []
                elif payload.get("aaData"):
                    rows = payload.get("aaData") or []
                if rows:
                    idx_sbl = _locate_sbl_col_index(fields, default_idx=12)
                    for row in rows:
                        if len(row) <= idx_sbl:
                            continue
                        code = str(row[0]).strip()
                        val_shares = _to_clean_float(row[idx_sbl])
                        if code and val_shares is not None and val_shares >= 0:
                            day_map[code] = round(val_shares / 1000.0)
        except Exception:
            pass

        if not day_map:
            try:
                csv_url = f"https://www.tpex.org.tw/www/zh-tw/margin/sbl?date={y_slash}&id=&response=csv"
                resp = session.get(csv_url, timeout=10, verify=False)
                if resp.status_code == 200 and len(resp.content) > 200:
                    try:
                        text = resp.content.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        text = resp.content.decode("cp950", errors="ignore")
                    idx_sbl = 12
                    for row in csv.reader(io.StringIO(text)):
                        if len(row) < 13:
                            continue
                        first_cell = str(row[0]).strip()
                        if "代號" in first_cell:
                            idx_sbl = _locate_sbl_col_index(row, default_idx=12)
                            continue
                        if re.match(r"^[0-9A-Za-z]{4,6}$", first_cell):
                            val_shares = _to_clean_float(row[idx_sbl])
                            if val_shares is not None and val_shares >= 0:
                                day_map[first_cell] = round(val_shares / 1000.0)
            except Exception:
                pass

    if day_map:
        _SBL_OFFICIAL_CACHE[cache_key] = day_map
    return day_map


def fetch_sbl_history(
    session: requests.Session,
    stock_id: str,
    start_date: str,
    end_date: str,
    mirror: str = None,
    preferred_suffix: str = None,
    recent_trade_dates: list[str] | None = None,
) -> pd.DataFrame:
    _ = mirror
    df_out = pd.DataFrame(columns=["date", "sbl_bal"])

    df_fm, _ = _fetch_finmind_dataset(session, "TaiwanDailyShortSaleBalances", stock_id, start_date, end_date)
    if not df_fm.empty and "date" in df_fm.columns and "SBLShortSalesCurrentDayBalance" in df_fm.columns:
        sbl_shares = pd.to_numeric(df_fm["SBLShortSalesCurrentDayBalance"], errors="coerce")
        tmp = pd.DataFrame({
            "date": df_fm["date"].astype(str).str.slice(0, 10),
            "sbl_bal": (sbl_shares / 1000.0).round(0),
        }).dropna(subset=["date", "sbl_bal"])
        tmp = tmp[(tmp["date"] >= start_date) & (tmp["date"] <= end_date) & (tmp["sbl_bal"] >= 0)]
        if not tmp.empty:
            df_out = tmp.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)

    existing_dates = set(df_out["date"].tolist()) if not df_out.empty else set()
    check_dates = [d for d in (recent_trade_dates or [end_date]) if d and start_date <= d <= end_date][-3:]
    dates_to_fetch = [d for d in check_dates if (d not in existing_dates) or (d == check_dates[-1])]

    if dates_to_fetch:
        markets = [preferred_suffix] if preferred_suffix in (".TW", ".TWO") else [".TW", ".TWO"]
        official_rows = []
        for ad_date in dates_to_fetch:
            for mkt in markets:
                day_map = _fetch_official_sbl_day_map(session, ad_date, mkt)
                if stock_id in day_map:
                    official_rows.append({"date": ad_date, "sbl_bal": day_map[stock_id]})
                    break
        if official_rows:
            df_off = pd.DataFrame(official_rows)
            if df_out.empty:
                df_out = df_off.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
            else:
                df_out = (
                    pd.concat([df_out, df_off], ignore_index=True)
                    .drop_duplicates(subset=["date"], keep="last")
                    .sort_values("date")
                    .reset_index(drop=True)
                )

    return df_out


def _get_tdcc_market_map(session: requests.Session) -> dict[str, dict]:
    """
    下載全市場 TDCC 集保 CSV 並直接解析為 {stock_id: entry} 字典快取（TTL 1 小時）：
    - 雙官方網址備援（opendata / smart）＋ timeout 拉長至 25 秒
    - 使用 csv.reader + strip() 徹底解決證券代號尾端空白 (e.g. '2454  ') 造成比對失敗的 BUG
    - 嚴格驗證解析出超過 100 檔有效股票才寫入快取，防止 HTML 錯誤頁汙染快取
    """
    now = time.time()
    if _TDCC_CACHE.get("map") and (now - _TDCC_CACHE.get("ts", 0.0)) < _TDCC_CACHE_TTL:
        return _TDCC_CACHE["map"]

    urls = [
        "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5",
        "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5",
    ]
    for url in urls:
        try:
            resp = session.get(url, timeout=25, verify=False)
            if resp.status_code != 200 or len(resp.content) < 10000:
                continue
            text = resp.content.decode("utf-8-sig", errors="ignore")
            head_lower = text[:400].lower()
            if "<html" in head_lower or "<!doctype" in head_lower:
                continue

            acc: dict[str, dict] = {}
            for row in csv.reader(io.StringIO(text)):
                if len(row) < 6:
                    continue
                raw_dt = row[0].strip().replace("-", "").replace("/", "")
                if len(raw_dt) != 8 or not raw_dt.isdigit():
                    continue
                sid = row[1].strip()
                if not sid:
                    continue
                try:
                    level = int(row[2].strip())
                    holders = int(round(float(row[3].strip().replace(",", ""))))
                    pct = float(row[5].strip().replace(",", "").replace("%", ""))
                except ValueError:
                    continue

                rec = acc.get(sid)
                if rec is None:
                    rec = {
                        "raw_dt": raw_dt,
                        "w1000": 0.0,
                        "w400": 0.0,
                        "total_holders": 0,
                        "sum_holders": 0,
                    }
                    acc[sid] = rec

                if level == 17:
                    rec["total_holders"] = holders
                elif 1 <= level <= 15:
                    rec["sum_holders"] += max(0, holders)
                    if level >= 12:
                        rec["w400"] += pct
                    if level == 15:
                        rec["w1000"] += pct

            market_map: dict[str, dict] = {}
            for sid, rec in acc.items():
                holders_final = rec["total_holders"] if rec["total_holders"] > 0 else rec["sum_holders"]
                if rec["w1000"] > 0 or rec["w400"] > 0 or holders_final > 0:
                    rd = rec["raw_dt"]
                    market_map[sid] = {
                        "date": f"{rd[:4]}-{rd[4:6]}-{rd[6:8]}",
                        "whale_1000_pct": round(rec["w1000"], 2),
                        "whale_400_pct": round(rec["w400"], 2),
                        "total_holders": int(holders_final),
                    }

            if len(market_map) >= 100:
                _TDCC_CACHE["ts"] = now
                _TDCC_CACHE["map"] = market_map
                return market_map
        except Exception:
            continue

    return _TDCC_CACHE.get("map") or {}


def _fetch_tdcc_from_qry_stock(session: requests.Session, stock_id: str, max_weeks: int = 2) -> list[dict]:
    """
    備援／首週補歷史：透過集保官方單股查詢頁 (qryStock) 直接抓最新 1～2 週股權分散表，
    避免全市場大檔超時，並讓新加入的個股當下即可算出週變化。
    """
    qry_url = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"

    def _parse_form(soup: BeautifulSoup):
        tok_el = soup.find("input", {"name": "SYNCHRONIZER_TOKEN"})
        uri_el = soup.find("input", {"name": "SYNCHRONIZER_URI"})
        fir_el = soup.find("input", {"name": "firDate"})
        sel_el = soup.find("select", {"name": "scaDate"})
        dates = []
        if sel_el:
            for opt in sel_el.find_all("option"):
                v = str(opt.get("value", "")).strip()
                if re.match(r"^\d{8}$", v):
                    dates.append(v)
        token = str(tok_el.get("value", "")).strip() if tok_el else ""
        uri = str(uri_el.get("value", "/portal/zh/smWeb/qryStock")).strip() if uri_el else "/portal/zh/smWeb/qryStock"
        fir_date = (str(fir_el.get("value", "")).strip() if fir_el else "") or (dates[0] if dates else "")
        return token, uri, fir_date, dates

    results = []
    try:
        resp_get = session.get(qry_url, timeout=15, verify=False)
        if resp_get.status_code != 200:
            return []
        token, uri, fir_date, avail_dates = _parse_form(BeautifulSoup(resp_get.text, "html.parser"))
        if not token or not avail_dates:
            return []

        for target_dt in avail_dates[:max_weeks]:
            if not token:
                break
            payload = {
                "SYNCHRONIZER_TOKEN": token,
                "SYNCHRONIZER_URI": uri,
                "method": "submit",
                "firDate": fir_date,
                "scaDate": target_dt,
                "sqlMethod": "StockNo",
                "stockNo": stock_id,
                "stockName": "",
            }
            resp_post = session.post(qry_url, data=payload, headers={"Referer": qry_url}, timeout=15, verify=False)
            if resp_post.status_code != 200:
                break
            post_soup = BeautifulSoup(resp_post.text, "html.parser")
            next_tok, next_uri, next_fir, _ = _parse_form(post_soup)
            token = next_tok
            if next_uri:
                uri = next_uri
            if next_fir:
                fir_date = next_fir

            w1000_pct, w400_pct = 0.0, 0.0
            total_holders, sum_holders = 0, 0
            for tr in post_soup.find_all("tr"):
                tds = [
                    td.get_text(strip=True).replace("\u3000", "").replace(" ", "")
                    for td in tr.find_all("td", recursive=False)
                ]
                if not tds:
                    continue
                if any("合計" in c for c in tds[:2]):
                    for idx_c, cell_txt in enumerate(tds):
                        if "合計" in cell_txt and idx_c + 1 < len(tds):
                            h_val = _to_clean_float(tds[idx_c + 1])
                            if h_val is not None and h_val > 0:
                                total_holders = int(round(h_val))
                            break
                    continue
                if len(tds) >= 5 and tds[0].isdigit():
                    lv = int(tds[0])
                    h_val = _to_clean_float(tds[2])
                    p_val = _to_clean_float(tds[4])
                    if 1 <= lv <= 15:
                        if h_val is not None and h_val >= 0:
                            sum_holders += int(round(h_val))
                        if p_val is not None:
                            if lv >= 12:
                                w400_pct += p_val
                            if lv == 15:
                                w1000_pct += p_val

            holders_final = total_holders if total_holders > 0 else sum_holders
            if w1000_pct > 0 or w400_pct > 0 or holders_final > 0:
                results.append({
                    "date": f"{target_dt[:4]}-{target_dt[4:6]}-{target_dt[6:8]}",
                    "whale_1000_pct": round(w1000_pct, 2),
                    "whale_400_pct": round(w400_pct, 2),
                    "total_holders": int(holders_final),
                })
    except Exception:
        pass
    return results


def fetch_tdcc_snapshot(session: requests.Session, stock_id: str, existing_history: list = None) -> list:
    """抓取集保大戶股權分散表並合併進歷史週序列（全市場 CSV 為主，失敗或不足 2 週自動走官方 qryStock 補齊）"""
    stock_id = str(stock_id).strip()
    by_date: dict[str, dict] = {}
    if isinstance(existing_history, list):
        for h in existing_history:
            if isinstance(h, dict) and h.get("date"):
                by_date[str(h["date"])] = h

    market_map = _get_tdcc_market_map(session)
    found_in_csv = False
    if stock_id in market_map:
        entry = market_map[stock_id]
        by_date[entry["date"]] = entry
        found_in_csv = True

    if (not found_in_csv) or (len(by_date) < 2):
        for entry in _fetch_tdcc_from_qry_stock(session, stock_id, max_weeks=2):
            by_date[entry["date"]] = entry

    history = sorted(by_date.values(), key=lambda x: x.get("date", ""))
    return history[-24:]


def _populate_aux_concentrations(
    stock_id: str,
    df_price: pd.DataFrame,
    df_aux: pd.DataFrame,
    mirror: str = None,
    only_recent_days: int | None = None,
    max_workers: int = 8,
    valid_broker_dates: set[str] | None = None,
) -> pd.DataFrame:
    """
    補齊 df_aux 中的每日官方前 15 大籌碼集中度 (conc_5d, conc_10d, conc_20d)：
    - 若該交易日無有效分點明細（例如盤中尚未公布），略過並清除集中度避免寫入被盤中量稀釋的假數值
    """
    if df_price.empty:
        return df_aux

    df_out = df_price[["date"]].merge(df_aux, on="date", how="left") if not df_aux.empty else pd.DataFrame({"date": df_price["date"]})
    for col in ("conc_5d", "conc_10d", "conc_20d"):
        if col not in df_out.columns:
            df_out[col] = np.nan

    if valid_broker_dates is not None and len(df_out) > 0:
        for idx in range(max(0, len(df_out) - 3), len(df_out)):
            if str(df_out.loc[idx, "date"]) not in valid_broker_dates:
                df_out.loc[idx, ["conc_5d", "conc_10d", "conc_20d"]] = np.nan

    start_idx = max(0, len(df_price) - only_recent_days) if (only_recent_days and only_recent_days > 0) else 0
    windows = [("conc_5d", 5), ("conc_10d", 10), ("conc_20d", 20)]
    tasks = []

    for idx in range(start_idx, len(df_price)):
        end_date = str(df_price.loc[idx, "date"])
        if valid_broker_dates is not None and end_date not in valid_broker_dates:
            continue
        for col_name, n_days in windows:
            if idx + 1 < n_days:
                continue
            if pd.notna(df_out.loc[idx, col_name]):
                continue
            sub = df_price.iloc[idx - n_days + 1 : idx + 1]
            start_date = str(sub.iloc[0]["date"])
            vol_sum = float(sub["volume"].fillna(0.0).sum()) if "volume" in sub.columns else 0.0
            if vol_sum > 0:
                tasks.append((idx, col_name, start_date, end_date, vol_sum))

    if not tasks:
        return df_out

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(fetch_period_concentration, stock_id, s_dt, e_dt, vol, mirror): (idx, col)
            for (idx, col, s_dt, e_dt, vol) in tasks
        }
        for future in as_completed(future_map):
            idx, col = future_map[future]
            try:
                res = future.result()
                if res and res.get("pct") is not None:
                    df_out.loc[idx, col] = res["pct"]
            except Exception:
                continue

    return df_out


# =====================================================================
# 核心分析引擎（日頻齊全檢核 ＋ 趨勢 60% ＋ 動能 40% 單日診斷與純 NumPy 回算）
# =====================================================================
def check_chip_completeness(
    df_price_full: pd.DataFrame,
    df_chips_full: pd.DataFrame,
    df_aux_full: pd.DataFrame = None,
    anchor_date: str | None = None,
) -> dict:
    """
    檢核指定基準日 (anchor_date) 的日頻籌碼是否齊全：
    - 必備計分項：主力分點明細、5日集中度（交易日滿 5 天時）、融資融券、借券賣出
    - 獨立追蹤項：投信 (has_trust，用於防止盤中補 0 截斷連買賣天數，不重複計入總分)
    """
    if df_price_full is None or df_price_full.empty:
        return {
            "is_complete": False,
            "missing_items": ["股價資料"],
            "has_broker": False,
            "has_conc5": False,
            "has_margin": False,
            "has_sbl": False,
            "has_trust": False,
            "anchor_date": "",
        }

    df_p = df_price_full.sort_values("date").reset_index(drop=True)
    latest_all_dt = str(df_p["date"].iloc[-1])
    target_dt = str(anchor_date) if anchor_date else latest_all_dt
    df_p_sub = df_p[df_p["date"].astype(str) <= target_dt].reset_index(drop=True)
    if df_p_sub.empty:
        return {
            "is_complete": False,
            "missing_items": ["股價資料"],
            "has_broker": False,
            "has_conc5": False,
            "has_margin": False,
            "has_sbl": False,
            "has_trust": False,
            "anchor_date": target_dt,
        }

    actual_dt = str(df_p_sub["date"].iloc[-1])
    today_vol = float(pd.to_numeric(df_p_sub["volume"].iloc[-1], errors="coerce") or 0.0) if "volume" in df_p_sub.columns else 1.0

    has_broker = False
    trust_net_today = 0.0
    if df_chips_full is not None and not df_chips_full.empty:
        row_c = df_chips_full[df_chips_full["date"].astype(str) == actual_dt]
        pure_cols = [c for c in df_chips_full.columns if c not in ("date", "投信")]
        if pure_cols:
            if today_vol <= 0:
                has_broker = True
            elif not row_c.empty:
                abs_sum = float(row_c[pure_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).abs().sum().sum())
                has_broker = abs_sum > 0
        if "投信" in df_chips_full.columns and not row_c.empty:
            trust_net_today = float(pd.to_numeric(row_c["投信"].iloc[-1], errors="coerce") or 0.0)

    need_conc5 = len(df_p_sub) >= 5
    has_conc5 = not need_conc5
    has_margin = True
    has_sbl = True
    has_trust = False

    if df_aux_full is not None and not df_aux_full.empty:
        df_a_dedup = df_aux_full.drop_duplicates(subset=["date"], keep="last").copy()
        df_a_dedup["date"] = df_a_dedup["date"].astype(str)
        row_a = df_a_dedup[df_a_dedup["date"] == actual_dt]

        if need_conc5:
            if "conc_5d" in df_a_dedup.columns and not row_a.empty:
                has_conc5 = pd.notna(pd.to_numeric(row_a["conc_5d"].iloc[-1], errors="coerce"))
            else:
                has_conc5 = False

        if "margin_bal" in df_a_dedup.columns:
            s_m_all = pd.to_numeric(df_a_dedup["margin_bal"], errors="coerce")
            if s_m_all.notna().any():
                has_margin = (not row_a.empty) and pd.notna(pd.to_numeric(row_a["margin_bal"].iloc[-1], errors="coerce"))

        if "sbl_bal" in df_a_dedup.columns:
            s_sbl_all = pd.to_numeric(df_a_dedup["sbl_bal"], errors="coerce")
            if s_sbl_all.notna().any():
                has_sbl = (not row_a.empty) and pd.notna(pd.to_numeric(row_a["sbl_bal"].iloc[-1], errors="coerce"))

        if not row_a.empty:
            if "trust_published" in df_a_dedup.columns and pd.notna(pd.to_numeric(row_a["trust_published"].iloc[-1], errors="coerce")):
                has_trust = float(pd.to_numeric(row_a["trust_published"].iloc[-1], errors="coerce")) > 0
            elif "trust_holding" in df_a_dedup.columns and pd.notna(pd.to_numeric(row_a["trust_holding"].iloc[-1], errors="coerce")):
                has_trust = True
            elif "trust_holding_pct" in df_a_dedup.columns and pd.notna(pd.to_numeric(row_a["trust_holding_pct"].iloc[-1], errors="coerce")):
                has_trust = True

    if not has_trust:
        if abs(trust_net_today) > 0:
            has_trust = True
        elif has_broker or (actual_dt != latest_all_dt):
            has_trust = True

    missing_items = []
    if not has_broker:
        missing_items.append("主力分點明細")
    elif not has_conc5:
        missing_items.append("5日集中度")
    if not has_margin:
        missing_items.append("融資融券")
    if not has_sbl:
        missing_items.append("借券賣出")

    return {
        "is_complete": len(missing_items) == 0,
        "missing_items": missing_items,
        "has_broker": has_broker,
        "has_conc5": has_conc5,
        "has_margin": has_margin,
        "has_sbl": has_sbl,
        "has_trust": has_trust,
        "anchor_date": actual_dt,
    }


def analyze_main_force(
    df_price: pd.DataFrame,
    df_chips: pd.DataFrame,
    period_label: str,
    df_chips_full: pd.DataFrame = None,
    df_price_full: pd.DataFrame = None,
    concentrations: dict = None,
    df_aux_full: pd.DataFrame = None,
    tdcc_history: list = None,
    issued_lots: float = 0.0,
    broker_diff_diag: str = "",
) -> dict:
    """整合全體波段分點合力、波段主力、廣度、投信獨立追蹤、融資四象限、情境借券、集保大戶與籌碼健康評分"""
    _ = broker_diff_diag
    all_cols = [c for c in df_chips.columns if c != "date"]
    pure_broker_cols = [c for c in all_cols if c != "投信"]

    full_df = df_chips_full if df_chips_full is not None else df_chips
    full_price = df_price_full if df_price_full is not None else df_price

    aux_aligned = None
    if not full_price.empty and df_aux_full is not None and not df_aux_full.empty:
        aux_aligned = full_price[["date"]].merge(
            df_aux_full.drop_duplicates(subset=["date"], keep="last"),
            on="date",
            how="left",
        )

    anchor_dt_chk = str(full_price["date"].iloc[-1]) if not full_price.empty else ""
    completeness = check_chip_completeness(full_price, full_df, df_aux_full, anchor_date=anchor_dt_chk)

    conc_out = {}
    raw_conc = concentrations if isinstance(concentrations, dict) else {}
    aux_conc_map = {"d5": "conc_5d", "d10": "conc_10d", "d20": "conc_20d"}
    if completeness["has_broker"]:
        for k, aux_col in aux_conc_map.items():
            base_item = dict(raw_conc.get(k, {})) if isinstance(raw_conc.get(k), dict) else {}
            z_val = base_item.get("z_score")
            z_samples = int(base_item.get("z_samples", 0) or 0)
            if aux_aligned is not None and aux_col in aux_aligned.columns:
                s_raw = pd.to_numeric(aux_aligned[aux_col], errors="coerce")
                s_clean = s_raw.dropna()
                z_samples = len(s_clean)
                if base_item.get("pct") is None and pd.notna(s_raw.iloc[-1]):
                    base_item["pct"] = round(float(s_raw.iloc[-1]), 2)
                if pd.notna(s_raw.iloc[-1]):
                    z_calc = compute_rolling_zscore(s_clean, window=STRATEGY_PARAMS["zscore_window"], min_periods=15)
                    if z_calc is not None:
                        z_val = z_calc
            base_item["z_score"] = z_val
            base_item["z_samples"] = z_samples
            conc_out[k] = base_item
    else:
        for k in aux_conc_map:
            conc_out[k] = {"pct": None, "net": 0, "buy15": 0, "sell15": 0, "z_score": None, "z_samples": 0}

    if df_price.empty or (not pure_broker_cols and "投信" not in all_cols):
        return {
            "completeness": completeness,
            "main_force_html": "無分點資料",
            "main_force_sub_lines": [],
            "status_html": "⚪ 無法判定",
            "status_sub": "",
            "divergence_alert": None,
            "scenario": {
                "id": 0, "type": "neutral", "sub_type": "無資料",
                "title": "⚪ 無資料", "desc": "", "action": "",
                "badges": [], "conviction_score": None if not completeness["is_complete"] else 50,
                "is_incomplete": not completeness["is_complete"],
                "missing_items": completeness["missing_items"],
                "score_breakdown": {
                    "base": 50,
                    "trend_total": 0, "trend_wave": 0, "trend_conc": 0, "trend_tdcc": 0, "trend_inst": 0,
                    "momentum_total": 0, "mom_conc_breadth": 0, "mom_credit_sbl": 0,
                    "day_trade": 0, "conc_breadth": 0, "conc_diff": 0,
                    "wave_trust": 0, "credit_sbl": 0, "tdcc": 0,
                },
                "margin_quadrant": 0,
                "sbl_mode": "neutral",
            },
            "concentrations": conc_out,
            "broker_tags": {},
            "wave_agg_info": None,
            "wave_broker_names": [],
            "top3_names": [],
            "top3_period_net": 0,
            "top3_period_ctrl_pct": 0.0,
            "top3_5d_ctrl_pct": 0.0,
            "top3_5d_ctrl_z": None,
            "top3_5d_float_pct": None,
            "top3_cost": 0.0,
            "top3_rows": [],
            "remark_md": "",
            "trust_info": None,
            "breadth_info": None,
            "broker_diff_info": None,
            "margin_info": None,
            "sbl_info": None,
            "tdcc_info": None,
            "winner_brokers": [],
            "recent_10d_rising": {},
            "top_buyers": {},
            "top_sellers": {},
            "ytd_top_buyers": {},
            "ytd_top_sellers": {},
            "today_buyers": {},
            "today_sellers": {},
        }

    anchor_date = str(full_price["date"].iloc[-1])
    period_vol = float(df_price["volume"].sum()) if "volume" in df_price.columns else 0.0
    vol_5d = float(full_price.tail(5)["volume"].sum()) if "volume" in full_price.columns else 0.0
    latest_close_full = float(full_price.iloc[-1]["close"])
    lb_days = STRATEGY_PARAMS["wave_lookback_days"]
    z_win = STRATEGY_PARAMS["zscore_window"]

    trust_info = None
    if "投信" in full_df.columns:
        trust_is_pending = (not completeness["has_trust"]) and (len(full_df) >= 2)
        if trust_is_pending:
            t_full_df = full_df.iloc[:-1].reset_index(drop=True)
            t_full_price = full_price.iloc[:-1].reset_index(drop=True)
            t_aux_aligned = aux_aligned.iloc[:-1].reset_index(drop=True) if aux_aligned is not None else None
            t_eff_date = str(t_full_price["date"].iloc[-1])
            t_period_chips = df_chips[df_chips["date"].astype(str) <= t_eff_date]
            t_period_price = df_price[df_price["date"].astype(str) <= t_eff_date]
            if t_period_chips.empty:
                t_period_chips = t_full_df.tail(1)
                t_period_price = t_full_price.tail(1)
            t_period_vol = float(t_period_price["volume"].sum()) if "volume" in t_period_price.columns else 0.0
            t_close_ref = float(t_full_price.iloc[-1]["close"])
        else:
            t_full_df = full_df
            t_full_price = full_price
            t_aux_aligned = aux_aligned
            t_eff_date = anchor_date
            t_period_chips = df_chips
            t_period_vol = period_vol
            t_close_ref = latest_close_full

        t_ytd = int(round(t_full_df["投信"].sum()))
        t_period = int(round(t_period_chips["投信"].sum())) if "投信" in t_period_chips.columns else 0
        t_d1 = int(round(t_full_df.tail(1)["投信"].sum()))
        t_d5 = int(round(t_full_df.tail(5)["投信"].sum()))
        t_d10 = int(round(t_full_df.tail(10)["投信"].sum()))
        t_d20 = int(round(t_full_df.tail(20)["投信"].sum()))
        t_streak = _calc_streak(t_full_df["投信"])
        t_ctrl = round((t_period / t_period_vol * 100.0), 1) if t_period_vol > 0 else 0.0

        merged_trust = t_full_price.merge(t_full_df[["date", "投信"]], on="date", how="left").fillna({"投信": 0.0})
        _, t_cost, t_inv = calculate_main_force_cost(merged_trust, merged_trust["投信"], window_days=lb_days)
        t_pnl = round((t_close_ref - t_cost) / t_cost * 100.0, 2) if (t_cost > 0 and t_inv > 0) else None

        holding_pct = None
        if t_aux_aligned is not None:
            if "trust_holding_pct" in t_aux_aligned.columns:
                s_tpct = pd.to_numeric(t_aux_aligned["trust_holding_pct"], errors="coerce").dropna()
                if not s_tpct.empty:
                    holding_pct = round(float(s_tpct.iloc[-1]), 2)
            if holding_pct is None and "trust_holding" in t_aux_aligned.columns and issued_lots > 0:
                s_thold = pd.to_numeric(t_aux_aligned["trust_holding"], errors="coerce").dropna()
                if not s_thold.empty:
                    holding_pct = round(float(s_thold.iloc[-1]) / issued_lots * 100.0, 2)

        trust_info = {
            "date": t_eff_date,
            "is_pending": trust_is_pending,
            "pending_date": anchor_date if trust_is_pending else "",
            "ytd": t_ytd,
            "period": t_period,
            "d1": t_d1,
            "d5": t_d5,
            "d10": t_d10,
            "d20": t_d20,
            "streak": t_streak,
            "ctrl_pct": t_ctrl,
            "cost": t_cost if (t_cost > 0 and t_inv > 0) else 0.0,
            "inv": int(round(t_inv)) if t_inv > 0 else 0,
            "pnl_pct": t_pnl,
            "holding_pct": holding_pct,
        }
        trust_info["phase"] = evaluate_trust_phase(trust_info)

    margin_info = None
    sbl_info = None
    tdcc_info = None

    if aux_aligned is not None:
        if "margin_bal" in aux_aligned.columns:
            s_m_raw = pd.to_numeric(aux_aligned["margin_bal"], errors="coerce")
            if pd.notna(s_m_raw.iloc[-1]):
                s_m = s_m_raw.dropna()
                s_s_raw = (
                    pd.to_numeric(aux_aligned["short_bal"], errors="coerce")
                    if "short_bal" in aux_aligned.columns
                    else pd.Series(dtype=float)
                )
                latest_m = int(round(float(s_m.iloc[-1])))
                base_m = float(s_m.iloc[-6]) if len(s_m) >= 6 else float(s_m.iloc[0])
                chg_5d = int(round(latest_m - base_m))
                chg_5d_pct = round((chg_5d / base_m * 100.0), 2) if base_m > 0 else 0.0
                chg_5d_z = compute_rolling_zscore(s_m.diff(5), window=z_win, min_periods=15)
                chg_5d_vol_pct = round((abs(chg_5d) / vol_5d * 100.0), 2) if vol_5d > 0 else None
                latest_s = int(round(float(s_s_raw.iloc[-1]))) if (len(s_s_raw) > 0 and pd.notna(s_s_raw.iloc[-1])) else 0
                sm_ratio = round((latest_s / latest_m * 100.0), 2) if latest_m > 0 else 0.0
                margin_info = {
                    "date": anchor_date,
                    "lag_days": 0,
                    "is_latest": True,
                    "latest_bal": latest_m,
                    "chg_5d": chg_5d,
                    "chg_5d_pct": chg_5d_pct,
                    "chg_5d_z": chg_5d_z,
                    "chg_5d_vol_pct": chg_5d_vol_pct,
                    "short_bal": latest_s,
                    "short_margin_ratio": sm_ratio,
                    "quadrant": 0,
                }

        if "sbl_bal" in aux_aligned.columns:
            s_sbl_raw = pd.to_numeric(aux_aligned["sbl_bal"], errors="coerce")
            if pd.notna(s_sbl_raw.iloc[-1]):
                s_sbl = s_sbl_raw.dropna()
                latest_sbl = int(round(float(s_sbl.iloc[-1])))
                base_sbl = float(s_sbl.iloc[-6]) if len(s_sbl) >= 6 else float(s_sbl.iloc[0])
                chg_5d = int(round(latest_sbl - base_sbl))
                chg_5d_pct = round((chg_5d / base_sbl * 100.0), 2) if base_sbl > 0 else 0.0
                chg_5d_z = compute_rolling_zscore(s_sbl.diff(5), window=z_win, min_periods=15)
                chg_5d_vol_pct = round((abs(chg_5d) / vol_5d * 100.0), 2) if vol_5d > 0 else None
                chg_1d = int(round(latest_sbl - float(s_sbl.iloc[-2]))) if len(s_sbl) >= 2 else 0
                sbl_info = {
                    "date": anchor_date,
                    "lag_days": 0,
                    "is_latest": True,
                    "latest_bal": latest_sbl,
                    "chg_5d": chg_5d,
                    "chg_5d_pct": chg_5d_pct,
                    "chg_5d_z": chg_5d_z,
                    "chg_5d_vol_pct": chg_5d_vol_pct,
                    "chg_1d": chg_1d,
                    "sbl_mode": "neutral",
                }

    if isinstance(tdcc_history, list) and tdcc_history:
        valid_tdcc = sorted(
            [t for t in tdcc_history if str(t.get("date", "")) <= anchor_date],
            key=lambda x: x.get("date", ""),
        )
        if valid_tdcc:
            latest_t = valid_tdcc[-1]
            prev_t = valid_tdcc[-2] if len(valid_tdcc) >= 2 else None
            t_dt = str(latest_t.get("date", ""))
            age_days = _date_diff_days(anchor_date, t_dt)
            tdcc_info = {
                "date": t_dt,
                "age_days": age_days,
                "is_stale": (age_days is not None and age_days > 14),
                "whale_1000_pct": float(latest_t.get("whale_1000_pct", 0.0)),
                "whale_400_pct": float(latest_t.get("whale_400_pct", 0.0)),
                "total_holders": int(latest_t.get("total_holders", 0)),
                "whale_1000_chg_1w": round(float(latest_t["whale_1000_pct"]) - float(prev_t["whale_1000_pct"]), 2) if prev_t else None,
                "whale_400_chg_1w": round(float(latest_t["whale_400_pct"]) - float(prev_t["whale_400_pct"]), 2) if prev_t else None,
                "holders_chg_1w": int(latest_t["total_holders"]) - int(prev_t["total_holders"]) if prev_t else None,
            }

    profiles = profile_brokers(full_price, full_df, lookback_days=lb_days)
    broker_tags = {k: v["tags"] for k, v in profiles.items() if v.get("tags")}
    top3_names, filtered_dt = select_wave_main_forces(profiles, top_n=3, exclude_day_traders=True)
    wave_agg_info = compute_wave_aggregate(full_price, full_df, profiles=profiles)
    wave_broker_names = wave_agg_info.get("wave_broker_names", []) if isinstance(wave_agg_info, dict) else []
    breadth_info = (
        compute_broker_breadth(full_df, profiles=profiles, lookback_days=5)
        if completeness["has_broker"]
        else None
    )

    dom_5d_buys = sorted(
        [v["net_5d"] for v in profiles.values() if not v.get("is_foreign") and not v.get("is_day_trader") and v["net_5d"] > 0],
        reverse=True,
    )
    domestic_top_buy_5d = int(sum(dom_5d_buys[:3])) if dom_5d_buys else 0
    if margin_info is not None:
        margin_info["domestic_top_buy_5d"] = domestic_top_buy_5d

    s_60d_all = pd.Series({k: v["net_60d"] for k, v in profiles.items()}).sort_values(ascending=False)
    s_ytd_all = pd.Series({k: v["net_ytd"] for k, v in profiles.items()}).sort_values(ascending=False)
    s_1d_all = pd.Series({k: v["net_1d"] for k, v in profiles.items()}).sort_values(ascending=False)

    top_buyers_60d = {k: int(v) for k, v in s_60d_all[s_60d_all > 0].head(5).items()}
    top_sellers_60d = {k: int(v) for k, v in s_60d_all[s_60d_all < 0].tail(5).sort_values(ascending=True).items()}
    ytd_buyers = {k: int(v) for k, v in s_ytd_all[s_ytd_all > 0].head(5).items()}
    ytd_sellers = {k: int(v) for k, v in s_ytd_all[s_ytd_all < 0].tail(5).sort_values(ascending=True).items()}
    today_buyers = {k: int(v) for k, v in s_1d_all[s_1d_all > 0].head(5).items()}
    today_sellers = {k: int(v) for k, v in s_1d_all[s_1d_all < 0].tail(5).sort_values(ascending=True).items()}

    winner_brokers = sorted(
        [v for v in profiles.values() if v.get("is_winner")],
        key=lambda x: (x["win_rate"], x["net_60d"]),
        reverse=True,
    )[:5]

    rising_10d = {
        k: v["net_10d"]
        for k, v in profiles.items()
        if not v.get("is_day_trader") and v["net_10d"] > 0
    }
    recent_10d_rising = dict(sorted(rising_10d.items(), key=lambda x: x[1], reverse=True)[:5])

    today_vol = float(full_price.iloc[-1]["volume"]) if "volume" in full_price.columns else 0.0
    dt_buyers_today = [k for k, v in profiles.items() if v.get("is_day_trader") and v["net_1d"] > 0]
    dt_buy_sum = sum(profiles[k]["net_1d"] for k in dt_buyers_today)
    dt_vol_pct = round((dt_buy_sum / today_vol * 100.0), 1) if today_vol > 0 else 0.0
    day_trade_info = {
        "is_heavy_day_trade": dt_vol_pct >= STRATEGY_PARAMS["dt_today_vol_warn_pct"],
        "dt_vol_pct": dt_vol_pct,
        "dt_buyers": dt_buyers_today,
    }

    if not top3_names:
        scenario = evaluate_chip_scenario(
            top3_5d=0,
            top3_5d_ctrl_pct=0.0,
            latest_close=latest_close_full,
            conc=conc_out,
            trust_info=trust_info,
            breadth_info=breadth_info,
            margin_info=margin_info,
            sbl_info=sbl_info,
            tdcc_info=tdcc_info,
            day_trade_info=day_trade_info,
            vol_5d=vol_5d,
            domestic_top_buy_5d=domestic_top_buy_5d,
            wave_agg_info=wave_agg_info,
        )
        if margin_info is not None:
            margin_info["quadrant"] = scenario.get("margin_quadrant", 0)
        if sbl_info is not None:
            sbl_info["sbl_mode"] = scenario.get("sbl_mode", "neutral")
        if breadth_info is not None:
            breadth_info["few_holding_up"] = bool(scenario.get("few_holding_up", False))
        if not completeness["is_complete"]:
            scenario["is_incomplete"] = True
            scenario["missing_items"] = completeness["missing_items"]
            scenario["conviction_score"] = None
        else:
            scenario["is_incomplete"] = False
            scenario["missing_items"] = []
        return {
            "completeness": completeness,
            "main_force_html": "無波段買超分點主力（偏空格局）",
            "main_force_sub_lines": ["近 60 日追蹤券商分點累計皆為淨賣超"],
            "status_html": "🟢 空方主導／出貨格局",
            "status_sub": "",
            "divergence_alert": None,
            "scenario": scenario,
            "concentrations": conc_out,
            "broker_tags": broker_tags,
            "wave_agg_info": wave_agg_info,
            "wave_broker_names": wave_broker_names,
            "top3_names": [],
            "top3_period_net": 0,
            "top3_period_ctrl_pct": 0.0,
            "top3_5d_ctrl_pct": 0.0,
            "top3_5d_ctrl_z": None,
            "top3_5d_float_pct": None,
            "top3_cost": 0.0,
            "top3_rows": [],
            "remark_md": "",
            "trust_info": trust_info,
            "breadth_info": breadth_info,
            "broker_diff_info": None,
            "margin_info": margin_info,
            "sbl_info": sbl_info,
            "tdcc_info": tdcc_info,
            "winner_brokers": winner_brokers,
            "recent_10d_rising": recent_10d_rising,
            "top_buyers": {},
            "top_sellers": top_sellers_60d,
            "ytd_top_buyers": ytd_buyers,
            "ytd_top_sellers": ytd_sellers,
            "today_buyers": today_buyers,
            "today_sellers": today_sellers,
        }

    primary_player = top3_names[0]
    primary_60d_net = profiles[primary_player]["net_60d"]
    primary_ytd_net = profiles[primary_player]["net_ytd"]

    merged_p1 = full_price.merge(full_df[["date", primary_player]], on="date", how="left").fillna({primary_player: 0.0})
    _, p1_cost, p1_inv = calculate_main_force_cost(merged_p1, merged_p1[primary_player], window_days=lb_days)

    merged_top3 = full_price.merge(full_df[["date"] + top3_names], on="date", how="left").fillna(0.0)
    top3_daily_series = merged_top3[top3_names].sum(axis=1)
    _, top3_cost, top3_inv = calculate_main_force_cost(merged_top3, top3_daily_series, window_days=lb_days)

    main_force_html = f"{primary_player}（近60日 {_fmt_html_num(primary_60d_net)}）"
    p1_cost_txt = f" ｜ 單一均價 <b style='color:#e2e8f0;'>{p1_cost:,.2f} 元</b>" if (p1_cost > 0 and p1_inv > 0) else ""
    main_force_sub_lines = [f"今年累計 {_fmt_html_num(primary_ytd_net)}{p1_cost_txt}"]
    if top3_cost > 0 and top3_inv > 0:
        pnl_top3 = (latest_close_full - top3_cost) / top3_cost * 100.0
        main_force_sub_lines.append(
            f"三大主力合計成本：<b style='color:#ffb74d;'>{top3_cost:,.2f} 元</b>（帳面 {_fmt_html_pct(pnl_top3)}）"
        )
    if filtered_dt:
        main_force_sub_lines.append(f"⚡ 已自動過濾短沖：{', '.join(filtered_dt[:3])}")

    period_sum = df_chips[pure_broker_cols].sum()
    top3_period_net = int(round(period_sum[top3_names].sum()))
    top3_period_ctrl_pct = round((top3_period_net / period_vol * 100.0), 1) if period_vol > 0 else 0.0
    top3_5d = int(round(top3_daily_series.tail(5).sum()))
    threshold = 50 if len(df_price) <= 1 else 300

    if top3_period_net > threshold:
        status_html = f"🔴 區間內三大波段主力吃貨中（{_fmt_html_num(top3_period_net)}）"
    elif top3_period_net < -threshold:
        status_html = f"🟢 區間內三大波段主力出貨中（{_fmt_html_num(top3_period_net)}）"
    else:
        status_html = f"🔵 區間內三大波段主力鎖碼觀望（{_fmt_html_num(top3_period_net)}）"

    status_sub = f"近 60 日波段三大主力：{', '.join(top3_names)}"
    top3_5d_ctrl_pct = round((top3_5d / vol_5d * 100.0), 1) if vol_5d > 0 else 0.0
    top3_5d_float_pct = round((top3_5d / issued_lots * 100.0), 2) if issued_lots > 0 else None

    vwap_col = full_price["vwap"] if "vwap" in full_price.columns else full_price["close"]
    top3_5d_amount_ntd = float((top3_daily_series.tail(5).values * vwap_col.tail(5).values * 1000.0).sum())

    rolling_5d_net = top3_daily_series.rolling(5).sum()
    rolling_5d_vol = full_price["volume"].rolling(5).sum().replace(0, np.nan)
    rolling_5d_ctrl_series = (rolling_5d_net / rolling_5d_vol) * 100.0
    top3_5d_ctrl_z = compute_rolling_zscore(rolling_5d_ctrl_series, window=z_win, min_periods=15)

    price_5d_pct = 0.0
    if len(full_price) >= 5:
        close_now = float(full_price.iloc[-1]["close"])
        close_5d_ago = float(full_price.iloc[-6]["close"]) if len(full_price) >= 6 else float(full_price.iloc[0]["close"])
        price_5d_pct = ((close_now - close_5d_ago) / close_5d_ago * 100.0) if close_5d_ago > 0 else 0.0

    c5_pct = conc_out.get("d5", {}).get("pct")
    divergence_alert = (
        evaluate_divergence_alert(
            price_5d_pct=price_5d_pct,
            top3_5d=top3_5d,
            top3_5d_ctrl_pct=top3_5d_ctrl_pct,
            latest_close=latest_close_full,
            top3_5d_amount_ntd=top3_5d_amount_ntd,
            top3_5d_ctrl_z=top3_5d_ctrl_z,
            conc_5d=c5_pct,
            trust_info=trust_info,
            margin_info=margin_info,
            sbl_info=sbl_info,
            wave_agg_info=wave_agg_info,
        )
        if completeness["is_complete"]
        else None
    )

    scenario = evaluate_chip_scenario(
        top3_5d=top3_5d,
        top3_5d_ctrl_pct=top3_5d_ctrl_pct,
        latest_close=latest_close_full,
        conc=conc_out,
        top3_5d_amount_ntd=top3_5d_amount_ntd,
        top3_5d_ctrl_z=top3_5d_ctrl_z,
        top3_5d_float_pct=top3_5d_float_pct,
        trust_info=trust_info,
        breadth_info=breadth_info,
        margin_info=margin_info,
        sbl_info=sbl_info,
        tdcc_info=tdcc_info,
        day_trade_info=day_trade_info,
        vol_5d=vol_5d,
        domestic_top_buy_5d=domestic_top_buy_5d,
        wave_agg_info=wave_agg_info,
    )
    if margin_info is not None:
        margin_info["quadrant"] = scenario.get("margin_quadrant", 0)
    if sbl_info is not None:
        sbl_info["sbl_mode"] = scenario.get("sbl_mode", "neutral")
    if breadth_info is not None:
        breadth_info["few_holding_up"] = bool(scenario.get("few_holding_up", False))

    if not completeness["is_complete"]:
        scenario["is_incomplete"] = True
        scenario["missing_items"] = completeness["missing_items"]
        scenario["conviction_score"] = None
    else:
        scenario["is_incomplete"] = False
        scenario["missing_items"] = []

    top3_rows = []
    for name in top3_names:
        prof = profiles[name]
        b_period = int(round(period_sum.get(name, 0)))
        ctrl_pct = (b_period / period_vol * 100.0) if period_vol > 0 else 0.0
        streak = _calc_streak(full_df[name])
        top3_rows.append({
            "name": name,
            "net_60d": prof["net_60d"],
            "ytd": prof["net_ytd"],
            "period": b_period,
            "d1": prof["net_1d"],
            "d10": prof["net_10d"],
            "d20": int(round(full_df.tail(20)[name].sum())),
            "streak": streak,
            "ctrl_pct": round(ctrl_pct, 1),
            "tags": prof.get("tags", []),
            "win_rate": prof.get("win_rate"),
            "avg_fwd_ret": prof.get("avg_fwd_ret"),
            "churn_ratio": prof.get("churn_ratio", 0.0),
        })

    sorted_period = period_sum.sort_values(ascending=False)
    top_buy_text = f"**{sorted_period.index[0]}**（{_fmt_md_num(int(round(sorted_period.iloc[0])))}）" if sorted_period.iloc[0] > 0 else "無"
    top_sell_text = f"**{sorted_period.index[-1]}**（{_fmt_md_num(int(round(sorted_period.iloc[-1])))}）" if sorted_period.iloc[-1] < 0 else "無"

    return {
        "completeness": completeness,
        "main_force_html": main_force_html,
        "main_force_sub_lines": main_force_sub_lines,
        "status_html": status_html,
        "status_sub": status_sub,
        "divergence_alert": divergence_alert,
        "scenario": scenario,
        "concentrations": conc_out,
        "broker_tags": broker_tags,
        "wave_agg_info": wave_agg_info,
        "wave_broker_names": wave_broker_names,
        "top3_names": top3_names,
        "top3_period_net": top3_period_net,
        "top3_period_ctrl_pct": top3_period_ctrl_pct,
        "top3_5d_ctrl_pct": top3_5d_ctrl_pct,
        "top3_5d_ctrl_z": top3_5d_ctrl_z,
        "top3_5d_float_pct": top3_5d_float_pct,
        "top3_cost": top3_cost if top3_inv > 0 else 0.0,
        "top3_rows": top3_rows,
        "remark_md": f"- 💡 備註：本檢視區間分點買超第一名為 {top_buy_text} ｜ 分點賣超第一名為 {top_sell_text}",
        "trust_info": trust_info,
        "breadth_info": breadth_info,
        "broker_diff_info": None,
        "margin_info": margin_info,
        "sbl_info": sbl_info,
        "tdcc_info": tdcc_info,
        "winner_brokers": winner_brokers,
        "recent_10d_rising": recent_10d_rising,
        "top_buyers": top_buyers_60d,
        "top_sellers": top_sellers_60d,
        "ytd_top_buyers": ytd_buyers,
        "ytd_top_sellers": ytd_sellers,
        "today_buyers": today_buyers,
        "today_sellers": today_sellers,
    }


def compute_daily_health_scores(
    df_price_full: pd.DataFrame,
    df_chips_full: pd.DataFrame,
    df_aux_full: pd.DataFrame = None,
    tdcc_history: list = None,
    issued_lots: float = 0.0,
    min_warmup_days: int = 20,
) -> pd.DataFrame:
    """
    純 NumPy 向量化回算每日「籌碼健康分數 (0~100)」與對應狀態：
    - 100% 對齊看板 analyze_main_force() 口徑（動態隔日沖過濾、Z-Score dropna/min_periods=10、融資/借券 dropna 5日變動、TDCC 天數差）
    - 若當日分點明細、5日集中度或近 3 日融資／借券尚未齊全，該日不計分 (NaN)
    """
    if df_price_full is None or df_price_full.empty or df_chips_full is None or df_chips_full.empty:
        return pd.DataFrame(columns=["date", "health_score", "scenario_id", "sub_type"])

    df_p = df_price_full.sort_values("date").reset_index(drop=True)
    df_c = df_p[["date"]].merge(df_chips_full, on="date", how="left").fillna(0.0)
    df_a = (
        df_p[["date"]].merge(df_aux_full.drop_duplicates(subset=["date"], keep="last"), on="date", how="left")
        if (df_aux_full is not None and not df_aux_full.empty)
        else pd.DataFrame({"date": df_p["date"]})
    )

    n = len(df_p)
    if n < min_warmup_days:
        return pd.DataFrame(columns=["date", "health_score", "scenario_id", "sub_type"])

    pure_cols = [c for c in df_c.columns if c not in ("date", "投信")]
    if not pure_cols:
        return pd.DataFrame(columns=["date", "health_score", "scenario_id", "sub_type"])

    n_brokers = len(pure_cols)
    pure_mat = df_c[pure_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    pure_5d_mat = pd.DataFrame(pure_mat).rolling(5, min_periods=1).sum().to_numpy(dtype=float)
    pure_row_abs_sum = np.abs(pure_mat).sum(axis=1)

    is_static_dt_arr = np.array([c in DAY_TRADE_BROKERS for c in pure_cols], dtype=bool)
    is_foreign_arr = np.array([is_foreign_broker(c) for c in pure_cols], dtype=bool)

    dates_str = df_p["date"].astype(str).tolist()
    date_ord_arr = np.array(
        [datetime.strptime(d[:10], "%Y-%m-%d").toordinal() for d in dates_str],
        dtype=int,
    )

    close_arr = pd.to_numeric(df_p["close"], errors="coerce").to_numpy(dtype=float)
    vol_arr = pd.to_numeric(df_p["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    vol5_sum_arr = pd.Series(vol_arr).rolling(5, min_periods=1).sum().to_numpy(dtype=float)
    vol20_arr_min10 = pd.Series(vol_arr).rolling(20, min_periods=10).sum().replace(0, np.nan).to_numpy(dtype=float)
    vol20_arr_full = pd.Series(vol_arr).rolling(20, min_periods=1).sum().to_numpy(dtype=float)
    vol60_arr_full = pd.Series(vol_arr).rolling(60, min_periods=1).sum().to_numpy(dtype=float)
    vwap_arr = (
        pd.to_numeric(df_p["vwap"], errors="coerce").fillna(df_p["close"]).to_numpy(dtype=float)
        if "vwap" in df_p.columns
        else close_arr
    )

    trust_arr = pd.to_numeric(df_c["投信"], errors="coerce").fillna(0.0).to_numpy(dtype=float) if "投信" in df_c.columns else np.zeros(n)
    trust_5d_arr = pd.Series(trust_arr).rolling(5, min_periods=1).sum().to_numpy(dtype=float)
    trust_20d_arr = pd.Series(trust_arr).rolling(20, min_periods=1).sum().to_numpy(dtype=float)
    trust_pct_arr = (
        pd.to_numeric(df_a["trust_holding_pct"], errors="coerce").to_numpy(dtype=float)
        if "trust_holding_pct" in df_a.columns
        else np.full(n, np.nan)
    )
    trust_hold_arr = (
        pd.to_numeric(df_a["trust_holding"], errors="coerce").to_numpy(dtype=float)
        if "trust_holding" in df_a.columns
        else np.full(n, np.nan)
    )

    c5_arr = pd.to_numeric(df_a["conc_5d"], errors="coerce").to_numpy(dtype=float) if "conc_5d" in df_a.columns else np.full(n, np.nan)
    c10_arr = pd.to_numeric(df_a["conc_10d"], errors="coerce").to_numpy(dtype=float) if "conc_10d" in df_a.columns else np.full(n, np.nan)
    c20_arr = pd.to_numeric(df_a["conc_20d"], errors="coerce").to_numpy(dtype=float) if "conc_20d" in df_a.columns else np.full(n, np.nan)
    m_arr = pd.to_numeric(df_a["margin_bal"], errors="coerce").to_numpy(dtype=float) if "margin_bal" in df_a.columns else np.full(n, np.nan)
    s_arr = pd.to_numeric(df_a["short_bal"], errors="coerce").to_numpy(dtype=float) if "short_bal" in df_a.columns else np.full(n, np.nan)
    sbl_arr = pd.to_numeric(df_a["sbl_bal"], errors="coerce").to_numpy(dtype=float) if "sbl_bal" in df_a.columns else np.full(n, np.nan)

    lb = STRATEGY_PARAMS["wave_lookback_days"]
    z_win = STRATEGY_PARAMS["zscore_window"]
    high_thresh = STRATEGY_PARAMS["breadth_streak_pct"]
    dt_churn_min = STRATEGY_PARAMS["dt_churn_ratio"]
    dt_flip_min = STRATEGY_PARAMS["dt_flip_rate"]

    tdcc_list = []
    if isinstance(tdcc_history, list):
        for t in sorted(tdcc_history, key=lambda x: x.get("date", "")):
            dt_t = str(t.get("date", ""))
            if len(dt_t) >= 10:
                try:
                    tdcc_list.append((t, datetime.strptime(dt_t[:10], "%Y-%m-%d").toordinal()))
                except Exception:
                    continue

    trust_streak_arr = np.zeros(n, dtype=int)
    cur_ts = 0
    for i in range(n):
        v = trust_arr[i]
        if v > 0:
            cur_ts = cur_ts + 1 if cur_ts > 0 else 1
        elif v < 0:
            cur_ts = cur_ts - 1 if cur_ts < 0 else -1
        else:
            cur_ts = 0
        trust_streak_arr[i] = cur_ts

    def _tail_zscore_dropna(arr: np.ndarray, idx: int) -> float | None:
        if idx < 0 or len(arr) == 0:
            return None
        sl = arr[: idx + 1]
        sl = sl[~np.isnan(sl)]
        if len(sl) < 15:
            return None
        sub = sl[-z_win:]
        std_v = float(np.std(sub))
        return round((float(sub[-1]) - float(np.mean(sub))) / std_v, 2) if std_v > 1e-9 else 0.0

    has_hist_margin = bool(np.any(~np.isnan(m_arr)))
    has_hist_sbl = bool(np.any(~np.isnan(sbl_arr)))

    records = []
    for i in range(n):
        dt_str = dates_str[i]
        if i + 1 < min_warmup_days:
            records.append({"date": dt_str, "health_score": np.nan, "scenario_id": 0, "sub_type": ""})
            continue

        is_recent_day = (i >= n - 3)
        broker_missing_i = (vol_arr[i] > 0) and (pure_row_abs_sum[i] <= 0)
        conc5_missing_i = np.isnan(c5_arr[i])
        margin_missing_i = is_recent_day and has_hist_margin and np.isnan(m_arr[i])
        sbl_missing_i = is_recent_day and has_hist_sbl and np.isnan(sbl_arr[i])
        if broker_missing_i or conc5_missing_i or margin_missing_i or sbl_missing_i:
            sub_lbl = "結算中" if (is_recent_day or broker_missing_i) else ""
            records.append({"date": dt_str, "health_score": np.nan, "scenario_id": 0, "sub_type": sub_lbl})
            continue

        w_start = max(0, i - lb + 1)
        win_mat = pure_mat[w_start : i + 1]
        net_60_vec = np.rint(win_mat.sum(axis=0))
        gross_60_vec = np.abs(win_mat).sum(axis=0)
        churn_vec = np.where(
            gross_60_vec > 0,
            np.round(gross_60_vec / np.maximum(np.abs(net_60_vec), 1.0), 2),
            0.0,
        )
        active_days_vec = (win_mat != 0).sum(axis=0)

        if win_mat.shape[0] >= 2:
            buy_mask = win_mat[:-1] > 0
            buy_cnt_vec = buy_mask.sum(axis=0)
            flip_cnt_vec = (buy_mask & (win_mat[1:] < 0)).sum(axis=0)
            flip_rate_vec = np.where(buy_cnt_vec >= 5, flip_cnt_vec / np.maximum(buy_cnt_vec, 1), 0.0)
        else:
            flip_rate_vec = np.zeros(n_brokers, dtype=float)

        is_dyn_dt = (~is_foreign_arr) & (active_days_vec >= 10) & (churn_vec >= dt_churn_min) & (flip_rate_vec >= dt_flip_min)
        dt_mask = is_static_dt_arr | is_dyn_dt
        wave_mask = ~dt_mask
        if not np.any(wave_mask):
            wave_mask = np.ones(n_brokers, dtype=bool)

        L = i + 1
        agg_daily_sub = pure_mat[:L, wave_mask].sum(axis=1)
        agg_cum_sub = np.cumsum(agg_daily_sub)
        cum_i = float(agg_cum_sub[-1])
        ma20_win = agg_cum_sub[max(0, i - 19) : L]
        ma20_i = float(np.mean(ma20_win)) if len(ma20_win) >= 5 else cum_i
        above_ma20_i = bool(cum_i > ma20_i) if abs(cum_i - ma20_i) > 1e-6 else None

        agg_5d_i = float(agg_daily_sub[max(0, i - 4) : L].sum())
        agg_20d_i = float(agg_daily_sub[max(0, i - 19) : L].sum())
        agg_60d_i = float(agg_daily_sub[w_start:L].sum())
        v20_i = float(vol20_arr_full[i])
        v60_i = float(vol60_arr_full[i])
        ctrl_20d_i = round((agg_20d_i / v20_i * 100.0), 2) if v20_i > 0 else 0.0
        ctrl_60d_i = round((agg_60d_i / v60_i * 100.0), 2) if v60_i > 0 else 0.0

        roll_20_net_arr = np.full(L, np.nan, dtype=float)
        if L >= 10:
            end_init = min(L, 20)
            roll_20_net_arr[9:end_init] = agg_cum_sub[9:end_init]
            if L > 20:
                roll_20_net_arr[20:] = agg_cum_sub[20:] - agg_cum_sub[: L - 20]
        with np.errstate(divide="ignore", invalid="ignore"):
            roll_20_ctrl_arr = (roll_20_net_arr / vol20_arr_min10[:L]) * 100.0
        ctrl_20d_z_i = _tail_zscore_dropna(roll_20_ctrl_arr, i)

        wave_agg_i = {
            "net_5d": int(round(agg_5d_i)),
            "net_20d": int(round(agg_20d_i)),
            "net_60d": int(round(agg_60d_i)),
            "ctrl_20d_pct": ctrl_20d_i,
            "ctrl_60d_pct": ctrl_60d_i,
            "ctrl_20d_z": ctrl_20d_z_i,
            "cum_above_ma20": above_ma20_i,
        }

        wave_indices = np.where(wave_mask)[0]
        sum_60_wave = net_60_vec[wave_indices]
        pos_rel = np.where(sum_60_wave > 0)[0]
        if len(pos_rel) > 0:
            top3_idx = wave_indices[pos_rel[np.argsort(sum_60_wave[pos_rel])[::-1][:3]]]
            top3_daily = pure_mat[:L, top3_idx].sum(axis=1)
            top3_5d_series = pure_5d_mat[:L, top3_idx].sum(axis=1)
        else:
            top3_daily = np.zeros(L)
            top3_5d_series = np.zeros(L)

        s5_start = max(0, i - 4)
        top3_5d = int(round(top3_5d_series[i]))
        vol_5d = float(vol5_sum_arr[i])
        top3_5d_ctrl_pct = round((top3_5d / vol_5d * 100.0), 1) if vol_5d > 0 else 0.0
        top3_5d_amt = float((top3_daily[s5_start:L] * vwap_arr[s5_start:L] * 1000.0).sum())
        top3_5d_flt = round((top3_5d / issued_lots * 100.0), 2) if issued_lots > 0 else None

        top3_5d_z = None
        if i >= 14:
            v5_sub = vol5_sum_arr[:L]
            ctrl_5d_arr = np.where(v5_sub > 0, (top3_5d_series / np.where(v5_sub > 0, v5_sub, 1.0)) * 100.0, np.nan)
            ctrl_5d_arr[:4] = np.nan
            top3_5d_z = _tail_zscore_dropna(ctrl_5d_arr, i)

        c5_z = _tail_zscore_dropna(c5_arr, i)

        row5_wave = pure_5d_mat[i, wave_mask]
        b5_i = int((row5_wave > 0).sum())
        s5_i = int((row5_wave < 0).sum())
        a5_i = b5_i + s5_i
        b5_pct_i = round(b5_i / a5_i * 100.0, 1) if a5_i > 0 else None

        f_wave_mask = wave_mask & is_foreign_arr
        fb5_i = fs5_i = fa5_i = 0
        fb5_pct_i = None
        if np.any(f_wave_mask):
            f_row5 = pure_5d_mat[i, f_wave_mask]
            fb5_i = int((f_row5 > 0).sum())
            fs5_i = int((f_row5 < 0).sum())
            fa5_i = fb5_i + fs5_i
            if fa5_i > 0:
                fb5_pct_i = round(fb5_i / fa5_i * 100.0, 1)

        high_streak_i = 0
        for back_idx in range(i, max(-1, i - 15), -1):
            r_d = pure_mat[back_idx, wave_mask]
            bd = int((r_d > 0).sum())
            sd = int((r_d < 0).sum())
            ad = bd + sd
            if ad < 4 or (bd / ad * 100.0) < high_thresh:
                break
            high_streak_i += 1

        breadth_i = {
            "breadth_5d": b5_pct_i,
            "buy_5d": b5_i,
            "sell_5d": s5_i,
            "active_5d": a5_i,
            "foreign_breadth_5d": fb5_pct_i,
            "foreign_buy_5d": fb5_i,
            "foreign_sell_5d": fs5_i,
            "foreign_active_5d": fa5_i,
            "high_streak": high_streak_i,
        }

        dt_info_i = None
        if np.any(dt_mask) and vol_arr[i] > 0:
            dt_indices = np.where(dt_mask)[0]
            row_dt = pure_mat[i, dt_indices]
            dt_pos_rel = np.where(row_dt > 0)[0]
            dt_pct_i = round(float(row_dt[dt_pos_rel].sum()) / float(vol_arr[i]) * 100.0, 1) if len(dt_pos_rel) > 0 else 0.0
            if dt_pct_i > 0:
                dt_names_i = [pure_cols[dt_indices[k]] for k in dt_pos_rel[:3]]
                dt_info_i = {
                    "is_heavy_day_trade": dt_pct_i >= STRATEGY_PARAMS["dt_today_vol_warn_pct"],
                    "dt_vol_pct": dt_pct_i,
                    "dt_buyers": dt_names_i,
                }

        dom_wave_mask = wave_mask & (~is_foreign_arr)
        dom_buy_5d = 0
        if np.any(dom_wave_mask):
            row5_dom = pure_5d_mat[i, dom_wave_mask]
            pos_dom = row5_dom[row5_dom > 0]
            if len(pos_dom) > 0:
                dom_buy_5d = int(round(np.sort(pos_dom)[::-1][:3].sum()))

        t_hold_i = None
        if not np.isnan(trust_pct_arr[i]):
            t_hold_i = round(float(trust_pct_arr[i]), 2)
        elif not np.isnan(trust_hold_arr[i]) and issued_lots > 0:
            t_hold_i = round(float(trust_hold_arr[i]) / issued_lots * 100.0, 2)

        t_info_i = {
            "streak": int(trust_streak_arr[i]),
            "d5": int(round(trust_5d_arr[i])),
            "d20": int(round(trust_20d_arr[i])),
            "cost": 0.0,
            "holding_pct": t_hold_i,
        }

        mg_i = None
        if not np.isnan(m_arr[i]):
            m_valid = m_arr[:L]
            m_valid = m_valid[~np.isnan(m_valid)]
            latest_m = int(round(float(m_valid[-1])))
            base_m = float(m_valid[-6]) if len(m_valid) >= 6 else float(m_valid[0])
            chg_m = int(round(latest_m - base_m))
            chg_m_pct = round(chg_m / base_m * 100.0, 2) if base_m > 0 else 0.0
            m_diff5 = (m_valid[5:] - m_valid[:-5]) if len(m_valid) >= 6 else np.empty(0, dtype=float)
            m_z_i = _tail_zscore_dropna(m_diff5, len(m_diff5) - 1) if len(m_diff5) >= 15 else None
            latest_s = int(round(float(s_arr[i]))) if not np.isnan(s_arr[i]) else 0
            sm_r = round(latest_s / latest_m * 100.0, 2) if latest_m > 0 else 0.0
            mg_i = {
                "chg_5d": chg_m,
                "chg_5d_pct": chg_m_pct,
                "chg_5d_z": m_z_i,
                "chg_5d_vol_pct": round(abs(chg_m) / vol_5d * 100.0, 2) if vol_5d > 0 else None,
                "short_margin_ratio": sm_r,
                "domestic_top_buy_5d": dom_buy_5d,
            }

        sbl_i = None
        if not np.isnan(sbl_arr[i]):
            sbl_valid = sbl_arr[:L]
            sbl_valid = sbl_valid[~np.isnan(sbl_valid)]
            latest_sbl = int(round(float(sbl_valid[-1])))
            base_sbl = float(sbl_valid[-6]) if len(sbl_valid) >= 6 else float(sbl_valid[0])
            chg_sbl = int(round(latest_sbl - base_sbl))
            chg_sbl_pct = round(chg_sbl / base_sbl * 100.0, 2) if base_sbl > 0 else 0.0
            sbl_diff5 = (sbl_valid[5:] - sbl_valid[:-5]) if len(sbl_valid) >= 6 else np.empty(0, dtype=float)
            sbl_z_i = _tail_zscore_dropna(sbl_diff5, len(sbl_diff5) - 1) if len(sbl_diff5) >= 15 else None
            sbl_i = {
                "chg_5d": chg_sbl,
                "chg_5d_pct": chg_sbl_pct,
                "chg_5d_z": sbl_z_i,
                "chg_5d_vol_pct": round(abs(chg_sbl) / vol_5d * 100.0, 2) if vol_5d > 0 else None,
            }

        tdcc_i = None
        if tdcc_list:
            cur_ord = int(date_ord_arr[i])
            past_t = [(t, o) for (t, o) in tdcc_list if o <= cur_ord]
            if len(past_t) >= 2:
                (lt, lt_ord), (pt, _) = past_t[-1], past_t[-2]
                tdcc_i = {
                    "age_days": max(0, cur_ord - lt_ord),
                    "whale_1000_chg_1w": round(float(lt["whale_1000_pct"]) - float(pt["whale_1000_pct"]), 2),
                    "whale_400_chg_1w": round(float(lt["whale_400_pct"]) - float(pt["whale_400_pct"]), 2),
                    "holders_chg_1w": int(lt["total_holders"]) - int(pt["total_holders"]),
                }

        conc_i = {
            "d5": {"pct": round(float(c5_arr[i]), 2), "z_score": c5_z},
            "d10": {"pct": round(float(c10_arr[i]), 2) if not np.isnan(c10_arr[i]) else None},
            "d20": {"pct": round(float(c20_arr[i]), 2) if not np.isnan(c20_arr[i]) else None},
        }

        sc = evaluate_chip_scenario(
            top3_5d=top3_5d,
            top3_5d_ctrl_pct=top3_5d_ctrl_pct,
            latest_close=float(close_arr[i]),
            conc=conc_i,
            top3_5d_amount_ntd=top3_5d_amt,
            top3_5d_ctrl_z=top3_5d_z,
            top3_5d_float_pct=top3_5d_flt,
            trust_info=t_info_i,
            breadth_info=breadth_i,
            margin_info=mg_i,
            sbl_info=sbl_i,
            tdcc_info=tdcc_i,
            day_trade_info=dt_info_i,
            vol_5d=vol_5d,
            domestic_top_buy_5d=dom_buy_5d,
            wave_agg_info=wave_agg_i,
        )
        records.append({
            "date": dt_str,
            "health_score": sc.get("conviction_score", 50),
            "scenario_id": sc.get("id", 0),
            "sub_type": sc.get("sub_type", ""),
        })

    return pd.DataFrame(records)


# =====================================================================
# GitHub 雲端同步（支援 Git Data API 單次打包 Commit）與資料庫管理
# =====================================================================
def _get_github_config():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("REPO_NAME", "").strip()
    if not token or not repo:
        try:
            import streamlit as st
            token = str(st.secrets.get("GITHUB_TOKEN", "")).strip()
            repo = str(st.secrets.get("REPO_NAME", "")).strip()
        except Exception:
            pass
    return token, repo


def sync_files_to_github_batch(file_pairs: list[tuple[str, str]], commit_message: str = "Sync database") -> tuple[bool, str]:
    token, repo = _get_github_config()
    if not token or not repo:
        return False, "未讀取到 GITHUB_TOKEN 或 REPO_NAME（請確認 Streamlit Secrets）"

    valid_pairs = [(lp, rp) for lp, rp in file_pairs if os.path.exists(lp)]
    if not valid_pairs:
        return True, "無需同步檔案"

    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    base_api = f"https://api.github.com/repos/{repo}"

    try:
        repo_resp = requests.get(base_api, headers=headers, timeout=10)
        if repo_resp.status_code != 200:
            return False, f"讀取儲存庫失敗 (HTTP {repo_resp.status_code})"
        default_branch = repo_resp.json().get("default_branch", "main")

        ref_resp = requests.get(f"{base_api}/git/ref/heads/{default_branch}", headers=headers, timeout=10)
        if ref_resp.status_code != 200:
            return False, f"讀取分支 {default_branch} 失敗 (HTTP {ref_resp.status_code})"
        head_sha = ref_resp.json()["object"]["sha"]

        commit_resp = requests.get(f"{base_api}/git/commits/{head_sha}", headers=headers, timeout=10)
        if commit_resp.status_code != 200:
            return False, f"讀取 HEAD Commit 失敗 (HTTP {commit_resp.status_code})"
        base_tree_sha = commit_resp.json()["tree"]["sha"]

        tree_items = []
        for local_path, repo_path in valid_pairs:
            with open(local_path, "rb") as f:
                content_b64 = base64.b64encode(f.read()).decode("utf-8")
            blob_resp = requests.post(
                f"{base_api}/git/blobs",
                headers=headers,
                json={"content": content_b64, "encoding": "base64"},
                timeout=15,
            )
            if blob_resp.status_code != 201:
                return False, f"建立 Blob 失敗 ({repo_path}: HTTP {blob_resp.status_code})"
            tree_items.append({
                "path": repo_path,
                "mode": "100644",
                "type": "blob",
                "sha": blob_resp.json()["sha"],
            })

        tree_resp = requests.post(
            f"{base_api}/git/trees",
            headers=headers,
            json={"base_tree": base_tree_sha, "tree": tree_items},
            timeout=15,
        )
        if tree_resp.status_code != 201:
            return False, f"建立 Tree 失敗 (HTTP {tree_resp.status_code})"
        new_tree_sha = tree_resp.json()["sha"]

        new_commit_resp = requests.post(
            f"{base_api}/git/commits",
            headers=headers,
            json={"message": commit_message, "tree": new_tree_sha, "parents": [head_sha]},
            timeout=15,
        )
        if new_commit_resp.status_code != 201:
            return False, f"建立 Commit 失敗 (HTTP {new_commit_resp.status_code})"
        new_commit_sha = new_commit_resp.json()["sha"]

        patch_resp = requests.patch(
            f"{base_api}/git/refs/heads/{default_branch}",
            headers=headers,
            json={"sha": new_commit_sha},
            timeout=15,
        )
        if patch_resp.status_code not in (200, 201):
            return False, f"更新分支失敗 (HTTP {patch_resp.status_code})"

        return True, f"已單次打包同步 {len(valid_pairs)} 個檔案至 GitHub database/"
    except Exception as e:
        return False, f"GitHub 打包同步異常 ({type(e).__name__})"


def _sync_file_to_github(local_path: str, repo_path: str) -> tuple[bool, str]:
    return sync_files_to_github_batch([(local_path, repo_path)], commit_message=f"Sync {repo_path}")


def _delete_file_from_github(repo_path: str) -> tuple[bool, str]:
    token, repo = _get_github_config()
    if not token or not repo:
        return False, "未設定 GITHUB_TOKEN 或 REPO_NAME"

    api_url = f"https://api.github.com/repos/{repo}/contents/{repo_path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }

    get_resp = requests.get(api_url, headers=headers, timeout=10)
    if get_resp.status_code == 404:
        return True, "檔案已不存在"
    if get_resp.status_code != 200:
        return False, f"查詢失敗 (HTTP {get_resp.status_code})"

    sha = get_resp.json().get("sha")
    del_resp = requests.delete(
        api_url,
        headers=headers,
        json={"message": f"Delete {repo_path} from Streamlit", "sha": sha},
        timeout=15,
    )
    if del_resp.status_code in (200, 204):
        return True, "已從 GitHub 刪除"
    return False, f"GitHub 刪除失敗 (HTTP {del_resp.status_code})"


def load_stock_order(existing_ids: list) -> list:
    saved_order = []
    if os.path.exists(ORDER_FILE):
        try:
            with open(ORDER_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    saved_order = [str(x).strip() for x in data]
        except Exception:
            pass

    existing_set = set(existing_ids)
    ordered = [sid for sid in saved_order if sid in existing_set]
    for sid in sorted(existing_ids):
        if sid not in ordered:
            ordered.append(sid)
    return ordered


def save_stock_order(order_list: list, sync_github: bool = True) -> None:
    clean_list = []
    for sid in order_list:
        s = str(sid).strip()
        if s and s not in clean_list:
            clean_list.append(s)

    with open(ORDER_FILE, "w", encoding="utf-8") as f:
        json.dump(clean_list, f, ensure_ascii=False, indent=2)

    is_gha = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    if sync_github and not is_gha:
        _sync_file_to_github(ORDER_FILE, "database/stock_order.json")


def delete_stock_database(stock_id: str) -> tuple[bool, str]:
    stock_id = stock_id.strip()
    target_files = [
        f"{stock_id}_price.csv",
        f"{stock_id}_chips.csv",
        f"{stock_id}_aux.csv",
        f"{stock_id}_meta.json",
    ]

    for fname in target_files:
        local_path = os.path.join(DB_DIR, fname)
        if os.path.exists(local_path):
            os.remove(local_path)
        _delete_file_from_github(f"database/{fname}")

    if os.path.exists(ORDER_FILE):
        try:
            with open(ORDER_FILE, "r", encoding="utf-8") as f:
                cur_order = json.load(f)
            if isinstance(cur_order, list) and stock_id in cur_order:
                cur_order = [x for x in cur_order if x != stock_id]
                save_stock_order(cur_order, sync_github=True)
        except Exception:
            pass

    return True, f"已刪除 {stock_id}"


def update_stock_database(stock_id: str, incremental_days: int = None, sync_github: bool = True) -> bool:
    """
    更新指定個股資料並存檔至 database/：
    - incremental_days=None（新增個股）：從 2026-01-02 至今全年度建檔
    - incremental_days=22（全部更新）：近 1 個月滾動更新（既有老分點更新近 1 個月不洗洞；
      自動掃描近 20 日前 10 大新波段主力並單獨補齊全年明細）
    """
    stock_id = stock_id.strip()
    now_tw = _get_tw_now()
    end_date = now_tw.strftime("%Y-%m-%d")

    price_file = f"{stock_id}_price.csv"
    chips_file = f"{stock_id}_chips.csv"
    aux_file = f"{stock_id}_aux.csv"
    meta_file = f"{stock_id}_meta.json"
    price_path = os.path.join(DB_DIR, price_file)
    chips_path = os.path.join(DB_DIR, chips_file)
    aux_path = os.path.join(DB_DIR, aux_file)
    meta_path = os.path.join(DB_DIR, meta_file)

    saved_meta = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                saved_meta = json.load(f)
        except Exception:
            saved_meta = {}
    preferred_suffix = saved_meta.get("ticker_suffix") if isinstance(saved_meta, dict) else None
    saved_tracked_brokers = saved_meta.get("tracked_brokers", {}) if isinstance(saved_meta, dict) else {}

    is_incremental = (
        incremental_days is not None
        and incremental_days > 0
        and os.path.exists(price_path)
        and os.path.exists(chips_path)
    )

    existing_chip_cols: set[str] = set()
    if is_incremental:
        df_price_old = pd.read_csv(price_path)
        df_chips_old = pd.read_csv(chips_path)
        if df_price_old.empty or df_chips_old.empty:
            is_incremental = False
        else:
            existing_chip_cols = {c for c in df_chips_old.columns if c not in ("date", "投信")}

    if is_incremental:
        lookback_cal_days = max(12, int(incremental_days * 1.8) + 10)
        lookback_start = (now_tw - timedelta(days=lookback_cal_days)).strftime("%Y-%m-%d")
        df_price_recent, matched_suffix = fetch_price_history(stock_id, lookback_start, end_date, preferred_suffix)
        if df_price_recent.empty:
            return False
        df_price_recent = df_price_recent.tail(incremental_days).copy()
        fetch_start = df_price_recent["date"].min()
    else:
        fetch_start = START_DATE_FIXED
        df_price_recent, matched_suffix = fetch_price_history(stock_id, fetch_start, end_date, preferred_suffix)
        if df_price_recent.empty:
            return False

    recent_trade_dates = df_price_recent["date"].tail(3).astype(str).tolist()
    active_suffix = matched_suffix or preferred_suffix

    session = _create_session()
    df_chips_recent = df_price_recent[["date"]].copy()
    df_aux_recent = df_price_recent[["date"]].copy()
    debug_notes = []

    # 1. 抓投信（含真實總持股、持股比率與已公布旗標 trust_published）
    df_trust, active_mirror, trust_err = fetch_investment_trust_history(session, stock_id, fetch_start, end_date)
    if not df_trust.empty:
        df_chips_recent = df_chips_recent.merge(df_trust[["date", "投信"]], on="date", how="left")
        trust_aux_cols = [c for c in ["date", "trust_holding", "trust_holding_pct"] if c in df_trust.columns]
        df_trust_aux = df_trust[trust_aux_cols].copy()
        df_trust_aux["trust_published"] = 1.0
        df_aux_recent = df_aux_recent.merge(df_trust_aux, on="date", how="left")
    else:
        if not is_incremental:
            df_chips_recent["投信"] = 0.0
        if trust_err:
            debug_notes.append(f"投信異常: {trust_err}")

    # 2. 抓中文股票名稱與已發行張數
    stock_name = str(saved_meta.get("stock_name", "")).strip() if isinstance(saved_meta, dict) else ""
    if not stock_name:
        stock_name = fetch_stock_name(session, stock_id, active_mirror)

    issued_lots = float(saved_meta.get("issued_lots", 0.0) or 0.0) if isinstance(saved_meta, dict) else 0.0
    if issued_lots <= 0:
        issued_lots = fetch_issued_lots(stock_id, active_suffix)

    # 3. 取得關鍵券商分點名單
    #    - 增量模式：保留既有追蹤池 ＋ 自動掃描近 20 日前 10 大（5買5賣）偵測新波段主力
    #    - 全量建檔：抓取今年累計前 30 大 ＋ 近 20 日前 10 大
    merged_broker_map = {}
    new_wave_broker_names: set[str] = set()

    if is_incremental and isinstance(saved_tracked_brokers, dict):
        for b_name, b_info in saved_tracked_brokers.items():
            if isinstance(b_info, dict) and (b_info.get("bhid") or b_info.get("b")):
                merged_broker_map[b_name] = {
                    "name": b_name,
                    "bhid": str(b_info.get("bhid", "")),
                    "b": str(b_info.get("b", "")),
                }

    if (not is_incremental) or (not merged_broker_map):
        brokers, active_mirror, broker_err = fetch_top_brokers(session, stock_id, fetch_start, end_date, active_mirror, limit=30)
        if broker_err:
            debug_notes.append(f"分點清單異常: {broker_err}")
        for b in brokers:
            merged_broker_map[b["name"]] = b
            if is_incremental and b["name"] not in existing_chip_cols:
                new_wave_broker_names.add(b["name"])

    if len(df_price_recent) >= 5:
        recent_20d_start = df_price_recent.tail(min(20, len(df_price_recent)))["date"].min()
        recent_20d_brokers, active_mirror, _ = fetch_top_brokers(
            session, stock_id, recent_20d_start, end_date, active_mirror, limit=10
        )
        for rb in recent_20d_brokers:
            b_name = rb["name"]
            if b_name not in merged_broker_map:
                merged_broker_map[b_name] = rb
            if is_incremental and b_name not in existing_chip_cols:
                new_wave_broker_names.add(b_name)

    target_brokers = list(merged_broker_map.values())

    # 4. 併發抓取分點每日買賣超明細
    #    - 老分點：只抓 fetch_start (近 1 個月)
    #    - 增量模式中新冒出的波段主力：單獨從 START_DATE_FIXED 補齊全年明細（同為 1 次請求）
    fetched_broker_cols = []
    new_full_broker_dfs: dict[str, pd.DataFrame] = {}

    if target_brokers:
        with ThreadPoolExecutor(max_workers=6) as executor:
            future_map = {}
            for b in target_brokers:
                b_start = START_DATE_FIXED if (is_incremental and b["name"] in new_wave_broker_names) else fetch_start
                future_map[executor.submit(fetch_broker_daily_history, stock_id, b, b_start, end_date, active_mirror)] = b

            for future in as_completed(future_map):
                b = future_map[future]
                b_name = b["name"]
                try:
                    df_b = future.result()
                    if df_b.empty:
                        continue
                    if is_incremental and b_name in new_wave_broker_names:
                        new_full_broker_dfs[b_name] = df_b
                    elif b_name not in df_chips_recent.columns:
                        df_chips_recent = df_chips_recent.merge(df_b, on="date", how="left")
                        fetched_broker_cols.append(b_name)
                except Exception:
                    continue

    fetched_count = len(fetched_broker_cols) + len(new_full_broker_dfs)
    if fetched_broker_cols:
        df_chips_recent[fetched_broker_cols] = df_chips_recent[fetched_broker_cols].fillna(0.0)
    if not is_incremental:
        df_chips_recent = df_chips_recent.fillna(0.0)

    # 5. 抓取輔助籌碼（融資融券、官方直抓＋FinMind 借券賣出）
    df_margin = fetch_margin_history(session, stock_id, fetch_start, end_date, active_mirror)
    if not df_margin.empty:
        df_aux_recent = df_aux_recent.merge(df_margin, on="date", how="left")
    else:
        debug_notes.append("融資融券暫無回傳（FinMind/MoneyDJ）")

    df_sbl = fetch_sbl_history(
        session,
        stock_id,
        fetch_start,
        end_date,
        active_mirror,
        preferred_suffix=active_suffix,
        recent_trade_dates=recent_trade_dates,
    )
    if not df_sbl.empty:
        df_aux_recent = df_aux_recent.merge(df_sbl, on="date", how="left")
    else:
        debug_notes.append("借券賣出暫無回傳（TWSE/TPEx/FinMind）")

    # 6. 合併或覆蓋資料庫（增量模式以 combine_first 保護舊日有效資料，永不誤刪最新日或洗空舊值）
    if is_incremental:
        df_old_p = df_price_old.drop_duplicates(subset=["date"], keep="last").copy()
        df_old_p["date"] = df_old_p["date"].astype(str)
        df_new_p = df_price_recent.drop_duplicates(subset=["date"], keep="last").copy()
        df_new_p["date"] = df_new_p["date"].astype(str)
        df_p_comb = df_new_p.set_index("date").combine_first(df_old_p.set_index("date"))
        df_p_comb.index.name = "date"
        df_price = df_p_comb.reset_index().sort_values("date").reset_index(drop=True)
        if "adj_factor" in df_price.columns:
            df_price["adj_factor"] = pd.to_numeric(df_price["adj_factor"], errors="coerce").ffill().bfill().fillna(1.0)

        df_old_c = df_chips_old.drop_duplicates(subset=["date"], keep="last").copy()
        df_old_c["date"] = df_old_c["date"].astype(str)
        df_old_c = df_old_c.set_index("date")
        df_new_c = df_chips_recent.drop_duplicates(subset=["date"], keep="last").copy()
        df_new_c["date"] = df_new_c["date"].astype(str)
        df_new_c = df_new_c.set_index("date")

        old_pure_cols = [c for c in df_old_c.columns if c != "投信"]
        new_pure_cols = [c for c in df_new_c.columns if c != "投信"]
        if old_pure_cols and new_pure_cols:
            for dt_c in df_new_c.index.intersection(df_old_c.index):
                old_abs = float(pd.to_numeric(df_old_c.loc[dt_c, old_pure_cols], errors="coerce").fillna(0.0).abs().sum())
                new_abs = float(pd.to_numeric(df_new_c.loc[dt_c, new_pure_cols], errors="coerce").fillna(0.0).abs().sum())
                if old_abs > 0 and new_abs == 0:
                    df_new_c.loc[dt_c, new_pure_cols] = np.nan

        all_dates_c = sorted(set(df_old_c.index) | set(df_new_c.index))
        all_cols_c = list(dict.fromkeys(list(df_old_c.columns) + list(df_new_c.columns)))
        df_merged_c = df_old_c.reindex(index=all_dates_c, columns=all_cols_c)
        for col_c in df_new_c.columns:
            df_merged_c.loc[df_new_c.index, col_c] = df_new_c[col_c].combine_first(df_merged_c.loc[df_new_c.index, col_c])

        # 將本次新發現的波段主力「全年度明細」直接併入完整日期索引
        for b_name, df_b in new_full_broker_dfs.items():
            s_b = df_b.drop_duplicates(subset=["date"], keep="last").copy()
            s_b["date"] = s_b["date"].astype(str)
            df_merged_c[b_name] = s_b.set_index("date")[b_name].reindex(df_merged_c.index).fillna(0.0)

        df_merged_c.index.name = "date"
        df_chips = df_merged_c.fillna(0.0).reset_index().sort_values("date").reset_index(drop=True)
        total_brokers = len([c for c in df_chips.columns if c not in ("date", "投信")])

        if os.path.exists(aux_path):
            df_old_a = pd.read_csv(aux_path).drop_duplicates(subset=["date"], keep="last")
            df_old_a["date"] = df_old_a["date"].astype(str)
            df_new_a = df_aux_recent.drop_duplicates(subset=["date"], keep="last").copy()
            df_new_a["date"] = df_new_a["date"].astype(str)
            df_a_comb = df_new_a.set_index("date").combine_first(df_old_a.set_index("date"))
            df_a_comb.index.name = "date"
            df_aux = df_a_comb.reset_index().sort_values("date").reset_index(drop=True)
        else:
            df_aux = df_aux_recent
    else:
        df_price = df_price_recent
        df_chips = df_chips_recent
        total_brokers = fetched_count
        if os.path.exists(aux_path):
            df_aux_prev = pd.read_csv(aux_path).drop_duplicates(subset=["date"], keep="last")
            df_aux_prev["date"] = df_aux_prev["date"].astype(str)
            df_new_idx = df_aux_recent.copy()
            df_new_idx["date"] = df_new_idx["date"].astype(str)
            df_new_idx = df_new_idx.set_index("date")
            df_prev_idx = df_aux_prev.set_index("date")
            keep_cols = [
                c for c in [
                    "conc_5d", "conc_10d", "conc_20d",
                    "margin_bal", "short_bal", "sbl_bal",
                    "trust_holding", "trust_holding_pct", "trust_published",
                ]
                if c in df_prev_idx.columns
            ]
            for col in keep_cols:
                if col in df_new_idx.columns:
                    df_new_idx[col] = df_new_idx[col].combine_first(df_prev_idx[col].reindex(df_new_idx.index))
                else:
                    df_new_idx[col] = df_prev_idx[col].reindex(df_new_idx.index)
            df_new_idx.index.name = "date"
            df_aux = df_new_idx.reset_index()
        else:
            df_aux = df_aux_recent

        if target_brokers and fetched_count == 0:
            debug_notes.append(f"找到 {len(target_brokers)} 個分點連結，但明細回傳為空")

    # 計算哪些交易日已具備有效分點明細（供集中度寫入防護使用）
    pure_cols_all = [c for c in df_chips.columns if c not in ("date", "投信")]
    valid_broker_dates: set[str] = set()
    if pure_cols_all and not df_price.empty:
        tmp_chk = df_price[["date", "volume"]].merge(df_chips, on="date", how="left")
        row_abs = tmp_chk[pure_cols_all].apply(pd.to_numeric, errors="coerce").fillna(0.0).abs().sum(axis=1)
        vol_s_chk = pd.to_numeric(tmp_chk["volume"], errors="coerce").fillna(0.0)
        valid_broker_dates = set(tmp_chk.loc[(row_abs > 0) | (vol_s_chk <= 0), "date"].astype(str).tolist())

    # 7. 補齊 _aux.csv 中的官方每日集中度（增量模式掃描近 1 個月以自動修復偶發缺漏）
    conc_scan_days = max(7, int(incremental_days) + 4) if is_incremental else None
    df_aux = _populate_aux_concentrations(
        stock_id=stock_id,
        df_price=df_price,
        df_aux=df_aux,
        mirror=active_mirror,
        only_recent_days=conc_scan_days,
        max_workers=8,
        valid_broker_dates=valid_broker_dates,
    )

    # 8. 僅在最新交易日已具備有效分點明細時，才抓取並寫入最新 5/10/20 日集中度
    latest_dt_str = str(df_price["date"].iloc[-1]) if not df_price.empty else ""
    concentrations = {}
    if latest_dt_str and (latest_dt_str in valid_broker_dates):
        concentrations = fetch_rolling_concentrations(stock_id, df_price, active_mirror)
        if not df_aux.empty and isinstance(concentrations, dict):
            last_i = len(df_aux) - 1
            for k, col in (("d5", "conc_5d"), ("d10", "conc_10d"), ("d20", "conc_20d")):
                pct_val = concentrations.get(k, {}).get("pct")
                if pct_val is not None:
                    df_aux.loc[last_i, col] = pct_val

    # 9. 抓取最新一週 TDCC 集保大戶（共用快取）
    tdcc_history_old = saved_meta.get("tdcc_history", []) if isinstance(saved_meta, dict) else []
    tdcc_history = fetch_tdcc_snapshot(session, stock_id, tdcc_history_old)

    # 10. 寫入本地 CSV
    df_price.to_csv(price_path, index=False, encoding="utf-8-sig")
    df_chips.to_csv(chips_path, index=False, encoding="utf-8-sig")
    df_aux.to_csv(aux_path, index=False, encoding="utf-8-sig")

    # 11. 產出摘要並持久化 tracked_brokers
    meta = analyze_main_force(
        df_price,
        df_chips,
        f"{START_DATE_FIXED} 至今",
        df_chips_full=df_chips,
        df_price_full=df_price,
        concentrations=concentrations,
        df_aux_full=df_aux,
        tdcc_history=tdcc_history,
        issued_lots=issued_lots,
    )
    meta["stock_id"] = stock_id
    meta["stock_name"] = stock_name
    meta["ticker_suffix"] = active_suffix or ""
    meta["issued_lots"] = issued_lots
    meta["broker_count"] = total_brokers
    meta["tracked_brokers"] = {
        name: {"bhid": info["bhid"], "b": info["b"]}
        for name, info in merged_broker_map.items()
    }
    meta["tdcc_history"] = tdcc_history
    meta["last_updated"] = now_tw.strftime("%Y-%m-%d %H:%M")
    meta["debug_notes"] = debug_notes

    is_gha = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    if is_gha:
        meta["github_sync"] = (
            f"✅ GitHub Actions 自動滾動同步（近 {incremental_days} 日）"
            if is_incremental
            else "✅ GitHub Actions 自動同步"
        )
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    elif sync_github:
        meta["github_sync"] = "✅ 已單次打包同步 4 個檔案至 GitHub database/"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        ok_sync, sync_msg = sync_files_to_github_batch(
            [
                (price_path, f"database/{price_file}"),
                (chips_path, f"database/{chips_file}"),
                (aux_path, f"database/{aux_file}"),
                (meta_path, f"database/{meta_file}"),
            ],
            commit_message=f"Update {stock_id} ({now_tw.strftime('%Y-%m-%d')})",
        )
        if not ok_sync:
            meta["github_sync"] = f"⚠️ {sync_msg}"
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
    else:
        meta["github_sync"] = "✅ 本地已更新（等待批次打包同步）"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    return True


if __name__ == "__main__":
    existing_stocks = sorted({
        fname.split("_")[0]
        for fname in os.listdir(DB_DIR)
        if fname.endswith("_price.csv")
    })
    print(f"[Daily Update] 開始近 1 個月滾動更新（22 個交易日）共 {len(existing_stocks)} 檔個股：{existing_stocks}", flush=True)
    for sid in existing_stocks:
        t0 = time.time()
        ok = update_stock_database(sid, incremental_days=22)
        status = "成功" if ok else "失敗"
        print(f"[Daily Update] {sid} 更新{status}（耗時 {time.time() - t0:.1f} 秒）", flush=True)
