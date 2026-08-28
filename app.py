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
CUT_NO_SALES_DAYS = 3  # Campaign/Ad set cut rule: zero sales on each of the last N days


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
breakeven_roas = st.sidebar.number_input(
    "Break-even ROAS", min_value=0.0, value=1.0, step=0.1,
    help="Used for the Ad tab's Critical/Warning/Healthy status, not the Campaign/Ad set Cut rule "
         "below (that one is spend + no-sales driven).",
)
refresh_ratio = st.sidebar.slider(
    "Ad refresh trigger (% of benchmark)", 0.3, 0.95, 0.7, 0.05,
    help="An ad below this fraction of the benchmark ROAS is flagged for review.",
)
min_window_spend = st.sidebar.number_input(
    "Minimum window spend to judge ($)", min_value=0.0, value=15.0, step=5.0,
    help="Ad tab only. Below this total spend, ROAS is treated as noise, not a verdict.",
)
min_active_days = st.sidebar.slider(
    "Minimum active days to judge", 1, 7, 3,
    help="Ad tab only, daily files. Ignored for single-window snapshots.",
)
anomaly_sensitivity = st.sidebar.slider(
    "Anomaly sensitivity", 0.02, 0.20, 0.08, 0.01,
    help="Expected share of ad-set-days / ad-days flagged as anomalous by the model (daily files "
         "with enough pooled history only). Higher = more flags.",
)
st.sidebar.caption("Campaign / Ad set Cut rule")
cut_today_spend = st.sidebar.number_input(
    "Cut if today's spend over ($)", min_value=0.0, value=50.0, step=5.0,
    help="Combined with cumulative spend below (either can trigger) and zero sales on each of the "
         "last 3 days — spend with no recent payoff.",
)
cut_cumulative_spend = st.sidebar.number_input(
    "...or cumulative window spend over ($)", min_value=0.0, value=100.0, step=5.0,
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

all_window_days = sorted(df["day"].unique())  # fixed reference for "today" / "last N days" everywhere

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
    c1, c2, c3 = st.columns(3)
    c1.metric("🟢 Scale", int(counts.get("Scale", 0)))
    c2.metric("🔵 Maintain", int(counts.get("Maintain", 0)))
    c3.metric("🔴 Cut", int(counts.get("Cut", 0)))


BUDGET_RULE_MD = (
    f"Checked in order, same rule for Campaigns and Ad sets:\n\n"
    f"1. 🔴 **Cut** — today's spend is over **\\${cut_today_spend:.0f}**, or cumulative window spend "
    f"is over **\\${cut_cumulative_spend:.0f}**, *and* there have been zero sales on **each of the "
    f"last {CUT_NO_SALES_DAYS} days** in the file. Spend without recent payoff.\n"
    f"2. 🟢 **Scale** — otherwise, if today's ROAS is at/above the **{benchmark_roas:.2f}x** "
    "benchmark, or the same spend-weighted trend used elsewhere (R² ≥ 0.35, 4+ days) is confidently "
    "improving. Either signal is a reason to push more budget in.\n"
    "3. 🔵 **Maintain** — otherwise: no cut signal, no scale signal.\n\n"
    "Needs daily data for \"today\" and the no-sales streak; single-window snapshots treat the whole "
    "window as today and skip the streak check (undefined for one data point) — Cut can't trigger "
    "there."
)

# ---------------------------------------------------------------------------
# TAB 0 — Hierarchy: the whole account as one drill-down pyramid
# ---------------------------------------------------------------------------
with tab_hierarchy:
    with st.expander("ℹ️ How to read this"):
        st.markdown(
            "A top-down mind map: the top node is the parent, each node below it is a direct child, "
            "sized by spend. Color is that node's own **Cut / Maintain / Scale** call — the same rule "
            "as the Campaign Budget and Ad Set Budget tabs (see their methodology expanders), so a "
            "campaign or ad set is colored identically everywhere in the app. Hover any node for its "
            "full metrics.\n\n"
            "**Pick a campaign** below to fan out its ad sets. Individual ads have their own tab — "
            "🎨 Ad Performance."
        )

    def _mind_map_roll(sub_df, level_col):
        roll = az.classify_budget_level(
            sub_df, level_col, benchmark_roas=benchmark_roas, has_daily_granularity=hdg,
            cut_today_spend=cut_today_spend, cut_cumulative_spend=cut_cumulative_spend,
            cut_no_sales_days=CUT_NO_SALES_DAYS, r2_threshold=R2_THRESHOLD, reference_days=all_window_days,
        )
        roll = roll.rename(columns={level_col: "label"})
        roll["bucket"] = roll["recommendation"]
        return roll.sort_values("spend", ascending=False)

    hier_camp_roll = _mind_map_roll(df, "campaign")
    fig = ch.mind_map_level("Total", kpi, hier_camp_roll, "Total → Campaigns")
    if fig is not None:
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No campaigns with data to plot.")

    hier_pick_campaign = st.selectbox("Drill into a campaign", hier_camp_roll["label"].tolist())

    hier_camp_df = df[df["campaign"] == hier_pick_campaign]
    hier_camp_row = hier_camp_roll.loc[hier_camp_roll["label"] == hier_pick_campaign].iloc[0]
    hier_adset_roll = _mind_map_roll(hier_camp_df, "adset")
    fig2 = ch.mind_map_level(hier_pick_campaign, hier_camp_row, hier_adset_roll, f"{hier_pick_campaign} → Ad sets")
    if fig2 is not None:
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No ad sets under this campaign with data to plot.")

# ---------------------------------------------------------------------------
# TAB 1 — Campaign budget
# ---------------------------------------------------------------------------
with tab_campaign:
    with st.expander("ℹ️ How Scale / Maintain / Cut is decided"):
        st.markdown(BUDGET_RULE_MD)

    camp_df = az.classify_budget_level(
        df, "campaign", benchmark_roas=benchmark_roas, has_daily_granularity=hdg,
        cut_today_spend=cut_today_spend, cut_cumulative_spend=cut_cumulative_spend,
        cut_no_sales_days=CUT_NO_SALES_DAYS, r2_threshold=R2_THRESHOLD, reference_days=all_window_days,
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
                "today_spend": st.column_config.NumberColumn("Today spend", format="$%.0f"),
                "today_roas": st.column_config.NumberColumn("Today ROAS", format="%.2fx"),
                "no_sales_recent": st.column_config.CheckboxColumn(f"No sales, {CUT_NO_SALES_DAYS}d"),
                "trend_slope": st.column_config.NumberColumn("Trend (x/day)", format="%.3f"),
                "trend_r2": st.column_config.NumberColumn("Trend fit (R²)", format="%.2f"),
                "trend_confident": st.column_config.CheckboxColumn("Trend trusted?"),
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
            "**Budget**\n\n" + BUDGET_RULE_MD + "\n\n**Anomalies** — every pooled ad-set-day's CTR "
            "and CPC are z-scored *within that ad set* (so 'unusual' means unusual for it, not just "
            "bigger), then a multivariate outlier model (Isolation Forest) flags days that are "
            "jointly unusual — catching e.g. 'CPC drifted up *and* CTR drifted down together' even "
            "if neither alone crosses a hard threshold. This needs a reasonable pool of ad-set-days "
            "(30+) to be meaningful; with less, it falls back to a simple day-over-day percent-change "
            "rule, and with a single-window snapshot it's unavailable entirely (nothing to compare)."
        )

    adset_budget = az.classify_budget_level(
        df, "adset", benchmark_roas=benchmark_roas, has_daily_granularity=hdg,
        cut_today_spend=cut_today_spend, cut_cumulative_spend=cut_cumulative_spend,
        cut_no_sales_days=CUT_NO_SALES_DAYS, r2_threshold=R2_THRESHOLD, reference_days=all_window_days,
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
    anomalies, method, pool = az.detect_anomalies(df, "adset", has_daily_granularity=hdg, contamination=anomaly_sensitivity)
    if method == "unavailable":
        st.info("No day-over-day data in this file (single-window snapshot) — nothing to compare.")
    elif method == "ml":
        latest_day = pool["day"].max()
        pool_latest = pool[pool["day"] == latest_day]
        st.plotly_chart(
            ch.anomaly_scatter(pool_latest, group_col="adset",
                                title=f"Ad set anomaly detection — {latest_day.date()} (CTR vs. CPC)"),
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
                "today_spend": st.column_config.NumberColumn("Today spend", format="$%.0f"),
                "today_roas": st.column_config.NumberColumn("Today ROAS", format="%.2fx"),
                "no_sales_recent": st.column_config.CheckboxColumn(f"No sales, {CUT_NO_SALES_DAYS}d"),
                "trend_slope": st.column_config.NumberColumn("Trend (x/day)", format="%.3f"),
                "trend_r2": st.column_config.NumberColumn("Trend fit (R²)", format="%.2f"),
                "trend_confident": st.column_config.CheckboxColumn("Trend trusted?"),
                "recommendation": st.column_config.TextColumn("Recommendation"),
                "rationale": st.column_config.TextColumn("Why", width="large"),
            },
        )

# ---------------------------------------------------------------------------
# TAB 3 — Ad performance
# ---------------------------------------------------------------------------
with tab_ad:
    with st.expander("ℹ️ How this is decided"):
        st.markdown(
            "**Status** (Critical/Warning/Healthy, shown below and available in the ranking table) "
            "checks data sufficiency before performance, same as before — see `classify_ads` in the "
            "Methodology tab.\n\n"
            "**Anomalies** — every pooled ad-day's CTR and CPC are z-scored *within that ad's own "
            "history*, then a multivariate outlier model (Isolation Forest) flags days that are "
            "jointly unusual — same approach as the Ad Set tab, applied one grain finer. Needs 30+ "
            "pooled ad-days to be meaningful; falls back to a day-over-day percent-change rule below "
            "that, and is unavailable for single-window snapshots."
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

    # -------------------------------------------------------------------
    # Anomalies — list only the flagged ads, not all of them
    # -------------------------------------------------------------------
    st.subheader("Anomalies")
    ad_anomalies, ad_method, ad_pool = az.detect_anomalies(
        df, "ad", has_daily_granularity=hdg, contamination=anomaly_sensitivity
    )
    latest_day = all_window_days[-1] if all_window_days else None
    flagged_today = pd.DataFrame()

    if ad_method == "unavailable":
        st.info("No day-over-day data in this file (single-window snapshot) — nothing to compare.")
    elif ad_method == "ml":
        pool_latest = ad_pool[ad_pool["day"] == latest_day]
        st.plotly_chart(
            ch.anomaly_scatter(pool_latest, group_col="ad",
                                title=f"Ad anomaly detection — {latest_day.date()} (CTR vs. CPC)"),
            use_container_width=True,
        )
        flagged_today = ad_anomalies[ad_anomalies["day"] == latest_day].sort_values("score", ascending=False)
        st.caption(
            f"{int(pool_latest['is_anomaly'].sum())} of {len(pool_latest)} ads flagged for "
            f"**{latest_day.date()}**. Model is fit on all {len(ad_pool)} pooled ad-days in the window; "
            "only the latest day is plotted."
        )
    elif len(ad_anomalies):
        st.caption("Not enough pooled history yet for the anomaly model — showing each flagged ad's "
                    "most recent day-over-day flag instead.")
        flagged_today = ad_anomalies.sort_values("day").groupby("ad", as_index=False).tail(1)
    else:
        st.success("No anomalies detected at the current thresholds.")

    if len(flagged_today):
        view_options = ["Latest day (funnel)", "Across days (trend)"] if hdg else ["Latest day (funnel)"]
        view_mode = st.radio("View", view_options, horizontal=True, key="ad_anomaly_view")
        for _, flag_row in flagged_today.head(8).iterrows():
            ad_name = flag_row["ad"]
            with st.expander(f"{ad_name}  —  {flag_row['detail']}"):
                ad_daily = az.daily_rollup(df[df["ad"] == ad_name], []).sort_values("day")
                if view_mode.startswith("Latest"):
                    latest_row = ad_daily[ad_daily["day"] == flag_row["day"]]
                    if len(latest_row):
                        r = latest_row.iloc[0]
                        st.plotly_chart(
                            ch.ad_funnel(r["impressions"], r["link_clicks"], r["purchases"],
                                         f"{ad_name} — {flag_row['day'].date()}"),
                            use_container_width=True,
                        )
                else:
                    fig_roas, fig_ctr = ch.ad_roas_ctr_chart(
                        ad_daily["day"], ad_daily["roas"], ad_daily["ctr"], breakeven_roas, benchmark_roas
                    )
                    tc1, tc2 = st.columns(2)
                    tc1.plotly_chart(fig_roas, use_container_width=True)
                    tc2.plotly_chart(fig_ctr, use_container_width=True)
        if len(flagged_today) > 8:
            st.caption(f"Showing the top 8 of {len(flagged_today)} flagged ads.")

    # -------------------------------------------------------------------
    # Ranking — every ad, sortable on multiple columns in priority order
    # -------------------------------------------------------------------
    st.subheader("Ranking")
    rank_mode = st.radio("Metrics from", ["All days (window)", "Single day"], horizontal=True, key="ad_rank_mode")

    creative_lookup = df[["ad", "creative_type"]].drop_duplicates()

    if rank_mode == "Single day" and hdg:
        rank_day = st.selectbox("Day", all_window_days, index=len(all_window_days) - 1,
                                 format_func=lambda d: pd.Timestamp(d).strftime("%Y-%m-%d"))
        rank_df = az.window_rollup(df[df["day"] == rank_day], ["ad"]).merge(creative_lookup, on="ad", how="left")
        is_all_days = False
    else:
        rank_df = az.window_rollup(df, ["ad"]).merge(creative_lookup, on="ad", how="left")
        rank_df = rank_df.merge(
            ads_df[["ad", "status", "trend_slope", "trend_confident", "delivery_declining"]], on="ad", how="left"
        )
        is_all_days = True

    sort_cols = [("spend", "Spend"), ("revenue", "Revenue"), ("roas", "ROAS"), ("cpa", "CPA"),
                 ("ctr", "CTR"), ("cpc", "CPC"), ("cpm", "CPM")]
    if is_all_days:
        sort_cols += [("active_days", "Active days"), ("trend_slope", "Trend (x/day)")]
    sort_options = {}
    for col, label in sort_cols:
        sort_options[f"{label} (high → low)"] = (col, False)
        sort_options[f"{label} (low → high)"] = (col, True)

    picked_sorts = st.multiselect(
        "Sort by (priority order — first pick is primary)", list(sort_options.keys()),
        default=["Spend (high → low)"],
    )
    if picked_sorts:
        rank_df = rank_df.sort_values(
            by=[sort_options[p][0] for p in picked_sorts],
            ascending=[sort_options[p][1] for p in picked_sorts],
            na_position="last",
        )
    else:
        rank_df = rank_df.sort_values("spend", ascending=False)

    display_cols = ["ad", "creative_type", "spend", "revenue", "roas", "cpa", "ctr", "cpc"]
    col_config = {
        "ad": st.column_config.TextColumn("Ad", width="medium"), "creative_type": "Type",
        "spend": st.column_config.NumberColumn("Spend", format="$%.0f"),
        "revenue": st.column_config.NumberColumn("Revenue", format="$%.0f"),
        "roas": st.column_config.NumberColumn("ROAS", format="%.2fx"),
        "cpa": st.column_config.NumberColumn("CPA", format="$%.2f"),
        "ctr": st.column_config.NumberColumn("CTR", format="%.2f%%"),
        "cpc": st.column_config.NumberColumn("CPC", format="$%.2f"),
    }
    if is_all_days:
        rank_df = rank_df.copy()
        rank_df["status_label"] = rank_df["status"].map(STATUS_LABELS).fillna(rank_df["status"])
        display_cols += ["active_days", "status_label", "trend_slope", "delivery_declining"]
        col_config.update({
            "active_days": st.column_config.NumberColumn("Days"),
            "status_label": "Status",
            "trend_slope": st.column_config.NumberColumn("Trend (x/day)", format="%.3f"),
            "delivery_declining": st.column_config.CheckboxColumn("Delivery declining?"),
        })

    st.dataframe(rank_df[display_cols], use_container_width=True, hide_index=True, column_config=col_config)
    csv_bytes = rank_df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download ranking as CSV", data=csv_bytes,
                        file_name=f"meta_ads_ad_ranking_{date_max.isoformat()}.csv", mime="text/csv")

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
        f"Same function, same rule, applied at both grains and reused by the Hierarchy tab's mind "
        f"map too (`analysis.py::classify_budget_level`). With your current sidebar values (cut "
        f"today-spend **\\${cut_today_spend:.0f}**, cut cumulative spend **\\${cut_cumulative_spend:.0f}**, "
        f"benchmark **{benchmark_roas:.2f}x**):\n\n"
        f"1. **🔴 Cut** — today's spend is over \\${cut_today_spend:.0f}, or cumulative window spend "
        f"is over \\${cut_cumulative_spend:.0f}, *and* zero sales on **each of the last "
        f"{CUT_NO_SALES_DAYS} days** in the file.\n"
        f"2. **🟢 Scale** — otherwise, if today's ROAS is at/above **{benchmark_roas:.2f}x**, or the "
        "weighted-regression trend from section 3 is confidently improving.\n"
        "3. **🔵 Maintain** — otherwise.\n\n"
        "\"Today\" is the most recent calendar day in the *whole loaded file* (fixed once, not "
        "per-entity — so an ad set with no rows on the last day still gets the correct \"today\" "
        "and \"last N days\" reference instead of a stale one inferred from its own sparser data). "
        "Needs daily data; single-window snapshots treat the whole window as \"today\" and skip the "
        "no-sales-streak check (undefined for one data point), so Cut can only be reached there via "
        "logic that never fires — meaning snapshot files only ever resolve to Scale or Maintain."
    )

    st.subheader("5. Anomaly detection — the ML model")
    st.markdown(
        f"`analysis.py::detect_anomalies`, one grain finer at the Ad tab (`group_col=\"ad\"`) than "
        "the Ad Set tab (`group_col=\"adset\"`) — same function either way. Every pooled (group, day) "
        "has its own **CTR and CPC z-scored against that group's own history** (so 'unusual' means "
        "unusual for that specific ad set/ad, not just a bigger/smaller one in absolute terms). Those "
        "two z-scores feed an **Isolation Forest** (`sklearn.ensemble.IsolationForest`, "
        f"`n_estimators=200`, `contamination={anomaly_sensitivity:.2f}` — your current *anomaly "
        "sensitivity* setting, `random_state=42` for reproducibility) fit across the pooled data — "
        "catching e.g. 'CPC drifted up *and* CTR drifted down together' even when neither alone "
        "crosses a hard threshold, which a per-metric rule can't see.\n\n"
        "- Needs a pool of **30+ (group, day) rows** to be meaningful. Below that it falls back to a "
        "simple **day-over-day % change rule** (CPC +50%, or CTR -40%).\n"
        "- Unavailable entirely for single-window snapshots (nothing to compare against).\n"
        "- The model is trained on the **full pooled window**, but only the **latest day's** flags "
        "are listed and plotted — training needs multiple days to know what's normal, but the "
        "actionable view is 'what's unusual right now,' not a history of anomalies that may have "
        "already resolved. The Ad tab's Anomalies section renders each flagged ad's latest-day "
        "**funnel** (Impressions → Link clicks → Purchases) or its **daily trend**, your choice."
    )

    st.subheader("6. Ad performance evaluation & ranking")
    st.markdown(
        f"`analysis.py::classify_ads`. Same weighted-regression trend as above, per ad, checked in "
        "order — data sufficiency, then performance:\n\n"
        f"1. ⬜ **Insufficient history** — fewer than {min_active_days} active day(s).\n"
        f"2. ⚪ **Low delivery** — total spend under \\${min_window_spend:,.0f}; ROAS here is sampling "
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
        "separate creative-level dataset or model.\n\n"
        "The **Ranking** table is a plain sort, no model: pick **All days** (window rollup, plus "
        "status/trend from above) or **Single day** (any date in the file, `window_rollup` on that "
        "day's rows alone — no status/trend, those need multiple days). Each entry in **Sort by** "
        "bundles a column with a direction, and multiple picks sort in the order chosen — the first "
        "pick is primary, later picks only break ties within it."
    )

    st.subheader("7. Hierarchy view")
    st.markdown(
        "No model here — `charts.py::mind_map_level` renders one 'star' at a time (one parent, its "
        "direct children below it, connected by lines). Total → Campaigns renders first; picking a "
        "campaign renders a second star for its Ad sets. Kept to one small star per view, rather than "
        "every level at once, on purpose — an earlier all-at-once version (a nested-box 'icicle' "
        "chart with the whole account rendered simultaneously) was hard to read at real account scale "
        "and had a rendering bug where its legend overlapped the title. Node size = spend; color = "
        "that node's own **Cut / Maintain / Scale** call from section 4 — the exact same function "
        "and thresholds as the Campaign Budget and Ad Set Budget tabs, so a campaign or ad set is "
        "colored identically everywhere in the app. Individual ads aren't part of this view — see the "
        "🎨 Ad Performance tab instead."
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
