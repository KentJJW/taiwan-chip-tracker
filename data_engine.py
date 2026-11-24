import os
import re
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
    compute_rolling_zscore,
    profile_brokers,
    select_wave_main_forces,
    evaluate_trust_phase,
    evaluate_chip_scenario,
    evaluate_divergence_alert,
    build_z_tooltip_html,  # noqa: F401
)

# 關閉 verify=False 警告與 yfinance 內部的 Failed download 終端機洗版
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

# TDCC 集保全市場 CSV 記憶體快取（批次更新時避免重複下載數 MB 大檔）
_TDCC_CACHE: dict = {"ts": 0.0, "text": ""}
_TDCC_CACHE_TTL = 3600


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
    """同時支援西元年 (2026/11/20) 與民國年 (115/11/20)，統一轉為 YYYY-MM-DD"""
    s = date_str.strip().replace("-", "/")
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


def _get_tw_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Taipei"))
    except Exception:
        return datetime.utcnow() + timedelta(hours=8)


def _to_clean_float(val: str) -> float | None:
    s = str(val).replace(",", "").replace("+", "").replace("%", "").strip()
    if s in ("", "--", "-", "N/A"):
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
    """
    移動加權平均成本法：
    - 優先使用每日真實 VWAP
    - 若 df_price 含有 adj_factor（來自還原權息比率），遇除權息日自動對歷史 avg_cost 做等比例校正
    """
    if "vwap" in df_price.columns and df_price["vwap"].notna().any():
        avg_prices = pd.to_numeric(df_price["vwap"], errors="coerce").fillna(df_price["close"])
    else:
        price_cols = [c for c in ["open", "high", "low", "close"] if c in df_price.columns]
        if price_cols:
            avg_prices = df_price[price_cols].mean(axis=1).fillna(df_price["close"])
        else:
            avg_prices = df_price["close"]

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

        # 偵測除權息日：若 adj_factor 發生跳變（> 0.1%），等比例調整既有庫存成本
        if prev_adj is not None and prev_adj > 0 and cur_adj > 0 and inv > 0 and avg_cost > 0:
            ratio = float(prev_adj) / float(cur_adj)
            if abs(ratio - 1.0) > 0.001:
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
def _fetch_twse_tpex_price(stock_id: str, start_date: str, end_date: str, preferred_suffix: str = None) -> tuple[pd.DataFrame, str]:
    """向證交所 (TWSE) 或櫃買中心 (TPEX) 抓取每日開高低收、成交量(張)與精準成交金額 VWAP"""
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
                        vwap = round(amt_ntd / vol_shares, 2) if (amt_ntd and vol_shares and vol_shares > 0) else round((op + hi + lo + cl) / 4.0, 2)
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
                    vol_k = _to_clean_float(item[1])
                    amt_k = _to_clean_float(item[2])
                    op, hi, lo, cl = _to_clean_float(item[3]), _to_clean_float(item[4]), _to_clean_float(item[5]), _to_clean_float(item[6])
                    if cl is not None:
                        vwap = round(amt_k / vol_k, 2) if (amt_k and vol_k and vol_k > 0) else round((op + hi + lo + cl) / 4.0, 2)
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


def _fetch_yf_price_and_adj(stock_id: str, start_date: str, end_date: str, preferred_suffix: str = None) -> tuple[pd.DataFrame, str]:
    """從 yfinance 抓取股價與還原權息因子 adj_factor (Adj Close / Close)"""
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
                out = out[out["date"] >= start_date].dropna(subset=["close"]).reset_index(drop=True)
                if not out.empty:
                    return out, suffix
        except Exception:
            continue
    return pd.DataFrame(), ""


def fetch_price_history(stock_id: str, start_date: str, end_date: str, preferred_suffix: str = None) -> tuple[pd.DataFrame, str]:
    """
    優先向 TWSE/TPEX 官方取得精準成交金額 VWAP，並結合 yfinance 補齊除權息因子 adj_factor；
    若官方 API 暫時無法連線，自動降級回 yfinance。
    """
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


def fetch_issued_lots(stock_id: str, preferred_suffix: str = None) -> float:
    """透過 yfinance 取得公司流通股數並換算為「已發行張數（千股）」"""
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
# 投信（含真實總持股與持股比率）、券商分點與集中度抓取
# =====================================================================
def fetch_investment_trust_history(session: requests.Session, stock_id: str, start_date: str, end_date: str, mirror: str = None):
    """
    從 MoneyDJ zcl.djhtm 抓取「投信」每日淨買賣超(張)，
    並透過動態表頭解析同步取得「投信真實持股張數 (trust_holding)」與「投信持股比率 (trust_holding_pct)」。
    """
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)
    html, ok_mirror, err = _fetch_dj_html(session, f"/z/zc/zcl/zcl.djhtm?a={stock_id}&c={s_fmt}&d={e_fmt}", mirror)
    if not html:
        return pd.DataFrame(columns=["date", "投信", "trust_holding", "trust_holding_pct"]), mirror, err

    soup = BeautifulSoup(html, "html.parser")

    # 動態偵測欄位索引（預設相容標準 zcl.djhtm 結構）
    idx_trust_net = 2
    idx_trust_hold = None
    idx_trust_pct = None

    for tr in soup.find_all("tr"):
        cells = [c.get_text(strip=True).replace(" ", "") for c in tr.find_all(["th", "td"])]
        if any("日期" in c for c in cells) and any("投信" in c for c in cells):
            for i, txt in enumerate(cells):
                if "投信" in txt and ("買賣超" in txt or "買賣" in txt or txt == "投信"):
                    idx_trust_net = i
                elif "投信" in txt and ("持股" in txt or "庫存" in txt) and ("比" not in txt and "%" not in txt):
                    idx_trust_hold = i
                elif "投信" in txt and ("比" in txt or "%" in txt):
                    idx_trust_pct = i
            break

    records = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) <= idx_trust_net:
            continue
        ad_date = _parse_dj_date(tds[0])
        if not (ad_date and start_date <= ad_date <= end_date):
            continue
        net_val = _to_clean_float(tds[idx_trust_net])
        if net_val is None:
            continue

        hold_val = _to_clean_float(tds[idx_trust_hold]) if (idx_trust_hold is not None and idx_trust_hold < len(tds)) else np.nan
        pct_val = _to_clean_float(tds[idx_trust_pct]) if (idx_trust_pct is not None and idx_trust_pct < len(tds)) else np.nan

        # 數值合理性防呆
        if hold_val is not None and not pd.isna(hold_val) and hold_val < 0:
            hold_val = np.nan
        if pct_val is not None and not pd.isna(pct_val) and not (0.0 <= pct_val <= 100.0):
            pct_val = np.nan

        records.append({
            "date": ad_date,
            "投信": net_val,
            "trust_holding": hold_val if hold_val is not None else np.nan,
            "trust_holding_pct": pct_val if pct_val is not None else np.nan,
        })

    if not records:
        return pd.DataFrame(columns=["date", "投信", "trust_holding", "trust_holding_pct"]), ok_mirror, "投信表格無資料"

    df = pd.DataFrame(records).drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
    return df, ok_mirror, ""


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
        tds = tr.find_all("td")
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
    """抓取指定區間的官方前 15 大買賣超合計張數，計算精準籌碼集中度 (%)"""
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
    """併發抓取最新一日的近 5 日、近 10 日、近 20 日全市場前 15 大籌碼集中度"""
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
    """抓取區間買超與賣超關鍵券商分點"""
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
    """抓取單一券商分點每日買賣超明細(張)"""
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
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
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
# 輔助籌碼抓取：買賣家數差、融資融券、借券賣出、TDCC 集保大戶
# =====================================================================
def fetch_broker_diff_history(session: requests.Session, stock_id: str, start_date: str, end_date: str, mirror: str = None) -> pd.DataFrame:
    """從 MoneyDJ 抓取每日「買超家數 － 賣超家數」"""
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)
    html, _, _ = _fetch_dj_html(session, f"/z/zc/zco/zco2/zco2.djhtm?a={stock_id}&c={s_fmt}&d={e_fmt}", mirror, timeout=6)
    if not html:
        html, _, _ = _fetch_dj_html(session, f"/z/zc/zco/zco2/zco2.djhtm?a={stock_id}", mirror, timeout=6)
    if not html:
        return pd.DataFrame(columns=["date", "broker_diff"])

    soup = BeautifulSoup(html, "html.parser")
    records = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) >= 4:
            ad_date = _parse_dj_date(tds[0])
            if ad_date and start_date <= ad_date <= end_date:
                buy_cnt = _to_clean_float(tds[1])
                sell_cnt = _to_clean_float(tds[2])
                if buy_cnt is not None and sell_cnt is not None:
                    records.append({"date": ad_date, "broker_diff": int(round(buy_cnt - sell_cnt))})

    if not records:
        return pd.DataFrame(columns=["date", "broker_diff"])
    return pd.DataFrame(records).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)


def fetch_margin_history(session: requests.Session, stock_id: str, start_date: str, end_date: str, mirror: str = None) -> pd.DataFrame:
    """從 MoneyDJ 抓取每日融資餘額(張)與融券餘額(張)（動態表頭解析＋數值防呆）"""
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)
    html, _, _ = _fetch_dj_html(session, f"/z/zc/zcn/zcn.djhtm?a={stock_id}&c={s_fmt}&d={e_fmt}", mirror, timeout=6)
    if not html:
        html, _, _ = _fetch_dj_html(session, f"/z/zc/zcn/zcn.djhtm?a={stock_id}", mirror, timeout=6)
    if not html:
        return pd.DataFrame(columns=["date", "margin_bal", "short_bal"])

    soup = BeautifulSoup(html, "html.parser")

    # 動態偵測「餘額」欄位索引（通常表頭會有兩次「餘額」：前者融資餘額、後者融券餘額）
    idx_margin = 4
    idx_short = 11
    for tr in soup.find_all("tr"):
        cells = [c.get_text(strip=True).replace(" ", "") for c in tr.find_all(["th", "td"])]
        if any("餘額" in c for c in cells) and len(cells) >= 10:
            bal_indices = [i for i, txt in enumerate(cells) if "餘額" in txt and "增減" not in txt]
            if len(bal_indices) >= 2:
                idx_margin, idx_short = bal_indices[0], bal_indices[1]
            break

    records = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
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
    return pd.DataFrame(records).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)


def fetch_sbl_history(session: requests.Session, stock_id: str, start_date: str, end_date: str, mirror: str = None) -> pd.DataFrame:
    """從 MoneyDJ 抓取每日借券賣出餘額(張)（動態表頭解析＋數值防呆）"""
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)
    html, _, _ = _fetch_dj_html(session, f"/z/zc/zcn/zcn0.djhtm?a={stock_id}&c={s_fmt}&d={e_fmt}", mirror, timeout=6)
    if not html:
        html, _, _ = _fetch_dj_html(session, f"/z/zc/zcn/zcn0.djhtm?a={stock_id}", mirror, timeout=6)
    if not html:
        return pd.DataFrame(columns=["date", "sbl_bal"])

    soup = BeautifulSoup(html, "html.parser")

    idx_sbl = 6
    for tr in soup.find_all("tr"):
        cells = [c.get_text(strip=True).replace(" ", "") for c in tr.find_all(["th", "td"])]
        if any("日期" in c for c in cells) and any("餘額" in c for c in cells):
            for i, txt in enumerate(cells):
                if "借券" in txt and "餘額" in txt:
                    idx_sbl = i
                    break
            else:
                bal_indices = [i for i, txt in enumerate(cells) if "餘額" in txt]
                if bal_indices:
                    idx_sbl = bal_indices[-1]
            break

    records = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) <= idx_sbl:
            continue
        ad_date = _parse_dj_date(tds[0])
        if ad_date and start_date <= ad_date <= end_date:
            sbl_bal = _to_clean_float(tds[idx_sbl])
            if sbl_bal is not None and sbl_bal >= 0:
                records.append({"date": ad_date, "sbl_bal": sbl_bal})

    if not records:
        return pd.DataFrame(columns=["date", "sbl_bal"])
    return pd.DataFrame(records).drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)


def _get_tdcc_csv_text(session: requests.Session) -> str:
    """單次下載全市場 TDCC 集保 CSV 並快取於記憶體（TTL 1 小時），避免批次更新重複下載大檔"""
    now = time.time()
    if _TDCC_CACHE["text"] and (now - _TDCC_CACHE["ts"]) < _TDCC_CACHE_TTL:
        return _TDCC_CACHE["text"]
    try:
        url = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
        resp = session.get(url, timeout=15, verify=False)
        if resp.status_code == 200 and len(resp.content) > 1000:
            text = resp.content.decode("utf-8-sig", errors="ignore")
            _TDCC_CACHE["ts"] = now
            _TDCC_CACHE["text"] = text
            return text
    except Exception:
        pass
    return _TDCC_CACHE["text"]


def fetch_tdcc_snapshot(session: requests.Session, stock_id: str, existing_history: list = None) -> list:
    """從集保結算所 OpenData 快取解析最新一週集保戶股權分散表，並合併進歷史週序列"""
    history = list(existing_history) if isinstance(existing_history, list) else []
    try:
        text = _get_tdcc_csv_text(session)
        if not text:
            return history
        lines = [line.strip() for line in text.splitlines() if f",{stock_id}," in f",{line},"]
        if not lines:
            return history

        w1000_pct = 0.0
        w400_pct = 0.0
        total_holders = 0
        raw_date = ""

        for line in lines:
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6 or parts[1] != stock_id:
                continue
            raw_date = parts[0]
            try:
                level = int(parts[2])
                holders = int(parts[3])
                pct = float(parts[5])
            except ValueError:
                continue
            if level == 17:
                total_holders = holders
            elif 1 <= level <= 15:
                if level >= 12:
                    w400_pct += pct
                if level == 15:
                    w1000_pct += pct

        if raw_date and len(raw_date) == 8 and (w1000_pct > 0 or w400_pct > 0):
            fmt_dt = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
            entry = {
                "date": fmt_dt,
                "whale_1000_pct": round(w1000_pct, 2),
                "whale_400_pct": round(w400_pct, 2),
                "total_holders": total_holders,
            }
            history = [h for h in history if h.get("date") != fmt_dt]
            history.append(entry)
            history.sort(key=lambda x: x.get("date", ""))
            return history[-24:]
    except Exception:
        pass
    return history


def _populate_aux_concentrations(
    stock_id: str,
    df_price: pd.DataFrame,
    df_aux: pd.DataFrame,
    mirror: str = None,
    only_recent_days: int | None = None,
    max_workers: int = 8,
) -> pd.DataFrame:
    """
    將每日官方前 15 大籌碼集中度 (conc_5d, conc_10d, conc_20d) 補入 df_aux：
    - only_recent_days=None（新個股建檔／單股全年度重抓）：補齊今年所有缺失交易日
    - only_recent_days=3（每日增量更新）：僅補齊最近幾個交易日
    """
    if df_price.empty:
        return df_aux

    df_out = df_price[["date"]].merge(df_aux, on="date", how="left") if not df_aux.empty else pd.DataFrame({"date": df_price["date"]})
    for col in ("conc_5d", "conc_10d", "conc_20d"):
        if col not in df_out.columns:
            df_out[col] = np.nan

    start_idx = max(0, len(df_price) - only_recent_days) if (only_recent_days and only_recent_days > 0) else 0
    windows = [("conc_5d", 5), ("conc_10d", 10), ("conc_20d", 20)]
    tasks = []

    for idx in range(start_idx, len(df_price)):
        end_date = str(df_price.loc[idx, "date"])
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
# 核心分析引擎（單日診斷 ＋ 全年度每日健康分數向量化回算）
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
    """整合滾動 60 日波段主力、投信獨立追蹤（真實持股比）、四大輔助籌碼、Z-Score 與五大劇本"""
    all_cols = [c for c in df_chips.columns if c != "date"]
    pure_broker_cols = [c for c in all_cols if c != "投信"]

    full_df = df_chips_full if df_chips_full is not None else df_chips
    full_price = df_price_full if df_price_full is not None else df_price

    # --- 1. 整理集中度並結合 _aux.csv 歷史序列計算 60 日滾動 Z-Score ---
    conc_out = {}
    raw_conc = concentrations if isinstance(concentrations, dict) else {}
    aux_conc_map = {"d5": "conc_5d", "d10": "conc_10d", "d20": "conc_20d"}
    for k, aux_col in aux_conc_map.items():
        base_item = dict(raw_conc.get(k, {})) if isinstance(raw_conc.get(k), dict) else {}
        z_val = base_item.get("z_score")
        z_samples = int(base_item.get("z_samples", 0) or 0)
        if df_aux_full is not None and not df_aux_full.empty and aux_col in df_aux_full.columns:
            s_clean = pd.to_numeric(df_aux_full[aux_col], errors="coerce").dropna()
            z_samples = len(s_clean)
            if base_item.get("pct") is None and not s_clean.empty:
                base_item["pct"] = round(float(s_clean.iloc[-1]), 2)
            z_calc = compute_rolling_zscore(s_clean, window=STRATEGY_PARAMS["zscore_window"], min_periods=15)
            if z_calc is not None:
                z_val = z_calc
        base_item["z_score"] = z_val
        base_item["z_samples"] = z_samples
        conc_out[k] = base_item

    if df_price.empty or (not pure_broker_cols and "投信" not in all_cols):
        return {
            "main_force_html": "無分點資料",
            "main_force_sub_lines": [],
            "status_html": "⚪ 無法判定",
            "status_sub": "",
            "divergence_alert": None,
            "scenario": {"id": 0, "type": "neutral", "sub_type": "無資料", "title": "⚪ 無資料", "desc": "", "action": "", "badges": [], "conviction_score": 50},
            "concentrations": conc_out,
            "broker_tags": {},
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

    period_vol = float(df_price["volume"].sum()) if "volume" in df_price.columns else 0.0
    latest_close_full = float(full_price.iloc[-1]["close"])
    lb_days = STRATEGY_PARAMS["wave_lookback_days"]

    # --- 2. 投信獨立追蹤（優先採用 _aux.csv 的真實持股比率） ---
    trust_info = None
    if "投信" in full_df.columns:
        t_ytd = int(round(full_df["投信"].sum()))
        t_period = int(round(df_chips["投信"].sum())) if "投信" in df_chips.columns else 0
        t_d1 = int(round(full_df.tail(1)["投信"].sum()))
        t_d5 = int(round(full_df.tail(5)["投信"].sum()))
        t_d10 = int(round(full_df.tail(10)["投信"].sum()))
        t_d20 = int(round(full_df.tail(20)["投信"].sum()))
        t_streak = _calc_streak(full_df["投信"])
        t_ctrl = round((t_period / period_vol * 100.0), 1) if period_vol > 0 else 0.0

        merged_trust = full_price.merge(full_df[["date", "投信"]], on="date", how="left").fillna({"投信": 0.0})
        _, t_cost, t_inv = calculate_main_force_cost(merged_trust, merged_trust["投信"], window_days=lb_days)
        t_pnl = round((latest_close_full - t_cost) / t_cost * 100.0, 2) if (t_cost > 0 and t_inv > 0) else None

        # 優先讀取真實投信持股比率，其次以真實持股張數 ÷ 股本計算，最後才降級回 None（不再誤用 t_ytd）
        holding_pct = None
        if df_aux_full is not None and not df_aux_full.empty:
            if "trust_holding_pct" in df_aux_full.columns:
                s_tpct = pd.to_numeric(df_aux_full["trust_holding_pct"], errors="coerce").dropna()
                if not s_tpct.empty:
                    holding_pct = round(float(s_tpct.iloc[-1]), 2)
            if holding_pct is None and "trust_holding" in df_aux_full.columns and issued_lots > 0:
                s_thold = pd.to_numeric(df_aux_full["trust_holding"], errors="coerce").dropna()
                if not s_thold.empty:
                    holding_pct = round(float(s_thold.iloc[-1]) / issued_lots * 100.0, 2)

        trust_info = {
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

    # --- 3. 整理四大輔助籌碼 ---
    broker_diff_info = None
    margin_info = None
    sbl_info = None
    tdcc_info = None

    if df_aux_full is not None and not df_aux_full.empty:
        if "broker_diff" in df_aux_full.columns and df_aux_full["broker_diff"].notna().any():
            s_diff = pd.to_numeric(df_aux_full["broker_diff"], errors="coerce").dropna()
            if not s_diff.empty:
                d1 = int(round(s_diff.iloc[-1]))
                d5 = int(round(s_diff.tail(5).sum()))
                neg_streak = 0
                for v in reversed(s_diff.tolist()):
                    if v < 0:
                        neg_streak += 1
                    else:
                        break
                broker_diff_info = {"diff_5d": d5, "diff_1d": d1, "neg_streak": neg_streak, "source": "全市場家數差"}
        elif pure_broker_cols:
            daily_b = (full_df[pure_broker_cols] > 0).sum(axis=1)
            daily_s = (full_df[pure_broker_cols] < 0).sum(axis=1)
            s_diff = daily_b - daily_s
            d1 = int(s_diff.iloc[-1])
            d5 = int(s_diff.tail(5).sum())
            neg_streak = 0
            for v in reversed(s_diff.tolist()):
                if v < 0:
                    neg_streak += 1
                else:
                    break
            broker_diff_info = {"diff_5d": d5, "diff_1d": d1, "neg_streak": neg_streak, "source": f"追蹤{len(pure_broker_cols)}分點"}

        if "margin_bal" in df_aux_full.columns and df_aux_full["margin_bal"].notna().any():
            s_m = pd.to_numeric(df_aux_full["margin_bal"], errors="coerce").dropna()
            s_s = pd.to_numeric(df_aux_full["short_bal"], errors="coerce").dropna() if "short_bal" in df_aux_full.columns else pd.Series(dtype=float)
            if not s_m.empty:
                latest_m = int(round(s_m.iloc[-1]))
                base_m = float(s_m.iloc[-6]) if len(s_m) >= 6 else float(s_m.iloc[0])
                chg_5d = int(round(latest_m - base_m))
                chg_5d_pct = round((chg_5d / base_m * 100.0), 2) if base_m > 0 else 0.0
                latest_s = int(round(s_s.iloc[-1])) if not s_s.empty else 0
                sm_ratio = round((latest_s / latest_m * 100.0), 2) if latest_m > 0 else 0.0
                margin_info = {
                    "latest_bal": latest_m,
                    "chg_5d": chg_5d,
                    "chg_5d_pct": chg_5d_pct,
                    "short_bal": latest_s,
                    "short_margin_ratio": sm_ratio,
                }

        if "sbl_bal" in df_aux_full.columns and df_aux_full["sbl_bal"].notna().any():
            s_sbl = pd.to_numeric(df_aux_full["sbl_bal"], errors="coerce").dropna()
            if not s_sbl.empty:
                latest_sbl = int(round(s_sbl.iloc[-1]))
                base_sbl = float(s_sbl.iloc[-6]) if len(s_sbl) >= 6 else float(s_sbl.iloc[0])
                chg_5d = int(round(latest_sbl - base_sbl))
                chg_5d_pct = round((chg_5d / base_sbl * 100.0), 2) if base_sbl > 0 else 0.0
                chg_1d = int(round(latest_sbl - float(s_sbl.iloc[-2]))) if len(s_sbl) >= 2 else 0
                sbl_info = {
                    "latest_bal": latest_sbl,
                    "chg_5d": chg_5d,
                    "chg_5d_pct": chg_5d_pct,
                    "chg_1d": chg_1d,
                }

    if isinstance(tdcc_history, list) and tdcc_history:
        latest_t = tdcc_history[-1]
        prev_t = tdcc_history[-2] if len(tdcc_history) >= 2 else None
        tdcc_info = {
            "date": latest_t.get("date", ""),
            "whale_1000_pct": float(latest_t.get("whale_1000_pct", 0.0)),
            "whale_400_pct": float(latest_t.get("whale_400_pct", 0.0)),
            "total_holders": int(latest_t.get("total_holders", 0)),
            "whale_1000_chg_1w": round(float(latest_t["whale_1000_pct"]) - float(prev_t["whale_1000_pct"]), 2) if prev_t else None,
            "whale_400_chg_1w": round(float(latest_t["whale_400_pct"]) - float(prev_t["whale_400_pct"]), 2) if prev_t else None,
            "holders_chg_1w": int(latest_t["total_holders"]) - int(prev_t["total_holders"]) if prev_t else None,
        }

    # --- 4. 分點行為畫像與滾動 60 日波段主力篩選 ---
    profiles = profile_brokers(full_price, full_df, lookback_days=lb_days)
    broker_tags = {k: v["tags"] for k, v in profiles.items() if v.get("tags")}
    top3_names, filtered_dt = select_wave_main_forces(profiles, top_n=3, exclude_day_traders=True)

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
        return {
            "main_force_html": "無波段買超分點主力（偏空格局）",
            "main_force_sub_lines": ["近 60 日追蹤券商分點累計皆為淨賣超"],
            "status_html": "🟢 空方主導／出貨格局",
            "status_sub": "",
            "divergence_alert": None,
            "scenario": {"id": 5, "type": "bear", "sub_type": "空方主導", "title": "🟢 當前劇本：5. 空方主導", "desc": "近 60 日主要券商分點皆為淨賣超。", "action": "避開空頭排列個股。", "badges": [], "conviction_score": 20},
            "concentrations": conc_out,
            "broker_tags": broker_tags,
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
            "broker_diff_info": broker_diff_info,
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

    # --- 5. 計算最大主力與三大波段主力數據 ---
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
    main_force_sub_lines = [
        f"今年累計 {_fmt_html_num(primary_ytd_net)}{p1_cost_txt}"
    ]
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

    vol_5d = float(full_price.tail(5)["volume"].sum()) if "volume" in full_price.columns else 0.0
    top3_5d_ctrl_pct = round((top3_5d / vol_5d * 100.0), 1) if vol_5d > 0 else 0.0
    top3_5d_float_pct = round((top3_5d / issued_lots * 100.0), 2) if issued_lots > 0 else None

    vwap_col = full_price["vwap"] if "vwap" in full_price.columns else full_price["close"]
    top3_5d_amount_ntd = float((top3_daily_series.tail(5).values * vwap_col.tail(5).values * 1000.0).sum())

    rolling_5d_net = top3_daily_series.rolling(5).sum()
    rolling_5d_vol = full_price["volume"].rolling(5).sum().replace(0, np.nan)
    rolling_5d_ctrl_series = (rolling_5d_net / rolling_5d_vol) * 100.0
    top3_5d_ctrl_z = compute_rolling_zscore(rolling_5d_ctrl_series, window=STRATEGY_PARAMS["zscore_window"], min_periods=15)

    latest_ma10 = round(float(full_price["close"].tail(10).mean()), 2)
    latest_ma20 = round(float(full_price["close"].tail(20).mean()), 2)
    vol_20d_avg = float(full_price["volume"].tail(20).mean()) if "volume" in full_price.columns else 0.0
    vol_5d_avg = float(full_price["volume"].tail(5).mean()) if "volume" in full_price.columns else 0.0
    vol_ratio_5_20 = round(vol_5d_avg / vol_20d_avg, 2) if vol_20d_avg > 0 else None

    price_5d_pct = 0.0
    if len(full_price) >= 5:
        close_now = float(full_price.iloc[-1]["close"])
        close_5d_ago = float(full_price.iloc[-6]["close"]) if len(full_price) >= 6 else float(full_price.iloc[0]["close"])
        price_5d_pct = ((close_now - close_5d_ago) / close_5d_ago * 100.0) if close_5d_ago > 0 else 0.0

    # --- 6. 呼叫 strategy.py 判定背離警報與五大劇本 ---
    c5_pct = conc_out.get("d5", {}).get("pct")
    divergence_alert = evaluate_divergence_alert(
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
    )

    scenario = evaluate_chip_scenario(
        top3_5d=top3_5d,
        top3_5d_ctrl_pct=top3_5d_ctrl_pct,
        latest_close=latest_close_full,
        ma10=latest_ma10,
        ma20=latest_ma20,
        top3_cost=top3_cost if top3_inv > 0 else 0.0,
        price_5d_pct=price_5d_pct,
        conc=conc_out,
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

    # --- 7. 前三大波段主力明細列 ---
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
        "main_force_html": main_force_html,
        "main_force_sub_lines": main_force_sub_lines,
        "status_html": status_html,
        "status_sub": status_sub,
        "divergence_alert": divergence_alert,
        "scenario": scenario,
        "concentrations": conc_out,
        "broker_tags": broker_tags,
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
        "broker_diff_info": broker_diff_info,
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
    向量化回算今年每一個交易日的「綜合籌碼健康分數 (0~100)」與對應劇本：
    - 預先向量化計算 5 日成交量、各分點 5 日滾動淨額與投信真實持股比率，大幅加速回算
    - 成本計算同步套用除權息因子 adj_factor
    """
    if df_price_full is None or df_price_full.empty or df_chips_full is None or df_chips_full.empty:
        return pd.DataFrame(columns=["date", "health_score", "scenario_id", "sub_type"])

    df_p = df_price_full.sort_values("date").reset_index(drop=True)
    df_c = df_p[["date"]].merge(df_chips_full, on="date", how="left").fillna(0.0)
    df_a = (
        df_p[["date"]].merge(df_aux_full, on="date", how="left")
        if (df_aux_full is not None and not df_aux_full.empty)
        else pd.DataFrame({"date": df_p["date"]})
    )

    n = len(df_p)
    if n < min_warmup_days:
        return pd.DataFrame(columns=["date", "health_score", "scenario_id", "sub_type"])

    pure_cols = [c for c in df_c.columns if c not in ("date", "投信")]
    if not pure_cols:
        return pd.DataFrame(columns=["date", "health_score", "scenario_id", "sub_type"])

    wave_cols = [c for c in pure_cols if c not in DAY_TRADE_BROKERS] or pure_cols
    chips_mat = df_c[wave_cols].to_numpy(dtype=float)

    # 預計算各分點 5 日滾動合計矩陣（消除內層逐日切片加總迴圈）
    chips_5d_mat = pd.DataFrame(chips_mat).rolling(5, min_periods=1).sum().to_numpy(dtype=float)

    close_arr = pd.to_numeric(df_p["close"], errors="coerce").to_numpy(dtype=float)
    vol_arr = pd.to_numeric(df_p["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    vol5_sum_arr = pd.Series(vol_arr).rolling(5, min_periods=1).sum().to_numpy(dtype=float)
    vwap_arr = (
        pd.to_numeric(df_p["vwap"], errors="coerce").fillna(df_p["close"]).to_numpy(dtype=float)
        if "vwap" in df_p.columns
        else close_arr
    )
    adj_arr = (
        pd.to_numeric(df_p["adj_factor"], errors="coerce").ffill().bfill().fillna(1.0).to_numpy(dtype=float)
        if "adj_factor" in df_p.columns
        else np.ones(n, dtype=float)
    )
    ma10_arr = pd.Series(close_arr).rolling(10).mean().to_numpy(dtype=float)
    ma20_arr = pd.Series(close_arr).rolling(20).mean().to_numpy(dtype=float)
    vol5_arr = pd.Series(vol_arr).rolling(5).mean().to_numpy(dtype=float)
    vol20_arr = pd.Series(vol_arr).rolling(20).mean().to_numpy(dtype=float)

    trust_arr = pd.to_numeric(df_c["投信"], errors="coerce").fillna(0.0).to_numpy(dtype=float) if "投信" in df_c.columns else np.zeros(n)
    trust_5d_arr = pd.Series(trust_arr).rolling(5, min_periods=1).sum().to_numpy(dtype=float)
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
    diff_arr = pd.to_numeric(df_a["broker_diff"], errors="coerce").to_numpy(dtype=float) if "broker_diff" in df_a.columns else np.full(n, np.nan)
    m_arr = pd.to_numeric(df_a["margin_bal"], errors="coerce").to_numpy(dtype=float) if "margin_bal" in df_a.columns else np.full(n, np.nan)
    s_arr = pd.to_numeric(df_a["short_bal"], errors="coerce").to_numpy(dtype=float) if "short_bal" in df_a.columns else np.full(n, np.nan)
    sbl_arr = pd.to_numeric(df_a["sbl_bal"], errors="coerce").to_numpy(dtype=float) if "sbl_bal" in df_a.columns else np.full(n, np.nan)

    lb = STRATEGY_PARAMS["wave_lookback_days"]
    z_win = STRATEGY_PARAMS["zscore_window"]
    tdcc_list = sorted(tdcc_history, key=lambda x: x.get("date", "")) if isinstance(tdcc_history, list) else []

    # 預計算每日投信連買／連賣天數
    trust_streak_arr = np.zeros(n, dtype=int)
    cur_s = 0
    for i in range(n):
        v = trust_arr[i]
        if v > 0:
            cur_s = cur_s + 1 if cur_s > 0 else 1
        elif v < 0:
            cur_s = cur_s - 1 if cur_s < 0 else -1
        else:
            cur_s = 0
        trust_streak_arr[i] = cur_s

    records = []
    for i in range(n):
        dt_str = str(df_p.loc[i, "date"])
        if i + 1 < min_warmup_days or np.isnan(c5_arr[i]) or np.isnan(c10_arr[i]) or np.isnan(c20_arr[i]):
            records.append({"date": dt_str, "health_score": np.nan, "scenario_id": 0, "sub_type": ""})
            continue

        # 1. 當日滾動 60 日三大波段主力
        w_start = max(0, i - lb + 1)
        sum_60 = chips_mat[w_start : i + 1].sum(axis=0)
        pos_idx = np.where(sum_60 > 0)[0]
        if len(pos_idx) > 0:
            top3_idx = pos_idx[np.argsort(sum_60[pos_idx])[::-1][:3]]
            top3_daily = chips_mat[:, top3_idx].sum(axis=1)
            top3_5d_series = chips_5d_mat[:, top3_idx].sum(axis=1)
        else:
            top3_daily = np.zeros(n)
            top3_5d_series = np.zeros(n)

        s5_start = max(0, i - 4)
        top3_5d = int(round(top3_5d_series[i]))
        vol_5d = float(vol5_sum_arr[i])
        top3_5d_ctrl_pct = round((top3_5d / vol_5d * 100.0), 1) if vol_5d > 0 else 0.0
        top3_5d_amt = float((top3_daily[s5_start : i + 1] * vwap_arr[s5_start : i + 1] * 1000.0).sum())
        top3_5d_flt = round((top3_5d / issued_lots * 100.0), 2) if issued_lots > 0 else None

        # 向量化計算三大主力控盤率 60 日 Z-Score
        top3_5d_z = None
        if i >= 14:
            z_s = max(4, i - z_win + 1)
            v5_slice = vol5_sum_arr[z_s : i + 1]
            valid_v = v5_slice > 0
            if int(valid_v.sum()) >= 15:
                ctrl_hist = (top3_5d_series[z_s : i + 1][valid_v] / v5_slice[valid_v]) * 100.0
                std_c = float(np.std(ctrl_hist))
                top3_5d_z = round((float(ctrl_hist[-1]) - float(np.mean(ctrl_hist))) / std_c, 2) if std_c > 1e-9 else 0.0

        c5_hist = c5_arr[max(0, i - z_win + 1) : i + 1]
        c5_hist = c5_hist[~np.isnan(c5_hist)]
        c5_z = None
        if len(c5_hist) >= 15:
            std_5 = float(np.std(c5_hist))
            c5_z = round((float(c5_hist[-1]) - float(np.mean(c5_hist))) / std_5, 2) if std_5 > 1e-9 else 0.0

        # 三大主力滾動 60 日移動加權成本（含除權息校正）
        inv = 0.0
        avg_c = 0.0
        prev_a = None
        for p_val, net_val, a_val in zip(vwap_arr[w_start : i + 1], top3_daily[w_start : i + 1], adj_arr[w_start : i + 1]):
            if prev_a is not None and prev_a > 0 and a_val > 0 and inv > 0 and avg_c > 0:
                ratio = prev_a / a_val
                if abs(ratio - 1.0) > 0.001:
                    avg_c *= ratio
            prev_a = a_val if a_val > 0 else prev_a
            if net_val > 0:
                avg_c = (inv * avg_c + net_val * p_val) / (inv + net_val) if inv > 0 else p_val
                inv += net_val
            elif net_val < 0:
                inv += net_val
                if inv <= 0:
                    inv = 0.0
                    avg_c = 0.0
        top3_cost_i = round(avg_c, 2) if inv > 0 else 0.0

        # 2. 投信當日狀態（採用真實持股比率）
        t_hold_i = None
        if not np.isnan(trust_pct_arr[i]):
            t_hold_i = round(float(trust_pct_arr[i]), 2)
        elif not np.isnan(trust_hold_arr[i]) and issued_lots > 0:
            t_hold_i = round(float(trust_hold_arr[i]) / issued_lots * 100.0, 2)

        t_info_i = {
            "streak": int(trust_streak_arr[i]),
            "d5": int(round(trust_5d_arr[i])),
            "cost": 0.0,
            "holding_pct": t_hold_i,
        }

        # 3. 輔助籌碼當日狀態
        bd_i = None
        if not np.isnan(diff_arr[i]):
            sub_d = diff_arr[s5_start : i + 1]
            sub_d = sub_d[~np.isnan(sub_d)]
            bd_i = {"diff_5d": int(round(sub_d.sum())), "neg_streak": 1 if diff_arr[i] < 0 else 0}

        mg_i = None
        if not np.isnan(m_arr[i]) and i >= 5 and not np.isnan(m_arr[i - 5]) and m_arr[i - 5] > 0:
            chg_m = int(round(m_arr[i] - m_arr[i - 5]))
            chg_m_pct = round(chg_m / m_arr[i - 5] * 100.0, 2)
            sm_r = round(s_arr[i] / m_arr[i] * 100.0, 2) if (not np.isnan(s_arr[i]) and m_arr[i] > 0) else 0.0
            mg_i = {"chg_5d": chg_m, "chg_5d_pct": chg_m_pct, "short_margin_ratio": sm_r}

        sbl_i = None
        if not np.isnan(sbl_arr[i]) and i >= 5 and not np.isnan(sbl_arr[i - 5]) and sbl_arr[i - 5] > 0:
            chg_sbl = int(round(sbl_arr[i] - sbl_arr[i - 5]))
            chg_sbl_pct = round(chg_sbl / sbl_arr[i - 5] * 100.0, 2)
            sbl_i = {"chg_5d": chg_sbl, "chg_5d_pct": chg_sbl_pct}

        tdcc_i = None
        if tdcc_list:
            past_t = [t for t in tdcc_list if t.get("date", "") <= dt_str]
            if len(past_t) >= 2:
                lt, pt = past_t[-1], past_t[-2]
                tdcc_i = {
                    "whale_1000_chg_1w": round(float(lt["whale_1000_pct"]) - float(pt["whale_1000_pct"]), 2),
                    "whale_400_chg_1w": round(float(lt["whale_400_pct"]) - float(pt["whale_400_pct"]), 2),
                    "holders_chg_1w": int(lt["total_holders"]) - int(pt["total_holders"]),
                }

        p5_pct = round((close_arr[i] - close_arr[i - 5]) / close_arr[i - 5] * 100.0, 2) if (i >= 5 and close_arr[i - 5] > 0) else 0.0
        v_rat = round(vol5_arr[i] / vol20_arr[i], 2) if (not np.isnan(vol20_arr[i]) and vol20_arr[i] > 0) else None
        conc_i = {
            "d5": {"pct": round(float(c5_arr[i]), 2), "z_score": c5_z},
            "d10": {"pct": round(float(c10_arr[i]), 2)},
            "d20": {"pct": round(float(c20_arr[i]), 2)},
        }

        sc = evaluate_chip_scenario(
            top3_5d=top3_5d,
            top3_5d_ctrl_pct=top3_5d_ctrl_pct,
            latest_close=float(close_arr[i]),
            ma10=float(ma10_arr[i]) if not np.isnan(ma10_arr[i]) else 0.0,
            ma20=float(ma20_arr[i]) if not np.isnan(ma20_arr[i]) else 0.0,
            top3_cost=top3_cost_i,
            price_5d_pct=p5_pct,
            conc=conc_i,
            top3_5d_amount_ntd=top3_5d_amt,
            top3_5d_ctrl_z=top3_5d_z,
            top3_5d_float_pct=top3_5d_flt,
            vol_ratio_5_20=v_rat,
            trust_info=t_info_i,
            broker_diff_info=bd_i,
            margin_info=mg_i,
            sbl_info=sbl_i,
            tdcc_info=tdcc_i,
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
    """
    透過 GitHub Git Data API（Blobs -> Tree -> Commit -> Ref）將多個檔案打包成單一 Commit 推送，
    大幅降低 API 次數與 Rate Limit 風險。
    file_pairs: [(local_path, repo_path), ...]
    """
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
        # 1. 取得預設分支名稱
        repo_resp = requests.get(base_api, headers=headers, timeout=10)
        if repo_resp.status_code != 200:
            return False, f"讀取儲存庫失敗 (HTTP {repo_resp.status_code})"
        default_branch = repo_resp.json().get("default_branch", "main")

        # 2. 取得目前 HEAD commit 與 base_tree
        ref_resp = requests.get(f"{base_api}/git/ref/heads/{default_branch}", headers=headers, timeout=10)
        if ref_resp.status_code != 200:
            return False, f"讀取分支 {default_branch} 失敗 (HTTP {ref_resp.status_code})"
        head_sha = ref_resp.json()["object"]["sha"]

        commit_resp = requests.get(f"{base_api}/git/commits/{head_sha}", headers=headers, timeout=10)
        if commit_resp.status_code != 200:
            return False, f"讀取 HEAD Commit 失敗 (HTTP {commit_resp.status_code})"
        base_tree_sha = commit_resp.json()["tree"]["sha"]

        # 3. 逐檔建立 Blob 並組裝 Tree
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

        # 4. 建立新 Tree
        tree_resp = requests.post(
            f"{base_api}/git/trees",
            headers=headers,
            json={"base_tree": base_tree_sha, "tree": tree_items},
            timeout=15,
        )
        if tree_resp.status_code != 201:
            return False, f"建立 Tree 失敗 (HTTP {tree_resp.status_code})"
        new_tree_sha = tree_resp.json()["sha"]

        # 5. 建立新 Commit
        new_commit_resp = requests.post(
            f"{base_api}/git/commits",
            headers=headers,
            json={"message": commit_message, "tree": new_tree_sha, "parents": [head_sha]},
            timeout=15,
        )
        if new_commit_resp.status_code != 201:
            return False, f"建立 Commit 失敗 (HTTP {new_commit_resp.status_code})"
        new_commit_sha = new_commit_resp.json()["sha"]

        # 6. 更新分支指標
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
    """透過 GitHub REST API 刪除儲存庫內的單一檔案"""
    token, repo = _get_github_config()
    if not token or not repo:
        return False, "未設定 GITHUB_TOKEN 或 REPO_NAME"

    api_url = f"https://api.github.com/repos/{repo}/contents/{repo_path}"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json"
    }

    get_resp = requests.get(api_url, headers=headers, timeout=10)
    if get_resp.status_code == 404:
        return True, "檔案已不存在"
    if get_resp.status_code != 200:
        return False, f"查詢失敗 (HTTP {get_resp.status_code})"

    sha = get_resp.json().get("sha")
    payload = {
        "message": f"Delete {repo_path} from Streamlit",
        "sha": sha
    }
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


def update_stock_database(stock_id: str, incremental_days: int = None, sync_github: bool = True) -> bool:
    """
    更新指定個股資料並存檔至 database/：
    - incremental_days=None（新增建檔／全年度重抓）：抓全年真實 VWAP、除權息因子、投信(含真實持股比)、分點、輔助籌碼與歷史集中度。
    - incremental_days=3（全部更新／每日排程）：僅增量抓近 3 日資料，並合併「既有追蹤分點 ∪ 近 3 日前 30 大」避免舊主力被填 0。
    - sync_github=False 時僅寫入本地，供 app.py「全部更新」結束後統一單次打包 Commit。
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

    if is_incremental:
        df_price_old = pd.read_csv(price_path)
        df_chips_old = pd.read_csv(chips_path)
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

    session = _create_session()
    df_chips_recent = df_price_recent[["date"]].copy()
    df_aux_recent = df_price_recent[["date"]].copy()
    debug_notes = []

    # 1. 抓投信（含真實總持股與持股比率）
    df_trust, active_mirror, trust_err = fetch_investment_trust_history(session, stock_id, fetch_start, end_date)
    if not df_trust.empty:
        df_chips_recent = df_chips_recent.merge(df_trust[["date", "投信"]], on="date", how="left")
        trust_aux_cols = [c for c in ["date", "trust_holding", "trust_holding_pct"] if c in df_trust.columns]
        if len(trust_aux_cols) > 1:
            df_aux_recent = df_aux_recent.merge(df_trust[trust_aux_cols], on="date", how="left")
    else:
        df_chips_recent["投信"] = 0.0
        if trust_err:
            debug_notes.append(f"投信異常: {trust_err}")

    # 2. 抓中文股票名稱與已發行張數
    stock_name = str(saved_meta.get("stock_name", "")).strip() if isinstance(saved_meta, dict) else ""
    if not stock_name:
        stock_name = fetch_stock_name(session, stock_id, active_mirror)

    issued_lots = float(saved_meta.get("issued_lots", 0.0) or 0.0) if isinstance(saved_meta, dict) else 0.0
    if issued_lots <= 0:
        issued_lots = fetch_issued_lots(stock_id, matched_suffix or preferred_suffix)

    # 3. 取得關鍵券商分點名單（增量更新時合併既有追蹤分點，防止舊主力被填 0）
    brokers, active_mirror, broker_err = fetch_top_brokers(session, stock_id, fetch_start, end_date, active_mirror, limit=30)
    if broker_err:
        debug_notes.append(f"分點清單異常: {broker_err}")

    merged_broker_map = {}
    if isinstance(saved_tracked_brokers, dict) and is_incremental:
        for b_name, b_info in saved_tracked_brokers.items():
            if isinstance(b_info, dict) and (b_info.get("bhid") or b_info.get("b")):
                merged_broker_map[b_name] = {
                    "name": b_name,
                    "bhid": str(b_info.get("bhid", "")),
                    "b": str(b_info.get("b", "")),
                }

    for b in brokers:
        merged_broker_map[b["name"]] = b

    if not is_incremental and len(df_price_recent) >= 3:
        recent_3d_start = df_price_recent.tail(3)["date"].min()
        recent_brokers, _, _ = fetch_top_brokers(session, stock_id, recent_3d_start, end_date, active_mirror, limit=20)
        for rb in recent_brokers:
            merged_broker_map[rb["name"]] = rb

    target_brokers = list(merged_broker_map.values())

    # 4. 併發抓取分點每日買賣超明細
    fetched_count = 0
    if target_brokers:
        with ThreadPoolExecutor(max_workers=6) as executor:
            future_map = {
                executor.submit(fetch_broker_daily_history, stock_id, b, fetch_start, end_date, active_mirror): b
                for b in target_brokers
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

    # 5. 抓取輔助籌碼（家數差、融資券、借券賣出）
    df_diff = fetch_broker_diff_history(session, stock_id, fetch_start, end_date, active_mirror)
    if not df_diff.empty:
        df_aux_recent = df_aux_recent.merge(df_diff, on="date", how="left")
    df_margin = fetch_margin_history(session, stock_id, fetch_start, end_date, active_mirror)
    if not df_margin.empty:
        df_aux_recent = df_aux_recent.merge(df_margin, on="date", how="left")
    df_sbl = fetch_sbl_history(session, stock_id, fetch_start, end_date, active_mirror)
    if not df_sbl.empty:
        df_aux_recent = df_aux_recent.merge(df_sbl, on="date", how="left")

    # 6. 合併或覆蓋資料庫
    if is_incremental:
        df_price_kept = df_price_old[df_price_old["date"] < fetch_start].copy()
        df_price = pd.concat([df_price_kept, df_price_recent], ignore_index=True, sort=False).sort_values("date").reset_index(drop=True)
        if "adj_factor" in df_price.columns:
            df_price["adj_factor"] = pd.to_numeric(df_price["adj_factor"], errors="coerce").ffill().bfill().fillna(1.0)

        df_chips_kept = df_chips_old[df_chips_old["date"] < fetch_start].copy()
        df_chips = pd.concat([df_chips_kept, df_chips_recent], ignore_index=True, sort=False).fillna(0.0)
        df_chips = df_chips.sort_values("date").reset_index(drop=True)
        total_brokers = len([c for c in df_chips.columns if c not in ("date", "投信")])

        if os.path.exists(aux_path):
            df_aux_old = pd.read_csv(aux_path)
            df_aux_kept = df_aux_old[df_aux_old["date"] < fetch_start].copy()
            df_aux = pd.concat([df_aux_kept, df_aux_recent], ignore_index=True, sort=False).sort_values("date").reset_index(drop=True)
        else:
            df_aux = df_aux_recent
    else:
        df_price = df_price_recent
        df_chips = df_chips_recent
        total_brokers = fetched_count
        if os.path.exists(aux_path):
            df_aux_prev = pd.read_csv(aux_path)
            keep_conc_cols = [c for c in ["date", "conc_5d", "conc_10d", "conc_20d"] if c in df_aux_prev.columns]
            if len(keep_conc_cols) > 1:
                df_aux = df_aux_recent.merge(df_aux_prev[keep_conc_cols], on="date", how="left")
            else:
                df_aux = df_aux_recent
        else:
            df_aux = df_aux_recent

        if target_brokers and fetched_count == 0:
            debug_notes.append(f"找到 {len(target_brokers)} 個分點連結，但明細回傳為空")

    # 7. 補齊 _aux.csv 中的官方每日集中度
    df_aux = _populate_aux_concentrations(
        stock_id=stock_id,
        df_price=df_price,
        df_aux=df_aux,
        mirror=active_mirror,
        only_recent_days=(incremental_days if is_incremental else None),
        max_workers=8,
    )

    # 8. 抓取最新 5 / 10 / 20 日全市場前 15 大精準籌碼集中度並寫入 _aux.csv 最後一列
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
    meta["ticker_suffix"] = matched_suffix or preferred_suffix or ""
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
            f"✅ GitHub Actions 自動增量同步（近 {incremental_days} 日）"
            if is_incremental
            else "✅ GitHub Actions 自動同步"
        )
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    elif sync_github:
        meta["github_sync"] = "✅ 準備打包同步至 GitHub database/"
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
        meta["github_sync"] = f"✅ {sync_msg}" if ok_sync else f"⚠️ {sync_msg}"
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
    print(f"[Daily Update] 開始增量更新（近 3 個交易日）共 {len(existing_stocks)} 檔個股：{existing_stocks}", flush=True)
    for sid in existing_stocks:
        t0 = time.time()
        ok = update_stock_database(sid, incremental_days=3)
        status = "成功" if ok else "失敗"
        print(f"[Daily Update] {sid} 更新{status}（耗時 {time.time() - t0:.1f} 秒）", flush=True)
