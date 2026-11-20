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
    update_stock_database,
    analyze_main_force,
    delete_stock_database,
    calculate_main_force_cost,
    fetch_stock_name,
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
.div-alert-bull {
    background-color: rgba(239, 83, 80, 0.12);
    border-left: 4px solid #ef5350;
    border-radius: 6px;
    padding: 10px 14px;
    margin: 10px 0 4px 0;
    font-size: 0.9rem;
    color: #f8fafc;
}
.div-alert-bear {
    background-color: rgba(38, 166, 154, 0.12);
    border-left: 4px solid #26a69a;
    border-radius: 6px;
    padding: 10px 14px;
    margin: 10px 0 4px 0;
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
    width: 290px;
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
    font-size: 0.88rem;
    background-color: #161922;
    border-radius: 8px;
    overflow: hidden;
}
.rank-table th {
    background-color: #222736;
    color: #cbd5e1;
    text-align: left;
    padding: 8px 12px;
    font-weight: 600;
}
.rank-table td {
    padding: 7px 12px;
    border-top: 1px solid #262c3d;
    color: #e2e8f0;
    font-variant-numeric: tabular-nums;
}
</style>
""", unsafe_allow_html=True)


def get_saved_stocks() -> list:
    if not os.path.exists(DB_DIR):
        return []
    return sorted({
        f.split("_")[0] for f in os.listdir(DB_DIR) if f.endswith("_price.csv")
    })


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
    """支援手動輸入 20260501、2026/5/1、2026-05-01 等格式"""
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


st.markdown("### 📈 台股主力分點吃貨／出貨追蹤系統")

saved_list = get_saved_stocks()
if "active_stock" not in st.session_state:
    st.session_state["active_stock"] = saved_list[0] if saved_list else "6488"

# --- 側邊欄：查詢、統計區間與 database 管理 ---
with st.sidebar:
    st.header("🔍 查詢與建檔")
    input_stock = st.text_input("輸入台股代號（例如：6488、2330、3231）", value="6488").strip()
    btn_query = st.button("抓取／更新資料 (2026/01/02起)", type="primary", use_container_width=True)

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
    st.subheader(f"📁 database 已存個股 ({len(saved_list)} 檔)")
    if saved_list:
        for sid in saved_list:
            col_s, col_del = st.columns([4, 1])
            display_name = get_stock_display_label(sid)
            with col_s:
                is_current = (sid == st.session_state.get("active_stock"))
                btn_label = f"▶ {display_name}" if is_current else display_name
                if st.button(btn_label, key=f"sel_{sid}", use_container_width=True):
                    st.session_state["active_stock"] = sid
                    st.rerun()
            with col_del:
                if st.button("✕", key=f"del_{sid}", help=f"刪除 {display_name} 的所有歷史資料", use_container_width=True):
                    with st.spinner(f"正在刪除 {display_name}..."):
                        delete_stock_database(sid)
                        remaining = get_saved_stocks()
                        st.session_state["active_stock"] = remaining[0] if remaining else ""
                    st.rerun()
    else:
        st.caption("目前 database 尚無資料，請輸入代號查詢。")

if btn_query and input_stock:
    st.session_state["active_stock"] = input_stock

target_stock = st.session_state.get("active_stock", "").strip()

if target_stock:
    price_path = os.path.join(DB_DIR, f"{target_stock}_price.csv")
    chips_path = os.path.join(DB_DIR, f"{target_stock}_chips.csv")
    meta_path = os.path.join(DB_DIR, f"{target_stock}_meta.json")

    if btn_query or not os.path.exists(price_path):
        with st.spinner(f"正在從 2026/01/02 抓取 {target_stock} 股價、投信與前 25 大券商分點明細..."):
            ok = update_stock_database(target_stock)
            if not ok:
                st.error(f"找不到股票代號 `{target_stock}`，請確認是否為正確的上市櫃代號（目前不支援興櫃）。")
                st.stop()
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

    analysis = analyze_main_force(
        df_price,
        df_chips,
        period_label,
        df_chips_full=df_chips_full,
        df_price_full=df_price_full,
    )
    broker_count = len([c for c in df_chips_full.columns if c not in ("date", "投信")])

    if broker_count == 0 and meta_saved.get("debug_notes"):
        st.warning(f"⚠️ 分點明細未取得（{'；'.join(meta_saved['debug_notes'])}）")

    # --- 區塊一：主力診斷結論 ---
    st.markdown(f"#### 🎯 {stock_title} 主力診斷結論（{period_label}｜已追蹤 {broker_count} 個券商分點）")
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
            <div class="card-label">今年以來最大主力</div>
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
            <div class="card-label">區間內三大主力狀態</div>
            <div class="card-value">{analysis.get("status_html", "N/A")}</div>
            {status_sub_html}
        </div>
        """, unsafe_allow_html=True)
    with c3:
        st.markdown(f"""
        <div class="summary-card">
            <div class="card-label">最新收盤價與區間漲跌</div>
            <div class="card-value">{latest_close:.2f} 元</div>
            <div class="card-sub">區間漲跌：{pct_html}<br>更新：{meta_saved.get('last_updated', '')[:10]}</div>
        </div>
        """, unsafe_allow_html=True)

    # --- 近 5 日價量籌碼背離警報（含 ⓘ 指標說明） ---
    div_alert = analysis.get("divergence_alert")
    if div_alert:
        css_cls = "div-alert-bull" if div_alert["type"] == "bull" else "div-alert-bear"
        div_tip_text = (
            "觸發條件：近 5 個交易日股價下跌達 3% 以上，但今年前三大波段主力合計仍為淨買超。"
            "代表主力趁急跌逢低承接，常為低檔換手或止跌轉折的前兆。"
            if div_alert["type"] == "bull" else
            "觸發條件：近 5 個交易日股價上漲達 3% 以上，但今年前三大波段主力合計卻為淨賣超。"
            "代表主力趁反彈逢高出貨，需慎防假突破或拉高倒貨風險。"
        )
        st.markdown(
            f'<div class="{css_cls}">'
            f'  <b>{div_alert["title"]}</b>'
            f'  <span class="tip-wrap tip-right">ⓘ<span class="tip-box">{div_tip_text}</span></span>'
            f'  <br>{div_alert["desc"]}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # --- 前三大主力明細（CSS 隱形網格對齊，含連買賣與控盤率 ⓘ 說明） ---
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

    # --- 買超前 5 大 & 賣超前 5 大左右並排對照表 ---
    top_buyers = analysis.get("top_buyers", {})
    top_sellers = analysis.get("top_sellers", {})
    if top_buyers or top_sellers:
        st.write("")
        col_b, col_s = st.columns(2)
        with col_b:
            rows_b = "".join([
                f'<tr><td>#{i+1}</td><td><b>{k}</b></td><td style="text-align:right; color:#ef5350; font-weight:700;">{v:+,} 張</td></tr>'
                for i, (k, v) in enumerate(top_buyers.items())
            ]) or '<tr><td colspan="3">無淨買超分點</td></tr>'
            st.markdown(f"""
            <table class="rank-table">
                <thead><tr><th colspan="3">🟢 今年以來累計買超前 5 大（外資依個別券商統計）</th></tr></thead>
                <tbody>{rows_b}</tbody>
            </table>
            """, unsafe_allow_html=True)
        with col_s:
            rows_s = "".join([
                f'<tr><td>#{i+1}</td><td><b>{k}</b></td><td style="text-align:right; color:#26a69a; font-weight:700;">{v:+,} 張</td></tr>'
                for i, (k, v) in enumerate(top_sellers.items())
            ]) or '<tr><td colspan="3">無淨賣超分點</td></tr>'
            st.markdown(f"""
            <table class="rank-table">
                <thead><tr><th colspan="3">🔴 今年以來累計賣超前 5 大（外資依個別券商統計）</th></tr></thead>
                <tbody>{rows_s}</tbody>
            </table>
            """, unsafe_allow_html=True)

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

    col_sel, col_btn, col_ma = st.columns([3.8, 1.1, 1.0])
    with col_btn:
        st.write("")
        st.write("")
        if st.button("⚡ 帶入前三大主力", use_container_width=True, disabled=not bool(top3_names)):
            st.session_state[ms_key] = [n for n in top3_names if n in available_brokers]
            st.rerun()
    with col_ma:
        st.write("")
        st.write("")
        show_ma = st.checkbox("顯示 10/20MA", value=True)
    with col_sel:
        selected_brokers = st.multiselect(
            "選擇要觀察的券商分點或「投信」（可單選或多選，多選時自動加總計算合力成本與總和折線）：",
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

        # 顯示所選主力的目前庫存均價與帳面損益摘要條
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

        # 上層：10MA 與 20MA 均線（先畫在底層，避免蓋住純白股價線）
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

        # 上層：主力成本線（亮橘虛線，高對比）
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

        # 上層：收盤價折線（純白粗線置頂，視覺最清晰）
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

        # 選定特定統計區間時，在圖表中標示（單日用垂直虛線，多日用反灰區塊）
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

        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("👈 請在左側輸入上市或上櫃股票代號（例如 `6488`、`2330`）開始查詢。")
