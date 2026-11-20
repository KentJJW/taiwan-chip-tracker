import os
import re
import json
from datetime import datetime
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from data_engine import DB_DIR, START_DATE_FIXED, update_stock_database, analyze_main_force

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
    font-size: 0.8rem;
    color: #34d399;
    margin-top: 4px;
}
</style>
""", unsafe_allow_html=True)


def get_saved_stocks() -> list:
    if not os.path.exists(DB_DIR):
        return []
    return sorted({
        f.split("_")[0] for f in os.listdir(DB_DIR) if f.endswith("_price.csv")
    })


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


st.markdown("### 📈 台股主力分點吃貨／出貨追蹤系統")

# --- 側邊欄：查詢與統計區間設定 ---
with st.sidebar:
    st.header("🔍 查詢與建檔")
    input_stock = st.text_input("輸入台股代號（例如：6488、2330、3231）", value="6488").strip()
    btn_query = st.button("抓取／更新資料 (2026/01/02起)", type="primary", use_container_width=True)

    st.divider()
    st.subheader("📅 統計區間")
    range_type = st.radio("選擇檢視範圍", options=["全部", "指定日期"], horizontal=True)

    custom_start_str = "20260102"
    custom_end_str = datetime.today().strftime("%Y%m%d")
    if range_type == "指定日期":
        st.caption("請直接輸入 8 碼數字（例：20260501）")
        custom_start_str = st.text_input("起始日期", value="20260102")
        custom_end_str = st.text_input("結束日期", value=datetime.today().strftime("%Y%m%d"))

    saved_list = get_saved_stocks()
    st.divider()
    st.subheader(f"📁 database 已存個股 ({len(saved_list)} 檔)")
    if saved_list:
        default_idx = saved_list.index(input_stock) if input_stock in saved_list else 0
        selected_from_db = st.selectbox("快速切換已存個股", saved_list, index=default_idx)
    else:
        selected_from_db = None
        st.caption("目前 database 尚無資料，請輸入代號查詢。")

target_stock = input_stock if btn_query or (input_stock and input_stock not in saved_list) else (selected_from_db or input_stock)

if target_stock:
    price_path = os.path.join(DB_DIR, f"{target_stock}_price.csv")
    chips_path = os.path.join(DB_DIR, f"{target_stock}_chips.csv")
    meta_path = os.path.join(DB_DIR, f"{target_stock}_meta.json")

    if btn_query or not os.path.exists(price_path):
        with st.spinner(f"正在從 2026/01/02 抓取 {target_stock} 股價、投信與前 25 大券商分點明細..."):
            ok = update_stock_database(target_stock)
            if not ok:
                st.error(f"找不到股票代號 `{target_stock}`，請確認是否為正確的上市櫃代號。")
                st.stop()
            st.rerun()

    df_price_full = pd.read_csv(price_path)
    df_chips_full = pd.read_csv(chips_path)
    with open(meta_path, "r", encoding="utf-8") as f:
        meta_saved = json.load(f)

    # 依「全部」或「指定日期」篩選資料區間
    min_date = df_price_full["date"].min()
    max_date = df_price_full["date"].max()

    if range_type == "指定日期":
        start_filter = parse_manual_date(custom_start_str, min_date)
        end_filter = parse_manual_date(custom_end_str, max_date)
        if start_filter > end_filter:
            st.error("起始日期不可晚於結束日期，請重新輸入。")
            st.stop()
        period_label = f"{start_filter} ~ {end_filter}"
    else:
        start_filter = START_DATE_FIXED
        end_filter = max_date
        period_label = f"{min_date} ~ {max_date}（全部）"

    df_price = df_price_full[(df_price_full["date"] >= start_filter) & (df_price_full["date"] <= end_filter)].copy()
    df_chips = df_chips_full[(df_chips_full["date"] >= start_filter) & (df_chips_full["date"] <= end_filter)].copy()

    if df_price.empty:
        st.warning(f"在指定區間 `{start_filter} ~ {end_filter}` 內查無交易資料，請確認輸入的日期。")
        st.stop()

    analysis = analyze_main_force(df_price, df_chips, period_label)
    broker_count = len([c for c in df_chips.columns if c not in ("date", "投信")])

    if broker_count == 0 and meta_saved.get("debug_notes"):
        st.warning(f"⚠️ 分點明細未取得（{'；'.join(meta_saved['debug_notes'])}）")

    # --- 區塊一：直接回答「主力是誰」與「吃貨還是出貨」 ---
    st.markdown(f"#### 🎯 {target_stock} 主力診斷結論（{period_label}｜已追蹤 {broker_count} 個券商分點）")
    if meta_saved.get("github_sync"):
        st.caption(f"雲端資料庫狀態：{meta_saved['github_sync']}")

    c1, c2, c3 = st.columns([2.2, 2, 1.2])
    with c1:
        st.markdown(f"""
        <div class="summary-card">
            <div class="card-label">區間最大主力</div>
            <div class="card-value">{analysis.get("main_force", "N/A")}</div>
        </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="summary-card">
            <div class="card-label">目前籌碼狀態</div>
            <div class="card-value">{analysis.get("status", "N/A")}</div>
        </div>
        """, unsafe_allow_html=True)
    with c3:
        st.markdown(f"""
        <div class="summary-card">
            <div class="card-label">區間最新收盤價</div>
            <div class="card-value">{df_price.iloc[-1]['close']:.2f} 元</div>
            <div class="card-sub">資料更新：{meta_saved.get('last_updated', '')[:10]}</div>
        </div>
        """, unsafe_allow_html=True)

    st.write("")
    for line in analysis.get("details", []):
        st.markdown(f"- {line}")

    top_buyers = analysis.get("top_buyers", {})
    top_sellers = analysis.get("top_sellers", {})
    if top_buyers:
        st.caption("🟢 區間累計買超前 5 大（外資依個別券商統計）：" +
                   " ｜ ".join([f"**{k}**: {v:+,} 張" for k, v in top_buyers.items()]))
    if top_sellers:
        st.caption("🔴 區間累計賣超前 5 大（外資依個別券商統計）：" +
                   " ｜ ".join([f"**{k}**: {v:+,} 張" for k, v in top_sellers.items()]))

    st.divider()

    # --- 區塊二：上方股價折線 ＋ 下方主力買賣超長條圖(紅買綠賣)與統計張數折線 ---
    st.markdown(f"#### 📉 股價 vs. 主力買賣超長條圖與統計張數（{period_label}）")

    available_brokers = [c for c in df_chips.columns if c != "date"]
    default_selected = [list(top_buyers.keys())[0]] if top_buyers else available_brokers[:1]

    col_ctrl1, col_ctrl2 = st.columns([3, 1.2])
    with col_ctrl1:
        selected_brokers = st.multiselect(
            "選擇要觀察的券商分點或「投信」（可單選或多選）：",
            options=available_brokers,
            default=default_selected
        )
    with col_ctrl2:
        chart_mode = st.radio(
            "多分點顯示方式",
            options=["所選分點加總合力", "各分點獨立統計折線"],
            horizontal=False
        )

    if not selected_brokers:
        st.warning("請至少選擇一個券商分點或「投信」來繪製圖表。")
    else:
        df_plot = df_price[["date", "close"]].merge(df_chips, on="date", how="left").fillna(0)

        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.06,
            row_heights=[0.52, 0.48],
            specs=[[{"secondary_y": False}], [{"secondary_y": True}]]
        )

        # 上層：股價折線
        fig.add_trace(
            go.Scatter(
                x=df_plot["date"],
                y=df_plot["close"],
                name="收盤價 (元)",
                line=dict(color="#FFD700", width=2.2),
                hovertemplate="%{y:,.2f} 元<extra>收盤價</extra>"
            ),
            row=1, col=1, secondary_y=False
        )

        # 下層主軸：每日買賣超長條圖（紅買超往上、綠賣超往下）
        daily_net = df_plot[selected_brokers].sum(axis=1)
        bar_colors = ["#ef5350" if v >= 0 else "#26a69a" for v in daily_net]
        bar_label = f"{selected_brokers[0]} 每日買賣超" if len(selected_brokers) == 1 else f"所選({len(selected_brokers)}個)合計每日買賣超"

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

        # 下層副軸：統計張數折線（累計買賣超，完整整數張數不顯示 K）
        if chart_mode == "所選分點加總合力" or len(selected_brokers) == 1:
            cum_total = daily_net.cumsum()
            line_label = f"{selected_brokers[0]} 統計張數" if len(selected_brokers) == 1 else "所選合力統計張數"
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
        else:
            for b_name in selected_brokers:
                fig.add_trace(
                    go.Scatter(
                        x=df_plot["date"],
                        y=df_plot[b_name].cumsum(),
                        name=f"{b_name} 統計張數",
                        line=dict(width=2),
                        hovertemplate="%{y:+,.0f} 張<extra>" + b_name + "</extra>"
                    ),
                    row=2, col=1, secondary_y=True
                )

        fig.add_hline(y=0, line_width=1, line_color="rgba(255, 255, 255, 0.25)", row=2, col=1)

        fig.update_layout(
            height=640,
            template="plotly_dark",
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=20, r=20, t=40, b=20)
        )
        fig.update_yaxes(title_text="收盤價 (元)", row=1, col=1, showgrid=True)
        fig.update_yaxes(title_text="每日買賣超 (張)", row=2, col=1, secondary_y=False, showgrid=True, zeroline=True, tickformat=",.0f")
        fig.update_yaxes(title_text="統計張數 (累計張)", row=2, col=1, secondary_y=True, showgrid=False, tickformat=",.0f")

        st.plotly_chart(fig, use_container_width=True)
