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

tab_hierarchy, tab_campaign, tab_adset, tab_ad, tab_methodology = st.tabs(
    ["🏔️ Hierarchy", "📊 Campaign Budget", "🎯 Ad Set Budget & Anomalies", "🎨 Ad Performance", "📚 Methodology"]
)


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
# TAB 0 — Hierarchy: the whole account as one drill-down pyramid
# ---------------------------------------------------------------------------
with tab_hierarchy:
    with st.expander("ℹ️ How to read this"):
        st.markdown(
            "A top-down mind map, one level at a time: the top node is the parent, each node below it "
            "is a direct child, sized by spend. Color = that node's *own* window ROAS against your "
            "Cut / Maintain / Scale thresholds (🟢 Scale · 🔵 Maintain · 🔴 Cut · ⚪ Insufficient data) — "
            "window-level, not a trend, since this spans every level of the account. Hover any node "
            "for its full metrics.\n\n"
            "**Pick a campaign** below to fan out its ad sets, then **pick an ad set** to fan out its "
            "individual ads — CPA/CTR/CPC show on hover at that level."
        )

    def _bucketed(roll):
        roll = roll.copy()
        roll["bucket"] = roll["roas"].apply(lambda r: az.roas_bucket(r, benchmark_roas, breakeven_roas))
        return roll.sort_values("spend", ascending=False)

    hier_camp_roll = _bucketed(az.window_rollup(df, ["campaign"]).rename(columns={"campaign": "label"}))
    fig = ch.mind_map_level("Total", kpi, hier_camp_roll, "Total → Campaigns")
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No campaigns with data to plot.")

    hier_pick_campaign = st.selectbox("Drill into a campaign", hier_camp_roll["label"].tolist())

    hier_camp_df = df[df["campaign"] == hier_pick_campaign]
    hier_camp_row = hier_camp_roll.loc[hier_camp_roll["label"] == hier_pick_campaign].iloc[0]
    hier_adset_roll = _bucketed(az.window_rollup(hier_camp_df, ["adset"]).rename(columns={"adset": "label"}))
    fig2 = ch.mind_map_level(hier_pick_campaign, hier_camp_row, hier_adset_roll, f"{hier_pick_campaign} → Ad sets")
    if fig2 is not None:
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No ad sets under this campaign with data to plot.")
        hier_adset_roll = None

    if hier_adset_roll is not None:
        hier_pick_adset = st.selectbox(
            f"Drill into an ad set (within '{hier_pick_campaign}')", hier_adset_roll["label"].tolist()
        )
        hier_adset_df = hier_camp_df[hier_camp_df["adset"] == hier_pick_adset]
        hier_adset_row = hier_adset_roll.loc[hier_adset_roll["label"] == hier_pick_adset].iloc[0]
        hier_ad_roll = _bucketed(az.window_rollup(hier_adset_df, ["ad"]).rename(columns={"ad": "label"}))
        fig3 = ch.mind_map_level(hier_pick_adset, hier_adset_row, hier_ad_roll, f"{hier_pick_adset} → Ads")
        if fig3 is not None:
            st.plotly_chart(fig3, use_container_width=True)
        else:
            st.info("No ads under this ad set with data to plot.")

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
        latest_day = pool["day"].max()
        pool_latest = pool[pool["day"] == latest_day]
        st.plotly_chart(
            ch.anomaly_scatter(pool_latest, title=f"Ad set anomaly detection — {latest_day.date()} (CTR vs. CPC)"),
            use_container_width=True,
        )
        st.caption(
            f"{int(pool_latest['is_anomaly'].sum())} of {len(pool_latest)} ad sets flagged for **{latest_day.date()}**. "
            f"The model is fit on all {len(pool)} pooled ad-set-days in the loaded window so it knows each ad set's "
            "normal range, but only the latest day is plotted — older, possibly-already-resolved anomalies aren't shown."
        )
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

# ---------------------------------------------------------------------------
# TAB 4 — Methodology: every algorithm/model behind the app, in one place,
# with the thresholds currently in effect (from the sidebar) filled in.
# ---------------------------------------------------------------------------
with tab_methodology:
    st.markdown(
        "Everything below runs the same way for every file — nothing is hidden per-tab. Thresholds "
        "shown are the ones **currently set in the sidebar**, so this reflects the exact run you're looking at."
    )

    st.subheader("1. Data shape detection")
    st.markdown(
        "On load, every column header is matched against known Meta export phrasings "
        "(`analysis.py::COLUMN_ALIASES`) regardless of exact wording. A blank-hierarchy, "
        "populated-spend row (the totals row Meta puts at the top of some exports) is detected and "
        "excluded from every breakdown, then used only as a reconciliation check.\n\n"
        "The file is then classified as one of two shapes, and every function below branches on it:\n"
        "- **Daily** — a `Day` column with 2+ distinct dates. Enables everything trend-based.\n"
        "- **Snapshot** — one row per ad over a single window. No trend is possible from one point, "
        "so every level falls back to a threshold read on the window average instead."
    )

    st.subheader("2. Ratio-of-sums, never average-of-ratios")
    st.markdown(
        "ROAS, CTR, CPC, and CPM are **never** averaged directly across rows — that silently "
        "misweights low-volume rows equally with high-volume ones. Every group (campaign, ad set, "
        "ad, day) first sums the raw components (spend, revenue, impressions, clicks), then "
        "recomputes the ratio from those sums (`analysis.py::add_ratio_columns`)."
    )

    st.subheader("3. Weighted linear regression — the trend algorithm")
    st.markdown(
        "Used identically at the campaign, ad set, and ad level (`analysis.py::weighted_roas_trend`). "
        "For each entity, every available day's ROAS is fit with a **spend-weighted least-squares "
        "line** (`numpy.polyfit`, weights = √(daily spend)) — not two arbitrary early/late buckets. "
        "The fit's weighted **R²** is computed and used as a trust gate:\n\n"
        f"- Needs **{min_active_days}+ active days** (your current *minimum active days* setting) and "
        f"**R² ≥ {R2_THRESHOLD:.2f}** (fixed) to be trusted at all.\n"
        "- A noisy or non-monotonic run of days (e.g. a mid-window dip that already recovered) "
        "naturally scores low R² — no special-casing is needed for 'what if the most recent day "
        "already turned around,' the gate handles it structurally.\n"
        "- When trusted, the decision uses the **fitted end-of-window ROAS**. When not trusted, it "
        "falls back to the **plain window-average ROAS** instead of an unreliable direction.\n"
        "- Fitted values are clipped at 0 (a negative extrapolated ROAS is impossible and was an "
        "early bug in this approach).\n\n"
        "This is the **filled vs. hollow marker** you see on every quadrant chart: filled = trend "
        "trusted and driving the call, hollow = too noisy, call rests on the ROAS level alone."
    )

    st.subheader("4. Budget allocation — Campaign & Ad set")
    st.markdown(
        f"Same function, same logic, applied at both grains (`analysis.py::classify_budget_level`). "
        f"With your current sidebar values (benchmark **{benchmark_roas:.2f}x**, break-even "
        f"**{breakeven_roas:.2f}x**, minimum window spend **${min_window_spend:,.0f}**, minimum "
        f"active days **{min_active_days}**):\n\n"
        "1. **Data sufficiency first** — below the minimum active days or minimum spend → "
        "`⚪ Insufficient data`, never judged as good or bad.\n"
        "2. Otherwise, `decision_level` = fitted end-of-window ROAS (if the trend is trusted) or "
        "blended window ROAS (if not).\n"
        f"3. **🔴 Cut** if blended ROAS or `decision_level` is below break-even "
        f"({breakeven_roas:.2f}x).\n"
        f"4. **🟢 Scale** if `decision_level` is at/above the benchmark ({benchmark_roas:.2f}x).\n"
        "5. **🔵 Maintain** otherwise."
    )

    st.subheader("5. Ad set anomaly detection — the ML model")
    st.markdown(
        f"`analysis.py::detect_adset_anomalies`. Every ad-set-day is pooled together, then each "
        "ad set's own **CTR and CPC are z-scored against its own history** (so 'unusual' means "
        "unusual for that specific ad set, not just a bigger/smaller one in absolute terms). Those "
        "two z-scores feed an **Isolation Forest** (`sklearn.ensemble.IsolationForest`, "
        f"`n_estimators=200`, `contamination={anomaly_sensitivity:.2f}` — your current *anomaly "
        "sensitivity* setting, `random_state=42` for reproducibility) fit across the pooled data — "
        "catching e.g. 'CPC drifted up *and* CTR drifted down together' even when neither alone "
        "crosses a hard threshold, which a per-metric rule can't see.\n\n"
        "- Needs a pool of **30+ ad-set-days** (across all ad sets combined) for the model to be "
        "meaningful. Below that it falls back to a simple **day-over-day % change rule** (CPC +50%, "
        "or CTR -40%).\n"
        "- Unavailable entirely for single-window snapshots (nothing to compare against).\n"
        "- The model is trained on the **full pooled window**, but the chart only plots the "
        "**latest day** — training needs multiple days per ad set to know what's normal, but the "
        "actionable view is 'what's unusual right now,' not a history of anomalies that may have "
        "already resolved."
    )

    st.subheader("6. Ad performance evaluation")
    st.markdown(
        f"`analysis.py::classify_ads`. Same weighted-regression trend as above, per ad, checked in "
        "order — data sufficiency, then performance:\n\n"
        f"1. ⬜ **Insufficient history** — fewer than {min_active_days} active day(s).\n"
        f"2. ⚪ **Low delivery** — total spend under ${min_window_spend:,.0f}; ROAS here is sampling "
        "noise, not signal.\n"
        f"3. 🔴 **Critical** — ROAS (or a trusted trend) below break-even ({breakeven_roas:.2f}x).\n"
        f"4. 🟠 **Warning** — trusted current level below {refresh_ratio:.0%} of benchmark "
        f"({benchmark_roas * refresh_ratio:.2f}x) — your current *ad refresh trigger*.\n"
        "5. 🟢 **Healthy** — everything else.\n\n"
        "Alongside status, each ad also gets a **delivery-declining** flag — the most recent 1-2 "
        "days' average spend compared against the ad's peak daily spend in the window — to separate "
        "'this creative is underperforming' from 'this ad simply isn't being delivered anymore.' "
        "`creative_type` is a lightweight keyword tag inferred from the ad's own name (Feed/catalog, "
        "KOL/UGC, AI-generated, Static image, Video) — context for the ad-level view, not a "
        "separate creative-level dataset or model."
    )

    st.subheader("7. Hierarchy view")
    st.markdown(
        "No model here — `charts.py::mind_map_level` renders one 'star' at a time (one parent, its "
        "direct children below it, connected by lines), built from the same `window_rollup` used "
        "elsewhere. Total → Campaigns renders first; picking a campaign renders a second star for its "
        "Ad sets, and picking an ad set renders a third for its Ads. Kept to one small star per view, "
        "rather than every level at once, on purpose — an earlier all-at-once version (a nested-box "
        "'icicle' chart with the whole account rendered simultaneously) was hard to read at real "
        "account scale and had a rendering bug where its legend overlapped the title. Node size = "
        "spend; color = that node's own window ROAS against the break-even/benchmark thresholds "
        "(`analysis.py::roas_bucket`) — window-level only, no trend, since a single star spans "
        "several entities of the same type at once rather than one entity's history."
    )

    st.subheader("8. Chart design choices")
    st.markdown(
        "- **Quadrant scatter** (Campaign/Ad set/Ad tabs) — x = ROAS (performance), y = fitted trend "
        "slope in ROAS-x/day (potential/direction), bubble size = √spend, color = recommendation/"
        "status, filled/hollow = trend confidence. This is deliberately the *one* chart each tab "
        "leads with, not a table.\n"
        "- **Percentile-clipped y-axis** — a trend fit from only 3-5 noisy days can occasionally "
        "swing to an extreme slope value that would otherwise stretch the axis and flatten every "
        "other point near zero. The axis is ranged to the 2nd-98th percentile of trend values "
        "instead of the true min/max; the point itself still plots at its real value (visible on "
        "hover), it just doesn't dictate the scale for everyone else.\n"
        "- **Latest-day anomaly view** — see section 5 above."
    )

    st.subheader("Libraries")
    st.markdown(
        "`pandas`/`numpy` for data handling and the weighted regression, `scikit-learn` "
        "(`IsolationForest`) for anomaly detection, `plotly` for every chart, `streamlit` for the UI. "
        "All analysis logic lives in `analysis.py`, kept free of any Streamlit import so it can be "
        "tested and reasoned about on its own."
    )
