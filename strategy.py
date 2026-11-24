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

# 100% 純籌碼策略參數集中管理
STRATEGY_PARAMS = {
    # 滾動視窗與 Z-Score 門檻
    "wave_lookback_days": 60,
    "zscore_window": 60,
    "z_bull_min": 1.0,
    "z_bear_max": -1.0,

    # 集中度與波段三大主力門檻
    "conc_strong_pct": 10.0,
    "conc_mod_pct": 3.0,
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

    # 信用交易（融資四象限與借券賣出）門檻與相對量能防呆
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

# 純籌碼結構 5 大狀態判讀矩陣（100% 撇除均線與漲跌）
SCENARIO_MATRIX = [
    {
        "id": 1,
        "type": "bull",
        "name": "🔥 1. 全面共振吸籌",
        "main_cond": "60日三大波段主力近 5 日積極買超（控盤 ≥ 2% 或 Z ≥ +1.0σ）",
        "conc_cond": "5日集中度為正（且多週期偏多）＋ 主力廣度 ≥ 65%（大戶齊心／外資共識）",
        "aux_cond": "投信連買、融資清洗或點火、借券回補或集保大戶增持；純籌碼分數 ≥ 70",
        "action": "點、面、輔助籌碼高度共振，屬籌碼最紮實的多方鎖碼或主升建倉結構。",
    },
    {
        "id": 2,
        "type": "bull",
        "name": "📈 2. 隱性吸籌／洗盤沉澱",
        "main_cond": "波段主力溫和買超或鎖碼未出，投信或特定贏家分點開始低檔承接",
        "conc_cond": "集中度溫和偏正或轉強，主力廣度未跌破 40%（無少數硬撐跡象）",
        "aux_cond": "常見融資清洗（散戶退場）或借券回補；純籌碼分數 55 ～ 69",
        "action": "籌碼正逐步沉澱集中，但尚未全面爆發共振，可列入觀察並追蹤廣度與集中度是否續強。",
    },
    {
        "id": 3,
        "type": "neutral",
        "name": "⚪ 3. 籌碼中性／換手觀望",
        "main_cond": "波段主力進出幅度不大，或買賣力道接近日常波動（|Z| < 1.0σ）",
        "conc_cond": "集中度介於 -3% ～ +3%，主力廣度落於 40% ～ 65% 中性區間",
        "aux_cond": "信用交易、借券與投信方向不明顯或彼此抵銷；純籌碼分數 36 ～ 54",
        "action": "籌碼處於正常換手與平衡狀態，缺乏明確大資金方向，宜等待新主導力量表態。",
    },
    {
        "id": 4,
        "type": "neutral",
        "name": "⚡ 4. 籌碼分歧／少數硬撐",
        "main_cond": "三大波段主力與全市場集中度方向相反，或當日隔日沖買盤佔比過高（≥ 8%）",
        "conc_cond": "集中度為正但主力廣度 ≤ 40%（少數大戶硬撐、多數大戶偷跑），或外資與本土對作",
        "aux_cond": "可能伴隨散戶融資過熱或高水位投信逢高調節；籌碼內部存在矛盾",
        "action": "表面數據與內部廣度出現背離，須提防隔日沖獲利了結或單一主力撐盤失敗風險。",
    },
    {
        "id": 5,
        "type": "bear",
        "name": "🟢 5. 全面發散（大戶撤退）",
        "main_cond": "波段主力明顯轉賣或無 60 日買超主力，投信連賣調節",
        "conc_cond": "5日集中度轉負（≤ -3% 或 Z ≤ -1.0σ）且主力廣度 ≤ 35%（大戶／外資集體撤退）",
        "aux_cond": "常見散戶融資過熱、借券空單加壓或集保大戶流出；純籌碼分數 ≤ 35",
        "action": "籌碼結構明確偏空發散，大資金持續撤離且流向散戶，應避開或待集中度與廣度重新收斂。",
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
    - 統計近 1/5/10/60 日與今年累計淨買賣超
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
                    # 排除極微小雜訊單（至少佔當日量 0.2% 或達 30 張）
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


def compute_broker_breadth(
    df_chips_full: pd.DataFrame,
    profiles: dict | None = None,
    lookback_days: int = 5,
) -> dict | None:
    """
    計算「主力分點廣度比（含外資分點共識度）」：
    - 排除「投信」與隔日沖分點，統計追蹤波段分點中「淨買超家數 ÷ (買超家數 + 賣超家數)」
    - 獨立拆出外資分點近 5 日買賣家數與共識比率，快速辨識外資集體買賣或對作
    - 同步計算整體波段分點廣度連續 ≥ 60%（高共識）或 ≤ 40%（低共識）之天數
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

    # 計算逐日廣度連續高檔 (≥ 60%) / 低檔 (≤ 40%) 天數
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
    """
    判定投信水位階段與短線動向：
    - 動向標籤嚴格依買超紅 (bull)、賣超綠 (bear)、觀望灰 (info) 呈現
    - 持股比率 0.5% ~ 5.0% 且偏買時標記為「初升認養甜蜜期」；>= 10% 標記為高水位警戒
    """
    if not isinstance(trust_info, dict):
        return {"tier_label": "", "tier_level": "info", "momentum": "", "momentum_level": "info"}

    streak = int(trust_info.get("streak", 0) or 0)
    d5 = int(trust_info.get("d5", 0) or 0)
    hold_pct = trust_info.get("holding_pct")

    # 1. 短線動向標籤
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

    # 2. 真實持股比率水位標籤
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
) -> dict | None:
    """
    價量籌碼背離視覺警示條（純視覺參考，不介入純籌碼健康分數計算）：
    - 股價近 5 日顯著下跌但主力與集中度逆勢強吸 → 底背離提醒
    - 股價近 5 日顯著上漲但主力與集中度逆勢倒貨 → 頂背離提醒
    """
    _ = (latest_close, trust_info, margin_info, sbl_info)
    p = STRATEGY_PARAMS
    amt_ok = (top3_5d_amount_ntd == 0.0) or (abs(top3_5d_amount_ntd) >= p["top3_min_amount_ntd"] * 0.5)

    # 多方底背離：價跌籌碼收
    if (
        price_5d_pct <= -3.0
        and top3_5d > 0
        and amt_ok
        and (top3_5d_ctrl_pct >= p["top3_ctrl_mod_pct"] or (top3_5d_ctrl_z is not None and top3_5d_ctrl_z >= p["z_bull_min"]))
        and (conc_5d is not None and conc_5d >= p["conc_mod_pct"])
    ):
        return {
            "type": "bull",
            "title": "🧲 價跌籌碼收（多方底背離）",
            "desc": (
                f"近 5 日股價回檔 {price_5d_pct:+.2f}%，但三大波段主力逆勢買超 {top3_5d:+,} 張"
                f"（控盤 {top3_5d_ctrl_pct:+.1f}%）且全市場 5 日集中度達 {conc_5d:+.2f}%。"
                "<br>代表大資金趁拉回承接籌碼，可留意下方承接力道與止穩訊號。"
            ),
        }

    # 空方頂背離：價漲籌碼散
    if (
        price_5d_pct >= 3.0
        and top3_5d < 0
        and amt_ok
        and (top3_5d_ctrl_pct <= -p["top3_ctrl_mod_pct"] or (top3_5d_ctrl_z is not None and top3_5d_ctrl_z <= p["z_bear_max"]))
        and (conc_5d is not None and conc_5d <= -p["conc_mod_pct"])
    ):
        return {
            "type": "bear",
            "title": "⚠️ 價漲籌碼散（空方頂背離）",
            "desc": (
                f"近 5 日股價上漲 {price_5d_pct:+.2f}%，但三大波段主力逢高出貨 {top3_5d:+,} 張"
                f"（控盤 {top3_5d_ctrl_pct:+.1f}%）且全市場 5 日集中度轉負至 {conc_5d:+.2f}%。"
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
) -> dict:
    """
    100% 純籌碼五層健康評分引擎（基準 50 分，總分 0～100，完全排除均線與漲跌）：
    1. 全市場前 15 大集中度 (±12) ＋ 主力分點廣度比含外資共識 (±6)  → 合計 ±18 分
    2. 60日三大波段主力 (±8) ＋ 投信真實水位與連買賣 (±6)          → 合計 ±14 分
    3. 融資四象限 (±5) ＋ 借券賣出 (±5)（皆含相對量能 ≥ 3% 濾網）  → 合計 ±10 分
    4. TDCC 集保大戶週歸戶驗證                                     → 合計 ±8 分
    5. 當日隔日沖灌水濾網（偏多時單向扣分）                        → 0 ～ -10 分
    """
    _ = (latest_close, broker_diff_info, vol_5d)
    p = STRATEGY_PARAMS
    badges = []

    c5 = (conc or {}).get("d5", {}).get("pct")
    c5_z = (conc or {}).get("d5", {}).get("z_score")
    c10 = (conc or {}).get("d10", {}).get("pct")
    c20 = (conc or {}).get("d20", {}).get("pct")

    # =========================================================
    # Layer 1A：全市場前 15 大籌碼集中度 (±12 分)
    # =========================================================
    conc_score = 0
    if c5 is not None:
        is_c5_strong_bull = (c5 >= p["conc_strong_pct"]) or (c5 > 0 and c5_z is not None and c5_z >= p["z_bull_min"])
        is_c5_strong_bear = (c5 <= -p["conc_strong_pct"]) or (c5 < 0 and c5_z is not None and c5_z <= p["z_bear_max"])

        if is_c5_strong_bull:
            conc_score += 8
        elif c5 >= p["conc_mod_pct"]:
            conc_score += 5
        elif c5 > 0:
            conc_score += 2
        elif is_c5_strong_bear:
            conc_score -= 8
        elif c5 <= -p["conc_mod_pct"]:
            conc_score -= 5
        elif c5 < 0:
            conc_score -= 2

        # 中期 (10日 / 20日) 同向共振加分
        if c5 > 0:
            if c10 is not None and c20 is not None and c10 > 0 and c20 > 0:
                conc_score += 4
                badges.append({
                    "label": "🔥 多週期集中共振",
                    "level": "bull",
                    "detail": f"全市場 5/10/20 日集中度同步為正（{c5:+.1f}% / {c10:+.1f}% / {c20:+.1f}%）。",
                })
            elif c10 is not None and c10 > 0:
                conc_score += 2
        elif c5 < 0:
            if c10 is not None and c20 is not None and c10 < 0 and c20 < 0:
                conc_score -= 4
                badges.append({
                    "label": "📉 多週期連續發散",
                    "level": "bear",
                    "detail": f"全市場 5/10/20 日集中度同步為負（{c5:+.1f}% / {c10:+.1f}% / {c20:+.1f}%）。",
                })
            elif c10 is not None and c10 < 0:
                conc_score -= 2

    conc_score = max(-12, min(12, conc_score))

    # =========================================================
    # Layer 1B：主力分點廣度比＋外資共識 (±6 分)
    # =========================================================
    breadth_score = 0
    few_holding_up = False
    breadth_valid = False
    b5_pct = None
    f5_pct = None
    f5_active = 0

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
                    badges.append({
                        "label": f"🧭 主力偏多共識 ({b5_pct:.0f}%)",
                        "level": "bull",
                        "detail": f"近 5 日波段分點 {buy_5d} 買 / {sell_5d} 賣。",
                    })
                elif b5_pct <= p["breadth_bear_pct"]:
                    breadth_score -= 3
                    badges.append({
                        "label": f"📉 主力偏空撤退 ({b5_pct:.0f}%)",
                        "level": "bear",
                        "detail": f"近 5 日波段分點僅 {buy_5d} 買 / {sell_5d} 賣。",
                    })

    breadth_score = max(-6, min(6, breadth_score))
    conc_breadth_score = max(-18, min(18, conc_score + breadth_score))

    # =========================================================
    # Layer 2：60日三大波段主力 (±8) ＋ 投信真實水位與動向 (±6)
    # =========================================================
    wave_score = 0
    amt_meaningful = (top3_5d_amount_ntd == 0.0) or (abs(top3_5d_amount_ntd) >= p["top3_min_amount_ntd"])
    z_bull = top3_5d_ctrl_z is not None and top3_5d_ctrl_z >= p["z_bull_min"]
    z_bear = top3_5d_ctrl_z is not None and top3_5d_ctrl_z <= p["z_bear_max"]

    if top3_5d > 0:
        if top3_5d_ctrl_pct >= p["top3_ctrl_strong_pct"] or (z_bull and top3_5d_ctrl_pct >= p["top3_ctrl_mod_pct"]):
            wave_score = 8 if amt_meaningful else 5
            badges.append({
                "label": f"🚀 三大主力強攻 ({top3_5d_ctrl_pct:+.1f}%)",
                "level": "bull",
                "detail": f"近 60 日波段三大主力近 5 日淨買超 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%）。",
            })
        elif top3_5d_ctrl_pct >= p["top3_ctrl_mod_pct"] or (top3_5d_float_pct is not None and top3_5d_float_pct >= 0.3):
            wave_score = 5 if amt_meaningful else 3
        elif top3_5d_ctrl_pct >= 0.5:
            wave_score = 2
    elif top3_5d < 0:
        if top3_5d_ctrl_pct <= -p["top3_ctrl_strong_pct"] or (z_bear and top3_5d_ctrl_pct <= -p["top3_ctrl_mod_pct"]):
            wave_score = -8 if amt_meaningful else -5
            badges.append({
                "label": f"🔻 三大主力急撤 ({top3_5d_ctrl_pct:+.1f}%)",
                "level": "bear",
                "detail": f"近 60 日波段三大主力近 5 日轉賣 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%）。",
            })
        elif top3_5d_ctrl_pct <= -p["top3_ctrl_mod_pct"] or (top3_5d_float_pct is not None and top3_5d_float_pct <= -0.3):
            wave_score = -5 if amt_meaningful else -3
        elif top3_5d_ctrl_pct <= -0.5:
            wave_score = -2

    trust_score = 0
    trust_bull = False
    if isinstance(trust_info, dict):
        t_streak = int(trust_info.get("streak", 0) or 0)
        t_d5 = int(trust_info.get("d5", 0) or 0)
        t_hold = trust_info.get("holding_pct")

        if t_streak >= 3 and t_d5 > 0:
            trust_bull = True
            trust_score += 4
            if t_hold is not None and 0.5 <= t_hold <= 5.0:
                trust_score += 2
                badges.append({
                    "label": f"🎯 投信初升認養 (連買{t_streak}日｜持股{t_hold:.1f}%)",
                    "level": "bull",
                    "detail": f"投信持股比率處於 0.5%～5% 甜蜜認養區間且連續買超 {t_streak} 日。",
                })
            elif t_hold is not None and t_hold >= 10.0:
                badges.append({
                    "label": f"⚠️ 投信高水位連買 (持股{t_hold:.1f}%)",
                    "level": "warn",
                    "detail": f"投信雖連買 {t_streak} 日，但持股比率已達 {t_hold:.1f}%，須留意結帳風險。",
                })
            else:
                if t_streak >= 5:
                    trust_score += 2
                badges.append({
                    "label": f"🏦 投信連買 {t_streak} 日",
                    "level": "bull",
                    "detail": f"投信近 5 日淨買超 {t_d5:+,} 張。",
                })
        elif t_d5 > 0 and t_streak >= 1:
            trust_bull = True
            trust_score += 2
        elif t_streak <= -3 and t_d5 < 0:
            trust_score -= 4
            if t_hold is not None and t_hold >= 10.0:
                trust_score -= 2
                badges.append({
                    "label": f"🏦 投信高檔結帳 (連賣{abs(t_streak)}日｜持股{t_hold:.1f}%)",
                    "level": "bear",
                    "detail": f"投信持股比率達 {t_hold:.1f}% 且連續賣超 {abs(t_streak)} 日，高水位結帳賣壓明顯。",
                })
            else:
                if abs(t_streak) >= 5:
                    trust_score -= 2
                badges.append({
                    "label": f"🏦 投信連賣 {abs(t_streak)} 日",
                    "level": "bear",
                    "detail": f"投信近 5 日淨賣超 {t_d5:+,} 張。",
                })
        elif t_d5 < 0 and t_streak <= -1:
            trust_score -= 2

    trust_score = max(-6, min(6, trust_score))
    wave_trust_score = max(-14, min(14, wave_score + trust_score))

    # =========================================================
    # Layer 3：融資四象限 (±5) ＋ 借券賣出 (±5)（相對量能 ≥ 3% 濾網）
    # =========================================================
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
    if isinstance(sbl_info, dict):
        sbl_5d = int(sbl_info.get("chg_5d", 0) or 0)
        sbl_pct = float(sbl_info.get("chg_5d_pct", 0.0) or 0.0)
        sbl_z = sbl_info.get("chg_5d_z")
        sbl_vol_pct = sbl_info.get("chg_5d_vol_pct")
        sbl_vol_ok = True if sbl_vol_pct is None else (sbl_vol_pct >= p["credit_vol_ratio_min"])

        if sbl_vol_ok and sbl_5d < 0 and (sbl_pct <= p["sbl_cover_5d_pct"] or (sbl_z is not None and sbl_z <= p["z_bear_max"])):
            sbl_score = 5
            badges.append({
                "label": f"🚀 借券回補 (5日 {sbl_5d:+,}張/{sbl_pct:+.1f}%)",
                "level": "bull",
                "detail": "外資／法人借券賣出餘額顯著下降，空方回補力道提供籌碼支撐。",
            })
        elif sbl_vol_ok and sbl_5d > 0 and (sbl_pct >= p["sbl_surge_5d_pct"] or (sbl_z is not None and sbl_z >= p["z_bull_min"])):
            sbl_score = -5
            badges.append({
                "label": f"🐻 借券空單加壓 (5日 {sbl_5d:+,}張/{sbl_pct:+.1f}%)",
                "level": "bear",
                "detail": "外資／法人借券賣出餘額顯著增加，空方避險或放空壓力升溫。",
            })

    credit_sbl_score = max(-10, min(10, margin_score + sbl_score))

    # =========================================================
    # Layer 4：TDCC 集保大戶週歸戶驗證 (±8 分)
    # =========================================================
    tdcc_score = 0
    if isinstance(tdcc_info, dict):
        w1000_chg = tdcc_info.get("whale_1000_chg_1w")
        w400_chg = tdcc_info.get("whale_400_chg_1w")
        h_chg = tdcc_info.get("holders_chg_1w")

        if w1000_chg is not None:
            if w1000_chg >= 0.5 and (h_chg is None or h_chg < 0):
                tdcc_score = 8
                badges.append({
                    "label": f"🐋 集保大戶強吸 (+{w1000_chg:.2f}%)",
                    "level": "bull",
                    "detail": "千張大戶持股比率單週大增且總股東人數減少，歸戶籌碼強烈集中。",
                })
            elif w1000_chg >= 0.2 and (h_chg is None or h_chg < 0):
                tdcc_score = 5
                badges.append({
                    "label": f"🐋 集保大戶增持 (+{w1000_chg:.2f}%)",
                    "level": "bull",
                    "detail": "千張大戶比率上升且散戶總人數減少，確認籌碼往大戶集中。",
                })
            elif w1000_chg >= 0.2 or (w400_chg is not None and w400_chg >= 0.3 and (h_chg is not None and h_chg < 0)):
                tdcc_score = 3
            elif w1000_chg <= -0.5 and (h_chg is None or h_chg > 0):
                tdcc_score = -8
                badges.append({
                    "label": f"⚠️ 集保大戶大減 ({w1000_chg:+.2f}%)",
                    "level": "bear",
                    "detail": "千張大戶比率單週顯著下滑且股東人數增加，歸戶籌碼明顯發散。",
                })
            elif w1000_chg <= -0.2 and (h_chg is None or h_chg > 0):
                tdcc_score = -5
                badges.append({
                    "label": f"⚠️ 集保大戶流出 ({w1000_chg:+.2f}%)",
                    "level": "bear",
                    "detail": "千張大戶比率下滑且總股東人數增加。",
                })
            elif w1000_chg <= -0.2 or (w400_chg is not None and w400_chg <= -0.3 and (h_chg is not None and h_chg > 0)):
                tdcc_score = -3

    tdcc_score = max(-8, min(8, tdcc_score))

    # =========================================================
    # Layer 5：當日隔日沖灌水濾網（偏多時單向扣分 0 ～ -10 分）
    # =========================================================
    raw_before_dt = 50 + conc_breadth_score + wave_trust_score + credit_sbl_score + tdcc_score
    dt_score = 0
    dt_vol_pct = float((day_trade_info or {}).get("dt_vol_pct", 0.0) or 0.0)
    dt_buyers = (day_trade_info or {}).get("dt_buyers") or []
    is_bull_biased = (raw_before_dt > 50) or (c5 is not None and c5 > 0) or (top3_5d > 0)

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
    # 判定 5 大純籌碼結構狀態
    # =========================================================
    point_surface_conflict = (
        (c5 is not None and c5 <= -p["conc_mod_pct"] and top3_5d > 0 and top3_5d_ctrl_pct >= p["top3_ctrl_mod_pct"])
        or (c5 is not None and c5 >= p["conc_mod_pct"] and top3_5d < 0 and top3_5d_ctrl_pct <= -p["top3_ctrl_mod_pct"])
    )
    heavy_dt_distortion = dt_score <= -6

    c5_txt = f"{c5:+.1f}%" if c5 is not None else "N/A"
    c5_z_txt = f"（Z:{c5_z:+.1f}σ）" if c5_z is not None else ""
    c10_txt = f"{c10:+.1f}%" if c10 is not None else "N/A"
    c20_txt = f"{c20:+.1f}%" if c20 is not None else "N/A"
    b5_txt = f"主力廣度 {b5_pct:.0f}%" if breadth_valid and b5_pct is not None else "主力廣度累積中"
    z_top3_txt = f"／Z:{top3_5d_ctrl_z:+.1f}σ" if top3_5d_ctrl_z is not None else ""

    summary_prefix = (
        f"全市場 5 日集中度 {c5_txt}{c5_z_txt}（10日 {c10_txt} ／ 20日 {c20_txt}），"
        f"{b5_txt}，近 60 日三大波段主力近 5 日淨額 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%{z_top3_txt}）"
    )

    if conviction_score >= 70 and (c5 is None or c5 > 0) and (not few_holding_up):
        sc_id = 1
        sc_type = "bull"
        sub_type = "全面共振吸籌"
        title = "🔥 純籌碼狀態：1. 全面共振吸籌（點面齊強）"
        desc = f"{summary_prefix}，大資金與多數追蹤波段分點高度同向偏多。"
        action = "籌碼集中度、主力廣度與輔助籌碼形成正向共振，屬結構最紮實的鎖碼或建倉格局。"
    elif conviction_score <= 35 or (
        c5 is not None
        and c5 <= -p["conc_mod_pct"]
        and conviction_score < 50
        and (top3_5d < 0 or (breadth_valid and b5_pct <= p["breadth_bear_pct"]))
    ):
        sc_id = 5
        sc_type = "bear"
        sub_type = "全面發散"
        title = "🟢 純籌碼狀態：5. 全面發散（大戶撤退）"
        desc = f"{summary_prefix}，籌碼明顯從大資金流向散戶或呈現主力集體撤退。"
        action = "籌碼結構偏空發散，下方缺乏大資金共識承接，應避開或等待集中度與廣度重新收斂。"
    elif few_holding_up or point_surface_conflict or heavy_dt_distortion:
        sc_id = 4
        sc_type = "neutral"
        sub_type = "籌碼分歧"
        title = "⚡ 純籌碼狀態：4. 籌碼分歧（少數硬撐／短沖干擾）"
        desc = f"{summary_prefix}，但內部主力廣度、三大主力方向或隔日沖佔比出現明顯矛盾。"
        action = "表面集中度與內部大戶共識不一致，須提防少數主力硬撐失敗或隔日沖短線獲利了結。"
    elif conviction_score >= 55:
        sc_id = 2
        sc_type = "bull"
        sub_type = "隱性吸籌"
        title = "📈 純籌碼狀態：2. 隱性吸籌／洗盤沉澱"
        desc = f"{summary_prefix}，籌碼已逐步向大戶端沉澱，整體偏向多方優勢。"
        action = "籌碼開始收斂但尚未達全面共振強度，可持續追蹤集中度與外資／波段廣度是否進一步放大。"
    else:
        sc_id = 3
        sc_type = "neutral"
        sub_type = "籌碼中性"
        title = "⚪ 純籌碼狀態：3. 籌碼中性／換手觀望"
        desc = f"{summary_prefix}，多空籌碼力道大致平衡。"
        action = "大資金尚未展現壓倒性單邊傾向，宜持續觀察主力廣度與集中度突破方向。"

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
            "conc_breadth": conc_breadth_score,
            "conc_diff": conc_breadth_score,  # 向下相容舊版鍵名
            "wave_trust": wave_trust_score,
            "credit_sbl": credit_sbl_score,
            "tdcc": tdcc_score,
            "day_trade": dt_score,
        },
        "margin_quadrant": margin_quadrant,
        "few_holding_up": few_holding_up,
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
