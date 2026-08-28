"""
Meta Ads Daily Checker — Streamlit app for a Meta Ads Manager export
(ads-level data: Account / Campaign / Ad set / Ad), either a rolling 3+ day
daily breakdown or a single-window snapshot.

Two objectives: budget allocation at the Campaign and Ad set level, and
granular performance evaluation of individual ads. Minimalist layout —
one high-value plot per question, tables tucked into an expander.

Run:  streamlit run app.py
"""

from __future__ import annotations

import io
import os

import pandas as pd
import streamlit as st

import analysis as az
import charts as ch

st.set_page_config(page_title="Meta Ads Daily Checker", page_icon="📊", layout="wide")

STATUS_LABELS = {
    "critical": "🔴 Critical", "warning": "🟠 Warning", "low_delivery": "⚪ Low delivery",
    "insufficient_history": "⬜ Insufficient history", "healthy": "🟢 Healthy",
}
STATUS_ORDER = ["critical", "warning", "low_delivery", "insufficient_history", "healthy"]
R2_THRESHOLD = 0.35  # weighted-regression trend must clear this fit quality to be trusted


# ---------------------------------------------------------------------------
# Sidebar — data input + thresholds
# ---------------------------------------------------------------------------
st.sidebar.title("📊 Meta Ads Daily Checker")
st.sidebar.caption("For a rolling 3+ day Ads Manager export, or a single-window snapshot.")

uploaded = st.sidebar.file_uploader("Upload export (.xlsx or .csv)", type=["xlsx", "xls", "csv"])
use_sample = st.sidebar.button(
    "Use bundled sample data", use_container_width=True,
    help="Loads a small synthetic example so you can see the app without uploading anything.",
)

st.sidebar.divider()
st.sidebar.subheader("Thresholds")
benchmark_roas = st.sidebar.number_input(
    "Benchmark / target ROAS", min_value=0.1, value=2.4, step=0.1,
    help="The ROAS level a healthy campaign/ad set/ad should hold. Set this from your own "
         "longer-run history — it isn't derived from this file.",
)
breakeven_roas = st.sidebar.number_input("Break-even ROAS", min_value=0.0, value=1.0, step=0.1)
refresh_ratio = st.sidebar.slider(
    "Ad refresh trigger (% of benchmark)", 0.3, 0.95, 0.7, 0.05,
    help="An ad below this fraction of the benchmark ROAS is flagged for review.",
)
min_window_spend = st.sidebar.number_input(
    "Minimum window spend to judge ($)", min_value=0.0, value=15.0, step=5.0,
    help="Below this total spend, ROAS is treated as noise, not a verdict.",
)
min_active_days = st.sidebar.slider(
    "Minimum active days to judge", 1, 7, 3,
    help="Daily files only — ignored for single-window snapshots.",
)
anomaly_sensitivity = st.sidebar.slider(
    "Ad set anomaly sensitivity", 0.02, 0.20, 0.08, 0.01,
    help="Expected share of ad-set-days flagged as anomalous by the model (daily files with "
         "enough pooled history only). Higher = more flags.",
)


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
        "Upload a Meta Ads Manager export — a rolling **3+ day daily breakdown**, or a "
        "**single-window snapshot** — with **Campaign name**, **Ad set name**, and **Ad name** "
        "columns included. Or click **Use bundled sample data** in the sidebar first."
    )
    st.info(
        "**Expected columns** (header text can vary — matched automatically): `Day` (optional — "
        "omit for a snapshot export), `Account name`, `Campaign name`, `Ad set name`, `Ad name`, "
        "`Amount spent (USD)`, `Purchases`, `Purchases conversion value` (or a ROAS/ROI column), "
        "`Impressions`, `Link clicks`, `CTR`, `CPC`, `Reach`, `Frequency`. A blank-hierarchy totals "
        "row at the top of the file is detected and excluded automatically."
    )
    st.stop()

try:
    result = az.load_and_standardize(raw_df)
except az.ColumnResolutionError as e:
    st.error(str(e))
    st.stop()

df_all = result.df
hdg = result.has_daily_granularity

st.title("Meta Ads Daily Checker")

accounts = sorted(df_all["account"].unique())
if len(accounts) > 1:
    picked_accounts = st.sidebar.multiselect("Filter by account", accounts, default=accounts)
    df = df_all[df_all["account"].isin(picked_accounts)] if picked_accounts else df_all
else:
    df = df_all

date_min, date_max = result.window_start.date(), result.window_end.date()
span_days = (date_max - date_min).days + 1
mode_label = "daily breakdown" if hdg else "single-window snapshot"
st.caption(
    f"**{source_label}** · {mode_label} · {date_min} → {date_max} ({span_days}d) · "
    f"{df['ad'].nunique()} ads · {df['adset'].nunique()} ad sets · {df['campaign'].nunique()} campaigns"
    + (f" · {len(accounts)} accounts" if len(accounts) > 1 else "")
)
for n in result.notes:
    (st.warning if n.startswith("⚠️") else st.caption)(n)
if result.totals_row_check is not None and result.totals_row_check["matches"]:
    st.caption(f"✅ Detail rows reconcile with the file's own summary row (${result.totals_row_check['computed_spend']:,.2f}).")

kpi = az.topline_kpis(df)
k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Spend", f"${kpi['spend']:,.0f}")
k2.metric("Revenue", f"${kpi['revenue']:,.0f}")
k3.metric("Blended ROAS", f"{kpi['roas']:.2f}x" if pd.notna(kpi["roas"]) else "—")
k4.metric("Blended CPA", f"${kpi['cpa']:,.2f}" if pd.notna(kpi["cpa"]) else "—")
k5.metric("Purchases", f"{kpi['purchases']:,.0f}")

st.divider()

tab_campaign, tab_adset, tab_ad = st.tabs(["📊 Campaign Budget", "🎯 Ad Set Budget & Anomalies", "🎨 Ad Performance"])


def _rec_counts_row(rec_df):
    counts = rec_df["recommendation"].value_counts()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🟢 Scale", int(counts.get("Scale", 0)))
    c2.metric("🔵 Maintain", int(counts.get("Maintain", 0)))
    c3.metric("🔴 Cut", int(counts.get("Cut", 0)))
    c4.metric("⚪ Insufficient", int(counts.get("Insufficient data", 0)))


TREND_METHOD_MD = (
    "A **spend-weighted linear regression** is fit through every available day's ROAS (not just "
    "two arbitrary buckets), and only trusted (filled marker) when it clears a minimum fit quality "
    "(R² ≥ 0.35) and has at least 4 days — a noisy or non-monotonic run of days (e.g. a dip that "
    "already recovered) fails that bar on its own, and the recommendation falls back to the plain "
    "window-average ROAS (hollow marker) instead of an unreliable direction.\n"
    "- 🔴 **Cut** — blended ROAS is below break-even, or a confident trend projects below it.\n"
    "- 🟢 **Scale** — confident current level (fitted or blended) is at/above the benchmark.\n"
    "- 🔵 **Maintain** — in between.\n- ⚪ **Insufficient data** — too little history or spend to judge."
)

# ---------------------------------------------------------------------------
# TAB 1 — Campaign budget
# ---------------------------------------------------------------------------
with tab_campaign:
    with st.expander("ℹ️ How Scale / Maintain / Cut is decided"):
        st.markdown(TREND_METHOD_MD if hdg else
                     "No Day column in this file, so no trend can be fit — each campaign is classified "
                     "on its window ROAS alone versus break-even and the benchmark.")

    camp_df = az.classify_budget_level(
        df, "campaign", benchmark_roas=benchmark_roas, has_daily_granularity=hdg,
        breakeven_roas=breakeven_roas, min_window_spend=min_window_spend,
        min_active_days=min_active_days, r2_threshold=R2_THRESHOLD,
    )
    _rec_counts_row(camp_df)

    if hdg:
        fig = ch.budget_quadrant(camp_df, "campaign", benchmark_roas, breakeven_roas, "Campaigns: performance vs. potential")
    else:
        fig = ch.budget_bar_snapshot(camp_df, "campaign", "Campaigns by spend, colored by recommendation")
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
        if hdg:
            st.caption("Filled marker = trend trusted (R² ≥ 0.35); hollow = too noisy, recommendation rests on the ROAS level. "
                       "Y-axis is scaled to the 2nd–98th percentile of trend values so one volatile low-day-count point can't "
                       "flatten the rest — hover any point for its exact trend.")
    else:
        st.info("No campaigns with enough data to plot yet.")

    if hdg:
        c1, c2 = st.columns(2)
        daily_all = az.daily_rollup(df, [])
        c1.plotly_chart(ch.daily_spend_chart(daily_all), use_container_width=True)
        c2.plotly_chart(ch.daily_roas_chart(daily_all, breakeven_roas, benchmark_roas), use_container_width=True)

    with st.expander("📋 Full campaign table"):
        st.dataframe(
            camp_df, use_container_width=True, hide_index=True,
            column_config={
                "campaign": st.column_config.TextColumn("Campaign"),
                "active_days": st.column_config.NumberColumn("Days"),
                "spend": st.column_config.NumberColumn("Spend", format="$%.0f"),
                "revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
                "roas": st.column_config.NumberColumn("ROAS", format="%.2fx"),
                "cpa": st.column_config.NumberColumn("CPA", format="$%.2f"),
                "trend_slope": st.column_config.NumberColumn("Trend (x/day)", format="%.3f"),
                "trend_r2": st.column_config.NumberColumn("Trend fit (R²)", format="%.2f"),
                "trend_confident": st.column_config.CheckboxColumn("Trend trusted?"),
                "decision_level": st.column_config.NumberColumn("Decision level", format="%.2fx"),
                "recommendation": st.column_config.TextColumn("Recommendation"),
                "rationale": st.column_config.TextColumn("Why", width="large"),
            },
        )

# ---------------------------------------------------------------------------
# TAB 2 — Ad set budget & anomalies
# ---------------------------------------------------------------------------
with tab_adset:
    with st.expander("ℹ️ How this is decided"):
        st.markdown(
            "**Budget** — the same weighted-regression trend as campaigns, applied at the ad set grain.\n\n"
            "**Anomalies** — every pooled ad-set-day's CTR and CPC are z-scored *within that ad set* "
            "(so 'unusual' means unusual for it, not just bigger), then a multivariate outlier model "
            "(Isolation Forest) flags days that are jointly unusual — catching e.g. 'CPC drifted up "
            "*and* CTR drifted down together' even if neither alone crosses a hard threshold. This "
            "needs a reasonable pool of ad-set-days (30+) to be meaningful; with less, it falls back "
            "to a simple day-over-day percent-change rule, and with a single-window snapshot it's "
            "unavailable entirely (nothing to compare)."
        )

    adset_budget = az.classify_budget_level(
        df, "adset", benchmark_roas=benchmark_roas, has_daily_granularity=hdg,
        breakeven_roas=breakeven_roas, min_window_spend=min_window_spend,
        min_active_days=min_active_days, r2_threshold=R2_THRESHOLD,
    )
    _rec_counts_row(adset_budget)

    if hdg:
        fig = ch.budget_quadrant(adset_budget, "adset", benchmark_roas, breakeven_roas, "Ad sets: performance vs. potential")
    else:
        fig = ch.budget_bar_snapshot(adset_budget, "adset", "Ad sets by spend, colored by recommendation")
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
        if hdg:
            st.caption("Filled marker = trend trusted (R² ≥ 0.35); hollow = too noisy, recommendation rests on the ROAS level. "
                       "Y-axis is scaled to the 2nd–98th percentile of trend values so one volatile low-day-count point can't "
                       "flatten the rest — hover any point for its exact trend.")
    else:
        st.info("No ad sets with enough data to plot yet.")

    st.subheader("Anomaly detection")
    anomalies, method, pool = az.detect_adset_anomalies(df, has_daily_granularity=hdg, contamination=anomaly_sensitivity)
    if method == "unavailable":
        st.info("No day-over-day data in this file (single-window snapshot) — nothing to compare.")
    elif method == "ml":
        st.plotly_chart(ch.anomaly_scatter(pool), use_container_width=True)
        st.caption(f"{int(pool['is_anomaly'].sum())} of {len(pool)} pooled ad-set-days flagged.")
    elif len(anomalies):
        st.caption("Not enough pooled history yet for the anomaly model — showing simple day-over-day flags instead.")
        st.dataframe(
            anomalies.sort_values("day", ascending=False).drop(columns="score"),
            use_container_width=True, hide_index=True,
            column_config={"adset": "Ad set", "day": st.column_config.DateColumn("Day"),
                           "type": "Type", "detail": "Detail"},
        )
    else:
        st.success("No anomalies detected at the current thresholds.")

    with st.expander("📋 Full ad set table"):
        st.dataframe(
            adset_budget, use_container_width=True, hide_index=True,
            column_config={
                "adset": st.column_config.TextColumn("Ad set"),
                "active_days": st.column_config.NumberColumn("Days"),
                "spend": st.column_config.NumberColumn("Spend", format="$%.0f"),
                "revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
                "roas": st.column_config.NumberColumn("ROAS", format="%.2fx"),
                "cpa": st.column_config.NumberColumn("CPA", format="$%.2f"),
                "trend_slope": st.column_config.NumberColumn("Trend (x/day)", format="%.3f"),
                "trend_r2": st.column_config.NumberColumn("Trend fit (R²)", format="%.2f"),
                "trend_confident": st.column_config.CheckboxColumn("Trend trusted?"),
                "decision_level": st.column_config.NumberColumn("Decision level", format="%.2fx"),
                "recommendation": st.column_config.TextColumn("Recommendation"),
                "rationale": st.column_config.TextColumn("Why", width="large"),
            },
        )

# ---------------------------------------------------------------------------
# TAB 3 — Ad performance
# ---------------------------------------------------------------------------
with tab_ad:
    with st.expander("ℹ️ How ad status is decided"):
        st.markdown(
            "Checked in order — **data sufficiency before performance**:\n"
            "1. ⬜ **Insufficient history** — fewer than the minimum active days (daily files only).\n"
            "2. ⚪ **Low delivery** — total window spend below the floor; the ROAS number is sampling "
            "noise, not a verdict.\n"
            "3. 🔴 **Critical** — ROAS below break-even, or a confident trend projects below it.\n"
            "4. 🟠 **Warning** — confident current level below the refresh-trigger fraction of benchmark.\n"
            "5. 🟢 **Healthy** — everything else.\n\n"
            + (TREND_METHOD_MD.split("\n", 1)[0] if hdg else "")
        )

    ads_df = az.classify_ads(
        df, benchmark_roas=benchmark_roas, has_daily_granularity=hdg, breakeven_roas=breakeven_roas,
        min_window_spend=min_window_spend, min_active_days=min_active_days, refresh_ratio=refresh_ratio,
        r2_threshold=R2_THRESHOLD,
    )
    status_counts = ads_df["status"].value_counts()
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric("🔴 Critical", int(status_counts.get("critical", 0)))
    s2.metric("🟠 Warning", int(status_counts.get("warning", 0)))
    s3.metric("🟢 Healthy", int(status_counts.get("healthy", 0)))
    s4.metric("⚪ Low delivery", int(status_counts.get("low_delivery", 0)))
    s5.metric("⬜ Insufficient", int(status_counts.get("insufficient_history", 0)))

    if hdg:
        fig = ch.ad_performance_scatter(ads_df, benchmark_roas, breakeven_roas)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Filled marker = trend trusted (R² ≥ 0.35); hollow = too noisy, status rests on the ROAS level. "
                       "Y-axis is scaled to the 2nd–98th percentile of trend values so one volatile low-day-count ad "
                       "can't flatten the rest — hover any point for its exact trend.")
        else:
            st.info("No ads with enough data to plot yet.")
    else:
        judged = ads_df[~ads_df["status"].isin(["insufficient_history", "low_delivery"])]
        status_to_rec = {"critical": "Cut", "warning": "Maintain", "healthy": "Scale"}
        plot_df = pd.DataFrame({
            "ad": judged["ad"], "spend": judged["spend"],
            "recommendation": judged["status"].map(status_to_rec),
        })
        fig = ch.budget_bar_snapshot(plot_df, "ad", "Ads by spend, colored by status")
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No ads with enough data to plot yet.")

    with st.expander("📋 Full ad table"):
        display_df = ads_df.copy()
        display_df["status_label"] = display_df["status"].map(STATUS_LABELS)
        st.dataframe(
            display_df[["ad", "creative_type", "status_label", "spend", "revenue", "roas",
                         "trend_slope", "trend_confident", "delivery_declining", "recommendation"]],
            use_container_width=True, hide_index=True,
            column_config={
                "ad": st.column_config.TextColumn("Ad", width="medium"),
                "creative_type": "Type", "status_label": "Status",
                "spend": st.column_config.NumberColumn("Spend", format="$%.0f"),
                "revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
                "roas": st.column_config.NumberColumn("ROAS", format="%.2fx"),
                "trend_slope": st.column_config.NumberColumn("Trend (x/day)", format="%.3f"),
                "trend_confident": st.column_config.CheckboxColumn("Trend trusted?"),
                "delivery_declining": st.column_config.CheckboxColumn("Delivery declining?"),
                "recommendation": st.column_config.TextColumn("Recommendation", width="large"),
            },
        )
        csv_bytes = ads_df.to_csv(index=False).encode("utf-8")
        st.download_button("⬇️ Download as CSV", data=csv_bytes,
                            file_name=f"meta_ads_check_{date_max.isoformat()}.csv", mime="text/csv")

    if hdg:
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
