"""
strategy.py — 台股純籌碼集中／發散評分引擎與分點行為畫像規則庫
包含：
1. 滾動 60 日波段主力篩選、行為式隔日沖偵測、外資分點辨識與獨立事件贏家分點勝率標記
2. 100% 撇除股價均線干預之「50 分基準 ±50 分」五層純籌碼評分系統：
   - 第一層：全市場 5/10/20 日集中度（含 Z-Score 與多週期加速度）＋ 買賣家數差（±18 分）
   - 第二層：近 60 日三大波段主力控盤率（含金額防呆與 Z-Score）＋ 投信真實持股水位與動向（±14 分）
   - 第三層：融資四象限（區分主力融資 vs. 散戶融資）＋ 借券賣出（含相對成交量濾網）（±10 分）
   - 第四層：TDCC 集保大戶週變動歸戶驗證（±8 分）
   - 第五層：當日隔日沖灌水單向風險扣分（最高 -10 分）
3. 五大純籌碼結構狀態診斷（強力集中／隱形吸籌／籌碼沉澱／籌碼分歧／全面發散）
4. 視覺參考用之價量籌碼背離警報（不參與籌碼分數計算）與 Z-Score 動態說明產生器
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# =====================================================================
# 一、 已知國內隔日沖熱點分點 ＆ 外資券商關鍵字（用於切分本土波段融資）
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

FOREIGN_BROKER_KEYWORDS = (
    "摩根",
    "美林",
    "瑞銀",
    "高盛",
    "花旗",
    "匯豐",
    "野村",
    "大和",
    "麥格理",
    "法銀巴黎",
    "法巴",
    "法興",
    "德意志",
    "瑞士信貸",
    "瑞信",
    "巴克萊",
    "港商",
    "美商",
    "新加坡商",
    "日商",
    "英商",
    "歐商",
    "台灣摩根",
    "台灣匯立",
    "上海匯豐",
)


def is_foreign_broker(broker_name: str) -> bool:
    """判斷分點是否為外資券商（外資與法人依法不得使用一般信用交易融資）"""
    name = str(broker_name or "").strip()
    return any(kw in name for kw in FOREIGN_BROKER_KEYWORDS)


# =====================================================================
# 二、 純籌碼策略核心量化門檻參數（集中管理，方便隨時微調）
# =====================================================================
STRATEGY_PARAMS = {
    # 滾動視窗設定
    "wave_lookback_days": 60,        # 波段主力鎖定視窗（交易日）
    "zscore_window": 60,             # 滾動 Z-Score 計算視窗
    "winner_forward_days": 20,       # 贏家分點前瞻勝率評估天期

    # 金額與最低張數防呆（高價股看金額、低價股防碎單）
    "min_abs_amount_5d_ntd": 5_000_000,   # 近 5 日三大主力淨額下限（500 萬元）
    "min_abs_shares_floor": 5,            # 最低張數底線（避免超高價股 1~2 張雜訊）

    # 滾動 Z-Score 自適應觸發門檻
    "z_bull_min": 1.5,               # Z-Score >= +1.5σ 視為顯著異常吸籌
    "z_bull_mild": 1.0,              # Z-Score >= +1.0σ 視為溫和偏多吸籌
    "z_bear_max": -1.5,              # Z-Score <= -1.5σ 視為顯著異常倒貨
    "z_bear_mild": -1.0,             # Z-Score <= -1.0σ 視為溫和偏空調節

    # 分點行為畫像門檻（隔日沖偵測 & 贏家分點判定）
    "dt_min_active_days": 6,         # 近 60 日至少有進出天數
    "dt_churn_ratio_min": 3.5,       # 週轉係數（總進出張數 / 淨額絕對值）門檻
    "dt_reversal_rate_min": 0.45,    # 買超隔日立即反手賣超 >= 50% 之比例
    "dt_today_vol_warn_pct": 5.0,    # 當日隔日沖買超佔總成交量警戒門檻 (%) -> -6 分
    "dt_today_vol_heavy_pct": 10.0,  # 當日隔日沖買超佔總成交量重度門檻 (%) -> -10 分
    "winner_cooldown_days": 10,      # 贏家分點獨立買超事件最小冷卻間隔（交易日）
    "winner_min_signals": 3,         # 贏家分點最少獨立顯著買超次數
    "winner_min_win_rate": 60.0,     # 贏家分點 20 日勝率下限 (%)
    "winner_min_avg_ret": 3.0,       # 贏家分點 20 日平均報酬下限 (%)

    # 第一層：全市場集中度門檻 (%)
    "conc_5d_strong": 10.0,          # 5日集中度顯著集中／發散 (±8 分)
    "conc_5d_mild": 3.0,             # 5日集中度溫和集中／發散 (±4 分)
    "diff_neg_streak_bonus": 3,      # 買賣家數差連負天數加分門檻 (+2 分)

    # 第二層：波段主力控盤率 (%) 與投信門檻
    "ctrl_5d_strong": 5.0,           # 三大主力 5日控盤率顯著吸籌／倒貨 (±8 分)
    "ctrl_5d_mild": 2.0,             # 三大主力 5日控盤率溫和買超／調節 (±4 分)
    "s2_ctrl_max": 1.5,              # 狀態 2 隱形吸籌：原三大主力控盤率上限 (%)
    "s2_c5_min": 5.0,                # 狀態 2 隱形吸籌：全市場 5日集中度下限 (%)
    "trust_streak_bull": 3,          # 投信連買共振天數
    "trust_streak_bear": -2,         # 投信連賣結帳天數
    "trust_high_holding_pct": 10.0,  # 投信真實持股比率高檔結帳警戒線 (%)
    "trust_sweet_holding_max": 5.0,  # 投信初升認養甜蜜區間上限 (%)

    # 第三層：信用（融資四象限）與借券賣出門檻 ＋ 相對成交量濾網
    "credit_vol_ratio_min": 3.0,     # |5日增減張數| ÷ 5日總成交量下限 (%)，防冷門股小分母失真
    "margin_surge_5d_pct": 5.0,      # 融資近 5 日顯著增加門檻 (%)
    "margin_clean_5d_pct": -2.0,     # 融資近 5 日顯著減少門檻 (%)
    "sbl_surge_5d_pct": 5.0,         # 借券賣出近 5 日大增門檻 (%)
    "sbl_cover_5d_pct": -3.0,        # 借券賣出近 5 日顯著回補門檻 (%)

    # 第四層：TDCC 集保大戶週變動門檻 (%)
    "tdcc_strong_chg_pct": 0.5,      # 週大戶比率變動 ±0.5% 且股東反向 (±8 分)
    "tdcc_mild_chg_pct": 0.2,        # 週大戶比率變動 ±0.2% 且股東反向 (±5 分)

    # 視覺輔助：近 5 日價量籌碼背離警報門檻（不影響純籌碼健康分數）
    "div_price_pct": 3.0,            # 近 5 日漲跌幅門檻 (%)
    "div_ctrl_pct": 2.0,             # 近 5 日三大主力控盤率門檻 (%)
    "div_c5_warn_max": -3.0,         # 底部背離時，若全市場 5 日集中度 <= -3%，降級為黃色防接刀警示
    "div_trust_warn_streak": -3,     # 底部背離時，若投信連賣 >= 3 日，降級為黃色防接刀警示
}

# 快捷上色標籤（紅＝偏多／集中、綠＝偏空／發散、橘＝中性／警戒）
_R = lambda s: f'<span style="color:#ef5350; font-weight:700;">{s}</span>'
_G = lambda s: f'<span style="color:#26a69a; font-weight:700;">{s}</span>'
_Y = lambda s: f'<span style="color:#ffb74d; font-weight:700;">{s}</span>'

# =====================================================================
# 三、 可收合抽屜「純籌碼結構 5 大狀態判讀矩陣」（100% 撇除均線與漲跌）
# =====================================================================
SCENARIO_MATRIX = [
    {
        "id": 1,
        "name": "1. 強力集中（點面共振）",
        "type": "bull",
        "main_cond": f"近60日三大波段主力<br>5日控盤 {_R('≥ +2.0%')} 或 {_R('Z ≥ +1.5σ')}<br>（淨額 ≥ 500萬、過濾隔日沖）",
        "conc_cond": f"5 / 10 日{_R('全面翻正')}且 5日 {_R('≥ +3.0%')}（或 {_R('Z ≥ +1.5σ')}）<br>買賣家數差為{_R('負值')}（真收籌）",
        "aux_cond": f"投信連買、融資清洗／主力融資點火、<br>借券回補或集保大戶增持同步加分",
        "price_cond": f"純籌碼健康分數 {_R('≥ 60 分')}<br>且當日隔日沖佔比未逾重度警戒",
        "action": "點、面同步鎖碼的最強籌碼結構。無論股價處於底部橫盤或剛轉強，皆具高度關注價值；若後續出現隔日沖過重則留意短線震盪。",
    },
    {
        "id": 2,
        "name": "2. 隱形吸籌（換手接棒）",
        "type": "bull",
        "main_cond": f"原近60日三大波段主力<br>5日控盤 {_Y('≤ +1.5%')}（持平或小幅調節）",
        "conc_cond": f"全市場 5日集中度飆高 {_R('≥ +5.0%')} 或 {_R('Z ≥ +1.5σ')}<br>且買賣家數差為{_R('負值')}",
        "aux_cond": f"若搭配「近10日新崛起分點」、贏家分點進場<br>或集保大戶上升更能確認換莊成功",
        "price_cond": f"純籌碼健康分數 {_R('≥ 55 分')}<br>（排除單日隔日沖暴力灌水）",
        "action": "新莊家進場接棒或原主力透過其他分點暗中藏單吸籌。建議直接追蹤「近 10 日新崛起波段分點」與「贏家分點」動向。",
    },
    {
        "id": 3,
        "name": "3. 籌碼沉澱（浮額清洗）",
        "type": "bull",
        "main_cond": f"波段主力進出溫和或鎖碼觀望<br>未出現明顯倒貨賣壓",
        "conc_cond": f"全市場 5日集中度維持正值<br>或買賣家數差連續為負（籌碼由散戶流向大戶）",
        "aux_cond": f"符合任一強沉澱訊號：{_R('融資清洗（主力承接）')}、<br>{_R('借券顯著回補')}、{_R('集保千張大戶增＋股東減')}",
        "price_cond": f"純籌碼健康分數 {_R('≥ 55 分')}<br>籌碼正悄悄變輕、賣壓逐步枯竭",
        "action": "屬於高勝率潛伏結構。籌碼已由不穩定的槓桿與散戶流出、沉澱至長線持有者手中，等待後續大資金點火。",
    },
    {
        "id": 4,
        "name": "4. 籌碼分歧（多空對作／雜訊）",
        "type": "neutral",
        "main_cond": f"點（三大波段主力）與面（全市場集中度）<br>方向相反，或分點與投信／借券互相對作",
        "conc_cond": f"5日集中度為正但{_Y('買賣家數差也為正')}（散戶同追）<br>或帳面集中但夾雜大量{_Y('隔日沖短線客')}",
        "aux_cond": f"常見組合：現貨買超但借券放空大增、<br>分點集中但集保歸戶大戶反而流出",
        "price_cond": f"純籌碼健康分數多落於 {_Y('36 ～ 65 分')}<br>方向尚未統一，雜訊偏高",
        "action": "籌碼結構出現內部矛盾，短線容易劇烈震盪或誘多洗盤。建議觀望，靜待分歧收斂、多空一方明確勝出再介入。",
    },
    {
        "id": 5,
        "name": "5. 全面發散（大戶撤退）",
        "type": "bear",
        "main_cond": f"三大波段主力倒貨 {_G('≤ -2.0%')} 或 {_G('Z ≤ -1.5σ')}<br>或外圍法人集體連賣撤退",
        "conc_cond": f"5日集中度轉負 {_G('≤ -3.0%')} 或 {_G('Z ≤ -1.5σ')}<br>買賣家數差轉正（少數大戶賣、多數散戶接）",
        "aux_cond": f"常伴隨{_G('散戶融資過熱')}、{_G('主力融資撤退')}、<br>{_G('投信結帳')}、{_G('借券空單加壓')}或{_G('集保大戶流出')}",
        "price_cond": f"純籌碼健康分數 {_G('≤ 35 分')}<br>或點面同步呈現顯著發散",
        "action": "籌碼正全面由大資金流向散戶，結構最為脆弱。切忌因短線反彈或帳面超跌而盲目承接，應待集中度與家數差止穩收斂。",
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
# 五、 分點行為畫像：隔日沖偵測、贏家勝率評分（含事件去重）與滾動 60 日主力篩選
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
    2. 顯著買超後 20 日前瞻勝率與平均報酬（套用事件冷卻期去重） -> 自動標記 🏆 贏家分點
    3. 標記 is_foreign，供融資四象限交叉比對本土波段買超張數
    4. 同步回傳近 60 日（波段）、近 10 日（短線）、近 5 日、今日與今年累計淨張數
    """
    p = STRATEGY_PARAMS
    lb = lookback_days or p["wave_lookback_days"]
    fwd = forward_days or p["winner_forward_days"]
    cooldown = int(p.get("winner_cooldown_days", 10))

    if df_price_full is None or df_price_full.empty or df_chips_full is None or df_chips_full.empty:
        return {}

    broker_cols = [c for c in df_chips_full.columns if c not in ("date", "投信")]
    if not broker_cols:
        return {}

    df_m = df_price_full[["date", "close"]].merge(df_chips_full, on="date", how="left").fillna(0.0)
    df_m = df_m.sort_values("date").reset_index(drop=True)

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

        # --- 1. 隔日沖行為指標（近 60 日） ---
        arr = s_60.to_numpy(dtype=float)
        nonzero_mask = arr != 0
        active_days = int(nonzero_mask.sum())
        gross_flow = float(np.abs(arr).sum())
        abs_net = abs(float(arr.sum()))
        churn_ratio = round(gross_flow / max(abs_net, 1.0), 2) if gross_flow > 0 else 0.0

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

        # --- 2. 贏家分點勝率（獨立事件去重，避免連續買超膨脹樣本與勝率） ---
        valid_mask = fwd_ret_s.notna() & (s_all > 0)
        win_rate = None
        avg_fwd_ret = None
        buy_signal_cnt = 0
        if valid_mask.any():
            pos_hist = s_all[valid_mask]
            q75 = float(pos_hist.quantile(0.75)) if len(pos_hist) >= 4 else float(pos_hist.median())
            sig_mask = valid_mask & (s_all >= max(q75, 5.0))
            raw_indices = np.where(sig_mask.to_numpy())[0]

            declustered_indices = []
            last_idx = -cooldown - 1
            for idx in raw_indices:
                if idx - last_idx >= cooldown:
                    declustered_indices.append(idx)
                    last_idx = idx

            buy_signal_cnt = len(declustered_indices)
            if buy_signal_cnt >= 2:
                sig_rets = fwd_ret_s.iloc[declustered_indices]
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
            "is_foreign": is_foreign_broker(col),
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

    if not wave_candidates:
        wave_candidates = [item["name"] for item in positive_items[:top_n]]

    return wave_candidates[:top_n], filtered_dt


# =====================================================================
# 六、 投信持股水位與買賣動向分流評估（買紅／賣綠明確分色）
# =====================================================================
def evaluate_trust_phase(trust_info: dict | None) -> dict | None:
    """
    將投信狀態拆分為兩個獨立語意維度：
    1. momentum / momentum_level：嚴格依實際買賣超上色（買超／連買＝bull 紅、賣超／連賣＝bear 綠、持平＝info 灰）
    2. tier_label / tier_level：真實持股水位（僅在偏買超時稱「初升認養甜蜜期」，連賣時中性顯示「持股水位 X%」）
    """
    if not trust_info:
        return None

    p = STRATEGY_PARAMS
    holding_pct = trust_info.get("holding_pct")
    streak = int(trust_info.get("streak", 0))
    d1 = int(trust_info.get("d1", 0))
    d5 = int(trust_info.get("d5", 0))
    d20 = int(trust_info.get("d20", 0))

    if streak >= p["trust_streak_bull"]:
        momentum = f"連買 {streak} 日認養中"
        momentum_level = "bull"
    elif streak > 0:
        momentum = f"連買 {streak} 日偏多"
        momentum_level = "bull"
    elif streak <= p["trust_streak_bear"]:
        momentum = f"連賣 {abs(streak)} 日調節／結帳"
        momentum_level = "bear"
    elif streak < 0:
        momentum = f"連賣 {abs(streak)} 日調節"
        momentum_level = "bear"
    elif d5 > 0:
        momentum = "近 5 日偏買超"
        momentum_level = "bull"
    elif d5 < 0:
        momentum = "近 5 日偏賣超"
        momentum_level = "bear"
    else:
        momentum = "中性觀望"
        momentum_level = "info"

    is_buying = (momentum_level == "bull") or (streak == 0 and d1 > 0)

    tier_label = "持股比待補"
    tier_level = "info"
    if holding_pct is not None:
        if holding_pct >= p["trust_high_holding_pct"]:
            tier_label = f"高水位警戒 ({holding_pct:.2f}%)"
            tier_level = "warn"
        elif holding_pct >= p["trust_sweet_holding_max"]:
            tier_label = f"中段持股水位 ({holding_pct:.2f}%)"
            tier_level = "info"
        elif holding_pct >= 0.5:
            if is_buying:
                tier_label = f"初升認養甜蜜期 ({holding_pct:.2f}%)"
                tier_level = "bull"
            else:
                tier_label = f"持股水位 {holding_pct:.2f}%"
                tier_level = "info"
        elif holding_pct > 0:
            if is_buying:
                tier_label = f"剛建立部位 ({holding_pct:.2f}%)"
                tier_level = "bull"
            else:
                tier_label = f"低持股水位 ({holding_pct:.2f}%)"
                tier_level = "info"
        else:
            tier_label = "尚無明顯持股 (0%)"
            tier_level = "info"

    return {
        "tier_label": tier_label,
        "tier_level": tier_level,
        "momentum": momentum,
        "momentum_level": momentum_level,
        "holding_pct": holding_pct,
        "d20": d20,
    }


# =====================================================================
# 七、 純籌碼五層計分引擎（50 分基準 ±50 分）＋ 五大純籌碼狀態診斷
# =====================================================================
def _check_amount_liquidity(top3_5d: int, latest_close: float, top3_5d_amount_ntd: float) -> bool:
    """檢查近 5 日主力進出是否達最低有效金額與張數底線（防冷門股碎單雜訊）"""
    p = STRATEGY_PARAMS
    if abs(top3_5d) < p["min_abs_shares_floor"]:
        return False
    est_amt = abs(top3_5d_amount_ntd) if top3_5d_amount_ntd else abs(top3_5d) * max(latest_close, 0.0) * 1000.0
    return est_amt >= p["min_abs_amount_5d_ntd"]


def _resolve_vol_ratio_pct(info: dict | None, chg_5d: int | float | None, vol_5d: float) -> float | None:
    """取得信用／借券 5 日變動張數佔近 5 日總成交量之比率 (%)"""
    if isinstance(info, dict) and info.get("chg_5d_vol_pct") is not None:
        return abs(float(info["chg_5d_vol_pct"]))
    if chg_5d is not None and vol_5d and vol_5d > 0:
        return round(abs(float(chg_5d)) / float(vol_5d) * 100.0, 2)
    return None


def evaluate_chip_scenario(
    top3_5d: int,
    top3_5d_ctrl_pct: float,
    latest_close: float = 0.0,
    ma10: float = 0.0,
    ma20: float = 0.0,
    top3_cost: float = 0.0,
    price_5d_pct: float = 0.0,
    conc: dict | None = None,
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
    vol_5d: float = 0.0,
    domestic_top_buy_5d: int | None = None,
) -> dict:
    """
    100% 純籌碼評分與結構診斷引擎（完全撇除 ma10 / ma20 / price_5d_pct / top3_cost 干預）：
    - 保留舊參數簽章以維持向下相容，但評分與五大狀態判定完全不看均線與股價漲跌。
    - 總分計算：基準 50 分 + 五層純籌碼加減分，鎖定於 [0, 100]。
    """
    _ = (ma10, ma20, top3_cost, price_5d_pct, vol_ratio_5_20)  # 明確不使用任何價量均線參數
    p = STRATEGY_PARAMS

    c5_dict = conc.get("d5", {}) if isinstance(conc, dict) else {}
    c10_dict = conc.get("d10", {}) if isinstance(conc, dict) else {}
    c20_dict = conc.get("d20", {}) if isinstance(conc, dict) else {}

    c5 = c5_dict.get("pct")
    c10 = c10_dict.get("pct")
    c20 = c20_dict.get("pct")
    c5_z = c5_dict.get("z_score")

    if c5 is None:
        return {
            "id": 0,
            "type": "neutral",
            "sub_type": "資料未齊",
            "title": "⚪ 籌碼中性整理（尚未取得完整集中度數據）",
            "desc": "請點擊左側「全部更新」或個股旁「🔄」以補齊最新 5 / 10 / 20 日全市場集中度。",
            "action": "",
            "badges": [],
            "conviction_score": 50,
            "score_breakdown": {"base": 50, "conc_diff": 0, "wave_trust": 0, "credit_sbl": 0, "tdcc": 0, "day_trade": 0},
            "margin_quadrant": 0,
        }

    badges: list[dict] = []
    extra_notes: list[str] = []

    # 預先整理共用訊號（供融資四象限、衝突檢核與狀態分類共用）
    diff_5d = broker_diff_info.get("diff_5d") if isinstance(broker_diff_info, dict) else None
    neg_streak = int(broker_diff_info.get("neg_streak", 0)) if isinstance(broker_diff_info, dict) else 0
    dt_vol_pct = float(day_trade_info.get("dt_vol_pct", 0.0) or 0.0) if isinstance(day_trade_info, dict) else 0.0
    is_dt_warn = dt_vol_pct >= p["dt_today_vol_warn_pct"]
    is_dt_heavy = dt_vol_pct >= p["dt_today_vol_heavy_pct"]

    tdcc_state = "neutral"
    whale_delta = None
    whale_lbl = "千張"
    holders_chg = None
    if isinstance(tdcc_info, dict):
        w1000_chg = tdcc_info.get("whale_1000_chg_1w")
        w400_chg = tdcc_info.get("whale_400_chg_1w")
        holders_chg = tdcc_info.get("holders_chg_1w")
        whale_delta = w1000_chg if w1000_chg is not None else w400_chg
        whale_lbl = "千張" if w1000_chg is not None else "400張"
        if whale_delta is not None and holders_chg is not None:
            if whale_delta >= p["tdcc_mild_chg_pct"] and holders_chg < 0:
                tdcc_state = "bull"
            elif whale_delta <= -p["tdcc_mild_chg_pct"] and holders_chg > 0:
                tdcc_state = "bear"

    # =================================================================
    # 第一層：全市場集中度（5/10/20日 ＋ Z-Score）與買賣家數差（上限 ±18 分）
    # =================================================================
    s_conc_level = 0
    if c5 >= p["conc_5d_strong"] or (c5 > 0 and c5_z is not None and c5_z >= p["z_bull_min"]):
        s_conc_level = 8
        badges.append({
            "level": "bull",
            "label": f"🔥 5日顯著集中 ({c5:+.1f}%)",
            "detail": f"全市場 5 日集中度達 {c5:+.2f}%{' (Z:' + f'{c5_z:+.1f}σ)' if c5_z is not None else ''}，屬強勢吸籌水準 (+8 分)。",
        })
    elif c5 >= p["conc_5d_mild"] or (c5 > 0 and c5_z is not None and c5_z >= p["z_bull_mild"]):
        s_conc_level = 4
    elif c5 <= -p["conc_5d_strong"] or (c5 < 0 and c5_z is not None and c5_z <= p["z_bear_max"]):
        s_conc_level = -8
        badges.append({
            "level": "bear",
            "label": f"⚠️ 5日顯著發散 ({c5:+.1f}%)",
            "detail": f"全市場 5 日集中度降至 {c5:+.2f}%{' (Z:' + f'{c5_z:+.1f}σ)' if c5_z is not None else ''}，大資金明顯撤出 (-8 分)。",
        })
    elif c5 <= -p["conc_5d_mild"] or (c5 < 0 and c5_z is not None and c5_z <= p["z_bear_mild"]):
        s_conc_level = -4

    s_conc_slope = 0
    if c10 is not None and c20 is not None:
        if c5 > 0 and c10 > 0 and c20 > 0:
            if c5 >= c10:
                s_conc_slope = 4
                badges.append({
                    "level": "bull",
                    "label": "📈 多週期加速吸籌",
                    "detail": f"20日({c20:+.1f}%) → 10日({c10:+.1f}%) → 5日({c5:+.1f}%) 全正且短線加速集中 (+4 分)。",
                })
            else:
                s_conc_slope = 2
        elif c5 < 0 and c10 < 0 and c20 < 0:
            if c5 <= c10:
                s_conc_slope = -4
                badges.append({
                    "level": "bear",
                    "label": "📉 多週期加速發散",
                    "detail": f"20日({c20:+.1f}%) → 10日({c10:+.1f}%) → 5日({c5:+.1f}%) 全負且短線發散惡化 (-4 分)。",
                })
            else:
                s_conc_slope = -2

    s_diff = 0
    is_retail_chasing = False
    if diff_5d is not None:
        if diff_5d < 0 and c5 > 0:
            s_diff = 4
            bonus_txt = ""
            if neg_streak >= p["diff_neg_streak_bonus"]:
                s_diff += 2
                bonus_txt = f"｜連負{neg_streak}日"
            badges.append({
                "level": "bull",
                "label": f"🧲 真收籌（5日家數差 {diff_5d:+}{bonus_txt}）",
                "detail": f"買超集中在少數分點、賣超分散於多數分點，確認籌碼由散戶流向主力 (+{s_diff} 分)。",
            })
        elif diff_5d > 0 and c5 > 0:
            s_diff = -3
            is_retail_chasing = True
            badges.append({
                "level": "warn",
                "label": f"👥 散戶同追（5日家數差 {diff_5d:+}）",
                "detail": "雖然 5 日集中度為正，但買超家數大於賣超家數，顯示散戶同步追進、籌碼偏凌亂 (-3 分)。",
            })
        elif diff_5d > 0 and c5 < 0:
            s_diff = -6
            badges.append({
                "level": "bear",
                "label": f"💸 籌碼渙散（5日家數差 {diff_5d:+}）",
                "detail": "少數大戶賣出、多數散戶接手，典型籌碼發散格局 (-6 分)。",
            })

    layer1_score = s_conc_level + s_conc_slope + s_diff

    # =================================================================
    # 第二層：近 60 日波段分點主力 ＋ 投信動向與真實持股比（上限 ±14 分）
    # =================================================================
    has_liq = _check_amount_liquidity(top3_5d, latest_close, top3_5d_amount_ntd)
    s_wave = 0
    if has_liq:
        if top3_5d > 0:
            if top3_5d_ctrl_pct >= p["ctrl_5d_strong"] or (top3_5d_ctrl_z is not None and top3_5d_ctrl_z >= p["z_bull_min"]):
                s_wave = 8
                badges.append({
                    "level": "bull",
                    "label": f"🎯 波段主力重手吸籌 ({top3_5d_ctrl_pct:+.1f}%)",
                    "detail": f"近 60 日三大波段主力近 5 日買超 {top3_5d:+,} 張（控盤 {top3_5d_ctrl_pct:+.1f}%{' / Z:' + f'{top3_5d_ctrl_z:+.1f}σ' if top3_5d_ctrl_z is not None else ''}）(+8 分)。",
                })
            elif top3_5d_ctrl_pct >= p["ctrl_5d_mild"] or (top3_5d_ctrl_z is not None and top3_5d_ctrl_z >= p["z_bull_mild"]):
                s_wave = 4
        elif top3_5d < 0:
            if top3_5d_ctrl_pct <= -p["ctrl_5d_strong"] or (top3_5d_ctrl_z is not None and top3_5d_ctrl_z <= p["z_bear_max"]):
                s_wave = -8
                badges.append({
                    "level": "bear",
                    "label": f"🎯 波段主力重手倒貨 ({top3_5d_ctrl_pct:+.1f}%)",
                    "detail": f"近 60 日三大波段主力近 5 日賣超 {top3_5d:+,} 張（控盤 {top3_5d_ctrl_pct:+.1f}%{' / Z:' + f'{top3_5d_ctrl_z:+.1f}σ' if top3_5d_ctrl_z is not None else ''}）(-8 分)。",
                })
            elif top3_5d_ctrl_pct <= -p["ctrl_5d_mild"] or (top3_5d_ctrl_z is not None and top3_5d_ctrl_z <= p["z_bear_mild"]):
                s_wave = -4

    s_trust = 0
    trust_state = "neutral"
    if isinstance(trust_info, dict):
        t_streak = int(trust_info.get("streak", 0))
        t_d5 = int(trust_info.get("d5", 0))
        t_hold = trust_info.get("holding_pct")

        if t_streak >= p["trust_streak_bull"] and t_d5 > 0:
            trust_state = "bull"
            if t_hold is not None and t_hold < p["trust_sweet_holding_max"]:
                s_trust = 6
                badges.append({
                    "level": "bull",
                    "label": f"🔥 投信初升認養（連買{t_streak}日｜持股{t_hold:.1f}%）",
                    "detail": "投信連續買超且真實持股仍在 5% 以下甜蜜區間，籌碼鎖定度高 (+6 分)。",
                })
            elif t_hold is not None and t_hold >= p["trust_high_holding_pct"]:
                s_trust = 1
                badges.append({
                    "level": "warn",
                    "label": f"🏦 投信連買{t_streak}日但高水位 ({t_hold:.1f}%)",
                    "detail": "投信雖仍買超，但真實持股已達 10% 以上高檔區，留意後續結帳風險 (+1 分)。",
                })
            else:
                s_trust = 4
                badges.append({
                    "level": "bull",
                    "label": f"🔥 投信共振（連買{t_streak}日）",
                    "detail": f"投信近 5 日買超 {t_d5:+,} 張，形成穩定法人籌碼支撐 (+4 分)。",
                })
        elif t_streak <= p["trust_streak_bear"] and t_d5 < 0:
            trust_state = "bear"
            s_trust = -6
            high_tag = f"（持股{t_hold:.1f}%）" if (t_hold is not None and t_hold >= p["trust_high_holding_pct"]) else ""
            badges.append({
                "level": "bear",
                "label": f"🏦 投信結帳調節（連賣{abs(t_streak)}日）",
                "detail": f"投信近 5 日賣超 {t_d5:+,} 張{high_tag}，法人籌碼持續流出 (-6 分)。",
            })

    layer2_score = s_wave + s_trust

    # =================================================================
    # 第三層：融資四象限 ＋ 借券賣出（雙重套用相對成交量濾網，上限 ±10 分）
    # =================================================================
    s_margin = 0
    margin_quadrant = 0  # 1:主力融資點火 2:散戶融資過熱 3:融資清洗 4:主力融資撤退
    if isinstance(margin_info, dict):
        m_chg_5d = margin_info.get("chg_5d")
        m_chg_pct = margin_info.get("chg_5d_pct")
        m_z = margin_info.get("chg_5d_z")
        m_vol_pct = _resolve_vol_ratio_pct(margin_info, m_chg_5d, vol_5d)
        m_vol_ok = True if m_vol_pct is None else (m_vol_pct >= p["credit_vol_ratio_min"])
        dom_buy = margin_info.get("domestic_top_buy_5d", domestic_top_buy_5d)

        if m_chg_5d is not None and m_chg_pct is not None and m_vol_ok:
            is_m_surge = (m_chg_5d > 0) and (
                m_chg_pct >= p["margin_surge_5d_pct"] or (m_z is not None and m_z >= p["z_bull_min"])
            )
            is_m_clean = (m_chg_5d < 0) and (
                m_chg_pct <= p["margin_clean_5d_pct"] or (m_z is not None and m_z <= p["z_bear_max"])
            )
            vol_note = f"、佔5日量 {m_vol_pct:.1f}%" if m_vol_pct is not None else ""

            if is_m_surge:
                # 區分「主力融資點火」vs.「散戶融資過熱」
                if c5 > 0 and (diff_5d is not None and diff_5d < 0) and not is_dt_warn and tdcc_state != "bear":
                    margin_quadrant = 1
                    s_margin = 3
                    dom_note = ""
                    if dom_buy is not None and dom_buy >= m_chg_5d * 0.5:
                        dom_note = f"，且本土波段分點同步買超 {int(dom_buy):+,} 張"
                    badges.append({
                        "level": "bull",
                        "label": f"🔥 疑似主力融資點火（5日 {m_chg_5d:+,}張）",
                        "detail": (
                            f"融資近 5 日增 {m_chg_5d:+,} 張（{m_chg_pct:+.1f}%{vol_note}），"
                            f"但全市場集中度為正且買賣家數差為負{dom_note}，屬籌碼集中型槓桿點火 (+3 分)。"
                        ),
                    })
                elif c5 <= 0 or (diff_5d is not None and diff_5d > 0) or tdcc_state == "bear":
                    margin_quadrant = 2
                    s_margin = -5
                    badges.append({
                        "level": "bear",
                        "label": f"⚠️ 散戶融資過熱（5日 {m_chg_5d:+,}張 / {m_chg_pct:+.1f}%）",
                        "detail": (
                            f"融資近 5 日大增（{m_chg_pct:+.1f}%{vol_note}）且伴隨籌碼發散或買家數擴大，"
                            f"屬散戶槓桿浮額擴散警訊 (-5 分)。"
                        ),
                    })
                else:
                    badges.append({
                        "level": "warn",
                        "label": f"⚡ 融資激增夾雜短沖（5日 {m_chg_5d:+,}張）",
                        "detail": f"融資大增（{m_chg_pct:+.1f}%{vol_note}）但同時伴隨隔日沖過重，不計入主力點火加分 (0 分)。",
                    })

            elif is_m_clean:
                # 區分「融資浮額清洗（主力承接）」vs.「主力融資撤退（伴隨發散）」
                if c5 > 0 or (diff_5d is not None and diff_5d < 0):
                    margin_quadrant = 3
                    s_margin = 5
                    badges.append({
                        "level": "bull",
                        "label": f"🧹 融資清洗・主力承接（5日 {m_chg_5d:+,}張）",
                        "detail": (
                            f"融資近 5 日減少 {m_chg_5d:+,} 張（{m_chg_pct:+.1f}%{vol_note}）"
                            f"同時集中度為正或家數差為負，確認浮額流向主力 (+5 分)。"
                        ),
                    })
                elif c5 < 0 and (diff_5d is None or diff_5d >= 0):
                    margin_quadrant = 4
                    s_margin = -3
                    badges.append({
                        "level": "bear",
                        "label": f"💨 融資退場伴隨發散（5日 {m_chg_5d:+,}張）",
                        "detail": (
                            f"融資減少（{m_chg_pct:+.1f}%{vol_note}）但集中度同步轉負且無少數主力承接，"
                            f"疑為原本融資主力結帳撤退 (-3 分)。"
                        ),
                    })

    s_sbl = 0
    sbl_state = "neutral"
    if isinstance(sbl_info, dict):
        sbl_chg_5d = sbl_info.get("chg_5d")
        sbl_chg_pct = sbl_info.get("chg_5d_pct")
        sbl_z = sbl_info.get("chg_5d_z")
        sbl_vol_pct = _resolve_vol_ratio_pct(sbl_info, sbl_chg_5d, vol_5d)
        sbl_vol_ok = True if sbl_vol_pct is None else (sbl_vol_pct >= p["credit_vol_ratio_min"])

        if sbl_chg_5d is not None and sbl_chg_pct is not None and sbl_vol_ok:
            vol_note = f"、佔5日量 {sbl_vol_pct:.1f}%" if sbl_vol_pct is not None else ""
            if sbl_chg_5d < 0 and (
                sbl_chg_pct <= p["sbl_cover_5d_pct"] or (sbl_z is not None and sbl_z <= p["z_bear_max"])
            ):
                s_sbl = 5
                sbl_state = "cover"
                badges.append({
                    "level": "bull",
                    "label": f"🚀 借券回補（5日 {sbl_chg_5d:+,}張 / {sbl_chg_pct:+.1f}%）",
                    "detail": f"機構空方回補借券（{sbl_chg_pct:+.1f}%{vol_note}），空單籌碼壓力顯著減輕 (+5 分)。",
                })
            elif sbl_chg_5d > 0 and (
                sbl_chg_pct >= p["sbl_surge_5d_pct"] or (sbl_z is not None and sbl_z >= p["z_bull_min"])
            ):
                s_sbl = -5
                sbl_state = "surge"
                badges.append({
                    "level": "bear",
                    "label": f"🐻 借券賣出加壓（5日 {sbl_chg_5d:+,}張 / {sbl_chg_pct:+.1f}%）",
                    "detail": f"外資或機構空單明顯加壓（{sbl_chg_pct:+.1f}%{vol_note}），慎防假集中真鎖空 (-5 分)。",
                })

    layer3_score = s_margin + s_sbl

    # =================================================================
    # 第四層：TDCC 集保戶股權分散表（每週歸戶總驗證，上限 ±8 分）
    # =================================================================
    layer4_score = 0
    if whale_delta is not None and holders_chg is not None:
        if whale_delta >= p["tdcc_strong_chg_pct"] and holders_chg < 0:
            layer4_score = 8
            badges.append({
                "level": "bull",
                "label": f"🐋 集保強確認（{whale_lbl} {whale_delta:+.2f}%｜股東 {holders_chg:+,}人）",
                "detail": "週歸戶數據證實大戶顯著增持且股東人數減少，排除藏分點與匯撥干擾 (+8 分)。",
            })
        elif whale_delta >= p["tdcc_mild_chg_pct"] and holders_chg < 0:
            layer4_score = 5
            badges.append({
                "level": "bull",
                "label": f"🐋 集保溫和集中（{whale_lbl} {whale_delta:+.2f}%｜股東 {holders_chg:+,}人）",
                "detail": "歸戶大戶持股上升、總股東人數下降，籌碼流向大戶 (+5 分)。",
            })
        elif whale_delta <= -p["tdcc_strong_chg_pct"] and holders_chg > 0:
            layer4_score = -8
            badges.append({
                "level": "bear",
                "label": f"⚠️ 集保強發散（{whale_lbl} {whale_delta:+.2f}%｜股東 {holders_chg:+,}人）",
                "detail": "歸戶大戶大幅流出且散戶人數暴增，慎防主力拆單或匯撥暗中出貨 (-8 分)。",
            })
        elif whale_delta <= -p["tdcc_mild_chg_pct"] and holders_chg > 0:
            layer4_score = -5
            badges.append({
                "level": "bear",
                "label": f"⚠️ 集保大戶流出（{whale_lbl} {whale_delta:+.2f}%｜股東 {holders_chg:+,}人）",
                "detail": "歸戶大戶減少且總股東人數增加，籌碼趨向發散 (-5 分)。",
            })

    # =================================================================
    # 第五層：隔日沖灌水單向濾網（僅在偏多集中 > 50 分時扣分，最高 -10 分）
    # =================================================================
    raw_score = 50 + layer1_score + layer2_score + layer3_score + layer4_score
    layer5_score = 0
    if is_dt_warn:
        dt_names = ", ".join((day_trade_info or {}).get("dt_buyers", [])[:3])
        if raw_score > 50:
            layer5_score = -10 if is_dt_heavy else -6
            extra_notes.append(f"⚠️ 今日買盤含高比例隔日沖（{dt_vol_pct:.1f}%），已扣減 {abs(layer5_score)} 分防假集中。")
        badges.append({
            "level": "warn",
            "label": f"⚡ 隔日沖過重 ({dt_vol_pct:.1f}%)",
            "detail": (
                f"最新交易日隔日沖主力（{dt_names}）淨買超佔總量 {dt_vol_pct:.1f}%，"
                f"{f'觸發防灌水扣分 ({layer5_score} 分)。' if layer5_score < 0 else '留意短線隔日賣壓。'}"
            ),
        })

    conviction_score = max(0, min(100, int(round(raw_score + layer5_score))))
    score_breakdown = {
        "base": 50,
        "conc_diff": layer1_score,
        "wave_trust": layer2_score,
        "credit_sbl": layer3_score,
        "tdcc": layer4_score,
        "day_trade": layer5_score,
    }

    # =================================================================
    # 五大純籌碼結構狀態診斷（僅作結構解讀，不回頭干預分數）
    # =================================================================
    z_ctrl_str = f" / Z:{top3_5d_ctrl_z:+.1f}σ" if top3_5d_ctrl_z is not None else ""
    z_c5_str = f" (Z:{c5_z:+.1f}σ)" if c5_z is not None else ""
    float_str = f"、佔股本 {top3_5d_float_pct:+.2f}%" if top3_5d_float_pct is not None else ""

    ctrl_bull = has_liq and top3_5d > 0 and (
        top3_5d_ctrl_pct >= p["ctrl_5d_mild"] or (top3_5d_ctrl_z is not None and top3_5d_ctrl_z >= p["z_bull_min"])
    )
    ctrl_bear = has_liq and top3_5d < 0 and (
        top3_5d_ctrl_pct <= -p["ctrl_5d_mild"] or (top3_5d_ctrl_z is not None and top3_5d_ctrl_z <= p["z_bear_max"])
    )
    c5_bull = (c5 >= p["conc_5d_mild"]) or (c5 > 0 and c5_z is not None and c5_z >= p["z_bull_min"])
    c5_bull_s2 = (c5 >= p["s2_c5_min"]) or (c5 > 0 and c5_z is not None and c5_z >= p["z_bull_min"])
    c5_bear = (c5 <= -p["conc_5d_mild"]) or (c5 < 0 and c5_z is not None and c5_z <= p["z_bear_max"])

    has_cleansing = (
        margin_quadrant == 3
        or sbl_state == "cover"
        or tdcc_state == "bull"
        or neg_streak >= p["diff_neg_streak_bonus"]
    )
    has_conflict = (
        is_retail_chasing
        or (is_dt_warn and c5 > 0)
        or (ctrl_bull and c5_bear)
        or (ctrl_bear and c5_bull)
        or (c5_bull and (sbl_state == "surge" or tdcc_state == "bear" or trust_state == "bear"))
    )

    c10_txt = f"{c10:+.1f}%" if c10 is not None else "N/A"
    c20_txt = f"{c20:+.1f}%" if c20 is not None else "N/A"

    # 狀態 5：全面發散（大戶撤退）
    if (
        conviction_score <= 35
        or (c5_bear and ctrl_bear)
        or (c5_bear and (c10 is not None and c10 < 0) and (diff_5d is not None and diff_5d > 0))
    ):
        scenario_id = 5
        scenario_type = "bear"
        sub_type = "全面發散"
        title = "🟢 純籌碼狀態：5. 全面發散（大戶撤退）"
        desc = (
            f"全市場 5 日集中度 {c5:+.1f}%{z_c5_str}（10日 {c10_txt} ／ 20日 {c20_txt}），"
            f"近 60 日三大波段主力近 5 日淨額 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%{z_ctrl_str}），"
            f"籌碼明顯從大資金流向散戶或法人持續調節。"
        )
        action = "籌碼結構偏空發散，下方缺乏大資金穩定承接，應避開或等待集中度與家數差重新收斂。"

    # 狀態 1：強力集中（點面共振）
    elif (
        conviction_score >= 60
        and ctrl_bull
        and c5_bull
        and (c10 is None or c10 > 0)
        and (c20 is None or c20 > 0 or c5 >= p["conc_5d_strong"] or (c5_z is not None and c5_z >= p["z_bull_min"]))
        and (diff_5d is None or diff_5d <= 0)
        and not is_dt_heavy
    ):
        scenario_id = 1
        scenario_type = "bull"
        sub_type = "強力集中"
        title = "🔴 純籌碼狀態：1. 強力集中（點面共振）"
        desc = (
            f"近 60 日三大波段主力近 5 日買超 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%{z_ctrl_str}{float_str}），"
            f"全市場 5 日集中度達 {c5:+.1f}%{z_c5_str}（10日 {c10_txt} ／ 20日 {c20_txt}），"
            f"點與面同步呈現高純度吸籌鎖碼。"
        )
        action = "籌碼集中度最強格局！大戶與外圍資金同向收籌，建議持續追蹤籌碼是否維持紅燈並搭配個人進出場節奏。"

    # 狀態 2：隱形吸籌（換手接棒）
    elif (
        conviction_score >= 55
        and top3_5d_ctrl_pct <= p["s2_ctrl_max"]
        and c5_bull_s2
        and (diff_5d is None or diff_5d <= 0)
        and not is_dt_heavy
    ):
        scenario_id = 2
        scenario_type = "bull"
        sub_type = "隱形吸籌"
        title = "🔴 純籌碼狀態：2. 隱形吸籌（換手接棒）"
        desc = (
            f"原近 60 日三大波段主力近 5 日控盤率僅 {top3_5d_ctrl_pct:+.1f}%（{top3_5d:+,} 張），"
            f"但全市場 5 日集中度逆勢飆高至 {c5:+.1f}%{z_c5_str}且買超集中在少數分點，"
            f"顯示有新主力進場換手或透過其他分點暗中吸籌。"
        )
        action = "建議對照下方「近 10 日新崛起波段分點」與「贏家分點」雷達，確認新莊家是否持續鎖碼。"

    # 狀態 3：籌碼沉澱（浮額清洗）
    elif (
        conviction_score >= 55
        and (c5 >= 0 or (diff_5d is not None and diff_5d < 0))
        and has_cleansing
        and not is_dt_heavy
    ):
        scenario_id = 3
        scenario_type = "bull"
        sub_type = "籌碼沉澱"
        title = "🔴 純籌碼狀態：3. 籌碼沉澱（浮額清洗）"
        desc = (
            f"全市場 5 日集中度 {c5:+.1f}%{z_c5_str}，雖尚未爆發全面搶籌，"
            f"但融資浮額清洗、借券回補、家數差連負或集保歸戶大戶增持已顯示籌碼正穩步沉澱變輕。"
        )
        action = "屬優質底部或中繼潛伏籌碼，賣壓逐步枯竭，可列入重點觀察等待集中度進一步加速。"

    # 狀態 4：籌碼分歧（多空對作／雜訊）
    elif has_conflict:
        scenario_id = 4
        scenario_type = "neutral"
        sub_type = "籌碼分歧"
        title = "🟡 純籌碼狀態：4. 籌碼分歧（多空對作／雜訊偏高）"
        desc = (
            f"全市場 5 日集中度 {c5:+.1f}%{z_c5_str}、三大波段主力控盤 {top3_5d_ctrl_pct:+.1f}%{z_ctrl_str}，"
            f"但各籌碼維度出現內部矛盾（如散戶同追、隔日沖過重、點面反向或現貨買超伴隨借券／投信／集保賣出）。"
        )
        action = "籌碼方向尚未統一、短線雜訊偏高，建議觀望等待多空分歧收斂。"

    # 狀態 0：籌碼中性整理
    else:
        scenario_id = 0
        scenario_type = "neutral"
        sub_type = "中性整理"
        title = "⚪ 純籌碼狀態：籌碼中性整理"
        desc = (
            f"近 60 日三大波段主力近 5 日淨額 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%{z_ctrl_str}{float_str}），"
            f"全市場 5 日集中度 {c5:+.1f}%{z_c5_str}（10日 {c10_txt} ／ 20日 {c20_txt}），"
            f"整體籌碼處於多空均衡換手階段。"
        )
        action = "籌碼無顯著集中或發散傾向，持續觀察後續 5 日集中度、家數差或法人動向表態。"

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
        "score_breakdown": score_breakdown,
        "margin_quadrant": margin_quadrant,
    }


# =====================================================================
# 八、 視覺參考用：近 5 日價量籌碼背離警報（不參與純籌碼健康分數計算）
# =====================================================================
def evaluate_divergence_alert(
    price_5d_pct: float,
    top3_5d: int,
    top3_5d_ctrl_pct: float,
    latest_close: float = 0.0,
    top3_5d_amount_ntd: float = 0.0,
    top3_5d_ctrl_z: float | None = None,
    conc_5d: float | None = None,
    trust_info: dict | None = None,
    margin_info: dict | None = None,
    sbl_info: dict | None = None,
) -> dict | None:
    """
    獨立於純籌碼評分之外的「價量籌碼背離」視覺提醒條：
    偵測近 5 日價跌主力買（底部承接）或價漲主力賣（高檔出貨）背離。
    """
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
        risk_reasons = []
        if conc_5d is not None and conc_5d <= p["div_c5_warn_max"]:
            risk_reasons.append(f"全市場 5 日集中度仍發散（{conc_5d:+.2f}%）")
        if trust_info and int(trust_info.get("streak", 0)) <= p["div_trust_warn_streak"] and int(trust_info.get("d5", 0)) < 0:
            risk_reasons.append(f"投信連賣 {abs(int(trust_info['streak']))} 日（近 5 日 {int(trust_info['d5']):+,} 張）")

        margin_note = ""
        if margin_info and (margin_info.get("chg_5d") or 0) < 0:
            margin_note = f"融資減少 {margin_info['chg_5d']:+,} 張"

        if risk_reasons:
            extra_m = f"雖伴隨 {margin_note}，但" if margin_note else "但"
            return {
                "type": "warn",
                "title": "🟡 逆勢承接警示（防接落刀：主力抄底但外圍賣壓仍重）",
                "desc": (
                    f"近 5 日股價下跌 <b>{price_5d_pct:.2f}%</b>，近 60 日三大波段主力雖逆勢買超 "
                    f"<b style='color:#ef5350;'>{top3_5d:+,} 張</b>（控盤率 <b>{top3_5d_ctrl_pct:+.1f}%{z_str}</b>{amt_str}），"
                    f"{extra_m}<b>{'、'.join(risk_reasons)}</b>。<br>"
                    f"⚠️ 單靠少數分點低接尚不足以止跌，切勿急著接落刀，建議等待全市場集中度收斂翻正。"
                ),
            }

        extra = ""
        if margin_note:
            extra = (
                f"<br>🧹 <b>{margin_note}</b>，"
                f"呈現「散戶停損、主力低接」結構。"
            )
        return {
            "type": "bull",
            "title": "🔴 底部承接背離警報（近 5 日價跌主力買）",
            "desc": (
                f"近 5 日股價下跌 <b>{price_5d_pct:.2f}%</b>，但近 60 日三大波段主力逆勢合計淨買超 "
                f"<b style='color:#ef5350;'>{top3_5d:+,} 張</b>（控盤率 <b>{top3_5d_ctrl_pct:+.1f}%{z_str}</b>{amt_str}），"
                f"且全市場未見失控發散，留意低檔換手訊號。{extra}"
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


# =====================================================================
# 九、 Z-Score 三段式動態懸浮說明文案
# =====================================================================
def build_z_tooltip_html(stock_title: str, ctrl5: float, z5: float | None) -> str:
    """產出 Z-Score 三段式懸浮說明文案（1.意義 2.目前個股診斷 3.各區間含意）"""
    z_bull = STRATEGY_PARAMS["z_bull_min"]
    z_bear = STRATEGY_PARAMS["z_bear_max"]
    ctrl5_col = "#ef5350" if ctrl5 > 0 else ("#26a69a" if ctrl5 < 0 else "#cbd5e1")

    if z5 is not None:
        if z5 >= z_bull:
            z5_col = "#ef5350"
            z_diag_desc = (
                f"代表三大主力近 5 日買超力道已突破過去 60 天慣性的 {z_bull:.1f} 倍標準差，"
                f"屬於罕見的<b style='color:#ef5350;'>「異常重手吃貨」</b>表態訊號。"
            )
        elif z5 >= 1.0:
            z5_col = "#fbbf24"
            z_diag_desc = (
                "代表三大主力近 5 日買超力道已高於日常波動上緣，"
                "屬於<b style='color:#fbbf24;'>「溫和偏多、積極建倉」</b>階段，但尚未達到暴力掃貨的異常水準。"
            )
        elif z5 <= z_bear:
            z5_col = "#26a69a"
            z_diag_desc = (
                f"代表三大主力近 5 日賣超力道已跌破過去 60 天慣性的 {z_bear:.1f} 倍標準差，"
                f"屬於罕見的<b style='color:#26a69a;'>「異常重手倒貨」</b>警戒訊號。"
            )
        elif z5 <= -1.0:
            z5_col = "#26a69a"
            z_diag_desc = (
                "代表三大主力近 5 日賣壓已超出日常波動下緣，"
                "屬於<b style='color:#26a69a;'>「溫和偏空、持續調節」</b>階段，但尚未達到恐慌性倒貨水準。"
            )
        else:
            z5_col = "#cbd5e1"
            if ctrl5 > 0:
                z_diag_desc = (
                    f"代表帳面雖有買超（{ctrl5:+.1f}%），但對照該股過去 60 天的波動慣性（{z5:+.1f}σ）"
                    f"只是<b>「日常正常範圍內的稍微偏多」</b>，尚未達到異常吸籌水準。"
                )
            elif ctrl5 < 0:
                z_diag_desc = (
                    f"代表帳面雖有賣超（{ctrl5:+.1f}%），但對照該股過去 60 天的波動慣性（{z5:+.1f}σ）"
                    f"只是<b>「日常正常範圍內的稍微偏空」</b>，尚未達到異常倒貨水準。"
                )
            else:
                z_diag_desc = "代表近 5 日三大主力買賣超互相抵銷，完全貼近過去 60 天的平均常態水準。"

        current_z_summary = (
            f"<b>2️⃣ 目前 {stock_title} 數據診斷：</b><br>"
            f"近 5 日控盤率 <b style='color:{ctrl5_col};'>{ctrl5:+.1f}%</b> ｜ "
            f"<b style='color:{z5_col};'>Z:{z5:+.1f}σ</b><br>"
            f"{z_diag_desc}<br><br>"
        )
    else:
        current_z_summary = (
            f"<b>2️⃣ 目前 {stock_title} 數據診斷：</b><br>"
            f"近 5 日控盤率 <b style='color:{ctrl5_col};'>{ctrl5:+.1f}%</b> ｜ <b>Z：樣本累積中</b><br>"
            f"該股歷史交易日樣本尚在累積中，暫無法計算 60 日滾動 Z-Score 偏離倍數。<br><br>"
        )

    return (
        "<b>1️⃣ Z（Z-Score 標準分數）意義：</b><br>"
        "衡量近 5 日控盤力道相對於該股過去 60 個交易日常態的偏離倍數。<br><br>"
        f"{current_z_summary}"
        "<b>3️⃣ Z 數值各區間含意：</b><br>"
        f"• <b>≥ +{z_bull:.1f}σ（明顯偏多）：</b>最積極買超的前 6.7%，屬異常重手吃貨表態（可自動幫權值股／高當沖股校準門檻）。<br>"
        f"• <b>+1.0σ ～ +{z_bull:.1f}σ（溫和偏多）：</b>買盤高於日常波動上緣，主力開始穩定建倉，但尚未暴力掃貨。<br>"
        f"• <b>-1.0σ ～ +1.0σ（中性盤整）：</b>涵蓋約 68% 交易日（Z = 0 為 60 日平均），屬日常正常換手波動。<br>"
        f"• <b>{z_bear:.1f}σ ～ -1.0σ（溫和偏空）：</b>賣壓超出日常波動下緣，主力正在有節奏地逢高調節。<br>"
        f"• <b>≤ {z_bear:.1f}σ（明顯偏空）：</b>賣最兇的後 6.7%，屬異常重手倒貨或停損砍倉警戒區。"
    )
