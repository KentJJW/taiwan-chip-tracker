import os
import re
import json
from datetime import datetime
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from strategy import build_z_tooltip_html
from data_engine import (
    DB_DIR,
    SCENARIO_MATRIX,
    STRATEGY_PARAMS,
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
/* --- 核心決策總結看板（結論置頂） --- */
.hero-panel {
    display: grid;
    grid-template-columns: 220px 1fr;
    gap: 16px;
    background-color: #161b26;
    border: 1px solid #2e374b;
    border-radius: 10px;
    padding: 14px 18px;
    margin-top: 6px;
    margin-bottom: 10px;
    align-items: center;
}
.hero-bull {
    border-left: 5px solid #ef5350;
    background: linear-gradient(90deg, rgba(239, 83, 80, 0.10) 0%, #161b26 35%);
}
.hero-bear {
    border-left: 5px solid #26a69a;
    background: linear-gradient(90deg, rgba(38, 166, 154, 0.10) 0%, #161b26 35%);
}
.hero-neutral {
    border-left: 5px solid #64748b;
    background: linear-gradient(90deg, rgba(100, 116, 139, 0.10) 0%, #161b26 35%);
}
.hero-score-box {
    border-right: 1px solid #2d364a;
    padding-right: 16px;
}
.hero-score-label {
    font-size: 0.82rem;
    color: #9aa4b2;
    display: flex;
    align-items: center;
    gap: 4px;
    margin-bottom: 4px;
}
.score-val {
    font-size: 2.0rem;
    font-weight: 800;
    font-variant-numeric: tabular-nums;
    line-height: 1.15;
}
.score-bar-bg {
    width: 100%;
    height: 7px;
    background-color: #273043;
    border-radius: 4px;
    margin: 7px 0 6px 0;
    overflow: hidden;
}
.score-bar-fill {
    height: 100%;
    border-radius: 4px;
}
.hero-score-sub {
    font-size: 0.8rem;
    color: #cbd5e1;
    font-weight: 600;
}
.hero-body-title {
    font-size: 1.02rem;
    font-weight: 700;
    color: #f8fafc;
    margin-bottom: 4px;
}
.hero-body-desc {
    font-size: 0.85rem;
    color: #cbd5e1;
    line-height: 1.48;
}
.hero-body-action {
    font-size: 0.85rem;
    color: #fde68a;
    margin-top: 5px;
    line-height: 1.45;
}

/* --- 精簡單行背離警示條 --- */
.div-strip {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    border-radius: 7px;
    padding: 8px 14px;
    margin-bottom: 10px;
    font-size: 0.85rem;
    line-height: 1.4;
}
.div-strip-bull {
    background-color: rgba(239, 83, 80, 0.12);
    border: 1px solid rgba(239, 83, 80, 0.35);
    border-left: 4px solid #ef5350;
    color: #f8fafc;
}
.div-strip-bear {
    background-color: rgba(38, 166, 154, 0.12);
    border: 1px solid rgba(38, 166, 154, 0.35);
    border-left: 4px solid #26a69a;
    color: #f8fafc;
}
.div-strip-warn {
    background-color: rgba(245, 158, 11, 0.12);
    border: 1px solid rgba(245, 158, 11, 0.35);
    border-left: 4px solid #f59e0b;
    color: #fef3c7;
}

/* --- 三張核心數據卡片 --- */
.summary-card {
    background-color: #1c202a;
    border: 1px solid #2e3444;
    border-radius: 10px;
    padding: 13px 16px;
    height: 100%;
    position: relative;
    overflow: visible;
}
.card-label {
    font-size: 0.82rem;
    color: #9aa4b2;
    margin-bottom: 5px;
    display: flex;
    align-items: center;
    gap: 4px;
}
.card-value {
    font-size: 1.08rem;
    font-weight: 700;
    color: #f3f4f6;
    line-height: 1.38;
    word-break: break-word;
}
.card-sub {
    font-size: 0.8rem;
    color: #94a3b8;
    margin-top: 6px;
    line-height: 1.45;
}

/* --- 投信獨立追蹤列（主次分明） --- */
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
    color: #e2e8f0;
}
.trust-title {
    font-weight: 700;
    font-size: 0.9rem;
    color: #f8fafc;
    display: inline-flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
}
.trust-tag {
    font-size: 0.74rem;
    color: #cbd5e1;
    background-color: #232a3b;
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: 600;
}
.trust-metrics-wrap {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 14px;
    font-variant-numeric: tabular-nums;
}
.trust-primary {
    display: inline-flex;
    align-items: center;
    gap: 10px;
    background-color: #111622;
    border: 1px solid #2b354b;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 0.92rem;
}
.trust-secondary {
    display: inline-flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 11px;
    font-size: 0.8rem;
    color: #94a3b8;
}
.trust-lbl {
    color: #94a3b8;
    font-size: 0.78rem;
    margin-right: 3px;
}

/* --- 四大輔助籌碼卡片（去雜訊＋狀態標籤） --- */
.aux-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin-top: 10px;
}
.aux-card {
    background-color: #161b26;
    border: 1px solid #2a3347;
    border-radius: 8px;
    padding: 10px 13px;
    font-variant-numeric: tabular-nums;
}
.aux-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
    gap: 6px;
}
.aux-title {
    font-size: 0.82rem;
    font-weight: 700;
    color: #cbd5e1;
    display: inline-flex;
    align-items: center;
}
.aux-badge {
    font-size: 0.72rem;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 4px;
    white-space: nowrap;
}
.aux-main {
    font-size: 1.05rem;
    font-weight: 700;
    color: #f8fafc;
    line-height: 1.35;
}
.aux-sub {
    font-size: 0.78rem;
    color: #94a3b8;
    margin-top: 4px;
    line-height: 1.4;
}

/* --- 全市場集中度面板 --- */
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
    margin-bottom: 9px;
    gap: 8px;
}
.conc-title {
    font-size: 0.9rem;
    font-weight: 700;
    color: #e2e8f0;
    display: inline-flex;
    align-items: center;
    flex-wrap: wrap;
}
.conc-sub-tag {
    font-size: 0.76rem;
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
    gap: 8px;
}
.conc-box-left {
    display: flex;
    flex-direction: column;
}
.conc-period {
    font-size: 0.79rem;
    color: #94a3b8;
    margin-bottom: 2px;
}
.conc-val-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
}
.conc-val {
    font-size: 1.15rem;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
}
.conc-z {
    font-size: 0.78rem;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 4px;
    background-color: #252d3f;
    font-variant-numeric: tabular-nums;
}
.conc-z-pending {
    font-size: 0.71rem;
    color: #64748b;
    padding: 1px 5px;
    border-radius: 4px;
    background-color: rgba(100, 116, 139, 0.14);
}
.conc-badge {
    font-size: 0.77rem;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 5px;
    white-space: nowrap;
}

/* --- 共振徽章 --- */
.badge-row {
    display: flex;
    flex-wrap: wrap;
    gap: 7px;
    margin-top: 8px;
}
.res-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 0.77rem;
    font-weight: 600;
    padding: 3px 9px;
    border-radius: 999px;
    border: 1px solid transparent;
    cursor: help;
}
.res-bull {
    background-color: rgba(239, 83, 80, 0.16);
    border-color: rgba(239, 83, 80, 0.4);
    color: #fca5a5;
}
.res-bear {
    background-color: rgba(38, 166, 154, 0.16);
    border-color: rgba(38, 166, 154, 0.4);
    color: #80cbc4;
}
.res-warn {
    background-color: rgba(245, 158, 11, 0.16);
    border-color: rgba(245, 158, 11, 0.45);
    color: #fcd34d;
}
.res-info {
    background-color: rgba(148, 163, 184, 0.14);
    border-color: rgba(148, 163, 184, 0.35);
    color: #cbd5e1;
}

/* --- 明細與排行榜 --- */
.top3-header-line {
    font-size: 0.94rem;
    color: #f1f5f9;
    margin: 4px 0 6px 4px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
}
.mf-list {
    margin: 2px 0 8px 16px;
    overflow: visible;
}
.mf-row {
    display: grid;
    grid-template-columns: 205px 140px 130px 112px 128px 128px 88px 132px;
    align-items: center;
    padding: 5px 0;
    font-size: 0.89rem;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    color: #e2e8f0;
    border-bottom: 1px dashed rgba(51, 65, 85, 0.45);
}
.mf-name {
    font-weight: 700;
    color: #f8fafc;
    padding-right: 8px;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}
.mf-tag-pill {
    font-size: 0.7rem;
    font-weight: 600;
    padding: 1px 5px;
    border-radius: 4px;
    background-color: rgba(251, 191, 36, 0.16);
    color: #fcd34d;
}
.mf-cell {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0 9px;
    border-left: 1px solid #334155;
}
.mf-lbl {
    color: #94a3b8;
    font-size: 0.81rem;
    margin-right: 5px;
    display: inline-flex;
    align-items: center;
}
.smart-grid {
    display: grid;
    grid-template-columns: 1.35fr 1fr;
    gap: 12px;
    margin: 10px 0 4px 0;
}
.smart-panel {
    background-color: #161b26;
    border: 1px solid #2b3448;
    border-radius: 8px;
    padding: 10px 13px;
}
.smart-title {
    font-size: 0.84rem;
    font-weight: 700;
    color: #e2e8f0;
    margin-bottom: 6px;
    display: flex;
    align-items: center;
    justify-content: space-between;
}

/* --- 懸浮提示框 --- */
.tip-wrap {
    position: relative;
    display: inline-flex;
    align-items: center;
    cursor: help;
    margin-left: 4px;
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
    z-index: 99999;
    width: 340px;
    background-color: #0f172a;
    color: #e2e8f0;
    border: 1px solid #475569;
    border-radius: 7px;
    padding: 10px 12px;
    font-size: 0.79rem;
    font-weight: 400;
    line-height: 1.55;
    white-space: normal;
    text-align: left;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.72);
    transition: opacity 0.15s ease;
    pointer-events: none;
}
.tip-box-wide {
    width: 420px;
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
.tip-down .tip-box {
    left: -10px;
    top: 145%;
}
.tip-down-left .tip-box {
    right: -14px;
    top: 145%;
}
.tip-mini-tbl {
    width: 100%;
    border-collapse: collapse;
    margin-top: 6px;
    font-size: 0.76rem;
}
.tip-mini-tbl th {
    background-color: #1e293b;
    color: #cbd5e1;
    padding: 4px 6px;
    text-align: left;
    border-bottom: 1px solid #334155;
    white-space: nowrap;
}
.tip-mini-tbl td {
    padding: 5px 6px;
    border-bottom: 1px solid #1e293b;
    color: #e2e8f0;
    vertical-align: top;
    line-height: 1.4;
}
.cost-banner {
    background-color: #1a2230;
    border-left: 4px solid #ff9100;
    border-radius: 6px;
    padding: 9px 14px;
    margin: 6px 0 10px 0;
    font-size: 0.89rem;
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
    font-size: 0.83rem;
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


def _span_num(val: int, suffix: str = " 張", invert_color: bool = False) -> str:
    if val == 0:
        return f'<span style="color:#cbd5e1; font-weight:700;">0{suffix}</span>'
    is_bull_color = (val < 0) if invert_color else (val > 0)
    color = "#ef5350" if is_bull_color else "#26a69a"
    return f'<span style="color:{color}; font-weight:700;">{val:+,}{suffix}</span>'


def _span_float(val: float, suffix: str = "%", invert_color: bool = False) -> str:
    if abs(val) < 1e-9:
        return f'<span style="color:#cbd5e1; font-weight:700;">0.00{suffix}</span>'
    is_bull_color = (val < 0) if invert_color else (val > 0)
    color = "#ef5350" if is_bull_color else "#26a69a"
    return f'<span style="color:{color}; font-weight:700;">{val:+.2f}{suffix}</span>'


def _span_streak(streak: int) -> str:
    if streak > 0:
        return f'<span style="color:#ef5350; font-weight:700;">連買 {streak} 日</span>'
    elif streak < 0:
        return f'<span style="color:#26a69a; font-weight:700;">連賣 {abs(streak)} 日</span>'
    return '<span style="color:#94a3b8;">持平</span>'


def _span_ctrl(pct: float) -> str:
    color = "#ef5350" if pct > 0 else ("#26a69a" if pct < 0 else "#94a3b8")
    return f'<span style="color:{color}; font-weight:700;">{pct:+.1f}%</span>'


def _span_zscore(z_val: float | None, z_samples: int = 0) -> str:
    if z_val is None:
        if z_samples > 0:
            return f'<span class="conc-z-pending" title="需累積滿 15 個交易日同口徑官方集中度快照才會啟用 Z-Score">Z累積 {z_samples}/15日</span>'
        return ""
    if z_val >= STRATEGY_PARAMS["z_bull_min"]:
        col = "#ef5350"
    elif z_val <= STRATEGY_PARAMS["z_bear_max"]:
        col = "#26a69a"
    else:
        col = "#cbd5e1"
    return f'<span class="conc-z" style="color:{col};">Z:{z_val:+.1f}σ</span>'


def _format_conc_card(label: str, info: dict) -> str:
    pct = info.get("pct") if isinstance(info, dict) else None
    net = info.get("net", 0) if isinstance(info, dict) else 0
    z_val = info.get("z_score") if isinstance(info, dict) else None
    z_samples = int(info.get("z_samples", 0)) if isinstance(info, dict) else 0

    if pct is None:
        return (
            f'<div class="conc-box">'
            f'  <div class="conc-box-left"><span class="conc-period">{label}</span>'
            f'  <span class="conc-val" style="color:#94a3b8;">待更新</span></div>'
            f'  <span class="conc-badge" style="background:#262e40; color:#94a3b8;">請點左側更新</span>'
            f'</div>'
        )

    is_z_bull = (z_val is not None and z_val >= STRATEGY_PARAMS["z_bull_min"])
    is_z_bear = (z_val is not None and z_val <= STRATEGY_PARAMS["z_bear_max"])

    if pct >= 10.0 or (pct > 0 and is_z_bull):
        color, badge_bg, badge_txt = "#ef5350", "rgba(239,83,80,0.18)", "🔥 顯著集中"
    elif pct >= 3.0:
        color, badge_bg, badge_txt = "#ef5350", "rgba(239,83,80,0.14)", "📈 偏多吸籌"
    elif pct <= -10.0 or (pct < 0 and is_z_bear):
        color, badge_bg, badge_txt = "#26a69a", "rgba(38,166,154,0.18)", "⚠️ 顯著發散"
    elif pct <= -3.0:
        color, badge_bg, badge_txt = "#26a69a", "rgba(38,166,154,0.14)", "📉 籌碼微散"
    else:
        color, badge_bg, badge_txt = "#cbd5e1", "rgba(148,163,184,0.14)", "⚪ 籌碼中性"

    return (
        f'<div class="conc-box">'
        f'  <div class="conc-box-left">'
        f'    <span class="conc-period">{label}（淨額 {net:+,} 張）</span>'
        f'    <div class="conc-val-row">'
        f'      <span class="conc-val" style="color:{color};">{pct:+.2f}%</span>'
        f'      {_span_zscore(z_val, z_samples)}'
        f'    </div>'
        f'  </div>'
        f'  <span class="conc-badge" style="background:{badge_bg}; color:{color};">{badge_txt}</span>'
        f'</div>'
    )


def _aux_badge(text: str, level: str) -> str:
    style_map = {
        "bull": ("#fca5a5", "rgba(239,83,80,0.16)"),
        "bear": ("#80cbc4", "rgba(38,166,154,0.16)"),
        "warn": ("#fcd34d", "rgba(245,158,11,0.16)"),
        "info": ("#94a3b8", "#232a3b"),
    }
    col, bg = style_map.get(level, style_map["info"])
    return f'<span class="aux-badge" style="color:{col}; background:{bg};">{text}</span>'


def _render_aux_dashboard(
    broker_diff_info: dict | None,
    margin_info: dict | None,
    sbl_info: dict | None,
    tdcc_info: dict | None,
) -> str:
    # 1. 買賣家數差
    diff_tip = "計算公式：買超分點家數 － 賣超分點家數。負值（紅）代表籌碼由散戶流向少數主力；正值（綠）代表籌碼流向散戶、趨於發散。"
    if broker_diff_info:
        d5 = broker_diff_info["diff_5d"]
        d1 = broker_diff_info["diff_1d"]
        neg_s = broker_diff_info["neg_streak"]
        b_html = _aux_badge("🧲 籌碼集中", "bull") if d5 < 0 else (_aux_badge("📉 籌碼渙散", "bear") if d5 > 0 else _aux_badge("⚪ 持平", "info"))
        diff_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head">'
            f'    <span class="aux-title">🧲 買賣家數差<span class="tip-wrap tip-down">ⓘ<span class="tip-box">{diff_tip}</span></span></span>'
            f'    {b_html}'
            f'  </div>'
            f'  <div class="aux-main">5日 {_span_num(d5, " 家", invert_color=True)}</div>'
            f'  <div class="aux-sub">今日 {_span_num(d1, " 家", invert_color=True)} ｜ 連負 <b>{neg_s}</b> 日</div>'
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

    # 2. 融資融券與券資比
    margin_tip = "融資減少（紅）代表散戶槓桿浮額清洗、籌碼沉澱；融資激增（橘）代表散戶追價過熱。券資比 ＝ 融券餘額 ÷ 融資餘額（＞25% 偏集中、＞35% 具軋空潛力）。"
    if margin_info:
        m_bal = margin_info["latest_bal"]
        m_5d = margin_info["chg_5d"]
        m_5d_pct = margin_info["chg_5d_pct"]
        sm_rat = margin_info["short_margin_ratio"]
        s_bal = margin_info["short_bal"]
        if m_5d_pct <= STRATEGY_PARAMS["margin_clean_5d_pct"] and m_5d < 0:
            m_badge = _aux_badge("🧹 融資清洗", "bull")
        elif m_5d_pct >= STRATEGY_PARAMS["margin_surge_5d_pct"] and m_5d > 0:
            m_badge = _aux_badge("⚠️ 融資激增", "warn")
        else:
            m_badge = _aux_badge("⚪ 信用中性", "info")
        margin_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head">'
            f'    <span class="aux-title">🧹 融資浮額／券資比<span class="tip-wrap tip-down">ⓘ<span class="tip-box">{margin_tip}</span></span></span>'
            f'    {m_badge}'
            f'  </div>'
            f'  <div class="aux-main">5日 {_span_num(m_5d, " 張", invert_color=True)} ({_span_float(m_5d_pct, "%", invert_color=True)})</div>'
            f'  <div class="aux-sub">餘額 <b>{m_bal:,}</b> 張 ｜ 融券 <b>{s_bal:,}</b> 張 ｜ 券資比 <b style="color:#fbbf24;">{sm_rat:.1f}%</b></div>'
            f'</div>'
        )
    else:
        margin_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head"><span class="aux-title">🧹 融資浮額／券資比<span class="tip-wrap tip-down">ⓘ<span class="tip-box">{margin_tip}</span></span></span>{_aux_badge("待更新", "info")}</div>'
            f'  <div class="aux-main" style="color:#94a3b8;">尚無資料</div>'
            f'  <div class="aux-sub">點擊左側「🔄」補抓信用交易</div>'
            f'</div>'
        )

    # 3. 借券賣出餘額
    sbl_tip = "外資與機構法人的真實放空水位。餘額減少（紅）代表空方回補、減輕賣壓並提供軋空助力；餘額大增（綠）代表空單加壓。"
    if sbl_info:
        sbl_bal = sbl_info["latest_bal"]
        sbl_5d = sbl_info["chg_5d"]
        sbl_pct = sbl_info["chg_5d_pct"]
        sbl_1d = sbl_info["chg_1d"]
        if sbl_pct <= STRATEGY_PARAMS["sbl_cover_5d_pct"] and sbl_5d < 0:
            sbl_badge = _aux_badge("🚀 借券回補", "bull")
        elif sbl_pct >= STRATEGY_PARAMS["sbl_surge_5d_pct"] and sbl_5d > 0:
            sbl_badge = _aux_badge("🐻 空單加壓", "bear")
        else:
            sbl_badge = _aux_badge("⚪ 空單持平", "info")
        sbl_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head">'
            f'    <span class="aux-title">🐻 借券賣出餘額<span class="tip-wrap tip-down">ⓘ<span class="tip-box">{sbl_tip}</span></span></span>'
            f'    {sbl_badge}'
            f'  </div>'
            f'  <div class="aux-main">5日 {_span_num(sbl_5d, " 張", invert_color=True)} ({_span_float(sbl_pct, "%", invert_color=True)})</div>'
            f'  <div class="aux-sub">餘額 <b>{sbl_bal:,}</b> 張 ｜ 今日 {_span_num(sbl_1d, " 張", invert_color=True)}</div>'
            f'</div>'
        )
    else:
        sbl_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head"><span class="aux-title">🐻 借券賣出餘額<span class="tip-wrap tip-down">ⓘ<span class="tip-box">{sbl_tip}</span></span></span>{_aux_badge("待更新", "info")}</div>'
            f'  <div class="aux-main" style="color:#94a3b8;">尚無資料</div>'
            f'  <div class="aux-sub">點擊左側「🔄」補抓借券資料</div>'
            f'</div>'
        )

    # 4. TDCC 集保戶股權分散表
    tdcc_tip = "集保中心每週五公布之歸戶股權分散表，可破解主力拆單藏分點與匯撥。千張大戶比率上升且總股東人數減少為標準籌碼集中訊號。"
    if tdcc_info:
        w1000 = tdcc_info["whale_1000_pct"]
        w1000_chg = tdcc_info.get("whale_1000_chg_1w")
        w400 = tdcc_info["whale_400_pct"]
        holders = tdcc_info["total_holders"]
        h_chg = tdcc_info.get("holders_chg_1w")
        t_dt = tdcc_info.get("date", "")
        chg_txt = _span_float(w1000_chg, "%") if w1000_chg is not None else '<span style="color:#94a3b8;">首週</span>'
        h_chg_txt = _span_num(h_chg, " 人", invert_color=True) if h_chg is not None else "—"
        if w1000_chg is not None and w1000_chg >= 0.2:
            t_badge = _aux_badge("🐋 大戶增持", "bull")
        elif w1000_chg is not None and w1000_chg <= -0.2:
            t_badge = _aux_badge("⚠️ 大戶流出", "bear")
        else:
            t_badge = _aux_badge(t_dt or "每週更新", "info")
        tdcc_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head">'
            f'    <span class="aux-title">🐋 TDCC 集保大戶<span class="tip-wrap tip-down-left">ⓘ<span class="tip-box">{tdcc_tip}</span></span></span>'
            f'    {t_badge}'
            f'  </div>'
            f'  <div class="aux-main">千張 <b>{w1000:.2f}%</b>（週 {chg_txt}）</div>'
            f'  <div class="aux-sub">400張 <b>{w400:.2f}%</b> ｜ 股東 <b>{holders:,}</b> 人（{h_chg_txt}）</div>'
            f'</div>'
        )
    else:
        tdcc_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head"><span class="aux-title">🐋 TDCC 集保大戶<span class="tip-wrap tip-down-left">ⓘ<span class="tip-box">{tdcc_tip}</span></span></span>{_aux_badge("每週更新", "info")}</div>'
            f'  <div class="aux-main" style="color:#94a3b8;">尚無資料</div>'
            f'  <div class="aux-sub">點擊左側「🔄」同步集保歸戶</div>'
            f'</div>'
        )

    return f'<div class="aux-grid">{diff_card}{margin_card}{sbl_card}{tdcc_card}</div>'


def _render_rank_table(
    title: str,
    data_dict: dict,
    val_color: str,
    empty_msg: str,
    broker_tags: dict | None = None,
) -> str:
    b_tags = broker_tags or {}
    rows = []
    for i, (k, v) in enumerate(data_dict.items()):
        tags = b_tags.get(k, [])
        short_tag = ""
        if any("隔日沖" in t for t in tags):
            short_tag += ' <span title="高頻隔日沖分點" style="color:#fbbf24;">⚡</span>'
        if any("贏家" in t for t in tags):
            short_tag += ' <span title="歷史高勝率贏家分點" style="color:#f59e0b;">🏆</span>'
        rows.append(
            f'<tr>'
            f'  <td style="width:24px; color:#94a3b8;">#{i+1}</td>'
            f'  <td><b>{k}</b>{short_tag}</td>'
            f'  <td style="text-align:right; color:{val_color}; font-weight:700;">{v:+,} 張</td>'
            f'</tr>'
        )
    rows_html = "".join(rows) or f'<tr><td colspan="3" style="color:#94a3b8;">{empty_msg}</td></tr>'

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
        horizontal=True,
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
            '全部更新：近 3 日增量更新所有個股、融資借券與集保<br>'
            '個股更新：全年度重抓校準（含近 60 日新主力與股本）'
            '</div>',
            unsafe_allow_html=True,
        )

        col_all_upd, col_sort_btn = st.columns([1.25, 1])
        with col_all_upd:
            if st.button(
                "🔄 全部更新",
                key="btn_update_all",
                help="對所有已存個股執行近 3 個交易日增量更新，同步更新集中度、融資券、借券與集保",
                width="stretch",
                disabled=st.session_state["sort_mode"],
            ):
                prog = st.progress(0, text="準備批量增量更新...")
                for idx, sid in enumerate(saved_list):
                    lbl = get_stock_display_label(sid)
                    prog.progress(idx / len(saved_list), text=f"正在增量更新 {lbl} ({idx+1}/{len(saved_list)})...")
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
                        help=f"全年度重抓校準 {display_name}（補齊 VWAP、近 60 日主力、融資借券與集保）",
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
    aux_path = os.path.join(DB_DIR, f"{target_stock}_aux.csv")
    meta_path = os.path.join(DB_DIR, f"{target_stock}_meta.json")

    if (btn_add and input_stock) or not os.path.exists(price_path):
        with st.spinner(f"正在抓取 {target_stock} 股價、投信、波段分點、融資借券與集保大戶資料..."):
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
    df_aux_full = None
    if os.path.exists(aux_path):
        try:
            df_aux_full = pd.read_csv(aux_path)
        except Exception:
            df_aux_full = None

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
    pct_change = ((latest_close - base_close) / base_close * 100.0) if base_close else 0.0
    if pct_change > 0:
        pct_html = f'<span style="color:#ef5350; font-weight:700;">▲ {pct_change:+.2f}%</span>'
    elif pct_change < 0:
        pct_html = f'<span style="color:#26a69a; font-weight:700;">▼ {pct_change:+.2f}%</span>'
    else:
        pct_html = '<span style="color:#cbd5e1; font-weight:700;">0.00%</span>'

    concentrations_saved = meta_saved.get("concentrations", {})
    tdcc_history_saved = meta_saved.get("tdcc_history", [])
    issued_lots_saved = float(meta_saved.get("issued_lots", 0.0) or 0.0)

    analysis = analyze_main_force(
        df_price,
        df_chips,
        period_label,
        df_chips_full=df_chips_full,
        df_price_full=df_price_full,
        concentrations=concentrations_saved,
        df_aux_full=df_aux_full,
        tdcc_history=tdcc_history_saved,
        issued_lots=issued_lots_saved,
    )
    broker_count = len([c for c in df_chips_full.columns if c not in ("date", "投信")])
    concentrations = analysis.get("concentrations") or concentrations_saved
    scenario = analysis.get("scenario", {})
    broker_tags = analysis.get("broker_tags", {})

    if broker_count == 0 and meta_saved.get("debug_notes"):
        st.warning(f"⚠️ 分點明細未取得（{'；'.join(meta_saved['debug_notes'])}）")

    # --- 標題列 ---
    updated_str = meta_saved.get("last_updated", "")
    latest_trade_date = df_price_full["date"].max()
    time_badge = f"🕒 更新時間：{updated_str}（最新交易日：{latest_trade_date}）" if updated_str else f"🕒 最新交易日：{latest_trade_date}"

    st.markdown(
        f'<div style="display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:8px;">'
        f'  <h4 style="margin:0;">🎯 {stock_title} 主力診斷結論（{period_label}｜追蹤 {broker_count} 分點）</h4>'
        f'  <span style="font-size:0.84rem; color:#94a3b8;">{time_badge}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if meta_saved.get("github_sync"):
        st.caption(f"雲端資料庫狀態：{meta_saved['github_sync']}")

    # =====================================================================
    # 區塊一：核心決策總結看板（結論置頂：分數 ＋ 劇本 ＋ 操作應對 ＋ 共振徽章）
    # =====================================================================
    conv_score = int(scenario.get("conviction_score", 50))
    if conv_score >= 70:
        score_col, score_lbl = "#ef5350", "偏多強勢共振"
    elif conv_score >= 55:
        score_col, score_lbl = "#f59e0b", "略偏多觀察"
    elif conv_score <= 35:
        score_col, score_lbl = "#26a69a", "偏空／出貨風險"
    else:
        score_col, score_lbl = "#94a3b8", "中性多空拉鋸"

    score_tip_html = (
        "<b>綜合籌碼健康分數（0～100 分）</b><br>"
        "以五大劇本為基準分，結合近 5 日投信、家數差、融資、借券、集保週變動、隔日沖與均線／深套趨勢加減計算：<br>"
        '<table class="tip-mini-tbl">'
        '  <thead><tr><th>分數區間</th><th>燈號狀態</th><th>實戰好壞與含義</th></tr></thead>'
        '  <tbody>'
        '    <tr><td style="white-space:nowrap;"><b>70 ～ 100 分</b></td><td style="color:#ef5350; font-weight:700; white-space:nowrap;">偏多強勢共振（紅）</td><td><b>極佳。</b>多頭劇本成立且有多項輔助籌碼同步驗證，屬高勝率結構。</td></tr>'
        '    <tr><td style="white-space:nowrap;"><b>55 ～ 69 分</b></td><td style="color:#f59e0b; font-weight:700; white-space:nowrap;">略偏多觀察（橘）</td><td><b>中上。</b>有偏多訊號或試單條件，但共振尚未齊全（或混有短線雜訊）。</td></tr>'
        '    <tr><td style="white-space:nowrap;"><b>36 ～ 54 分</b></td><td style="color:#94a3b8; font-weight:700; white-space:nowrap;">中性多空拉鋸（灰）</td><td><b>普通／觀望。</b>多空籌碼互相抵銷，方向不明確。</td></tr>'
        '    <tr><td style="white-space:nowrap;"><b>0 ～ 35 分</b></td><td style="color:#26a69a; font-weight:700; white-space:nowrap;">偏空／出貨風險（綠）</td><td><b>差／警戒。</b>觸發空方劇本或籌碼嚴重發散，應避開或逢彈減碼。</td></tr>'
        '  </tbody>'
        '</table>'
    )

    sc_type = scenario.get("type", "neutral")
    hero_cls = "hero-bull" if sc_type == "bull" else ("hero-bear" if sc_type == "bear" else "hero-neutral")
    sc_action_html = f'<div class="hero-body-action">💡 操作應對：{scenario["action"]}</div>' if scenario.get("action") else ""

    badges_html = ""
    sc_badges = scenario.get("badges") or []
    if sc_badges:
        pill_list = []
        for b in sc_badges:
            lvl = b.get("level", "info")
            cls_map = {"bull": "res-bull", "bear": "res-bear", "warn": "res-warn", "info": "res-info"}
            b_cls = cls_map.get(lvl, "res-info")
            detail_esc = str(b.get("detail", "")).replace('"', "&quot;")
            pill_list.append(
                f'<span class="res-badge {b_cls}" title="{detail_esc}">{b.get("label", "")}</span>'
            )
        badges_html = f'<div class="badge-row">{"".join(pill_list)}</div>'

    st.markdown(
        f'<div class="hero-panel {hero_cls}">'
        f'  <div class="hero-score-box">'
        f'    <div class="hero-score-label">'
        f'      <span>綜合籌碼健康分數</span>'
        f'      <span class="tip-wrap tip-down">ⓘ<span class="tip-box tip-box-wide">{score_tip_html}</span></span>'
        f'    </div>'
        f'    <div class="score-val" style="color:{score_col};">{conv_score} <span style="font-size:0.88rem; font-weight:500; color:#94a3b8;">/ 100</span></div>'
        f'    <div class="score-bar-bg"><div class="score-bar-fill" style="width:{conv_score}%; background:{score_col};"></div></div>'
        f'    <div class="hero-score-sub" style="color:{score_col};">{score_lbl}</div>'
        f'  </div>'
        f'  <div>'
        f'    <div class="hero-body-title">{scenario.get("title", "")}</div>'
        f'    <div class="hero-body-desc">{scenario.get("desc", "")}</div>'
        f'    {sc_action_html}'
        f'    {badges_html}'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # --- 區塊一之二：精簡單行價量籌碼背離警示條（不重複長篇警語，細節收進 ⓘ） ---
    div_alert = analysis.get("divergence_alert")
    if div_alert:
        alert_type = div_alert.get("type", "bull")
        strip_cls = (
            "div-strip-bull" if alert_type == "bull"
            else ("div-strip-warn" if alert_type == "warn" else "div-strip-bear")
        )
        # 擷取核心數據句（第一行）避免與上方劇本操作建議重複
        raw_desc_lines = str(div_alert.get("desc", "")).split("<br>")
        compact_desc = raw_desc_lines[0] if raw_desc_lines else ""
        full_tip_desc = str(div_alert.get("desc", ""))
        st.markdown(
            f'<div class="div-strip {strip_cls}">'
            f'  <div><b>{div_alert["title"]}</b> ｜ {compact_desc}</div>'
            f'  <span class="tip-wrap tip-left">ⓘ 詳情<span class="tip-box tip-box-wide">{full_tip_desc}</span></span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # =====================================================================
    # 區塊一之三：三大核心卡片（最大分點主力 ／ 三大波段主力狀態 ／ 最新收盤價）
    # =====================================================================
    ctrl5 = float(analysis.get("top3_5d_ctrl_pct", 0.0) or 0.0)
    z5 = analysis.get("top3_5d_ctrl_z")
    z_tip_html = build_z_tooltip_html(stock_title, ctrl5, z5)

    # 消除視覺訊號打架：大格局偏空或 Z 值屬日常波動時，將「吃貨中」柔化為黃色提示
    raw_status_html = analysis.get("status_html", "N/A")
    top3_period_net = int(analysis.get("top3_period_net", 0))
    if "吃貨中" in raw_status_html:
        if conv_score <= 35:
            display_status_html = f'🟡 區間逆勢承接（{_span_num(top3_period_net)}｜格局偏空）'
        elif z5 is not None and abs(z5) < 1.0:
            display_status_html = f'🟡 區間小幅買超（{_span_num(top3_period_net)}｜屬日常波動）'
        else:
            display_status_html = raw_status_html
    else:
        display_status_html = raw_status_html

    c1, c2, c3 = st.columns([2.3, 2.2, 1.2])
    with c1:
        sub_html = "".join([
            f'<div class="card-sub">{line}</div>'
            for line in analysis.get("main_force_sub_lines", [])
        ])
        st.markdown(f"""
        <div class="summary-card">
            <div class="card-label">近 60 日最大波段分點主力（已濾除隔日沖）</div>
            <div class="card-value">{analysis.get("main_force_html", "N/A")}</div>
            {sub_html}
        </div>
        """, unsafe_allow_html=True)
    with c2:
        flt5 = analysis.get("top3_5d_float_pct")
        extra_stat_parts = [f"近5日控盤 {ctrl5:+.1f}%"]
        if z5 is not None:
            extra_stat_parts.append(f"Z:{z5:+.1f}σ")
        if flt5 is not None:
            extra_stat_parts.append(f"佔股本 {flt5:+.2f}%")
        status_sub_html = (
            f'<div class="card-sub">{analysis.get("status_sub", "")}<br>'
            f'<span style="color:#cbd5e1;">{" ｜ ".join(extra_stat_parts)}</span></div>'
        )
        st.markdown(f"""
        <div class="summary-card">
            <div class="card-label">
                <span>區間內三大波段主力狀態</span>
                <span class="tip-wrap tip-down">ⓘ<span class="tip-box tip-box-wide">{z_tip_html}</span></span>
            </div>
            <div class="card-value">{display_status_html}</div>
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

    # --- 區塊一之四：投信獨立追蹤列（連買賣與近 5 日放大突出） ---
    trust_info = analysis.get("trust_info")
    if trust_info:
        trust_tip = (
            "投信下單分散於各大本土券商法人專戶中，系統不將其與分點張數重複加總，"
            "但已正式將投信連買／連賣與持股比率（佔股本 %）納入五大劇本的共振評分層。"
            "動向標籤嚴格以買超紅、賣超綠呈現；持股水位 0.5%～5% 且偏買時為初升認養甜蜜期，10% 以上須留意結帳賣壓。"
        )
        phase = trust_info.get("phase") or {}
        tier_lvl = phase.get("tier_level", "info")
        mom_lvl = phase.get("momentum_level", "info")

        tier_style_map = {
            "bull": ("#fca5a5", "rgba(239,83,80,0.14)", "rgba(239,83,80,0.45)"),
            "warn": ("#fcd34d", "rgba(245,158,11,0.15)", "rgba(245,158,11,0.45)"),
            "info": ("#cbd5e1", "#232a3b", "#334155"),
        }
        mom_style_map = {
            "bull": ("#ef5350", "rgba(239,83,80,0.16)", "rgba(239,83,80,0.55)"),
            "bear": ("#26a69a", "rgba(38,166,154,0.16)", "rgba(38,166,154,0.55)"),
            "info": ("#94a3b8", "#232a3b", "#334155"),
        }
        t_col, t_bg, t_bd = tier_style_map.get(tier_lvl, tier_style_map["info"])
        m_col, m_bg, m_bd = mom_style_map.get(mom_lvl, mom_style_map["info"])

        tier_pill = (
            f'<span class="trust-tag" style="color:{t_col}; background:{t_bg}; border:1px solid {t_bd};">'
            f'{phase.get("tier_label", "")}</span>'
            if phase.get("tier_label") else ""
        )
        mom_pill = (
            f'<span class="trust-tag" style="color:{m_col}; background:{m_bg}; border:1px solid {m_bd};">'
            f'{phase.get("momentum", "")}</span>'
            if phase.get("momentum") else ""
        )

        cost_seg = ""
        if trust_info.get("cost", 0) > 0 and trust_info.get("pnl_pct") is not None:
            p_val = trust_info["pnl_pct"]
            p_col = "#ef5350" if p_val > 0 else ("#26a69a" if p_val < 0 else "#cbd5e1")
            cost_seg = (
                f'<span><span class="trust-lbl">60日均價</span>'
                f'<b style="color:#ffb74d;">{trust_info["cost"]:,.2f}</b> '
                f'(<span style="color:{p_col}; font-weight:700;">{p_val:+.1f}%</span>)</span>'
            )

        st.markdown(
            f'<div class="trust-bar">'
            f'  <div class="trust-title">'
            f'    🏦 投信獨立追蹤'
            f'    <span class="tip-wrap tip-right">ⓘ<span class="tip-box">{trust_tip}</span></span>'
            f'    {tier_pill}{mom_pill}'
            f'  </div>'
            f'  <div class="trust-metrics-wrap">'
            f'    <div class="trust-primary">'
            f'      <span>{_span_streak(trust_info["streak"])}</span>'
            f'      <span style="color:#475569;">｜</span>'
            f'      <span><span class="trust-lbl">近 5 日</span>{_span_num(trust_info.get("d5", 0))}</span>'
            f'    </div>'
            f'    <div class="trust-secondary">'
            f'      <span><span class="trust-lbl">今日</span>{_span_num(trust_info["d1"])}</span>'
            f'      <span><span class="trust-lbl">區間</span>{_span_num(trust_info["period"])}</span>'
            f'      <span><span class="trust-lbl">近 20 日</span>{_span_num(trust_info["d20"])}</span>'
            f'      <span><span class="trust-lbl">今年</span>{_span_num(trust_info["ytd"])}</span>'
            f'      <span><span class="trust-lbl">控盤</span>{_span_ctrl(trust_info["ctrl_pct"])}</span>'
            f'      {cost_seg}'
            f'    </div>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # --- 區塊一之五：四大輔助籌碼儀表板（去雜訊＋狀態標籤） ---
    st.markdown(
        _render_aux_dashboard(
            analysis.get("broker_diff_info"),
            analysis.get("margin_info"),
            analysis.get("sbl_info"),
            analysis.get("tdcc_info"),
        ),
        unsafe_allow_html=True,
    )

    # --- 區塊一之六：全市場前 15 大籌碼集中度橫條 ---
    conc_tip = (
        "計算公式：（該區間全市場前 15 大買超分點總張數 － 前 15 大賣超分點總張數）÷ 區間總成交量 × 100%。"
        "為確保統計口徑完全一致，系統每日自動保存官方快照，累積滿 15 個交易日後即啟用 60 日滾動 Z-Score。"
    )
    st.markdown(
        f'<div class="conc-panel">'
        f'  <div class="conc-header">'
        f'    <div class="conc-title">'
        f'      📊 全市場前 15 大籌碼集中度'
        f'      <span class="tip-wrap tip-right">ⓘ<span class="tip-box">{conc_tip}</span></span>'
        f'      <span class="conc-sub-tag">短中線全市場視角（純官方口徑）</span>'
        f'    </div>'
        f'  </div>'
        f'  <div class="conc-grid">'
        f'    {_format_conc_card("近 5 日集中度", concentrations.get("d5", {}))}'
        f'    {_format_conc_card("近 10 日集中度", concentrations.get("d10", {}))}'
        f'    {_format_conc_card("近 20 日集中度", concentrations.get("d20", {}))}'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # --- 可收合抽屜：價 × 點 × 面 × 共振 交叉判讀矩陣 ---
    matched_id = scenario.get("id", 0)
    with st.expander("📖 價 × 點 × 面 × 共振 交叉判讀矩陣（點擊展開 5 大實戰劇本與操作應對）", expanded=False):
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
                f'  <td style="width:17%;">{item.get("main_cond", "")}</td>'
                f'  <td style="width:20%;">{item.get("conc_cond", "")}</td>'
                f'  <td style="width:28%;">{item.get("action", "")}</td>'
                f'</tr>'
            )
        st.markdown(f"""
        <table class="matrix-table">
            <thead>
                <tr>
                    <th>盤面情境劇本</th>
                    <th>股價與均線（價）</th>
                    <th>60日三大波段主力（點）</th>
                    <th>集中度與共振驗證（面）</th>
                    <th>實戰解讀與操作應對</th>
                </tr>
            </thead>
            <tbody>{"".join(m_rows)}</tbody>
        </table>
        """, unsafe_allow_html=True)

    # --- 前三大波段券商分點主力明細（滾動 60 日，含贏家／週轉率資訊） ---
    st.write("")
    top3_names = analysis.get("top3_names", [])
    if top3_names:
        top3_net_val = analysis.get("top3_period_net", 0)
        top3_ctrl_val = analysis.get("top3_period_ctrl_pct", 0.0)
        group_ctrl_tip = (
            "<b>波段主力定義：</b>採近 60 個交易日淨買超前 3 大純券商分點，並透過週轉係數與隔日反手率自動過濾高頻隔日沖分點。<br><br>"
            "<b>控盤率實戰標準：</b><br>"
            "• <b>絕對值 &lt; 2%：</b>影響力一般，屬正常換手。<br>"
            "• <b>絕對值 2% ～ 5%：</b>態度積極，或 Z-Score 達 ±1.5σ 以上。<br>"
            "• <b>絕對值 &gt; 5%：</b>高度控盤，波段走勢幾乎由這批主力主導。"
        )
        cost_inline = (
            f' <span style="color:#64748b; margin:0 6px;">｜</span>'
            f'<span style="color:#cbd5e1;">合力成本：</span><b style="color:#ffb74d;">{analysis["top3_cost"]:,.2f} 元</b>'
            if analysis.get("top3_cost", 0) > 0 else ""
        )
        st.markdown(
            f'<div class="top3-header-line">'
            f'  <span>• 近 60 日三大波段主力（<b>{", ".join(top3_names)}</b>）本區間合計：{_span_num(top3_net_val)}</span>'
            f'  <span style="color:#64748b; margin:0 6px;">｜</span>'
            f'  <span style="color:#cbd5e1;">區間控盤率：</span>{_span_ctrl(top3_ctrl_val)}'
            f'  {cost_inline}'
            f'  <span class="tip-wrap tip-right">ⓘ<span class="tip-box">{group_ctrl_tip}</span></span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    top3_rows = analysis.get("top3_rows", [])
    if top3_rows:
        ctrl_tip_text = (
            "計算公式：該分點本區間淨買賣超張數 ÷ 本區間總成交量。"
            "滑鼠移到分點名稱可查看該分點的今年累計張數、週轉係數與歷史 20 日前瞻勝率。"
        )
        rows_html = []
        for r in top3_rows:
            tag_html = "".join([f'<span class="mf-tag-pill">{t}</span>' for t in (r.get("tags") or [])[:1]])
            wr_txt = f"20日勝率 {r['win_rate']:.1f}%（均報 {r['avg_fwd_ret']:+.1f}%）" if r.get("win_rate") is not None else "勝率樣本累積中"
            name_title = f"今年累計 {r['ytd']:+,} 張｜60日週轉係數 {r.get('churn_ratio', 0):.1f}x｜{wr_txt}"
            rows_html.append(
                f'<div class="mf-row">'
                f'  <div class="mf-name" title="{name_title}">◦ {r["name"]} {tag_html}</div>'
                f'  <div class="mf-cell"><span class="mf-lbl">近 60 日</span>{_span_num(r["net_60d"])}</div>'
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

    # --- 聰明錢雷達：🏆 高勝率贏家分點 ＋ 🚀 近 10 日新崛起波段分點 ---
    winner_brokers = analysis.get("winner_brokers") or []
    recent_10d_rising = analysis.get("recent_10d_rising") or {}
    if winner_brokers or recent_10d_rising:
        w_rows = "".join([
            f'<tr>'
            f'  <td><b>🏆 {w["name"]}</b></td>'
            f'  <td style="text-align:right; color:#fbbf24; font-weight:700;">{w["win_rate"]:.0f}% ({w["buy_signals"]}次)</td>'
            f'  <td style="text-align:right; color:#ef5350;">{w["avg_fwd_ret"]:+.1f}%</td>'
            f'  <td style="text-align:right;">{_span_num(w["net_5d"], "")}</td>'
            f'  <td style="text-align:right;">{_span_num(w["net_60d"], "")}</td>'
            f'</tr>'
            for w in winner_brokers
        ]) or '<tr><td colspan="5" style="color:#94a3b8;">目前歷史樣本尚無同時滿足 20 日勝率 ≥ 60% 且均報 ≥ +3% 之分點</td></tr>'

        r10_rows = "".join([
            f'<tr>'
            f'  <td style="width:24px; color:#94a3b8;">#{i+1}</td>'
            f'  <td><b>{k}</b>'
            f'{" 🏆" if any("贏家" in t for t in broker_tags.get(k, [])) else ""}</td>'
            f'  <td style="text-align:right; color:#ef5350; font-weight:700;">{v:+,} 張</td>'
            f'</tr>'
            for i, (k, v) in enumerate(recent_10d_rising.items())
        ]) or '<tr><td colspan="3" style="color:#94a3b8;">近 10 日無明顯波段新進買超分點</td></tr>'

        st.markdown(
            f'<div class="smart-grid">'
            f'  <div class="smart-panel">'
            f'    <div class="smart-title"><span>🏆 歷史高勝率「贏家分點」雷達（買後 20 日回測）</span><span style="font-size:0.74rem; color:#94a3b8;">已排除隔日沖</span></div>'
            f'    <table class="rank-table">'
            f'      <thead><tr><th>分點名稱</th><th style="text-align:right;">20日勝率</th><th style="text-align:right;">均報酬</th><th style="text-align:right;">近5日(張)</th><th style="text-align:right;">近60日(張)</th></tr></thead>'
            f'      <tbody>{w_rows}</tbody>'
            f'    </table>'
            f'  </div>'
            f'  <div class="smart-panel">'
            f'    <div class="smart-title"><span>🚀 近 10 日新崛起波段分點（換手接棒追蹤）</span><span style="font-size:0.74rem; color:#94a3b8;">已排除隔日沖</span></div>'
            f'    <table class="rank-table">'
            f'      <thead><tr><th colspan="3">近 10 日淨買超前 5 大波段分點</th></tr></thead>'
            f'      <tbody>{r10_rows}</tbody>'
            f'    </table>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # --- 四欄並排排行榜（支援切換近 60 日波段 vs 今年累計，並標註 🏆／⚡） ---
    top_buyers_60d = analysis.get("top_buyers", {})
    top_sellers_60d = analysis.get("top_sellers", {})
    ytd_buyers = analysis.get("ytd_top_buyers", {})
    ytd_sellers = analysis.get("ytd_top_sellers", {})
    today_buyers = analysis.get("today_buyers", {})
    today_sellers = analysis.get("today_sellers", {})

    if top_buyers_60d or top_sellers_60d or today_buyers or today_sellers:
        st.write("")
        col_rank_hdr, col_rank_mode = st.columns([3.5, 1.5])
        with col_rank_hdr:
            st.caption("分點排行榜（🏆＝歷史贏家分點｜⚡＝高頻隔日沖熱點）")
        with col_rank_mode:
            rank_mode = st.radio(
                "右側兩欄口徑",
                options=["近 60 日波段", "今年累計"],
                index=0,
                horizontal=True,
                label_visibility="collapsed",
            )

        right_buyers = top_buyers_60d if rank_mode == "近 60 日波段" else ytd_buyers
        right_sellers = top_sellers_60d if rank_mode == "近 60 日波段" else ytd_sellers
        right_lbl = "近 60 日" if rank_mode == "近 60 日波段" else "今年"

        short_dt = latest_trade_date[5:] if len(latest_trade_date) >= 10 else latest_trade_date
        col_tb, col_ts, col_yb, col_ys = st.columns(4)
        with col_tb:
            st.markdown(
                _render_rank_table(f"🔴 今日分點買超前 5 大（{short_dt}）", today_buyers, "#ef5350", "今日無淨買超分點", broker_tags),
                unsafe_allow_html=True,
            )
        with col_ts:
            st.markdown(
                _render_rank_table(f"🟢 今日分點賣超前 5 大（{short_dt}）", today_sellers, "#26a69a", "今日無淨賣超分點", broker_tags),
                unsafe_allow_html=True,
            )
        with col_yb:
            st.markdown(
                _render_rank_table(f"🔴 {right_lbl}分點買超前 5 大", right_buyers, "#ef5350", f"{right_lbl}無淨買超分點", broker_tags),
                unsafe_allow_html=True,
            )
        with col_ys:
            st.markdown(
                _render_rank_table(f"🟢 {right_lbl}分點賣超前 5 大", right_sellers, "#26a69a", f"{right_lbl}無淨賣超分點", broker_tags),
                unsafe_allow_html=True,
            )

    st.divider()

    # --- 區塊二：股價、10/20MA、VWAP 主力成本線 vs. 分點買賣超 ＋ 融資／借券副圖 ---
    chart_title_suffix = f"（標示區間：{period_label}）" if range_type != "全部" else f"（{period_label}）"
    st.markdown(f"#### 📉 {stock_title} 股價、10/20MA 與主力 VWAP 成本線 vs. 籌碼流向{chart_title_suffix}")

    available_brokers = [c for c in df_chips_full.columns if c != "date"]
    default_selected = [n for n in top3_names if n in available_brokers] or available_brokers[:1]
    winner_names = [w["name"] for w in winner_brokers if w["name"] in available_brokers]

    ms_key = f"ms_{target_stock}"
    if ms_key not in st.session_state:
        st.session_state[ms_key] = default_selected
    else:
        cleaned_sel = [b for b in st.session_state[ms_key] if b in available_brokers]
        if cleaned_sel != st.session_state[ms_key]:
            st.session_state[ms_key] = cleaned_sel or default_selected

    col_sel, col_btn1, col_btn2 = st.columns([3.8, 1.15, 1.15])
    with col_btn1:
        st.write("")
        st.write("")
        if st.button("⚡ 三大波段主力", width="stretch", disabled=not bool(top3_names)):
            st.session_state[ms_key] = [n for n in top3_names if n in available_brokers]
            st.rerun()
    with col_btn2:
        st.write("")
        st.write("")
        if st.button("🏆 帶入贏家分點", width="stretch", disabled=not bool(winner_names)):
            st.session_state[ms_key] = winner_names[:3]
            st.rerun()
    with col_sel:
        selected_brokers = st.multiselect(
            "選擇要觀察的券商分點或「投信」（預設為近 60 日三大波段主力合力；觀察投信建議單獨勾選）：",
            options=available_brokers,
            key=ms_key,
        )

    c_opt1, c_opt2, c_opt3 = st.columns([1.1, 1.4, 2.0])
    with c_opt1:
        show_ma = st.checkbox("顯示 10/20MA", value=True)
    with c_opt2:
        has_aux_series = (
            df_aux_full is not None
            and (
                ("margin_bal" in df_aux_full.columns and df_aux_full["margin_bal"].notna().any())
                or ("sbl_bal" in df_aux_full.columns and df_aux_full["sbl_bal"].notna().any())
            )
        )
        show_aux_chart = st.checkbox(
            "顯示融資／借券副圖",
            value=bool(has_aux_series),
            disabled=not bool(has_aux_series),
        )
    with c_opt3:
        cost_basis = st.radio(
            "成本線計算基準",
            options=["近 60 日波段成本", "今年累計成本"],
            index=0,
            horizontal=True,
            label_visibility="collapsed",
        )

    if not selected_brokers:
        st.warning("請至少選擇一個券商分點或「投信」來繪製圖表。")
    else:
        price_cols = [c for c in ["date", "open", "high", "low", "close", "vwap"] if c in df_price_full.columns]
        df_plot = df_price_full[price_cols].merge(df_chips_full, on="date", how="left").fillna(0.0)
        if df_aux_full is not None and not df_aux_full.empty:
            aux_cols = [c for c in ["date", "margin_bal", "sbl_bal"] if c in df_aux_full.columns]
            if len(aux_cols) > 1:
                df_plot = df_plot.merge(df_aux_full[aux_cols], on="date", how="left")

        df_plot["ma10"] = df_plot["close"].rolling(10).mean()
        df_plot["ma20"] = df_plot["close"].rolling(20).mean()

        daily_net = df_plot[selected_brokers].sum(axis=1)
        cum_total = daily_net.cumsum()
        win_days = STRATEGY_PARAMS["wave_lookback_days"] if cost_basis == "近 60 日波段成本" else None
        cost_series, latest_cost, latest_inv = calculate_main_force_cost(df_plot, daily_net, window_days=win_days)

        target_label = selected_brokers[0] if len(selected_brokers) == 1 else f"所選 {len(selected_brokers)} 個分點合力"
        latest_full_close = float(df_plot.iloc[-1]["close"])
        cost_mode_tag = "近 60 日 VWAP 成本" if win_days else "今年累計 VWAP 成本"

        if latest_cost > 0 and latest_inv > 0:
            sel_pnl = (latest_full_close - latest_cost) / latest_cost * 100.0
            pnl_color = "#ef5350" if sel_pnl > 0 else ("#26a69a" if sel_pnl < 0 else "#cbd5e1")
            st.markdown(
                f'<div class="cost-banner">💰 <b>{target_label}</b> {cost_mode_tag}：'
                f'<b style="color:#ffb74d;">{latest_cost:,.2f} 元</b> ｜ '
                f'區間剩餘庫存：<b>{int(round(latest_inv)):,} 張</b> ｜ '
                f'對照最新收盤價 ({latest_full_close:.2f} 元) 帳面損益：'
                f'<b style="color:{pnl_color};">{sel_pnl:+.2f}%</b></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div class="cost-banner">💰 <b>{target_label}</b> 在「{cost_basis}」基準下剩餘庫存已歸零（無持有庫存）</div>',
                unsafe_allow_html=True,
            )

        use_3rows = bool(show_aux_chart and has_aux_series)
        if use_3rows:
            fig = make_subplots(
                rows=3,
                cols=1,
                shared_xaxes=True,
                vertical_spacing=0.05,
                row_heights=[0.44, 0.34, 0.22],
                specs=[[{"secondary_y": False}], [{"secondary_y": True}], [{"secondary_y": True}]],
            )
            chart_height = 760
        else:
            fig = make_subplots(
                rows=2,
                cols=1,
                shared_xaxes=True,
                vertical_spacing=0.06,
                row_heights=[0.52, 0.48],
                specs=[[{"secondary_y": False}], [{"secondary_y": True}]],
            )
            chart_height = 640

        # 上層：10MA 與 20MA 均線
        if show_ma:
            fig.add_trace(
                go.Scatter(
                    x=df_plot["date"],
                    y=df_plot["ma10"],
                    name="10MA",
                    line=dict(color="rgba(56, 189, 248, 0.78)", width=1.3),
                    hovertemplate="%{y:,.2f} 元<extra>10MA</extra>",
                ),
                row=1, col=1, secondary_y=False,
            )
            fig.add_trace(
                go.Scatter(
                    x=df_plot["date"],
                    y=df_plot["ma20"],
                    name="20MA",
                    line=dict(color="rgba(192, 132, 252, 0.78)", width=1.3),
                    hovertemplate="%{y:,.2f} 元<extra>20MA</extra>",
                ),
                row=1, col=1, secondary_y=False,
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
                    hovertemplate="%{y:,.2f} 元<extra>主力成本均價</extra>",
                ),
                row=1, col=1, secondary_y=False,
            )

        # 上層：收盤價折線（金黃色粗線置頂）
        fig.add_trace(
            go.Scatter(
                x=df_plot["date"],
                y=df_plot["close"],
                name="收盤價 (元)",
                line=dict(color="#FFD700", width=2.6),
                hovertemplate="%{y:,.2f} 元<extra>收盤價</extra>",
            ),
            row=1, col=1, secondary_y=False,
        )

        # 中層主軸：每日買賣超長條圖
        bar_colors = ["#ef5350" if v >= 0 else "#26a69a" for v in daily_net]
        bar_label = f"{selected_brokers[0]} 每日買賣超" if len(selected_brokers) == 1 else f"所選({len(selected_brokers)}個)合計每日買賣超"
        fig.add_trace(
            go.Bar(
                x=df_plot["date"],
                y=daily_net,
                name=f"{bar_label} (張)",
                marker_color=bar_colors,
                opacity=0.85,
                hovertemplate="%{y:+,.0f} 張<extra>" + bar_label + "</extra>",
            ),
            row=2, col=1, secondary_y=False,
        )

        # 中層副軸：累計統計張數折線
        line_label = f"{selected_brokers[0]} 累計張數" if len(selected_brokers) == 1 else f"所選({len(selected_brokers)}個)累計張數"
        fig.add_trace(
            go.Scatter(
                x=df_plot["date"],
                y=cum_total,
                name=f"{line_label} (累計張)",
                line=dict(color="#00E5FF", width=2.4),
                hovertemplate="%{y:+,.0f} 張<extra>" + line_label + "</extra>",
            ),
            row=2, col=1, secondary_y=True,
        )

        # 下層（選配）：融資餘額 vs 借券賣出餘額
        if use_3rows:
            if "margin_bal" in df_plot.columns and df_plot["margin_bal"].notna().any():
                fig.add_trace(
                    go.Scatter(
                        x=df_plot["date"],
                        y=df_plot["margin_bal"],
                        name="融資餘額 (張)",
                        line=dict(color="#f59e0b", width=2.0),
                        connectgaps=True,
                        hovertemplate="%{y:,.0f} 張<extra>融資餘額</extra>",
                    ),
                    row=3, col=1, secondary_y=False,
                )
            if "sbl_bal" in df_plot.columns and df_plot["sbl_bal"].notna().any():
                fig.add_trace(
                    go.Scatter(
                        x=df_plot["date"],
                        y=df_plot["sbl_bal"],
                        name="借券賣出餘額 (張)",
                        line=dict(color="#a78bfa", width=2.0, dash="dot"),
                        connectgaps=True,
                        hovertemplate="%{y:,.0f} 張<extra>借券賣出餘額</extra>",
                    ),
                    row=3, col=1, secondary_y=True,
                )
            fig.update_yaxes(title_text="融資餘額 (張)", row=3, col=1, secondary_y=False, showgrid=True, tickformat=",.0f")
            fig.update_yaxes(title_text="借券賣出 (張)", row=3, col=1, secondary_y=True, showgrid=False, tickformat=",.0f")

        if range_type != "全部":
            if start_filter == end_filter:
                fig.add_vline(
                    x=end_filter,
                    line_width=2,
                    line_dash="dash",
                    line_color="rgba(255, 255, 255, 0.6)",
                    annotation_text=f"統計區間 ({range_type})",
                    annotation_position="top left",
                    annotation_font_color="#e5e7eb",
                )
            else:
                fig.add_vrect(
                    x0=start_filter,
                    x1=end_filter,
                    fillcolor="rgba(255, 255, 255, 0.12)",
                    line_width=1,
                    line_dash="dash",
                    line_color="rgba(255, 255, 255, 0.45)",
                    annotation_text=f"統計區間 ({range_type})",
                    annotation_position="top left",
                    annotation_font_color="#e5e7eb",
                )

        fig.add_hline(y=0, line_width=1, line_color="rgba(255, 255, 255, 0.25)", row=2, col=1)

        fig.update_layout(
            height=chart_height,
            template="plotly_dark",
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.04, xanchor="left", x=0),
            margin=dict(l=20, r=20, t=72, b=20),
        )
        fig.update_yaxes(title_text="股價／均線／成本 (元)", row=1, col=1, showgrid=True)
        fig.update_yaxes(title_text="每日買賣超 (張)", row=2, col=1, secondary_y=False, showgrid=True, zeroline=True, tickformat=",.0f")
        fig.update_yaxes(title_text="統計張數 (累計張)", row=2, col=1, secondary_y=True, showgrid=False, tickformat=",.0f")

        st.plotly_chart(fig, width="stretch")
else:
    st.info("👈 請在左側輸入上市或上櫃股票代號（例如 `6488`、`2330`）新增建檔。")
