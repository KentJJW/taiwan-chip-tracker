import os
import re
import ssl
import json
import time
import base64
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
import requests
from requests.adapters import HTTPAdapter
import pandas as pd
import yfinance as yf
from bs4 import BeautifulSoup

DB_DIR = "database"
os.makedirs(DB_DIR, exist_ok=True)

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


def _fetch_dj_html(session: requests.Session, path_with_query: str, preferred_mirror: str = None):
    mirrors = [preferred_mirror] + [m for m in DJ_MIRRORS if m != preferred_mirror] if preferred_mirror else DJ_MIRRORS
    errors = []
    for base_url in mirrors:
        url = f"{base_url}{path_with_query}"
        try:
            resp = session.get(url, headers={"Referer": f"{base_url}/"}, timeout=12, verify=False)
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
    """從 zco.djhtm 解析所有買賣超分點連結 (同時相容不同大小寫與相對路徑)"""
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


def fetch_top_brokers(session: requests.Session, stock_id: str, start_date: str, end_date: str, mirror: str = None):
    """抓取區間買超與賣超關鍵券商分點，若區間頁無結果自動退回預設頁"""
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
            return broker_list[:25], ok_mirror, ""
        last_err = f"頁面 {path} 無法解析分點連結 (HTML長度:{len(html)})"

    return [], mirror, last_err


def fetch_broker_daily_history(session: requests.Session, stock_id: str, broker_info: dict, start_date: str, end_date: str, mirror: str) -> pd.DataFrame:
    """抓取單一券商分點每日買賣超明細(張)，多路徑備援並讀取最後一欄(買賣超)"""
    bhid = broker_info["bhid"]
    b_hex = broker_info["b"]
    broker_name = broker_info["name"]
    s_fmt = _format_dj_date(start_date)
    e_fmt = _format_dj_date(end_date)

    # 依序嘗試：1. 指定區間(C=1&D=&E=)  2. 預設完整歷史(a, b, BHID)  3. 精簡版(a, b)
    candidate_paths = []
    if bhid and b_hex:
        candidate_paths.append(f"/z/zc/zco/zco0/zco0.djhtm?A={stock_id}&BHID={bhid}&b={b_hex}&C=1&D={s_fmt}&E={e_fmt}")
        candidate_paths.append(f"/z/zc/zco/zco0/zco0.djhtm?a={stock_id}&b={b_hex}&BHID={bhid}")
    if b_hex:
        candidate_paths.append(f"/z/zc/zco/zco0/zco0.djhtm?a={stock_id}&b={b_hex}")
    if bhid and not b_hex:
        candidate_paths.append(f"/z/zc/zco/zco0/zco0.djhtm?a={stock_id}&BHID={bhid}&b={bhid}")

    for path in candidate_paths:
        html, _, _ = _fetch_dj_html(session, path, mirror)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        records = []
        for tr in soup.find_all("tr"):
            tds = [td.get_text(strip=True).replace(",", "").replace("+", "") for td in tr.find_all("td")]
            # zco0 表格欄位：日期, 買進(張), 賣出(張), 買賣總額(張), 買賣超(張) -> 最後一欄才是買賣超
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


def analyze_main_force(df_price: pd.DataFrame, df_chips: pd.DataFrame, period_label: str, df_chips_full: pd.DataFrame = None) -> dict:
    """
    錨定邏輯：
    1. 「主力是誰」永遠以 2026/01/02 至今全年度累計買超排行決定（不隨檢視區間換人）。
    2. 「吃貨／出貨狀態」與明細，則動態計算這批年度主力在「選定檢視區間」內的實際買賣超。
    """
    broker_cols = [c for c in df_chips.columns if c != "date"]
    if not broker_cols or df_price.empty:
        return {"main_force": "無分點資料", "status": "⚪ 無法判定", "top_buyers": {}, "top_sellers": {}, "details": []}

    full_df = df_chips_full if df_chips_full is not None else df_chips

    # 1. 全年度（2026/01/02 至今）累計排行 -> 決定真正的「波段大主力」
    total_ytd = full_df[broker_cols].sum().sort_values(ascending=False)
    top_buyers_ytd = total_ytd[total_ytd > 0].head(5)
    top_sellers_ytd = total_ytd[total_ytd < 0].tail(5).sort_values(ascending=True)

    # 2. 選定檢視區間內的買賣超變化 -> 觀察主力在該區間是在吃貨還是出貨
    period_sum = df_chips[broker_cols].sum()
    recent_1 = full_df.tail(1)[broker_cols].sum()
    recent_10 = full_df.tail(10)[broker_cols].sum()
    recent_20 = full_df.tail(20)[broker_cols].sum()

    if top_buyers_ytd.empty:
        return {
            "main_force": "無波段買超主力（全年度偏空）",
            "status": "🔴 全年空方主導／出貨格局",
            "top_buyers": {},
            "top_sellers": {k: int(v) for k, v in top_sellers_ytd.items()},
            "details": ["- 今年以來主要分點與投信累計皆為淨賣超"]
        }

    # 鎖定年度最大主力與前三大主力
    primary_player = top_buyers_ytd.index[0]
    primary_ytd_net = int(top_buyers_ytd.iloc[0])
    primary_period_net = int(period_sum.get(primary_player, 0))
    primary_1d = int(recent_1.get(primary_player, 0))
    primary_10d = int(recent_10.get(primary_player, 0))
    primary_20d = int(recent_20.get(primary_player, 0))

    top3_names = list(top_buyers_ytd.head(3).index)
    top3_period_net = int(period_sum[top3_names].sum())
    top3_20d = int(recent_20[top3_names].sum())

    # 依據年度主力在「目前檢視區間」的動作判定狀態
    if primary_player in DAY_TRADE_BROKERS and abs(primary_ytd_net) < 500:
        status = "🟡 隔日沖短線客主導（慎防倒貨）"
    elif top3_period_net > 300 and primary_period_net > 0:
        status = f"🟢 區間內主力吃貨中（{top3_period_net:+,} 張）"
    elif top3_period_net < -300 or primary_period_net < -200:
        status = f"🔴 區間內主力出貨中（{top3_period_net:+,} 張）"
    else:
        status = f"🔵 區間內主力鎖碼觀望（{top3_period_net:+,} 張）"

    details = [
        f"- 今年最大買超主力：**{primary_player}**（今年累計 **{primary_ytd_net:+,}** 張 ｜ 本區間淨買賣 **{primary_period_net:+,}** 張）",
        f"- **主力買賣超（{primary_player}）**：今日 **{primary_1d:+,}** 張 ｜ 近 10 日 **{primary_10d:+,}** 張 ｜ 近 20 日 **{primary_20d:+,}** 張",
        f"- 今年前三大主力（{', '.join(top3_names)}）本區間合計：**{top3_period_net:+,}** 張（近 20 日合計：**{top3_20d:+,}** 張）"
    ]

    for name in top3_names:
        b_ytd = int(total_ytd.get(name, 0))
        b_period = int(period_sum.get(name, 0))
        b_1d = int(recent_1.get(name, 0))
        b_10d = int(recent_10.get(name, 0))
        b_20d = int(recent_20.get(name, 0))
        details.append(
            f"  - **{name}**（今年累計 {b_ytd:+,} 張）：本區間 **{b_period:+,}** 張 ｜ 今日 **{b_1d:+,}** 張 ｜ 近 10 日 **{b_10d:+,}** 張 ｜ 近 20 日 **{b_20d:+,}** 張"
        )

    # 額外附註：若該短線區間買超第一名不是年度最大主力，也列出來供參考
    short_top = period_sum.sort_values(ascending=False)
    if not short_top.empty and short_top.iloc[0] > 0 and short_top.index[0] != primary_player:
        details.append(f"- 💡 備註：本檢視區間短線買超第一名為 **{short_top.index[0]}**（本區間 **{int(short_top.iloc[0]):+,}** 張）")

    return {
        "main_force": f"{primary_player}（今年 {primary_ytd_net:+,} 張）",
        "status": status,
        "top_buyers": {k: int(v) for k, v in top_buyers_ytd.items()},
        "top_sellers": {k: int(v) for k, v in top_sellers_ytd.items()},
        "details": details
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


def delete_stock_database(stock_id: str) -> tuple[bool, str]:
    """同時刪除本地 database/ 與 GitHub 上的該個股所有資料檔"""
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

    return True, f"已刪除 {stock_id}"


def update_stock_database(stock_id: str) -> bool:
    """固定從 2026-01-02 開始抓取指定個股股價、投信與前 25 大券商分點，存檔至 database/"""
    stock_id = stock_id.strip()
    start_date = START_DATE_FIXED
    end_date = datetime.today().strftime("%Y-%m-%d")

    df_price = fetch_price_history(stock_id, start_date, end_date)
    if df_price.empty:
        return False

    session = _create_session()
    df_chips = df_price[["date"]].copy()
    debug_notes = []

    # 1. 抓投信（統稱）
    df_trust, active_mirror, trust_err = fetch_investment_trust_history(session, stock_id, start_date, end_date)
    if not df_trust.empty:
        df_chips = df_chips.merge(df_trust, on="date", how="left")
    else:
        df_chips["投信"] = 0.0
        if trust_err:
            debug_notes.append(f"投信異常: {trust_err}")

    # 2. 抓前 25 大券商分點（外資依個別券商名稱獨立統計）
    brokers, active_mirror, broker_err = fetch_top_brokers(session, stock_id, start_date, end_date, active_mirror)
    if broker_err:
        debug_notes.append(f"分點清單異常: {broker_err}")

    fetched_count = 0
    for b in brokers:
        df_b = fetch_broker_daily_history(session, stock_id, b, start_date, end_date, active_mirror)
        if not df_b.empty and b["name"] not in df_chips.columns:
            df_chips = df_chips.merge(df_b, on="date", how="left")
            fetched_count += 1
        time.sleep(0.15)

    if brokers and fetched_count == 0:
        debug_notes.append(f"找到 {len(brokers)} 個分點連結，但明細回傳為空（範例: {brokers[0]}）")

    df_chips = df_chips.fillna(0.0)

    # 3. 寫入本地 CSV 並同步至 GitHub
    price_file = f"{stock_id}_price.csv"
    chips_file = f"{stock_id}_chips.csv"
    meta_file = f"{stock_id}_meta.json"

    df_price.to_csv(os.path.join(DB_DIR, price_file), index=False, encoding="utf-8-sig")
    df_chips.to_csv(os.path.join(DB_DIR, chips_file), index=False, encoding="utf-8-sig")

    ok1, sync_msg = _sync_file_to_github(os.path.join(DB_DIR, price_file), f"database/{price_file}")
    if ok1:
        ok2, sync_msg = _sync_file_to_github(os.path.join(DB_DIR, chips_file), f"database/{chips_file}")

    # 4. 產出基礎摘要存檔
    meta = analyze_main_force(df_price, df_chips, f"{start_date} 至今")
    meta["stock_id"] = stock_id
    meta["broker_count"] = fetched_count
    meta["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta["debug_notes"] = debug_notes
    meta["github_sync"] = f"✅ {sync_msg}" if ok1 else f"⚠️ {sync_msg}"

    with open(os.path.join(DB_DIR, meta_file), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    if ok1:
        _sync_file_to_github(os.path.join(DB_DIR, meta_file), f"database/{meta_file}")

    return True


if __name__ == "__main__":
    existing_stocks = sorted({
        fname.split("_")[0]
        for fname in os.listdir(DB_DIR)
        if fname.endswith("_price.csv")
    })
    for sid in existing_stocks:
        update_stock_database(sid)
