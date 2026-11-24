import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd
from data_engine import DB_DIR, fetch_period_concentration, _sync_file_to_github


def backfill_one_stock(stock_id: str, max_workers: int = 8) -> None:
    price_path = os.path.join(DB_DIR, f"{stock_id}_price.csv")
    aux_path = os.path.join(DB_DIR, f"{stock_id}_aux.csv")

    if not os.path.exists(price_path):
        print(f"[{stock_id}] 找不到 price.csv，跳過")
        return

    df_price = pd.read_csv(price_path).sort_values("date").reset_index(drop=True)
    if len(df_price) < 5:
        print(f"[{stock_id}] 交易日不足 5 日，跳過")
        return

    # 讀取或初始化 _aux.csv
    if os.path.exists(aux_path):
        df_aux = pd.read_csv(aux_path)
    else:
        df_aux = pd.DataFrame({"date": df_price["date"]})

    # 與 df_price 的交易日對齊
    df_aux = df_price[["date"]].merge(df_aux, on="date", how="left")
    for col in ("conc_5d", "conc_10d", "conc_20d"):
        if col not in df_aux.columns:
            df_aux[col] = pd.NA

    # 建立待抓取任務清單（已有數值的日期自動跳過）
    tasks = []
    windows = [("conc_5d", 5), ("conc_10d", 10), ("conc_20d", 20)]

    for idx in range(len(df_price)):
        end_date = str(df_price.loc[idx, "date"])
        for col_name, n_days in windows:
            if idx + 1 < n_days:
                continue
            if pd.notna(df_aux.loc[idx, col_name]):
                continue
            sub = df_price.iloc[idx - n_days + 1 : idx + 1]
            start_date = str(sub.iloc[0]["date"])
            vol_sum = float(sub["volume"].fillna(0.0).sum())
            if vol_sum > 0:
                tasks.append((idx, col_name, start_date, end_date, vol_sum))

    if not tasks:
        print(f"[{stock_id}] 今年歷史集中度已全數齊全，無需補抓。")
        return

    print(f"[{stock_id}] 開始補抓 {len(tasks)} 筆歷史集中度（共 {len(df_price)} 個交易日）...", flush=True)
    t0 = time.time()
    done_cnt = 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(fetch_period_concentration, stock_id, s_dt, e_dt, vol): (idx, col)
            for (idx, col, s_dt, e_dt, vol) in tasks
        }
        for future in as_completed(future_map):
            idx, col = future_map[future]
            try:
                res = future.result()
                if res and res.get("pct") is not None:
                    df_aux.loc[idx, col] = res["pct"]
            except Exception:
                pass
            done_cnt += 1
            if done_cnt % 100 == 0 or done_cnt == len(tasks):
                print(f"  [{stock_id}] 進度：{done_cnt}/{len(tasks)} ({time.time() - t0:.1f}s)", flush=True)

    df_aux.to_csv(aux_path, index=False, encoding="utf-8-sig")
    print(f"[{stock_id}] 已寫入本地 {aux_path}", flush=True)

    # 若有設定環境變數或 secrets，順便同步回 GitHub
    ok, msg = _sync_file_to_github(aux_path, f"database/{stock_id}_aux.csv")
    print(f"[{stock_id}] 雲端同步狀態：{msg}", flush=True)


if __name__ == "__main__":
    stock_ids = sorted({
        f.split("_")[0]
        for f in os.listdir(DB_DIR)
        if f.endswith("_price.csv")
    })
    print(f"偵測到 {len(stock_ids)} 檔已存個股：{stock_ids}\n")
    for sid in stock_ids:
        backfill_one_stock(sid, max_workers=8)
    print("\n✅ 全部歷史集中度補齊完成！若未自動推送至 GitHub，請手動 git add/commit/push database/ 目錄。")
