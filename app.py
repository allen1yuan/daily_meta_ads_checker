"""
Meta Ads Daily Checker — Streamlit app for a 3-7 day rolling Meta Ads
Manager export. Three levels: Campaign (budget allocation), Ad set
(performance + anomaly flags), Ad (creative fatigue / winners).

Run:  streamlit run app.py
"""

from __future__ import annotations

import io
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import analysis as az
import charts as ch

st.set_page_config(page_title="Meta Ads Daily Checker", page_icon="📊", layout="wide")

STATUS_LABELS = {
    "critical": "🔴 Critical", "warning": "🟠 Warning", "low_delivery": "⚪ Low delivery",
    "insufficient_history": "⬜ Insufficient history", "healthy": "🟢 Healthy",
}
STATUS_ORDER = ["critical", "warning", "low_delivery", "insufficient_history", "healthy"]


# ---------------------------------------------------------------------------
# Sidebar — data input + thresholds
# ---------------------------------------------------------------------------
st.sidebar.title("📊 Meta Ads Daily Checker")
st.sidebar.caption("For a rolling 3–7 day Ads Manager export.")

uploaded = st.sidebar.file_uploader("Upload export (.xlsx or .csv)", type=["xlsx", "xls", "csv"])
use_sample = st.sidebar.button(
    "Use bundled sample data", use_container_width=True,
    help="Loads a small synthetic example so you can see the app without uploading anything.",
)

st.sidebar.divider()
st.sidebar.subheader("Thresholds")
st.sidebar.caption("Defaults are reasonable starting points — tune them to your account.")
benchmark_roas = st.sidebar.number_input(
    "Benchmark / target ROAS", min_value=0.1, value=2.4, step=0.1,
    help="The ROAS level a healthy ad/campaign should hold. Set this from your own longer-run history "
         "(e.g. the converged ROAS from a full-year analysis) — it isn't derived from this short window.",
)
breakeven_roas = st.sidebar.number_input("Break-even ROAS", min_value=0.0, value=1.0, step=0.1)
refresh_ratio = st.sidebar.slider(
    "Refresh trigger (% of benchmark)", 0.3, 0.95, 0.7, 0.05,
    help="An ad/campaign below this fraction of the benchmark ROAS is flagged.",
)
min_window_spend = st.sidebar.number_input(
    "Minimum window spend to judge ($)", min_value=0.0, value=15.0, step=5.0,
    help="Below this total spend in the window, ROAS is treated as noise, not a verdict — "
         "flagged as 'low delivery' instead of 'critical'.",
)
min_active_days = st.sidebar.slider("Minimum active days to judge", 1, 7, 3)
st.sidebar.caption("Anomaly detection (ad sets)")
cpc_spike_pct = st.sidebar.slider(
    "CPC spike threshold (day-over-day)", 10, 200, 50, 5, format="+%d%%",
    help="Flag a day where CPC jumped more than this much vs. the previous active day.",
) / 100
ctr_drop_pct = st.sidebar.slider(
    "CTR drop threshold (day-over-day)", 10, 90, 40, 5, format="-%d%%",
    help="Flag a day where CTR fell more than this much vs. the previous active day.",
) / 100


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def _read_file(file_bytes: bytes, name: str) -> pd.DataFrame:
    buf = io.BytesIO(file_bytes)
    if name.lower().endswith(".csv"):
        return pd.read_csv(buf)
    return pd.read_excel(buf)


raw_df = None
source_label = None
if uploaded is not None:
    raw_df = _read_file(uploaded.getvalue(), uploaded.name)
    source_label = uploaded.name
elif use_sample or st.session_state.get("_use_sample"):
    st.session_state["_use_sample"] = True
    sample_path = os.path.join(os.path.dirname(__file__), "sample_data", "sample_meta_ads_export.xlsx")
    raw_df = pd.read_excel(sample_path)
    source_label = "bundled sample data (synthetic)"

if raw_df is None:
    st.title("Meta Ads Daily Checker")
    st.markdown(
        "Upload a Meta Ads Manager export covering the **last 3–7 days**, broken out by day "
        "(ideally with **Campaign name**, **Ad set name**, and **Ad name** columns all included — "
        "Meta lets you add all three as columns alongside a Day breakdown in one export). "
        "Or click **Use bundled sample data** in the sidebar to see the app with synthetic data first."
    )
    st.info(
        "**Expected columns** (exact header text can vary — the app matches common Meta export "
        "phrasings automatically): `Day`, `Campaign name`, `Ad set name`, `Ad name`, "
        "`Amount spent (USD)`, `Purchases`, `Purchases conversion value` (or a ROAS/ROI column), "
        "`Impressions`, `Link clicks`, `CTR`, `CPC`, `Reach`, `Frequency`."
    )
    st.stop()

try:
    df, resolved, notes = az.load_and_standardize(raw_df)
except az.ColumnResolutionError as e:
    st.error(str(e))
    st.stop()

st.title("Meta Ads Daily Checker")
date_min, date_max = df["day"].min().date(), df["day"].max().date()
span_days = (date_max - date_min).days + 1
st.caption(f"Source: **{source_label}** · Window: **{date_min} → {date_max}** ({span_days} days) · "
           f"{df['ad'].nunique()} ads · {df['adset'].nunique()} ad sets · {df['campaign'].nunique()} campaigns")

if span_days > 7:
    st.warning(
        f"This file spans {span_days} days — more than the 3–7 day window this app is tuned for. "
        "It will still run, but trend/anomaly thresholds were chosen for a short rolling window "
        "and may behave oddly (e.g. too many 'insufficient history' flags) on a much longer one."
    )
for n in notes:
    st.info(n)

# ---------------------------------------------------------------------------
# Top-line KPIs
# ---------------------------------------------------------------------------
kpi = az.topline_kpis(df)
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total spend", f"${kpi['spend']:,.0f}")
k2.metric("Total revenue", f"${kpi['revenue']:,.0f}")
k3.metric("Blended ROAS", f"{kpi['roas']:.2f}x" if pd.notna(kpi["roas"]) else "—",
          delta=f"{kpi['roas'] - breakeven_roas:+.2f}x vs break-even" if pd.notna(kpi["roas"]) else None)
k4.metric("Blended CPA", f"${kpi['cpa']:,.2f}" if pd.notna(kpi["cpa"]) else "—")
k5.metric("Purchases", f"{kpi['purchases']:,.0f}")

st.divider()

tab_campaign, tab_adset, tab_ad = st.tabs(
    ["📊 Campaign Overview", "🎯 Ad Set Diagnosis", "🎨 Ad Creative Performance"]
)

# ---------------------------------------------------------------------------
# TAB 1 — Campaign
# ---------------------------------------------------------------------------
with tab_campaign:
    st.subheader("Daily trend")
    daily_all = az.daily_rollup(df, [])
    c1, c2 = st.columns(2)
    c1.plotly_chart(ch.daily_spend_chart(daily_all), use_container_width=True)
    c2.plotly_chart(ch.daily_roas_chart(daily_all, breakeven_roas, benchmark_roas), use_container_width=True)

    st.subheader("Budget allocation recommendations")
    st.caption(
        "Each campaign's window is split into an early half and a late half; the late-half ROAS is "
        "compared to the campaign's own early-half ROAS and to the benchmark to recommend **Scale** "
        "(at/above benchmark, holding or rising), **Cut** (below break-even, or fell sharply), "
        "or **Maintain** (in between)."
    )
    camp_df = az.classify_campaigns(
        df, benchmark_roas=benchmark_roas, breakeven_roas=breakeven_roas,
        min_window_spend=min_window_spend, min_active_days=min_active_days,
    )
    if df["campaign"].nunique() <= 1:
        st.info("Only one campaign (or no Campaign name column) in this file — recommendation table shown below, chart skipped.")
    else:
        st.plotly_chart(ch.campaign_recommendation_chart(camp_df), use_container_width=True)

    rec_counts = camp_df["recommendation"].value_counts()
    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.metric("🟢 Scale", int(rec_counts.get("Scale", 0)))
    rc2.metric("🔵 Maintain", int(rec_counts.get("Maintain", 0)))
    rc3.metric("🔴 Cut", int(rec_counts.get("Cut", 0)))
    rc4.metric("⚪ Insufficient data", int(rec_counts.get("Insufficient data", 0)))

    st.dataframe(
        camp_df,
        use_container_width=True, hide_index=True,
        column_config={
            "campaign": st.column_config.TextColumn("Campaign"),
            "active_days": st.column_config.NumberColumn("Active days"),
            "spend": st.column_config.NumberColumn("Spend", format="$%.0f"),
            "revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
            "roas": st.column_config.NumberColumn("ROAS", format="%.2fx"),
            "cpa": st.column_config.NumberColumn("CPA", format="$%.2f"),
            "early_roas": st.column_config.NumberColumn("Early-window ROAS", format="%.2fx"),
            "late_roas": st.column_config.NumberColumn("Late-window ROAS", format="%.2fx"),
            "trend_pct": st.column_config.NumberColumn("Trend", format="%.0f%%"),
            "recommendation": st.column_config.TextColumn("Recommendation"),
            "rationale": st.column_config.TextColumn("Why", width="large"),
        },
    )

# ---------------------------------------------------------------------------
# TAB 2 — Ad set
# ---------------------------------------------------------------------------
with tab_adset:
    st.subheader("Ad set performance")
    anomalies = az.detect_adset_anomalies(df, cpc_spike_pct=cpc_spike_pct, ctr_drop_pct=ctr_drop_pct)
    adset_df = az.adset_summary(df, anomalies)

    st.dataframe(
        adset_df,
        use_container_width=True, hide_index=True,
        column_config={
            "adset": st.column_config.TextColumn("Ad set"),
            "active_days": st.column_config.NumberColumn("Active days"),
            "spend": st.column_config.NumberColumn("Spend", format="$%.0f"),
            "revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
            "purchases": st.column_config.NumberColumn("Purchases", format="%.0f"),
            "roas": st.column_config.NumberColumn("ROAS", format="%.2fx"),
            "cpa": st.column_config.NumberColumn("CPA", format="$%.2f"),
            "ctr": st.column_config.NumberColumn("CTR", format="%.2f%%"),
            "cpc": st.column_config.NumberColumn("CPC", format="$%.2f"),
            "cpm": st.column_config.NumberColumn("CPM", format="$%.2f"),
            "frequency": st.column_config.NumberColumn("Frequency", format="%.2f"),
            "impressions": None, "link_clicks": None, "reach": None,
            "anomaly_count": st.column_config.NumberColumn("⚠️ Anomalies"),
            "anomaly_types": st.column_config.TextColumn("Anomaly type(s)"),
        },
    )

    st.subheader("Anomaly detection")
    st.caption(
        f"Day-over-day CPC spikes (> +{cpc_spike_pct:.0%}) and CTR drops (> -{ctr_drop_pct:.0%}) versus the "
        "previous active day — a direct percent-change check rather than a z-score, since 3–7 points isn't "
        "enough to estimate a stable standard deviation. Days under $5 spend are skipped as comparison points "
        "so a jump from $1 to $3 of spend doesn't register as a 'spike.'"
    )
    if len(anomalies) == 0:
        st.success("No day-over-day CPC spikes or CTR drops detected at the current thresholds.")
    else:
        st.dataframe(
            anomalies.sort_values("day", ascending=False),
            use_container_width=True, hide_index=True,
            column_config={
                "adset": st.column_config.TextColumn("Ad set"),
                "day": st.column_config.DateColumn("Day"),
                "type": st.column_config.TextColumn("Type"),
                "detail": st.column_config.TextColumn("Detail"),
            },
        )

    flagged_adsets = adset_df[adset_df["anomaly_count"] > 0]["adset"].tolist()
    if flagged_adsets:
        st.subheader("Flagged ad set trend")
        pick = st.selectbox("Ad set", flagged_adsets)
        daily_pick = az.daily_rollup(df[df["adset"] == pick], []).sort_values("day")
        cc1, cc2 = st.columns(2)
        f1 = go.Figure()
        f1.add_trace(go.Scatter(x=daily_pick["day"], y=daily_pick["cpc"], mode="lines+markers",
                                 line=dict(color=ch.CAT["orange"], width=2.5)))
        f1.update_layout(**ch.BASE_LAYOUT, title=f"CPC — {pick}", yaxis_title="CPC ($)", height=300)
        f2 = go.Figure()
        f2.add_trace(go.Scatter(x=daily_pick["day"], y=daily_pick["ctr"], mode="lines+markers",
                                 line=dict(color=ch.CAT["aqua"], width=2.5)))
        f2.update_layout(**ch.BASE_LAYOUT, title=f"CTR — {pick}", yaxis_title="CTR (%)", height=300)
        cc1.plotly_chart(f1, use_container_width=True)
        cc2.plotly_chart(f2, use_container_width=True)

# ---------------------------------------------------------------------------
# TAB 3 — Ad
# ---------------------------------------------------------------------------
with tab_ad:
    st.subheader("Creative performance — fatigue and winners")
    st.caption(
        "Status is checked for **data sufficiency before performance**: an ad with too little history or "
        "too little spend this window is flagged as such, not judged as good or bad. Only ads with enough "
        "signal are classified against the ROAS benchmark."
    )
    ads_df = az.classify_ads(
        df, benchmark_roas=benchmark_roas, breakeven_roas=breakeven_roas,
        min_window_spend=min_window_spend, min_active_days=min_active_days,
        refresh_ratio=refresh_ratio,
    )
    status_counts = ads_df["status"].value_counts().to_dict()
    st.plotly_chart(
        ch.status_count_chart(status_counts, STATUS_ORDER, STATUS_LABELS, ch.STATUS_COLORS),
        use_container_width=True,
    )

    display_df = ads_df.copy()
    display_df["status_label"] = display_df["status"].map(STATUS_LABELS)

    st.dataframe(
        display_df[["ad", "status_label", "active_days", "spend", "revenue", "roas",
                     "roas_trend", "ctr_trend", "spend_trend_ratio", "delivery_declining", "recommendation"]],
        use_container_width=True, hide_index=True,
        column_config={
            "ad": st.column_config.TextColumn("Ad", width="medium"),
            "status_label": st.column_config.TextColumn("Status"),
            "active_days": st.column_config.NumberColumn("Active days"),
            "spend": st.column_config.NumberColumn("Spend", format="$%.0f"),
            "revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
            "roas": st.column_config.NumberColumn("ROAS", format="%.2fx"),
            "roas_trend": st.column_config.LineChartColumn("ROAS by day", width="small"),
            "ctr_trend": st.column_config.LineChartColumn("CTR by day", width="small"),
            "spend_trend_ratio": st.column_config.ProgressColumn(
                "Recent vs. peak spend", format="%.0f%%", min_value=0.0, max_value=1.5,
            ),
            "delivery_declining": st.column_config.CheckboxColumn("Delivery declining?"),
            "recommendation": st.column_config.TextColumn("Recommendation", width="large"),
        },
    )

    csv_bytes = ads_df.drop(columns=["ctr_trend", "roas_trend", "spend_trend"]).to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download ad-level table as CSV", data=csv_bytes,
        file_name=f"meta_ads_check_{date_max.isoformat()}.csv", mime="text/csv",
    )

    st.subheader("Inspect one ad")
    pick_ad = st.selectbox("Ad", ads_df["ad"].tolist())
    ad_daily = az.daily_rollup(df[df["ad"] == pick_ad], []).sort_values("day")
    fig_roas, fig_ctr = ch.ad_roas_ctr_chart(
        ad_daily["day"], ad_daily["roas"], ad_daily["ctr"], breakeven_roas, benchmark_roas
    )
    ac1, ac2, ac3 = st.columns(3)
    ac1.plotly_chart(ch.daily_spend_chart(ad_daily), use_container_width=True)
    ac2.plotly_chart(fig_roas, use_container_width=True)
    ac3.plotly_chart(fig_ctr, use_container_width=True)
