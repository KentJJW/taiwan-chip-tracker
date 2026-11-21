"""
strategy.py — 台股主力籌碼策略與五大實戰劇本規則庫
未來若需微調均線濾網、控盤率門檻、集中度標準或劇本提示文字，僅需修改此檔案即可。
"""

# =====================================================================
# 一、 策略核心量化門檻參數（集中管理，方便隨時微調）
# =====================================================================
STRATEGY_PARAMS = {
    # 最低防呆張數（避免成交量極度萎縮時，買賣幾張就算出極端控盤率）
    "min_abs_shares_5d": 30,

    # 1. 雙重共振（主升段）
    "s1_ctrl_min": 2.0,          # 近 5 日三大主力控盤率下限 (%)
    "s1_c5_min": 3.0,            # 近 5 日集中度下限 (%)
    "s1_c5_strong": 8.0,         # 若未嚴格遞增，5 日集中度直接達標門檻 (%)

    # 2. 換手接棒（藏分點）
    "s2_ctrl_max": 1.5,          # 近 5 日三大主力控盤率上限 (%)
    "s2_c5_min": 5.0,            # 近 5 日全市場集中度下限 (%)
    "s2_c10_min": 2.0,           # 近 10 日集中度輔助門檻 (%)

    # 3. 高檔派發（假突破）
    "s3_price_5d_min": 2.0,      # 近 5 日股價漲幅下限 (%)
    "s3_ctrl_max": -2.0,         # 近 5 日三大主力控盤率上限 (%)
    "s3_c5_max": -3.0,           # 近 5 日集中度轉負上限 (%)

    # 4. 成本護盤（真洗盤）
    "s4_cost_buffer": 1.02,      # 回測主力成本均價容許上緣倍數 (+2% 內)
    "s4_price_5d_max": -1.5,     # 近 5 日股價回檔幅度門檻 (%)
    "s4_ctrl_min": 1.5,          # 近 5 日三大主力逆勢買超控盤率下限 (%)
    "s4_c5_min": 3.0,            # 近 5 日集中度下限 (%)

    # 5. 主力棄守（真破線）
    "s5_cost_break": 0.98,       # 跌破主力成本均價門檻 (-2% 以下)
    "s5_ctrl_max": -3.0,         # 近 5 日三大主力倒貨控盤率上限 (%)
    "s5_c5_max": -5.0,           # 近 5 日集中度上限 (%)
    "s5_c10_max": -3.0,          # 近 10 日集中度上限 (%)

    # 近 5 日價量背離警報門檻
    "div_price_pct": 3.0,        # 近 5 日漲跌幅門檻 (%)
    "div_ctrl_pct": 2.0,         # 近 5 日三大主力控盤率門檻 (%)
}

# 快捷上色標籤（紅＝偏多／買超、綠＝偏空／賣超、橘＝關鍵價位均線）
_R = lambda s: f'<span style="color:#ef5350; font-weight:700;">{s}</span>'
_G = lambda s: f'<span style="color:#26a69a; font-weight:700;">{s}</span>'
_Y = lambda s: f'<span style="color:#ffb74d; font-weight:700;">{s}</span>'

# =====================================================================
# 二、 可收合抽屜「價 × 點 × 面 交叉判讀矩陣」（三欄精簡＋關鍵數字上色）
# =====================================================================
SCENARIO_MATRIX = [
    {
        "id": 1,
        "name": "1. 雙重共振（主升段）",
        "type": "bull",
        "price_cond": f"站穩 {_Y('均價、10MA、20MA')}<br>且 {_Y('10MA ≥ 20MA')}",
        "main_cond": f"5日控盤 {_R('≥ +2.0%')}",
        "conc_cond": f"5 / 10 / 20 日{_R('全正')}<br>且遞增加速（{_R('短 > 長')}）",
        "action": "最強多頭格局。波段大戶與短線資金同步鎖碼，沿 10MA 或主力成本線偏多操作。"
    },
    {
        "id": 2,
        "name": "2. 換手接棒（藏分點）",
        "type": "bull",
        "price_cond": f"站上 {_Y('10MA')}<br>（濾除空頭急跌）",
        "main_cond": f"5日控盤 {_Y('≤ +1.5%')}<br>（持平或小調節）",
        "conc_cond": f"20日偏低或負值<br>5日翻正飆高 {_R('≥ +5.0%')}",
        "action": "隱形起漲／新莊家進場。主力改用其他分點吃貨或新大戶接手，留意「本區間買超第一名」動向。"
    },
    {
        "id": 3,
        "name": "3. 高檔派發（假突破）",
        "type": "bear",
        "price_cond": f"近5日上漲 {_R('≥ +2.0%')}",
        "main_cond": f"5日控盤 {_G('≤ -2.0%')}",
        "conc_cond": f"20日仍為正值<br>5日率先轉負 {_G('≤ -3.0%')}",
        "action": "高檔出貨警訊。波段大戶趁散戶追價倒貨，慎防假突破與拉高套牢風險。"
    },
    {
        "id": 4,
        "name": "4. 成本護盤（真洗盤）",
        "type": "bull",
        "price_cond": f"回測 {_Y('均價 +2% 內')}<br>且近5日下跌 {_G('≤ -1.5%')}",
        "main_cond": f"5日控盤 {_R('≥ +1.5%')}",
        "conc_cond": f"5日維持正值 {_R('≥ +3.0%')}<br>（急跌籌碼反集中）",
        "action": "主力刻意洗盤。大戶不願跌破自身成本區，趁恐慌承接浮額，止跌回穩後易展開反彈。"
    },
    {
        "id": 5,
        "name": "5. 主力棄守（真破線）",
        "type": "bear",
        "price_cond": f"跌破 {_G('均價逾 2%')}<br>且失守 {_G('20MA 月線')}",
        "main_cond": f"5日控盤 {_G('≤ -3.0%')}",
        "conc_cond": f"5日 {_G('≤ -5.0%')}、10日 {_G('≤ -3.0%')}<br>同步明顯發散",
        "action": "多殺多棄守格局。連波段最大主力都在停損結帳，下方無大資金接手，切忌盲目攤平。"
    }
]


# =====================================================================
# 三、 五大實戰劇本自動判定邏輯
# =====================================================================
def evaluate_chip_scenario(
    top3_5d: int,
    top3_5d_ctrl_pct: float,
    latest_close: float,
    ma10: float,
    ma20: float,
    primary_cost: float,
    price_5d_pct: float,
    conc: dict,
) -> dict:
    """結合今年三大主力控盤率、收盤價與 10MA/20MA/成本均價，以及 5/10/20 日集中度判定劇本"""
    p = STRATEGY_PARAMS
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
    above_ma20 = (ma20 > 0 and latest_close >= ma20)
    ma_bull_align = (ma10 > 0 and ma20 > 0 and ma10 >= ma20)
    below_ma20 = (ma20 > 0 and latest_close < ma20)
    near_or_below_cost = (primary_cost > 0 and latest_close <= primary_cost * p["s4_cost_buffer"])
    min_sh = p["min_abs_shares_5d"]

    # 情境 5：主力棄守（真破線）— 跌破主力均價逾 2% 且失守 20MA 月線
    if (
        primary_cost > 0
        and latest_close < primary_cost * p["s5_cost_break"]
        and below_ma20
        and top3_5d <= -min_sh
        and top3_5d_ctrl_pct <= p["s5_ctrl_max"]
        and c5 <= p["s5_c5_max"]
        and c10 <= p["s5_c10_max"]
    ):
        return {
            "id": 5,
            "type": "bear",
            "title": "🟢 當前劇本：5. 主力棄守（真破線）",
            "desc": (
                f"收盤價（{latest_close:,.2f} 元）跌破主力均價（{primary_cost:,.2f} 元）與 20MA（{ma20:,.2f} 元），"
                f"且近 5 日三大主力大賣 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%）、5 日集中度降至 {c5:+.1f}%。"
            ),
            "action": "連波段大戶都在停損結帳且月線失守，下方無大資金承接，切忌因主力帳面虧損而盲目攤平。"
        }

    # 情境 3：高檔派發（假突破）
    if (
        price_5d_pct >= p["s3_price_5d_min"]
        and top3_5d <= -min_sh
        and top3_5d_ctrl_pct <= p["s3_ctrl_max"]
        and c20 > 0
        and c5 <= p["s3_c5_max"]
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

    # 情境 1：雙重共振（主升段）— 站穩主力成本、10MA 與 20MA，且 10MA >= 20MA
    if (
        above_ma10
        and above_ma20
        and ma_bull_align
        and (above_cost or primary_cost == 0)
        and top3_5d >= min_sh
        and top3_5d_ctrl_pct >= p["s1_ctrl_min"]
        and c5 > p["s1_c5_min"]
        and c10 > 0
        and c20 > 0
        and (c5 >= c10 or c5 >= p["s1_c5_strong"])
    ):
        return {
            "id": 1,
            "type": "bull",
            "title": "🔴 當前劇本：1. 雙重共振（主升段）",
            "desc": (
                f"收盤價（{latest_close:,.2f} 元）站穩 10MA（{ma10:,.2f} 元）、20MA（{ma20:,.2f} 元）與主力成本之上，"
                f"三大主力近 5 日買超 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%），"
                f"且 20日({c20:+.1f}%) → 10日({c10:+.1f}%) → 5日({c5:+.1f}%) 集中度全面翻正加速。"
            ),
            "action": "最強多頭格局！波段大戶與短線大資金同步鎖碼，可沿 10MA 或主力成本線偏多操作。"
        }

    # 情境 4：成本護盤（真洗盤）
    if (
        near_or_below_cost
        and price_5d_pct <= p["s4_price_5d_max"]
        and top3_5d >= min_sh
        and top3_5d_ctrl_pct >= p["s4_ctrl_min"]
        and c5 >= p["s4_c5_min"]
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

    # 情境 2：換手接棒（藏分點）— 收盤價站上 10MA 即可（搶先捕捉起漲初期，不強制 20MA）
    if (
        above_ma10
        and top3_5d_ctrl_pct <= p["s2_ctrl_max"]
        and c5 >= p["s2_c5_min"]
        and (c10 >= p["s2_c10_min"] or c20 < c5)
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
            f"收盤價 {latest_close:,.2f} 元（10MA {ma10:,.2f} ／ 20MA {ma20:,.2f}），"
            f"三大主力近 5 日淨額 {top3_5d:+,} 張（控盤率 {top3_5d_ctrl_pct:+.1f}%），"
            f"全市場 5 日集中度 {c5:+.1f}%，處於多空均衡或橫盤整理階段。"
        ),
        "action": "持續觀察收盤價與 10MA/20MA 位置、5 日集中度是否突破 ±5% 或三大主力控盤率表態。"
    }


# =====================================================================
# 四、 近 5 日價量籌碼背離警報判定
# =====================================================================
def evaluate_divergence_alert(price_5d_pct: float, top3_5d: int, top3_5d_ctrl_pct: float) -> dict | None:
    """偵測近 5 日價跌主力買（底部承接）或價漲主力賣（高檔出貨）背離"""
    p = STRATEGY_PARAMS
    if price_5d_pct <= -p["div_price_pct"] and top3_5d > 0 and top3_5d_ctrl_pct >= p["div_ctrl_pct"]:
        return {
            "type": "bull",
            "title": "🔴 底部承接背離警報（近 5 日價跌主力買）",
            "desc": (
                f"近 5 日股價下跌 <b>{price_5d_pct:.2f}%</b>，但今年前三大主力逆勢合計淨買超 "
                f"<b style='color:#ef5350;'>{top3_5d:+,} 張</b>（近 5 日控盤率 <b>{top3_5d_ctrl_pct:+.1f}%</b>），"
                f"留意低檔換手與止跌轉折訊號。"
            )
        }
    elif price_5d_pct >= p["div_price_pct"] and top3_5d < 0 and top3_5d_ctrl_pct <= -p["div_ctrl_pct"]:
        return {
            "type": "bear",
            "title": "🟢 高檔出貨背離警報（近 5 日價漲主力賣）",
            "desc": (
                f"近 5 日股價上漲 <b>{price_5d_pct:+.2f}%</b>，但今年前三大主力合計逢高賣超 "
                f"<b style='color:#26a69a;'>{top3_5d:+,} 張</b>（近 5 日控盤率 <b>{top3_5d_ctrl_pct:+.1f}%</b>），"
                f"慎防假突破或拉高倒貨。"
            )
        }
    return None
