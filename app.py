import os
import re
import json
from datetime import datetime, timezone, timedelta
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from strategy import build_z_tooltip_html, simulate_swing_backtest
from data_engine import (
    DB_DIR,
    SCENARIO_MATRIX,
    STRATEGY_PARAMS,
    check_chip_completeness,
    update_stock_database,
    analyze_main_force,
    compute_daily_health_scores,
    delete_stock_database,
    calculate_main_force_cost,
    fetch_stock_name,
    load_stock_order,
    save_stock_order,
    sync_files_to_github_batch,
)

TW_TZ = timezone(timedelta(hours=8))

st.set_page_config(page_title="台股主力分點吃貨／出貨追蹤系統", layout="wide")

st.markdown("""
<style>
.block-container { padding-top: 2.2rem !important; }
.hero-panel { display: grid; grid-template-columns: 225px 1fr; gap: 16px; background-color: #161b26; border: 1px solid #2e374b; border-radius: 10px; padding: 14px 18px; margin-top: 6px; margin-bottom: 10px; align-items: center; }
.hero-bull { border-left: 5px solid #ef5350; background: linear-gradient(90deg, rgba(239, 83, 80, 0.10) 0%, #161b26 35%); }
.hero-bear { border-left: 5px solid #26a69a; background: linear-gradient(90deg, rgba(38, 166, 154, 0.10) 0%, #161b26 35%); }
.hero-neutral { border-left: 5px solid #64748b; background: linear-gradient(90deg, rgba(100, 116, 139, 0.10) 0%, #161b26 35%); }
.hero-score-box { border-right: 1px solid #2d364a; padding-right: 16px; }
.hero-score-label { font-size: 0.82rem; color: #9aa4b2; display: flex; align-items: center; gap: 4px; margin-bottom: 4px; }
.score-val { font-size: 2.0rem; font-weight: 800; font-variant-numeric: tabular-nums; line-height: 1.15; }
.score-bar-bg { width: 100%; height: 7px; background-color: #273043; border-radius: 4px; margin: 7px 0 6px 0; overflow: hidden; }
.score-bar-fill { height: 100%; border-radius: 4px; }
.hero-score-sub { font-size: 0.8rem; color: #cbd5e1; font-weight: 600; }
.hero-body-title { font-size: 1.02rem; font-weight: 700; color: #f8fafc; margin-bottom: 4px; }
.hero-body-desc { font-size: 0.85rem; color: #cbd5e1; line-height: 1.48; }
.hero-body-action { font-size: 0.85rem; color: #fde68a; margin-top: 5px; line-height: 1.45; }
.score-breakdown-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 7px; font-size: 0.75rem; font-variant-numeric: tabular-nums; color: #94a3b8; }
.sb-pill { background-color: #111622; border: 1px solid #283247; border-radius: 5px; padding: 2px 7px; }
.sb-pill-sub { color: #64748b; margin-left: 3px; font-size: 0.72rem; }
.div-strip { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; border-radius: 7px; padding: 8px 14px; margin-bottom: 10px; font-size: 0.85rem; line-height: 1.4; }
.div-strip-bull { background-color: rgba(239, 83, 80, 0.12); border: 1px solid rgba(239, 83, 80, 0.35); border-left: 4px solid #ef5350; color: #f8fafc; }
.div-strip-bear { background-color: rgba(38, 166, 154, 0.12); border: 1px solid rgba(38, 166, 154, 0.35); border-left: 4px solid #26a69a; color: #f8fafc; }
.div-strip-warn { background-color: rgba(245, 158, 11, 0.12); border: 1px solid rgba(245, 158, 11, 0.35); border-left: 4px solid #f59e0b; color: #fef3c7; }
.summary-card { background-color: #1c202a; border: 1px solid #2e3444; border-radius: 10px; padding: 13px 16px; height: 100%; position: relative; overflow: visible; }
.card-label { font-size: 0.82rem; color: #9aa4b2; margin-bottom: 5px; display: flex; align-items: center; gap: 4px; }
.card-value { font-size: 1.08rem; font-weight: 700; color: #f3f4f6; line-height: 1.38; word-break: break-word; }
.card-sub { font-size: 0.8rem; color: #94a3b8; margin-top: 6px; line-height: 1.45; }
.trust-bar { background-color: #181d28; border: 1px solid #2f3b52; border-left: 4px solid #38bdf8; border-radius: 8px; padding: 10px 14px; margin-top: 10px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; color: #e2e8f0; }
.trust-title { font-weight: 700; font-size: 0.9rem; color: #f8fafc; display: inline-flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.trust-tag { font-size: 0.74rem; color: #cbd5e1; background-color: #232a3b; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
.trust-metrics-wrap { display: flex; flex-wrap: wrap; align-items: center; gap: 14px; font-variant-numeric: tabular-nums; }
.trust-primary { display: inline-flex; align-items: center; gap: 10px; background-color: #111622; border: 1px solid #2b354b; border-radius: 6px; padding: 4px 10px; font-size: 0.92rem; }
.trust-secondary { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 11px; font-size: 0.8rem; color: #94a3b8; }
.trust-lbl { color: #94a3b8; font-size: 0.78rem; margin-right: 3px; }
.aux-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 10px; }
.aux-card { background-color: #161b26; border: 1px solid #2a3347; border-radius: 8px; padding: 10px 13px; font-variant-numeric: tabular-nums; }
.aux-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px; gap: 6px; }
.aux-title { font-size: 0.82rem; font-weight: 700; color: #cbd5e1; display: inline-flex; align-items: center; }
.aux-badge { font-size: 0.72rem; font-weight: 700; padding: 2px 7px; border-radius: 4px; white-space: nowrap; }
.aux-main { font-size: 1.05rem; font-weight: 700; color: #f8fafc; line-height: 1.35; }
.aux-sub { font-size: 0.78rem; color: #94a3b8; margin-top: 4px; line-height: 1.4; }
.conc-panel { background-color: #161a23; border: 1px solid #2d3548; border-radius: 10px; padding: 12px 16px; margin-top: 10px; margin-bottom: 6px; }
.conc-header { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; margin-bottom: 9px; gap: 8px; }
.conc-title { font-size: 0.9rem; font-weight: 700; color: #e2e8f0; display: inline-flex; align-items: center; flex-wrap: wrap; }
.conc-sub-tag { font-size: 0.76rem; color: #94a3b8; background-color: #232938; padding: 2px 8px; border-radius: 4px; margin-left: 8px; font-weight: 400; }
.conc-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.conc-box { background-color: #1e2330; border: 1px solid #2e374b; border-radius: 8px; padding: 10px 14px; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.conc-box-left { display: flex; flex-direction: column; }
.conc-period { font-size: 0.79rem; color: #94a3b8; margin-bottom: 2px; }
.conc-val-row { display: flex; align-items: baseline; gap: 8px; }
.conc-val { font-size: 1.15rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.conc-z { font-size: 0.78rem; font-weight: 600; padding: 1px 6px; border-radius: 4px; background-color: #252d3f; font-variant-numeric: tabular-nums; }
.conc-z-pending { font-size: 0.71rem; color: #64748b; padding: 1px 5px; border-radius: 4px; background-color: rgba(100, 116, 139, 0.14); }
.conc-badge { font-size: 0.77rem; font-weight: 600; padding: 3px 8px; border-radius: 5px; white-space: nowrap; }
.badge-row { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 8px; }
.res-badge { display: inline-flex; align-items: center; gap: 4px; font-size: 0.77rem; font-weight: 600; padding: 3px 9px; border-radius: 999px; border: 1px solid transparent; cursor: help; }
.res-bull { background-color: rgba(239, 83, 80, 0.16); border-color: rgba(239, 83, 80, 0.4); color: #fca5a5; }
.res-bear { background-color: rgba(38, 166, 154, 0.16); border-color: rgba(38, 166, 154, 0.4); color: #80cbc4; }
.res-warn { background-color: rgba(245, 158, 11, 0.16); border-color: rgba(245, 158, 11, 0.45); color: #fcd34d; }
.res-info { background-color: rgba(148, 163, 184, 0.14); border-color: rgba(148, 163, 184, 0.35); color: #cbd5e1; }
.bt-panel { background-color: #161b26; border: 1px solid #2d364a; border-radius: 10px; padding: 13px 16px; margin-top: 6px; margin-bottom: 10px; }
.bt-sub-title { font-size: 0.87rem; font-weight: 700; color: #f1f5f9; margin: 14px 0 8px 0; }
.bt-verdict { background-color: #111622; border: 1px solid #293349; border-left: 3px solid #f59e0b; border-radius: 6px; padding: 8px 12px; font-size: 0.83rem; color: #e2e8f0; margin-bottom: 10px; line-height: 1.5; }
.bt-signal-strip { background-color: rgba(56, 189, 248, 0.08); border: 1px solid rgba(56, 189, 248, 0.28); border-radius: 6px; padding: 7px 12px; font-size: 0.82rem; color: #e2e8f0; margin-bottom: 10px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px; }
.bt-table { width: 100%; border-collapse: collapse; font-size: 0.81rem; font-variant-numeric: tabular-nums; background-color: #121722; border-radius: 7px; overflow: hidden; }
.bt-table th { background-color: #1f2636; color: #cbd5e1; padding: 8px 9px; text-align: right; font-weight: 600; white-space: nowrap; }
.bt-table th:first-child { text-align: left; }
.bt-table td { padding: 8px 9px; border-top: 1px solid #232b3d; text-align: right; color: #e2e8f0; white-space: nowrap; }
.bt-table td:first-child { text-align: left; font-weight: 700; }
.bt-table-main th:last-child, .bt-table-main td:last-child { text-align: left; font-size: 0.77rem; }
.bt-row-highlight td { background-color: rgba(245, 158, 11, 0.08); }
.bt-row-bh td { background-color: rgba(56, 189, 248, 0.06); border-top: 1px solid #334155; color: #cbd5e1; }
.bt-note { font-size: 0.75rem; color: #94a3b8; margin-top: 8px; line-height: 1.5; }
.trade-table { width: 100%; border-collapse: collapse; font-size: 0.8rem; font-variant-numeric: tabular-nums; background-color: #121722; border-radius: 7px; overflow: hidden; }
.trade-table th { background-color: #1f2636; color: #cbd5e1; padding: 7px 9px; text-align: right; font-weight: 600; white-space: nowrap; }
.trade-table th:nth-child(1), .trade-table th:nth-child(2), .trade-table th:nth-child(3), .trade-table th:nth-child(6), .trade-table th:last-child { text-align: left; }
.trade-table td { padding: 7px 9px; border-top: 1px solid #232b3d; text-align: right; color: #e2e8f0; white-space: nowrap; }
.trade-table td:nth-child(1), .trade-table td:nth-child(2), .trade-table td:nth-child(3), .trade-table td:nth-child(6), .trade-table td:last-child { text-align: left; }
.trade-row-open td { background-color: rgba(251, 191, 36, 0.08); }
.top3-header-line { font-size: 0.94rem; color: #f1f5f9; margin: 4px 0 6px 4px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
.mf-list { margin: 2px 0 8px 16px; overflow: visible; }
.mf-row { display: grid; grid-template-columns: 205px 140px 130px 112px 128px 128px 88px 132px; align-items: center; padding: 5px 0; font-size: 0.89rem; font-variant-numeric: tabular-nums; white-space: nowrap; color: #e2e8f0; border-bottom: 1px dashed rgba(51, 65, 85, 0.45); }
.mf-name { font-weight: 700; color: #f8fafc; padding-right: 8px; display: inline-flex; align-items: center; gap: 4px; }
.mf-tag-pill { font-size: 0.7rem; font-weight: 600; padding: 1px 5px; border-radius: 4px; background-color: rgba(251, 191, 36, 0.16); color: #fcd34d; }
.mf-cell { display: flex; justify-content: space-between; align-items: center; padding: 0 9px; border-left: 1px solid #334155; }
.mf-lbl { color: #94a3b8; font-size: 0.81rem; margin-right: 5px; display: inline-flex; align-items: center; }
.smart-grid { display: grid; grid-template-columns: 1.35fr 1fr; gap: 12px; margin: 10px 0 4px 0; }
.smart-panel { background-color: #161b26; border: 1px solid #2b3448; border-radius: 8px; padding: 10px 13px; }
.smart-title { font-size: 0.84rem; font-weight: 700; color: #e2e8f0; margin-bottom: 6px; display: flex; align-items: center; justify-content: space-between; }
.tip-wrap { position: relative; display: inline-flex; align-items: center; cursor: help; margin-left: 4px; color: #94a3b8; font-size: 0.85rem; font-weight: 400; }
.tip-wrap:hover { color: #f8fafc; }
.tip-box { visibility: hidden; opacity: 0; position: absolute; z-index: 99999; width: 340px; background-color: #0f172a; color: #e2e8f0; border: 1px solid #475569; border-radius: 7px; padding: 10px 12px; font-size: 0.79rem; font-weight: 400; line-height: 1.55; white-space: normal; text-align: left; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.72); transition: opacity 0.15s ease; pointer-events: none; }
.tip-box-wide { width: 455px; }
.tip-wrap:hover .tip-box { visibility: visible; opacity: 1; }
.tip-right .tip-box { left: 140%; top: 50%; transform: translateY(-50%); }
.tip-left .tip-box { right: 140%; top: 50%; transform: translateY(-50%); }
.tip-down .tip-box { left: -10px; top: 145%; }
.tip-down-left .tip-box { right: -14px; top: 145%; }
.tip-mini-tbl { width: 100%; border-collapse: collapse; margin-top: 6px; font-size: 0.76rem; }
.tip-mini-tbl th { background-color: #1e293b; color: #cbd5e1; padding: 4px 6px; text-align: left; border-bottom: 1px solid #334155; white-space: nowrap; }
.tip-mini-tbl td { padding: 5px 6px; border-bottom: 1px solid #1e293b; color: #e2e8f0; vertical-align: top; line-height: 1.4; }
.cost-banner { background-color: #1a2230; border-left: 4px solid #ff9100; border-radius: 6px; padding: 9px 14px; margin: 6px 0 10px 0; font-size: 0.89rem; color: #e2e8f0; }
.rank-table { width: 100%; border-collapse: collapse; font-size: 0.79rem; background-color: #161922; border-radius: 8px; overflow: hidden; }
.rank-table th { background-color: #222736; color: #cbd5e1; text-align: left; padding: 7px 7px; font-weight: 600; white-space: nowrap; }
.rank-table td { padding: 6px 7px; border-top: 1px solid #262c3d; color: #e2e8f0; font-variant-numeric: tabular-nums; white-space: nowrap; }
.matrix-table { width: 100%; border-collapse: collapse; font-size: 0.83rem; background-color: #141821; }
.matrix-table th { background-color: #1f2533; color: #cbd5e1; text-align: left; padding: 8px 10px; font-weight: 600; }
.matrix-table td { padding: 8px 10px; border-top: 1px solid #262e40; color: #cbd5e1; line-height: 1.5; vertical-align: top; }
.matrix-active-bull td { background-color: rgba(239, 83, 80, 0.14); color: #ffffff; border-top: 1px solid rgba(239, 83, 80, 0.45); border-bottom: 1px solid rgba(239, 83, 80, 0.45); }
.matrix-active-bear td { background-color: rgba(38, 166, 154, 0.14); color: #ffffff; border-top: 1px solid rgba(38, 166, 154, 0.45); border-bottom: 1px solid rgba(38, 166, 154, 0.45); }
.matrix-active-neutral td { background-color: rgba(245, 158, 11, 0.13); color: #ffffff; border-top: 1px solid rgba(245, 158, 11, 0.45); border-bottom: 1px solid rgba(245, 158, 11, 0.45); }
.sidebar-help-box { background-color: #1a1f2b; border: 1px solid #2d3548; border-radius: 6px; padding: 7px 10px; font-size: 0.78rem; color: #94a3b8; line-height: 1.5; margin-bottom: 8px; }
.snap-bar-label { font-size: 0.82rem; color: #94a3b8; padding-top: 8px; font-weight: 600; }
/* 左側已存個股清單：分數置於最左側、股號對齊、選中改冰藍底框 */
section[data-testid="stSidebar"] [class*="st-key-sel_"] button {
    display: flex !important;
    justify-content: flex-start !important;
    align-items: center !important;
    text-align: left !important;
    padding-left: 10px !important;
    padding-right: 8px !important;
    font-variant-numeric: tabular-nums;
}
section[data-testid="stSidebar"] [class*="st-key-sel_active_"] button {
    background-color: rgba(56, 189, 248, 0.16) !important;
    border-color: #38bdf8 !important;
    border-left: 4px solid #38bdf8 !important;
    padding-left: 7px !important;
}
section[data-testid="stSidebar"] [class*="st-key-sel_active_"] button:hover {
    background-color: rgba(56, 189, 248, 0.22) !important;
    border-color: #7dd3fc !important;
}
section[data-testid="stSidebar"] [class*="st-key-sel_"] button > div {
    width: 100% !important;
    display: flex !important;
    justify-content: flex-start !important;
    text-align: left !important;
}
section[data-testid="stSidebar"] [class*="st-key-sel_"] button p {
    width: 100% !important;
    text-align: left !important;
    white-space: nowrap !important;
    overflow: hidden;
    text-overflow: ellipsis;
    margin: 0 !important;
}
section[data-testid="stSidebar"] [class*="st-key-sel_"] button strong {
    display: inline-block;
    min-width: 2.3rem;
    margin-right: 0.55rem;
    text-align: left;
}
section[data-testid="stSidebar"] [class*="st-key-sel_"] button strong,
section[data-testid="stSidebar"] [class*="st-key-sel_"] button strong span {
    color: #fbbf24 !important;
}

/* 右側籌碼基準日按鈕：分數置於最左側 */
[class*="st-key-btn_snap_"] button {
    display: flex !important;
    justify-content: flex-start !important;
    align-items: center !important;
    text-align: left !important;
    padding-left: 12px !important;
    padding-right: 10px !important;
    font-variant-numeric: tabular-nums;
}
[class*="st-key-btn_snap_"] button > div {
    width: 100% !important;
    display: flex !important;
    justify-content: flex-start !important;
    text-align: left !important;
}
[class*="st-key-btn_snap_"] button p {
    width: 100% !important;
    text-align: left !important;
    white-space: nowrap !important;
    margin: 0 !important;
}
[class*="st-key-btn_snap_"] button strong {
    display: inline-block;
    min-width: 1.9rem;
    margin-right: 0.5rem;
    text-align: left;
    color: #fbbf24 !important;
}
[class*="st-key-btn_snap_"] button[kind="primary"] strong,
[class*="st-key-btn_snap_"] button[data-testid="stBaseButton-primary"] strong {
    color: #fef08a !important;
}
</style>
""", unsafe_allow_html=True)


def get_saved_stocks() -> list:
    if not os.path.exists(DB_DIR):
        return []
    raw_ids = [f.split("_")[0] for f in os.listdir(DB_DIR) if f.endswith("_price.csv")]
    return load_stock_order(raw_ids)


def _get_stock_cache_key(stock_id: str) -> str:
    parts = []
    for suffix in ("_price.csv", "_chips.csv", "_aux.csv", "_meta.json"):
        p = os.path.join(DB_DIR, f"{stock_id}{suffix}")
        parts.append(f"{os.path.getmtime(p):.3f}" if os.path.exists(p) else "0")
    return "|".join(parts)


@st.cache_data(show_spinner=False)
def _load_stock_data_cached(stock_id: str, cache_key: str):
    price_path = os.path.join(DB_DIR, f"{stock_id}_price.csv")
    chips_path = os.path.join(DB_DIR, f"{stock_id}_chips.csv")
    aux_path = os.path.join(DB_DIR, f"{stock_id}_aux.csv")
    meta_path = os.path.join(DB_DIR, f"{stock_id}_meta.json")

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

    return df_price_full, df_chips_full, df_aux_full, meta_saved


@st.cache_data(show_spinner=False)
def _compute_daily_health_cached(
    stock_id: str,
    cache_key: str,
    _df_price_full: pd.DataFrame,
    _df_chips_full: pd.DataFrame,
    _df_aux_full: pd.DataFrame | None,
    _tdcc_history: list,
    issued_lots: float,
) -> pd.DataFrame:
    return compute_daily_health_scores(
        df_price_full=_df_price_full,
        df_chips_full=_df_chips_full,
        df_aux_full=_df_aux_full,
        tdcc_history=_tdcc_history,
        issued_lots=issued_lots,
    )


@st.cache_data(ttl=86400, show_spinner=False)
def _cached_fetch_stock_name(stock_id: str) -> str:
    return fetch_stock_name(stock_id=stock_id)


def get_stock_display_label(stock_id: str) -> str:
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


@st.cache_data(show_spinner=False)
def _get_snap_date_score_cached(stock_id: str, cache_key: str, anchor_date: str) -> int | None:
    """計算指定基準日的籌碼健康分數（依檔案 mtime 快取，與看板 100% 同口徑）"""
    try:
        df_price_raw, df_chips_raw, df_aux_raw, meta_saved = _load_stock_data_cached(stock_id, cache_key)
        if df_price_raw is None or df_price_raw.empty or df_chips_raw is None or df_chips_raw.empty:
            return None
        df_p_full = df_price_raw.sort_values("date").reset_index(drop=True)
        df_c_full = df_chips_raw.sort_values("date").reset_index(drop=True)
        df_a_full = (
            df_aux_raw.sort_values("date").reset_index(drop=True)
            if (df_aux_raw is not None and not df_aux_raw.empty)
            else None
        )
        df_p_snap = df_p_full[df_p_full["date"].astype(str) <= anchor_date].reset_index(drop=True)
        df_c_snap = df_c_full[df_c_full["date"].astype(str) <= anchor_date].reset_index(drop=True)
        df_a_snap = (
            df_a_full[df_a_full["date"].astype(str) <= anchor_date].reset_index(drop=True)
            if df_a_full is not None
            else None
        )
        if df_p_snap.empty:
            return None

        tdcc_hist = meta_saved.get("tdcc_history", []) if isinstance(meta_saved, dict) else []
        issued_lots = float(meta_saved.get("issued_lots", 0.0) or 0.0) if isinstance(meta_saved, dict) else 0.0

        res = analyze_main_force(
            df_p_snap.tail(1),
            df_c_snap.tail(1),
            anchor_date,
            df_chips_full=df_c_snap,
            df_price_full=df_p_snap,
            concentrations={},
            df_aux_full=df_a_snap,
            tdcc_history=tdcc_hist,
            issued_lots=issued_lots,
        )
        sc = res.get("scenario") if isinstance(res, dict) else None
        if isinstance(sc, dict) and (not sc.get("is_incomplete")):
            score = sc.get("conviction_score")
            if score is not None and not pd.isna(score):
                return int(round(float(score)))
    except Exception:
        pass
    return None


def get_stock_sidebar_score(stock_id: str) -> str:
    """最新日已結算直接顯示分數；結算中自動顯示前一個完整日『前日:XX』，無完整歷史才顯示 TBD"""
    meta_path = os.path.join(DB_DIR, f"{stock_id}_meta.json")
    if not os.path.exists(meta_path):
        return "TBD"
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta_data = json.load(f)
        sc = meta_data.get("scenario") if isinstance(meta_data, dict) else None
        if isinstance(sc, dict) and (not sc.get("is_incomplete")):
            score = sc.get("conviction_score")
            if score is not None and not pd.isna(score):
                return str(int(round(float(score))))

        cache_key = _get_stock_cache_key(stock_id)
        prev_score = _get_prev_complete_score_cached(stock_id, cache_key)
        if prev_score is not None:
            return f"前日:{prev_score}"
    except Exception:
        pass
    return "TBD"


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


def _span_delta_score(val: int) -> str:
    if val > 0:
        return f'<span style="color:#ef5350; font-weight:700;">{val:+d}</span>'
    elif val < 0:
        return f'<span style="color:#26a69a; font-weight:700;">{val:+d}</span>'
    return '<span style="color:#94a3b8; font-weight:600;">0</span>'


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
            f'  <span class="conc-badge" style="background:#262e40; color:#94a3b8;">尚未公布</span>'
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

    net_tag = f"（淨額 {net:+,} 張）" if net != 0 else ""
    return (
        f'<div class="conc-box">'
        f'  <div class="conc-box-left">'
        f'    <span class="conc-period">{label}{net_tag}</span>'
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
    breadth_info: dict | None,
    margin_info: dict | None,
    sbl_info: dict | None,
    tdcc_info: dict | None,
    day_word: str = "當日",
) -> str:
    p = STRATEGY_PARAMS

    breadth_tip = (
        "<b>主力分點廣度比（排除隔日沖與無進出分點｜需 ≥ 6 家活躍波段分點才計分）：</b><br>"
        "計算公式：近 5 日淨買超波段分點家數 ÷ (買超家數 ＋ 賣超家數) × 100%。<br>"
        "• <b>🔥 群起共識 (+4分)：</b>5日集中度正且廣度 ≥ 65%（外資共識 ≥ 70% 或連 3 日 ≥ 60% 再 +2分）。<br>"
        "• <b>⚠️ 少數硬撐 (-3分)：</b>5日集中度雖為正，但廣度 ≤ 40%（多數追蹤大戶實為調節偷跑）。<br>"
        "• <b>📉 集體撤退 (-4分)：</b>5日集中度負且廣度 ≤ 35%（外資共識 ≤ 30% 再 -2分）。"
    )
    if breadth_info and breadth_info.get("breadth_5d") is not None:
        b5 = float(breadth_info["breadth_5d"])
        buy5 = int(breadth_info.get("buy_5d", 0) or 0)
        sell5 = int(breadth_info.get("sell_5d", 0) or 0)
        act5 = int(breadth_info.get("active_5d", 0) or 0)
        b1 = breadth_info.get("breadth_1d")
        fb5 = breadth_info.get("foreign_breadth_5d")
        fbuy5 = int(breadth_info.get("foreign_buy_5d", 0) or 0)
        fsell5 = int(breadth_info.get("foreign_sell_5d", 0) or 0)
        fact5 = int(breadth_info.get("foreign_active_5d", 0) or 0)
        few_hold = bool(breadth_info.get("few_holding_up", False))

        f_bull = (fb5 is not None and fact5 >= p["foreign_breadth_min_active"] and fb5 >= p["foreign_breadth_bull_pct"])
        f_bear = (fb5 is not None and fact5 >= p["foreign_breadth_min_active"] and fb5 <= p["foreign_breadth_bear_pct"])

        if act5 < p["breadth_min_active"]:
            b_html, b_col = _aux_badge(f"樣本不足({act5}家)", "info"), "#cbd5e1"
        elif few_hold:
            b_html, b_col = _aux_badge("⚠️ 少數硬撐", "warn"), "#fbbf24"
        elif b5 >= p["breadth_bull_pct"]:
            b_html, b_col = _aux_badge("🌐 外資齊買" if f_bull else "🔥 群起共識", "bull"), "#ef5350"
        elif b5 <= p["breadth_bear_pct"]:
            b_html, b_col = _aux_badge("🌐 外資齊賣" if f_bear else "📉 集體撤退", "bear"), "#26a69a"
        else:
            b_html, b_col = _aux_badge("⚪ 多空分歧", "info"), "#e2e8f0"

        if fact5 > 0 and fb5 is not None:
            f_col = "#ef5350" if fb5 >= p["foreign_breadth_bull_pct"] else ("#26a69a" if fb5 <= p["foreign_breadth_bear_pct"] else "#cbd5e1")
            f_seg = f'外資 <b>{fbuy5}買/{fsell5}賣</b> (<span style="color:{f_col}; font-weight:700;">{fb5:.0f}%</span>)'
        else:
            f_seg = "外資無進出"

        today_seg = f" ｜ {day_word} <b>{b1:.0f}%</b>" if b1 is not None else ""
        breadth_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head">'
            f'    <span class="aux-title">🧭 主力分點廣度<span class="tip-wrap tip-down">ⓘ<span class="tip-box tip-box-wide">{breadth_tip}</span></span></span>'
            f'    {b_html}'
            f'  </div>'
            f'  <div class="aux-main">5日 <span style="color:{b_col}; font-weight:700;">{b5:.0f}%</span> <span style="font-size:0.84rem; color:#cbd5e1; font-weight:600;">({buy5}買/{sell5}賣)</span></div>'
            f'  <div class="aux-sub">{f_seg}{today_seg}</div>'
            f'</div>'
        )
    else:
        breadth_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head"><span class="aux-title">🧭 主力分點廣度<span class="tip-wrap tip-down">ⓘ<span class="tip-box tip-box-wide">{breadth_tip}</span></span></span>{_aux_badge("⏳ 待更新", "warn")}</div>'
            f'  <div class="aux-main" style="color:#94a3b8;">待更新</div>'
            f'  <div class="aux-sub">尚未公布</div>'
            f'</div>'
        )

    margin_tip = (
        "<b>融資四象限交叉判讀（須通過 5 日變動佔 5 日總量 ≥ 3% 濾網）：</b><br>"
        "• <b>🔥 主力融資點火 (+3分)：</b>融資大增＋集中度正＋本土波段買盤達融資增量 50%（或主力廣度 ≥ 55%）。<br>"
        "• <b>⚠️ 散戶融資過熱 (-5分)：</b>融資大增＋集中度負或主力廣度／本土買盤不足（散戶槓桿擴散）。<br>"
        "• <b>🧹 融資清洗 (+5分)：</b>融資減少＋集中度正或主力廣度 ≥ 60%（散戶停損、主力承接）。<br>"
        "• <b>💨 融資退場發散 (-3分)：</b>融資減少＋集中度轉負且廣度偏弱（原主力融資結帳撤退）。"
    )
    if margin_info:
        m_bal = margin_info["latest_bal"]
        m_5d = margin_info["chg_5d"]
        m_5d_pct = margin_info["chg_5d_pct"]
        m_vol_pct = margin_info.get("chg_5d_vol_pct")
        sm_rat = margin_info["short_margin_ratio"]
        s_bal = margin_info["short_bal"]
        quad = int(margin_info.get("quadrant", 0) or 0)

        if quad == 1:
            m_badge, inv_col = _aux_badge("🔥 主力融資點火", "bull"), False
        elif quad == 2:
            m_badge, inv_col = _aux_badge("⚠️ 散戶融資過熱", "bear"), True
        elif quad == 3:
            m_badge, inv_col = _aux_badge("🧹 融資清洗", "bull"), True
        elif quad == 4:
            m_badge, inv_col = _aux_badge("💨 融資退場發散", "bear"), False
        else:
            m_badge, inv_col = _aux_badge("⚪ 信用中性", "info"), True

        vol_seg = f" ｜ 佔量 <b>{m_vol_pct:.1f}%</b>" if m_vol_pct is not None else ""
        margin_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head">'
            f'    <span class="aux-title">🧹 融資四象限／券資比<span class="tip-wrap tip-down">ⓘ<span class="tip-box tip-box-wide">{margin_tip}</span></span></span>'
            f'    {m_badge}'
            f'  </div>'
            f'  <div class="aux-main">5日 {_span_num(m_5d, " 張", invert_color=inv_col)} ({_span_float(m_5d_pct, "%", invert_color=inv_col)})</div>'
            f'  <div class="aux-sub">餘額 <b>{m_bal:,}</b> 張{vol_seg} ｜ 券資比 <b style="color:#fbbf24;">{sm_rat:.1f}%</b> ({s_bal:,}張)</div>'
            f'</div>'
        )
    else:
        margin_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head"><span class="aux-title">🧹 融資四象限／券資比<span class="tip-wrap tip-down">ⓘ<span class="tip-box tip-box-wide">{margin_tip}</span></span></span>{_aux_badge("⏳ 待更新", "warn")}</div>'
            f'  <div class="aux-main" style="color:#94a3b8;">待更新</div>'
            f'  <div class="aux-sub">尚未公布</div>'
            f'</div>'
        )

    sbl_tip = (
        "<b>外資與機構法人借券賣出餘額（含多空情境修正｜須達 5 日總量 ≥ 3% 濾網）：</b><br>"
        "• <b>🚀 借券回補 (+5分)：</b>偏多或止穩籌碼下借券大減，屬空方認輸回補。<br>"
        "• <b>ℹ️ 獲利回補 (0分)：</b>波段分點與集中度仍偏空時借券大減，屬空軍順勢獲利了結，不予多方加分。<br>"
        "• <b>🐻 空單加壓 (-5分)：</b>借券餘額顯著增加，空方避險／放空壓力升溫（偏多對作時減輕為 -2 分）。"
    )
    if sbl_info:
        sbl_bal = sbl_info["latest_bal"]
        sbl_5d = sbl_info["chg_5d"]
        sbl_pct = sbl_info["chg_5d_pct"]
        sbl_z = sbl_info.get("chg_5d_z")
        sbl_vol_pct = sbl_info.get("chg_5d_vol_pct")
        sbl_1d = sbl_info["chg_1d"]
        sbl_mode = str(sbl_info.get("sbl_mode", "") or "")
        sbl_vol_ok = True if sbl_vol_pct is None else (sbl_vol_pct >= p["credit_vol_ratio_min"])

        if sbl_mode == "profit_cover":
            sbl_badge = _aux_badge("ℹ️ 獲利回補 (0分)", "info")
            main_sbl_html = f'5日 <span style="color:#cbd5e1; font-weight:700;">{sbl_5d:+,} 張</span> (<span style="color:#cbd5e1; font-weight:700;">{sbl_pct:+.2f}%</span>)'
        elif sbl_mode == "bull_cover" or (not sbl_mode and sbl_vol_ok and sbl_5d < 0 and (sbl_pct <= p["sbl_cover_5d_pct"] or (sbl_z is not None and sbl_z <= p["z_bear_max"]))):
            sbl_badge = _aux_badge("🚀 借券回補", "bull")
            main_sbl_html = f'5日 {_span_num(sbl_5d, " 張", invert_color=True)} ({_span_float(sbl_pct, "%", invert_color=True)})'
        elif sbl_mode == "bear_surge" or (not sbl_mode and sbl_vol_ok and sbl_5d > 0 and (sbl_pct >= p["sbl_surge_5d_pct"] or (sbl_z is not None and sbl_z >= p["z_bull_min"]))):
            sbl_badge = _aux_badge("🐻 空單加壓", "bear")
            main_sbl_html = f'5日 {_span_num(sbl_5d, " 張", invert_color=True)} ({_span_float(sbl_pct, "%", invert_color=True)})'
        else:
            sbl_badge = _aux_badge("⚪ 空單持平", "info")
            main_sbl_html = f'5日 {_span_num(sbl_5d, " 張", invert_color=True)} ({_span_float(sbl_pct, "%", invert_color=True)})'

        vol_seg = f" ｜ 佔量 <b>{sbl_vol_pct:.1f}%</b>" if sbl_vol_pct is not None else ""
        sbl_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head">'
            f'    <span class="aux-title">🐻 借券賣出餘額<span class="tip-wrap tip-down">ⓘ<span class="tip-box tip-box-wide">{sbl_tip}</span></span></span>'
            f'    {sbl_badge}'
            f'  </div>'
            f'  <div class="aux-main">{main_sbl_html}</div>'
            f'  <div class="aux-sub">餘額 <b>{sbl_bal:,}</b> 張{vol_seg} ｜ {day_word} {_span_num(sbl_1d, " 張", invert_color=True)}</div>'
            f'</div>'
        )
    else:
        sbl_card = (
            f'<div class="aux-card">'
            f'  <div class="aux-head"><span class="aux-title">🐻 借券賣出餘額<span class="tip-wrap tip-down">ⓘ<span class="tip-box tip-box-wide">{sbl_tip}</span></span></span>{_aux_badge("⏳ 待更新", "warn")}</div>'
            f'  <div class="aux-main" style="color:#94a3b8;">待更新</div>'
            f'  <div class="aux-sub">尚未公布</div>'
            f'</div>'
        )

    tdcc_tip = "集保中心每週五公布之歸戶股權分散表，可破解主力拆單藏分點與匯撥。大戶比率上升且總股東人數減少為標準籌碼集中訊號（納入中線趨勢面 ±4 分；超過 14 天未更新自動歸零不計分）。"
    if tdcc_info:
        w1000 = tdcc_info["whale_1000_pct"]
        w1000_chg = tdcc_info.get("whale_1000_chg_1w")
        w400 = tdcc_info["whale_400_pct"]
        holders = tdcc_info["total_holders"]
        h_chg = tdcc_info.get("holders_chg_1w")
        t_dt = tdcc_info.get("date", "")
        is_stale = bool(tdcc_info.get("is_stale", False))
        chg_txt = _span_float(w1000_chg, "%") if w1000_chg is not None else '<span style="color:#94a3b8;">首週</span>'
        h_chg_txt = _span_num(h_chg, " 人", invert_color=True) if h_chg is not None else "—"
        if is_stale:
            t_badge = _aux_badge("⚠️ 逾期 (0分)", "warn")
        elif w1000_chg is not None and w1000_chg >= 0.2 and (h_chg is None or h_chg < 0):
            t_badge = _aux_badge("🐋 大戶增持", "bull")
        elif w1000_chg is not None and w1000_chg <= -0.2 and (h_chg is None or h_chg > 0):
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
            f'  <div class="aux-sub">尚未公布</div>'
            f'</div>'
        )

    return f'<div class="aux-grid">{breadth_card}{margin_card}{sbl_card}{tdcc_card}</div>'


def _fmt_bt_pct(val: float | None, neutral_zero: bool = False) -> str:
    if val is None:
        return '<span style="color:#64748b;">—</span>'
    if neutral_zero and abs(val) < 1e-9:
        return '<span style="color:#cbd5e1; font-weight:600;">0.00%</span>'
    col = "#ef5350" if val > 0 else ("#26a69a" if val < 0 else "#cbd5e1")
    return f'<span style="color:{col}; font-weight:700;">{val:+.2f}%</span>'


def _fmt_bt_mdd(val: float | None) -> str:
    if val is None:
        return '<span style="color:#64748b;">—</span>'
    col = "#fca5a5" if val <= -20.0 else ("#fbbf24" if val <= -10.0 else "#80cbc4")
    return f'<span style="color:{col}; font-weight:700;">{val:.1f}%</span>'


def _fmt_bt_pr(trade_count: int, pr: float | None) -> str:
    if trade_count == 0:
        return '<span style="color:#64748b;">—</span>'
    if pr is None:
        return '<span style="color:#ef5350; font-weight:700;">全勝</span>'
    col = "#ef5350" if pr >= 1.5 else ("#fbbf24" if pr >= 1.0 else "#26a69a")
    return f'<span style="color:{col}; font-weight:700;">{pr:.2f}</span>'


def _fmt_bt_wr(trade_count: int, wr: float | None) -> str:
    if wr is None or trade_count == 0:
        return '<span style="color:#64748b;">—</span>'
    col = "#fca5a5" if wr >= 55.0 else ("#80cbc4" if wr < 40.0 else "#cbd5e1")
    return f'<span style="color:{col}; font-weight:700;">{wr:.1f}%</span>'


def _render_swing_summary_html(sim_res: dict) -> str:
    if not sim_res or not sim_res.get("valid"):
        return (
            '<div class="bt-panel">'
            '  <div class="bt-note">目前有效評分交易日樣本不足（需滿 20 日暖身期），尚無法進行籌碼波段模擬回測。</div>'
            '</div>'
        )

    strategies = sim_res["strategies"]
    order = sim_res["strategy_order"]
    bh = sim_res["bh"]
    params = sim_res.get("params") or {}
    l_in = int(params.get("long_entry", 60))
    l_out = int(params.get("long_exit", 40))
    s_in = int(params.get("short_entry", 30))
    s_out = int(params.get("short_exit", 50))
    main_s = strategies.get("both_pure") or strategies[order[0]]

    rows_html = []
    for k in order:
        s = strategies[k]
        row_cls = "bt-row-highlight" if k == "both_pure" else ""
        cnt_txt = f"{s['trade_count']} 筆" + (" (含持倉)" if s.get("has_open") else "")
        hold_txt = f"{s['avg_hold_days']:.1f} 日" if s.get("avg_hold_days") is not None else "—"
        rows_html.append(
            f'<tr class="{row_cls}">'
            f'  <td>{s["label"]}</td>'
            f'  <td>{cnt_txt}</td>'
            f'  <td>{_fmt_bt_wr(s["trade_count"], s.get("win_rate"))}</td>'
            f'  <td>{_fmt_bt_pr(s["trade_count"], s.get("payoff_ratio"))}</td>'
            f'  <td>{_fmt_bt_pct(s.get("avg_ret_pct"))}</td>'
            f'  <td>{hold_txt}</td>'
            f'  <td>{_fmt_bt_pct(s.get("total_ret_pct"), neutral_zero=True)}</td>'
            f'  <td>{_fmt_bt_mdd(s.get("mdd_pct"))}</td>'
            f'  <td>{s.get("next_signal", "")}</td>'
            f'</tr>'
        )

    rows_html.append(
        f'<tr class="bt-row-bh">'
        f'  <td>📌 同期買進持有 (Buy &amp; Hold)</td>'
        f'  <td>1 筆</td><td>—</td><td>—</td><td>—</td>'
        f'  <td>{sim_res["total_days"]} 日</td>'
        f'  <td>{_fmt_bt_pct(bh.get("total_ret_pct"), neutral_zero=True)}</td>'
        f'  <td>{_fmt_bt_mdd(bh.get("mdd_pct"))}</td>'
        f'  <td>基準對照（{sim_res["start_date"][5:]} ~ {sim_res["end_date"][5:]}）</td>'
        f'</tr>'
    )

    compare_block_html = ""
    cmp_list = sim_res.get("threshold_compare") or []
    if cmp_list:
        cmp_rows = []
        for item in cmp_list:
            row_cls = "bt-row-highlight" if item.get("is_current") else ""
            cur_tag = (
                ' <span style="font-size:0.71rem; padding:1px 6px; border-radius:4px; background:#f59e0b; color:#0f172a; font-weight:700; margin-left:4px;">目前設定</span>'
                if item.get("is_current") else ""
            )
            cnt_txt = f"{item['trade_count']} 筆" + (" (含持倉)" if item.get("has_open") else "")
            hold_txt = f"{item['avg_hold_days']:.1f} 日" if item.get("avg_hold_days") is not None else "—"
            cmp_rows.append(
                f'<tr class="{row_cls}">'
                f'  <td>{item["label"]}{cur_tag}</td>'
                f'  <td>{cnt_txt}</td>'
                f'  <td>{_fmt_bt_wr(item["trade_count"], item.get("win_rate"))}</td>'
                f'  <td>{_fmt_bt_pr(item["trade_count"], item.get("payoff_ratio"))}</td>'
                f'  <td>{_fmt_bt_pct(item.get("avg_ret_pct"))}</td>'
                f'  <td>{hold_txt}</td>'
                f'  <td>{_fmt_bt_pct(item.get("total_ret_pct"), neutral_zero=True)}</td>'
                f'  <td>{_fmt_bt_mdd(item.get("mdd_pct"))}</td>'
                f'</tr>'
            )
        compare_block_html = (
            f'<div class="bt-sub-title">🎯 籌碼門檻敏感度對照（多空雙向｜尋找最佳多空判斷分數）</div>'
            f'<table class="bt-table">'
            f'  <thead><tr><th>門檻組合（做多進/出 ｜ 放空進/出）</th><th>交易次數</th><th>勝率</th><th>賺賠比</th><th>單筆均報</th><th>平均持有</th><th>累積淨報酬</th><th>最大回撤 (MDD)</th></tr></thead>'
            f'  <tbody>{"".join(cmp_rows)}</tbody>'
            f'</table>'
        )

    rules_note = (
        f"規則說明（100% 籌碼，不含均線與停損）：當日盤後算出籌碼分數 → <b>次日開盤價</b>執行進出，每筆來回扣除 <b>{sim_res['cost_pct']:.2f}%</b> 摩擦成本。"
        f"目前設定：做多 ≥ {l_in} 分進場、&lt; {l_out} 分出場（或直接翻空）；放空 ≤ {s_in} 分進場、&gt; {s_out} 分回補（或直接翻多）。"
        "「賺賠比」＝平均獲利交易報酬 ÷ 平均虧損交易幅度（≥ 1.5 代表大賺小賠）。"
    )

    return (
        f'<div class="bt-panel">'
        f'  <div class="bt-verdict">🎯 <b>籌碼波段總結（{sim_res["start_date"]} ~ {sim_res["end_date"]}｜共 {sim_res["total_days"]} 交易日）：</b>{sim_res["summary_text"]}</div>'
        f'  <div class="bt-signal-strip">'
        f'    <span>🧭 <b>籌碼多空雙向目前狀態／次日預告：</b>{main_s["next_signal"]}</span>'
        f'    <span style="font-size:0.76rem; color:#94a3b8;">已含單筆來回摩擦成本 {sim_res["cost_pct"]:.2f}%</span>'
        f'  </div>'
        f'  <table class="bt-table bt-table-main">'
        f'    <thead><tr><th>策略模式</th><th>交易次數</th><th>勝率</th><th>賺賠比</th><th>單筆均報</th><th>平均持有</th><th>累積淨報酬</th><th>最大回撤 (MDD)</th><th>目前狀態／次日開盤訊號</th></tr></thead>'
        f'    <tbody>{"".join(rows_html)}</tbody>'
        f'  </table>'
        f'  {compare_block_html}'
        f'  <div class="bt-note">{rules_note}</div>'
        f'</div>'
    )


def _render_trade_list_html(trades: list) -> str:
    if not trades:
        return '<div class="bt-note">此門檻設定於今年度有效區間內尚未觸發任何進場訊號。</div>'

    rows_html = []
    for t in trades:
        is_open = bool(t.get("is_open", False))
        row_cls = "trade-row-open" if is_open else ""
        dir_badge = (
            '<span style="font-size:0.72rem; padding:1px 6px; border-radius:4px; background:rgba(239,83,80,0.16); color:#fca5a5; font-weight:700;">做多</span>'
            if t["direction"] == "做多"
            else '<span style="font-size:0.72rem; padding:1px 6px; border-radius:4px; background:rgba(38,166,154,0.16); color:#80cbc4; font-weight:700;">放空</span>'
        )
        ret = float(t["net_ret_pct"])
        ret_col = "#ef5350" if ret > 0 else ("#26a69a" if ret < 0 else "#cbd5e1")
        exit_dt_txt = f"{t['exit_date']} (收盤)" if is_open else t["exit_date"]
        reason_style = 'style="color:#fbbf24; font-weight:700;"' if is_open else ""
        rows_html.append(
            f'<tr class="{row_cls}">'
            f'  <td style="color:#94a3b8;">#{t["no"]}</td>'
            f'  <td>{dir_badge}</td>'
            f'  <td>{t["entry_date"]}</td>'
            f'  <td>{t["entry_price"]:,.2f}</td>'
            f'  <td>{t["entry_score"]} 分</td>'
            f'  <td>{exit_dt_txt}</td>'
            f'  <td>{t["exit_price"]:,.2f}</td>'
            f'  <td>{t["hold_days"]} 日</td>'
            f'  <td style="color:{ret_col}; font-weight:700;">{ret:+.2f}%</td>'
            f'  <td {reason_style}>{t["exit_reason"]}</td>'
            f'</tr>'
        )

    return (
        f'<table class="trade-table">'
        f'  <thead><tr><th>筆次</th><th>方向</th><th>進場日 (開盤)</th><th>進場價</th><th>觸發分數</th><th>出場日 (開盤)</th><th>出場價</th><th>持有天數</th><th>淨報酬</th><th>出場原因／狀態</th></tr></thead>'
        f'  <tbody>{"".join(rows_html)}</tbody>'
        f'</table>'
    )


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


def _compute_health_regimes(
    scores: pd.Series,
    long_entry: int = 60,
    long_exit: int = 40,
    short_entry: int = 30,
    short_exit: int = 50,
):
    """模式 B 波段持倉區塊：完整連動四項進出門檻（含交叉防呆），與波段模擬回測 100% 對齊"""
    safe_l_out = min(long_exit, long_entry)
    safe_s_out = max(short_exit, short_entry)
    n = len(scores)
    s_vals = scores.to_numpy(dtype=float) if scores is not None else [float("nan")] * n

    regimes = []
    pos = 0
    for i in range(n):
        sc = s_vals[i]
        if pd.notna(sc):
            can_long = (sc >= long_entry) and (long_entry > short_entry)
            can_short = (sc <= short_entry) and (long_entry > short_entry)
            if pos == 1:
                if can_short:
                    pos = -1
                elif sc < safe_l_out:
                    pos = 0
            elif pos == -1:
                if can_long:
                    pos = 1
                elif sc > safe_s_out:
                    pos = 0
            else:
                if can_long:
                    pos = 1
                elif can_short:
                    pos = -1

        regimes.append("bull" if pos == 1 else ("bear" if pos == -1 else "neutral"))

    bands = []
    cur_reg, start_i = None, 0
    for i, reg in enumerate(regimes):
        if reg != cur_reg:
            if cur_reg in ("bull", "bear"):
                bands.append((start_i, i - 1, cur_reg))
            cur_reg, start_i = reg, i
    if cur_reg in ("bull", "bear"):
        bands.append((start_i, n - 1, cur_reg))

    return bands, regimes


st.markdown("### 📈 台股籌碼追蹤")

tw_now = datetime.now(TW_TZ)
tw_today = tw_now.strftime("%Y-%m-%d")
tw_today_compact = tw_now.strftime("%Y%m%d")

saved_list = get_saved_stocks()
if "active_stock" not in st.session_state:
    st.session_state["active_stock"] = saved_list[0] if saved_list else "6488"
if "sort_mode" not in st.session_state:
    st.session_state["sort_mode"] = False
if "show_bt_summary" not in st.session_state:
    st.session_state["show_bt_summary"] = False
if "subchart_mode" not in st.session_state:
    st.session_state["subchart_mode"] = "score"

# --- 側邊欄：新增個股、統計區間與已存個股管理 ---
with st.sidebar:
    st.header("🔍 新增個股")
    input_stock = st.text_input("輸入台股代號", value="", placeholder="輸入 4 碼上市櫃代號").strip()
    btn_add = st.button("➕ 新增 (2026/01/02起)", type="primary", width="stretch")

    st.divider()
    st.subheader("📅 統計區間")
    range_type = st.radio(
        "選擇檢視範圍",
        options=["今日", "5日", "10日", "20日", "60日", "全部", "指定日期"],
        index=0,
        horizontal=True,
    )

    custom_start_str = "20260102"
    custom_end_str = tw_today_compact
    if range_type == "指定日期":
        st.caption("請直接輸入 8 碼數字（例：20260501）")
        custom_start_str = st.text_input("起始日期", value="20260102")
        custom_end_str = st.text_input("結束日期", value=tw_today_compact)

    st.divider()
    st.subheader(f"📁 已存個股 ({len(saved_list)})")

    if saved_list:
        st.markdown(
            '<div class="sidebar-help-box">'
            '全部更新：近 1 個月滾動更新所有個股（自動收編新主力）<br>'
            '除權息／整年重建：按 ✕ 刪除後重新新增代號'
            '</div>',
            unsafe_allow_html=True,
        )

        col_all_upd, col_sort_btn = st.columns([1.25, 1])
        with col_all_upd:
            if st.button(
                "🔄 全部更新",
                key="btn_update_all",
                help="對所有已存個股執行近 1 個月（22 個交易日）滾動更新並自動收編新主力，結束後單次打包同步至 GitHub",
                width="stretch",
                disabled=st.session_state["sort_mode"],
            ):
                prog = st.progress(0, text="準備批量近 1 個月滾動更新...")
                updated_ids = []
                for idx, sid in enumerate(saved_list):
                    lbl = get_stock_display_label(sid)
                    prog.progress(idx / len(saved_list) * 0.9, text=f"正在滾動更新 {lbl} ({idx+1}/{len(saved_list)})...")
                    if update_stock_database(sid, incremental_days=22, sync_github=False):
                        updated_ids.append(sid)

                if updated_ids and os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
                    prog.progress(0.95, text=f"正在單次打包同步 {len(updated_ids)} 檔個股至 GitHub...")
                    file_pairs = []
                    for sid in updated_ids:
                        meta_p = os.path.join(DB_DIR, f"{sid}_meta.json")
                        if os.path.exists(meta_p):
                            try:
                                with open(meta_p, "r", encoding="utf-8") as f:
                                    m_obj = json.load(f)
                                m_obj["github_sync"] = f"✅ 已單次打包同步（全部更新 {len(updated_ids)} 檔）"
                                with open(meta_p, "w", encoding="utf-8") as f:
                                    json.dump(m_obj, f, ensure_ascii=False, indent=2)
                            except Exception:
                                pass
                        for suffix in ("_price.csv", "_chips.csv", "_aux.csv", "_meta.json"):
                            fname = f"{sid}{suffix}"
                            lp = os.path.join(DB_DIR, fname)
                            if os.path.exists(lp):
                                file_pairs.append((lp, f"database/{fname}"))

                    ok_batch, batch_msg = sync_files_to_github_batch(
                        file_pairs,
                        commit_message=f"Batch update {len(updated_ids)} stocks ({tw_today})",
                    )
                    if not ok_batch:
                        for sid in updated_ids:
                            meta_p = os.path.join(DB_DIR, f"{sid}_meta.json")
                            if os.path.exists(meta_p):
                                try:
                                    with open(meta_p, "r", encoding="utf-8") as f:
                                        m_obj = json.load(f)
                                    m_obj["github_sync"] = f"⚠️ {batch_msg}"
                                    with open(meta_p, "w", encoding="utf-8") as f:
                                        json.dump(m_obj, f, ensure_ascii=False, indent=2)
                                except Exception:
                                    pass

                _load_stock_data_cached.clear()
                _compute_daily_health_cached.clear()
                prog.progress(1.0, text="全部滾動更新完成！")
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
                col_s, col_del = st.columns([4.25, 0.75])
                display_name = get_stock_display_label(sid)
                score_txt = get_stock_sidebar_score(sid)
                with col_s:
                    is_current = (sid == st.session_state.get("active_stock"))
                    btn_label = f":orange[**{score_txt}**] {display_name}"
                    btn_key = f"sel_active_{sid}" if is_current else f"sel_{sid}"
                    if st.button(btn_label, key=btn_key, width="stretch"):
                        st.session_state["active_stock"] = sid
                        st.rerun()
                with col_del:
                    if st.button("✕", key=f"del_{sid}", help=f"刪除 {display_name} 的所有歷史資料", width="stretch"):
                        with st.spinner(f"正在刪除 {display_name}..."):
                            delete_stock_database(sid)
                            _load_stock_data_cached.clear()
                            _compute_daily_health_cached.clear()
                            remaining = get_saved_stocks()
                            st.session_state["active_stock"] = remaining[0] if remaining else ""
                        st.rerun()
    else:
        st.caption("目前 database 尚無資料，請在上方輸入代號新增。")

prev_active_stock = st.session_state.get("active_stock", "").strip()
if btn_add and input_stock:
    st.session_state["active_stock"] = input_stock

target_stock = st.session_state.get("active_stock", "").strip()

if target_stock:
    price_path = os.path.join(DB_DIR, f"{target_stock}_price.csv")

    if (btn_add and input_stock) or not os.path.exists(price_path):
        with st.spinner(f"正在抓取 {target_stock} 真實 VWAP 股價、投信持股比、波段分點、歷史集中度、融資借券與集保大戶資料..."):
            ok = update_stock_database(target_stock, incremental_days=None, sync_github=True)
            if not ok:
                fallback_stock = (
                    prev_active_stock
                    if (prev_active_stock and prev_active_stock != target_stock and prev_active_stock in saved_list)
                    else (saved_list[0] if saved_list else "")
                )
                st.session_state["active_stock"] = fallback_stock
                st.error(f"找不到股票代號 `{target_stock}`，請確認是否為正確的上市櫃代號（目前不支援興櫃）。已自動還原回原本個股。")
                st.stop()
            cur_order = get_saved_stocks()
            if target_stock not in cur_order:
                cur_order.insert(0, target_stock)
                save_stock_order(cur_order, sync_github=True)
            _load_stock_data_cached.clear()
            _compute_daily_health_cached.clear()
            st.rerun()

    cache_key = _get_stock_cache_key(target_stock)
    df_price_raw, df_chips_raw, df_aux_raw, meta_saved = _load_stock_data_cached(target_stock, cache_key)
    df_price_full = df_price_raw.sort_values("date").reset_index(drop=True).copy()
    df_chips_full = df_chips_raw.sort_values("date").reset_index(drop=True).copy()
    df_aux_full = df_aux_raw.sort_values("date").reset_index(drop=True).copy() if df_aux_raw is not None else None

    stock_title = get_stock_display_label(target_stock)

    # --- 基準日回放選項（逐日檢核分點／集中度／融資／借券是否齊全） ---
    valid_dates = df_price_full["date"].astype(str).tolist()
    n_valid = len(valid_dates)
    snap_options = []

    is_latest_today = (n_valid > 0 and valid_dates[-1] == tw_today)
    cand_defs = []
    if n_valid >= 3:
        cand_defs.append((-2, "前天" if is_latest_today else "前2日", valid_dates[-3]))
    if n_valid >= 2:
        cand_defs.append((-1, "昨天" if is_latest_today else "前1日", valid_dates[-2]))
    if n_valid >= 1:
        cand_defs.append((0, "今天" if is_latest_today else "最新", valid_dates[-1]))

    for off_v, rel_l, dt_v in cand_defs:
        comp_opt = check_chip_completeness(df_price_full, df_chips_full, df_aux_full, anchor_date=dt_v)
        is_incomp = not comp_opt["is_complete"]
        sc_v = None if is_incomp else _get_snap_date_score_cached(target_stock, cache_key, dt_v)
        snap_options.append((off_v, rel_l, dt_v, is_incomp, sc_v))

    snap_key = f"snap_offset_{target_stock}"
    valid_offsets = [opt[0] for opt in snap_options]
    if snap_key not in st.session_state or st.session_state[snap_key] not in valid_offsets:
        st.session_state[snap_key] = 0

    active_offset = int(st.session_state[snap_key])
    active_snap_item = next(opt for opt in snap_options if opt[0] == active_offset)
    snap_rel_name = active_snap_item[1]
    valid_anchor_date = active_snap_item[2]
    latest_trade_date = str(df_price_full["date"].max())
    day_word = "今日" if (valid_anchor_date == tw_today) else "當日"

    df_price_snap = df_price_full[df_price_full["date"] <= valid_anchor_date].reset_index(drop=True)
    df_chips_snap = df_chips_full[df_chips_full["date"] <= valid_anchor_date].reset_index(drop=True)
    df_aux_snap = (
        df_aux_full[df_aux_full["date"] <= valid_anchor_date].reset_index(drop=True)
        if (df_aux_full is not None and not df_aux_full.empty)
        else None
    )

    min_date = df_price_snap["date"].min()
    max_date = df_price_snap["date"].max()
    days_map = {"今日": 1, "5日": 5, "10日": 10, "20日": 20, "60日": 60}

    if range_type in days_map:
        n_days = days_map[range_type]
        df_price = df_price_snap.tail(n_days).copy()
        start_filter = df_price["date"].min()
        end_filter = df_price["date"].max()
        df_chips = df_chips_snap[(df_chips_snap["date"] >= start_filter) & (df_chips_snap["date"] <= end_filter)].copy()
        if n_days == 1:
            period_label = f"今日（{end_filter}）" if valid_anchor_date == tw_today else f"{snap_rel_name}（{end_filter}）"
        else:
            period_label = f"近 {n_days} 日（{start_filter} ~ {end_filter}）"
    elif range_type == "指定日期":
        start_filter = parse_manual_date(custom_start_str, min_date)
        end_filter = min(parse_manual_date(custom_end_str, max_date), max_date)
        if start_filter > end_filter:
            st.error("起始日期不可晚於結束日期（或目前基準日），請重新輸入。")
            st.stop()
        df_price = df_price_snap[(df_price_snap["date"] >= start_filter) & (df_price_snap["date"] <= end_filter)].copy()
        df_chips = df_chips_snap[(df_chips_snap["date"] >= start_filter) & (df_chips_snap["date"] <= end_filter)].copy()
        period_label = f"{start_filter} ~ {end_filter}"
    else:
        start_filter, end_filter = min_date, max_date
        df_price = df_price_snap.copy()
        df_chips = df_chips_snap.copy()
        period_label = f"{min_date} ~ {max_date}（全部）"

    if df_price.empty:
        st.warning(f"在指定區間 `{start_filter} ~ {end_filter}` 內查無交易資料，請確認輸入的日期。")
        st.stop()

    display_close = float(df_price.iloc[-1]["close"])
    display_close_label = "最新收盤價與區間漲跌" if valid_anchor_date == tw_today else f"{snap_rel_name}收盤價與區間漲跌"
    prior_rows = df_price_snap[df_price_snap["date"] < start_filter]
    base_close = float(prior_rows.iloc[-1]["close"]) if not prior_rows.empty else float(df_price.iloc[0]["close"])
    pct_change = ((display_close - base_close) / base_close * 100.0) if base_close else 0.0
    pct_prefix = "區間漲跌："

    if pct_change > 0:
        pct_html = f'<span style="color:#ef5350; font-weight:700;">▲ {pct_change:+.2f}%</span>'
    elif pct_change < 0:
        pct_html = f'<span style="color:#26a69a; font-weight:700;">▼ {pct_change:+.2f}%</span>'
    else:
        pct_html = '<span style="color:#cbd5e1; font-weight:700;">0.00%</span>'

    tdcc_history_saved = meta_saved.get("tdcc_history", [])
    issued_lots_saved = float(meta_saved.get("issued_lots", 0.0) or 0.0)

    analysis = analyze_main_force(
        df_price,
        df_chips,
        period_label,
        df_chips_full=df_chips_snap,
        df_price_full=df_price_snap,
        concentrations={},
        df_aux_full=df_aux_snap,
        tdcc_history=tdcc_history_saved,
        issued_lots=issued_lots_saved,
    )
    completeness = analysis.get("completeness") or {
        "is_complete": True,
        "missing_items": [],
        "has_broker": True,
        "has_margin": True,
        "has_sbl": True,
        "has_trust": True,
    }
    is_chip_complete = bool(completeness.get("is_complete", True))
    missing_chip_items = completeness.get("missing_items") or []
    has_broker_today = bool(completeness.get("has_broker", True))

    broker_count = len([c for c in df_chips_full.columns if c not in ("date", "投信")])
    concentrations = analysis.get("concentrations") or {}
    scenario = analysis.get("scenario", {})
    broker_tags = analysis.get("broker_tags", {})
    top3_names = analysis.get("top3_names", [])
    winner_brokers = analysis.get("winner_brokers") or []

    if broker_count == 0 and meta_saved.get("debug_notes"):
        st.warning(f"⚠️ 分點明細未取得（{'；'.join(meta_saved['debug_notes'])}）")

    # --- 標題列 ---
    updated_str = meta_saved.get("last_updated", "")
    if not is_chip_complete:
        time_badge = f"⏳ {valid_anchor_date} 籌碼結算中（待公布：{'、'.join(missing_chip_items)}）" + (f" ｜ 更新：{updated_str}" if updated_str else "")
    elif valid_anchor_date != latest_trade_date:
        time_badge = f"⏪ 歷史回放基準日：{valid_anchor_date}（{snap_rel_name}｜最新交易日：{latest_trade_date}）"
    else:
        time_badge = f"🕒 更新時間：{updated_str}（最新交易日：{latest_trade_date}）" if updated_str else f"🕒 最新交易日：{latest_trade_date}"

    st.markdown(
        f'<div style="display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:8px;">'
        f'  <h4 style="margin:0;">🎯 {stock_title} 籌碼診斷（{period_label}｜追蹤 {broker_count} 分點）</h4>'
        f'  <span style="font-size:0.84rem; color:#94a3b8;">{time_badge}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if meta_saved.get("github_sync"):
        st.caption(f"雲端資料庫狀態：{meta_saved['github_sync']}")

    # --- 基準日快速切換列 ---
    snap_cols = st.columns([1.15] + [1.65] * len(snap_options) + [2.1])
    with snap_cols[0]:
        st.markdown('<div class="snap-bar-label">📅 籌碼基準日：</div>', unsafe_allow_html=True)
    for idx_opt, (off_val, rel_lbl, dt_val, is_incomp, sc_val) in enumerate(snap_options):
        short_d = dt_val[5:].replace("-", "/") if len(dt_val) >= 10 else dt_val
        score_prefix = f"**{sc_val}**" if (not is_incomp and sc_val is not None) else "**⏳**"
        btn_txt = f"{score_prefix} {rel_lbl} ({short_d})"
        is_sel = (off_val == active_offset)
        with snap_cols[idx_opt + 1]:
            if st.button(btn_txt, key=f"btn_snap_{target_stock}_{off_val}", type="primary" if is_sel else "secondary", width="stretch"):
                st.session_state[snap_key] = off_val
                st.rerun()

    # =====================================================================
    # 區塊一：核心決策總結看板（100% 籌碼｜趨勢 60% ＋ 動能 40%）
    # =====================================================================
    bull_min = int(STRATEGY_PARAMS.get("bull_score_min", 60))
    strong_bull_min = int(STRATEGY_PARAMS.get("strong_bull_score_min", 70))
    bear_max = int(STRATEGY_PARAMS.get("bear_score_max", 30))
    long_exit_default = int(STRATEGY_PARAMS.get("long_exit_score", 40))
    short_exit_default = int(STRATEGY_PARAMS.get("short_exit_score", 50))

    score_tip_html = (
        f"<b>100% 籌碼健康分數（0～100 分｜趨勢 60% ＋ 動能 40%｜不含技術面）</b><br>"
        "以 <b>50 分</b>為多空中性基準，分兩層累計加減（最高 ±50 分）：<br>"
        "• <b>一、中線趨勢面 (±30 分，佔 60%)：</b><br>"
        "&nbsp;&nbsp;◦ 全體波段分點合力 20/60 日控盤與庫存均線 (±16)<br>"
        "&nbsp;&nbsp;◦ 全市場 10/20 日中線集中度 (±10)<br>"
        "&nbsp;&nbsp;◦ TDCC 集保大戶週變化 (±4｜逾 14 天不計分)<br>"
        "• <b>二、短線動能面 (±20 分，佔 40%)：</b><br>"
        "&nbsp;&nbsp;◦ 5 日集中度 (±4) ＋ 主力分點廣度比含外資共識 (±6)<br>"
        "&nbsp;&nbsp;◦ 融資四象限 (±5) ＋ 情境借券賣出 (±5；空頭背景回補不加分)<br>"
        "• <b>三、當日隔日沖灌水濾網（偏多時單向扣分）：</b>0 ～ -10 分<br>"
        "<span style='color:#94a3b8;'>※ 投信下單已涵蓋於本土券商分點與集中度中，為免重複計分改於下方獨立追蹤。</span>"
        '<table class="tip-mini-tbl">'
        '  <thead><tr><th>分數區間</th><th>燈號狀態</th><th>籌碼含義</th></tr></thead>'
        '  <tbody>'
        f'    <tr><td style="white-space:nowrap;"><b>{strong_bull_min} ～ 100 分</b></td><td style="color:#ef5350; font-weight:700; white-space:nowrap;">強力共振（紅）</td><td>波段趨勢與短線動能高度共振，屬籌碼最紮實的主升鎖碼格局。</td></tr>'
        f'    <tr><td style="white-space:nowrap;"><b>{bull_min} ～ {strong_bull_min - 1} 分</b></td><td style="color:#fb7185; font-weight:700; white-space:nowrap;">偏多吸籌（紅）</td><td>大資金已開始偏向集中且尚未過熱，持續觀察廣度與集中度變化。</td></tr>'
        f'    <tr><td style="white-space:nowrap;"><b>{bear_max + 1} ～ {bull_min - 1} 分</b></td><td style="color:#94a3b8; font-weight:700; white-space:nowrap;">中性／分歧（黃）</td><td>多空力道抵銷，或趨勢與動能方向衝突／少數硬撐，宜觀望。</td></tr>'
        f'    <tr><td style="white-space:nowrap;"><b>0 ～ {bear_max} 分</b></td><td style="color:#26a69a; font-weight:700; white-space:nowrap;">明顯發散（綠）</td><td>全體波段分點持續倒貨且籌碼流向散戶，整體籌碼明顯偏空。</td></tr>'
        '  </tbody>'
        '</table>'
    )

    complete_opts = [o for o in snap_options if not o[3]]
    if complete_opts:
        last_comp = complete_opts[-1]
        prev_short_d = last_comp[2][5:].replace("-", "/") if len(last_comp[2]) >= 10 else last_comp[2]
        prev_btn_name = f"{last_comp[1]} ({prev_short_d})"
    else:
        prev_btn_name = "前一日"

    conv_score = None
    if not is_chip_complete:
        missing_txt = "、".join(missing_chip_items)
        st.markdown(
            f'<div class="hero-panel hero-neutral">'
            f'  <div class="hero-score-box">'
            f'    <div class="hero-score-label"><span>籌碼健康分數</span><span class="tip-wrap tip-down">ⓘ<span class="tip-box tip-box-wide">{score_tip_html}</span></span></div>'
            f'    <div class="score-val" style="color:#64748b;">— <span style="font-size:0.88rem; font-weight:500; color:#64748b;">/ 100</span></div>'
            f'    <div class="score-bar-bg"><div class="score-bar-fill" style="width:0%; background:#64748b;"></div></div>'
            f'    <div class="hero-score-sub" style="color:#fbbf24;">⏳ 結算中（籌碼未齊）</div>'
            f'  </div>'
            f'  <div>'
            f'    <div class="hero-body-title">⏳ {valid_anchor_date} 籌碼尚未齊全，暫不計算當日分數</div>'
            f'    <div class="hero-body-desc">今日尚缺以下必備日頻籌碼，等待公布：<b style="color:#fbbf24;">{missing_txt}</b>。為避免缺漏項目以空值或 0 張計分造成誤判，待資料齊全並更新後再自動顯示總分與狀態定調。</div>'
            f'    <div class="hero-body-action">💡 下方已公布之單項卡片（收盤價、投信、集保大戶等）照常顯示；如需檢視最新完整評分，請點擊上方「<b>{prev_btn_name}</b>」。</div>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        conv_score = int(scenario.get("conviction_score", 50))
        if conv_score >= strong_bull_min:
            score_col, score_lbl = "#ef5350", f"強力籌碼集中 (≥{strong_bull_min})"
        elif conv_score >= bull_min:
            score_col, score_lbl = "#fb7185", f"偏多吸籌區 ({bull_min}~{strong_bull_min - 1})"
        elif conv_score <= bear_max:
            score_col, score_lbl = "#26a69a", f"籌碼明顯發散 (≤{bear_max})"
        else:
            score_col, score_lbl = "#94a3b8", "籌碼中性／分歧"

        sc_type = scenario.get("type", "neutral")
        hero_cls = "hero-bull" if sc_type == "bull" else ("hero-bear" if sc_type == "bear" else "hero-neutral")
        sc_action_html = f'<div class="hero-body-action">💡 籌碼解讀：{scenario["action"]}</div>' if scenario.get("action") else ""

        sb = scenario.get("score_breakdown") or {}
        breakdown_html = ""
        if sb:
            t_tot = int(sb.get("trend_total", 0))
            t_wave = int(sb.get("trend_wave", 0))
            t_conc = int(sb.get("trend_conc", 0))
            t_tdcc = int(sb.get("trend_tdcc", sb.get("tdcc", sb.get("trend_inst", 0))))
            m_tot = int(sb.get("momentum_total", 0))
            m_cb = int(sb.get("mom_conc_breadth", sb.get("conc_breadth", 0)))
            m_cs = int(sb.get("mom_credit_sbl", sb.get("credit_sbl", 0)))
            dt_val = int(sb.get("day_trade", 0))
            breakdown_html = (
                f'<div class="score-breakdown-row">'
                f'  <span class="sb-pill">基準 <b>{sb.get("base", 50)}</b></span>'
                f'  <span class="sb-pill">🌊 趨勢(60%) {_span_delta_score(t_tot)}'
                f'    <span class="sb-pill-sub">(合力{t_wave:+d} · 10/20集中{t_conc:+d} · 集保{t_tdcc:+d})</span></span>'
                f'  <span class="sb-pill">⚡ 動能(40%) {_span_delta_score(m_tot)}'
                f'    <span class="sb-pill-sub">(5日/廣度{m_cb:+d} · 資/借{m_cs:+d})</span></span>'
                f'  <span class="sb-pill">隔日沖濾網 {_span_delta_score(dt_val)}</span>'
                f'</div>'
            )

        badges_html = ""
        sc_badges = scenario.get("badges") or []
        if sc_badges:
            cls_map = {"bull": "res-bull", "bear": "res-bear", "warn": "res-warn", "info": "res-info"}
            pill_list = [
                f'<span class="res-badge {cls_map.get(b.get("level", "info"), "res-info")}" title="{str(b.get("detail", "")).replace(chr(34), "&quot;")}">{b.get("label", "")}</span>'
                for b in sc_badges
            ]
            badges_html = f'<div class="badge-row">{"".join(pill_list)}</div>'

        st.markdown(
            f'<div class="hero-panel {hero_cls}">'
            f'  <div class="hero-score-box">'
            f'    <div class="hero-score-label"><span>籌碼健康分數</span><span class="tip-wrap tip-down">ⓘ<span class="tip-box tip-box-wide">{score_tip_html}</span></span></div>'
            f'    <div class="score-val" style="color:{score_col};">{conv_score} <span style="font-size:0.88rem; font-weight:500; color:#94a3b8;">/ 100</span></div>'
            f'    <div class="score-bar-bg"><div class="score-bar-fill" style="width:{conv_score}%; background:{score_col};"></div></div>'
            f'    <div class="hero-score-sub" style="color:{score_col};">{score_lbl}</div>'
            f'  </div>'
            f'  <div>'
            f'    <div class="hero-body-title">{scenario.get("title", "")}</div>'
            f'    <div class="hero-body-desc">{scenario.get("desc", "")}</div>'
            f'    {sc_action_html}{breakdown_html}{badges_html}'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        div_alert = analysis.get("divergence_alert")
        if div_alert:
            alert_type = div_alert.get("type", "bull")
            strip_cls = "div-strip-bull" if alert_type == "bull" else ("div-strip-warn" if alert_type == "warn" else "div-strip-bear")
            raw_desc_lines = str(div_alert.get("desc", "")).split("<br>")
            compact_desc = raw_desc_lines[0] if raw_desc_lines else ""
            full_tip_desc = str(div_alert.get("desc", "")) + "<br><span style='color:#94a3b8;'>（此項僅作價量對照參考，不參與籌碼健康評分）</span>"
            st.markdown(
                f'<div class="div-strip {strip_cls}">'
                f'  <div><b>{div_alert["title"]}</b><span class="tip-wrap tip-down" style="margin:0 6px 0 4px;">ⓘ 詳情<span class="tip-box tip-box-wide">{full_tip_desc}</span></span>｜ {compact_desc}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # --- 三大核心卡片 ---
    ctrl5 = float(analysis.get("top3_5d_ctrl_pct", 0.0) or 0.0)
    z5 = analysis.get("top3_5d_ctrl_z")
    z_tip_html = build_z_tooltip_html(stock_title, ctrl5, z5)

    raw_status_html = analysis.get("status_html", "N/A")
    top3_period_net = int(analysis.get("top3_period_net", 0))
    if "吃貨中" in raw_status_html:
        if is_chip_complete and conv_score is not None and conv_score <= bear_max:
            display_status_html = f'🟡 區間逆勢承接（{_span_num(top3_period_net)}｜整體籌碼偏發散）'
        elif z5 is not None and abs(z5) < 1.0:
            display_status_html = f'🟡 區間小幅買超（{_span_num(top3_period_net)}｜屬日常波動）'
        else:
            display_status_html = raw_status_html
    else:
        display_status_html = raw_status_html

    wave_agg_info = analysis.get("wave_agg_info")
    c1, c2, c3 = st.columns([2.3, 2.2, 1.2])
    with c1:
        sub_lines_c1 = list(analysis.get("main_force_sub_lines", []))
        if not has_broker_today:
            sub_lines_c1.append(f'<span style="color:#fbbf24;">⏳ 尚不含 {valid_anchor_date} 未公布之分點明細</span>')
        sub_html = "".join([f'<div class="card-sub">{line}</div>' for line in sub_lines_c1])
        st.markdown(f'<div class="summary-card"><div class="card-label">近 60 日最大波段分點主力（已濾除隔日沖）</div><div class="card-value">{analysis.get("main_force_html", "N/A")}</div>{sub_html}</div>', unsafe_allow_html=True)
    with c2:
        if not has_broker_today:
            st.markdown(
                f'<div class="summary-card">'
                f'  <div class="card-label"><span>區間內三大波段主力狀態</span><span class="tip-wrap tip-down">ⓘ<span class="tip-box tip-box-wide">{z_tip_html}</span></span></div>'
                f'  <div class="card-value" style="color:#94a3b8;">⏳ {valid_anchor_date} 主力分點待公布</div>'
                f'  <div class="card-sub">{analysis.get("status_sub", "")}<br>待今日分點明細公布後自動更新，或點擊上方「{prev_btn_name}」檢視前日狀態</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
        else:
            flt5 = analysis.get("top3_5d_float_pct")
            extra_stat_parts = [f"近5日控盤 {ctrl5:+.1f}%"]
            if z5 is not None:
                extra_stat_parts.append(f"Z:{z5:+.1f}σ")
            if flt5 is not None:
                extra_stat_parts.append(f"佔股本 {flt5:+.2f}%")
            wave_agg_line = ""
            if isinstance(wave_agg_info, dict):
                w20_n = int(wave_agg_info.get("net_20d", 0) or 0)
                w20_c = float(wave_agg_info.get("ctrl_20d_pct", 0.0) or 0.0)
                w_cnt = int(wave_agg_info.get("broker_count", 0) or 0)
                wave_agg_line = f'<br>🌐 全體波段({w_cnt}家) 20日 {_span_num(w20_n)}（控盤 {_span_ctrl(w20_c)}）'
            status_sub_html = f'<div class="card-sub">{analysis.get("status_sub", "")}<br><span style="color:#cbd5e1;">{" ｜ ".join(extra_stat_parts)}</span>{wave_agg_line}</div>'
            st.markdown(f'<div class="summary-card"><div class="card-label"><span>區間內三大波段主力狀態</span><span class="tip-wrap tip-down">ⓘ<span class="tip-box tip-box-wide">{z_tip_html}</span></span></div><div class="card-value">{display_status_html}</div>{status_sub_html}</div>', unsafe_allow_html=True)
    with c3:
        chip_dt_note = f"籌碼日：{valid_anchor_date}" if is_chip_complete else f"籌碼日：{valid_anchor_date}（結算中）"
        st.markdown(f'<div class="summary-card"><div class="card-label">{display_close_label}</div><div class="card-value">{display_close:.2f} 元</div><div class="card-sub">{pct_prefix}{pct_html}<br>{chip_dt_note}</div></div>', unsafe_allow_html=True)

    # --- 投信獨立追蹤列（今日未公布時自動沿用前一日統計並標記待公布，防連買賣歸零） ---
    trust_info = analysis.get("trust_info")
    if trust_info:
        trust_tip = (
            "投信下單分散於各大本土券商法人專戶中，其買賣流向已反映於分點明細與全市場集中度；"
            "為避免重複計權，本區採獨立追蹤（不計入籌碼健康總分），專供監控投信連買賣動能與真實持股水位"
            "（0.5%～5% 且 5 日／20 日同步買超時為認養加碼期，10% 以上須留意結帳賣壓）。"
        )
        t_is_pending = bool(trust_info.get("is_pending", False))
        t_day_word = "前日" if t_is_pending else day_word
        t_pend_dt = str(trust_info.get("pending_date", ""))
        t_eff_dt = str(trust_info.get("date", ""))
        t_pend_short = t_pend_dt[5:].replace("-", "/") if len(t_pend_dt) >= 10 else t_pend_dt
        t_eff_short = t_eff_dt[5:].replace("-", "/") if len(t_eff_dt) >= 10 else t_eff_dt
        pending_pill = (
            f'<span class="trust-tag" style="color:#fcd34d; background:rgba(245,158,11,0.16); border:1px solid rgba(245,158,11,0.45);">'
            f'⏳ {t_pend_short} 投信待公布（沿用 {t_eff_short}）</span>'
            if t_is_pending else ""
        )

        phase = trust_info.get("phase") or {}
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
        t_col, t_bg, t_bd = tier_style_map.get(phase.get("tier_level", "info"), tier_style_map["info"])
        m_col, m_bg, m_bd = mom_style_map.get(phase.get("momentum_level", "info"), mom_style_map["info"])
        tier_pill = f'<span class="trust-tag" style="color:{t_col}; background:{t_bg}; border:1px solid {t_bd};">{phase.get("tier_label", "")}</span>' if phase.get("tier_label") else ""
        mom_pill = f'<span class="trust-tag" style="color:{m_col}; background:{m_bg}; border:1px solid {m_bd};">{phase.get("momentum", "")}</span>' if phase.get("momentum") else ""
        cost_seg = ""
        if trust_info.get("cost", 0) > 0 and trust_info.get("pnl_pct") is not None:
            p_val = trust_info["pnl_pct"]
            p_col = "#ef5350" if p_val > 0 else ("#26a69a" if p_val < 0 else "#cbd5e1")
            cost_seg = f'<span><span class="trust-lbl">60日均價</span><b style="color:#ffb74d;">{trust_info["cost"]:,.2f}</b> (<span style="color:{p_col}; font-weight:700;">{p_val:+.1f}%</span>)</span>'

        st.markdown(
            f'<div class="trust-bar">'
            f'  <div class="trust-title">🏦 投信獨立追蹤<span class="tip-wrap tip-right">ⓘ<span class="tip-box">{trust_tip}</span></span>{pending_pill}{tier_pill}{mom_pill}</div>'
            f'  <div class="trust-metrics-wrap">'
            f'    <div class="trust-primary"><span>{_span_streak(trust_info["streak"])}</span><span style="color:#475569;">｜</span><span><span class="trust-lbl">近 5 日</span>{_span_num(trust_info.get("d5", 0))}</span></div>'
            f'    <div class="trust-secondary"><span><span class="trust-lbl">{t_day_word}</span>{_span_num(trust_info["d1"])}</span><span><span class="trust-lbl">區間</span>{_span_num(trust_info["period"])}</span><span><span class="trust-lbl">近 20 日</span>{_span_num(trust_info["d20"])}</span><span><span class="trust-lbl">今年</span>{_span_num(trust_info["ytd"])}</span><span><span class="trust-lbl">控盤</span>{_span_ctrl(trust_info["ctrl_pct"])}</span>{cost_seg}</div>'
            f'  </div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # --- 四大輔助籌碼儀表板 ---
    st.markdown(
        _render_aux_dashboard(
            analysis.get("breadth_info"),
            analysis.get("margin_info"),
            analysis.get("sbl_info"),
            analysis.get("tdcc_info"),
            day_word=day_word,
        ),
        unsafe_allow_html=True,
    )

    # --- 全市場前 15 大籌碼集中度橫條 ---
    conc_tip = (
        "計算公式：（該區間全市場前 15 大買超分點總張數 － 前 15 大賣超分點總張數）÷ 區間總成交量 × 100%。"
        "採區間歸戶重排口徑，並自動保存每日官方快照計算過去 60 個交易日的滾動 Z-Score 標準分數。"
    )
    st.markdown(
        f'<div class="conc-panel">'
        f'  <div class="conc-header"><div class="conc-title">📊 全市場前 15 大籌碼集中度<span class="tip-wrap tip-right">ⓘ<span class="tip-box">{conc_tip}</span></span><span class="conc-sub-tag">短中線全市場視角（基準日：{valid_anchor_date}）</span></div></div>'
        f'  <div class="conc-grid">{_format_conc_card("近 5 日集中度", concentrations.get("d5", {}))}{_format_conc_card("近 10 日集中度", concentrations.get("d10", {}))}{_format_conc_card("近 20 日集中度", concentrations.get("d20", {}))}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # --- 籌碼結構 5 大狀態判讀矩陣 ---
    matched_id = scenario.get("id", 0) if is_chip_complete else 0
    with st.expander("📖 籌碼結構 5 大狀態判讀矩陣（100% 籌碼｜趨勢 60% ＋ 動能 40%）", expanded=False):
        m_rows = []
        for item in SCENARIO_MATRIX:
            is_match = (item["id"] == matched_id)
            row_cls, badge = "", ""
            if is_match:
                row_cls = "matrix-active-bull" if item["type"] == "bull" else ("matrix-active-bear" if item["type"] == "bear" else "matrix-active-neutral")
                badge = '<br><span style="display:inline-block; margin-top:4px; padding:1px 6px; border-radius:4px; background:#f59e0b; color:#0f172a; font-weight:700; font-size:0.75rem;">👈 目前符合</span>'
            aux_col_html = item.get("aux_cond", "")
            if item.get("price_cond"):
                aux_col_html = f'{aux_col_html}<br><span style="color:#94a3b8;">{item["price_cond"]}</span>' if aux_col_html else item["price_cond"]
            m_rows.append(
                f'<tr class="{row_cls}">'
                f'  <td style="width:17%; font-weight:700;">{item["name"]}{badge}</td>'
                f'  <td style="width:19%;">{item.get("main_cond", "")}</td>'
                f'  <td style="width:19%;">{item.get("conc_cond", "")}</td>'
                f'  <td style="width:20%;">{aux_col_html}</td>'
                f'  <td style="width:25%;">{item.get("action", "")}</td>'
                f'</tr>'
            )
        st.markdown(f'<table class="matrix-table"><thead><tr><th>籌碼結構狀態</th><th>全體波段分點合力（趨勢核心）</th><th>集中度與主力廣度</th><th>輔助籌碼與分數檢核</th><th>籌碼解讀與觀察重點</th></tr></thead><tbody>{"".join(m_rows)}</tbody></table>', unsafe_allow_html=True)

    # --- 前三大波段券商分點主力明細、聰明錢雷達與排行榜 ---
    if has_broker_today:
        st.write("")
        if top3_names:
            top3_net_val = analysis.get("top3_period_net", 0)
            top3_ctrl_val = analysis.get("top3_period_ctrl_pct", 0.0)
            group_ctrl_tip = (
                "<b>波段主力定義：</b>採近 60 個交易日淨買超前 3 大純券商分點，並透過週轉係數與隔日反手率自動過濾高頻隔日沖分點。<br><br>"
                "<b>控盤率實戰標準：</b><br>"
                "• <b>絕對值 &lt; 2%：</b>影響力一般，屬正常換手。<br>"
                "• <b>絕對值 2% ～ 5%：</b>態度積極，或 Z-Score 達 ±1.0σ 以上。<br>"
                "• <b>絕對值 &gt; 5%：</b>高度控盤，波段籌碼高度集中於這批主力。"
            )
            cost_inline = f' <span style="color:#64748b; margin:0 6px;">｜</span><span style="color:#cbd5e1;">合力成本：</span><b style="color:#ffb74d;">{analysis["top3_cost"]:,.2f} 元</b>' if analysis.get("top3_cost", 0) > 0 else ""
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
            ctrl_tip_text = "計算公式：該分點本區間淨買賣超張數 ÷ 本區間總成交量。滑鼠移到分點名稱可查看該分點的今年累計張數、週轉係數與歷史 20 日前瞻勝率。"
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
                    f'  <div class="mf-cell"><span class="mf-lbl">{day_word}</span>{_span_num(r["d1"])}</div>'
                    f'  <div class="mf-cell"><span class="mf-lbl">近 10 日</span>{_span_num(r["d10"])}</div>'
                    f'  <div class="mf-cell"><span class="mf-lbl">近 20 日</span>{_span_num(r["d20"])}</div>'
                    f'  <div class="mf-cell" style="justify-content:center;">{_span_streak(r["streak"])}</div>'
                    f'  <div class="mf-cell"><span class="mf-lbl">控盤率<span class="tip-wrap tip-left">ⓘ<span class="tip-box">{ctrl_tip_text}</span></span></span>{_span_ctrl(r["ctrl_pct"])}</div>'
                    f'</div>'
                )
            st.markdown(f'<div class="mf-list">{"".join(rows_html)}</div>', unsafe_allow_html=True)

        if analysis.get("remark_md"):
            st.markdown(analysis["remark_md"])

        recent_10d_rising = analysis.get("recent_10d_rising") or {}
        if winner_brokers or recent_10d_rising:
            w_rows = "".join([
                f'<tr><td><b>🏆 {w["name"]}</b></td><td style="text-align:right; color:#fbbf24; font-weight:700;">{w["win_rate"]:.0f}% ({w["buy_signals"]}次)</td><td style="text-align:right; color:#ef5350;">{w["avg_fwd_ret"]:+.1f}%</td><td style="text-align:right;">{_span_num(w["net_5d"], "")}</td><td style="text-align:right;">{_span_num(w["net_60d"], "")}</td></tr>'
                for w in winner_brokers
            ]) or '<tr><td colspan="5" style="color:#94a3b8;">目前歷史樣本尚無同時滿足 20 日勝率 ≥ 60% 且均報 ≥ +3% 之獨立贏家分點</td></tr>'

            r10_rows = "".join([
                f'<tr><td style="width:24px; color:#94a3b8;">#{i+1}</td><td><b>{k}</b>{" 🏆" if any("贏家" in t for t in broker_tags.get(k, [])) else ""}</td><td style="text-align:right; color:#ef5350; font-weight:700;">{v:+,} 張</td></tr>'
                for i, (k, v) in enumerate(recent_10d_rising.items())
            ]) or '<tr><td colspan="3" style="color:#94a3b8;">近 10 日無明顯波段新進買超分點</td></tr>'

            st.markdown(
                f'<div class="smart-grid">'
                f'  <div class="smart-panel"><div class="smart-title"><span>🏆 歷史高勝率「贏家分點」雷達（獨立事件 20 日回測）</span><span style="font-size:0.74rem; color:#94a3b8;">已排除隔日沖</span></div><table class="rank-table"><thead><tr><th>分點名稱</th><th style="text-align:right;">20日勝率</th><th style="text-align:right;">均報酬</th><th style="text-align:right;">近5日(張)</th><th style="text-align:right;">近60日(張)</th></tr></thead><tbody>{w_rows}</tbody></table></div>'
                f'  <div class="smart-panel"><div class="smart-title"><span>🚀 近 10 日新崛起波段分點（換手接棒追蹤）</span><span style="font-size:0.74rem; color:#94a3b8;">已排除隔日沖</span></div><table class="rank-table"><thead><tr><th colspan="3">近 10 日淨買超前 5 大波段分點</th></tr></thead><tbody>{r10_rows}</tbody></table></div>'
                f'</div>',
                unsafe_allow_html=True,
            )

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
                rank_mode = st.radio("右側兩欄口徑", options=["近 60 日波段", "今年累計"], index=0, horizontal=True, label_visibility="collapsed")

            right_buyers = top_buyers_60d if rank_mode == "近 60 日波段" else ytd_buyers
            right_sellers = top_sellers_60d if rank_mode == "近 60 日波段" else ytd_sellers
            right_lbl = "近 60 日" if rank_mode == "近 60 日波段" else "今年"

            short_dt = valid_anchor_date[5:] if len(valid_anchor_date) >= 10 else valid_anchor_date
            col_tb, col_ts, col_yb, col_ys = st.columns(4)
            with col_tb:
                st.markdown(_render_rank_table(f"🔴 {day_word}分點買超前 5 大（{short_dt}）", today_buyers, "#ef5350", f"{day_word}無淨買超分點", broker_tags), unsafe_allow_html=True)
            with col_ts:
                st.markdown(_render_rank_table(f"🟢 {day_word}分點賣超前 5 大（{short_dt}）", today_sellers, "#26a69a", f"{day_word}無淨賣超分點", broker_tags), unsafe_allow_html=True)
            with col_yb:
                st.markdown(_render_rank_table(f"🔴 {right_lbl}分點買超前 5 大", right_buyers, "#ef5350", f"{right_lbl}無淨買超分點", broker_tags), unsafe_allow_html=True)
            with col_ys:
                st.markdown(_render_rank_table(f"🟢 {right_lbl}分點賣超前 5 大", right_sellers, "#26a69a", f"{right_lbl}無淨賣超分點", broker_tags), unsafe_allow_html=True)
    else:
        st.info(f"⏳ {valid_anchor_date} 主力分點明細尚未公布，待盤後分點資料更新後將自動顯示當日主力明細與買賣超排行榜；可先點擊上方「{prev_btn_name}」檢視前一日完整明細。")

    st.divider()

    # =====================================================================
    # 區塊二：股價（單色折線＋紅綠波段背景區）＋ 三合一可切換副圖
    # =====================================================================
    st.markdown(f"#### 📉 {stock_title} 股價、籌碼波段燈號與主力 VWAP 成本線走勢")

    available_brokers = [c for c in df_chips_full.columns if c != "date"]
    wave_broker_names = [n for n in (analysis.get("wave_broker_names") or []) if n in available_brokers]
    default_selected = [n for n in top3_names if n in available_brokers] or available_brokers[:1]
    winner_names = [w["name"] for w in winner_brokers if w["name"] in available_brokers]

    ms_key = f"ms_{target_stock}"
    if ms_key not in st.session_state:
        st.session_state[ms_key] = default_selected
    else:
        cleaned_sel = [b for b in st.session_state[ms_key] if b in available_brokers]
        if cleaned_sel != st.session_state[ms_key]:
            st.session_state[ms_key] = cleaned_sel or default_selected

    col_sel, col_btn0, col_btn1, col_btn2 = st.columns([3.2, 1.15, 1.05, 1.05], vertical_alignment="bottom")
    with col_btn0:
        if st.button("🌐 全體波段分點", width="stretch", disabled=not bool(wave_broker_names), help="一鍵帶入全部追蹤波段分點（已排除隔日沖與投信，避免與本土分點重複計數）"):
            st.session_state[ms_key] = wave_broker_names
            st.rerun()
    with col_btn1:
        if st.button("⚡ 三大波段主力", width="stretch", disabled=not bool(top3_names)):
            st.session_state[ms_key] = [n for n in top3_names if n in available_brokers]
            st.rerun()
    with col_btn2:
        if st.button("🏆 帶入贏家分點", width="stretch", disabled=not bool(winner_names)):
            st.session_state[ms_key] = winner_names[:3]
            st.rerun()
    with col_sel:
        selected_brokers = st.multiselect(
            "選擇要觀察的券商分點或「投信」（預設為近 60 日三大波段主力；點右側可一鍵切換全體波段分點）：",
            options=available_brokers,
            key=ms_key,
        )

    df_health = _compute_daily_health_cached(
        stock_id=target_stock,
        cache_key=cache_key,
        _df_price_full=df_price_full,
        _df_chips_full=df_chips_full,
        _df_aux_full=df_aux_full,
        _tdcc_history=tdcc_history_saved,
        issued_lots=issued_lots_saved,
    )
    has_health_series = (not df_health.empty) and df_health["health_score"].notna().any()
    has_aux_series = (
        df_aux_full is not None
        and (
            ("margin_bal" in df_aux_full.columns and df_aux_full["margin_bal"].notna().any())
            or ("sbl_bal" in df_aux_full.columns and df_aux_full["sbl_bal"].notna().any())
        )
    )

    active_bull_in = int(st.session_state.get("bt_long_entry_std", bull_min))
    active_bull_out = min(int(st.session_state.get("bt_long_exit_std", long_exit_default)), active_bull_in)
    active_bear_in = int(st.session_state.get("bt_short_entry_std", bear_max))
    active_bear_out = max(int(st.session_state.get("bt_short_exit_std", short_exit_default)), active_bear_in)

    c_opt1, c_opt2, c_opt3 = st.columns([1.2, 2.3, 1.7])
    with c_opt1:
        show_ma = st.checkbox("顯示 10/20MA (純參考)", value=False, help="均線僅作主圖視覺對照，已完全排除於籌碼評分與回測之外")
    with c_opt2:
        show_health_regime = st.checkbox(
            f"籌碼波段燈號 (多≥{active_bull_in}/<{active_bull_out} · 空≤{active_bear_in}/>{active_bear_out})",
            value=bool(has_health_series),
            disabled=not bool(has_health_series),
            help=f"模式 B 波段持倉區塊，與下方回測規則 100% 同步：站上 ≥{active_bull_in} 分持續亮紅底至跌破 <{active_bull_out} 分；跌破 ≤{active_bear_in} 分持續亮綠底至站上 >{active_bear_out} 分。",
        )
    with c_opt3:
        cost_basis = st.radio("成本線計算基準", options=["近 60 日波段成本", "今年累計成本"], index=0, horizontal=True, label_visibility="collapsed")

    if st.session_state["subchart_mode"] == "score" and not has_health_series:
        st.session_state["subchart_mode"] = "flow"
    elif st.session_state["subchart_mode"] == "credit" and not has_aux_series:
        st.session_state["subchart_mode"] = "flow"
    active_sub = st.session_state["subchart_mode"]

    if not selected_brokers:
        st.warning("請至少選擇一個券商分點或「投信」來繪製圖表。")
    else:
        price_cols = [c for c in ["date", "open", "high", "low", "close", "vwap", "adj_factor"] if c in df_price_full.columns]
        df_plot = df_price_full[price_cols].merge(df_chips_full, on="date", how="left")
        chip_merge_cols = [c for c in df_chips_full.columns if c != "date"]
        if chip_merge_cols:
            df_plot[chip_merge_cols] = df_plot[chip_merge_cols].fillna(0.0)

        if has_health_series:
            df_plot = df_plot.merge(df_health[["date", "health_score", "sub_type"]], on="date", how="left")
        else:
            df_plot["health_score"], df_plot["sub_type"] = float("nan"), ""
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

        is_all_wave_selected = bool(wave_broker_names) and set(selected_brokers) == set(wave_broker_names)
        if len(selected_brokers) == 1:
            target_label = selected_brokers[0]
        elif is_all_wave_selected:
            target_label = f"全體波段分點({len(selected_brokers)}家)合力"
        else:
            target_label = f"所選 {len(selected_brokers)} 個分點合力"

        latest_full_close = float(df_plot.iloc[-1]["close"])
        cost_mode_tag = "近 60 日 VWAP 成本" if win_days else "今年累計 VWAP 成本"

        if latest_cost > 0 and latest_inv > 0:
            sel_pnl = (latest_full_close - latest_cost) / latest_cost * 100.0
            pnl_color = "#ef5350" if sel_pnl > 0 else ("#26a69a" if sel_pnl < 0 else "#cbd5e1")
            st.markdown(
                f'<div class="cost-banner">💰 <b>{target_label}</b> {cost_mode_tag}（純視覺參考）：'
                f'<b style="color:#ffb74d;">{latest_cost:,.2f} 元</b> ｜ '
                f'區間剩餘庫存：<b>{int(round(latest_inv)):,} 張</b> ｜ '
                f'對照最新有效收盤價 ({latest_full_close:.2f} 元) 帳面損益：'
                f'<b style="color:{pnl_color};">{sel_pnl:+.2f}%</b></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(f'<div class="cost-banner">💰 <b>{target_label}</b> 在「{cost_basis}」基準下剩餘庫存已歸零（無持有庫存）</div>', unsafe_allow_html=True)

        fig = make_subplots(
            rows=2,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.145,
            row_heights=[0.56, 0.44],
            specs=[[{"secondary_y": True}], [{"secondary_y": True}]],
        )

        # --- Row 1：單色收盤價折線、選配 10/20MA、主力成本線與波段紅綠背景區塊 ---
        if show_ma:
            fig.add_trace(go.Scatter(x=df_plot["date"], y=df_plot["ma10"], name="10MA (純參考)", legend="legend", line=dict(color="rgba(56, 189, 248, 0.78)", width=1.3), hovertemplate="%{y:,.2f} 元<extra>10MA</extra>"), row=1, col=1, secondary_y=False)
            fig.add_trace(go.Scatter(x=df_plot["date"], y=df_plot["ma20"], name="20MA (純參考)", legend="legend", line=dict(color="rgba(192, 132, 252, 0.85)", width=1.5), hovertemplate="%{y:,.2f} 元<extra>20MA</extra>"), row=1, col=1, secondary_y=False)

        if cost_series.notna().any():
            fig.add_trace(go.Scatter(x=df_plot["date"], y=cost_series, name=f"{target_label} 成本線", legend="legend", line=dict(color="#FF9100", width=2.2, dash="dash"), connectgaps=False, hovertemplate="%{y:,.2f} 元<extra>主力成本均價</extra>"), row=1, col=1, secondary_y=False)

        if show_health_regime and has_health_series:
            regime_bands, regime_list = _compute_health_regimes(
                df_plot["health_score"],
                long_entry=active_bull_in,
                long_exit=active_bull_out,
                short_entry=active_bear_in,
                short_exit=active_bear_out,
            )
            reg_label_map = {"bull": "🔴 波段偏多", "bear": "🟢 波段偏空", "neutral": "🟡 空手觀望"}
            price_hover_texts = [
                f"{cl_v:,.2f} 元 ｜ 籌碼 {int(round(sc_v))} 分（{reg_label_map.get(reg_v, '')}）"
                if pd.notna(sc_v)
                else (f"{cl_v:,.2f} 元 ｜ ⏳ 籌碼結算中" if st_v == "結算中" else f"{cl_v:,.2f} 元")
                for cl_v, sc_v, reg_v, st_v in zip(df_plot["close"], df_plot["health_score"], regime_list, df_plot["sub_type"].fillna(""))
            ]
            fig.add_trace(
                go.Scatter(
                    x=df_plot["date"],
                    y=df_plot["close"],
                    name="收盤價 (元)",
                    legend="legend",
                    line=dict(color="#FFD700", width=2.6),
                    text=price_hover_texts,
                    hovertemplate="%{text}<extra>收盤／籌碼</extra>",
                ),
                row=1, col=1, secondary_y=False,
            )
            first_dt = df_plot["date"].iloc[0]
            fig.add_trace(
                go.Scatter(
                    x=[first_dt],
                    y=[None],
                    mode="markers",
                    marker=dict(symbol="square", size=10, color="rgba(239, 83, 80, 0.55)"),
                    name=f"波段偏多區 (≥{active_bull_in}/<{active_bull_out})",
                    legend="legend",
                    hoverinfo="skip",
                ),
                row=1, col=1, secondary_y=False,
            )
            fig.add_trace(
                go.Scatter(
                    x=[first_dt],
                    y=[None],
                    mode="markers",
                    marker=dict(symbol="square", size=10, color="rgba(38, 166, 154, 0.55)"),
                    name=f"波段偏空區 (≤{active_bear_in}/>{active_bear_out})",
                    legend="legend",
                    hoverinfo="skip",
                ),
                row=1, col=1, secondary_y=False,
            )
            for b_start, b_end, b_reg in regime_bands:
                band_color = "rgba(239, 83, 80, 0.13)" if b_reg == "bull" else "rgba(38, 166, 154, 0.13)"
                fig.add_vrect(x0=b_start - 0.5, x1=b_end + 0.5, fillcolor=band_color, line_width=0, layer="below", row=1, col=1)
        else:
            price_hover_texts = [
                f"{cl_v:,.2f} 元 ｜ 籌碼 {int(round(sc_v))} 分（{st_v}）"
                if pd.notna(sc_v)
                else (f"{cl_v:,.2f} 元 ｜ ⏳ 籌碼結算中" if st_v == "結算中" else f"{cl_v:,.2f} 元")
                for cl_v, sc_v, st_v in zip(df_plot["close"], df_plot["health_score"], df_plot["sub_type"].fillna(""))
            ]
            fig.add_trace(go.Scatter(x=df_plot["date"], y=df_plot["close"], name="收盤價 (元)", legend="legend", line=dict(color="#FFD700", width=2.6), text=price_hover_texts, hovertemplate="%{text}<extra>收盤價</extra>"), row=1, col=1, secondary_y=False)

        fig.update_yaxes(row=1, col=1, secondary_y=True, visible=False)
        fig.update_yaxes(title_text="股價／成本 (元)", row=1, col=1, secondary_y=False, showgrid=True)

        # --- Row 2：依切換按鈕顯示「籌碼分數走勢」／「主力買賣超」／「融資與借券餘額」 ---
        if active_sub == "score" and has_health_series:
            n_rows = len(df_plot)
            fig.add_hrect(y0=active_bull_in, y1=100, fillcolor="rgba(239, 83, 80, 0.08)", line_width=0, layer="below", row=2, col=1)
            fig.add_hrect(y0=0, y1=active_bear_in, fillcolor="rgba(38, 166, 154, 0.08)", line_width=0, layer="below", row=2, col=1)
            fig.add_trace(
                go.Scatter(x=df_plot["date"], y=df_plot["health_score"], name="籌碼健康分數", legend="legend2", line=dict(color="#38bdf8", width=2.5), connectgaps=False, hovertemplate="%{y:.0f} 分<extra>籌碼分數</extra>"),
                row=2, col=1, secondary_y=False,
            )
            threshold_lines = [
                (active_bull_in, f"做多進場 (≥{active_bull_in})", "rgba(239, 83, 80, 0.85)", "dash"),
                (active_bull_out, f"做多出場 (<{active_bull_out})", "rgba(251, 146, 60, 0.80)", "dot"),
                (active_bear_in, f"放空進場 (≤{active_bear_in})", "rgba(38, 166, 154, 0.85)", "dash"),
                (active_bear_out, f"放空出場 (>{active_bear_out})", "rgba(250, 204, 21, 0.78)", "dot"),
            ]
            for th_val, th_name, th_col, th_dash in threshold_lines:
                fig.add_trace(
                    go.Scatter(x=df_plot["date"], y=[th_val] * n_rows, name=th_name, legend="legend2", line=dict(color=th_col, width=1.35, dash=th_dash), hoverinfo="skip"),
                    row=2, col=1, secondary_y=False,
                )
            fig.update_yaxes(title_text="籌碼分數", range=[0, 102], dtick=20, row=2, col=1, secondary_y=False, showgrid=True)
            fig.update_yaxes(row=2, col=1, secondary_y=True, visible=False)

        elif active_sub == "credit" and has_aux_series:
            if "margin_bal" in df_plot.columns and df_plot["margin_bal"].notna().any():
                fig.add_trace(go.Scatter(x=df_plot["date"], y=df_plot["margin_bal"], name="融資餘額 (張)", legend="legend2", line=dict(color="#f59e0b", width=2.2), connectgaps=True, hovertemplate="%{y:,.0f} 張<extra>融資餘額</extra>"), row=2, col=1, secondary_y=False)
            if "sbl_bal" in df_plot.columns and df_plot["sbl_bal"].notna().any():
                fig.add_trace(go.Scatter(x=df_plot["date"], y=df_plot["sbl_bal"], name="借券賣出餘額 (張)", legend="legend2", line=dict(color="#a78bfa", width=2.2, dash="dot"), connectgaps=True, hovertemplate="%{y:,.0f} 張<extra>借券賣出餘額</extra>"), row=2, col=1, secondary_y=True)
            fig.update_yaxes(title_text="融資餘額 (張)", row=2, col=1, secondary_y=False, showgrid=True, tickformat=",.0f")
            fig.update_yaxes(title_text="借券賣出 (張)", row=2, col=1, secondary_y=True, showgrid=False, tickformat=",.0f")

        else:
            bar_colors = ["#ef5350" if v >= 0 else "#26a69a" for v in daily_net]
            bar_label = f"{target_label} 每日買賣超"
            fig.add_trace(go.Bar(x=df_plot["date"], y=daily_net, name=f"{bar_label} (張)", legend="legend2", marker_color=bar_colors, opacity=0.85, hovertemplate="%{y:+,.0f} 張<extra>" + bar_label + "</extra>"), row=2, col=1, secondary_y=False)
            line_label = f"{target_label} 累計張數"
            fig.add_trace(go.Scatter(x=df_plot["date"], y=cum_total, name=f"{line_label} (累計張)", legend="legend2", line=dict(color="#00E5FF", width=2.4), hovertemplate="%{y:+,.0f} 張<extra>" + line_label + "</extra>"), row=2, col=1, secondary_y=True)
            fig.add_hline(y=0, line_width=1, line_color="rgba(255, 255, 255, 0.25)", row=2, col=1)
            fig.update_yaxes(title_text="每日買賣超 (張)", row=2, col=1, secondary_y=False, showgrid=True, zeroline=True, tickformat=",.0f")
            fig.update_yaxes(title_text="統計張數 (累計張)", row=2, col=1, secondary_y=True, showgrid=False, tickformat=",.0f")

        date_list = df_plot["date"].tolist()
        if range_type not in ("全部", "今日") and start_filter != end_filter:
            match_indices = [i for i, d in enumerate(date_list) if start_filter <= d <= end_filter]
            if len(match_indices) >= 2:
                fig.add_vrect(x0=match_indices[0] - 0.5, x1=match_indices[-1] + 0.5, fillcolor="rgba(255, 255, 255, 0.06)", line_width=1, line_dash="dot", line_color="rgba(255, 255, 255, 0.28)")

        if (valid_anchor_date != latest_trade_date) and (valid_anchor_date in date_list):
            fig.add_vline(x=date_list.index(valid_anchor_date), line_width=1.4, line_dash="dash", line_color="rgba(251, 191, 36, 0.75)")

        n_pts = len(df_plot)
        tick_step = max(1, n_pts // 10)
        tick_indices = list(range(0, n_pts, tick_step))
        if (n_pts - 1) not in tick_indices:
            if tick_indices and ((n_pts - 1) - tick_indices[-1]) < max(3, tick_step * 0.5):
                tick_indices[-1] = n_pts - 1
            else:
                tick_indices.append(n_pts - 1)
        tick_vals = [df_plot["date"].iloc[i] for i in tick_indices]
        tick_texts = [d[5:].replace("-", "/") for d in tick_vals]
        fig.update_xaxes(type="category", tickmode="array", tickvals=tick_vals, ticktext=tick_texts, showgrid=False)

        d1_low = float(fig.layout.yaxis.domain[0]) if (fig.layout.yaxis and fig.layout.yaxis.domain) else 0.49
        d2_low = float(fig.layout.yaxis3.domain[0]) if (fig.layout.yaxis3 and fig.layout.yaxis3.domain) else 0.0

        fig.update_layout(
            height=720,
            template="plotly_dark",
            hovermode="x unified",
            margin=dict(l=20, r=20, t=28, b=90),
            legend=dict(orientation="h", yanchor="top", y=d1_low - 0.015, xanchor="left", x=0, bgcolor="rgba(0,0,0,0)"),
            legend2=dict(orientation="h", yanchor="top", y=d2_low - 0.075, xanchor="left", x=0, bgcolor="rgba(0,0,0,0)"),
        )

        st.plotly_chart(fig, width="stretch")

        # --- 下方副圖三合一快速切換按鈕列（置於副圖正下方） ---
        sub_lbl_col, sub_b1, sub_b2, sub_b3, _ = st.columns([1.0, 1.3, 1.2, 1.35, 1.5])
        with sub_lbl_col:
            st.markdown('<div class="snap-bar-label">🔀 副圖切換：</div>', unsafe_allow_html=True)
        with sub_b1:
            if st.button("📈 籌碼分數走勢", key="btn_sub_score", type="primary" if active_sub == "score" else "secondary", disabled=not bool(has_health_series), width="stretch"):
                st.session_state["subchart_mode"] = "score"
                st.rerun()
        with sub_b2:
            if st.button("📊 主力買賣超", key="btn_sub_flow", type="primary" if active_sub == "flow" else "secondary", width="stretch"):
                st.session_state["subchart_mode"] = "flow"
                st.rerun()
        with sub_b3:
            if st.button("💳 融資／借券餘額", key="btn_sub_credit", type="primary" if active_sub == "credit" else "secondary", disabled=not bool(has_aux_series), width="stretch"):
                st.session_state["subchart_mode"] = "credit"
                st.rerun()

    # =====================================================================
    # 區塊三：100% 籌碼波段進出模擬回測與門檻敏感度對照
    # =====================================================================
    if has_health_series:
        st.divider()
        col_bt_title, col_bt_toggle = st.columns([4.2, 1.15], vertical_alignment="bottom")
        with col_bt_title:
            st.markdown(f"#### 🧪 {stock_title} 籌碼波段進出模擬回測（次日開盤進出｜100% 不含技術面）")
        with col_bt_toggle:
            toggle_lbl = "🔼 收合績效總表" if st.session_state["show_bt_summary"] else "🔽 展開績效總表"
            if st.button(toggle_lbl, key="btn_toggle_bt_summary", width="stretch"):
                st.session_state["show_bt_summary"] = not st.session_state["show_bt_summary"]
                st.rerun()

        p_col1, p_col2, p_col3, p_col4, p_col5 = st.columns(5)
        with p_col1:
            bt_long_in = st.number_input("做多進場分數 (≥)", min_value=45, max_value=85, value=bull_min, step=5, key="bt_long_entry_std")
        with p_col2:
            bt_long_out = st.number_input("做多出場分數 (<)", min_value=25, max_value=70, value=long_exit_default, step=5, key="bt_long_exit_std")
        with p_col3:
            bt_short_in = st.number_input("放空進場分數 (≤)", min_value=15, max_value=50, value=bear_max, step=5, key="bt_short_entry_std")
        with p_col4:
            bt_short_out = st.number_input("放空出場分數 (>)", min_value=30, max_value=75, value=short_exit_default, step=5, key="bt_short_exit_std")
        with p_col5:
            round_trip_cost = st.number_input("來回交易成本 (%)", min_value=0.0, max_value=2.0, value=0.50, step=0.10, format="%.2f", help="單筆進出合計扣除之手續費與證交稅摩擦成本（預設 0.50%）", key="bt_cost_pct")

        sim_res = simulate_swing_backtest(
            df_price_full=df_price_full,
            df_health=df_health,
            long_entry=int(bt_long_in),
            long_exit=int(bt_long_out),
            short_entry=int(bt_short_in),
            short_exit=int(bt_short_out),
            round_trip_cost_pct=float(round_trip_cost),
        )

        for warn_msg in (sim_res.get("param_warnings") or []):
            st.warning(f"⚠️ 門檻設定提醒：{warn_msg}")

        if st.session_state["show_bt_summary"]:
            st.markdown(_render_swing_summary_html(sim_res), unsafe_allow_html=True)

        if sim_res.get("valid"):
            strats = sim_res["strategies"]
            with st.expander("📋 查看籌碼逐筆波段進出明細（含進出場日、次日開盤價、觸發分數與出場原因）", expanded=False):
                strat_options = sim_res["strategy_order"]
                sel_strat_key = st.radio(
                    "選擇檢視策略明細",
                    options=strat_options,
                    format_func=lambda k: f"{strats[k]['label']}（{strats[k]['trade_count']} 筆）",
                    index=0,
                    horizontal=True,
                    label_visibility="collapsed",
                    key=f"trade_log_sel_{target_stock}",
                )
                st.markdown(_render_trade_list_html(strats[sel_strat_key]["trades"]), unsafe_allow_html=True)
else:
    st.info("👈 請在左側輸入上市或上櫃股票代號（例如 `6488`、`2330`）新增建檔。")
