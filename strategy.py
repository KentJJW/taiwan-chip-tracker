import numpy as np
import pandas as pd

# 知名高頻隔日沖券商分點名單（靜態黑名單；另有動態週轉與反手率偵測）
DAY_TRADE_BROKERS = {
    "凱基-台北",
    "凱基-松山",
    "凱基-站前",
    "凱基-虎尾",
    "凱基-市政",
    "富邦-建國",
    "富邦-嘉義",
    "富邦-仁愛",
    "元大-土城永寧",
    "元大-竹科",
    "統一-內湖",
    "統一-敦南",
    "群益金鼎-大安",
    "群益金鼎-民權",
    "永豐金-敦南",
    "永豐金-竹北",
    "國票-敦北法人",
    "兆豐-大安",
    "元富-城中",
    "華南永昌-永和",
    "國泰-敦南",
}

# 外資券商分點辨識關鍵字（對應 MoneyDJ 標準分點命名）
FOREIGN_BROKER_KEYWORDS = (
    "美商",
    "港商",
    "新加坡商",
    "瑞銀",
    "高盛",
    "摩根",
    "美林",
    "匯豐",
    "巴黎",
    "花旗",
    "野村",
    "麥格理",
    "大和國泰",
    "德意志",
    "法國興業",
    "瑞士信貸",
    "巴克萊",
)

# 100% 純籌碼策略參數集中管理（趨勢 60% ＋ 動能 40%）
STRATEGY_PARAMS = {
    # 多空分數門檻（做多門檻下修至 55 分，空方門檻 35 分）
    "bull_score_min": 55,
    "strong_bull_score_min": 70,
    "bear_score_max": 35,

    # 滾動視窗與 Z-Score 門檻
    "wave_lookback_days": 60,
    "zscore_window": 60,
    "z_bull_min": 1.0,
    "z_bear_max": -1.0,

    # 全體波段分點合力控盤率門檻（趨勢核心）
    "wave_agg_20d_strong_pct": 4.0,
    "wave_agg_20d_mod_pct": 1.5,
    "wave_agg_20d_mild_pct": 0.3,
    "wave_agg_60d_mod_pct": 1.0,

    # 集中度與前三大波段主力門檻
    "conc_strong_pct": 10.0,
    "conc_mod_pct": 3.0,
    "conc_20d_strong_pct": 8.0,
    "conc_20d_mod_pct": 2.0,
    "conc_10d_strong_pct": 5.0,
    "conc_10d_mod_pct": 1.5,
    "top3_ctrl_strong_pct": 5.0,
    "top3_ctrl_mod_pct": 2.0,
    "top3_min_amount_ntd": 30_000_000.0,

    # 主力分點廣度比（含外資共識）門檻
    "breadth_min_active": 6,
    "breadth_bull_pct": 65.0,
    "breadth_bear_pct": 35.0,
    "breadth_hold_warn_pct": 40.0,
    "breadth_streak_pct": 60.0,
    "breadth_streak_days": 3,
    "foreign_breadth_min_active": 3,
    "foreign_breadth_bull_pct": 70.0,
    "foreign_breadth_bear_pct": 30.0,

    # 信用交易（融資四象限與情境借券）門檻與相對量能防呆
    "credit_vol_ratio_min": 3.0,
    "margin_surge_5d_pct": 5.0,
    "margin_clean_5d_pct": -5.0,
    "margin_dom_buy_ratio": 0.50,
    "margin_quad_bull_breadth": 55.0,
    "margin_quad_clean_breadth": 60.0,
    "margin_quad_bear_breadth": 45.0,
    "sbl_cover_5d_pct": -5.0,
    "sbl_surge_5d_pct": 5.0,
    "short_margin_squeeze_pct": 20.0,

    # 隔日沖動態偵測與當日灌水濾網
    "dt_churn_ratio": 4.0,
    "dt_flip_rate": 0.55,
    "dt_today_vol_warn_pct": 5.0,
    "dt_today_vol_mid_pct": 8.0,
    "dt_today_vol_high_pct": 12.0,

    # 贏家分點獨立事件 20 日回測門檻
    "winner_fwd_days": 20,
    "winner_cooldown_days": 10,
    "winner_min_signals": 3,
    "winner_min_win_rate": 60.0,
    "winner_min_avg_ret": 3.0,
}

# 純籌碼結構 5 大狀態判讀矩陣（趨勢 60% ＋ 動能 40%，做多門檻 ≥ 55 分）
SCENARIO_MATRIX = [
    {
        "id": 1,
        "type": "bull",
        "name": "🔥 1. 波段趨勢強多共振",
        "main_cond": "全體波段分點 20/60 日合力明確偏多，累計庫存站上 20 日庫存均線",
        "conc_cond": "10/20 日中線集中度與 5 日集中度同向偏多 ＋ 主力廣度 ≥ 65%（大戶／外資齊心）",
        "aux_cond": "投信 20 日偏多、融資清洗或點火、多頭借券回補或集保大戶增持；純籌碼分數 ≥ 70",
        "action": "趨勢面與動能面高度共振，屬主升鎖碼格局；短線乖離偏大時宜等拉回或分批操作。",
    },
    {
        "id": 2,
        "type": "bull",
        "name": "📈 2. 偏多吸籌／進場甜蜜區",
        "main_cond": "全體波段分點合力轉正或庫存穩步墊高，大戶籌碼逐步由空翻多",
        "conc_cond": "中線集中度溫和偏正或止跌收斂，主力廣度未跌破 40%（無少數硬撐跡象）",
        "aux_cond": "常見融資清洗或投信／大戶緩步承接；純籌碼分數 55 ～ 69（達做多門檻）",
        "action": "已進入偏多做多區且尚未過熱，若股價站穩 20MA 往往為風險報酬比最佳的切入位置。",
    },
    {
        "id": 3,
        "type": "neutral",
        "name": "⚪ 3. 籌碼中性／換手觀望",
        "main_cond": "全體波段分點 20 日淨進出幅度不大，累計庫存貼近 20 日均線橫向整理",
        "conc_cond": "10/20 日集中度介於 -2% ～ +2%，主力廣度落於 40% ～ 65% 中性區間",
        "aux_cond": "趨勢面與動能面互相抵銷，大資金未展現壓倒性方向；純籌碼分數 36 ～ 54",
        "action": "籌碼處於正常換手與平衡期，缺乏明確波段方向，宜等待突破 55 分或跌破 35 分表態。",
    },
    {
        "id": 4,
        "type": "neutral",
        "name": "⚡ 4. 籌碼分歧／少數硬撐",
        "main_cond": "中線趨勢（全體波段分點）與短線動能方向衝突，或當日隔日沖佔比過高（≥ 8%）",
        "conc_cond": "5 日集中度為正但主力廣度 ≤ 40%（少數大戶硬撐、多數大戶偷跑），或外資與本土對作",
        "aux_cond": "常見散戶融資過熱、隔日沖拉高灌水，或僅少數分點逆勢攤平接刀",
        "action": "表面買盤與全體波段分點共識不一致，須提防單一主力撐盤失敗或隔日沖獲利了結。",
    },
    {
        "id": 5,
        "type": "bear",
        "name": "🟢 5. 波段全面發散（大戶撤退）",
        "main_cond": "全體波段分點 20/60 日合力持續倒貨，累計庫存跌破 20 日庫存均線向下",
        "conc_cond": "10/20 日與 5 日集中度轉負 ＋ 主力廣度 ≤ 35%（大戶／外資集體撤退）",
        "aux_cond": "投信連賣、散戶融資過熱或空方加壓（空頭背景借券回補不加分）；純籌碼分數 ≤ 35",
        "action": "趨勢與動能同步偏空發散，應避開做多；若股價同步跌破 20MA 則符合偏空操作條件。",
    },
]


def is_foreign_broker(broker_name: str) -> bool:
    """辨識券商分點是否為外資分點"""
    name = str(broker_name or "").strip()
    if not name:
        return False
    return any(kw in name for kw in FOREIGN_BROKER_KEYWORDS)


def compute_rolling_zscore(
    series: pd.Series,
    window: int = 60,
    min_periods: int = 15,
) -> float | None:
    """計算序列最新一筆數值相對於過去 window 期之標準分數 (Z-Score)"""
    if series is None:
        return None
    s_clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(s_clean) < min_periods:
        return None
    sub = s_clean.tail(window).to_numpy(dtype=float)
    std_v = float(np.std(sub))
    if std_v <= 1e-9:
        return 0.0
    return round((float(sub[-1]) - float(np.mean(sub))) / std_v, 2)


def profile_brokers(
    df_price: pd.DataFrame,
    df_chips: pd.DataFrame,
    lookback_days: int = 60,
) -> dict:
    """
    對所有追蹤券商分點（不含「投信」）進行行為畫像：
    - 統計近 1/5/10/20/60 日與今年累計淨買賣超
    - 結合靜態名單與「週轉係數＋隔日反手率」自動標記隔日沖分點
    - 以獨立事件 20 日前瞻報酬回測篩選「🏆 高勝率贏家分點」
    """
    if df_price is None or df_price.empty or df_chips is None or df_chips.empty:
        return {}

    pure_cols = [c for c in df_chips.columns if c not in ("date", "投信")]
    if not pure_cols:
        return {}

    df_p = df_price.sort_values("date").reset_index(drop=True)
    df_m = df_p[["date", "close"]].merge(
        df_chips[["date"] + pure_cols],
        on="date",
        how="left",
    )
    df_m[pure_cols] = df_m[pure_cols].fillna(0.0)

    n = len(df_m)
    close_arr = pd.to_numeric(df_m["close"], errors="coerce").to_numpy(dtype=float)
    vol_arr = (
        pd.to_numeric(df_p["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        if "volume" in df_p.columns
        else np.zeros(n, dtype=float)
    )

    fwd_days = STRATEGY_PARAMS["winner_fwd_days"]
    cooldown = STRATEGY_PARAMS["winner_cooldown_days"]
    lb_start = max(0, n - lookback_days)

    profiles = {}
    for col in pure_cols:
        arr = pd.to_numeric(df_m[col], errors="coerce").fillna(0.0).to_numpy(dtype=float)
        arr_lb = arr[lb_start:]

        net_1d = int(round(arr[-1])) if n >= 1 else 0
        net_5d = int(round(arr[-5:].sum())) if n >= 1 else 0
        net_10d = int(round(arr[-10:].sum())) if n >= 1 else 0
        net_20d = int(round(arr[-20:].sum())) if n >= 1 else 0
        net_60d = int(round(arr_lb.sum()))
        net_ytd = int(round(arr.sum()))

        gross_60d = float(np.abs(arr_lb).sum())
        churn_ratio = round(gross_60d / max(abs(float(net_60d)), 1.0), 2) if gross_60d > 0 else 0.0

        # 動態隔日沖偵測（統計買超日後隔天立刻轉賣超的比例）
        active_days = int((arr_lb != 0).sum())
        buy_days_idx = np.where(arr_lb[:-1] > 0)[0]
        flip_rate = 0.0
        if len(buy_days_idx) >= 5:
            next_vals = arr_lb[buy_days_idx + 1]
            flip_cnt = int((next_vals < 0).sum())
            flip_rate = flip_cnt / len(buy_days_idx)

        is_foreign = is_foreign_broker(col)
        is_static_dt = col in DAY_TRADE_BROKERS
        is_dynamic_dt = (
            (not is_foreign)
            and active_days >= 10
            and churn_ratio >= STRATEGY_PARAMS["dt_churn_ratio"]
            and flip_rate >= STRATEGY_PARAMS["dt_flip_rate"]
        )
        is_day_trader = bool(is_static_dt or is_dynamic_dt)

        # 獨立事件 20 日前瞻勝率回測（僅對非隔日沖分點計算）
        win_rate = None
        avg_fwd_ret = None
        buy_signals = 0
        is_winner = False

        if (not is_day_trader) and n > fwd_days + 5:
            pos_vals = arr[arr > 0]
            if len(pos_vals) >= STRATEGY_PARAMS["winner_min_signals"]:
                sig_thresh = max(15.0, float(np.percentile(pos_vals, 65)))
                fwd_rets = []
                last_sig_i = -cooldown - 1
                for i in range(0, n - fwd_days):
                    if i - last_sig_i < cooldown:
                        continue
                    v_i = arr[i]
                    if v_i < sig_thresh:
                        continue
                    if vol_arr[i] > 0 and v_i < 30.0 and (v_i / vol_arr[i] * 100.0) < 0.2:
                        continue
                    c_now = close_arr[i]
                    c_fwd = close_arr[i + fwd_days]
                    if pd.notna(c_now) and pd.notna(c_fwd) and c_now > 0:
                        fwd_rets.append((c_fwd - c_now) / c_now * 100.0)
                        last_sig_i = i

                buy_signals = len(fwd_rets)
                if buy_signals >= STRATEGY_PARAMS["winner_min_signals"]:
                    wins = sum(1 for r in fwd_rets if r > 0)
                    win_rate = round(wins / buy_signals * 100.0, 1)
                    avg_fwd_ret = round(float(np.mean(fwd_rets)), 2)
                    if (
                        win_rate >= STRATEGY_PARAMS["winner_min_win_rate"]
                        and avg_fwd_ret >= STRATEGY_PARAMS["winner_min_avg_ret"]
                    ):
                        is_winner = True

        tags = []
        if is_day_trader:
            tags.append("⚡ 隔日沖")
        if is_winner:
            tags.append("🏆 贏家")
        if is_foreign:
            tags.append("🌐 外資")

        profiles[col] = {
            "name": col,
            "net_1d": net_1d,
            "net_5d": net_5d,
            "net_10d": net_10d,
            "net_20d": net_20d,
            "net_60d": net_60d,
            "net_ytd": net_ytd,
            "churn_ratio": churn_ratio,
            "flip_rate": round(flip_rate, 2),
            "is_foreign": is_foreign,
            "is_day_trader": is_day_trader,
            "is_winner": is_winner,
            "win_rate": win_rate,
            "avg_fwd_ret": avg_fwd_ret,
            "buy_signals": buy_signals,
            "tags": tags,
        }

    return profiles


def select_wave_main_forces(
    profiles: dict,
    top_n: int = 3,
    exclude_day_traders: bool = True,
) -> tuple[list[str], list[str]]:
    """依近 60 日累計淨買超挑選前 top_n 大波段主力，並回傳被濾除的隔日沖分點名單"""
    if not profiles:
        return [], []

    sorted_pos = sorted(
        [v for v in profiles.values() if v.get("net_60d", 0) > 0],
        key=lambda x: x["net_60d"],
        reverse=True,
    )

    top_names = []
    filtered_dt = []
    for item in sorted_pos:
        if exclude_day_traders and item.get("is_day_trader"):
            filtered_dt.append(item["name"])
            continue
        top_names.append(item["name"])
        if len(top_names) >= top_n:
            break

    return top_names, filtered_dt


def compute_wave_aggregate(
    df_price_full: pd.DataFrame,
    df_chips_full: pd.DataFrame,
    profiles: dict | None = None,
) -> dict | None:
    """
    計算「全體波段分點合力」（排除隔日沖與投信，涵蓋買賣兩端所有追蹤核心分點）：
    - 解決只抓「近 60 日最大買超前三名」在空頭段只看到接刀手、看不到砸盤黑手的盲區
    - 產出 5/10/20/60 日淨流向、20/60 日控盤率、20 日控盤 Z-Score 與累計庫存相對 20 日均線位置
    """
    if df_price_full is None or df_price_full.empty or df_chips_full is None or df_chips_full.empty:
        return None

    pure_cols = [c for c in df_chips_full.columns if c not in ("date", "投信")]
    if not pure_cols:
        return None

    if profiles:
        wave_cols = [c for c in pure_cols if not profiles.get(c, {}).get("is_day_trader", c in DAY_TRADE_BROKERS)]
    else:
        wave_cols = [c for c in pure_cols if c not in DAY_TRADE_BROKERS]
    if not wave_cols:
        wave_cols = pure_cols

    df_p = df_price_full.sort_values("date").reset_index(drop=True)
    df_m = df_p[["date", "volume"]].merge(
        df_chips_full[["date"] + wave_cols],
        on="date",
        how="left",
    )
    df_m[wave_cols] = df_m[wave_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    vol_s = pd.to_numeric(df_m["volume"], errors="coerce").fillna(0.0)

    agg_daily = df_m[wave_cols].sum(axis=1)
    cum_s = agg_daily.cumsum()
    cum_ma20_s = cum_s.rolling(20, min_periods=5).mean()

    net_1d = int(round(float(agg_daily.iloc[-1])))
    net_5d = int(round(float(agg_daily.tail(5).sum())))
    net_10d = int(round(float(agg_daily.tail(10).sum())))
    net_20d = int(round(float(agg_daily.tail(20).sum())))
    net_60d = int(round(float(agg_daily.tail(60).sum())))

    vol_5d = float(vol_s.tail(5).sum())
    vol_20d = float(vol_s.tail(20).sum())
    vol_60d = float(vol_s.tail(60).sum())

    ctrl_5d_pct = round(net_5d / vol_5d * 100.0, 2) if vol_5d > 0 else 0.0
    ctrl_20d_pct = round(net_20d / vol_20d * 100.0, 2) if vol_20d > 0 else 0.0
    ctrl_60d_pct = round(net_60d / vol_60d * 100.0, 2) if vol_60d > 0 else 0.0

    roll_20_net = agg_daily.rolling(20, min_periods=10).sum()
    roll_20_vol = vol_s.rolling(20, min_periods=10).sum().replace(0, np.nan)
    roll_20_ctrl = (roll_20_net / roll_20_vol) * 100.0
    ctrl_20d_z = compute_rolling_zscore(roll_20_ctrl, window=STRATEGY_PARAMS["zscore_window"], min_periods=15)

    cum_now = float(cum_s.iloc[-1])
    cum_ma20 = float(cum_ma20_s.iloc[-1]) if pd.notna(cum_ma20_s.iloc[-1]) else cum_now
    cum_above_ma20 = bool(cum_now > cum_ma20) if abs(cum_now - cum_ma20) > 1e-6 else None

    return {
        "wave_broker_names": wave_cols,
        "broker_count": len(wave_cols),
        "net_1d": net_1d,
        "net_5d": net_5d,
        "net_10d": net_10d,
        "net_20d": net_20d,
        "net_60d": net_60d,
        "ctrl_5d_pct": ctrl_5d_pct,
        "ctrl_20d_pct": ctrl_20d_pct,
        "ctrl_60d_pct": ctrl_60d_pct,
        "ctrl_20d_z": ctrl_20d_z,
        "cum_now": int(round(cum_now)),
        "cum_ma20": int(round(cum_ma20)),
        "cum_above_ma20": cum_above_ma20,
    }


def compute_broker_breadth(
    df_chips_full: pd.DataFrame,
    profiles: dict | None = None,
    lookback_days: int = 5,
) -> dict | None:
    """
    計算「主力分點廣度比（含外資分點共識度）」：
    - 排除「投信」與隔日沖分點，統計追蹤波段分點中「淨買超家數 ÷ (買超家數 + 賣超家數)」
    - 獨立拆出外資分點近 5 日買賣家數與共識比率，快速辨識外資集體買賣或對作
    """
    if df_chips_full is None or df_chips_full.empty:
        return None

    pure_cols = [c for c in df_chips_full.columns if c not in ("date", "投信")]
    if not pure_cols:
        return None

    if profiles:
        wave_cols = [c for c in pure_cols if not profiles.get(c, {}).get("is_day_trader", c in DAY_TRADE_BROKERS)]
    else:
        wave_cols = [c for c in pure_cols if c not in DAY_TRADE_BROKERS]

    if not wave_cols:
        wave_cols = pure_cols

    foreign_cols = [c for c in wave_cols if is_foreign_broker(c)]
    mat = df_chips_full[wave_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if mat.shape[0] == 0:
        return None

    # 近 5 日整體波段分點廣度
    sum_5d = mat[-lookback_days:].sum(axis=0)
    buy_5d = int((sum_5d > 0).sum())
    sell_5d = int((sum_5d < 0).sum())
    active_5d = buy_5d + sell_5d
    breadth_5d = round(buy_5d / active_5d * 100.0, 1) if active_5d > 0 else None

    # 今日單日整體波段分點廣度
    day_1 = mat[-1]
    buy_1d = int((day_1 > 0).sum())
    sell_1d = int((day_1 < 0).sum())
    active_1d = buy_1d + sell_1d
    breadth_1d = round(buy_1d / active_1d * 100.0, 1) if active_1d > 0 else None

    # 外資分點近 5 日共識度
    f_buy_5d = 0
    f_sell_5d = 0
    f_active_5d = 0
    f_breadth_5d = None
    if foreign_cols:
        f_idx = [wave_cols.index(c) for c in foreign_cols]
        f_sum_5d = sum_5d[f_idx]
        f_buy_5d = int((f_sum_5d > 0).sum())
        f_sell_5d = int((f_sum_5d < 0).sum())
        f_active_5d = f_buy_5d + f_sell_5d
        if f_active_5d > 0:
            f_breadth_5d = round(f_buy_5d / f_active_5d * 100.0, 1)

    high_thresh = STRATEGY_PARAMS["breadth_streak_pct"]
    low_thresh = 100.0 - high_thresh
    high_streak = 0
    low_streak = 0

    for row in mat[::-1]:
        b_i = int((row > 0).sum())
        s_i = int((row < 0).sum())
        a_i = b_i + s_i
        if a_i < 4:
            break
        pct_i = b_i / a_i * 100.0
        if pct_i >= high_thresh:
            high_streak += 1
        else:
            break

    for row in mat[::-1]:
        b_i = int((row > 0).sum())
        s_i = int((row < 0).sum())
        a_i = b_i + s_i
        if a_i < 4:
            break
        pct_i = b_i / a_i * 100.0
        if pct_i <= low_thresh:
            low_streak += 1
        else:
            break

    return {
        "breadth_5d": breadth_5d,
        "buy_5d": buy_5d,
        "sell_5d": sell_5d,
        "active_5d": active_5d,
        "breadth_1d": breadth_1d,
        "buy_1d": buy_1d,
        "sell_1d": sell_1d,
        "active_1d": active_1d,
        "foreign_breadth_5d": f_breadth_5d,
        "foreign_buy_5d": f_buy_5d,
        "foreign_sell_5d": f_sell_5d,
        "foreign_active_5d": f_active_5d,
        "high_streak": high_streak,
        "low_streak": low_streak,
        "total_wave_brokers": len(wave_cols),
    }


def evaluate_trust_phase(trust_info: dict) -> dict:
    """判定投信水位階段與短線動向"""
    if not isinstance(trust_info, dict):
        return {"tier_label": "", "tier_level": "info", "momentum": "", "momentum_level": "info"}

    streak = int(trust_info.get("streak", 0) or 0)
    d5 = int(trust_info.get("d5", 0) or 0)
    hold_pct = trust_info.get("holding_pct")

    if streak >= 3 and d5 > 0:
        momentum = f"連買 {streak} 日認養中"
        momentum_level = "bull"
    elif d5 > 0 and streak >= 1:
        momentum = f"偏買（連買 {streak} 日）"
        momentum_level = "bull"
    elif streak <= -3 and d5 < 0:
        momentum = f"連賣 {abs(streak)} 日調節中"
        momentum_level = "bear"
    elif d5 < 0 and streak <= -1:
        momentum = f"偏賣（連賣 {abs(streak)} 日）"
        momentum_level = "bear"
    elif d5 > 0:
        momentum = "近 5 日偏多小買"
        momentum_level = "bull"
    elif d5 < 0:
        momentum = "近 5 日偏空小賣"
        momentum_level = "bear"
    else:
        momentum = "短線觀望"
        momentum_level = "info"

    tier_label = ""
    tier_level = "info"
    if hold_pct is not None:
        if hold_pct >= 10.0:
            tier_label = f"⚠️ 高水位警戒 ({hold_pct:.2f}%)"
            tier_level = "warn"
        elif 0.5 <= hold_pct <= 5.0 and (d5 > 0 or streak > 0):
            tier_label = f"🎯 初升認養甜蜜期 ({hold_pct:.2f}%)"
            tier_level = "bull"
        elif hold_pct > 5.0:
            tier_label = f"中高度持股 ({hold_pct:.2f}%)"
            tier_level = "info"
        elif hold_pct >= 0.5:
            tier_label = f"低中水位 ({hold_pct:.2f}%)"
            tier_level = "info"
        else:
            tier_label = f"低水位觀察 ({hold_pct:.2f}%)"
            tier_level = "info"

    return {
        "tier_label": tier_label,
        "tier_level": tier_level,
        "momentum": momentum,
        "momentum_level": momentum_level,
    }


def evaluate_divergence_alert(
    price_5d_pct: float,
    top3_5d: int,
    top3_5d_ctrl_pct: float,
    latest_close: float,
    top3_5d_amount_ntd: float = 0.0,
    top3_5d_ctrl_z: float | None = None,
    conc_5d: float | None = None,
    trust_info: dict | None = None,
    margin_info: dict | None = None,
    sbl_info: dict | None = None,
    wave_agg_info: dict | None = None,
) -> dict | None:
    """價量籌碼背離視覺警示條（純視覺參考，不介入純籌碼健康分數計算）"""
    _ = (latest_close, trust_info, margin_info, sbl_info)
    p = STRATEGY_PARAMS
    amt_ok = (top3_5d_amount_ntd == 0.0) or (abs(top3_5d_amount_ntd) >= p["top3_min_amount_ntd"] * 0.5)

    agg_5d = wave_agg_info.get("net_5d") if isinstance(wave_agg_info, dict) else None
    agg_bull_ok = True if agg_5d is None else (agg_5d > 0)
    agg_bear_ok = True if agg_5d is None else (agg_5d < 0)

    if (
        price_5d_pct <= -3.0
        and top3_5d > 0
        and agg_bull_ok
        and amt_ok
        and (top3_5d_ctrl_pct >= p["top3_ctrl_mod_pct"] or (top3_5d_ctrl_z is not None and top3_5d_ctrl_z >= p["z_bull_min"]))
        and (conc_5d is not None and conc_5d >= p["conc_mod_pct"])
    ):
        return {
            "type": "bull",
            "title": "🧲 價跌籌碼收（多方底背離）",
            "desc": (
                f"近 5 日股價回檔 {price_5d_pct:+.2f}%，但全體波段分點與三大主力同步買超"
                f"且全市場 5 日集中度達 {conc_5d:+.2f}%。"
                "<br>代表大資金趁拉回承接籌碼，可留意下方承接力道與止穩訊號。"
            ),
        }

    if (
        price_5d_pct >= 3.0
        and (top3_5d < 0 or (agg_5d is not None and agg_5d < 0))
        and agg_bear_ok
        and (conc_5d is not None and conc_5d <= -p["conc_mod_pct"])
    ):
        return {
            "type": "bear",
            "title": "⚠️ 價漲籌碼散（空方頂背離）",
            "desc": (
                f"近 5 日股價上漲 {price_5d_pct:+.2f}%，但全體波段分點逢高調節"
                f"且全市場 5 日集中度轉負至 {conc_5d:+.2f}%。"
                "<br>代表大資金正趁拉抬派發籌碼，追高風險顯著升高。"
            ),
        }

    return None


def evaluate_chip_scenario(
    top3_5d: int,
    top3_5d_ctrl_pct: float,
    latest_close: float,
    conc: dict,
    top3_5d_amount_ntd: float = 0.0,
    top3_5d_ctrl_z: float | None = None,
    top3_5d_float_pct: float | None = None,
    trust_info: dict | None = None,
    breadth_info: dict | None = None,
    broker_diff_info: dict | None = None,
    margin_info: dict | None = None,
    sbl_info: dict | None = None,
    tdcc_info: dict | None = None,
    day_trade_info: dict | None = None,
    vol_5d: float = 0.0,
    domestic_top_buy_5d: int = 0,
    wave_agg_info: dict | None = None,
) -> dict:
    """
    100% 純籌碼「趨勢 (60%) ＋ 動能 (40%)」雙層健康評分引擎（基準 50 分，總分 0～100）：
    一、中線趨勢面 (±30 分，佔 60%)：
        1. 全體波段分點合力趨勢 (±14)：20日控盤率/Z-Score (±8) ＋ 累計庫存相對20日均線與60日斜率 (±6)
        2. 中線集中度 10/20日 (±8)：20日集中度 (±5) ＋ 10日集中度 (±3)
        3. 中長線法人與大戶 (±8)：投信 20日動向與持股水位 (±4) ＋ TDCC 集保大戶週變化 (±4)
    二、短線動能面 (±20 分，佔 40%)：
        4. 短線集中度與廣度 (±10)：5日集中度 (±4) ＋ 主力分點廣度比含外資共識 (±6)
        5. 信用交易與情境借券 (±10)：融資四象限 (±5) ＋ 情境修正借券賣出 (±5，空頭背景回補改判 0 分)
    三、當日隔日沖灌水濾網（偏多時單向扣分 0 ～ -10 分）
    """
    _ = (latest_close, broker_diff_info, vol_5d, top3_5d_amount_ntd, top3_5d_float_pct)
    p = STRATEGY_PARAMS
    badges = []

    c5 = (conc or {}).get("d5", {}).get("pct")
    c5_z = (conc or {}).get("d5", {}).get("z_score")
    c10 = (conc or {}).get("d10", {}).get("pct")
    c20 = (conc or {}).get("d20", {}).get("pct")

    # =========================================================
    # 一、中線趨勢面 (±30 分，佔 60%)
    # =========================================================
    trend_wave_score = 0
    wave_20d_net = None
    wave_20d_ctrl = None
    wave_above_ma20 = None

    if isinstance(wave_agg_info, dict):
        wave_20d_net = int(wave_agg_info.get("net_20d", 0) or 0)
        wave_20d_ctrl = float(wave_agg_info.get("ctrl_20d_pct", 0.0) or 0.0)
        wave_60d_ctrl = float(wave_agg_info.get("ctrl_60d_pct", 0.0) or 0.0)
        wave_20d_z = wave_agg_info.get("ctrl_20d_z")
        wave_above_ma20 = wave_agg_info.get("cum_above_ma20")
        wave_60d_net = int(wave_agg_info.get("net_60d", 0) or 0)

        if wave_20d_ctrl >= p["wave_agg_20d_strong_pct"] or (
            wave_20d_ctrl >= p["wave_agg_20d_mod_pct"] and wave_20d_z is not None and wave_20d_z >= p["z_bull_min"]
        ):
            trend_wave_score += 8
        elif wave_20d_ctrl >= p["wave_agg_20d_mod_pct"]:
            trend_wave_score += 5
        elif wave_20d_ctrl >= p["wave_agg_20d_mild_pct"]:
            trend_wave_score += 2
        elif wave_20d_ctrl <= -p["wave_agg_20d_strong_pct"] or (
            wave_20d_ctrl <= -p["wave_agg_20d_mod_pct"] and wave_20d_z is not None and wave_20d_z <= p["z_bear_max"]
        ):
            trend_wave_score -= 8
        elif wave_20d_ctrl <= -p["wave_agg_20d_mod_pct"]:
            trend_wave_score -= 5
        elif wave_20d_ctrl <= -p["wave_agg_20d_mild_pct"]:
            trend_wave_score -= 2

        if wave_above_ma20 is True:
            trend_wave_score += 3
        elif wave_above_ma20 is False:
            trend_wave_score -= 3

        if wave_60d_net > 0 and wave_60d_ctrl >= p["wave_agg_60d_mod_pct"]:
            trend_wave_score += 3
        elif wave_60d_net > 0:
            trend_wave_score += 1
        elif wave_60d_net < 0 and wave_60d_ctrl <= -p["wave_agg_60d_mod_pct"]:
            trend_wave_score -= 3
        elif wave_60d_net < 0:
            trend_wave_score -= 1

        if trend_wave_score >= 10:
            badges.append({
                "label": f"🌊 波段合力強多 (20日 {wave_20d_ctrl:+.1f}%)",
                "level": "bull",
                "detail": f"全體波段分點近 20 日合計 {wave_20d_net:+,} 張（控盤 {wave_20d_ctrl:+.1f}%），累計庫存站穩 20 日均線上方。",
            })
        elif trend_wave_score <= -10:
            badges.append({
                "label": f"🌊 波段合力急撤 (20日 {wave_20d_ctrl:+.1f}%)",
                "level": "bear",
                "detail": f"全體波段分點近 20 日合計 {wave_20d_net:+,} 張（控盤 {wave_20d_ctrl:+.1f}%），累計庫存跌破 20 日均線向下。",
            })
    else:
        if top3_5d > 0 and top3_5d_ctrl_pct >= p["top3_ctrl_strong_pct"]:
            trend_wave_score = 10
        elif top3_5d > 0 and top3_5d_ctrl_pct >= p["top3_ctrl_mod_pct"]:
            trend_wave_score = 5
        elif top3_5d < 0 and top3_5d_ctrl_pct <= -p["top3_ctrl_strong_pct"]:
            trend_wave_score = -10
        elif top3_5d < 0 and top3_5d_ctrl_pct <= -p["top3_ctrl_mod_pct"]:
            trend_wave_score = -5

    trend_wave_score = max(-14, min(14, trend_wave_score))

    # 1B. 中線集中度 10日／20日 (±8 分)
    trend_conc_score = 0
    if c20 is not None:
        if c20 >= p["conc_20d_strong_pct"]:
            trend_conc_score += 5
        elif c20 >= p["conc_20d_mod_pct"]:
            trend_conc_score += 3
        elif c20 > 0:
            trend_conc_score += 1
        elif c20 <= -p["conc_20d_strong_pct"]:
            trend_conc_score -= 5
        elif c20 <= -p["conc_20d_mod_pct"]:
            trend_conc_score -= 3
        elif c20 < 0:
            trend_conc_score -= 1

    if c10 is not None:
        if c10 >= p["conc_10d_strong_pct"]:
            trend_conc_score += 3
        elif c10 >= p["conc_10d_mod_pct"]:
            trend_conc_score += 2
        elif c10 > 0:
            trend_conc_score += 1
        elif c10 <= -p["conc_10d_strong_pct"]:
            trend_conc_score -= 3
        elif c10 <= -p["conc_10d_mod_pct"]:
            trend_conc_score -= 2
        elif c10 < 0:
            trend_conc_score -= 1

    if c5 is not None and c10 is not None and c20 is not None:
        if c5 > 0 and c10 > 0 and c20 > 0:
            badges.append({
                "label": "🔥 多週期集中共振",
                "level": "bull",
                "detail": f"全市場 5/10/20 日集中度同步為正（{c5:+.1f}% / {c10:+.1f}% / {c20:+.1f}%）。",
            })
        elif c5 < 0 and c10 < 0 and c20 < 0:
            badges.append({
                "label": "📉 多週期連續發散",
                "level": "bear",
                "detail": f"全市場 5/10/20 日集中度同步為負（{c5:+.1f}% / {c10:+.1f}% / {c20:+.1f}%）。",
            })

    trend_conc_score = max(-8, min(8, trend_conc_score))

    # 1C. 中長線法人與大戶：投信 20 日趨勢 (±4) ＋ TDCC 集保大戶 (±4)
    trust_score = 0
    if isinstance(trust_info, dict):
        t_streak = int(trust_info.get("streak", 0) or 0)
        t_d5 = int(trust_info.get("d5", 0) or 0)
        t_d20 = int(trust_info.get("d20", t_d5) or 0)
        t_hold = trust_info.get("holding_pct")

        if t_d20 > 0 and (t_streak >= 3 or t_d5 > 0):
            trust_score += 3
            if t_hold is not None and 0.5 <= t_hold <= 5.0:
                trust_score += 1
                badges.append({
                    "label": f"🎯 投信初升認養 (持股{t_hold:.1f}%)",
                    "level": "bull",
                    "detail": f"投信近 20 日淨買超 {t_d20:+,} 張且持股比率處於 0.5%～5% 甜蜜認養區間。",
                })
            elif t_hold is not None and t_hold >= 10.0:
                badges.append({
                    "label": f"⚠️ 投信高水位偏買 (持股{t_hold:.1f}%)",
                    "level": "warn",
                    "detail": f"投信持股比率已達 {t_hold:.1f}%，須留意波段結帳風險。",
                })
            elif t_streak >= 3:
                trust_score += 1
                badges.append({
                    "label": f"🏦 投信連買 {t_streak} 日",
                    "level": "bull",
                    "detail": f"投信近 20 日淨買超 {t_d20:+,} 張（近 5 日 {t_d5:+,} 張）。",
                })
        elif t_d20 > 0:
            trust_score += 2
        elif t_d20 < 0 and (t_streak <= -3 or t_d5 < 0):
            trust_score -= 3
            if t_hold is not None and t_hold >= 10.0:
                trust_score -= 1
                badges.append({
                    "label": f"🏦 投信高檔結帳 (持股{t_hold:.1f}%)",
                    "level": "bear",
                    "detail": f"投信持股比率達 {t_hold:.1f}% 且近 20 日賣超 {t_d20:+,} 張，高水位結帳賣壓明顯。",
                })
            elif t_streak <= -3:
                trust_score -= 1
                badges.append({
                    "label": f"🏦 投信連賣 {abs(t_streak)} 日",
                    "level": "bear",
                    "detail": f"投信近 20 日淨賣超 {t_d20:+,} 張（近 5 日 {t_d5:+,} 張）。",
                })
        elif t_d20 < 0:
            trust_score -= 2

    trust_score = max(-4, min(4, trust_score))

    tdcc_score = 0
    if isinstance(tdcc_info, dict):
        w1000_chg = tdcc_info.get("whale_1000_chg_1w")
        w400_chg = tdcc_info.get("whale_400_chg_1w")
        h_chg = tdcc_info.get("holders_chg_1w")

        if w1000_chg is not None:
            if w1000_chg >= 0.5 and (h_chg is None or h_chg < 0):
                tdcc_score = 4
                badges.append({
                    "label": f"🐋 集保大戶強吸 (+{w1000_chg:.2f}%)",
                    "level": "bull",
                    "detail": "千張大戶持股比率單週大增且總股東人數減少，歸戶籌碼強烈集中。",
                })
            elif w1000_chg >= 0.2 and (h_chg is None or h_chg < 0):
                tdcc_score = 3
                badges.append({
                    "label": f"🐋 集保大戶增持 (+{w1000_chg:.2f}%)",
                    "level": "bull",
                    "detail": "千張大戶比率上升且散戶總人數減少，確認籌碼往大戶集中。",
                })
            elif w1000_chg >= 0.2 or (w400_chg is not None and w400_chg >= 0.3 and (h_chg is not None and h_chg < 0)):
                tdcc_score = 1
            elif w1000_chg <= -0.5 and (h_chg is None or h_chg > 0):
                tdcc_score = -4
                badges.append({
                    "label": f"⚠️ 集保大戶大減 ({w1000_chg:+.2f}%)",
                    "level": "bear",
                    "detail": "千張大戶比率單週顯著下滑且股東人數增加，歸戶籌碼明顯發散。",
                })
            elif w1000_chg <= -0.2 and (h_chg is None or h_chg > 0):
                tdcc_score = -3
                badges.append({
                    "label": f"⚠️ 集保大戶流出 ({w1000_chg:+.2f}%)",
                    "level": "bear",
                    "detail": "千張大戶比率下滑且總股東人數增加。",
                })
            elif w1000_chg <= -0.2 or (w400_chg is not None and w400_chg <= -0.3 and (h_chg is not None and h_chg > 0)):
                tdcc_score = -1

    tdcc_score = max(-4, min(4, tdcc_score))
    trend_inst_score = max(-8, min(8, trust_score + tdcc_score))
    trend_score = max(-30, min(30, trend_wave_score + trend_conc_score + trend_inst_score))

    # =========================================================
    # 二、短線動能面 (±20 分，佔 40%)
    # =========================================================
    conc5_score = 0
    if c5 is not None:
        is_c5_strong_bull = (c5 >= p["conc_strong_pct"]) or (c5 > 0 and c5_z is not None and c5_z >= p["z_bull_min"])
        is_c5_strong_bear = (c5 <= -p["conc_strong_pct"]) or (c5 < 0 and c5_z is not None and c5_z <= p["z_bear_max"])
        if is_c5_strong_bull:
            conc5_score = 4
        elif c5 >= p["conc_mod_pct"]:
            conc5_score = 2
        elif c5 > 0:
            conc5_score = 1
        elif is_c5_strong_bear:
            conc5_score = -4
        elif c5 <= -p["conc_mod_pct"]:
            conc5_score = -2
        elif c5 < 0:
            conc5_score = -1

    breadth_score = 0
    few_holding_up = False
    breadth_valid = False
    b5_pct = None

    if isinstance(breadth_info, dict):
        b5_pct = breadth_info.get("breadth_5d")
        active_5d = int(breadth_info.get("active_5d", 0) or 0)
        buy_5d = int(breadth_info.get("buy_5d", 0) or 0)
        sell_5d = int(breadth_info.get("sell_5d", 0) or 0)
        f5_pct = breadth_info.get("foreign_breadth_5d")
        f5_active = int(breadth_info.get("foreign_active_5d", 0) or 0)
        f5_buy = int(breadth_info.get("foreign_buy_5d", 0) or 0)
        f5_sell = int(breadth_info.get("foreign_sell_5d", 0) or 0)
        high_streak = int(breadth_info.get("high_streak", 0) or 0)

        if b5_pct is not None and active_5d >= p["breadth_min_active"]:
            breadth_valid = True
            has_foreign_bull = (f5_pct is not None and f5_active >= p["foreign_breadth_min_active"] and f5_pct >= p["foreign_breadth_bull_pct"])
            has_foreign_bear = (f5_pct is not None and f5_active >= p["foreign_breadth_min_active"] and f5_pct <= p["foreign_breadth_bear_pct"])

            if c5 is not None and c5 > 0:
                if b5_pct >= p["breadth_bull_pct"]:
                    breadth_score += 4
                    badges.append({
                        "label": f"🧭 主力共識 ({b5_pct:.0f}%)",
                        "level": "bull",
                        "detail": f"近 5 日波段分點 {buy_5d} 買 / {sell_5d} 賣，多數大戶方向一致偏多。",
                    })
                    if has_foreign_bull:
                        breadth_score += 2
                        badges.append({
                            "label": f"🌐 外資齊買 ({f5_buy}買/{f5_sell}賣)",
                            "level": "bull",
                            "detail": f"近 5 日外資分點共識度達 {f5_pct:.0f}%，屬外資集體同步買超。",
                        })
                    elif high_streak >= p["breadth_streak_days"]:
                        breadth_score += 2
                        badges.append({
                            "label": f"🔥 廣度連強 {high_streak} 日",
                            "level": "bull",
                            "detail": f"追蹤波段分點已連續 {high_streak} 個交易日維持 60% 以上偏多共識。",
                        })
                elif b5_pct <= p["breadth_hold_warn_pct"]:
                    breadth_score -= 3
                    few_holding_up = True
                    badges.append({
                        "label": f"⚠️ 少數主力硬撐 ({b5_pct:.0f}%)",
                        "level": "warn",
                        "detail": f"5 日集中度雖為正 ({c5:+.1f}%)，但追蹤波段分點僅 {buy_5d} 買 / {sell_5d} 賣，多數大戶實為調節。",
                    })
            elif c5 is not None and c5 < 0:
                if b5_pct <= p["breadth_bear_pct"]:
                    breadth_score -= 4
                    badges.append({
                        "label": f"📉 主力集體撤退 ({b5_pct:.0f}%)",
                        "level": "bear",
                        "detail": f"近 5 日波段分點僅 {buy_5d} 買 / {sell_5d} 賣，搭配負集中度確認大戶全面撤離。",
                    })
                    if has_foreign_bear:
                        breadth_score -= 2
                        badges.append({
                            "label": f"🌐 外資齊賣 ({f5_buy}買/{f5_sell}賣)",
                            "level": "bear",
                            "detail": f"近 5 日外資分點多方共識僅 {f5_pct:.0f}%，呈現外資集體同步倒貨。",
                        })
            else:
                if b5_pct >= p["breadth_bull_pct"]:
                    breadth_score += 3
                elif b5_pct <= p["breadth_bear_pct"]:
                    breadth_score -= 3

    breadth_score = max(-6, min(6, breadth_score))
    mom_conc_breadth_score = max(-10, min(10, conc5_score + breadth_score))

    # 2B. 融資四象限 (±5 分) ＋ 情境修正借券賣出 (±5 分)
    margin_score = 0
    margin_quadrant = 0
    is_heavy_dt = bool((day_trade_info or {}).get("is_heavy_day_trade", False))

    if isinstance(margin_info, dict):
        m_5d = int(margin_info.get("chg_5d", 0) or 0)
        m_pct = float(margin_info.get("chg_5d_pct", 0.0) or 0.0)
        m_z = margin_info.get("chg_5d_z")
        m_vol_pct = margin_info.get("chg_5d_vol_pct")
        sm_ratio = float(margin_info.get("short_margin_ratio", 0.0) or 0.0)
        dom_buy = int(margin_info.get("domestic_top_buy_5d", domestic_top_buy_5d) or 0)

        m_vol_ok = True if m_vol_pct is None else (m_vol_pct >= p["credit_vol_ratio_min"])
        m_surge = m_vol_ok and m_5d > 0 and (m_pct >= p["margin_surge_5d_pct"] or (m_z is not None and m_z >= p["z_bull_min"]))
        m_drop = m_vol_ok and m_5d < 0 and (m_pct <= p["margin_clean_5d_pct"] or (m_z is not None and m_z <= p["z_bear_max"]))

        if m_surge:
            dom_support = (dom_buy >= m_5d * p["margin_dom_buy_ratio"]) or (breadth_valid and b5_pct >= p["margin_quad_bull_breadth"])
            if (c5 is not None and c5 > 0) and dom_support and (not is_heavy_dt):
                margin_quadrant = 1
                margin_score = 3
                badges.append({
                    "label": f"🔥 主力融資點火 (5日 {m_5d:+,}張)",
                    "level": "bull",
                    "detail": f"融資增加 {m_pct:+.1f}% 且配合正集中度與本土波段買盤／高廣度，研判為大戶槓桿點火。",
                })
            else:
                margin_quadrant = 2
                margin_score = -5
                badges.append({
                    "label": f"⚠️ 散戶融資過熱 (5日 {m_5d:+,}張)",
                    "level": "bear",
                    "detail": f"融資大增 {m_pct:+.1f}%，但集中度轉負或主力廣度不足，籌碼流向高槓桿散戶。",
                })
        elif m_drop:
            if (c5 is not None and c5 > 0) or (breadth_valid and b5_pct >= p["margin_quad_clean_breadth"]):
                margin_quadrant = 3
                margin_score = 5
                badges.append({
                    "label": f"🧹 融資清洗 (5日 {m_5d:+,}張)",
                    "level": "bull",
                    "detail": f"融資減肥 {m_pct:+.1f}% 同時伴隨正集中度或高主力廣度，屬典型散戶停損、主力承接。",
                })
            elif (c5 is not None and c5 < 0) and ((not breadth_valid) or b5_pct <= p["margin_quad_bear_breadth"]):
                margin_quadrant = 4
                margin_score = -3
                badges.append({
                    "label": f"💨 融資退場發散 (5日 {m_5d:+,}張)",
                    "level": "bear",
                    "detail": f"融資減少 {m_pct:+.1f}% 且集中度與廣度同步偏弱，原主力融資結帳退場。",
                })

        if sm_ratio >= p["short_margin_squeeze_pct"]:
            badges.append({
                "label": f"⚡ 高券資比 ({sm_ratio:.1f}%)",
                "level": "info",
                "detail": f"券資比達 {sm_ratio:.1f}%，若籌碼持續集中易形成軋空助燃條件。",
            })

    sbl_score = 0
    sbl_mode = "neutral"
    if isinstance(sbl_info, dict):
        sbl_5d = int(sbl_info.get("chg_5d", 0) or 0)
        sbl_pct = float(sbl_info.get("chg_5d_pct", 0.0) or 0.0)
        sbl_z = sbl_info.get("chg_5d_z")
        sbl_vol_pct = sbl_info.get("chg_5d_vol_pct")
        sbl_vol_ok = True if sbl_vol_pct is None else (sbl_vol_pct >= p["credit_vol_ratio_min"])

        is_bear_chip_context = (trend_wave_score <= -3) or (
            c5 is not None and c5 < 0 and (c10 is None or c10 < 0)
        )

        if sbl_vol_ok and sbl_5d < 0 and (sbl_pct <= p["sbl_cover_5d_pct"] or (sbl_z is not None and sbl_z <= p["z_bear_max"])):
            if is_bear_chip_context:
                sbl_score = 0
                sbl_mode = "profit_cover"
                badges.append({
                    "label": f"ℹ️ 借券獲利回補 (5日 {sbl_5d:+,}張)",
                    "level": "info",
                    "detail": "借券賣出餘額雖下降，但波段分點與集中度仍偏空，研判為空單順勢獲利回補（不予多方加分）。",
                })
            else:
                sbl_score = 5
                sbl_mode = "bull_cover"
                badges.append({
                    "label": f"🚀 借券回補 (5日 {sbl_5d:+,}張/{sbl_pct:+.1f}%)",
                    "level": "bull",
                    "detail": "偏多或止穩籌碼背景下借券餘額顯著減少，空方認輸回補提供籌碼支撐。",
                })
        elif sbl_vol_ok and sbl_5d > 0 and (sbl_pct >= p["sbl_surge_5d_pct"] or (sbl_z is not None and sbl_z >= p["z_bull_min"])):
            sbl_mode = "bear_surge"
            if trend_wave_score >= 6 and c5 is not None and c5 > 0:
                sbl_score = -2
                badges.append({
                    "label": f"⚡ 偏多對作借券增 (5日 {sbl_5d:+,}張)",
                    "level": "warn",
                    "detail": "波段籌碼偏多但借券空單增加，屬多空對作或避險鎖單（減輕扣分，留意軋空）。",
                })
            else:
                sbl_score = -5
                badges.append({
                    "label": f"🐻 借券空單加壓 (5日 {sbl_5d:+,}張/{sbl_pct:+.1f}%)",
                    "level": "bear",
                    "detail": "外資／法人借券賣出餘額顯著增加，空方放空壓力升溫。",
                })

    mom_credit_sbl_score = max(-10, min(10, margin_score + sbl_score))
    momentum_score = max(-20, min(20, mom_conc_breadth_score + mom_credit_sbl_score))

    # =========================================================
    # 三、當日隔日沖灌水濾網（偏多時單向扣分 0 ～ -10 分）
    # =========================================================
    raw_before_dt = 50 + trend_score + momentum_score
    dt_score = 0
    dt_vol_pct = float((day_trade_info or {}).get("dt_vol_pct", 0.0) or 0.0)
    dt_buyers = (day_trade_info or {}).get("dt_buyers") or []
    is_bull_biased = (raw_before_dt > 50) or (c5 is not None and c5 > 0) or (trend_wave_score > 0)

    if is_bull_biased and dt_vol_pct >= p["dt_today_vol_warn_pct"]:
        if dt_vol_pct >= p["dt_today_vol_high_pct"]:
            dt_score = -10
        elif dt_vol_pct >= p["dt_today_vol_mid_pct"]:
            dt_score = -6
        else:
            dt_score = -3
        dt_names_txt = "、".join(dt_buyers[:3])
        badges.append({
            "label": f"⚡ 隔日沖介入 ({dt_vol_pct:.1f}%)",
            "level": "warn",
            "detail": f"當日隔日沖熱點（{dt_names_txt}）買超佔成交量 {dt_vol_pct:.1f}%，提防隔日開高倒貨賣壓。",
        })

    conviction_score = int(max(0, min(100, raw_before_dt + dt_score)))

    # =========================================================
    # 判定 5 大純籌碼結構狀態（做多門檻 ≥ 55 分，空方門檻 ≤ 35 分）
    # =========================================================
    bull_min = p["bull_score_min"]
    strong_bull_min = p["strong_bull_score_min"]
    bear_max = p["bear_score_max"]

    trend_mom_conflict = (
        (trend_score >= 8 and momentum_score <= -8)
        or (trend_score <= -8 and momentum_score >= 8)
    )
    heavy_dt_distortion = dt_score <= -6

    c5_txt = f"{c5:+.1f}%" if c5 is not None else "N/A"
    c10_txt = f"{c10:+.1f}%" if c10 is not None else "N/A"
    c20_txt = f"{c20:+.1f}%" if c20 is not None else "N/A"
    b5_txt = f"主力廣度 {b5_pct:.0f}%" if breadth_valid and b5_pct is not None else "主力廣度累積中"
    if wave_20d_net is not None and wave_20d_ctrl is not None:
        ma_txt = "站上20日均線" if wave_above_ma20 is True else ("跌破20日均線" if wave_above_ma20 is False else "貼近均線")
        wave_txt = f"全體波段分點 20 日合計 {wave_20d_net:+,} 張（控盤 {wave_20d_ctrl:+.1f}%／庫存{ma_txt}）"
    else:
        wave_txt = f"三大波段主力近 5 日淨額 {top3_5d:+,} 張（控盤 {top3_5d_ctrl_pct:+.1f}%）"

    summary_prefix = (
        f"{wave_txt}；全市場集中度 5/10/20 日分別為 {c5_txt} / {c10_txt} / {c20_txt}，{b5_txt}"
    )

    if conviction_score >= strong_bull_min and trend_score > 0 and (not few_holding_up):
        sc_id = 1
        sc_type = "bull"
        sub_type = "波段強多共振"
        title = "🔥 純籌碼狀態：1. 波段趨勢強多共振（≥70 分）"
        desc = f"{summary_prefix}，中線波段趨勢（{trend_score:+d}）與短線動能（{momentum_score:+d}）同步偏多。"
        action = "趨勢與動能高度共振，屬主升鎖碼格局；短線乖離偏大時宜等拉回或分批操作。"
    elif conviction_score <= bear_max or (trend_score <= -12 and conviction_score < 45):
        sc_id = 5
        sc_type = "bear"
        sub_type = "波段全面發散"
        title = "🟢 純籌碼狀態：5. 波段全面發散（≤35 分｜大戶撤退）"
        desc = f"{summary_prefix}，中線趨勢（{trend_score:+d}）與短線動能（{momentum_score:+d}）明顯偏空發散。"
        action = "波段主力持續撤離且籌碼流向散戶，應避開做多；若股價跌破 20MA 則符合偏空條件。"
    elif few_holding_up or trend_mom_conflict or heavy_dt_distortion:
        sc_id = 4
        sc_type = "neutral"
        sub_type = "籌碼分歧"
        title = "⚡ 純籌碼狀態：4. 籌碼分歧（趨勢動能衝突／少數硬撐）"
        desc = f"{summary_prefix}，但中線趨勢（{trend_score:+d}）與短線動能（{momentum_score:+d}）或隔日沖存在明顯矛盾。"
        action = "表面集中度與全體波段共識不一致，須提防少數主力硬撐失敗或隔日沖短線獲利了結。"
    elif conviction_score >= bull_min:
        sc_id = 2
        sc_type = "bull"
        sub_type = "偏多甜蜜區"
        title = "📈 純籌碼狀態：2. 偏多吸籌／進場甜蜜區（55～69 分）"
        desc = f"{summary_prefix}，已站上 55 分做多門檻（趨勢 {trend_score:+d} ／ 動能 {momentum_score:+d}）。"
        action = "籌碼已轉向大戶集中且尚未短線過熱，搭配股價站穩 20MA 時為高勝率切入區間。"
    else:
        sc_id = 3
        sc_type = "neutral"
        sub_type = "籌碼中性"
        title = "⚪ 純籌碼狀態：3. 籌碼中性／換手觀望（36～54 分）"
        desc = f"{summary_prefix}，多空力道大致平衡（趨勢 {trend_score:+d} ／ 動能 {momentum_score:+d}）。"
        action = "大資金尚未展現單邊波段傾向，宜等待突破 55 分偏多或跌破 35 分偏空表態。"

    return {
        "id": sc_id,
        "type": sc_type,
        "sub_type": sub_type,
        "title": title,
        "desc": desc,
        "action": action,
        "badges": badges,
        "conviction_score": conviction_score,
        "score_breakdown": {
            "base": 50,
            "trend_total": trend_score,
            "trend_wave": trend_wave_score,
            "trend_conc": trend_conc_score,
            "trend_inst": trend_inst_score,
            "momentum_total": momentum_score,
            "mom_conc_breadth": mom_conc_breadth_score,
            "mom_credit_sbl": mom_credit_sbl_score,
            "day_trade": dt_score,
            "conc_breadth": mom_conc_breadth_score,
            "conc_diff": mom_conc_breadth_score,
            "wave_trust": trend_wave_score + trust_score,
            "credit_sbl": mom_credit_sbl_score,
            "tdcc": tdcc_score,
        },
        "margin_quadrant": margin_quadrant,
        "sbl_mode": sbl_mode,
        "few_holding_up": few_holding_up,
    }


def compute_score_backtest(
    df_price_full: pd.DataFrame,
    df_health: pd.DataFrame,
    horizons: tuple[int, ...] = (5, 10, 20),
) -> dict:
    """
    計算今年度「純籌碼健康分數區間」與「進場濾網實戰對照」回測統計（5 / 10 / 20 個交易日）：
    1. 分數區間表：≥70、55~69、36~54、≤35 分
    2. 進場濾網對照表（做多門檻 55 分、放空門檻 35 分）：
       - 基準做多 (≥55) vs. 趨勢濾網做多 (≥55 且收盤 > 20MA)
       - 甜蜜區濾網做多 (55~69 且收盤 > 20MA) vs. 啟動突破做多 (<55 首破 ≥55，5日冷卻)
       - 基準放空 (≤35) vs. 趨勢濾網放空 (≤35 且收盤 < 20MA)
    """
    empty_res = {"rows": [], "filter_rows": [], "total_scored_days": 0, "verdict": ""}
    if df_price_full is None or df_price_full.empty or df_health is None or df_health.empty:
        return empty_res

    bull_min = STRATEGY_PARAMS["bull_score_min"]
    strong_bull_min = STRATEGY_PARAMS["strong_bull_score_min"]
    bear_max = STRATEGY_PARAMS["bear_score_max"]

    df = (
        df_price_full[["date", "close"]]
        .sort_values("date")
        .merge(df_health[["date", "health_score"]], on="date", how="left")
        .reset_index(drop=True)
    )
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["health_score"] = pd.to_numeric(df["health_score"], errors="coerce")
    df["ma20"] = df["close"].rolling(20).mean()
    df["prev_score"] = df["health_score"].shift(1)

    for h in horizons:
        df[f"fwd_{h}d"] = (df["close"].shift(-h) / df["close"] - 1.0) * 100.0

    # 計算「由 <55 首次突破 ≥55」啟動點（5 個交易日冷卻避免重複計數）
    breakout_mask = np.zeros(len(df), dtype=bool)
    last_bo_idx = -999
    for idx in range(len(df)):
        sc_now = df.loc[idx, "health_score"]
        sc_prev = df.loc[idx, "prev_score"]
        if pd.notna(sc_now) and pd.notna(sc_prev) and sc_now >= bull_min and sc_prev < bull_min:
            if idx - last_bo_idx >= 5:
                breakout_mask[idx] = True
                last_bo_idx = idx
    df["is_bull_breakout"] = breakout_mask

    scored = df.dropna(subset=["health_score"]).copy()
    if scored.empty:
        return empty_res

    # --- 1. 分數區間表 ---
    buckets = [
        (f"≥ {strong_bull_min} 分（強力共振）", "bull", lambda d: d["health_score"] >= strong_bull_min),
        (f"{bull_min} ～ {strong_bull_min - 1} 分（偏多甜蜜區）", "warn", lambda d: (d["health_score"] >= bull_min) & (d["health_score"] < strong_bull_min)),
        (f"{bear_max + 1} ～ {bull_min - 1} 分（中性分歧）", "info", lambda d: (d["health_score"] > bear_max) & (d["health_score"] < bull_min)),
        (f"≤ {bear_max} 分（明顯偏空）", "bear", lambda d: d["health_score"] <= bear_max),
    ]

    rows = []
    for label, level, cond_fn in buckets:
        sub = scored[cond_fn(scored)]
        row_item = {
            "label": label,
            "level": level,
            "days": int(len(sub)),
            "stats": {},
        }
        for h in horizons:
            rets = sub[f"fwd_{h}d"].dropna()
            cnt = int(len(rets))
            if cnt > 0:
                long_wr = round(float((rets > 0).mean()) * 100.0, 1)
                short_wr = round(float((rets < 0).mean()) * 100.0, 1)
                avg_ret = round(float(rets.mean()), 2)
            else:
                long_wr = short_wr = avg_ret = None
            row_item["stats"][h] = {
                "count": cnt,
                "long_wr": long_wr,
                "short_wr": short_wr,
                "avg_ret": avg_ret,
            }
        rows.append(row_item)

    # --- 2. 進場濾網實戰對照表 ---
    filter_specs = [
        (
            f"基準做多：分數 ≥ {bull_min}（無濾網）",
            "long",
            "bull",
            lambda d: d["health_score"] >= bull_min,
        ),
        (
            f"趨勢濾網做多：分數 ≥ {bull_min} ＋ 收盤 > 20MA",
            "long",
            "bull",
            lambda d: (d["health_score"] >= bull_min) & d["ma20"].notna() & (d["close"] > d["ma20"]),
        ),
        (
            f"甜蜜區做多：分數 {bull_min}～{strong_bull_min - 1} ＋ 收盤 > 20MA",
            "long",
            "warn",
            lambda d: (d["health_score"] >= bull_min) & (d["health_score"] < strong_bull_min) & d["ma20"].notna() & (d["close"] > d["ma20"]),
        ),
        (
            f"啟動突破做多：由 <{bull_min} 首次站上 ≥ {bull_min}（5日冷卻）",
            "long",
            "warn",
            lambda d: d["is_bull_breakout"],
        ),
        (
            f"基準放空：分數 ≤ {bear_max}（無濾網）",
            "short",
            "bear",
            lambda d: d["health_score"] <= bear_max,
        ),
        (
            f"趨勢濾網放空：分數 ≤ {bear_max} ＋ 收盤 < 20MA",
            "short",
            "bear",
            lambda d: (d["health_score"] <= bear_max) & d["ma20"].notna() & (d["close"] < d["ma20"]),
        ),
    ]

    filter_rows = []
    for label, direction, level, cond_fn in filter_specs:
        sub = scored[cond_fn(scored)]
        f_item = {
            "label": label,
            "direction": direction,
            "level": level,
            "days": int(len(sub)),
            "stats": {},
        }
        for h in horizons:
            rets = sub[f"fwd_{h}d"].dropna()
            cnt = int(len(rets))
            if cnt > 0:
                if direction == "long":
                    win_rate = round(float((rets > 0).mean()) * 100.0, 1)
                    strat_ret = round(float(rets.mean()), 2)
                else:
                    win_rate = round(float((rets < 0).mean()) * 100.0, 1)
                    strat_ret = round(float((-rets).mean()), 2)
                raw_ret = round(float(rets.mean()), 2)
            else:
                win_rate = strat_ret = raw_ret = None
            f_item["stats"][h] = {
                "count": cnt,
                "win_rate": win_rate,
                "strat_ret": strat_ret,
                "raw_ret": raw_ret,
            }
        filter_rows.append(f_item)

    # --- 3. 產生客觀實證摘要（對照無濾網 vs 20MA 趨勢濾網） ---
    verdict_parts = []
    ref_h = 20 if (filter_rows[0]["stats"][20]["count"] >= 3 or filter_rows[4]["stats"][20]["count"] >= 3) else 10

    long_base = filter_rows[0]["stats"][ref_h]
    long_ma = filter_rows[1]["stats"][ref_h]
    if long_base["count"] >= 3 and long_base["strat_ret"] is not None:
        base_txt = f"≥{bull_min}分做多 {ref_h}日勝率 {long_base['win_rate']:.1f}%（均報 {long_base['strat_ret']:+.2f}%）"
        if long_ma["count"] >= 3 and long_ma["strat_ret"] is not None:
            base_txt += f" → 加20MA濾網 {long_ma['win_rate']:.1f}%（{long_ma['strat_ret']:+.2f}%）"
        verdict_parts.append(base_txt)

    short_base = filter_rows[4]["stats"][ref_h]
    short_ma = filter_rows[5]["stats"][ref_h]
    if short_base["count"] >= 3 and short_base["strat_ret"] is not None:
        s_txt = f"≤{bear_max}分放空 {ref_h}日勝率 {short_base['win_rate']:.1f}%（空單均報 {short_base['strat_ret']:+.2f}%）"
        if short_ma["count"] >= 3 and short_ma["strat_ret"] is not None:
            s_txt += f" → 加20MA濾網 {short_ma['win_rate']:.1f}%（{short_ma['strat_ret']:+.2f}%）"
        verdict_parts.append(s_txt)

    return {
        "rows": rows,
        "filter_rows": filter_rows,
        "total_scored_days": int(len(scored)),
        "verdict": " ｜ ".join(verdict_parts),
    }


def build_z_tooltip_html(stock_title: str, ctrl5: float, z5: float | None) -> str:
    """建立「三大波段主力近 5 日控盤率與 60 日 Z-Score」懸浮說明 HTML"""
    z_line = (
        f"目前 <b>{stock_title}</b> 近 5 日三大波段主力控盤率為 <b>{ctrl5:+.1f}%</b>，"
        f"對應過去 60 日滾動 Z-Score 為 <b>{z5:+.2f}σ</b>。"
        if z5 is not None
        else f"目前 <b>{stock_title}</b> 近 5 日三大波段主力控盤率為 <b>{ctrl5:+.1f}%</b>（Z-Score 樣本累積中）。"
    )
    return (
        f"{z_line}<br><br>"
        "<b>為什麼要搭配 60 日 Z-Score？</b><br>"
        "不同個股成交量體質差異極大，透過標準分數可自動校準該檔股票的「平常波動尺度」：<br>"
        "• <b>Z ≥ +1.0σ：</b>主力近 5 日買超力道顯著高於過去 60 日常態（強勢吸籌）。<br>"
        "• <b>-1.0σ &lt; Z &lt; +1.0σ：</b>屬於該股日常正常換手波動。<br>"
        "• <b>Z ≤ -1.0σ：</b>主力近 5 日賣超力道顯著異常（積極出貨）。"
    )
