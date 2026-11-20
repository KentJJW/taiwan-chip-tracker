import os
import json
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from data_engine import DB_DIR, update_stock_database

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


st.markdown("### 📈 台股主力分點吃貨／出貨追蹤系統")

# --- 側邊欄：查詢與 database 管理 ---
with st.sidebar:
    st.header("🔍 查詢與建檔")
    input_stock = st.text_input("輸入台股代號（例如：6488、2330、3231）", value="6488").strip()
    range_mode = st.radio(
        "統計區間",
        options=["YTD", "1Y"],
        format_func=lambda x: "今年以來 (2026 YTD)" if x == "YTD" else "近一年 (365天)",
        horizontal=True
    )
    btn_query = st.button("抓取／更新籌碼資料", type="primary", use_container_width=True)

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
        with st.spinner(f"正在抓取 {target_stock} 今年以來股價、投信與各券商分點明細，並寫入 database/ ..."):
            ok = update_stock_database(target_stock, mode=range_mode)
            if not ok:
                st.error(f"找不到股票代號 `{target_stock}`，請確認是否為正確的上市櫃代號。")
                st.stop()
            st.rerun()

    df_price = pd.read_csv(price_path)
    df_chips = pd.read_csv(chips_path)
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    period_label = meta.get("period_label", "今年以來")
    broker_count = meta.get("broker_count", 0)

    if broker_count == 0 and meta.get("debug_notes"):
        st.warning(f"⚠️ 分點明細未完整取得（{ '；'.join(meta['debug_notes']) }），請點擊左側「抓取／更新籌碼資料」重試。")

    # --- 區塊一：直接回答「主力是誰」與「吃貨還是出貨」 ---
    st.markdown(f"#### 🎯 {target_stock} 主力診斷結論（{period_label}｜已追蹤 {broker_count} 個關鍵分點）")
    if meta.get("github_sync"):
        st.caption(f"雲端資料庫狀態：{meta['github_sync']}")

    c1, c2, c3 = st.columns([2.2, 2, 1.2])
    with c1:
        st.markdown(f"""
        <div class="summary-card">
            <div class="card-label">{period_label}最大主力</div>
            <div class="card-value">{meta.get("main_force", "N/A")}</div>
        </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="summary-card">
            <div class="card-label">目前籌碼狀態</div>
            <div class="card-value">{meta.get("status", "N/A")}</div>
        </div>
        """, unsafe_allow_html=True)
    with c3:
        st.markdown(f"""
        <div class="summary-card">
            <div class="card-label">最新收盤價</div>
            <div class="card-value">{df_price.iloc[-1]['close']:.2f} 元</div>
            <div class="card-sub">更新：{meta.get('last_updated', '')[:10]}</div>
        </div>
        """, unsafe_allow_html=True)

    st.write("")
    for line in meta.get("details", []):
        st.markdown(f"- {line}")

    top_buyers = meta.get("top_buyers", {})
    top_sellers = meta.get("top_sellers", {})
    if top_buyers:
        st.caption(f"🟢 {period_label}累計買超前 5 大（外資依個別券商統計）：" +
                   " ｜ ".join([f"**{k}**: {v:+,} 張" for k, v in top_buyers.items()]))
    if top_sellers:
        st.caption(f"🔴 {period_label}累計賣超前 5 大（外資依個別券商統計）：" +
                   " ｜ ".join([f"**{k}**: {v:+,} 張" for k, v in top_sellers.items()]))

    st.divider()

    # --- 區塊二：上方股價折線 ＋ 下方主力買賣超長條圖(紅買綠賣)與統計張數折線 ---
    st.markdown(f"#### 📉 {period_label}股價 vs. 主力買賣超長條圖與統計張數")

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

        # 建立上下兩層子圖：上層股價、下層主力買賣超長條圖(主軸) + 統計張數折線(副軸)
        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.06,
            row_heights=[0.52, 0.48],
            specs=[[{"secondary_y": False}], [{"secondary_y": True}]]
        )

        # 1. 上方子圖：收盤價折線
        fig.add_trace(
            go.Scatter(
                x=df_plot["date"],
                y=df_plot["close"],
                name="收盤價 (元)",
                line=dict(color="#FFD700", width=2.2)
            ),
            row=1, col=1, secondary_y=False
        )

        # 2. 下方子圖：每日買賣超長條圖（紅色買超往上、綠色賣超往下）
        daily_net = df_plot[selected_brokers].sum(axis=1)
        bar_colors = ["#ef5350" if v >= 0 else "#26a69a" for v in daily_net]
        bar_label = f"{selected_brokers[0]} 每日買賣超" if len(selected_brokers) == 1 else f"所選({len(selected_brokers)}個)合計每日買賣超"

        fig.add_trace(
            go.Bar(
                x=df_plot["date"],
                y=daily_net,
                name=f"{bar_label} (張)",
                marker_color=bar_colors,
                opacity=0.85
            ),
            row=2, col=1, secondary_y=False
        )

        # 3. 下方子圖疊加：統計張數（累計淨買賣超折線）
        if chart_mode == "所選分點加總合力" or len(selected_brokers) == 1:
            cum_total = daily_net.cumsum()
            line_label = f"{selected_brokers[0]} 統計張數" if len(selected_brokers) == 1 else "所選合力統計張數"
            fig.add_trace(
                go.Scatter(
                    x=df_plot["date"],
                    y=cum_total,
                    name=f"{line_label} (累計張)",
                    line=dict(color="#00E5FF", width=2.5)
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
                        line=dict(width=2)
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
        fig.update_yaxes(title_text="每日買賣超 (張)", row=2, col=1, secondary_y=False, showgrid=True, zeroline=True)
        fig.update_yaxes(title_text="統計張數 (累計張)", row=2, col=1, secondary_y=True, showgrid=False)

        st.plotly_chart(fig, use_container_width=True)
