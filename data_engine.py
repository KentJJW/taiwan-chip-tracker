import os
import re
import ssl
import json
import time
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
import urllib3
import requests
from requests.adapters import HTTPAdapter
import pandas as pd
import yfinance as yf
from bs4 import BeautifulSoup

# 關閉 verify=False 產生的大量 InsecureRequestWarning 日誌洗版
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

DAY_TRADE_BROKERS = {
    "凱基-台北", "元大-土城永寧", "富邦-嘉義", "群益金鼎-大安",
    "國票-敦北法人", "統一-內湖", "永豐金-松山", "兆豐-大同"
}

SCENARIO_MATRIX = [
    {
        "id": 1,
        "name": "1. 雙重共振（主升段）",
        "type": "bull",
        "main_cond": "股價站穩庫存均價與 10MA 之上，且近 5 日三大主力控盤率 ≥ +2.0%",
        "conc_cond": "5 / 10 / 20 日集中度皆為正，且呈現遞增加速（短 > 長）",
        "action": "最強多頭格局。波段大戶與短線資金同步鎖碼，沿 10MA 或主力成本線偏多操作。"
    },
    {
        "id": 2,
        "name": "2. 換手接棒（藏分點）",
        "type": "bull",
        "main_cond": "收盤價站上 10MA（濾除空頭急跌），且近 5 日三大主力控盤率 ≤ +1.5%",
        "conc_cond": "20 日偏低或負值，但 5 日、10 日集中度翻正並飆高（≥ +5.0%）",
        "action": "隱形起漲／新莊家進場。主力改用其他分點吃貨或新大戶接手，留意「本區間買超第一名」動向。"
    },
    {
        "id": 3,
        "name": "3. 高檔派發（假突破）",
        "type": "bear",
        "main_cond": "近 5 日股價上漲 ≥ +2.0%，但三大主力近 5 日控盤率 ≤ -2.0%",
        "conc_cond": "20 日仍為正值，但 5 日集中度率先轉負（≤ -3.0%，籌碼由集中轉發散）",
        "action": "高檔出貨警訊。波段大戶趁散戶追價倒貨，慎防假突破與拉高套牢風險。"
    },
    {
        "id": 4,
        "name": "4. 成本護盤（真洗盤）",
        "type": "bull",
        "main_cond": "股價回測均價 +2% 內或微幅跌破，近 5 日下跌但三大主力控盤率 ≥ +1.5%",
        "conc_cond": "近 5 日集中度維持 ≥ +3.0%（急跌時籌碼反而向大戶集中）",
        "action": "主力刻意洗盤。大戶不願跌破自身成本區，趁恐慌承接浮額，止跌回穩後易展開反彈。"
    },
    {
        "id": 5,
        "name": "5. 主力棄守（真破線）",
        "type": "bear",
        "main_cond": "股價跌破主力均價逾 2%，且近 5 日三大主力控盤率 ≤ -3.0%",
        "conc_cond": "5 日（≤ -5.0%）、10 日（≤ -3.0%）集中度同步明顯為負",
        "action": "多殺多棄守格局。連波段最大主力都在停損結帳，下方無大資金接手，切忌盲目攤平。"
    }
]


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


def calculate_main_force_cost(df_price: pd.DataFrame, daily_net: pd.Series) -> tuple[pd.Series, float, float]:
    """移動加權平均成本法（目前庫存均價）"""
    price_cols = [c for c in ["open", "high", "low", "close"] if c in df_price.columns]
    if price_cols:
        avg_prices = df_price[price_cols].mean(axis=1).fillna(df_price["close"])
    else:
        avg_prices = df_price["close"]

    inv = 0.0
    avg_cost = 0.0
    cost_list = []

    for p, net in zip(avg_prices, daily_net):
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


def fetch_price_history(stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
    """抓取指定區間每日股價 (自動判斷上市 .TW 或上櫃 .TWO)"""
    end_dt = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    for suffix in [".TW", ".TWO"]:
        ticker = f"{stock_id}{suffix}"
        df = yf.download(ticker, start=start_date, end=end_dt, progress=False, auto_adjust=False)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.reset_index()
            df["date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
            df = df[["date", "Open", "High", "Low", "Close", "Volume"]].copy()
            df.columns = ["date", "open", "high", "low", "close", "volume"]
            df["volume"] = (df["volume"] / 1000).round(0)
            return df[df["date"] >= start_date].dropna(subset=["close"])
    return pd.DataFrame()


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


def fetch_investment_trust_history(session: requests.Session, stock_id: str, start_date: str, end_date: str, mirror: str = None):
    """抓取「投信」每日淨買賣超(張)"""
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)
    html, ok_mirror, err = _fetch_dj_html(session, f"/z/zc/zcl/zcl.djhtm?a={stock_id}&c={s_fmt}&d={e_fmt}", mirror)
    if not html:
        return pd.DataFrame(columns=["date", "投信"]), mirror, err

    soup = BeautifulSoup(html, "html.parser")
    records = []
    for tr in soup.find_all("tr"):
        tds = [td.get_text(strip=True).replace(",", "").replace("+", "") for td in tr.find_all("td")]
        if len(tds) >= 4:
            ad_date = _parse_dj_date(tds[0])
            if ad_date and start_date <= ad_date <= end_date:
                try:
                    records.append({"date": ad_date, "投信": float(tds[2])})
                except ValueError:
                    continue

    if not records:
        return pd.DataFrame(columns=["date", "投信"]), ok_mirror, "投信表格無資料"
    df = pd.DataFrame(records).groupby("date", as_index=False)["投信"].sum()
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
                try:
                    buy_total = abs(float(tds[i + 1]))
                except ValueError:
                    pass
            elif "合計賣超" in text and i + 1 < len(tds):
                try:
                    sell_total = abs(float(tds[i + 1]))
                except ValueError:
                    pass

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
                try:
                    b_sum += abs(float(tds[3].get_text(strip=True).replace(",", "").replace("+", "")))
                except ValueError:
                    pass
            if right_a and "zco0" in right_a["href"].lower():
                try:
                    s_sum += abs(float(tds[8].get_text(strip=True).replace(",", "").replace("+", "")))
                except ValueError:
                    pass
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
    """抓取單一券商分點每日買賣超明細(張)（使用獨立 Session 支援多執行緒併發）"""
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
            tds = [td.get_text(strip=True).replace(",", "").replace("+", "") for td in tr.find_all("td")]
            if len(tds) >= 5:
                ad_date = _parse_dj_date(tds[0])
                if ad_date and start_date <= ad_date <= end_date:
                    try:
                        records.append({"date": ad_date, broker_name: float(tds[-1])})
                    except ValueError:
                        continue

        if records:
            return pd.DataFrame(records).groupby("date", as_index=False)[broker_name].sum()

    return pd.DataFrame(columns=["date", broker_name])


def evaluate_chip_scenario(
    top3_5d: int,
    top3_5d_ctrl_pct: float,
    latest_close: float,
    ma10: float,
    primary_cost: float,
    price_5d_pct: float,
    conc: dict,
) -> dict:
    """根據今年三大主力近 5 日控盤率 (%)、收盤價與 10MA/成本均價，以及 5/10/20 日集中度，判定五大實戰劇本"""
    c5 = conc.get("d5", {}).get("pct")
    c10 = conc.get("d10", {}).get("pct")
    c20 = conc.get("d20", {}).get("pct")

    if c5 is None or c10 is None or c20 is None:
        return {
            "id": 0,
            "type": "neutral",
            "title": "⚪ 籌碼中性整理（尚未取得完整集中度數據）",
            "desc": "請點擊左側「全部更新」或個股旁「🔄」以補齊最新 5 / 10 / 20 日全市場集中度。",
            "action": ""
        }

    above_cost = (primary_cost > 0 and latest_close >= primary_cost)
    above_ma10 = (ma10 > 0 and latest_close >= ma10)
    near_or_below_cost = (primary_cost > 0 and latest_close <= primary_cost * 1.02)

    # 情境 5：主力棄守（真破線）
    if (
        primary_cost > 0
        and latest_close < primary_cost * 0.98
        and top3_5d <= -30
        and top3_5d_ctrl_pct <= -3.0
        and c5 <= -5.0
        and c10 <= -3.0
    ):
        return {
            "id": 5,
            "type": "bear",
            "title": "🟢 當前劇本：5. 主力棄守（真破線）",
            "desc": (
                f"收盤價（{latest_close:,.2f} 元）已跌破最大主力均價（{primary_cost:,.2f} 元），"
                f"且近 5 日三大主力大賣 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%）、5 日集中度降至 {c5:+.1f}%。"
            ),
            "action": "連波段大戶都在停損結帳，下方無大資金承接，切忌因主力帳面虧損而盲目攤平。"
        }

    # 情境 3：高檔派發（假突破）
    if (
        price_5d_pct >= 2.0
        and top3_5d <= -30
        and top3_5d_ctrl_pct <= -2.0
        and c20 > 0
        and c5 <= -3.0
    ):
        return {
            "id": 3,
            "type": "bear",
            "title": "🟢 當前劇本：3. 高檔派發（假突破）",
            "desc": (
                f"近 5 日股價上漲 {price_5d_pct:+.2f}%，但今年三大主力逢高賣超 {top3_5d:+,} 張"
                f"（控盤率 {top3_5d_ctrl_pct:+.1f}%），且 5 日集中度由正轉負（{c5:+.1f}%）。"
            ),
            "action": "波段大戶正趁散戶追價把籌碼倒出，慎防假突破與拉高套牢風險。"
        }

    # 情境 1：雙重共振（主升段）
    if (
        above_ma10
        and (above_cost or primary_cost == 0)
        and top3_5d >= 30
        and top3_5d_ctrl_pct >= 2.0
        and c5 > 3.0
        and c10 > 0
        and c20 > 0
        and (c5 >= c10 or c5 >= 8.0)
    ):
        return {
            "id": 1,
            "type": "bull",
            "title": "🔴 當前劇本：1. 雙重共振（主升段）",
            "desc": (
                f"收盤價（{latest_close:,.2f} 元）站穩 10MA（{ma10:,.2f} 元）與主力成本之上，"
                f"三大主力近 5 日買超 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%），"
                f"且 20日({c20:+.1f}%) → 10日({c10:+.1f}%) → 5日({c5:+.1f}%) 集中度全面翻正加速。"
            ),
            "action": "最強多頭格局！波段大戶與短線大資金同步鎖碼，可沿 10MA 或主力成本線偏多操作。"
        }

    # 情境 4：成本護盤（真洗盤）
    if (
        near_or_below_cost
        and price_5d_pct <= -1.5
        and top3_5d >= 30
        and top3_5d_ctrl_pct >= 1.5
        and c5 >= 3.0
    ):
        return {
            "id": 4,
            "type": "bull",
            "title": "🔴 當前劇本：4. 成本護盤（真洗盤）",
            "desc": (
                f"收盤價（{latest_close:,.2f} 元）回測主力均價（{primary_cost:,.2f} 元）附近，"
                f"但三大主力逆勢買超 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%），5 日集中度維持 {c5:+.1f}%。"
            ),
            "action": "主力刻意洗盤護成本，趁恐慌承接浮額，一旦止跌回穩極易展開下一波反彈。"
        }

    # 情境 2：換手接棒（藏分點）
    if (
        above_ma10
        and top3_5d_ctrl_pct <= 1.5
        and c5 >= 5.0
        and (c10 >= 2.0 or c20 < c5)
    ):
        return {
            "id": 2,
            "type": "bull",
            "title": "🔴 當前劇本：2. 換手接棒（藏分點）",
            "desc": (
                f"收盤價（{latest_close:,.2f} 元）已站上 10MA（{ma10:,.2f} 元），"
                f"今年三大主力近 5 日控盤率僅 {top3_5d_ctrl_pct:+.1f}%（{top3_5d:+,} 張），"
                f"但全市場 5 日集中度逆勢飆升至 {c5:+.1f}%。"
            ),
            "action": "有新莊家進場接手或主力改用其他分點暗中吃貨，可對照下方「本區間買超第一名」鎖定新主力。"
        }

    return {
        "id": 0,
        "type": "neutral",
        "title": "⚪ 當前狀態：籌碼中性整理（尚未觸發五大關鍵劇本）",
        "desc": (
            f"收盤價 {latest_close:,.2f} 元（10MA {ma10:,.2f} 元），三大主力近 5 日淨額 {top3_5d:+,} 張"
            f"（控盤率 {top3_5d_ctrl_pct:+.1f}%），全市場 5 日集中度 {c5:+.1f}%，處於多空均衡或橫盤整理階段。"
        ),
        "action": "持續觀察收盤價與 10MA 位置、5 日集中度是否突破 ±5% 或三大主力控盤率表態。"
    }


def analyze_main_force(
    df_price: pd.DataFrame,
    df_chips: pd.DataFrame,
    period_label: str,
    df_chips_full: pd.DataFrame = None,
    df_price_full: pd.DataFrame = None,
    concentrations: dict = None,
) -> dict:
    """
    1. 錨定 2026/01/02 至今全年度累計買超排行決定「波段大主力」。
    2. 計算最大主力的累計買超、累計賣超、淨總數與目前庫存均價（成本）。
    3. 計算前三大主力各分點的連買／連賣天數、區間控盤率 (%)。
    4. 偵測近 5 日價量籌碼背離訊號，並結合收盤價、10MA 與 5/10/20 日集中度判定五大實戰劇本。
    """
    broker_cols = [c for c in df_chips.columns if c != "date"]
    if not broker_cols or df_price.empty:
        return {
            "main_force_html": "無分點資料",
            "main_force_sub_lines": [],
            "status_html": "⚪ 無法判定",
            "status_sub": "",
            "divergence_alert": None,
            "scenario": {"id": 0, "type": "neutral", "title": "⚪ 無資料", "desc": "", "action": ""},
            "top3_names": [],
            "top3_header_md": "",
            "top3_rows": [],
            "remark_md": "",
            "top_buyers": {},
            "top_sellers": {},
        }

    full_df = df_chips_full if df_chips_full is not None else df_chips
    full_price = df_price_full if df_price_full is not None else df_price

    # 1. 全年度（2026/01/02 至今）累計排行 -> 決定波段大主力
    total_ytd = full_df[broker_cols].sum().sort_values(ascending=False)
    top_buyers_ytd = total_ytd[total_ytd > 0].head(5)
    top_sellers_ytd = total_ytd[total_ytd < 0].tail(5).sort_values(ascending=True)

    # 2. 選定檢視區間與近期交易日統計
    period_sum = df_chips[broker_cols].sum()
    period_vol = float(df_price["volume"].sum()) if "volume" in df_price.columns else 0.0
    recent_1 = full_df.tail(1)[broker_cols].sum()
    recent_5 = full_df.tail(5)[broker_cols].sum()
    recent_10 = full_df.tail(10)[broker_cols].sum()
    recent_20 = full_df.tail(20)[broker_cols].sum()

    if top_buyers_ytd.empty:
        return {
            "main_force_html": "無波段買超主力（全年度偏空）",
            "main_force_sub_lines": ["今年以來主要分點與投信累計皆為淨賣超"],
            "status_html": "🔴 全年空方主導／出貨格局",
            "status_sub": "",
            "divergence_alert": None,
            "scenario": {"id": 5, "type": "bear", "title": "🟢 當前劇本：全年空方主導", "desc": "今年以來主要分點累計皆為淨賣超。", "action": "避開空頭排列個股。"},
            "top3_names": [],
            "top3_header_md": "",
            "top3_rows": [],
            "remark_md": "",
            "top_buyers": {},
            "top_sellers": {k: int(v) for k, v in top_sellers_ytd.items()},
        }

    # 鎖定年度最大主力
    primary_player = top_buyers_ytd.index[0]
    primary_ytd_net = int(top_buyers_ytd.iloc[0])

    # 計算最大主力今年以來的累計買超、賣超與目前庫存均價（主力成本）
    primary_series_ytd = full_df[primary_player]
    primary_buy_total = int(primary_series_ytd[primary_series_ytd > 0].sum())
    primary_sell_total = int(primary_series_ytd[primary_series_ytd < 0].sum())

    merged_for_cost = full_price.merge(full_df[["date", primary_player]], on="date", how="left").fillna({primary_player: 0.0})
    _, primary_cost, primary_inv = calculate_main_force_cost(merged_for_cost, merged_for_cost[primary_player])
    latest_close_full = float(full_price.iloc[-1]["close"])
    latest_ma10 = round(float(full_price["close"].tail(10).mean()), 2)

    main_force_html = f"{primary_player}（今年 {_fmt_html_num(primary_ytd_net)}）"
    main_force_sub_lines = [
        f"今年累計買超 {_fmt_html_num(primary_buy_total)} ／ 賣超 {_fmt_html_num(primary_sell_total)}（淨額 {_fmt_html_num(primary_ytd_net)}）"
    ]
    if primary_cost > 0 and primary_inv > 0:
        pnl_pct = (latest_close_full - primary_cost) / primary_cost * 100
        main_force_sub_lines.append(
            f"目前庫存均價：<b style='color:#ffb74d;'>{primary_cost:,.2f} 元</b>（帳面損益 {_fmt_html_pct(pnl_pct)}）"
        )

    # 卡片二：區間內今年三大主力狀態
    top3_names = list(top_buyers_ytd.head(3).index)
    top3_period_net = int(period_sum[top3_names].sum())
    top3_5d = int(recent_5[top3_names].sum())
    top3_20d = int(recent_20[top3_names].sum())
    threshold = 50 if len(df_price) <= 1 else 300

    if primary_player in DAY_TRADE_BROKERS and abs(primary_ytd_net) < 500:
        status_html = "🟡 隔日沖短線客主導（慎防倒貨）"
    elif top3_period_net > threshold:
        status_html = f"🟢 區間內今年三大主力吃貨中（{_fmt_html_num(top3_period_net)}）"
    elif top3_period_net < -threshold:
        status_html = f"🔴 區間內今年三大主力出貨中（{_fmt_html_num(top3_period_net)}）"
    else:
        status_html = f"🔵 區間內今年三大主力鎖碼觀望（{_fmt_html_num(top3_period_net)}）"

    status_sub = f"三大主力名單：{', '.join(top3_names)}"

    # 近 5 日價量籌碼背離警報 & 近 5 日三大主力控盤率 (%)
    divergence_alert = None
    price_5d_pct = 0.0
    vol_5d = float(full_price.tail(5)["volume"].sum()) if "volume" in full_price.columns else 0.0
    top3_5d_ctrl_pct = round((top3_5d / vol_5d * 100), 2) if vol_5d > 0 else 0.0

    if len(full_price) >= 5:
        close_now = float(full_price.iloc[-1]["close"])
        close_5d_ago = float(full_price.iloc[-6]["close"]) if len(full_price) >= 6 else float(full_price.iloc[0]["close"])
        price_5d_pct = ((close_now - close_5d_ago) / close_5d_ago * 100) if close_5d_ago > 0 else 0.0

        if price_5d_pct <= -3.0 and top3_5d > 0 and top3_5d_ctrl_pct >= 2.0:
            divergence_alert = {
                "type": "bull",
                "title": "🔴 底部承接背離警報（近 5 日價跌主力買）",
                "desc": (
                    f"近 5 日股價下跌 <b>{price_5d_pct:.2f}%</b>，但今年前三大主力逆勢合計淨買超 "
                    f"<b style='color:#ef5350;'>{top3_5d:+,} 張</b>（近 5 日控盤率 <b>{top3_5d_ctrl_pct:+.1f}%</b>），"
                    f"留意低檔換手與止跌轉折訊號。"
                )
            }
        elif price_5d_pct >= 3.0 and top3_5d < 0 and top3_5d_ctrl_pct <= -2.0:
            divergence_alert = {
                "type": "bear",
                "title": "🟢 高檔出貨背離警報（近 5 日價漲主力賣）",
                "desc": (
                    f"近 5 日股價上漲 <b>{price_5d_pct:+.2f}%</b>，但今年前三大主力合計逢高賣超 "
                    f"<b style='color:#26a69a;'>{top3_5d:+,} 張</b>（近 5 日控盤率 <b>{top3_5d_ctrl_pct:+.1f}%</b>），"
                    f"慎防假突破或拉高倒貨。"
                )
            }

    # 判定五大實戰劇本（結合收盤價、10MA、庫存均價與控盤率 % 自適應門檻）
    scenario = evaluate_chip_scenario(
        top3_5d=top3_5d,
        top3_5d_ctrl_pct=top3_5d_ctrl_pct,
        latest_close=latest_close_full,
        ma10=latest_ma10,
        primary_cost=primary_cost if primary_inv > 0 else 0.0,
        price_5d_pct=price_5d_pct,
        conc=concentrations or {},
    )

    # 前三大主力各分點明細（含連買／連賣天數、區間控盤率 %）
    top3_rows = []
    for name in top3_names:
        b_period = int(period_sum.get(name, 0))
        ctrl_pct = (b_period / period_vol * 100) if period_vol > 0 else 0.0
        streak = _calc_streak(full_df[name])
        top3_rows.append({
            "name": name,
            "ytd": int(total_ytd.get(name, 0)),
            "period": b_period,
            "d1": int(recent_1.get(name, 0)),
            "d10": int(recent_10.get(name, 0)),
            "d20": int(recent_20.get(name, 0)),
            "streak": streak,
            "ctrl_pct": round(ctrl_pct, 1),
        })

    # 備註：同時顯示本檢視區間「買超第一名」與「賣超第一名」
    sorted_period = period_sum.sort_values(ascending=False)
    top_buy_text = f"**{sorted_period.index[0]}**（{_fmt_md_num(int(sorted_period.iloc[0]))}）" if sorted_period.iloc[0] > 0 else "無"
    top_sell_text = f"**{sorted_period.index[-1]}**（{_fmt_md_num(int(sorted_period.iloc[-1]))}）" if sorted_period.iloc[-1] < 0 else "無"

    return {
        "main_force_html": main_force_html,
        "main_force_sub_lines": main_force_sub_lines,
        "status_html": status_html,
        "status_sub": status_sub,
        "divergence_alert": divergence_alert,
        "scenario": scenario,
        "top3_names": top3_names,
        "top3_header_md": f"- 今年前三大主力（**{', '.join(top3_names)}**）本區間合計：{_fmt_md_num(top3_period_net)}（近 20 日合計：{_fmt_md_num(top3_20d)}）",
        "top3_rows": top3_rows,
        "remark_md": f"- 💡 備註：本檢視區間買超第一名為 {top_buy_text} ｜ 賣超第一名為 {top_sell_text}",
        "top_buyers": {k: int(v) for k, v in top_buyers_ytd.items()},
        "top_sellers": {k: int(v) for k, v in top_sellers_ytd.items()},
    }


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
        "Accept": "application/vnd.github.v3+json"
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
        f"{stock_id}_meta.json"
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


def update_stock_database(stock_id: str, incremental_days: int = None) -> bool:
    """
    更新指定個股資料並存檔至 database/：
    - incremental_days=None（手動單檔全量校準）：聯集「全年度前 30 大波段主力」與「近 3 個交易日前 20 大活躍分點」，回溯全年明細。
    - incremental_days=3（全部更新／GitHub Actions 排程）：僅抓最近 3 個交易日並合併進既有資料庫。
    """
    stock_id = stock_id.strip()
    now_tw = _get_tw_now()
    end_date = now_tw.strftime("%Y-%m-%d")

    price_file = f"{stock_id}_price.csv"
    chips_file = f"{stock_id}_chips.csv"
    meta_file = f"{stock_id}_meta.json"
    price_path = os.path.join(DB_DIR, price_file)
    chips_path = os.path.join(DB_DIR, chips_file)
    meta_path = os.path.join(DB_DIR, meta_file)

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
        df_price_recent = fetch_price_history(stock_id, lookback_start, end_date)
        if df_price_recent.empty:
            return False
        df_price_recent = df_price_recent.tail(incremental_days).copy()
        fetch_start = df_price_recent["date"].min()
    else:
        fetch_start = START_DATE_FIXED
        df_price_recent = fetch_price_history(stock_id, fetch_start, end_date)
        if df_price_recent.empty:
            return False

    session = _create_session()
    df_chips_recent = df_price_recent[["date"]].copy()
    debug_notes = []

    # 1. 抓投信（統稱）
    df_trust, active_mirror, trust_err = fetch_investment_trust_history(session, stock_id, fetch_start, end_date)
    if not df_trust.empty:
        df_chips_recent = df_chips_recent.merge(df_trust, on="date", how="left")
    else:
        df_chips_recent["投信"] = 0.0
        if trust_err:
            debug_notes.append(f"投信異常: {trust_err}")

    # 2. 抓中文股票名稱
    stock_name = ""
    if is_incremental and os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                stock_name = str(json.load(f).get("stock_name", "")).strip()
        except Exception:
            pass
    if not stock_name:
        stock_name = fetch_stock_name(session, stock_id, active_mirror)

    # 3. 取得關鍵券商分點名單
    brokers, active_mirror, broker_err = fetch_top_brokers(session, stock_id, fetch_start, end_date, active_mirror, limit=30)
    if broker_err:
        debug_notes.append(f"分點清單異常: {broker_err}")

    # 全量模式額外聯集「近 3 個交易日前 20 大活躍分點」，確保突然建倉或突發倒貨的分點完整納入
    if not is_incremental and len(df_price_recent) >= 3:
        recent_3d_start = df_price_recent.tail(3)["date"].min()
        recent_brokers, _, _ = fetch_top_brokers(session, stock_id, recent_3d_start, end_date, active_mirror, limit=20)
        if recent_brokers:
            existing_names = {b["name"] for b in brokers}
            for rb in recent_brokers:
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

    # 5. 合併或覆蓋資料庫
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

    # 6. 抓取近 5 / 10 / 20 日全市場前 15 大精準籌碼集中度
    concentrations = fetch_rolling_concentrations(stock_id, df_price, active_mirror)

    # 7. 寫入本地 CSV；若在 GitHub Actions 內執行則交由 workflow 統一 git push，避免衝突
    df_price.to_csv(price_path, index=False, encoding="utf-8-sig")
    df_chips.to_csv(chips_path, index=False, encoding="utf-8-sig")

    is_gha = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
    if is_gha:
        ok1 = True
        sync_msg = f"GitHub Actions 自動增量同步（近 {incremental_days} 日）" if is_incremental else "GitHub Actions 自動同步"
    else:
        ok1, sync_msg = _sync_file_to_github(price_path, f"database/{price_file}")
        if ok1:
            _, sync_msg = _sync_file_to_github(chips_path, f"database/{chips_file}")

    # 8. 產出基礎摘要存檔
    meta = analyze_main_force(
        df_price,
        df_chips,
        f"{START_DATE_FIXED} 至今",
        df_chips_full=df_chips,
        df_price_full=df_price,
        concentrations=concentrations,
    )
    meta["stock_id"] = stock_id
    meta["stock_name"] = stock_name
    meta["broker_count"] = total_brokers
    meta["concentrations"] = concentrations
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
    print(f"[Daily Update] 開始增量更新（近 3 個交易日）共 {len(existing_stocks)} 檔個股：{existing_stocks}", flush=True)
    for sid in existing_stocks:
        t0 = time.time()
        ok = update_stock_database(sid, incremental_days=3)
        status = "成功" if ok else "失敗"
        print(f"[Daily Update] {sid} 更新{status}（耗時 {time.time() - t0:.1f} 秒）", flush=True)
