import os
import json
import time
import re
from datetime import datetime, timedelta
import requests
import pandas as pd
import yfinance as yf
from bs4 import BeautifulSoup
from FinMind.data import DataLoader

DB_DIR = "database"
os.makedirs(DB_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# 常見隔日沖分點名單（用於標記警示，避免誤判為波段吃貨）
DAY_TRADE_BROKERS = {
    "凱基-台北", "元大-土城永寧", "富邦-嘉義", "群益金鼎-大安",
    "國票-敦北法人", "統一-內湖", "永豐金-松山", "兆豐-大同"
}


def _roc_to_ad(roc_str: str) -> str:
    """將民國年字串 (例如 115/11/20) 轉為西元 YYYY-MM-DD"""
    parts = roc_str.strip().split("/")
    if len(parts) != 3:
        return ""
    year = int(parts[0]) + 1911
    return f"{year:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"


def fetch_1y_price(stock_id: str, start_date: str, end_date: str) -> pd.DataFrame:
    """抓取近一年每日股價 (自動判斷上市 .TW 或上櫃 .TWO)"""
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
            df["volume"] = (df["volume"] / 1000).round(0)  # 轉為張數
            return df.dropna(subset=["close"])
    return pd.DataFrame()


def fetch_1y_investment_trust(stock_id: str, start_date: str) -> pd.DataFrame:
    """抓取近一年每日「投信」淨買賣超(張)"""
    try:
        dl = DataLoader()
        df_inst = dl.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=start_date)
        if df_inst.empty:
            return pd.DataFrame(columns=["date", "投信"])
        df_trust = df_inst[df_inst["name"] == "Investment_Trust"].copy()
        df_trust["投信"] = ((df_trust["buy"] - df_trust["sell"]) / 1000).round(1)
        df_trust = df_trust.groupby("date", as_index=False)["投信"].sum()
        return df_trust[["date", "投信"]]
    except Exception:
        return pd.DataFrame(columns=["date", "投信"])


def fetch_top_brokers_1y(stock_id: str, start_date: str, end_date: str) -> list:
    """抓取近一年區間買超前 15 大 & 賣超前 10 大券商分點名稱與代碼 (外資依個別券商計算)"""
    s_fmt = f"{int(start_date[:4])}-{int(start_date[5:7])}-{int(start_date[8:10])}"
    e_fmt = f"{int(end_date[:4])}-{int(end_date[5:7])}-{int(end_date[8:10])}"
    url = f"https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco.djhtm?a={stock_id}&e={s_fmt}&f={e_fmt}"

    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = "cp950"
    soup = BeautifulSoup(resp.text, "html.parser")

    brokers = {}
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if "zco0.djhtm" in href and "b=" in href:
            match = re.search(r"[?&]b=([^&]+)", href)
            name = a_tag.get_text(strip=True)
            if match and name:
                bhid = match.group(1)
                if bhid not in brokers:
                    brokers[bhid] = name

    # 取前 20 個關鍵進出分點（涵蓋主要買超與主要賣超分點）
    return [{"bhid": k, "name": v} for k, v in list(brokers.items())[:20]]


def fetch_broker_daily_history(stock_id: str, bhid: str, broker_name: str, start_date: str) -> pd.DataFrame:
    """抓取單一券商分點在該個股的每日買賣超明細(張)"""
    url = f"https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco0/zco0.djhtm?a={stock_id}&b={bhid}&c=1"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "cp950"
        soup = BeautifulSoup(resp.text, "html.parser")

        records = []
        for tr in soup.find_all("tr"):
            tds = [td.get_text(strip=True).replace(",", "") for td in tr.find_all("td")]
            if len(tds) >= 4 and re.match(r"^\d{2,3}/\d{2}/\d{2}$", tds[0]):
                ad_date = _roc_to_ad(tds[0])
                if ad_date >= start_date:
                    try:
                        net_lots = float(tds[3])  # 買賣超(張)
                        records.append({"date": ad_date, broker_name: net_lots})
                    except ValueError:
                        continue
        if not records:
            return pd.DataFrame(columns=["date", broker_name])
        df = pd.DataFrame(records).groupby("date", as_index=False)[broker_name].sum()
        return df
    except Exception:
        return pd.DataFrame(columns=["date", broker_name])


def analyze_main_force(df_price: pd.DataFrame, df_chips: pd.DataFrame) -> dict:
    """直接判定：1. 主力是誰  2. 目前在吃貨還是出貨"""
    broker_cols = [c for c in df_chips.columns if c != "date"]
    if not broker_cols or df_price.empty:
        return {"main_force": "資料不足", "status": "⚪ 無法判定", "details": []}

    # 近一年總累計淨買超排行
    total_1y = df_chips[broker_cols].sum().sort_values(ascending=False)
    # 近 20 日 & 近 5 日淨買超
    recent_20 = df_chips.tail(20)[broker_cols].sum()
    recent_5 = df_chips.tail(5)[broker_cols].sum()

    top_buyers_1y = total_1y[total_1y > 0].head(5)
    if top_buyers_1y.empty:
        return {
            "main_force": "無明顯波段買超主力（全年度偏空調節）",
            "status": "🔴 全年空方主導／出貨格局",
            "top_buyers": {},
            "details": ["近一年主要分點與投信累計皆為淨賣超"]
        }

    primary_player = top_buyers_1y.index[0]
    primary_1y_net = int(top_buyers_1y.iloc[0])
    primary_20d_net = int(recent_20.get(primary_player, 0))
    primary_5d_net = int(recent_5.get(primary_player, 0))

    # 計算前 3 大年度主力近 5 日與近 20 日合力動向
    top3_names = list(top_buyers_1y.head(3).index)
    top3_20d = int(recent_20[top3_names].sum())
    top3_5d = int(recent_5[top3_names].sum())

    # 狀態判定邏輯
    if primary_player in DAY_TRADE_BROKERS and abs(primary_1y_net) < 500:
        status = "🟡 隔日沖短線客主導（慎防隔日倒貨）"
    elif top3_20d > 0 and top3_5d > 0:
        status = "🟢 主力持續吃貨中"
    elif top3_20d < -300 or top3_5d < -200:
        status = "🔴 主力反手出貨倒貨中"
    else:
        status = "🔵 主力吃貨鎖碼／高檔觀望中"

    details = [
        f"年度最大主力：**{primary_player}**（近一年累計淨買超 **{primary_1y_net:+,}** 張）",
        f"最大主力近期動向：近 20 日 **{primary_20d_net:+,}** 張 ｜ 近 5 日 **{primary_5d_net:+,}** 張",
        f"年度前三大主力（{', '.join(top3_names)}）近 20 日合計：**{top3_20d:+,}** 張"
    ]

    return {
        "main_force": f"{primary_player}（年度累計 {primary_1y_net:+,} 張）",
        "status": status,
        "top_buyers": {k: int(v) for k, v in top_buyers_1y.items()},
        "details": details
    }


def update_stock_database(stock_id: str) -> bool:
    """抓取指定個股近一年股價與籌碼，並存檔至 database/ 資料夾"""
    stock_id = stock_id.strip()
    end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=365)).strftime("%Y-%m-%d")

    df_price = fetch_1y_price(stock_id, start_date, end_date)
    if df_price.empty:
        return False

    # 以交易日為主軸合併籌碼
    df_chips = df_price[["date"]].copy()

    # 1. 抓投信（統稱）
    df_trust = fetch_1y_investment_trust(stock_id, start_date)
    if not df_trust.empty:
        df_chips = df_chips.merge(df_trust, on="date", how="left")
    else:
        df_chips["投信"] = 0.0

    # 2. 抓前 20 大券商分點（外資依個別券商名稱獨立統計）
    brokers = fetch_top_brokers_1y(stock_id, start_date, end_date)
    for b in brokers:
        df_b = fetch_broker_daily_history(stock_id, b["bhid"], b["name"], start_date)
        if not df_b.empty and b["name"] not in df_chips.columns:
            df_chips = df_chips.merge(df_b, on="date", how="left")
        time.sleep(0.25)  # 禮貌性延遲避免被擋

    df_chips = df_chips.fillna(0.0)

    # 3. 產出主力與吃貨/出貨分析摘要
    meta = analyze_main_force(df_price, df_chips)
    meta["stock_id"] = stock_id
    meta["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 4. 寫入 database/ 資料夾
    df_price.to_csv(os.path.join(DB_DIR, f"{stock_id}_price.csv"), index=False, encoding="utf-8-sig")
    df_chips.to_csv(os.path.join(DB_DIR, f"{stock_id}_chips.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(DB_DIR, f"{stock_id}_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return True


if __name__ == "__main__":
    # 供 GitHub Actions 每天自動更新 database/ 內所有已存在的股票
    existing_stocks = sorted({
        fname.split("_")[0]
        for fname in os.listdir(DB_DIR)
        if fname.endswith("_price.csv")
    })
    print(f"準備更新 database 內共 {len(existing_stocks)} 檔個股: {existing_stocks}")
    for sid in existing_stocks:
        print(f"正在更新 {sid}...")
        update_stock_database(sid)
