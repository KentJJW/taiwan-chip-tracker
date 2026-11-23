"""
strategy.py — 台股主力籌碼策略、分點行為畫像與多維度共振規則庫
包含：
1. 滾動 60 日波段主力篩選、行為式隔日沖偵測與贏家分點勝率標記
2. 金額防呆、60 日滾動 Z-Score 自適應門檻與五大實戰劇本判定
3. 投信持股水位、買賣家數差、融資浮額、借券賣出、集保歸戶大戶之共振驗證層
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# =====================================================================
# 一、 已知國內隔日沖熱點分點（搭配下方行為式週轉／反手率動態過濾）
# =====================================================================
DAY_TRADE_BROKERS = {
    "凱基-台北",
    "凱基-虎尾",
    "凱基-嘉義",
    "富邦-嘉義",
    "富邦-虎尾",
    "統一-虎尾",
    "統一-內湖",
    "永豐金-虎尾",
    "永豐金-松山",
    "元大-土城永寧",
    "群益金鼎-大安",
    "國票-敦北法人",
    "兆豐-大同",
    "合庫-嘉義",
    "元富-城中",
}

# =====================================================================
# 二、 策略核心量化門檻參數（集中管理，方便隨時微調）
# =====================================================================
STRATEGY_PARAMS = {
    # 滾動視窗設定
    "wave_lookback_days": 60,        # 波段主力鎖定視窗（交易日）
    "zscore_window": 60,             # 滾動 Z-Score 計算視窗
    "winner_forward_days": 20,       # 贏家分點前瞻勝率評估天期

    # 金額與最低張數防呆（高價股看金額、低價股防碎單）
    "min_abs_amount_5d_ntd": 5_000_000,   # 近 5 日三大主力淨額下限（500 萬元）
    "min_abs_shares_floor": 5,            # 最低張數底線（避免超高價股 1~2 張雜訊）

    # 滾動 Z-Score 自適應觸發門檻（解決權值股／高當沖股固定門檻過嚴問題）
    "z_bull_min": 1.5,               # Z-Score >= +1.5σ 視為顯著異常吸籌
    "z_bear_max": -1.5,              # Z-Score <= -1.5σ 視為顯著異常倒貨

    # 分點行為畫像門檻（隔日沖偵測 & 贏家分點判定）
    "dt_min_active_days": 6,         # 近 60 日至少有進出天數
    "dt_churn_ratio_min": 3.5,       # 週轉係數（總進出張數 / 淨額絕對值）門檻
    "dt_reversal_rate_min": 0.45,    # 買超隔日立即反手賣超 >= 50% 之比例
    "dt_today_vol_warn_pct": 5.0,    # 當日隔日沖買超佔總成交量警戒門檻 (%)
    "dt_today_top15_warn_pct": 30.0, # 當日隔日沖佔前 15 大買超警戒門檻 (%)
    "winner_min_signals": 3,         # 贏家分點最少顯著買超次數
    "winner_min_win_rate": 60.0,     # 贏家分點 20 日勝率下限 (%)
    "winner_min_avg_ret": 3.0,       # 贏家分點 20 日平均報酬下限 (%)

    # 1. 雙重共振（主升段）
    "s1_ctrl_min": 2.0,              # 近 5 日三大主力控盤率下限 (%)
    "s1_c5_min": 3.0,                # 近 5 日集中度下限 (%)
    "s1_c5_strong": 8.0,             # 若未嚴格遞增，5 日集中度直接達標門檻 (%)

    # 2. 換手接棒（藏分點）
    "s2_ctrl_max": 1.5,              # 近 5 日三大主力控盤率上限 (%)
    "s2_c5_min": 5.0,                # 近 5 日全市場集中度下限 (%)
    "s2_c10_min": 2.0,               # 近 10 日集中度輔助門檻 (%)

    # 3. 高檔派發（假突破／反彈派發）
    "s3_price_5d_min": 2.0,          # 近 5 日股價漲幅下限 (%)
    "s3_ctrl_max": -2.0,             # 近 5 日三大主力控盤率上限 (%)
    "s3_c5_max": -3.0,               # 近 5 日集中度轉負上限 (%)

    # 4. 成本護盤（真洗盤）
    "s4_cost_buffer": 1.025,         # 回測三大主力合計成本上緣倍數 (+2.5% 內)
    "s4_price_5d_max": -1.5,         # 近 5 日股價回檔幅度門檻 (%)
    "s4_ctrl_min": 1.5,              # 近 5 日三大主力逆勢買超控盤率下限 (%)
    "s4_c5_min": 3.0,                # 近 5 日集中度下限 (%)
    "s4_max_vol_ratio": 1.6,         # 5日均量 / 20日均量上限（過大代表爆量多殺多風險）

    # 5. 主力棄守（真破線）
    "s5_cost_break": 0.98,           # 跌破三大主力合計成本門檻 (-2% 以下)
    "s5_ctrl_max": -3.0,             # 近 5 日三大主力倒貨控盤率上限 (%)
    "s5_c5_max": -5.0,               # 近 5 日集中度上限 (%)
    "s5_c10_max": -3.0,              # 近 10 日集中度上限 (%)

    # 近 5 日價量籌碼背離警報門檻
    "div_price_pct": 3.0,            # 近 5 日漲跌幅門檻 (%)
    "div_ctrl_pct": 2.0,             # 近 5 日三大主力控盤率門檻 (%)

    # 六大籌碼共振確認門檻
    "trust_streak_bull": 3,          # 投信連買共振天數
    "trust_streak_bear": -2,         # 投信連賣結帳天數
    "trust_high_holding_pct": 10.0,  # 投信持股比率高檔結帳警戒線 (%)
    "trust_sweet_holding_max": 5.0,  # 投信初升認養甜蜜區間上限 (%)
    "margin_surge_5d_pct": 5.0,      # 融資近 5 日激增警戒線 (%)
    "margin_clean_5d_pct": -2.0,     # 融資近 5 日顯著清洗門檻 (%)
    "sbl_surge_5d_pct": 5.0,         # 借券賣出近 5 日大增警戒線 (%)
    "sbl_cover_5d_pct": -3.0,        # 借券賣出近 5 日顯著回補門檻 (%)
}

# 快捷上色標籤（紅＝偏多／買超、綠＝偏空／賣超、橘＝關鍵價位均線）
_R = lambda s: f'<span style="color:#ef5350; font-weight:700;">{s}</span>'
_G = lambda s: f'<span style="color:#26a69a; font-weight:700;">{s}</span>'
_Y = lambda s: f'<span style="color:#ffb74d; font-weight:700;">{s}</span>'

# =====================================================================
# 三、 可收合抽屜「價 × 點 × 面 × 共振 交叉判讀矩陣」
# =====================================================================
SCENARIO_MATRIX = [
    {
        "id": 1,
        "name": "1. 雙重共振（主升段）",
        "type": "bull",
        "price_cond": f"站穩 {_Y('三大主力成本、10MA、20MA')}<br>且 {_Y('10MA ≥ 20MA')}",
        "main_cond": f"近60日三大波段主力<br>5日控盤 {_R('≥ +2.0%')} 或 {_R('Z ≥ +1.5σ')}<br>（淨額 ≥ 500萬、過濾隔日沖）",
        "conc_cond": f"5 / 10 / 20 日{_R('全正')}且加速<br>搭配家數差連負／投信連買／集保大戶增更佳",
        "action": "最強多頭格局。沿 10MA 或三大主力成本線偏多操作；若同時出現隔日沖過重或借券賣出暴增，須降碼慎防短線震盪。",
    },
    {
        "id": 2,
        "name": "2. 換手接棒（藏分點）",
        "type": "bull",
        "price_cond": f"站上 {_Y('10MA')}<br>（濾除空頭急跌）",
        "main_cond": f"原三大波段主力<br>5日控盤 {_Y('≤ +1.5%')}（持平或小調節）",
        "conc_cond": f"5日集中度翻正飆高 {_R('≥ +5.0%')} 或 {_R('Z ≥ +1.5σ')}<br>若集保大戶同步增加更能確認換莊成功",
        "action": "隱形起漲／新莊家進場。對照「近 10 日新強勢分點」與「贏家分點」鎖定新主力，但須確認非單日隔日沖灌水。",
    },
    {
        "id": 3,
        "name": "3. 高檔派發（假突破／反彈逃命）",
        "type": "bear",
        "price_cond": f"近5日上漲 {_R('≥ +2.0%')}",
        "main_cond": f"三大波段主力<br>5日控盤 {_G('≤ -2.0%')} 或 {_G('Z ≤ -1.5σ')}",
        "conc_cond": f"5日集中度轉負 {_G('≤ -3.0%')} 或 {_G('Z ≤ -1.5σ')}<br>（20日正＝初期派發；20日負＝中繼反彈派發）",
        "action": "主力逢高倒貨警訊。若同時伴隨投信結帳、融資暴增或借券賣出增加，切忌追高，應逢反彈減碼。",
    },
    {
        "id": 4,
        "name": "4. 成本護盤（真洗盤）",
        "type": "bull",
        "price_cond": f"回測 {_Y('三大主力成本 +2.5% 內')}<br>近5日下跌 {_G('≤ -1.5%')} 且未失控爆量",
        "main_cond": f"三大波段主力<br>5日控盤 {_R('≥ +1.5%')} 或 {_R('Z ≥ +1.5σ')}",
        "conc_cond": f"5日維持正值 {_R('≥ +3.0%')} 或 {_R('Z ≥ +1.5σ')}<br>若搭配{_R('融資清洗減少')}勝率大幅提升",
        "action": "主力回測成本區承接浮額。量縮止穩且融資退場時為高勝率切入點；若爆巨量長黑則慎防主力一起套牢。",
    },
    {
        "id": 5,
        "name": "5. 主力棄守（真破線）",
        "type": "bear",
        "price_cond": f"跌破 {_G('三大主力成本逾 2%')}<br>且失守 {_G('20MA 月線')}",
        "main_cond": f"三大波段主力<br>5日控盤 {_G('≤ -3.0%')} 或 {_G('Z ≤ -1.5σ')}",
        "conc_cond": f"5日 {_G('≤ -5.0%')}、10日 {_G('≤ -3.0%')}<br>籌碼全面發散流向散戶",
        "action": "多殺多棄守格局。連波段主力都在停損結帳且月線失守，切忌因主力帳面虧損而盲目攤平。",
    },
]


# =====================================================================
# 四、 量化統計工具：滾動 Z-Score
# =====================================================================
def compute_rolling_zscore(
    series: pd.Series,
    window: int = 60,
    min_periods: int = 15,
) -> float | None:
    """
    計算序列最新一筆數值相對於過去 window 交易日的滾動 Z-Score (標準差倍數)。
    用於自動校準不同股性、成交量級與當沖比之指標敏感度。
    """
    if series is None or series.empty:
        return None
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if len(clean) < min_periods:
        return None
    tail = clean.tail(window)
    std = float(tail.std(ddof=0))
    if std <= 1e-9:
        return 0.0
    mean = float(tail.mean())
    latest = float(tail.iloc[-1])
    return round((latest - mean) / std, 2)


# =====================================================================
# 五、 分點行為畫像：隔日沖偵測、贏家勝率評分與滾動 60 日主力篩選
# =====================================================================
def profile_brokers(
    df_price_full: pd.DataFrame,
    df_chips_full: pd.DataFrame,
    lookback_days: int | None = None,
    forward_days: int | None = None,
) -> dict:
    """
    以現有 price 與 chips 歷史明細，自動對所有券商分點建立行為畫像：
    1. 週轉係數 (churn_ratio) 與 隔日反手率 (reversal_rate) -> 行為式偵測隔日沖／假外資
    2. 顯著買超後 20 日前瞻勝率與平均報酬 -> 自動標記 🏆 贏家分點
    3. 同步回傳近 60 日（波段）、近 10 日（短線）、近 5 日、今日與今年累計淨張數
    """
    p = STRATEGY_PARAMS
    lb = lookback_days or p["wave_lookback_days"]
    fwd = forward_days or p["winner_forward_days"]

    if df_price_full is None or df_price_full.empty or df_chips_full is None or df_chips_full.empty:
        return {}

    broker_cols = [c for c in df_chips_full.columns if c not in ("date", "投信")]
    if not broker_cols:
        return {}

    df_m = df_price_full[["date", "close"]].merge(df_chips_full, on="date", how="left").fillna(0.0)
    df_m = df_m.sort_values("date").reset_index(drop=True)

    # 計算前瞻 N 日報酬率（用於評估贏家分點勝率）
    close_s = pd.to_numeric(df_m["close"], errors="coerce")
    fwd_ret_s = (close_s.shift(-fwd) / close_s - 1.0) * 100.0

    df_60 = df_m.tail(lb)
    df_10 = df_m.tail(10)
    df_5 = df_m.tail(5)
    df_1 = df_m.tail(1)

    profiles = {}
    for col in broker_cols:
        s_all = pd.to_numeric(df_m[col], errors="coerce").fillna(0.0)
        s_60 = pd.to_numeric(df_60[col], errors="coerce").fillna(0.0)

        net_ytd = int(round(s_all.sum()))
        net_60d = int(round(s_60.sum()))
        net_10d = int(round(pd.to_numeric(df_10[col], errors="coerce").fillna(0.0).sum()))
        net_5d = int(round(pd.to_numeric(df_5[col], errors="coerce").fillna(0.0).sum()))
        net_1d = int(round(pd.to_numeric(df_1[col], errors="coerce").fillna(0.0).sum()))

        # --- 1. 隔日沖行為指標（以近 60 日評估） ---
        arr = s_60.to_numpy(dtype=float)
        nonzero_mask = arr != 0
        active_days = int(nonzero_mask.sum())
        gross_flow = float(np.abs(arr).sum())
        abs_net = abs(float(arr.sum()))
        churn_ratio = round(gross_flow / max(abs_net, 1.0), 2) if gross_flow > 0 else 0.0

        # 計算「顯著買超日隔天立即反手倒出 >= 50%」之比例
        pos_vals = arr[arr > 0]
        reversal_rate = 0.0
        if len(pos_vals) >= 2 and len(arr) >= 2:
            sig_thresh = max(float(np.median(pos_vals)) * 0.5, 5.0)
            buy_events = 0
            reversals = 0
            for i in range(len(arr) - 1):
                if arr[i] >= sig_thresh:
                    buy_events += 1
                    if arr[i + 1] < 0 and abs(arr[i + 1]) >= 0.5 * arr[i]:
                        reversals += 1
            if buy_events > 0:
                reversal_rate = round(reversals / buy_events, 2)

        in_dt_list = col in DAY_TRADE_BROKERS
        behavior_dt = (
            active_days >= p["dt_min_active_days"]
            and churn_ratio >= p["dt_churn_ratio_min"]
            and reversal_rate >= p["dt_reversal_rate_min"]
        )

        # 處理「名列隔日沖分點，但近期其實呈單向波段鎖碼」之例外（如凱基台北大戶長線帳戶）
        is_day_trader = False
        dt_reason = ""
        if in_dt_list:
            if churn_ratio >= 1.8 or reversal_rate >= 0.25 or abs(net_60d) < 300:
                is_day_trader = True
                dt_reason = "已知隔日沖熱點分點"
            else:
                dt_reason = "熱點分點但近期呈波段鎖碼"
        elif behavior_dt:
            is_day_trader = True
            dt_reason = f"高頻短沖（週轉 {churn_ratio:.1f}x／隔日反手 {int(reversal_rate * 100)}%）"

        # --- 2. 贏家分點勝率（以全歷史可驗證區間評估） ---
        valid_mask = fwd_ret_s.notna() & (s_all > 0)
        win_rate = None
        avg_fwd_ret = None
        buy_signal_cnt = 0
        if valid_mask.any():
            pos_hist = s_all[valid_mask]
            q75 = float(pos_hist.quantile(0.75)) if len(pos_hist) >= 4 else float(pos_hist.median())
            sig_mask = valid_mask & (s_all >= max(q75, 5.0))
            sig_rets = fwd_ret_s[sig_mask]
            buy_signal_cnt = int(len(sig_rets))
            if buy_signal_cnt >= 2:
                win_rate = round(float((sig_rets > 0).mean() * 100.0), 1)
                avg_fwd_ret = round(float(sig_rets.mean()), 2)

        is_winner = (
            not is_day_trader
            and buy_signal_cnt >= p["winner_min_signals"]
            and win_rate is not None
            and avg_fwd_ret is not None
            and win_rate >= p["winner_min_win_rate"]
            and avg_fwd_ret >= p["winner_min_avg_ret"]
        )

        tags = []
        if is_day_trader:
            tags.append("⚡隔日沖")
        elif dt_reason:
            tags.append("👀熱點鎖碼")
        if is_winner:
            tags.append(f"🏆贏家({int(round(win_rate))}%)")

        profiles[col] = {
            "name": col,
            "net_60d": net_60d,
            "net_ytd": net_ytd,
            "net_10d": net_10d,
            "net_5d": net_5d,
            "net_1d": net_1d,
            "active_days": active_days,
            "churn_ratio": churn_ratio,
            "reversal_rate": reversal_rate,
            "is_day_trader": is_day_trader,
            "day_trade_reason": dt_reason,
            "win_rate": win_rate,
            "avg_fwd_ret": avg_fwd_ret,
            "buy_signals": buy_signal_cnt,
            "is_winner": is_winner,
            "tags": tags,
        }

    return profiles


def select_wave_main_forces(
    broker_profiles: dict,
    top_n: int = 3,
    exclude_day_traders: bool = True,
) -> tuple[list[str], list[str]]:
    """
    從滾動 60 日淨買超分點中篩選真正的波段主力名單：
    - 預設排除高頻隔日沖分點，避免主力名單被短線客綁架
    - 回傳 (選出的波段主力名單, 被過濾掉的前段隔日沖名單)
    """
    if not broker_profiles:
        return [], []

    positive_items = [info for info in broker_profiles.values() if info["net_60d"] > 0]
    positive_items.sort(key=lambda x: x["net_60d"], reverse=True)

    if not positive_items:
        return [], []

    filtered_dt = []
    wave_candidates = []
    for item in positive_items:
        if exclude_day_traders and item["is_day_trader"]:
            if len(filtered_dt) < 5:
                filtered_dt.append(item["name"])
            continue
        wave_candidates.append(item["name"])

    # 若市場上全數淨買超皆為隔日沖（極端案例），才降級回退避免系統無對象可追蹤
    if not wave_candidates:
        wave_candidates = [item["name"] for item in positive_items[:top_n]]

    return wave_candidates[:top_n], filtered_dt


# =====================================================================
# 六、 投信持股水位與階段評估
# =====================================================================
def evaluate_trust_phase(trust_info: dict | None) -> dict | None:
    """依據投信持股比率（佔股本%）與近期買賣連貫性，判定投信所處階段"""
    if not trust_info:
        return None

    p = STRATEGY_PARAMS
    holding_pct = trust_info.get("holding_pct")
    streak = int(trust_info.get("streak", 0))
    d5 = int(trust_info.get("d5", 0))
    d20 = int(trust_info.get("d20", 0))

    tier_label = "持股比待補"
    tier_level = "info"
    if holding_pct is not None:
        if holding_pct >= p["trust_high_holding_pct"]:
            tier_label = f"高水位警戒 ({holding_pct:.2f}%)"
            tier_level = "warn"
        elif holding_pct >= p["trust_sweet_holding_max"]:
            tier_label = f"中段核心持股 ({holding_pct:.2f}%)"
            tier_level = "info"
        elif holding_pct >= 0.5:
            tier_label = f"初升認養甜蜜期 ({holding_pct:.2f}%)"
            tier_level = "bull"
        elif holding_pct > 0:
            tier_label = f"剛建立部位 ({holding_pct:.2f}%)"
            tier_level = "bull"
        else:
            tier_label = "尚無明顯持股 (0%)"
            tier_level = "info"

    momentum = "中性觀望"
    if streak >= p["trust_streak_bull"] and d5 > 0:
        momentum = f"連買 {streak} 日認養中"
    elif streak <= p["trust_streak_bear"] and d5 < 0:
        momentum = f"連賣 {abs(streak)} 日調節／結帳"
    elif d20 > 0 and d5 < 0:
        momentum = "波段買超但短線轉調節"
    elif d20 < 0 and d5 > 0:
        momentum = "短線由賣轉買試單"

    return {
        "tier_label": tier_label,
        "tier_level": tier_level,
        "momentum": momentum,
        "holding_pct": holding_pct,
    }


# =====================================================================
# 七、 五大實戰劇本自動判定 ＋ 多維度共振驗證層
# =====================================================================
def _check_amount_liquidity(top3_5d: int, latest_close: float, top3_5d_amount_ntd: float) -> bool:
    """檢查近 5 日主力進出是否達最低有效金額與張數底線"""
    p = STRATEGY_PARAMS
    if abs(top3_5d) < p["min_abs_shares_floor"]:
        return False
    est_amt = abs(top3_5d_amount_ntd) if top3_5d_amount_ntd else abs(top3_5d) * max(latest_close, 0.0) * 1000.0
    return est_amt >= p["min_abs_amount_5d_ntd"]


def _build_resonance_layer(
    scenario_id: int,
    scenario_type: str,
    latest_close: float,
    ma20: float,
    conc_5d: float | None,
    trust_info: dict | None,
    broker_diff_info: dict | None,
    margin_info: dict | None,
    sbl_info: dict | None,
    tdcc_info: dict | None,
    day_trade_info: dict | None,
    vol_ratio_5_20: float | None,
) -> tuple[list[dict], int, list[str]]:
    """
    依據 6 大籌碼維度（投信、家數差、融資、借券、集保、隔日沖）產出共振徽章、
    綜合籌碼健康分數 (0~100) 與額外操作提醒。
    """
    p = STRATEGY_PARAMS
    badges: list[dict] = []
    extra_notes: list[str] = []

    # 基準分數
    if scenario_id == 1:
        score = 78
    elif scenario_id in (2, 4):
        score = 68
    elif scenario_id == 3:
        score = 28
    elif scenario_id == 5:
        score = 18
    else:
        score = 50

    # --- 1. 隔日沖過濾警訊 ---
    if day_trade_info and day_trade_info.get("is_heavy_day_trade"):
        dt_vol_pct = day_trade_info.get("dt_vol_pct", 0.0)
        dt_names = ", ".join(day_trade_info.get("dt_buyers", [])[:3])
        badges.append({
            "level": "warn",
            "label": f"⚡ 隔日沖過重 ({dt_vol_pct:.1f}%)",
            "detail": f"最新交易日隔日沖主力（{dt_names}）淨買超佔當日成交量 {dt_vol_pct:.1f}%，慎防隔日開高倒貨。",
        })
        if scenario_type == "bull":
            score -= 14
            extra_notes.append("⚠️ 今日買盤含大量隔日沖（慎防隔日開高倒貨，勿盲目追早盤高點）。")

    # --- 2. 投信動向與持股水位 ---
    if trust_info:
        t_streak = int(trust_info.get("streak", 0))
        t_d5 = int(trust_info.get("d5", 0))
        t_cost = float(trust_info.get("cost", 0.0) or 0.0)
        t_hold = trust_info.get("holding_pct")

        if t_streak >= p["trust_streak_bull"] and t_d5 > 0:
            if t_hold is not None and t_hold < p["trust_sweet_holding_max"]:
                badges.append({
                    "level": "bull",
                    "label": f"🔥 投信初升認養（連買{t_streak}日｜持股{t_hold:.1f}%）",
                    "detail": "投信連續買超且持股水位仍在 5% 以下甜蜜區間，土洋／內資共振力道強。",
                })
                score += 10
            elif t_hold is not None and t_hold >= p["trust_high_holding_pct"]:
                badges.append({
                    "level": "warn",
                    "label": f"🏦 投信連買{t_streak}日但高水位 ({t_hold:.1f}%)",
                    "detail": "投信雖仍買超，但持股已達 10% 以上高檔區，需留意季底或達標後結帳風險。",
                })
                score += 3
            else:
                above_t = "且站上投信成本" if (t_cost > 0 and latest_close >= t_cost) else ""
                badges.append({
                    "level": "bull",
                    "label": f"🔥 投信共振（連買{t_streak}日）",
                    "detail": f"投信近 5 日買超 {t_d5:+,} 張{above_t}，形成穩定波段買盤支撐。",
                })
                score += 8
        elif t_streak <= p["trust_streak_bear"] and t_d5 < 0:
            high_tag = f"（原持股{t_hold:.1f}%）" if (t_hold is not None and t_hold >= p["trust_high_holding_pct"]) else ""
            badges.append({
                "level": "bear",
                "label": f"🏦 投信結帳調節（連賣{abs(t_streak)}日）",
                "detail": f"投信近 5 日賣超 {t_d5:+,} 張{high_tag}，留意法人連環調節賣壓。",
            })
            score -= 10
            if scenario_id == 3:
                extra_notes.append("⚠️ 投信同步轉賣，形成「分點派發＋投信結帳」雙重高危訊號。")

    # --- 3. 買賣家數差（籌碼流向驗證） ---
    if broker_diff_info:
        diff_5d = broker_diff_info.get("diff_5d")
        neg_streak = int(broker_diff_info.get("neg_streak", 0))
        if diff_5d is not None:
            if diff_5d < 0 and (conc_5d is not None and conc_5d > 0):
                badges.append({
                    "level": "bull",
                    "label": f"🧲 真收籌（5日家數差 {diff_5d:+}｜連負{neg_streak}日）",
                    "detail": "買超集中在少數分點、賣超分散於大量分點，確認籌碼由散戶流向主力。",
                })
                score += 6
            elif diff_5d > 0 and (conc_5d is not None and conc_5d > 0):
                badges.append({
                    "level": "warn",
                    "label": f"👥 散戶同追（5日家數差 {diff_5d:+}）",
                    "detail": "雖然集中度為正，但買超家數大於賣超家數，顯示散戶同步追價，籌碼略顯凌亂。",
                })
                score -= 4
            elif diff_5d > 0 and (conc_5d is not None and conc_5d < 0):
                badges.append({
                    "level": "bear",
                    "label": f"💸 籌碼渙散（5日家數差 {diff_5d:+}）",
                    "detail": "少數大戶賣出、多數散戶接手，典型籌碼發散格局。",
                })
                score -= 6

    # --- 4. 融資餘額變化（散戶浮額） ---
    if margin_info:
        m_chg_5d = margin_info.get("chg_5d")
        m_chg_pct = margin_info.get("chg_5d_pct")
        sm_ratio = margin_info.get("short_margin_ratio")
        if m_chg_5d is not None and m_chg_pct is not None:
            if m_chg_pct <= p["margin_clean_5d_pct"] and m_chg_5d < 0:
                badges.append({
                    "level": "bull",
                    "label": f"🧹 融資清洗（5日 {m_chg_5d:+,}張 / {m_chg_pct:+.1f}%）",
                    "detail": "散戶融資明顯退場，籌碼沉澱有利主力拉抬或止跌。",
                })
                score += 7
                if scenario_id == 4:
                    extra_notes.append("✅ 融資同步大減，確認主力精準承接散戶停損浮額。")
            elif m_chg_pct >= p["margin_surge_5d_pct"] and m_chg_5d > 0:
                badges.append({
                    "level": "warn",
                    "label": f"⚠️ 融資激增（5日 {m_chg_5d:+,}張 / {m_chg_pct:+.1f}%）",
                    "detail": "短線散戶槓桿浮額快速累積，容易引發高檔劇烈震盪或洗盤。",
                })
                score -= 6
        if sm_ratio is not None and sm_ratio >= 20.0 and latest_close >= ma20 > 0:
            badges.append({
                "level": "bull",
                "label": f"🔥 高券資比 ({sm_ratio:.1f}%)",
                "detail": "股價站穩月線且券資比偏高，具備潛在軋空燃料。",
            })
            score += 4

    # --- 5. 借券賣出餘額（外資／機構真實空方力道） ---
    if sbl_info:
        sbl_chg_5d = sbl_info.get("chg_5d")
        sbl_chg_pct = sbl_info.get("chg_5d_pct")
        if sbl_chg_5d is not None and sbl_chg_pct is not None:
            if sbl_chg_pct >= p["sbl_surge_5d_pct"] and sbl_chg_5d > 0:
                badges.append({
                    "level": "bear",
                    "label": f"🐻 借券賣出大增（5日 {sbl_chg_5d:+,}張 / {sbl_chg_pct:+.1f}%）",
                    "detail": "外資或機構空單明顯加壓，若現貨同步拉抬需慎防「假拉真鎖空」。",
                })
                score -= 9
                if scenario_type == "bull":
                    extra_notes.append("⚠️ 警訊：近 5 日借券賣出同步大增，慎防外資現貨誘多、借券放空。")
            elif sbl_chg_pct <= p["sbl_cover_5d_pct"] and sbl_chg_5d < 0:
                badges.append({
                    "level": "bull",
                    "label": f"🚀 借券回補（5日 {sbl_chg_5d:+,}張 / {sbl_chg_pct:+.1f}%）",
                    "detail": "機構空方開始回補借券，空方壓力減輕並提供軋空助力。",
                })
                score += 6

    # --- 6. TDCC 集保戶股權分散表（破解藏分點與匯撥） ---
    if tdcc_info:
        w1000_chg = tdcc_info.get("whale_1000_chg_1w")
        w400_chg = tdcc_info.get("whale_400_chg_1w")
        holders_chg = tdcc_info.get("holders_chg_1w")
        whale_delta = w1000_chg if w1000_chg is not None else w400_chg
        whale_lbl = "千張" if w1000_chg is not None else "400張"

        if whale_delta is not None and holders_chg is not None:
            if whale_delta >= 0.2 and holders_chg < 0:
                badges.append({
                    "level": "bull",
                    "label": f"🐋 集保確認（{whale_lbl}大戶 {whale_delta:+.2f}%｜股東 {holders_chg:+,}人）",
                    "detail": "歸戶數據證實大戶持股上升、總股東人數下降，排除假分點與匯撥干擾。",
                })
                score += 8
            elif whale_delta <= -0.2 and holders_chg > 0:
                badges.append({
                    "level": "bear",
                    "label": f"⚠️ 集保背離（{whale_lbl}大戶 {whale_delta:+.2f}%｜股東 {holders_chg:+,}人）",
                    "detail": "歸戶大戶流出且散戶人數暴增，慎防主力透過拆單藏分點或匯撥暗中出貨。",
                })
                score -= 9

    # --- 7. 量能異常檢核（針對情境 4 真洗盤） ---
    if scenario_id == 4 and vol_ratio_5_20 is not None and vol_ratio_5_20 > p["s4_max_vol_ratio"]:
        badges.append({
            "level": "warn",
            "label": f"📊 回檔量能偏大 (5/20量比 {vol_ratio_5_20:.2f}x)",
            "detail": "回測主力成本時成交量未收斂，須確認是否為基本面利空爆量，待量縮止穩再介入。",
        })
        score -= 7
        extra_notes.append("⚠️ 回檔過程量能放大，建議等量縮不破低再確認護盤有效。")

    score = max(0, min(100, int(round(score))))
    return badges, score, extra_notes


def evaluate_chip_scenario(
    top3_5d: int,
    top3_5d_ctrl_pct: float,
    latest_close: float,
    ma10: float,
    ma20: float,
    top3_cost: float,
    price_5d_pct: float,
    conc: dict,
    top3_5d_amount_ntd: float = 0.0,
    top3_5d_ctrl_z: float | None = None,
    top3_5d_float_pct: float | None = None,
    vol_ratio_5_20: float | None = None,
    trust_info: dict | None = None,
    broker_diff_info: dict | None = None,
    margin_info: dict | None = None,
    sbl_info: dict | None = None,
    tdcc_info: dict | None = None,
    day_trade_info: dict | None = None,
) -> dict:
    """
    結合滾動 60 日三大波段主力（含金額防呆與 Z-Score）、三大主力合計成本、
    10/20MA、全市場 5/10/20 日集中度（含 Z-Score）與六大籌碼共振層，判定當前盤面劇本。
    """
    p = STRATEGY_PARAMS
    c5_dict = conc.get("d5", {}) if isinstance(conc, dict) else {}
    c10_dict = conc.get("d10", {}) if isinstance(conc, dict) else {}
    c20_dict = conc.get("d20", {}) if isinstance(conc, dict) else {}

    c5 = c5_dict.get("pct")
    c10 = c10_dict.get("pct")
    c20 = c20_dict.get("pct")
    c5_z = c5_dict.get("z_score")

    if c5 is None or c10 is None or c20 is None:
        return {
            "id": 0,
            "type": "neutral",
            "sub_type": "資料未齊",
            "title": "⚪ 籌碼中性整理（尚未取得完整集中度數據）",
            "desc": "請點擊左側「全部更新」或個股旁「🔄」以補齊最新 5 / 10 / 20 日全市場集中度。",
            "action": "",
            "badges": [],
            "conviction_score": 50,
        }

    # 價位與均線結構
    above_cost = (top3_cost > 0 and latest_close >= top3_cost)
    above_ma10 = (ma10 > 0 and latest_close >= ma10)
    above_ma20 = (ma20 > 0 and latest_close >= ma20)
    ma_bull_align = (ma10 > 0 and ma20 > 0 and ma10 >= ma20)
    below_ma20 = (ma20 > 0 and latest_close < ma20)
    near_or_below_cost = (top3_cost > 0 and latest_close <= top3_cost * p["s4_cost_buffer"])

    # 金額流動性防呆
    has_liq = _check_amount_liquidity(top3_5d, latest_close, top3_5d_amount_ntd)

    # 自適應 Z-Score 輔助判定（固定門檻 OR 方向正確且 Z-Score 達顯著標準差）
    ctrl_bull_s1 = (top3_5d_ctrl_pct >= p["s1_ctrl_min"]) or (
        top3_5d_ctrl_pct > 0 and top3_5d_ctrl_z is not None and top3_5d_ctrl_z >= p["z_bull_min"]
    )
    ctrl_bull_s4 = (top3_5d_ctrl_pct >= p["s4_ctrl_min"]) or (
        top3_5d_ctrl_pct > 0 and top3_5d_ctrl_z is not None and top3_5d_ctrl_z >= p["z_bull_min"]
    )
    ctrl_bear_s3 = (top3_5d_ctrl_pct <= p["s3_ctrl_max"]) or (
        top3_5d_ctrl_pct < 0 and top3_5d_ctrl_z is not None and top3_5d_ctrl_z <= p["z_bear_max"]
    )
    ctrl_bear_s5 = (top3_5d_ctrl_pct <= p["s5_ctrl_max"]) or (
        top3_5d_ctrl_pct < 0 and top3_5d_ctrl_z is not None and top3_5d_ctrl_z <= p["z_bear_max"]
    )

    c5_bull_s1 = (c5 > p["s1_c5_min"]) or (c5 > 0 and c5_z is not None and c5_z >= p["z_bull_min"])
    c5_bull_s2 = (c5 >= p["s2_c5_min"]) or (c5 > 0 and c5_z is not None and c5_z >= p["z_bull_min"])
    c5_bull_s4 = (c5 >= p["s4_c5_min"]) or (c5 > 0 and c5_z is not None and c5_z >= p["z_bull_min"])
    c5_bear_s3 = (c5 <= p["s3_c5_max"]) or (c5 < 0 and c5_z is not None and c5_z <= p["z_bear_max"])
    c5_bear_s5 = (c5 <= p["s5_c5_max"]) or (c5 < 0 and c5_z is not None and c5_z <= p["z_bear_max"])

    z_ctrl_str = f" / Z:{top3_5d_ctrl_z:+.1f}σ" if top3_5d_ctrl_z is not None else ""
    z_c5_str = f" (Z:{c5_z:+.1f}σ)" if c5_z is not None else ""
    float_str = f"、佔股本 {top3_5d_float_pct:+.2f}%" if top3_5d_float_pct is not None else ""

    scenario_id = 0
    scenario_type = "neutral"
    sub_type = "中性整理"
    title = "⚪ 當前狀態：籌碼中性整理（尚未觸發五大關鍵劇本）"
    desc = (
        f"收盤價 {latest_close:,.2f} 元（10MA {ma10:,.2f} ／ 20MA {ma20:,.2f}），"
        f"近 60 日三大波段主力近 5 日淨額 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%{z_ctrl_str}{float_str}），"
        f"全市場 5 日集中度 {c5:+.1f}%{z_c5_str}，處於多空均衡或橫盤整理階段。"
    )
    action = "持續觀察收盤價與 10MA/20MA 位置、5 日集中度（含 Z-Score）或三大波段主力控盤率表態。"

    # 情境 5：主力棄守（真破線）— 最高風控優先序
    if (
        top3_cost > 0
        and latest_close < top3_cost * p["s5_cost_break"]
        and below_ma20
        and has_liq
        and top3_5d < 0
        and ctrl_bear_s5
        and c5_bear_s5
        and c10 <= p["s5_c10_max"]
    ):
        scenario_id = 5
        scenario_type = "bear"
        sub_type = "真破線棄守"
        title = "🟢 當前劇本：5. 主力棄守（真破線）"
        desc = (
            f"收盤價（{latest_close:,.2f} 元）跌破三大主力合計成本（{top3_cost:,.2f} 元）逾 2% 且失守 20MA（{ma20:,.2f} 元），"
            f"三大主力近 5 日大賣 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%{z_ctrl_str}），"
            f"5 日集中度降至 {c5:+.1f}%{z_c5_str}。"
        )
        action = "連波段主力都在停損結帳且月線失守，下方無大資金承接，切忌因主力帳面虧損而盲目攤平。"

    # 情境 3：高檔派發（移除 c20 > 0 強制否決，細分初期派發與中繼反彈派發）
    elif (
        price_5d_pct >= p["s3_price_5d_min"]
        and has_liq
        and top3_5d < 0
        and ctrl_bear_s3
        and c5_bear_s3
    ):
        scenario_id = 3
        scenario_type = "bear"
        is_early = c20 > 0
        sub_type = "高檔初期派發" if is_early else "中繼反彈派發"
        title = f"🟢 當前劇本：3. 高檔派發（{'假突破初期出貨' if is_early else '弱勢反彈逃命'}）"
        desc = (
            f"近 5 日股價上漲 {price_5d_pct:+.2f}%，但三大波段主力逢高賣超 {top3_5d:+,} 張"
            f"（控盤率 {top3_5d_ctrl_pct:+.1f}%{z_ctrl_str}），5 日集中度降至 {c5:+.1f}%{z_c5_str}"
            f"（20 日集中度 {c20:+.1f}%）。"
        )
        action = (
            "波段大戶正趁散戶追價把籌碼倒出，慎防假突破與拉高套牢風險。"
            if is_early
            else "中線籌碼本已發散，主力再藉短線反彈持續調節，反彈應視為減碼逃命機會。"
        )

    # 情境 1：雙重共振（主升段）
    elif (
        above_ma10
        and above_ma20
        and ma_bull_align
        and (above_cost or top3_cost == 0)
        and has_liq
        and top3_5d > 0
        and ctrl_bull_s1
        and c5_bull_s1
        and c10 > 0
        and c20 > 0
        and (c5 >= c10 or c5 >= p["s1_c5_strong"] or (c5_z is not None and c5_z >= p["z_bull_min"]))
    ):
        scenario_id = 1
        scenario_type = "bull"
        sub_type = "主升共振"
        title = "🔴 當前劇本：1. 雙重共振（主升段）"
        desc = (
            f"收盤價（{latest_close:,.2f} 元）站穩 10MA（{ma10:,.2f} 元）、20MA（{ma20:,.2f} 元）與三大主力成本（{top3_cost:,.2f} 元）之上，"
            f"三大波段主力近 5 日買超 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%{z_ctrl_str}{float_str}），"
            f"且 20日({c20:+.1f}%) → 10日({c10:+.1f}%) → 5日({c5:+.1f}%{z_c5_str}) 集中度全面翻正。"
        )
        action = "最強多頭格局！波段大戶與短線大資金同步鎖碼，可沿 10MA 或三大主力成本線偏多操作。"

    # 情境 4：成本護盤（真洗盤）
    elif (
        near_or_below_cost
        and price_5d_pct <= p["s4_price_5d_max"]
        and has_liq
        and top3_5d > 0
        and ctrl_bull_s4
        and c5_bull_s4
    ):
        scenario_id = 4
        scenario_type = "bull"
        sub_type = "量縮護盤" if (vol_ratio_5_20 is None or vol_ratio_5_20 <= p["s4_max_vol_ratio"]) else "放量承接(需觀察)"
        title = f"🔴 當前劇本：4. 成本護盤（{'真洗盤' if sub_type == '量縮護盤' else '回測成本放量承接'}）"
        vol_txt = f"，5/20 均量比 {vol_ratio_5_20:.2f}x" if vol_ratio_5_20 is not None else ""
        desc = (
            f"收盤價（{latest_close:,.2f} 元）回測三大主力合計成本（{top3_cost:,.2f} 元）附近{vol_txt}，"
            f"但三大主力逆勢買超 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%{z_ctrl_str}），"
            f"5 日集中度維持 {c5:+.1f}%{z_c5_str}。"
        )
        action = "波段主力回測自身成本區積極護盤，趁恐慌承接浮額，止跌回穩後易展開下一波反彈。"

    # 情境 2：換手接棒（藏分點）
    elif (
        above_ma10
        and top3_5d_ctrl_pct <= p["s2_ctrl_max"]
        and c5_bull_s2
        and (c10 >= p["s2_c10_min"] or c20 < c5)
    ):
        scenario_id = 2
        scenario_type = "bull"
        sub_type = "新莊接手"
        title = "🔴 當前劇本：2. 換手接棒（藏分點／新主力進場）"
        desc = (
            f"收盤價（{latest_close:,.2f} 元）站上 10MA（{ma10:,.2f} 元），"
            f"原近 60 日三大主力近 5 日控盤率僅 {top3_5d_ctrl_pct:+.1f}%（{top3_5d:+,} 張），"
            f"但全市場 5 日集中度逆勢飆升至 {c5:+.1f}%{z_c5_str}。"
        )
        action = "有新莊家進場接手或主力改用其他分點暗中吃貨，建議鎖定「近 10 日新強勢分點」與「贏家分點」動向。"

    # 疊加六大籌碼共振確認層與分數
    badges, conviction_score, extra_notes = _build_resonance_layer(
        scenario_id=scenario_id,
        scenario_type=scenario_type,
        latest_close=latest_close,
        ma20=ma20,
        conc_5d=c5,
        trust_info=trust_info,
        broker_diff_info=broker_diff_info,
        margin_info=margin_info,
        sbl_info=sbl_info,
        tdcc_info=tdcc_info,
        day_trade_info=day_trade_info,
        vol_ratio_5_20=vol_ratio_5_20,
    )

    if extra_notes:
        action = f"{action} {' '.join(extra_notes)}"

    return {
        "id": scenario_id,
        "type": scenario_type,
        "sub_type": sub_type,
        "title": title,
        "desc": desc,
        "action": action,
        "badges": badges,
        "conviction_score": conviction_score,
    }


# =====================================================================
# 八、 近 5 日價量籌碼背離警報（加入金額防呆、Z-Score 與融資／借券驗證）
# =====================================================================
def evaluate_divergence_alert(
    price_5d_pct: float,
    top3_5d: int,
    top3_5d_ctrl_pct: float,
    latest_close: float = 0.0,
    top3_5d_amount_ntd: float = 0.0,
    top3_5d_ctrl_z: float | None = None,
    margin_info: dict | None = None,
    sbl_info: dict | None = None,
) -> dict | None:
    """偵測近 5 日價跌主力買（底部承接）或價漲主力賣（高檔出貨）背離，附帶金額防呆與融資驗證"""
    p = STRATEGY_PARAMS

    if not _check_amount_liquidity(top3_5d, latest_close, top3_5d_amount_ntd):
        return None

    z_str = f" / Z:{top3_5d_ctrl_z:+.1f}σ" if top3_5d_ctrl_z is not None else ""
    amt_wan = (
        abs(top3_5d_amount_ntd) / 10000.0
        if top3_5d_amount_ntd
        else abs(top3_5d) * max(latest_close, 0.0) * 0.1
    )
    amt_str = f"、約 {amt_wan:,.0f} 萬元" if amt_wan > 0 else ""

    bull_ctrl_ok = (top3_5d_ctrl_pct >= p["div_ctrl_pct"]) or (
        top3_5d_ctrl_pct > 0 and top3_5d_ctrl_z is not None and top3_5d_ctrl_z >= p["z_bull_min"]
    )
    bear_ctrl_ok = (top3_5d_ctrl_pct <= -p["div_ctrl_pct"]) or (
        top3_5d_ctrl_pct < 0 and top3_5d_ctrl_z is not None and top3_5d_ctrl_z <= p["z_bear_max"]
    )

    if price_5d_pct <= -p["div_price_pct"] and top3_5d > 0 and bull_ctrl_ok:
        extra = ""
        if margin_info and (margin_info.get("chg_5d") or 0) < 0:
            extra = (
                f"<br>🧹 <b>融資同步清洗 {margin_info['chg_5d']:+,} 張</b>，"
                f"確認為高勝率「散戶停損、主力低接」結構。"
            )
        return {
            "type": "bull",
            "title": "🔴 底部承接背離警報（近 5 日價跌主力買）",
            "desc": (
                f"近 5 日股價下跌 <b>{price_5d_pct:.2f}%</b>，但近 60 日三大波段主力逆勢合計淨買超 "
                f"<b style='color:#ef5350;'>{top3_5d:+,} 張</b>（控盤率 <b>{top3_5d_ctrl_pct:+.1f}%{z_str}</b>{amt_str}），"
                f"留意低檔換手與止跌轉折訊號。{extra}"
            ),
        }

    if price_5d_pct >= p["div_price_pct"] and top3_5d < 0 and bear_ctrl_ok:
        extra = ""
        if sbl_info and (sbl_info.get("chg_5d") or 0) > 0:
            extra = (
                f"<br>🐻 <b>借券賣出同步增加 {sbl_info['chg_5d']:+,} 張</b>，"
                f"現貨倒貨與機構放空並進，避險意識務必拉高。"
            )
        return {
            "type": "bear",
            "title": "🟢 高檔出貨背離警報（近 5 日價漲主力賣）",
            "desc": (
                f"近 5 日股價上漲 <b>{price_5d_pct:+.2f}%</b>，但近 60 日三大波段主力合計逢高賣超 "
                f"<b style='color:#26a69a;'>{top3_5d:+,} 張</b>（控盤率 <b>{top3_5d_ctrl_pct:+.1f}%{z_str}</b>{amt_str}），"
                f"慎防假突破或拉高倒貨。{extra}"
            ),
        }

    return None
