"""
data_engine.py — 台股多維度籌碼資料引擎
支援：
1. 股價（含 VWAP 均價）、前 30 大波段分點＋近 60 日＋近 3 日活躍分點、5/10/20 日全市場集中度
2. 投信買賣超與估計持股水位、融資融券與券資比、TWSE/TPEX 借券賣出餘額（含批量快取）
3. TDCC 集保戶股權分散表（神秘金字塔優先＋集保官方 OpenData 備援）、買賣家數差
4. 滾動 60 日波段主力篩選、隔日沖過濾、贏家分點勝率與純口徑 60 日滾動 Z-Score 分析
"""
from __future__ import annotations

import os
import re
import io
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
    DAY_TRADE_BROKERS,  # noqa: F401
    STRATEGY_PARAMS,
    SCENARIO_MATRIX,  # noqa: F401
    compute_rolling_zscore,
    profile_brokers,
    select_wave_main_forces,
    evaluate_trust_phase,
    evaluate_chip_scenario,
    evaluate_divergence_alert,
)

# 關閉 verify=False 警告與 yfinance 終端機洗版
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

# 單次執行期記憶體快取（避免批量更新多檔股票時重複打 TWSE / TPEX / TDCC 全市場端點）
_SBL_DAILY_CACHE: dict[tuple[str, str], dict[str, float]] = {}
_TDCC_LATEST_CACHE: dict[str, dict] = {}


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
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    })
    return session


def _fetch_dj_html(
    session: requests.Session,
    path_with_query: str,
    preferred_mirror: str = None,
    timeout: int = 6,
):
    mirrors = (
        [preferred_mirror] + [m for m in DJ_MIRRORS if m != preferred_mirror]
        if preferred_mirror
        else DJ_MIRRORS
    )
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
    """支援西元年 (2026/11/20、20261120) 與民國年 (115/11/20)，統一轉為 YYYY-MM-DD"""
    s = date_str.strip().replace("-", "/")
    if re.match(r"^\d{8}$", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    m = re.match(r"^(\d{2,4})/(\d{1,2})/(\d{1,2})$", s)
    if not m:
        return ""
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 1911:
        year += 1911
    return f"{year:04d}-{month:02d}-{day:02d}"


def _parse_short_or_full_date(date_str: str, ref_dt: datetime) -> str:
    """支援 YYYY/MM/DD、民國年與 MM/DD（HiStock 常用短日期格式）"""
    full = _parse_dj_date(date_str)
    if full:
        return full
    s = date_str.strip().replace("-", "/")
    m = re.match(r"^(\d{1,2})/(\d{1,2})$", s)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = ref_dt.year
        if month > ref_dt.month + 1:
            year -= 1
        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except ValueError:
            return ""
    return ""


def _format_dj_date(ad_date: str) -> str:
    dt = datetime.strptime(ad_date, "%Y-%m-%d")
    return f"{dt.year}-{dt.month}-{dt.day}"


def _get_tw_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Taipei"))
    except Exception:
        return datetime.utcnow() + timedelta(hours=8)


def _to_float_safe(v) -> float | None:
    s = str(v).replace(",", "").replace("+", "").replace("%", "").strip()
    if s in ("", "--", "-", "N/A", "nan", "None"):
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
    vals = pd.to_numeric(series, errors="coerce").fillna(0.0).tolist()
    if not vals or vals[-1] == 0:
        return 0
    sign = 1 if vals[-1] > 0 else -1
    streak = 0
    for v in reversed(vals):
        if (sign > 0 and v > 0) or (sign < 0 and v < 0):
            streak += 1
        else:
            break
    return int(streak * sign)


# =====================================================================
# 一、 主力成本計算（支援 VWAP 真實均價與滾動視窗）
# =====================================================================
def calculate_main_force_cost(
    df_price: pd.DataFrame,
    daily_net: pd.Series,
    window_days: int | None = None,
) -> tuple[pd.Series, float, float]:
    """
    移動加權平均成本法（目前庫存均價）：
    - 優先使用當日真實成交均價 (vwap)，缺值時退回 (O+H+L+C)/4 或 close。
    - 若傳入 window_days（例如 60），僅以最近 N 個交易日起算庫存成本，降低年初截斷誤差。
    """
    if df_price.empty:
        return pd.Series(dtype=float), 0.0, 0.0

    price_cols = [c for c in ["open", "high", "low", "close"] if c in df_price.columns]
    ohlc_avg = df_price[price_cols].mean(axis=1).fillna(df_price["close"]) if price_cols else df_price["close"]

    if "vwap" in df_price.columns:
        vwap_s = pd.to_numeric(df_price["vwap"], errors="coerce")
        avg_prices = vwap_s.where(vwap_s > 0, ohlc_avg)
    else:
        avg_prices = ohlc_avg

    start_idx = max(0, len(df_price) - window_days) if (window_days and window_days > 0) else 0

    inv = 0.0
    avg_cost = 0.0
    cost_list = []

    for i, (p, net) in enumerate(zip(avg_prices, daily_net)):
        if i < start_idx:
            cost_list.append(None)
            continue

        if pd.isna(p) or pd.isna(net):
            cost_list.append(round(avg_cost, 2) if inv > 0 and avg_cost > 0 else None)
            continue

        net_f = float(net)
        p_f = float(p)
        if net_f > 0:
            if inv <= 0:
                inv = net_f
                avg_cost = p_f
            else:
                avg_cost = (inv * avg_cost + net_f * p_f) / (inv + net_f)
                inv += net_f
        elif net_f < 0:
            inv += net_f
            if inv <= 0:
                inv = 0.0
                avg_cost = 0.0

        cost_list.append(round(avg_cost, 2) if inv > 0 and avg_cost > 0 else None)

    latest_cost = round(float(avg_cost), 2) if inv > 0 and avg_cost > 0 else 0.0
    return pd.Series(cost_list, index=df_price.index), latest_cost, float(inv)


# =====================================================================
# 二、 股價（含 VWAP）、股本（發行張數）與中文名稱抓取
# =====================================================================
def _fetch_twse_tpex_price(
    stock_id: str,
    start_date: str,
    end_date: str,
    preferred_suffix: str = None,
) -> tuple[pd.DataFrame, str]:
    """向證交所 (TWSE) 或櫃買中心 (TPEX) 抓取每日 OHLC、成交量(張) 與真實成交均價 (VWAP)"""
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
                    vol_shares = _to_float_safe(item[1])
                    turnover_ntd = _to_float_safe(item[2])
                    op = _to_float_safe(item[3])
                    hi = _to_float_safe(item[4])
                    lo = _to_float_safe(item[5])
                    cl = _to_float_safe(item[6])
                    if cl is not None:
                        vwap = (
                            round(turnover_ntd / vol_shares, 2)
                            if (turnover_ntd and vol_shares and vol_shares > 0)
                            else round(((op or cl) + (hi or cl) + (lo or cl) + cl) / 4.0, 2)
                        )
                        rows.append({
                            "date": ad_date,
                            "open": op if op is not None else cl,
                            "high": hi if hi is not None else cl,
                            "low": lo if lo is not None else cl,
                            "close": cl,
                            "volume": round(vol_shares / 1000.0) if vol_shares is not None else 0.0,
                            "vwap": vwap,
                        })
            except Exception:
                continue
        if rows:
            return pd.DataFrame(rows).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
        return pd.DataFrame()

    def _try_tpex() -> pd.DataFrame:
        rows = []
        for y, m in months:
            roc_ym = f"{y - 1911:03d}/{m:02d}"
            url = f"https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php?l=zh-tw&d={roc_ym}&stkno={stock_id}"
            try:
                resp = session.get(url, timeout=8, verify=False)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                for item in (data.get("aaData") or []):
                    if len(item) < 7:
                        continue
                    ad_date = _parse_dj_date(str(item[0]))
                    if not ad_date or not (start_date <= ad_date <= end_date):
                        continue
                    vol_k = _to_float_safe(item[1])
                    turnover_k = _to_float_safe(item[2])
                    op = _to_float_safe(item[3])
                    hi = _to_float_safe(item[4])
                    lo = _to_float_safe(item[5])
                    cl = _to_float_safe(item[6])
                    if cl is not None:
                        vwap = (
                            round(turnover_k / vol_k, 2)
                            if (turnover_k and vol_k and vol_k > 0)
                            else round(((op or cl) + (hi or cl) + (lo or cl) + cl) / 4.0, 2)
                        )
                        rows.append({
                            "date": ad_date,
                            "open": op if op is not None else cl,
                            "high": hi if hi is not None else cl,
                            "low": lo if lo is not None else cl,
                            "close": cl,
                            "volume": round(vol_k) if vol_k is not None else 0.0,
                            "vwap": vwap,
                        })
            except Exception:
                continue
        if rows:
            return pd.DataFrame(rows).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
        return pd.DataFrame()

    order = [".TWO", ".TW"] if preferred_suffix == ".TWO" else [".TW", ".TWO"]
    for suf in order:
        df = _try_twse() if suf == ".TW" else _try_tpex()
        if not df.empty:
            return df, suf

    return pd.DataFrame(), ""


def fetch_price_history(
    stock_id: str,
    start_date: str,
    end_date: str,
    preferred_suffix: str = None,
) -> tuple[pd.DataFrame, str]:
    """
    抓取指定區間每日股價並回傳 (DataFrame, 成功後綴 '.TW' 或 '.TWO')：
    - 短天期（<= 35 日）優先向 TWSE/TPEX 取得含真實 VWAP 的官方日行情。
    - 長天期或官方失敗時使用 yfinance 並估算 (O+H+L+C)/4 作為 vwap。
    """
    dt_span = (datetime.strptime(end_date, "%Y-%m-%d") - datetime.strptime(start_date, "%Y-%m-%d")).days
    if dt_span <= 35:
        df_off, suf_off = _fetch_twse_tpex_price(stock_id, start_date, end_date, preferred_suffix)
        if not df_off.empty:
            return df_off, suf_off

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
                df = df[["date", "Open", "High", "Low", "Close", "Volume"]].copy()
                df.columns = ["date", "open", "high", "low", "close", "volume"]
                df["volume"] = (df["volume"] / 1000.0).round(0)
                df["vwap"] = df[["open", "high", "low", "close"]].mean(axis=1).round(2)
                df_out = df[df["date"] >= start_date].dropna(subset=["close"])
                if not df_out.empty:
                    return df_out, suffix
        except Exception:
            continue

    return _fetch_twse_tpex_price(stock_id, start_date, end_date, preferred_suffix)


def fetch_issued_lots(
    session: requests.Session,
    stock_id: str,
    suffix: str = ".TW",
    mirror: str = None,
) -> float:
    """取得公司在外發行總張數（張 = 總股數 / 1000），供投信持股比與佔股本比率計算"""
    try:
        tkr = yf.Ticker(f"{stock_id}{suffix or '.TW'}")
        shares = tkr.fast_info.get("shares")
        if shares and float(shares) > 10000:
            return round(float(shares) / 1000.0, 2)
    except Exception:
        pass

    try:
        html, _, _ = _fetch_dj_html(session, f"/z/zc/zca/zca.djhtm?a={stock_id}", mirror, timeout=5)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            tds = [td.get_text(strip=True) for td in soup.find_all("td")]
            for i, txt in enumerate(tds):
                if "股本(億" in txt and i + 1 < len(tds):
                    cap_yi = _to_float_safe(tds[i + 1])
                    if cap_yi and cap_yi > 0:
                        return round(cap_yi * 10000.0, 2)
    except Exception:
        pass

    return 0.0


def fetch_stock_name(session: requests.Session = None, stock_id: str = "", mirror: str = None) -> str:
    """多來源擷取台股中文名稱"""
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


# =====================================================================
# 三、 投信（買賣超＋估計持股）、融資融券、借券賣出、家數差、TDCC 集保
# =====================================================================
def fetch_investment_trust_history(
    session: requests.Session,
    stock_id: str,
    start_date: str,
    end_date: str,
    mirror: str = None,
):
    """抓取「投信」每日淨買賣超(張) 與 投信估計持股(張)（同一頁零額外請求）"""
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)
    html, ok_mirror, err = _fetch_dj_html(
        session, f"/z/zc/zcl/zcl.djhtm?a={stock_id}&c={s_fmt}&d={e_fmt}", mirror
    )
    if not html:
        return pd.DataFrame(columns=["date", "投信", "trust_holding"]), mirror, err

    soup = BeautifulSoup(html, "html.parser")
    records = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True).replace(",", "").replace("+", "") for td in tr.find_all("td")]
        if len(tds) >= 4:
            ad_date = _parse_dj_date(tds[0])
            if ad_date and start_date <= ad_date <= end_date:
                net_val = _to_float_safe(tds[2])
                hold_val = _to_float_safe(tds[6]) if len(tds) >= 7 else None
                if net_val is not None:
                    records.append({
                        "date": ad_date,
                        "投信": net_val,
                        "trust_holding": hold_val if hold_val is not None else np.nan,
                    })

    if not records:
        return pd.DataFrame(columns=["date", "投信", "trust_holding"]), ok_mirror, "投信表格無資料"

    df = (
        pd.DataFrame(records)
        .groupby("date", as_index=False)
        .agg({"投信": "sum", "trust_holding": "last"})
    )
    return df, ok_mirror, ""


def fetch_margin_history(
    session: requests.Session,
    stock_id: str,
    start_date: str,
    end_date: str,
    mirror: str = None,
) -> pd.DataFrame:
    """從 MoneyDJ zcn.djhtm 抓取每日融資餘額、融資增減、融券餘額、融券增減與券資比(%)"""
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)
    html, _, _ = _fetch_dj_html(
        session, f"/z/zc/zcn/zcn.djhtm?a={stock_id}&c={s_fmt}&d={e_fmt}", mirror, timeout=6
    )
    cols = ["date", "margin_bal", "margin_chg", "short_bal", "short_chg", "short_margin_ratio"]
    if not html:
        return pd.DataFrame(columns=cols)

    soup = BeautifulSoup(html, "html.parser")
    records = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) >= 14:
            ad_date = _parse_dj_date(tds[0])
            if ad_date and start_date <= ad_date <= end_date:
                m_bal = _to_float_safe(tds[4])
                m_chg = _to_float_safe(tds[5])
                s_bal = _to_float_safe(tds[11])
                s_chg = _to_float_safe(tds[12])
                sm_rat = _to_float_safe(tds[13])
                if m_bal is not None:
                    records.append({
                        "date": ad_date,
                        "margin_bal": m_bal,
                        "margin_chg": m_chg if m_chg is not None else 0.0,
                        "short_bal": s_bal if s_bal is not None else 0.0,
                        "short_chg": s_chg if s_chg is not None else 0.0,
                        "short_margin_ratio": sm_rat if sm_rat is not None else 0.0,
                    })

    if not records:
        return pd.DataFrame(columns=cols)

    return pd.DataFrame(records).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)


def _fetch_sbl_day_market(session: requests.Session, market: str, ad_date: str) -> dict[str, float]:
    """抓取單一交易日全市場（TWSE 或 TPEX）各股借券賣出當日餘額(張)，結果自動快取"""
    cache_key = (market, ad_date)
    if cache_key in _SBL_DAILY_CACHE:
        return _SBL_DAILY_CACHE[cache_key]

    result: dict[str, float] = {}
    dt = datetime.strptime(ad_date, "%Y-%m-%d")

    if market == ".TW":
        ymd = dt.strftime("%Y%m%d")
        url = f"https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U?date={ymd}&response=json"
        try:
            resp = session.get(url, timeout=8, verify=False)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("stat") == "OK" and data.get("data"):
                    fields = data.get("fields") or []
                    bal_indices = [
                        idx for idx, f in enumerate(fields)
                        if ("餘額" in str(f) and "前日" not in str(f))
                    ]
                    sbl_idx = bal_indices[1] if len(bal_indices) >= 2 else 12
                    for row in data["data"]:
                        if len(row) > sbl_idx:
                            sid = str(row[0]).strip()
                            shares = _to_float_safe(row[sbl_idx])
                            if sid and shares is not None:
                                result[sid] = round(shares / 1000.0, 2)
        except Exception:
            pass
    else:
        ad_slash = dt.strftime("%Y/%m/%d")
        roc_slash = f"{dt.year - 1911:03d}/{dt.month:02d}/{dt.day:02d}"
        urls = [
            f"https://www.tpex.org.tw/www/zh-tw/margin/sbl?date={ad_slash}&response=json",
            f"https://www.tpex.org.tw/web/stock/margin_trading/margin_sbl/margin_sbl_result.php?l=zh-tw&d={roc_slash}",
        ]
        for url in urls:
            try:
                resp = session.get(url, timeout=8, verify=False)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                rows = []
                if isinstance(data.get("tables"), list) and data["tables"]:
                    rows = data["tables"][0].get("data") or []
                elif data.get("aaData"):
                    rows = data.get("aaData") or []
                for row in rows:
                    if len(row) >= 13:
                        sid = str(row[0]).strip()
                        shares = _to_float_safe(row[12])
                        if sid and shares is not None:
                            result[sid] = round(shares / 1000.0, 2)
                if result:
                    break
            except Exception:
                continue

    _SBL_DAILY_CACHE[cache_key] = result
    return result


def fetch_sbl_history(
    session: requests.Session,
    stock_id: str,
    dates: list[str],
    market_suffix: str = ".TW",
) -> pd.DataFrame:
    """抓取指定交易日清單的個股借券賣出餘額(張)與日增減"""
    cols = ["date", "sbl_bal", "sbl_chg"]
    if not dates:
        return pd.DataFrame(columns=cols)

    target_dates = sorted(set(dates))[-8:]
    market = ".TWO" if market_suffix == ".TWO" else ".TW"

    records = []
    for d in target_dates:
        day_map = _fetch_sbl_day_market(session, market, d)
        if stock_id in day_map:
            records.append({"date": d, "sbl_bal": day_map[stock_id]})

    if not records:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(records).sort_values("date").reset_index(drop=True)
    df["sbl_chg"] = df["sbl_bal"].diff().fillna(0.0).round(2)
    return df


def fetch_market_broker_diff(session: requests.Session, stock_id: str) -> pd.DataFrame:
    """
    從 HiStock 取得全市場每日『買賣家數差』（同時支援 YYYY/MM/DD 與 MM/DD 格式）；
    若遇網路阻擋則回傳空表，由 analyze_main_force 自動退回追蹤分點家數差。
    """
    cols = ["date", "broker_diff_mkt"]
    ref_dt = _get_tw_now()
    try:
        url = f"https://histock.tw/stock/brokertr.aspx?no={stock_id}"
        resp = session.get(url, headers={"Referer": "https://histock.tw/"}, timeout=6, verify=False)
        if resp.status_code != 200 or len(resp.text) < 500:
            return pd.DataFrame(columns=cols)
        soup = BeautifulSoup(resp.text, "html.parser")
        records = []
        for table in soup.find_all("table"):
            trs = table.find_all("tr")
            if not trs:
                continue
            headers = [c.get_text(strip=True) for c in trs[0].find_all(["th", "td"])]
            diff_idx = None
            for idx, h in enumerate(headers):
                if "家數差" in h:
                    diff_idx = idx
                    break
            if diff_idx is None:
                continue
            for tr in trs[1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) > diff_idx:
                    ad_date = _parse_short_or_full_date(tds[0], ref_dt)
                    val = _to_float_safe(tds[diff_idx])
                    if ad_date and val is not None:
                        records.append({"date": ad_date, "broker_diff_mkt": val})
            if records:
                break
        if records:
            return pd.DataFrame(records).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    except Exception:
        pass
    return pd.DataFrame(columns=cols)


def _fetch_norway_tdcc_history(session: requests.Session, stock_id: str) -> list[dict]:
    """
    主來源：向「神秘金字塔 (norway.twsthr.info)」抓取單一個股近十餘週之集保大戶明細。
    頁面輕量、免下載全市場大檔，一次即可取得 400 張／1000 張大戶比率與總股東人數週變動。
    """
    url = f"https://norway.twsthr.info/StockHolders.aspx?stock={stock_id}"
    try:
        resp = session.get(
            url,
            headers={"Referer": "https://norway.twsthr.info/"},
            timeout=12,
            verify=False,
        )
        if resp.status_code != 200 or len(resp.content) < 1000:
            return []
        resp.encoding = resp.apparent_encoding or "utf-8"
        soup = BeautifulSoup(resp.text, "html.parser")

        results: dict[str, dict] = {}
        for table in soup.find_all("table"):
            txt = table.get_text(" ", strip=True)
            if "總股東人數" not in txt or "1000" not in txt:
                continue

            trs = table.find_all("tr")
            header_row_idx = None
            headers: list[str] = []
            for idx, tr in enumerate(trs[:8]):
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
                joined = " ".join(cells)
                if "日期" in joined and "總股東人數" in joined:
                    header_row_idx = idx
                    headers = cells
                    break

            if header_row_idx is None:
                continue

            # 動態定位關鍵欄位索引
            h_date = None
            h_holders = None
            h_w400 = None
            h_w1000 = None
            for i, h in enumerate(headers):
                h_clean = h.replace(" ", "")
                if h_date is None and "日期" in h_clean:
                    h_date = i
                elif h_holders is None and "總股東人數" in h_clean:
                    h_holders = i
                elif h_w400 is None and "400" in h_clean and ("百分比" in h_clean or "%" in h_clean):
                    h_w400 = i
                elif h_w1000 is None and "1000" in h_clean and ("百分比" in h_clean or "%" in h_clean):
                    h_w1000 = i

            if h_date is None or h_holders is None or (h_w400 is None and h_w1000 is None):
                continue

            for tr in trs[header_row_idx + 1:]:
                tds = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(tds) < 6:
                    continue

                # 容錯前置核取方塊／序號欄造成的欄位偏移
                actual_date_idx = None
                ad_date = ""
                for probe in range(min(4, len(tds))):
                    parsed = _parse_dj_date(tds[probe])
                    if parsed:
                        actual_date_idx = probe
                        ad_date = parsed
                        break
                if actual_date_idx is None or not ad_date:
                    continue

                offset = actual_date_idx - h_date

                def _get_val(h_idx):
                    if h_idx is None:
                        return None
                    real_idx = h_idx + offset
                    if 0 <= real_idx < len(tds):
                        return _to_float_safe(tds[real_idx])
                    return None

                holders_f = _get_val(h_holders)
                w400_f = _get_val(h_w400)
                w1000_f = _get_val(h_w1000)
                if holders_f is None:
                    continue

                results[ad_date] = {
                    "date": ad_date,
                    "whale_1000_pct": round(float(w1000_f), 2) if w1000_f is not None else 0.0,
                    "whale_400_pct": round(float(w400_f), 2) if w400_f is not None else 0.0,
                    "total_holders": int(round(float(holders_f))),
                }

            if results:
                break

        return [results[k] for k in sorted(results.keys())][-12:]
    except Exception:
        return []


def _load_tdcc_latest_opendata(session: requests.Session) -> dict[str, dict]:
    """備援來源：下載集保中心官方 OpenData 最新一週全市場股權分散表（單次執行期快取）"""
    if _TDCC_LATEST_CACHE:
        return _TDCC_LATEST_CACHE

    urls = [
        "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5",
        "https://smart.tdcc.com.tw/opendata/getOD.ashx?id=1-5",
    ]
    for url in urls:
        try:
            resp = session.get(url, timeout=30, verify=False)
            if resp.status_code != 200 or len(resp.content) < 5000:
                continue
            text = resp.content.decode("utf-8-sig", errors="ignore")
            df = pd.read_csv(io.StringIO(text), dtype=str)
            if df.shape[1] < 6:
                continue

            c_date, c_sid, c_lvl, c_ppl, _, c_pct = df.columns[:6]
            df[c_sid] = df[c_sid].str.strip()
            df[c_lvl] = pd.to_numeric(df[c_lvl], errors="coerce")
            df[c_ppl] = pd.to_numeric(df[c_ppl].str.replace(",", ""), errors="coerce").fillna(0)
            df[c_pct] = pd.to_numeric(df[c_pct].str.replace(",", ""), errors="coerce").fillna(0.0)

            for sid, grp in df.groupby(c_sid):
                raw_dt = str(grp[c_date].iloc[0]).strip()
                ad_date = _parse_dj_date(raw_dt) or raw_dt
                w1000 = float(grp.loc[grp[c_lvl] == 15, c_pct].sum())
                w400 = float(grp.loc[grp[c_lvl].isin([12, 13, 14, 15]), c_pct].sum())
                tot_rows = grp.loc[grp[c_lvl] == 17, c_ppl]
                holders = int(tot_rows.iloc[0]) if not tot_rows.empty else int(grp.loc[grp[c_lvl] <= 15, c_ppl].sum())
                _TDCC_LATEST_CACHE[sid] = {
                    "date": ad_date,
                    "whale_1000_pct": round(w1000, 2),
                    "whale_400_pct": round(w400, 2),
                    "total_holders": holders,
                }
            if _TDCC_LATEST_CACHE:
                break
        except Exception:
            continue

    return _TDCC_LATEST_CACHE


def fetch_tdcc_weekly_history(
    session: requests.Session,
    stock_id: str,
    existing_history: list | None = None,
) -> list[dict]:
    """
    取得近 12 週 TDCC 集保戶股權分散表（千張／400張大戶持股比與總股東人數）：
    1. 優先抓取「神秘金字塔」單股頁面，一次取得多週歷史與最新週變動。
    2. 若神秘金字塔無法連線或尚未更新最新一週，再向官方集保 OpenData CSV 備援補齊。
    """
    merged: dict[str, dict] = {}
    for item in (existing_history or []):
        if isinstance(item, dict) and item.get("date"):
            merged[str(item["date"])] = item

    # 1. 優先：神秘金字塔（單股輕量頁面，支援多週歷史）
    norway_rows = _fetch_norway_tdcc_history(session, stock_id)
    for row in norway_rows:
        merged[row["date"]] = row

    # 2. 備援／校準：官方集保 OpenData（僅當神秘金字塔缺資料或逾 8 天未更新時才拉全市場大檔）
    need_opendata = (len(merged) == 0)
    if not need_opendata:
        try:
            latest_dt = datetime.strptime(max(merged.keys()), "%Y-%m-%d")
            if (_get_tw_now().replace(tzinfo=None) - latest_dt).days >= 9:
                need_opendata = True
        except Exception:
            pass

    if need_opendata:
        od_map = _load_tdcc_latest_opendata(session)
        if stock_id in od_map:
            latest_item = od_map[stock_id]
            merged[latest_item["date"]] = latest_item

    ordered = [merged[k] for k in sorted(merged.keys())][-12:]
    return ordered


# =====================================================================
# 四、 券商分點明細與全市場前 15 大集中度抓取
# =====================================================================
def _extract_brokers_from_html(html: str) -> list:
    """從 zco.djhtm 解析所有買賣超分點連結"""
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
    """從 zco.djhtm 表格底部解析『合計買超張數』與『合計賣超張數』（前 15 大合計）"""
    soup = BeautifulSoup(html, "html.parser")
    buy_total = None
    sell_total = None

    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True).replace(",", "").replace("+", "") for td in tr.find_all("td")]
        for i, text in enumerate(tds):
            if "合計買超" in text and i + 1 < len(tds):
                v = _to_float_safe(tds[i + 1])
                if v is not None:
                    buy_total = abs(v)
            elif "合計賣超" in text and i + 1 < len(tds):
                v = _to_float_safe(tds[i + 1])
                if v is not None:
                    sell_total = abs(v)

    if buy_total is not None and sell_total is not None:
        return buy_total, sell_total

    b_sum = 0.0
    s_sum = 0.0
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 10:
            left_a = tds[0].find("a", href=True)
            right_a = tds[5].find("a", href=True)
            if left_a and "zco0" in left_a["href"].lower():
                v = _to_float_safe(tds[3].get_text(strip=True))
                if v is not None:
                    b_sum += abs(v)
            if right_a and "zco0" in right_a["href"].lower():
                v = _to_float_safe(tds[8].get_text(strip=True))
                if v is not None:
                    s_sum += abs(v)
    return b_sum, s_sum


def fetch_period_concentration(
    stock_id: str,
    start_date: str,
    end_date: str,
    period_vol: float,
    mirror: str = None,
) -> dict:
    """抓取指定區間的官方前 15 大買賣超合計張數，計算精準籌碼集中度 (%)"""
    session = _create_session()
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)
    html, _, _ = _fetch_dj_html(
        session, f"/z/zc/zco/zco.djhtm?a={stock_id}&e={s_fmt}&f={e_fmt}", mirror, timeout=6
    )
    if not html or period_vol <= 0:
        return {"pct": None, "net": 0, "buy15": 0, "sell15": 0}

    buy15, sell15 = _parse_zco_totals(html)
    net_15 = buy15 - sell15
    pct = round((net_15 / period_vol) * 100.0, 2)
    return {
        "pct": pct,
        "net": int(round(net_15)),
        "buy15": int(round(buy15)),
        "sell15": int(round(sell15)),
    }


def fetch_rolling_concentrations(stock_id: str, df_price: pd.DataFrame, mirror: str = None) -> dict:
    """併發抓取近 5 日、近 10 日、近 20 日的全市場前 15 大籌碼集中度"""
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


def fetch_top_brokers(
    session: requests.Session,
    stock_id: str,
    start_date: str,
    end_date: str,
    mirror: str = None,
    limit: int = 30,
):
    """抓取區間買超與賣超關鍵券商分點"""
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)

    candidate_paths = [
        f"/z/zc/zco/zco.djhtm?a={stock_id}&e={s_fmt}&f={e_fmt}",
        f"/z/zc/zco/zco.djhtm?a={stock_id}&d=60",
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


def fetch_broker_daily_history(
    stock_id: str,
    broker_info: dict,
    start_date: str,
    end_date: str,
    mirror: str,
) -> pd.DataFrame:
    """抓取單一券商分點每日買賣超明細(張)（使用獨立 Session 支援多執行緒併發）"""
    session = _create_session()
    bhid = broker_info["bhid"]
    b_hex = broker_info["b"]
    broker_name = broker_info["name"]
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)

    candidate_paths = []
    if bhid and b_hex:
        candidate_paths.append(
            f"/z/zc/zco/zco0/zco0.djhtm?A={stock_id}&BHID={bhid}&b={b_hex}&C=1&D={s_fmt}&E={e_fmt}"
        )
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
            tds = [td.get_text(strip=True).replace(",", "").replace("+", "") for td in tr.find_all("td")]
            if len(tds) >= 5:
                ad_date = _parse_dj_date(tds[0])
                if ad_date and start_date <= ad_date <= end_date:
                    val = _to_float_safe(tds[-1])
                    if val is not None:
                        records.append({"date": ad_date, broker_name: val})

        if records:
            return pd.DataFrame(records).groupby("date", as_index=False)[broker_name].sum()

    return pd.DataFrame(columns=["date", broker_name])


# =====================================================================
# 五、 輔助指標整理與純官方口徑集中度 Z-Score 計算
# =====================================================================
def _compute_official_concentration_zscores(
    concentrations: dict,
    df_aux_full: pd.DataFrame | None = None,
) -> dict:
    """
    嚴格以「同口徑官方集中度歷史快照」計算 60 日滾動 Z-Score：
    - 僅當 _aux.csv 已累積 >= 15 個交易日的官方前 15 大集中度快照時，才啟用 Z-Score，
      徹底消除「追蹤分點代理均值 vs. 全市場官方現值」混口徑導致的正負號偏移失真。
    """
    z_win = STRATEGY_PARAMS["zscore_window"]
    col_map = {
        "d5": "conc_5d_official",
        "d10": "conc_10d_official",
        "d20": "conc_20d_official",
    }

    out = {}
    for k, aux_col in col_map.items():
        raw = concentrations.get(k, {}) if isinstance(concentrations, dict) else {}
        item = dict(raw) if isinstance(raw, dict) else {"pct": None, "net": 0, "buy15": 0, "sell15": 0}
        item["z_score"] = None
        item["z_samples"] = 0

        if df_aux_full is not None and aux_col in df_aux_full.columns:
            off_s = pd.to_numeric(df_aux_full[aux_col], errors="coerce").dropna()
            item["z_samples"] = int(len(off_s))
            if len(off_s) >= 15:
                item["z_score"] = compute_rolling_zscore(off_s, window=z_win, min_periods=15)

        out[k] = item

    return out


def _extract_aux_metrics(
    df_price_full: pd.DataFrame,
    df_chips_full: pd.DataFrame,
    pure_broker_cols: list[str],
    df_aux_full: pd.DataFrame | None,
    tdcc_history: list | None,
) -> tuple[dict | None, dict | None, dict | None, dict | None]:
    """整理買賣家數差、融資券、借券賣出、TDCC 集保四大輔助指標摘要"""
    broker_diff_info = None
    margin_info = None
    sbl_info = None
    tdcc_info = None

    # 1. 買賣家數差（優先全市場，無則退回追蹤分點）
    diff_series = None
    diff_source = ""
    if df_aux_full is not None and "broker_diff_mkt" in df_aux_full.columns:
        s_mkt = pd.to_numeric(df_aux_full["broker_diff_mkt"], errors="coerce").dropna()
        if len(s_mkt) >= 3:
            diff_series = s_mkt
            diff_source = "全市場"

    if diff_series is None and pure_broker_cols and not df_chips_full.empty:
        sub_mat = df_chips_full[pure_broker_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
        buy_cnt = (sub_mat > 0).sum(axis=1)
        sell_cnt = (sub_mat < 0).sum(axis=1)
        diff_series = (buy_cnt - sell_cnt).astype(float)
        diff_source = f"追蹤{len(pure_broker_cols)}分點"

    if diff_series is not None and not diff_series.empty:
        d1 = int(round(float(diff_series.iloc[-1])))
        d5 = int(round(float(diff_series.tail(5).sum())))
        neg_streak = 0
        for v in reversed(diff_series.tolist()):
            if v < 0:
                neg_streak += 1
            else:
                break
        broker_diff_info = {
            "diff_1d": d1,
            "diff_5d": d5,
            "neg_streak": int(neg_streak),
            "source": diff_source,
        }

    # 2. 融資融券與券資比
    if df_aux_full is not None and "margin_bal" in df_aux_full.columns:
        df_m = df_aux_full.dropna(subset=["margin_bal"]).copy()
        if not df_m.empty:
            latest_bal = float(df_m["margin_bal"].iloc[-1])
            chg_1d = float(df_m["margin_chg"].iloc[-1]) if "margin_chg" in df_m.columns and not pd.isna(df_m["margin_chg"].iloc[-1]) else 0.0
            base_bal = float(df_m["margin_bal"].iloc[-6]) if len(df_m) >= 6 else float(df_m["margin_bal"].iloc[0])
            chg_5d = latest_bal - base_bal
            chg_5d_pct = round((chg_5d / base_bal * 100.0), 2) if base_bal > 0 else 0.0

            short_bal = float(df_m["short_bal"].iloc[-1]) if "short_bal" in df_m.columns and not pd.isna(df_m["short_bal"].iloc[-1]) else 0.0
            short_base = float(df_m["short_bal"].iloc[-6]) if ("short_bal" in df_m.columns and len(df_m) >= 6) else short_bal
            short_chg_5d = short_bal - short_base
            sm_ratio = (
                float(df_m["short_margin_ratio"].iloc[-1])
                if "short_margin_ratio" in df_m.columns and not pd.isna(df_m["short_margin_ratio"].iloc[-1])
                else (round(short_bal / latest_bal * 100.0, 2) if latest_bal > 0 else 0.0)
            )
            margin_info = {
                "latest_bal": int(round(latest_bal)),
                "chg_1d": int(round(chg_1d)),
                "chg_5d": int(round(chg_5d)),
                "chg_5d_pct": float(chg_5d_pct),
                "short_bal": int(round(short_bal)),
                "short_chg_5d": int(round(short_chg_5d)),
                "short_margin_ratio": round(float(sm_ratio), 2),
            }

    # 3. 借券賣出餘額
    if df_aux_full is not None and "sbl_bal" in df_aux_full.columns:
        df_s = df_aux_full.dropna(subset=["sbl_bal"]).copy()
        if not df_s.empty:
            latest_sbl = float(df_s["sbl_bal"].iloc[-1])
            chg_1d = float(df_s["sbl_chg"].iloc[-1]) if "sbl_chg" in df_s.columns and not pd.isna(df_s["sbl_chg"].iloc[-1]) else 0.0
            base_sbl = float(df_s["sbl_bal"].iloc[-6]) if len(df_s) >= 6 else float(df_s["sbl_bal"].iloc[0])
            chg_5d = latest_sbl - base_sbl
            chg_5d_pct = round((chg_5d / base_sbl * 100.0), 2) if base_sbl > 0 else (100.0 if chg_5d > 0 else 0.0)
            sbl_info = {
                "latest_bal": int(round(latest_sbl)),
                "chg_1d": int(round(chg_1d)),
                "chg_5d": int(round(chg_5d)),
                "chg_5d_pct": float(chg_5d_pct),
            }

    # 4. TDCC 集保戶股權分散表
    if tdcc_history and isinstance(tdcc_history, list):
        valid_tdcc = [x for x in tdcc_history if isinstance(x, dict) and x.get("date")]
        valid_tdcc.sort(key=lambda x: x["date"])
        if valid_tdcc:
            cur = valid_tdcc[-1]
            prev = valid_tdcc[-2] if len(valid_tdcc) >= 2 else None
            w1000 = float(cur.get("whale_1000_pct", 0.0))
            w400 = float(cur.get("whale_400_pct", 0.0))
            holders = int(cur.get("total_holders", 0))
            tdcc_info = {
                "date": str(cur["date"]),
                "whale_1000_pct": round(w1000, 2),
                "whale_1000_chg_1w": round(w1000 - float(prev.get("whale_1000_pct", 0.0)), 2) if prev else None,
                "whale_400_pct": round(w400, 2),
                "whale_400_chg_1w": round(w400 - float(prev.get("whale_400_pct", 0.0)), 2) if prev else None,
                "total_holders": holders,
                "holders_chg_1w": int(holders - int(prev.get("total_holders", 0))) if prev else None,
            }

    return broker_diff_info, margin_info, sbl_info, tdcc_info


# =====================================================================
# 六、 核心籌碼診斷分析引擎（滾動 60 日主力＋六大指標共振）
# =====================================================================
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
) -> dict:
    """
    全維度籌碼診斷引擎：
    1. 以滾動 60 日淨買超（自動排除高頻隔日沖）鎖定波段前三大主力。
    2. 投信維持獨立維度並輸出買紅／賣綠分流狀態與持股水位，納入劇本共振層。
    3. 整合純口徑官方集中度 Z-Score、三大主力控盤率 Z-Score、金額防呆、VWAP 合計成本、
       防接刀背離警報、贏家分點勝率、買賣家數差、融資券、借券賣出與 TDCC 集保大戶。
    """
    p = STRATEGY_PARAMS
    all_cols = [c for c in df_chips.columns if c != "date"]
    pure_broker_cols = [c for c in all_cols if c != "投信"]

    empty_resp = {
        "main_force_html": "無分點資料",
        "main_force_sub_lines": [],
        "status_html": "⚪ 無法判定",
        "status_sub": "",
        "divergence_alert": None,
        "scenario": {
            "id": 0, "type": "neutral", "sub_type": "無資料",
            "title": "⚪ 無資料", "desc": "", "action": "",
            "badges": [], "conviction_score": 50,
        },
        "top3_names": [],
        "top3_period_net": 0,
        "top3_period_ctrl_pct": 0.0,
        "top3_5d_ctrl_pct": 0.0,
        "top3_5d_ctrl_z": None,
        "top3_5d_float_pct": None,
        "top3_cost": 0.0,
        "top3_inv": 0,
        "top3_pnl_pct": None,
        "top3_rows": [],
        "filtered_day_traders": [],
        "day_trade_info": {"is_heavy_day_trade": False, "dt_vol_pct": 0.0, "dt_top15_pct": 0.0, "dt_buyers": [], "dt_net_today": 0},
        "winner_brokers": [],
        "recent_10d_rising": {},
        "broker_tags": {},
        "remark_md": "",
        "trust_info": None,
        "broker_diff_info": None,
        "margin_info": None,
        "sbl_info": None,
        "tdcc_info": None,
        "concentrations": concentrations or {},
        "vol_ratio_5_20": None,
        "issued_lots": float(issued_lots or 0.0),
        "top_buyers": {},
        "top_sellers": {},
        "ytd_top_buyers": {},
        "ytd_top_sellers": {},
        "today_buyers": {},
        "today_sellers": {},
    }

    if df_price.empty or (not pure_broker_cols and "投信" not in all_cols):
        return empty_resp

    full_df = df_chips_full if df_chips_full is not None else df_chips
    full_price = df_price_full if df_price_full is not None else df_price
    period_vol = float(df_price["volume"].sum()) if "volume" in df_price.columns else 0.0
    latest_close_full = float(full_price.iloc[-1]["close"])
    wave_days = p["wave_lookback_days"]

    # --- 1. 獨立計算「投信」完整數據（含持股水位與買紅／賣綠動向判定） ---
    trust_info = None
    if "投信" in full_df.columns:
        t_ytd = int(round(float(full_df["投信"].sum())))
        t_period = int(round(float(df_chips["投信"].sum()))) if "投信" in df_chips.columns else 0
        t_d1 = int(round(float(full_df.tail(1)["投信"].sum())))
        t_d5 = int(round(float(full_df.tail(5)["投信"].sum())))
        t_d10 = int(round(float(full_df.tail(10)["投信"].sum())))
        t_d20 = int(round(float(full_df.tail(20)["投信"].sum())))
        t_streak = _calc_streak(full_df["投信"])
        t_ctrl = round((t_period / period_vol * 100.0), 1) if period_vol > 0 else 0.0

        merged_trust = full_price.merge(full_df[["date", "投信"]], on="date", how="left").fillna({"投信": 0.0})
        _, t_cost, t_inv = calculate_main_force_cost(merged_trust, merged_trust["投信"], window_days=wave_days)
        if t_cost <= 0:
            _, t_cost, t_inv = calculate_main_force_cost(merged_trust, merged_trust["投信"])
        t_pnl = round((latest_close_full - t_cost) / t_cost * 100.0, 2) if (t_cost > 0 and t_inv > 0) else None

        t_holding_lots = None
        t_holding_pct = None
        if df_aux_full is not None and "trust_holding" in df_aux_full.columns:
            th_s = pd.to_numeric(df_aux_full["trust_holding"], errors="coerce").dropna()
            if not th_s.empty:
                t_holding_lots = int(round(float(th_s.iloc[-1])))
                if issued_lots and issued_lots > 0:
                    t_holding_pct = round((t_holding_lots / float(issued_lots)) * 100.0, 2)

        trust_info = {
            "ytd": t_ytd,
            "period": t_period,
            "d1": t_d1,
            "d5": t_d5,
            "d10": t_d10,
            "d20": t_d20,
            "streak": t_streak,
            "ctrl_pct": t_ctrl,
            "cost": round(float(t_cost), 2) if (t_cost > 0 and t_inv > 0) else 0.0,
            "inv": int(round(t_inv)) if t_inv > 0 else 0,
            "pnl_pct": t_pnl,
            "holding_lots": t_holding_lots,
            "holding_pct": t_holding_pct,
        }
        trust_info["phase"] = evaluate_trust_phase(trust_info)

    # --- 2. 整理四大輔助籌碼（家數差、融資券、借券、集保） ---
    broker_diff_info, margin_info, sbl_info, tdcc_info = _extract_aux_metrics(
        full_price, full_df, pure_broker_cols, df_aux_full, tdcc_history
    )

    if not pure_broker_cols:
        empty_resp["trust_info"] = trust_info
        empty_resp["broker_diff_info"] = broker_diff_info
        empty_resp["margin_info"] = margin_info
        empty_resp["sbl_info"] = sbl_info
        empty_resp["tdcc_info"] = tdcc_info
        return empty_resp

    # --- 3. 分點行為畫像：隔日沖辨識、贏家分點勝率、滾動 60 日波段主力 ---
    broker_profiles = profile_brokers(full_price, full_df, lookback_days=wave_days)
    top3_names, filtered_dt = select_wave_main_forces(broker_profiles, top_n=3, exclude_day_traders=True)

    # 嚴格同口徑計算官方集中度 Z-Score（未滿 15 日不混算假 Z 值）
    conc_with_z = _compute_official_concentration_zscores(concentrations or {}, df_aux_full)

    # 統計各視窗排名
    total_ytd = full_df[pure_broker_cols].sum().sort_values(ascending=False)
    total_60d = full_df.tail(wave_days)[pure_broker_cols].sum().sort_values(ascending=False)
    period_sum = df_chips[pure_broker_cols].sum()

    recent_1 = full_df.tail(1)[pure_broker_cols].sum()
    recent_1_sorted = recent_1.sort_values(ascending=False)
    today_buyers = recent_1_sorted[recent_1_sorted > 0].head(5)
    today_sellers = recent_1_sorted[recent_1_sorted < 0].tail(5).sort_values(ascending=True)

    recent_5 = full_df.tail(5)[pure_broker_cols].sum()
    recent_10 = full_df.tail(10)[pure_broker_cols].sum()
    recent_20 = full_df.tail(20)[pure_broker_cols].sum()

    top_buyers_60d = total_60d[total_60d > 0].head(5)
    top_sellers_60d = total_60d[total_60d < 0].tail(5).sort_values(ascending=True)
    ytd_buyers = total_ytd[total_ytd > 0].head(5)
    ytd_sellers = total_ytd[total_ytd < 0].tail(5).sort_values(ascending=True)

    rising_10d_sorted = recent_10.sort_values(ascending=False)
    recent_10d_rising = {
        k: int(round(float(v)))
        for k, v in rising_10d_sorted.items()
        if v > 0 and not broker_profiles.get(k, {}).get("is_day_trader", False)
    }
    recent_10d_rising = dict(list(recent_10d_rising.items())[:5])

    winner_brokers = sorted(
        [
            {
                "name": info["name"],
                "win_rate": info["win_rate"],
                "avg_fwd_ret": info["avg_fwd_ret"],
                "buy_signals": info["buy_signals"],
                "net_60d": info["net_60d"],
                "net_5d": info["net_5d"],
                "net_1d": info["net_1d"],
            }
            for info in broker_profiles.values()
            if info["is_winner"]
        ],
        key=lambda x: (x["net_5d"] > 0, x["win_rate"] or 0.0, x["net_60d"]),
        reverse=True,
    )[:5]

    broker_tags = {
        name: info["tags"]
        for name, info in broker_profiles.items()
        if info.get("tags")
    }

    # --- 4. 偵測最新交易日隔日沖集中度風險 ---
    latest_vol = float(full_price.iloc[-1]["volume"]) if "volume" in full_price.columns else 0.0
    dt_today_buyers = []
    dt_today_net = 0.0
    for b_name, b_val in today_buyers.items():
        if broker_profiles.get(b_name, {}).get("is_day_trader", False) and b_val > 0:
            dt_today_buyers.append(b_name)
            dt_today_net += float(b_val)

    dt_vol_pct = round((dt_today_net / latest_vol * 100.0), 1) if latest_vol > 0 else 0.0
    pos_today_top15 = float(recent_1_sorted[recent_1_sorted > 0].head(15).sum())
    dt_top15_pct = round((dt_today_net / pos_today_top15 * 100.0), 1) if pos_today_top15 > 0 else 0.0
    is_heavy_dt = bool(
        dt_today_buyers
        and (
            dt_vol_pct >= p["dt_today_vol_warn_pct"]
            or (dt_top15_pct >= p["dt_today_top15_warn_pct"] and dt_vol_pct >= 2.0)
        )
    )
    day_trade_info = {
        "is_heavy_day_trade": is_heavy_dt,
        "dt_vol_pct": dt_vol_pct,
        "dt_top15_pct": dt_top15_pct,
        "dt_buyers": dt_today_buyers,
        "dt_net_today": int(round(dt_today_net)),
    }

    if not top3_names:
        empty_resp.update({
            "main_force_html": "無波段買超分點主力（近 60 日偏空）",
            "main_force_sub_lines": ["近 60 個交易日追蹤券商分點累計皆為淨賣超"],
            "status_html": "🟢 近 60 日分點空方主導／出貨格局",
            "scenario": {
                "id": 5, "type": "bear", "sub_type": "全線偏空",
                "title": "🟢 當前劇本：近 60 日空方主導",
                "desc": "近 60 個交易日主要券商分點累計皆為淨賣超。",
                "action": "避開空頭排列與無主力承接個股。",
                "badges": [], "conviction_score": 15,
            },
            "filtered_day_traders": filtered_dt,
            "day_trade_info": day_trade_info,
            "winner_brokers": winner_brokers,
            "recent_10d_rising": recent_10d_rising,
            "broker_tags": broker_tags,
            "trust_info": trust_info,
            "broker_diff_info": broker_diff_info,
            "margin_info": margin_info,
            "sbl_info": sbl_info,
            "tdcc_info": tdcc_info,
            "concentrations": conc_with_z,
            "top_sellers": {k: int(round(float(v))) for k, v in top_sellers_60d.items()},
            "ytd_top_buyers": {k: int(round(float(v))) for k, v in ytd_buyers.items()},
            "ytd_top_sellers": {k: int(round(float(v))) for k, v in ytd_sellers.items()},
            "today_buyers": {k: int(round(float(v))) for k, v in today_buyers.items()},
            "today_sellers": {k: int(round(float(v))) for k, v in today_sellers.items()},
        })
        return empty_resp

    # --- 5. 計算近 60 日波段前三大主力合計成本（VWAP）與第一大主力資訊 ---
    primary_player = top3_names[0]
    primary_60d_net = int(round(float(total_60d.get(primary_player, 0.0))))
    primary_ytd_net = int(round(float(total_ytd.get(primary_player, 0.0))))

    merged_all = full_price.merge(full_df[["date"] + top3_names], on="date", how="left").fillna(0.0)
    top3_daily_net = merged_all[top3_names].sum(axis=1)
    _, top3_cost, top3_inv = calculate_main_force_cost(merged_all, top3_daily_net, window_days=wave_days)
    if top3_cost <= 0:
        _, top3_cost, top3_inv = calculate_main_force_cost(merged_all, top3_daily_net)

    _, primary_cost, primary_inv = calculate_main_force_cost(
        merged_all, merged_all[primary_player], window_days=wave_days
    )
    if primary_cost <= 0:
        _, primary_cost, primary_inv = calculate_main_force_cost(merged_all, merged_all[primary_player])

    top3_pnl_pct = (
        round((latest_close_full - top3_cost) / top3_cost * 100.0, 2)
        if (top3_cost > 0 and top3_inv > 0)
        else None
    )

    latest_ma10 = round(float(full_price["close"].tail(10).mean()), 2)
    latest_ma20 = round(float(full_price["close"].tail(20).mean()), 2)

    vol_ratio_5_20 = None
    if "volume" in full_price.columns and len(full_price) >= 10:
        v5 = float(full_price["volume"].tail(5).mean())
        v20 = float(full_price["volume"].tail(20).mean())
        if v20 > 0:
            vol_ratio_5_20 = round(v5 / v20, 2)

    p_tags = " ".join(broker_profiles.get(primary_player, {}).get("tags", []))
    p_tag_html = f' <span style="font-size:0.78rem; color:#fbbf24;">{p_tags}</span>' if p_tags else ""
    main_force_html = f"{primary_player}{p_tag_html}（近60日 {_fmt_html_num(primary_60d_net)}）"

    main_force_sub_lines = [
        f"今年累計 {_fmt_html_num(primary_ytd_net)}"
        + (f" ｜ 單一均價 <b>{primary_cost:,.2f} 元</b>" if primary_cost > 0 else "")
    ]
    if top3_cost > 0 and top3_inv > 0 and top3_pnl_pct is not None:
        main_force_sub_lines.append(
            f"三大主力合計成本：<b style='color:#ffb74d;'>{top3_cost:,.2f} 元</b>（帳面 {_fmt_html_pct(top3_pnl_pct)}）"
        )
    if filtered_dt:
        main_force_sub_lines.append(
            f"<span style='color:#94a3b8;'>⚡ 已自動過濾短沖：{', '.join(filtered_dt[:3])}</span>"
        )

    # --- 6. 近 5 日控盤率、金額防呆、佔股本比率與 60 日滾動 Z-Score ---
    top3_period_net = int(round(float(period_sum[top3_names].sum())))
    top3_period_ctrl_pct = round((top3_period_net / period_vol * 100.0), 1) if period_vol > 0 else 0.0
    top3_5d = int(round(float(recent_5[top3_names].sum())))

    last5_merged = merged_all.tail(5)
    p_col = "vwap" if "vwap" in last5_merged.columns else "close"
    last5_prices = pd.to_numeric(last5_merged[p_col], errors="coerce").fillna(latest_close_full)
    top3_5d_amount_ntd = float((last5_merged[top3_names].sum(axis=1) * last5_prices * 1000.0).sum())

    vol_5d = float(full_price.tail(5)["volume"].sum()) if "volume" in full_price.columns else 0.0
    top3_5d_ctrl_pct = round((top3_5d / vol_5d * 100.0), 2) if vol_5d > 0 else 0.0
    top3_5d_float_pct = (
        round((top3_5d / float(issued_lots)) * 100.0, 2)
        if (issued_lots and issued_lots > 0)
        else None
    )

    # 三大波段主力 5 日控盤率之 60 日滾動 Z-Score（純同口徑計算）
    roll5_net_s = top3_daily_net.rolling(5).sum()
    roll5_vol_s = pd.to_numeric(merged_all["volume"], errors="coerce").rolling(5).sum()
    ctrl_5d_series = (roll5_net_s / roll5_vol_s.replace(0, np.nan)) * 100.0
    top3_5d_ctrl_z = compute_rolling_zscore(ctrl_5d_series, window=p["zscore_window"])

    period_amt_ntd = abs(top3_period_net) * latest_close_full * 1000.0
    min_card_amt = 1_500_000 if len(df_price) <= 1 else p["min_abs_amount_5d_ntd"]

    if is_heavy_dt and len(df_price) <= 1:
        status_html = f"🟡 今日隔日沖買盤過重（佔量 {dt_vol_pct:.1f}%，慎防倒貨）"
    elif top3_period_net > 0 and (top3_period_ctrl_pct >= 1.5 or period_amt_ntd >= min_card_amt):
        status_html = f"🔴 區間內三大波段主力吃貨中（{_fmt_html_num(top3_period_net)}）"
    elif top3_period_net < 0 and (top3_period_ctrl_pct <= -1.5 or period_amt_ntd >= min_card_amt):
        status_html = f"🟢 區間內三大波段主力出貨中（{_fmt_html_num(top3_period_net)}）"
    else:
        status_html = f"🔵 區間內三大波段主力鎖碼觀望（{_fmt_html_num(top3_period_net)}）"

    status_sub = f"近 60 日波段三大主力：{', '.join(top3_names)}"

    price_5d_pct = 0.0
    if len(full_price) >= 5:
        close_now = float(full_price.iloc[-1]["close"])
        close_5d_ago = float(full_price.iloc[-6]["close"]) if len(full_price) >= 6 else float(full_price.iloc[0]["close"])
        price_5d_pct = ((close_now - close_5d_ago) / close_5d_ago * 100.0) if close_5d_ago > 0 else 0.0

    # --- 7. 呼叫 strategy.py 判定背離警報（含防接刀濾網）與五大劇本 ---
    c5_latest_pct = conc_with_z.get("d5", {}).get("pct")
    divergence_alert = evaluate_divergence_alert(
        price_5d_pct=price_5d_pct,
        top3_5d=top3_5d,
        top3_5d_ctrl_pct=top3_5d_ctrl_pct,
        latest_close=latest_close_full,
        top3_5d_amount_ntd=top3_5d_amount_ntd,
        top3_5d_ctrl_z=top3_5d_ctrl_z,
        conc_5d=c5_latest_pct,
        trust_info=trust_info,
        margin_info=margin_info,
        sbl_info=sbl_info,
    )

    scenario = evaluate_chip_scenario(
        top3_5d=top3_5d,
        top3_5d_ctrl_pct=top3_5d_ctrl_pct,
        latest_close=latest_close_full,
        ma10=latest_ma10,
        ma20=latest_ma20,
        top3_cost=top3_cost if top3_inv > 0 else 0.0,
        price_5d_pct=price_5d_pct,
        conc=conc_with_z,
        top3_5d_amount_ntd=top3_5d_amount_ntd,
        top3_5d_ctrl_z=top3_5d_ctrl_z,
        top3_5d_float_pct=top3_5d_float_pct,
        vol_ratio_5_20=vol_ratio_5_20,
        trust_info=trust_info,
        broker_diff_info=broker_diff_info,
        margin_info=margin_info,
        sbl_info=sbl_info,
        tdcc_info=tdcc_info,
        day_trade_info=day_trade_info,
    )

    # --- 8. 整理前三大波段主力明細列 ---
    top3_rows = []
    for name in top3_names:
        b_period = int(round(float(period_sum.get(name, 0.0))))
        ctrl_pct = (b_period / period_vol * 100.0) if period_vol > 0 else 0.0
        streak = _calc_streak(full_df[name])
        prof = broker_profiles.get(name, {})
        top3_rows.append({
            "name": name,
            "net_60d": int(round(float(total_60d.get(name, 0.0)))),
            "ytd": int(round(float(total_ytd.get(name, 0.0)))),
            "period": b_period,
            "d1": int(round(float(recent_1.get(name, 0.0)))),
            "d10": int(round(float(recent_10.get(name, 0.0)))),
            "d20": int(round(float(recent_20.get(name, 0.0)))),
            "streak": streak,
            "ctrl_pct": round(float(ctrl_pct), 1),
            "tags": prof.get("tags", []),
            "win_rate": prof.get("win_rate"),
            "avg_fwd_ret": prof.get("avg_fwd_ret"),
            "churn_ratio": prof.get("churn_ratio", 0.0),
        })

    sorted_period = period_sum.sort_values(ascending=False)
    top_buy_text = (
        f"**{sorted_period.index[0]}**（{_fmt_md_num(int(round(float(sorted_period.iloc[0]))))}）"
        if sorted_period.iloc[0] > 0 else "無"
    )
    top_sell_text = (
        f"**{sorted_period.index[-1]}**（{_fmt_md_num(int(round(float(sorted_period.iloc[-1]))))}）"
        if sorted_period.iloc[-1] < 0 else "無"
    )

    return {
        "main_force_html": main_force_html,
        "main_force_sub_lines": main_force_sub_lines,
        "status_html": status_html,
        "status_sub": status_sub,
        "divergence_alert": divergence_alert,
        "scenario": scenario,
        "top3_names": top3_names,
        "top3_period_net": top3_period_net,
        "top3_period_ctrl_pct": top3_period_ctrl_pct,
        "top3_5d_ctrl_pct": top3_5d_ctrl_pct,
        "top3_5d_ctrl_z": top3_5d_ctrl_z,
        "top3_5d_float_pct": top3_5d_float_pct,
        "top3_cost": round(float(top3_cost), 2) if top3_inv > 0 else 0.0,
        "top3_inv": int(round(top3_inv)) if top3_inv > 0 else 0,
        "top3_pnl_pct": top3_pnl_pct,
        "top3_rows": top3_rows,
        "filtered_day_traders": filtered_dt,
        "day_trade_info": day_trade_info,
        "winner_brokers": winner_brokers,
        "recent_10d_rising": recent_10d_rising,
        "broker_tags": broker_tags,
        "remark_md": f"- 💡 備註：本檢視區間分點買超第一名為 {top_buy_text} ｜ 分點賣超第一名為 {top_sell_text}",
        "trust_info": trust_info,
        "broker_diff_info": broker_diff_info,
        "margin_info": margin_info,
        "sbl_info": sbl_info,
        "tdcc_info": tdcc_info,
        "concentrations": conc_with_z,
        "vol_ratio_5_20": vol_ratio_5_20,
        "issued_lots": float(issued_lots or 0.0),
        "top_buyers": {k: int(round(float(v))) for k, v in top_buyers_60d.items()},
        "top_sellers": {k: int(round(float(v))) for k, v in top_sellers_60d.items()},
        "ytd_top_buyers": {k: int(round(float(v))) for k, v in ytd_buyers.items()},
        "ytd_top_sellers": {k: int(round(float(v))) for k, v in ytd_sellers.items()},
        "today_buyers": {k: int(round(float(v))) for k, v in today_buyers.items()},
        "today_sellers": {k: int(round(float(v))) for k, v in today_sellers.items()},
    }


# =====================================================================
# 七、 GitHub 雲端資料庫同步、排序與刪除管理
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


def _sync_file_to_github(local_path: str, repo_path: str) -> tuple[bool, str]:
    token, repo = _get_github_config()
    if not token or not repo:
        return False, "未讀取到 GITHUB_TOKEN 或 REPO_NAME（請確認 Streamlit Secrets）"

    api_url = f"https://api.github.com/repos/{repo}/contents/{repo_path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    with open(local_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("utf-8")

    sha = None
    get_resp = requests.get(api_url, headers=headers, timeout=10)
    if get_resp.status_code == 200:
        sha = get_resp.json().get("sha")

    payload = {"message": f"Sync {repo_path}", "content": content_b64}
    if sha:
        payload["sha"] = sha

    put_resp = requests.put(api_url, headers=headers, json=payload, timeout=15)
    if put_resp.status_code in (200, 201):
        return True, "已同步至 GitHub database/"
    try:
        err_msg = put_resp.json().get("message", "")
    except Exception:
        err_msg = put_resp.text[:80]
    return False, f"GitHub 寫入失敗 (HTTP {put_resp.status_code}: {err_msg})"


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
    payload = {"message": f"Delete {repo_path} from Streamlit", "sha": sha}
    del_resp = requests.delete(api_url, headers=headers, json=payload, timeout=15)
    if del_resp.status_code in (200, 204):
        return True, "已從 GitHub 刪除"
    return False, f"GitHub 刪除失敗 (HTTP {del_resp.status_code})"


def load_stock_order(existing_ids: list) -> list:
    """讀取自訂排序清單，並與實際存在的個股檔案對齊"""
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
    """儲存自訂排序清單至 database/stock_order.json，並同步到 GitHub"""
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
    """同時刪除本地 database/ 與 GitHub 上的該個股所有資料檔，並更新排序清單"""
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


# =====================================================================
# 八、 資料庫主更新流程（全量校準 ／ 近 3 日增量同步）
# =====================================================================
def update_stock_database(stock_id: str, incremental_days: int = None) -> bool:
    """
    更新指定個股資料並存檔至 database/：
    - 產生/更新 {id}_price.csv、{id}_chips.csv、{id}_aux.csv、{id}_meta.json。
    - incremental_days=None（單檔全量校準）：聯集近 60 日波段前 30 大＋全年度前 30 大＋近 3 日前 20 大分點。
    - incremental_days=3（批量／排程增量更新）：抓最近 3 個交易日分點與輔助籌碼並合併進既有資料庫。
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

    is_incremental = (
        incremental_days is not None
        and incremental_days > 0
        and os.path.exists(price_path)
        and os.path.exists(chips_path)
    )

    df_price_old = pd.DataFrame()
    df_chips_old = pd.DataFrame()
    df_aux_old = pd.DataFrame()
    if is_incremental:
        df_price_old = pd.read_csv(price_path)
        df_chips_old = pd.read_csv(chips_path)
        if os.path.exists(aux_path):
            try:
                df_aux_old = pd.read_csv(aux_path)
            except Exception:
                df_aux_old = pd.DataFrame()
        if df_price_old.empty or df_chips_old.empty:
            is_incremental = False

    if is_incremental:
        lookback_start = (now_tw - timedelta(days=12)).strftime("%Y-%m-%d")
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

    active_suffix = matched_suffix or preferred_suffix or ".TW"
    session = _create_session()
    df_chips_recent = df_price_recent[["date"]].copy()
    debug_notes = []

    # 1. 抓投信買賣超與估計持股
    df_trust, active_mirror, trust_err = fetch_investment_trust_history(
        session, stock_id, fetch_start, end_date
    )
    if not df_trust.empty:
        df_chips_recent = df_chips_recent.merge(df_trust[["date", "投信"]], on="date", how="left")
    else:
        df_chips_recent["投信"] = 0.0
        if trust_err:
            debug_notes.append(f"投信異常: {trust_err}")

    # 2. 抓中文股票名稱與在外發行張數 (issued_lots)
    stock_name = str(saved_meta.get("stock_name", "")).strip() if isinstance(saved_meta, dict) else ""
    if not stock_name:
        stock_name = fetch_stock_name(session, stock_id, active_mirror)

    issued_lots = float(saved_meta.get("issued_lots", 0.0) or 0.0) if isinstance(saved_meta, dict) else 0.0
    if issued_lots <= 0 or not is_incremental:
        fetched_lots = fetch_issued_lots(session, stock_id, active_suffix, active_mirror)
        if fetched_lots > 0:
            issued_lots = fetched_lots

    # 3. 取得關鍵券商分點名單（全量模式聯集：全年30大 ＋ 近60日25大 ＋ 近3日20大）
    brokers, active_mirror, broker_err = fetch_top_brokers(
        session, stock_id, fetch_start, end_date, active_mirror, limit=30
    )
    if broker_err:
        debug_notes.append(f"分點清單異常: {broker_err}")

    if not is_incremental:
        existing_names = {b["name"] for b in brokers}
        extra_windows = []
        if len(df_price_recent) >= 20:
            extra_windows.append((df_price_recent.tail(60)["date"].min(), 25))
        if len(df_price_recent) >= 3:
            extra_windows.append((df_price_recent.tail(3)["date"].min(), 20))
        for w_start, w_lim in extra_windows:
            extra_b, _, _ = fetch_top_brokers(session, stock_id, w_start, end_date, active_mirror, limit=w_lim)
            for rb in extra_b:
                if rb["name"] not in existing_names:
                    brokers.append(rb)
                    existing_names.add(rb["name"])

    # 4. 6 執行緒併發抓取分點每日買賣超明細
    fetched_count = 0
    if brokers:
        with ThreadPoolExecutor(max_workers=6) as executor:
            future_map = {
                executor.submit(fetch_broker_daily_history, stock_id, b, fetch_start, end_date, active_mirror): b
                for b in brokers
            }
            for future in as_completed(future_map):
                b = future_map[future]
                try:
                    df_b = future.result()
                    if not df_b.empty and b["name"] not in df_chips_recent.columns:
                        df_chips_recent = df_chips_recent.merge(df_b, on="date", how="left")
                        fetched_count += 1
                except Exception:
                    continue

    df_chips_recent = df_chips_recent.fillna(0.0)

    # 5. 合併股價與分點主表
    if is_incremental:
        df_price_kept = df_price_old[df_price_old["date"] < fetch_start].copy()
        df_price = pd.concat([df_price_kept, df_price_recent], ignore_index=True).sort_values("date").reset_index(drop=True)

        df_chips_kept = df_chips_old[df_chips_old["date"] < fetch_start].copy()
        df_chips = pd.concat([df_chips_kept, df_chips_recent], ignore_index=True, sort=False).fillna(0.0)
        df_chips = df_chips.sort_values("date").reset_index(drop=True)
        total_brokers = len([c for c in df_chips.columns if c not in ("date", "投信")])
    else:
        df_price = df_price_recent
        df_chips = df_chips_recent
        total_brokers = fetched_count
        if brokers and fetched_count == 0:
            debug_notes.append(f"找到 {len(brokers)} 個分點連結，但明細回傳為空")

    # 6. 併發抓取輔助籌碼（集中度、融資券、借券賣出、全市場家數差、TDCC 集保）
    aux_fetch_start = (
        df_price.tail(max(incremental_days + 7, 10))["date"].min()
        if (is_incremental and not df_aux_old.empty)
        else df_price.tail(90)["date"].min()
    )
    recent_dates_for_sbl = df_price["date"].tail(8).tolist()
    existing_tdcc = saved_meta.get("tdcc_history", []) if isinstance(saved_meta, dict) else []

    with ThreadPoolExecutor(max_workers=5) as ex:
        fut_conc = ex.submit(fetch_rolling_concentrations, stock_id, df_price, active_mirror)
        fut_margin = ex.submit(fetch_margin_history, _create_session(), stock_id, aux_fetch_start, end_date, active_mirror)
        fut_sbl = ex.submit(fetch_sbl_history, _create_session(), stock_id, recent_dates_for_sbl, active_suffix)
        fut_diff = ex.submit(fetch_market_broker_diff, _create_session(), stock_id)
        fut_tdcc = ex.submit(fetch_tdcc_weekly_history, _create_session(), stock_id, existing_tdcc)

        concentrations = fut_conc.result()
        df_margin = fut_margin.result()
        df_sbl = fut_sbl.result()
        df_diff = fut_diff.result()
        tdcc_history = fut_tdcc.result()

    # 7. 組合並合併 _aux.csv（同時累積官方 5/10/20 日集中度歷史快照）
    df_aux_new = df_price[["date"]].tail(max(len(df_margin), len(df_sbl), 15)).copy()
    if not df_trust.empty and "trust_holding" in df_trust.columns:
        df_aux_new = df_aux_new.merge(df_trust[["date", "trust_holding"]], on="date", how="left")
    if not df_margin.empty:
        df_aux_new = df_aux_new.merge(df_margin, on="date", how="left")
    if not df_sbl.empty:
        df_aux_new = df_aux_new.merge(df_sbl, on="date", how="left")
    if not df_diff.empty:
        df_aux_new = df_aux_new.merge(df_diff, on="date", how="left")

    latest_dt = df_price["date"].max()
    for key, col_name in (("d5", "conc_5d_official"), ("d10", "conc_10d_official"), ("d20", "conc_20d_official")):
        c_val = concentrations.get(key, {}).get("pct") if isinstance(concentrations, dict) else None
        if c_val is not None:
            df_aux_new[col_name] = np.nan
            df_aux_new.loc[df_aux_new["date"] == latest_dt, col_name] = float(c_val)

    if not df_aux_old.empty:
        df_aux = (
            pd.concat([df_aux_old, df_aux_new], ignore_index=True, sort=False)
            .sort_values("date")
            .groupby("date", as_index=False)
            .last()
            .sort_values("date")
            .reset_index(drop=True)
        )
    else:
        df_aux = df_aux_new.sort_values("date").reset_index(drop=True)

    # 8. 寫入本地 CSV；若在 GitHub Actions 內執行則交由 workflow 統一 git push
    df_price.to_csv(price_path, index=False, encoding="utf-8-sig")
    df_chips.to_csv(chips_path, index=False, encoding="utf-8-sig")
    df_aux.to_csv(aux_path, index=False, encoding="utf-8-sig")

    is_gha = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    if is_gha:
        ok1 = True
        sync_msg = (
            f"GitHub Actions 自動增量同步（近 {incremental_days} 日）"
            if is_incremental
            else "GitHub Actions 自動同步"
        )
    else:
        ok1, sync_msg = _sync_file_to_github(price_path, f"database/{price_file}")
        if ok1:
            ok1, sync_msg = _sync_file_to_github(chips_path, f"database/{chips_file}")
        if ok1:
            _, sync_msg = _sync_file_to_github(aux_path, f"database/{aux_file}")

    # 9. 產出完整診斷摘要存入 _meta.json
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
    meta["ticker_suffix"] = active_suffix
    meta["issued_lots"] = issued_lots
    meta["broker_count"] = total_brokers
    meta["tdcc_history"] = tdcc_history
    meta["last_updated"] = now_tw.strftime("%Y-%m-%d %H:%M")
    meta["debug_notes"] = debug_notes
    meta["github_sync"] = f"✅ {sync_msg}" if ok1 else f"⚠️ {sync_msg}"

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    if not is_gha and ok1:
        _sync_file_to_github(meta_path, f"database/{meta_file}")

    return True


if __name__ == "__main__":
    existing_stocks = sorted({
        fname.split("_")[0]
        for fname in os.listdir(DB_DIR)
        if fname.endswith("_price.csv")
    })
    print(
        f"[Daily Update] 開始增量更新（近 3 個交易日）共 {len(existing_stocks)} 檔個股：{existing_stocks}",
        flush=True,
    )
    for sid in existing_stocks:
        t0 = time.time()
        ok = update_stock_database(sid, incremental_days=3)
        status = "成功" if ok else "失敗"
        print(f"[Daily Update] {sid} 更新{status}（耗時 {time.time() - t0:.1f} 秒）", flush=True)
