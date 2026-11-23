import os
import re
import json
from datetime import datetime
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from data_engine import (
    DB_DIR,
    SCENARIO_MATRIX,
    update_stock_database,
    analyze_main_force,
    delete_stock_database,
    calculate_main_force_cost,
    fetch_stock_name,
    load_stock_order,
    save_stock_order,
)

st.set_page_config(page_title="台股主力分點吃貨／出貨追蹤系統", layout="wide")

st.markdown("""
<style>
.summary-card {
    background-color: #1c202a;
    border: 1px solid #2e3444;
    border-radius: 10px;
    padding: 14px 16px;
    height: 100%;
}
.card-label {
    font-size: 0.85rem;
    color: #9aa4b2;
    margin-bottom: 6px;
}
.card-value {
    font-size: 1.15rem;
    font-weight: 700;
    color: #f3f4f6;
    line-height: 1.4;
    word-break: break-word;
}
.card-sub {
    font-size: 0.82rem;
    color: #94a3b8;
    margin-top: 6px;
    line-height: 1.45;
}
.trust-bar {
    background-color: #181d28;
    border: 1px solid #2f3b52;
    border-left: 4px solid #38bdf8;
    border-radius: 8px;
    padding: 10px 14px;
    margin-top: 10px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    font-size: 0.88rem;
    color: #e2e8f0;
}
.trust-title {
    font-weight: 700;
    color: #f8fafc;
    display: inline-flex;
    align-items: center;
    gap: 6px;
}
.trust-tag {
    font-size: 0.74rem;
    color: #94a3b8;
    background-color: #232a3b;
    padding: 2px 7px;
    border-radius: 4px;
    font-weight: 400;
}
.trust-metrics {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 14px;
    font-variant-numeric: tabular-nums;
}
.trust-item {
    color: #cbd5e1;
}
.trust-lbl {
    color: #94a3b8;
    font-size: 0.82rem;
    margin-right: 4px;
}
.conc-panel {
    background-color: #161a23;
    border: 1px solid #2d3548;
    border-radius: 10px;
    padding: 12px 16px;
    margin-top: 10px;
    margin-bottom: 6px;
}
.conc-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
    gap: 8px;
}
.conc-title {
    font-size: 0.92rem;
    font-weight: 700;
    color: #e2e8f0;
    display: inline-flex;
    align-items: center;
}
.conc-sub-tag {
    font-size: 0.78rem;
    color: #94a3b8;
    background-color: #232938;
    padding: 2px 8px;
    border-radius: 4px;
    margin-left: 8px;
    font-weight: 400;
}
.conc-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
}
.conc-box {
    background-color: #1e2330;
    border: 1px solid #2e374b;
    border-radius: 8px;
    padding: 10px 14px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}
.conc-box-left {
    display: flex;
    flex-direction: column;
}
.conc-period {
    font-size: 0.8rem;
    color: #94a3b8;
    margin-bottom: 2px;
}
.conc-val {
    font-size: 1.15rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
}
.conc-badge {
    font-size: 0.78rem;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 5px;
    white-space: nowrap;
}
.scenario-bull {
    background-color: rgba(239, 83, 80, 0.13);
    border-left: 4px solid #ef5350;
    border-radius: 6px;
    padding: 9px 13px;
    margin-top: 10px;
    font-size: 0.88rem;
    color: #f8fafc;
    line-height: 1.5;
}
.scenario-bear {
    background-color: rgba(38, 166, 154, 0.13);
    border-left: 4px solid #26a69a;
    border-radius: 6px;
    padding: 9px 13px;
    margin-top: 10px;
    font-size: 0.88rem;
    color: #f8fafc;
    line-height: 1.5;
}
.scenario-neutral {
    background-color: rgba(148, 163, 184, 0.08);
    border-left: 4px solid #64748b;
    border-radius: 6px;
    padding: 8px 13px;
    margin-top: 10px;
    font-size: 0.85rem;
    color: #cbd5e1;
    line-height: 1.45;
}
.div-alert-bull {
    background-color: rgba(239, 83, 80, 0.12);
    border-left: 4px solid #ef5350;
    border-radius: 6px;
    padding: 10px 14px;
    margin: 8px 0 4px 0;
    font-size: 0.9rem;
    color: #f8fafc;
}
.div-alert-bear {
    background-color: rgba(38, 166, 154, 0.12);
    border-left: 4px solid #26a69a;
    border-radius: 6px;
    padding: 10px 14px;
    margin: 8px 0 4px 0;
    font-size: 0.9rem;
    color: #f8fafc;
}
.mf-list {
    margin: 2px 0 8px 22px;
    overflow: visible;
}
.mf-row {
    display: grid;
    grid-template-columns: 145px 155px 135px 120px 135px 135px 90px 145px;
    align-items: center;
    padding: 4px 0;
    font-size: 0.92rem;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    color: #e2e8f0;
}
.mf-name {
    font-weight: 700;
    color: #f8fafc;
    padding-right: 8px;
}
.mf-cell {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 10px;
    border-left: 1px solid #334155;
}
.mf-lbl {
    color: #94a3b8;
    font-size: 0.84rem;
    margin-right: 6px;
    display: inline-flex;
    align-items: center;
}
.tip-wrap {
    position: relative;
    display: inline-flex;
    align-items: center;
    cursor: help;
    margin-left: 5px;
    color: #94a3b8;
    font-size: 0.85rem;
    font-weight: 400;
}
.tip-wrap:hover {
    color: #f8fafc;
}
.tip-box {
    visibility: hidden;
    opacity: 0;
    position: absolute;
    z-index: 9999;
    width: 300px;
    background-color: #0f172a;
    color: #e2e8f0;
    border: 1px solid #475569;
    border-radius: 6px;
    padding: 8px 11px;
    font-size: 0.8rem;
    font-weight: 400;
    line-height: 1.5;
    white-space: normal;
    text-align: left;
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.6);
    transition: opacity 0.15s ease;
    pointer-events: none;
}
.tip-wrap:hover .tip-box {
    visibility: visible;
    opacity: 1;
}
.tip-right .tip-box {
    left: 140%;
    top: 50%;
    transform: translateY(-50%);
}
.tip-left .tip-box {
    right: 140%;
    top: 50%;
    transform: translateY(-50%);
}
.cost-banner {
    background-color: #1a2230;
    border-left: 4px solid #ff9100;
    border-radius: 6px;
    padding: 9px 14px;
    margin: 6px 0 10px 0;
    font-size: 0.9rem;
    color: #e2e8f0;
}
.rank-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.79rem;
    background-color: #161922;
    border-radius: 8px;
    overflow: hidden;
}
.rank-table th {
    background-color: #222736;
    color: #cbd5e1;
    text-align: left;
    padding: 7px 7px;
    font-weight: 600;
    white-space: nowrap;
}
.rank-table td {
    padding: 6px 7px;
    border-top: 1px solid #262c3d;
    color: #e2e8f0;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}
.matrix-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.84rem;
    background-color: #141821;
}
.matrix-table th {
    background-color: #1f2533;
    color: #cbd5e1;
    text-align: left;
    padding: 8px 10px;
    font-weight: 600;
}
.matrix-table td {
    padding: 8px 10px;
    border-top: 1px solid #262e40;
    color: #cbd5e1;
    line-height: 1.5;
    vertical-align: top;
}
.matrix-active-bull td {
    background-color: rgba(239, 83, 80, 0.14);
    color: #ffffff;
    border-top: 1px solid rgba(239, 83, 80, 0.45);
    border-bottom: 1px solid rgba(239, 83, 80, 0.45);
}
.matrix-active-bear td {
    background-color: rgba(38, 166, 154, 0.14);
    color: #ffffff;
    border-top: 1px solid rgba(38, 166, 154, 0.45);
    border-bottom: 1px solid rgba(38, 166, 154, 0.45);
}
.sidebar-help-box {
    background-color: #1a1f2b;
    border: 1px solid #2d3548;
    border-radius: 6px;
    padding: 7px 10px;
    font-size: 0.78rem;
    color: #94a3b8;
    line-height: 1.5;
    margin-bottom: 8px;
}
</style>
""", unsafe_allow_html=True)


def get_saved_stocks() -> list:
    if not os.path.exists(DB_DIR):
        return []
    raw_ids = [
        f.split("_")[0] for f in os.listdir(DB_DIR) if f.endswith("_price.csv")
    ]
    return load_stock_order(raw_ids)


@st.cache_data(ttl=86400, show_spinner=False)
def _cached_fetch_stock_name(stock_id: str) -> str:
    return fetch_stock_name(stock_id=stock_id)


def get_stock_display_label(stock_id: str) -> str:
    """回傳 '6488 環球晶'；若舊資料尚未存有中文名稱，自動即時補抓並寫入本地 _meta.json"""
    meta_path = os.path.join(DB_DIR, f"{stock_id}_meta.json")
    meta_data = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_data = json.load(f)
            name = str(meta_data.get("stock_name", "")).strip()
            if name:
                return f"{stock_id} {name}"
        except Exception:
            pass

    fetched_name = _cached_fetch_stock_name(stock_id)
    if fetched_name:
        if os.path.exists(meta_path) and isinstance(meta_data, dict):
            try:
                meta_data["stock_name"] = fetched_name
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta_data, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        return f"{stock_id} {fetched_name}"

    return stock_id


def parse_manual_date(text: str, fallback: str) -> str:
    raw = text.strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 8:
        try:
            return datetime.strptime(digits, "%Y%m%d").strftime("%Y-%m-%d")
        except ValueError:
            pass
    m = re.match(r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$", raw)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return fallback


def _span_num(val: int, suffix: str = " 張") -> str:
    color = "#ef5350" if val > 0 else ("#26a69a" if val < 0 else "#cbd5e1")
    return f'<span style="color:{color}; font-weight:700;">{val:+,}{suffix}</span>'


def _span_streak(streak: int) -> str:
    if streak > 0:
        return f'<span style="color:#ef5350; font-weight:700;">連買 {streak} 日</span>'
    elif streak < 0:
        return f'<span style="color:#26a69a; font-weight:700;">連賣 {abs(streak)} 日</span>'
    return '<span style="color:#94a3b8;">持平</span>'


def _span_ctrl(pct: float) -> str:
    color = "#ef5350" if pct > 0 else ("#26a69a" if pct < 0 else "#94a3b8")
    return f'<span style="color:{color}; font-weight:700;">{pct:+.1f}%</span>'


def _format_conc_card(label: str, info: dict) -> str:
    pct = info.get("pct") if isinstance(info, dict) else None
    net = info.get("net", 0) if isinstance(info, dict) else 0
    if pct is None:
        return (
            f'<div class="conc-box">'
            f'  <div class="conc-box-left"><span class="conc-period">{label}</span><span class="conc-val" style="color:#94a3b8;">待更新</span></div>'
            f'  <span class="conc-badge" style="background:#262e40; color:#94a3b8;">請點左側更新</span>'
            f'</div>'
        )

    if pct >= 10.0:
        color, badge_bg, badge_txt = "#ef5350", "rgba(239,83,80,0.18)", "🔥 高度集中"
    elif pct >= 3.0:
        color, badge_bg, badge_txt = "#ef5350", "rgba(239,83,80,0.14)", "📈 偏多吸籌"
    elif pct <= -10.0:
        color, badge_bg, badge_txt = "#26a69a", "rgba(38,166,154,0.18)", "⚠️ 嚴重發散"
    elif pct <= -3.0:
        color, badge_bg, badge_txt = "#26a69a", "rgba(38,166,154,0.14)", "📉 籌碼微散"
    else:
        color, badge_bg, badge_txt = "#cbd5e1", "rgba(148,163,184,0.14)", "⚪ 籌碼中性"

    return (
        f'<div class="conc-box">'
        f'  <div class="conc-box-left">'
        f'    <span class="conc-period">{label}（淨額 {net:+,} 張）</span>'
        f'    <span class="conc-val" style="color:{color};">{pct:+.2f}%</span>'
        f'  </div>'
        f'  <span class="conc-badge" style="background:{badge_bg}; color:{color};">{badge_txt}</span>'
        f'</div>'
    )


def _render_rank_table(title: str, data_dict: dict, val_color: str, empty_msg: str) -> str:
    rows_html = "".join([
        f'<tr>'
        f'  <td style="width:24px; color:#94a3b8;">#{i+1}</td>'
        f'  <td><b>{k}</b></td>'
        f'  <td style="text-align:right; color:{val_color}; font-weight:700;">{v:+,} 張</td>'
        f'</tr>'
        for i, (k, v) in enumerate(data_dict.items())
    ]) or f'<tr><td colspan="3" style="color:#94a3b8;">{empty_msg}</td></tr>'

    return (
        f'<table class="rank-table">'
        f'  <thead><tr><th colspan="3">{title}</th></tr></thead>'
        f'  <tbody>{rows_html}</tbody>'
        f'</table>'
    )


st.markdown("### 📈 台股主力分點吃貨／出貨追蹤系統")

saved_list = get_saved_stocks()
if "active_stock" not in st.session_state:
    st.session_state["active_stock"] = saved_list[0] if saved_list else "6488"
if "sort_mode" not in st.session_state:
    st.session_state["sort_mode"] = False

# --- 側邊欄：新增個股、統計區間與已存個股管理 ---
with st.sidebar:
    st.header("🔍 新增個股建檔")
    input_stock = st.text_input("輸入新台股代號（例如：2330、3231）", value="", placeholder="輸入 4 碼上市櫃代號").strip()
    btn_add = st.button("➕ 新增並建檔 (2026/01/02起)", type="primary", width="stretch")

    st.divider()
    st.subheader("📅 統計區間")
    range_type = st.radio(
        "選擇檢視範圍",
        options=["今日", "5日", "10日", "20日", "60日", "全部", "指定日期"],
        index=0,
        horizontal=True
    )

    custom_start_str = "20260102"
    custom_end_str = datetime.today().strftime("%Y%m%d")
    if range_type == "指定日期":
        st.caption("請直接輸入 8 碼數字（例：20260501）")
        custom_start_str = st.text_input("起始日期", value="20260102")
        custom_end_str = st.text_input("結束日期", value=datetime.today().strftime("%Y%m%d"))

    st.divider()
    st.subheader(f"📁 已存個股 ({len(saved_list)})")

    if saved_list:
        st.markdown(
            '<div class="sidebar-help-box">'
            '全部更新：近 3 日增量更新所有個股與集中度<br>'
            '個股更新：全年度重抓校準（補齊新分點全年明細）'
            '</div>',
            unsafe_allow_html=True,
        )

        col_all_upd, col_sort_btn = st.columns([1.25, 1])
        with col_all_upd:
            if st.button(
                "🔄 全部更新",
                key="btn_update_all",
                help="對所有已存個股執行近 3 個交易日增量更新，並重算最新 5/10/20 日集中度",
                width="stretch",
                disabled=st.session_state["sort_mode"],
            ):
                prog = st.progress(0, text="準備批量增量更新...")
                for idx, sid in enumerate(saved_list):
                    lbl = get_stock_display_label(sid)
                    prog.progress((idx) / len(saved_list), text=f"正在增量更新 {lbl} ({idx+1}/{len(saved_list)})...")
                    update_stock_database(sid, incremental_days=3)
                prog.progress(1.0, text="全部增量更新完成！")
                st.rerun()

        with col_sort_btn:
            if not st.session_state["sort_mode"]:
                if st.button("↕️ 排序", key="btn_enter_sort", help="手動調整自選股排列順序", width="stretch"):
                    st.session_state["sort_mode"] = True
                    st.rerun()
            else:
                if st.button("✓ 完成", key="btn_exit_sort", type="primary", help="完成排序並儲存", width="stretch"):
                    save_stock_order(saved_list, sync_github=True)
                    st.session_state["sort_mode"] = False
                    st.rerun()

        if st.session_state["sort_mode"]:
            st.caption("點擊 ↑ / ↓ 調整順序，完成後請按「✓ 完成」同步儲存")
            for idx, sid in enumerate(saved_list):
                display_name = get_stock_display_label(sid)
                c_name, c_up, c_dn = st.columns([3.4, 0.8, 0.8])
                with c_name:
                    st.markdown(
                        f'<div style="padding:6px 10px; background:#1e2330; border:1px solid #334155; '
                        f'border-radius:6px; font-size:0.88rem; color:#f1f5f9;">{idx+1}. {display_name}</div>',
                        unsafe_allow_html=True,
                    )
                with c_up:
                    if st.button("↑", key=f"up_{sid}", disabled=(idx == 0), width="stretch"):
                        new_list = list(saved_list)
                        new_list[idx - 1], new_list[idx] = new_list[idx], new_list[idx - 1]
                        save_stock_order(new_list, sync_github=False)
                        st.rerun()
                with c_dn:
                    if st.button("↓", key=f"dn_{sid}", disabled=(idx == len(saved_list) - 1), width="stretch"):
                        new_list = list(saved_list)
                        new_list[idx + 1], new_list[idx] = new_list[idx], new_list[idx + 1]
                        save_stock_order(new_list, sync_github=False)
                        st.rerun()
        else:
            for sid in saved_list:
                col_s, col_upd, col_del = st.columns([3.3, 0.85, 0.85])
                display_name = get_stock_display_label(sid)
                with col_s:
                    is_current = (sid == st.session_state.get("active_stock"))
                    btn_label = f"▶ {display_name}" if is_current else display_name
                    if st.button(btn_label, key=f"sel_{sid}", width="stretch"):
                        st.session_state["active_stock"] = sid
                        st.rerun()
                with col_upd:
                    if st.button(
                        "🔄",
                        key=f"upd_{sid}",
                        help=f"全年度重抓校準 {display_name}（從 2026/01/02 完整回溯）",
                        width="stretch",
                    ):
                        with st.spinner(f"正在全年度重抓校準 {display_name}..."):
                            ok = update_stock_database(sid, incremental_days=None)
                            if ok:
                                st.session_state["active_stock"] = sid
                        st.rerun()
                with col_del:
                    if st.button("✕", key=f"del_{sid}", help=f"刪除 {display_name} 的所有歷史資料", width="stretch"):
                        with st.spinner(f"正在刪除 {display_name}..."):
                            delete_stock_database(sid)
                            remaining = get_saved_stocks()
                            st.session_state["active_stock"] = remaining[0] if remaining else ""
                        st.rerun()
    else:
        st.caption("目前 database 尚無資料，請在上方輸入代號新增。")

if btn_add and input_stock:
    st.session_state["active_stock"] = input_stock

target_stock = st.session_state.get("active_stock", "").strip()

if target_stock:
    price_path = os.path.join(DB_DIR, f"{target_stock}_price.csv")
    chips_path = os.path.join(DB_DIR, f"{target_stock}_chips.csv")
    meta_path = os.path.join(DB_DIR, f"{target_stock}_meta.json")

    if (btn_add and input_stock) or not os.path.exists(price_path):
        with st.spinner(f"正在從 2026/01/02 抓取 {target_stock} 股價、投信、前 30 大主力與 5/10/20 日籌碼集中度..."):
            ok = update_stock_database(target_stock, incremental_days=None)
            if not ok:
                st.error(f"找不到股票代號 `{target_stock}`，請確認是否為正確的上市櫃代號（目前不支援興櫃）。")
                st.stop()
            cur_order = get_saved_stocks()
            if target_stock not in cur_order:
                cur_order.insert(0, target_stock)
                save_stock_order(cur_order, sync_github=True)
            st.rerun()

    df_price_full = pd.read_csv(price_path)
    df_chips_full = pd.read_csv(chips_path)
    with open(meta_path, "r", encoding="utf-8") as f:
        meta_saved = json.load(f)

    stock_title = get_stock_display_label(target_stock)

    # 計算統計區間起訖日
    min_date = df_price_full["date"].min()
    max_date = df_price_full["date"].max()
    days_map = {"今日": 1, "5日": 5, "10日": 10, "20日": 20, "60日": 60}

    if range_type in days_map:
        n_days = days_map[range_type]
        df_price = df_price_full.tail(n_days).copy()
        start_filter = df_price["date"].min()
        end_filter = df_price["date"].max()
        df_chips = df_chips_full[(df_chips_full["date"] >= start_filter) & (df_chips_full["date"] <= end_filter)].copy()
        period_label = f"今日（{end_filter}）" if n_days == 1 else f"近 {n_days} 日（{start_filter} ~ {end_filter}）"
    elif range_type == "指定日期":
        start_filter = parse_manual_date(custom_start_str, min_date)
        end_filter = parse_manual_date(custom_end_str, max_date)
        if start_filter > end_filter:
            st.error("起始日期不可晚於結束日期，請重新輸入。")
            st.stop()
        df_price = df_price_full[(df_price_full["date"] >= start_filter) & (df_price_full["date"] <= end_filter)].copy()
        df_chips = df_chips_full[(df_chips_full["date"] >= start_filter) & (df_chips_full["date"] <= end_filter)].copy()
        period_label = f"{start_filter} ~ {end_filter}"
    else:
        start_filter = min_date
        end_filter = max_date
        df_price = df_price_full.copy()
        df_chips = df_chips_full.copy()
        period_label = f"{min_date} ~ {max_date}（全部）"

    if df_price.empty:
        st.warning(f"在指定區間 `{start_filter} ~ {end_filter}` 內查無交易資料，請確認輸入的日期。")
        st.stop()

    # 計算區間股價漲跌幅
    latest_close = float(df_price.iloc[-1]["close"])
    prior_rows = df_price_full[df_price_full["date"] < start_filter]
    base_close = float(prior_rows.iloc[-1]["close"]) if not prior_rows.empty else float(df_price.iloc[0]["close"])
    pct_change = ((latest_close - base_close) / base_close * 100) if base_close else 0.0
    if pct_change > 0:
        pct_html = f'<span style="color:#ef5350; font-weight:700;">▲ {pct_change:+.2f}%</span>'
    elif pct_change < 0:
        pct_html = f'<span style="color:#26a69a; font-weight:700;">▼ {pct_change:+.2f}%</span>'
    else:
        pct_html = '<span style="color:#cbd5e1; font-weight:700;">0.00%</span>'

    concentrations = meta_saved.get("concentrations", {})
    analysis = analyze_main_force(
        df_price,
        df_chips,
        period_label,
        df_chips_full=df_chips_full,
        df_price_full=df_price_full,
        concentrations=concentrations,
    )
    broker_count = len([c for c in df_chips_full.columns if c not in ("date", "投信")])

    if broker_count == 0 and meta_saved.get("debug_notes"):
        st.warning(f"⚠️ 分點明細未取得（{'；'.join(meta_saved['debug_notes'])}）")

    # --- 區塊一：主力診斷結論（純券商分點口徑，不與投信重複計算） ---
    updated_str = meta_saved.get("last_updated", "")
    latest_trade_date = df_price_full["date"].max()
    time_badge = f"🕒 更新時間：{updated_str}（最新交易日：{latest_trade_date}）" if updated_str else f"🕒 最新交易日：{latest_trade_date}"

    st.markdown(
        f'<div style="display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:8px;">'
        f'  <h4 style="margin:0;">🎯 {stock_title} 主力診斷結論（{period_label}｜已追蹤 {broker_count} 個券商分點）</h4>'
        f'  <span style="font-size:0.85rem; color:#94a3b8;">{time_badge}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if meta_saved.get("github_sync"):
        st.caption(f"雲端資料庫狀態：{meta_saved['github_sync']}")

    c1, c2, c3 = st.columns([2.4, 2, 1.2])
    with c1:
        sub_html = "".join([
            f'<div class="card-sub">{line}</div>'
            for line in analysis.get("main_force_sub_lines", [])
        ])
        st.markdown(f"""
        <div class="summary-card">
            <div class="card-label">今年以來最大券商分點主力</div>
            <div class="card-value">{analysis.get("main_force_html", "N/A")}</div>
            {sub_html}
        </div>
        """, unsafe_allow_html=True)
    with c2:
        status_sub_html = (
            f'<div class="card-sub">{analysis["status_sub"]}</div>'
            if analysis.get("status_sub") else ""
        )
        st.markdown(f"""
        <div class="summary-card">
            <div class="card-label">區間內今年三大分點主力狀態</div>
            <div class="card-value">{analysis.get("status_html", "N/A")}</div>
            {status_sub_html}
        </div>
        """, unsafe_allow_html=True)
    with c3:
        st.markdown(f"""
        <div class="summary-card">
            <div class="card-label">最新收盤價與區間漲跌</div>
            <div class="card-value">{latest_close:.2f} 元</div>
            <div class="card-sub">區間漲跌：{pct_html}<br>更新：{updated_str[:16] if updated_str else latest_trade_date}</div>
        </div>
        """, unsafe_allow_html=True)

    # --- 區塊一之二：投信獨立追蹤列（身分維度獨立顯示，不與券商分點混算） ---
    trust_info = analysis.get("trust_info")
    if trust_info:
        trust_tip = (
            "投信下單分散於元大、富邦、國泰、玉山等各大本土券商法人專戶中，若與券商分點並列加總會產生重複計算。"
            "系統已將投信獨立於此列呈現，不計入下方券商分點排行與三大分點主力合計。"
        )
        cost_seg = ""
        if trust_info.get("cost", 0) > 0 and trust_info.get("pnl_pct") is not None:
            p_val = trust_info["pnl_pct"]
            p_col = "#ef5350" if p_val > 0 else ("#26a69a" if p_val < 0 else "#cbd5e1")
            cost_seg = (
                f'<span class="trust-item"><span class="trust-lbl">庫存均價</span>'
                f'<b style="color:#ffb74d;">{trust_info["cost"]:,.2f} 元</b> '
                f'(<span style="color:{p_col}; font-weight:700;">{p_val:+.2f}%</span>)</span>'
            )

        st.markdown(
            f'<div class="trust-bar">'
            f'  <div class="trust-title">'
            f'    🏦 投信獨立追蹤'
            f'    <span class="tip-wrap tip-right">ⓘ<span class="tip-box">{trust_tip}</span></span>'
            f'    <span class="trust-tag">獨立法人維度｜不與分點重複加總</span>'
            f'  </div>'
            f'  <div class="trust-metrics">'
            f'    <span class="trust-item"><span class="trust-lbl">今年累計</span>{_span_num(trust_info["ytd"])}</span>'
            f'    <span class="trust-item"><span class="trust-lbl">本區間</span>{_span_num(trust_info["period"])}</span>'
            f'    <span class="trust-item"><span class="trust-lbl">今日</span>{_span_num(trust_info["d1"])}</span>'
            f'    <span class="trust-item"><span class="trust-lbl">近 10 日</span>{_span_num(trust_info["d10"])}</span>'
            f'    <span class="trust-item"><span class="trust-lbl">近 20 日</span>{_span_num(trust_info["d20"])}</span>'
            f'    <span class="trust-item">{_span_streak(trust_info["streak"])}</span>'
            f'    <span class="trust-item"><span class="trust-lbl">控盤率</span>{_span_ctrl(trust_info["ctrl_pct"])}</span>'
            f'    {cost_seg}'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # --- 區塊一點五：全市場前 15 大籌碼集中度橫條 ＋ 五大劇本自動亮燈提示 ---
    conc_tip = (
        "計算公式：（該區間全市場前 15 大買超分點總張數 － 前 15 大賣超分點總張數）÷ 區間總成交量 × 100%。"
        "正值代表籌碼向大戶集中（偏多），負值代表籌碼流向散戶（發散）。此數據直接取自 MoneyDJ 總表，涵蓋全市場前 15 大分點。"
    )
    scenario = analysis.get("scenario", {})
    sc_type = scenario.get("type", "neutral")
    sc_cls = "scenario-bull" if sc_type == "bull" else ("scenario-bear" if sc_type == "bear" else "scenario-neutral")
    sc_action_html = f'<br><span style="color:#cbd5e1;">💡 操作應對：{scenario["action"]}</span>' if scenario.get("action") else ""

    st.markdown(
        f'<div class="conc-panel">'
        f'  <div class="conc-header">'
        f'    <div class="conc-title">'
        f'      📊 全市場前 15 大籌碼集中度'
        f'      <span class="tip-wrap tip-right">ⓘ<span class="tip-box">{conc_tip}</span></span>'
        f'      <span class="conc-sub-tag">短中線全市場視角，不限今年主力名單</span>'
        f'    </div>'
        f'  </div>'
        f'  <div class="conc-grid">'
        f'    {_format_conc_card("近 5 日集中度", concentrations.get("d5", {}))}'
        f'    {_format_conc_card("近 10 日集中度", concentrations.get("d10", {}))}'
        f'    {_format_conc_card("近 20 日集中度", concentrations.get("d20", {}))}'
        f'  </div>'
        f'  <div class="{sc_cls}">'
        f'    <b>{scenario.get("title", "")}</b><br>'
        f'    {scenario.get("desc", "")}'
        f'    {sc_action_html}'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # --- 可收合抽屜：價 × 點 × 面 三維度交叉判讀矩陣 ---
    matched_id = scenario.get("id", 0)
    with st.expander("📖 價 × 點 × 面 交叉判讀矩陣（點擊展開 5 大實戰劇本與操作應對）", expanded=False):
        m_rows = []
        for item in SCENARIO_MATRIX:
            is_match = (item["id"] == matched_id)
            row_cls = ""
            badge = ""
            if is_match:
                row_cls = "matrix-active-bull" if item["type"] == "bull" else "matrix-active-bear"
                badge = '<br><span style="display:inline-block; margin-top:4px; padding:1px 6px; border-radius:4px; background:#f59e0b; color:#0f172a; font-weight:700; font-size:0.75rem;">👈 目前符合</span>'
            m_rows.append(
                f'<tr class="{row_cls}">'
                f'  <td style="width:17%; font-weight:700;">{item["name"]}{badge}</td>'
                f'  <td style="width:18%;">{item.get("price_cond", "")}</td>'
                f'  <td style="width:16%;">{item.get("main_cond", "")}</td>'
                f'  <td style="width:21%;">{item.get("conc_cond", "")}</td>'
                f'  <td style="width:28%;">{item.get("action", "")}</td>'
                f'</tr>'
            )
        st.markdown(f"""
        <table class="matrix-table">
            <thead>
                <tr>
                    <th>盤面情境劇本</th>
                    <th>股價與均線（價）</th>
                    <th>三大分點主力（點）</th>
                    <th>5/10/20日集中度（面）</th>
                    <th>實戰解讀與操作應對</th>
                </tr>
            </thead>
            <tbody>{"".join(m_rows)}</tbody>
        </table>
        """, unsafe_allow_html=True)

    # --- 近 5 日價量籌碼背離警報 ---
    div_alert = analysis.get("divergence_alert")
    if div_alert:
        css_cls = "div-alert-bull" if div_alert["type"] == "bull" else "div-alert-bear"
        div_tip_text = (
            "觸發條件：近 5 日股價跌幅 ≥ 3%，且今年前三大券商分點主力近 5 日合計淨買超控盤率 ≥ +2.0%。"
            "代表大資金趁急跌有效承接，常為低檔換手或止跌轉折的前兆。"
            if div_alert["type"] == "bull" else
            "觸發條件：近 5 日股價漲幅 ≥ 3%，且今年前三大券商分點主力近 5 日合計淨賣超控盤率 ≤ -2.0%。"
            "代表大資金趁反彈有效出貨，需慎防假突破或拉高倒貨風險。"
        )
        st.markdown(
            f'<div class="{css_cls}">'
            f'  <b>{div_alert["title"]}</b>'
            f'  <span class="tip-wrap tip-right">ⓘ<span class="tip-box">{div_tip_text}</span></span>'
            f'  <br>{div_alert["desc"]}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # --- 前三大券商分點主力明細 ---
    st.write("")
    if analysis.get("top3_header_md"):
        st.markdown(analysis["top3_header_md"])

    top3_rows = analysis.get("top3_rows", [])
    if top3_rows:
        ctrl_tip_text = (
            "計算公式：該分點本區間淨買賣超張數 ÷ 本區間總成交量。"
            "正值代表買盤主導、負值代表賣壓主導；絕對值超過 10% 通常代表該主力對這段走勢具備高度控盤影響力。"
        )
        rows_html = []
        for r in top3_rows:
            rows_html.append(
                f'<div class="mf-row">'
                f'  <div class="mf-name">◦ {r["name"]}</div>'
                f'  <div class="mf-cell"><span class="mf-lbl">今年累計</span>{_span_num(r["ytd"])}</div>'
                f'  <div class="mf-cell"><span class="mf-lbl">本區間</span>{_span_num(r["period"])}</div>'
                f'  <div class="mf-cell"><span class="mf-lbl">今日</span>{_span_num(r["d1"])}</div>'
                f'  <div class="mf-cell"><span class="mf-lbl">近 10 日</span>{_span_num(r["d10"])}</div>'
                f'  <div class="mf-cell"><span class="mf-lbl">近 20 日</span>{_span_num(r["d20"])}</div>'
                f'  <div class="mf-cell" style="justify-content:center;">{_span_streak(r["streak"])}</div>'
                f'  <div class="mf-cell">'
                f'    <span class="mf-lbl">控盤率<span class="tip-wrap tip-left">ⓘ<span class="tip-box">{ctrl_tip_text}</span></span></span>'
                f'    {_span_ctrl(r["ctrl_pct"])}'
                f'  </div>'
                f'</div>'
            )
        st.markdown(f'<div class="mf-list">{"".join(rows_html)}</div>', unsafe_allow_html=True)

    if analysis.get("remark_md"):
        st.markdown(analysis["remark_md"])

    # --- 四欄並排排行榜（純券商分點口徑，不含投信）：今日買賣超前 5 大（左） ＋ 今年累計買賣超前 5 大（右） ---
    top_buyers = analysis.get("top_buyers", {})
    top_sellers = analysis.get("top_sellers", {})
    today_buyers = analysis.get("today_buyers", {})
    today_sellers = analysis.get("today_sellers", {})

    if top_buyers or top_sellers or today_buyers or today_sellers:
        st.write("")
        short_dt = latest_trade_date[5:] if len(latest_trade_date) >= 10 else latest_trade_date
        col_tb, col_ts, col_yb, col_ys = st.columns(4)
        with col_tb:
            st.markdown(
                _render_rank_table(f"🔴 今日分點買超前 5 大（{short_dt}）", today_buyers, "#ef5350", "今日無淨買超分點"),
                unsafe_allow_html=True,
            )
        with col_ts:
            st.markdown(
                _render_rank_table(f"🟢 今日分點賣超前 5 大（{short_dt}）", today_sellers, "#26a69a", "今日無淨賣超分點"),
                unsafe_allow_html=True,
            )
        with col_yb:
            st.markdown(
                _render_rank_table("🔴 今年分點買超前 5 大", top_buyers, "#ef5350", "今年無淨買超分點"),
                unsafe_allow_html=True,
            )
        with col_ys:
            st.markdown(
                _render_rank_table("🟢 今年分點賣超前 5 大", top_sellers, "#26a69a", "今年無淨賣超分點"),
                unsafe_allow_html=True,
            )

    st.divider()

    # --- 區塊二：完整年度股價、10MA/20MA、主力成本線 vs. 買賣超長條圖與總和統計張數折線 ---
    chart_title_suffix = f"（標示區間：{period_label}）" if range_type != "全部" else f"（{period_label}）"
    st.markdown(f"#### 📉 {stock_title} 股價、10/20MA 與主力成本線 vs. 買賣超統計{chart_title_suffix}")

    available_brokers = [c for c in df_chips_full.columns if c != "date"]
    top3_names = analysis.get("top3_names", [])
    default_selected = [top3_names[0]] if top3_names else available_brokers[:1]

    ms_key = f"ms_{target_stock}"
    if ms_key not in st.session_state:
        st.session_state[ms_key] = default_selected

    col_sel, col_btn, col_ma = st.columns([3.8, 1.2, 1.0])
    with col_btn:
        st.write("")
        st.write("")
        if st.button("⚡ 帶入前三大分點", width="stretch", disabled=not bool(top3_names)):
            st.session_state[ms_key] = [n for n in top3_names if n in available_brokers]
            st.rerun()
    with col_ma:
        st.write("")
        st.write("")
        show_ma = st.checkbox("顯示 10/20MA", value=True)
    with col_sel:
        selected_brokers = st.multiselect(
            "選擇要觀察的券商分點或「投信」（多選時自動加總計算合力成本；若欲觀察投信建議單獨勾選）：",
            options=available_brokers,
            key=ms_key
        )

    if not selected_brokers:
        st.warning("請至少選擇一個券商分點或「投信」來繪製圖表。")
    else:
        price_cols = [c for c in ["date", "open", "high", "low", "close"] if c in df_price_full.columns]
        df_plot = df_price_full[price_cols].merge(df_chips_full, on="date", how="left").fillna(0)
        df_plot["ma10"] = df_plot["close"].rolling(10).mean()
        df_plot["ma20"] = df_plot["close"].rolling(20).mean()

        daily_net = df_plot[selected_brokers].sum(axis=1)
        cum_total = daily_net.cumsum()
        cost_series, latest_cost, latest_inv = calculate_main_force_cost(df_plot, daily_net)

        target_label = selected_brokers[0] if len(selected_brokers) == 1 else f"所選 {len(selected_brokers)} 個分點合力"
        latest_full_close = float(df_plot.iloc[-1]["close"])
        if latest_cost > 0 and latest_inv > 0:
            sel_pnl = (latest_full_close - latest_cost) / latest_cost * 100
            pnl_color = "#ef5350" if sel_pnl > 0 else ("#26a69a" if sel_pnl < 0 else "#cbd5e1")
            st.markdown(
                f'<div class="cost-banner">💰 <b>{target_label}</b> 目前庫存均價（主力成本）：'
                f'<b style="color:#ffb74d;">{latest_cost:,.2f} 元</b> ｜ '
                f'今年剩餘庫存：<b>{int(round(latest_inv)):,} 張</b> ｜ '
                f'對照最新收盤價 ({latest_full_close:.2f} 元) 帳面損益：'
                f'<b style="color:{pnl_color};">{sel_pnl:+.2f}%</b></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="cost-banner">💰 <b>{target_label}</b> 目前剩餘庫存已歸零（無持有庫存，暫無主力成本均價）</div>',
                unsafe_allow_html=True,
            )

        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.06,
            row_heights=[0.52, 0.48],
            specs=[[{"secondary_y": False}], [{"secondary_y": True}]]
        )

        # 上層：10MA 與 20MA 均線
        if show_ma:
            fig.add_trace(
                go.Scatter(
                    x=df_plot["date"],
                    y=df_plot["ma10"],
                    name="10MA",
                    line=dict(color="rgba(56, 189, 248, 0.78)", width=1.3),
                    hovertemplate="%{y:,.2f} 元<extra>10MA</extra>"
                ),
                row=1, col=1, secondary_y=False
            )
            fig.add_trace(
                go.Scatter(
                    x=df_plot["date"],
                    y=df_plot["ma20"],
                    name="20MA",
                    line=dict(color="rgba(192, 132, 252, 0.78)", width=1.3),
                    hovertemplate="%{y:,.2f} 元<extra>20MA</extra>"
                ),
                row=1, col=1, secondary_y=False
            )

        # 上層：主力成本線（亮橘虛線）
        if cost_series.notna().any():
            fig.add_trace(
                go.Scatter(
                    x=df_plot["date"],
                    y=cost_series,
                    name=f"{target_label} 成本線",
                    line=dict(color="#FF9100", width=2.2, dash="dash"),
                    connectgaps=False,
                    hovertemplate="%{y:,.2f} 元<extra>主力成本均價</extra>"
                ),
                row=1, col=1, secondary_y=False
            )

        # 上層：收盤價折線（金黃色粗線置頂）
        fig.add_trace(
            go.Scatter(
                x=df_plot["date"],
                y=df_plot["close"],
                name="收盤價 (元)",
                line=dict(color="#FFD700", width=2.6),
                hovertemplate="%{y:,.2f} 元<extra>收盤價</extra>"
            ),
            row=1, col=1, secondary_y=False
        )

        # 下層主軸：每日買賣超長條圖（紅買超往上、綠賣超往下）
        bar_colors = ["#ef5350" if v >= 0 else "#26a69a" for v in daily_net]
        bar_label = f"{selected_brokers[0]} 每日買賣超" if len(selected_brokers) == 1 else f"所選({len(selected_brokers)}個)總和每日買賣超"

        fig.add_trace(
            go.Bar(
                x=df_plot["date"],
                y=daily_net,
                name=f"{bar_label} (張)",
                marker_color=bar_colors,
                opacity=0.85,
                hovertemplate="%{y:+,.0f} 張<extra>" + bar_label + "</extra>"
            ),
            row=2, col=1, secondary_y=False
        )

        # 下層副軸：統一顯示總和統計張數折線（累計買賣超，整數張數顯示）
        line_label = f"{selected_brokers[0]} 統計張數" if len(selected_brokers) == 1 else f"所選({len(selected_brokers)}個)總和統計張數"
        fig.add_trace(
            go.Scatter(
                x=df_plot["date"],
                y=cum_total,
                name=f"{line_label} (累計張)",
                line=dict(color="#00E5FF", width=2.5),
                hovertemplate="%{y:+,.0f} 張<extra>" + line_label + "</extra>"
            ),
            row=2, col=1, secondary_y=True
        )

        if range_type != "全部":
            if start_filter == end_filter:
                fig.add_vline(
                    x=end_filter,
                    line_width=2,
                    line_dash="dash",
                    line_color="rgba(255, 255, 255, 0.6)",
                    annotation_text=f"統計區間 ({range_type})",
                    annotation_position="top left",
                    annotation_font_color="#e5e7eb"
                )
            else:
                fig.add_vrect(
                    x0=start_filter,
                    x1=end_filter,
                    fillcolor="rgba(255, 255, 255, 0.14)",
                    line_width=1,
                    line_dash="dash",
                    line_color="rgba(255, 255, 255, 0.45)",
                    annotation_text=f"統計區間 ({range_type})",
                    annotation_position="top left",
                    annotation_font_color="#e5e7eb"
                )

        fig.add_hline(y=0, line_width=1, line_color="rgba(255, 255, 255, 0.25)", row=2, col=1)

        fig.update_layout(
            height=640,
            template="plotly_dark",
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.06, xanchor="left", x=0),
            margin=dict(l=20, r=20, t=75, b=20)
        )
        fig.update_yaxes(title_text="股價／均線／成本 (元)", row=1, col=1, showgrid=True)
        fig.update_yaxes(title_text="每日買賣超 (張)", row=2, col=1, secondary_y=False, showgrid=True, zeroline=True, tickformat=",.0f")
        fig.update_yaxes(title_text="統計張數 (累計張)", row=2, col=1, secondary_y=True, showgrid=False, tickformat=",.0f")

        st.plotly_chart(fig, width="stretch")
else:
    st.info("👈 請在左側輸入上市或上櫃股票代號（例如 `6488`、`2330`）新增建檔。")
