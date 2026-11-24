# 1. 買賣家數差
    diff_tip = "計算公式：買超分點家數 － 賣超分點家數。負值（紅）代表籌碼由散戶流向少數主力；正值（綠）代表籌碼流向散戶、趨於發散。若外部資料延遲超過 1 個交易日，將不計入今日分數。"
    if broker_diff_info:
        d5 = broker_diff_info["diff_5d"]
        d1 = broker_diff_info.get("diff_1d")
        d1_latest = broker_diff_info.get("diff_1d_latest", d1)
        neg_s = broker_diff_info["neg_streak"]
        bd_date = str(broker_diff_info.get("date", ""))
        bd_short = bd_date[5:] if len(bd_date) >= 10 else bd_date
        is_stale = bool(broker_diff_info.get("is_stale"))

        if is_stale:
            b_html = _aux_badge(f"⏳ 延遲({bd_short})", "warn")
            sub_txt = f"{bd_short} {_span_num(d1_latest, ' 家', invert_color=True)} ｜ 延遲未計分"
        else:
            b_html = _aux_badge("🧲 籌碼集中", "bull") if d5 < 0 else (_aux_badge("📉 籌碼渙散", "bear") if d5 > 0 else _aux_badge("⚪ 持平", "info"))
            today_txt = _span_num(d1, " 家", invert_color=True) if d1 is not None else f'<span style="color:#94a3b8;">待公布({bd_short})</span>'
            sub_txt = f"今日 {today_txt} ｜ 連負 <b>{neg_s}</b> 日"

        diff_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head">'
            f'    <span class="aux-title">🧲 買賣家數差<span class="tip-wrap tip-down">ⓘ<span class="tip-box">{diff_tip}</span></span></span>'
            f'    {b_html}'
            f'  </div>'
            f'  <div class="aux-main">5日 {_span_num(d5, " 家", invert_color=True)}</div>'
            f'  <div class="aux-sub">{sub_txt}</div>'
            f'</div>'
        )
    else:
        diff_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head"><span class="aux-title">🧲 買賣家數差<span class="tip-wrap tip-down">ⓘ<span class="tip-box">{diff_tip}</span></span></span>{_aux_badge("待更新", "info")}</div>'
            f'  <div class="aux-main" style="color:#94a3b8;">尚無資料</div>'
            f'  <div class="aux-sub">點擊左側「🔄」補齊</div>'
            f'</div>'
        )
