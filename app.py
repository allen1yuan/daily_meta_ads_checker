"""
Meta Ads Daily Checker — Streamlit app for a Meta Ads Manager export,
either a 3-7 day rolling daily breakdown or a single-window snapshot.
Four levels: Campaign (budget allocation), Ad set (performance + anomaly
flags), Ad (creative fatigue / winners), Creative (re-uploads rolled up).

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
st.sidebar.caption("For a rolling 3–7 day Ads Manager export, or a single-window snapshot.")

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
         "(e.g. the converged ROAS from a full-year analysis) — it isn't derived from this file.",
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
min_active_days = st.sidebar.slider(
    "Minimum active days to judge", 1, 7, 3,
    help="Only applies to files with day-by-day granularity — ignored for single-window snapshots, "
         "since a single aggregated row can't say how many distinct days it covers.",
)
st.sidebar.caption("Anomaly detection (ad sets) — daily files only")
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
        "Upload a Meta Ads Manager export — either a rolling **3–7 day daily breakdown**, or a "
        "**single-window snapshot** (Campaign / Ad set / Ad, no Day breakdown) — ideally with "
        "**Campaign name**, **Ad set name**, and **Ad name** columns all included (Meta lets you add "
        "all three, plus Account name, in one export). Or click **Use bundled sample data** in the "
        "sidebar to see the app with synthetic daily data first."
    )
    st.info(
        "**Expected columns** (exact header text can vary — the app matches common Meta export "
        "phrasings automatically): `Day` (optional — omit for a snapshot export), `Account name`, "
        "`Campaign name`, `Ad set name`, `Ad name`, `Amount spent (USD)`, `Purchases`, "
        "`Purchases conversion value` (or a ROAS/ROI column), `Impressions`, `Link clicks`, `CTR`, "
        "`CPC`, `Reach`, `Frequency`. A blank-hierarchy totals row at the top of the file (common in "
        "Meta exports) is detected and excluded automatically."
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

# --- Account filter, if the file has more than one -------------------------
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
    f"Source: **{source_label}** · Mode: **{mode_label}** · Window: **{date_min} → {date_max}** ({span_days} days) · "
    f"{df['ad'].nunique()} ads · {df['adset'].nunique()} ad sets · {df['campaign'].nunique()} campaigns"
    + (f" · {len(accounts)} accounts" if len(accounts) > 1 else "")
)

if hdg and span_days > 7:
    st.warning(
        f"This file spans {span_days} days — more than the 3–7 day window this app is tuned for. "
        "It will still run, but trend/anomaly thresholds were chosen for a short rolling window "
        "and may behave oddly (e.g. too many 'insufficient history' flags) on a much longer one."
    )
for n in result.notes:
    (st.warning if n.startswith("⚠️") else st.info)(n)

if result.totals_row_check is not None:
    tc = result.totals_row_check
    if tc["matches"]:
        st.success(
            f"✓ Detail rows reconcile with the file's own summary row: ${tc['computed_spend']:,.2f} spend.",
            icon="✅",
        )

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

tab_campaign, tab_adset, tab_ad, tab_creative = st.tabs(
    ["📊 Campaign Overview", "🎯 Ad Set Diagnosis", "🎨 Ad Performance", "🧬 Creative Rollup"]
)

# ---------------------------------------------------------------------------
# TAB 1 — Campaign
# ---------------------------------------------------------------------------
with tab_campaign:
    with st.expander("ℹ️ How Scale / Maintain / Cut is decided"):
        st.markdown(
            "**Daily files** (this file): each campaign's window is split into an early half and a "
            "late half by day count. Late-half ROAS is compared to *the campaign's own* early-half "
            "ROAS, and to your benchmark:\n"
            "- 🔴 **Cut** — late-half ROAS is below break-even, **or** ROAS fell more than 30% from "
            "early to late window *and the most recent single day hasn't already recovered above "
            "benchmark*.\n"
            "- 🔵 **Maintain (recovered)** — ROAS fell >30% early-to-late by that same test, **but** "
            "the single most recent day is already back at/above benchmark — the late-window average "
            "can span several days and bury a same-day turnaround, so a real rebound downgrades this "
            "from Cut to a watch-it flag instead of an immediate cut. The 'Most recent day' column "
            "shows the number this is based on.\n"
            "- 🟢 **Scale** — late-half ROAS is at/above the benchmark and not declining more than 5%.\n"
            "- 🔵 **Maintain** — everything else (below benchmark but above break-even, not sharply falling).\n"
            "- ⚪ **Insufficient data** — fewer than the minimum active days, or below the minimum "
            "window spend, to trust a trend.\n\n"
            "**Snapshot files** (no Day column): there's no trend to compare, so the same four labels "
            "are assigned from the window's ROAS alone, versus break-even and the benchmark."
            if hdg else
            "**Snapshot files** (this file): there's no Day column, so no early/late trend can be "
            "computed — each campaign is classified from its window ROAS alone:\n"
            "- 🔴 **Cut** — ROAS below break-even.\n- 🟢 **Scale** — ROAS at/above the benchmark.\n"
            "- 🔵 **Maintain** — in between.\n- ⚪ **Insufficient data** — below the minimum window spend.\n\n"
            "**Daily files** (Day column present) additionally compare an early-half vs. late-half "
            "ROAS trend, which isn't possible here."
        )
    if hdg:
        st.subheader("Daily trend")
        daily_all = az.daily_rollup(df, [])
        c1, c2 = st.columns(2)
        c1.plotly_chart(ch.daily_spend_chart(daily_all), use_container_width=True)
        c2.plotly_chart(ch.daily_roas_chart(daily_all, breakeven_roas, benchmark_roas), use_container_width=True)
    else:
        st.info(
            f"Single-window snapshot ({date_min} → {date_max}) — no day-by-day trend to chart. "
            "Budget recommendations below are based on the window's ROAS level only."
        )

    st.subheader("Budget allocation recommendations")
    if hdg:
        st.caption(
            "Each campaign's window is split into an early half and a late half; the late-half ROAS is "
            "compared to the campaign's own early-half ROAS and to the benchmark to recommend **Scale** "
            "(at/above benchmark, holding or rising), **Cut** (below break-even, or fell sharply), "
            "or **Maintain** (in between)."
        )
    else:
        st.caption(
            "No day-over-day data in this file, so there's no trend to compare — each campaign is "
            "classified on its window ROAS alone: **Scale** (at/above benchmark), **Cut** (below "
            "break-even), **Maintain** (in between)."
        )
    camp_df = az.classify_campaigns(
        df, benchmark_roas=benchmark_roas, has_daily_granularity=hdg, breakeven_roas=breakeven_roas,
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
            "late_roas": st.column_config.NumberColumn("Late-window ROAS (avg)", format="%.2fx"),
            "trend_pct": st.column_config.NumberColumn("Trend", format="%.0f%%"),
            "latest_day_roas": st.column_config.NumberColumn(
                "Most recent day", format="%.2fx",
                help="ROAS on the single most recent active day — can differ from the late-window "
                     "average above when the late window spans several days and the very latest one "
                     "has already turned around.",
            ),
            "recommendation": st.column_config.TextColumn("Recommendation"),
            "rationale": st.column_config.TextColumn("Why", width="large"),
        },
    )

# ---------------------------------------------------------------------------
# TAB 2 — Ad set
# ---------------------------------------------------------------------------
with tab_adset:
    with st.expander("ℹ️ How anomalies are flagged"):
        st.markdown(
            "Day-over-day **percent change** in CPC and CTR versus the previous active day — "
            "deliberately not a z-score, since 3–7 days isn't enough to estimate a stable standard "
            "deviation. Days under $5 spend are skipped as comparison points, so a jump from $1 to $3 "
            "of spend doesn't register as a 'spike.'\n"
            "- **CPC spike** — CPC rose more than the threshold (default +50%) vs. the prior day.\n"
            "- **CTR drop** — CTR fell more than the threshold (default -40%) vs. the prior day.\n\n"
            "Both thresholds are adjustable in the sidebar. This needs at least two comparable days per "
            "ad set, so it's unavailable for single-window snapshot files (nothing to compare against)."
        )
    st.subheader("Ad set performance")
    anomalies = az.detect_adset_anomalies(
        df, has_daily_granularity=hdg, cpc_spike_pct=cpc_spike_pct, ctr_drop_pct=ctr_drop_pct
    )
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
    if not hdg:
        st.info(
            "This file has no day-over-day breakdown (single-window snapshot), so there's nothing to "
            "compare a day against — anomaly detection needs at least two days per ad set. Upload a "
            "multi-day export to use this section."
        )
    else:
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
    with st.expander("ℹ️ How ad status is decided"):
        st.markdown(
            "Checked in order — **data sufficiency before performance** — so a low-signal ad is never "
            "mistaken for a genuinely bad one:\n"
            "1. ⬜ **Insufficient history** — fewer than the minimum active days (daily files only; "
            "skipped for snapshot files, since one aggregated row can't say how many days it covers).\n"
            "2. ⚪ **Low delivery** — total window spend is below the minimum spend floor. The ROAS "
            "number here is sampling noise, not a verdict — check delivery/budget, not the creative.\n"
            "3. 🔴 **Critical** — ROAS is below break-even.\n"
            "4. 🟠 **Warning** — ROAS is below the refresh-trigger fraction (default 70%) of the benchmark.\n"
            "5. 🟢 **Healthy** — everything else.\n\n"
            "**Daily files only**: `spend_trend_ratio` compares an ad's most recent day(s) of spend to "
            "its own peak day in the window. When that ratio is low, delivery is winding down on its "
            "own — a different problem (and a different fix) from a steady-spend ad that's genuinely "
            "underperforming, so it's called out separately in the recommendation text rather than "
            "folded into the ROAS number."
        )
    st.subheader("Ad performance — fatigue and winners")
    st.caption(
        "Status is checked for **data sufficiency before performance**: an ad with too little spend this "
        "window is flagged as such, not judged as good or bad. Only ads with enough signal are classified "
        "against the ROAS benchmark."
        + ("" if hdg else " (Single-window snapshot: the active-days check is skipped — a single aggregated "
                            "row can't tell us how many distinct days it covers — so only the spend floor applies.)")
    )
    ads_df = az.classify_ads(
        df, benchmark_roas=benchmark_roas, has_daily_granularity=hdg, breakeven_roas=breakeven_roas,
        min_window_spend=min_window_spend, min_active_days=min_active_days, refresh_ratio=refresh_ratio,
    )
    status_counts = ads_df["status"].value_counts().to_dict()
    st.plotly_chart(
        ch.status_count_chart(status_counts, STATUS_ORDER, STATUS_LABELS, ch.STATUS_COLORS),
        use_container_width=True,
    )

    display_df = ads_df.copy()
    display_df["status_label"] = display_df["status"].map(STATUS_LABELS)

    base_cols = ["ad", "creative_type", "status_label", "spend", "revenue", "roas"]
    trend_cols = ["roas_trend", "ctr_trend", "spend_trend_ratio", "delivery_declining"] if hdg else []
    tail_cols = ["recommendation"]

    col_config = {
        "ad": st.column_config.TextColumn("Ad", width="medium"),
        "creative_type": st.column_config.TextColumn("Type"),
        "status_label": st.column_config.TextColumn("Status"),
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
    }
    st.dataframe(
        display_df[base_cols + trend_cols + tail_cols],
        use_container_width=True, hide_index=True, column_config=col_config,
    )

    csv_bytes = ads_df.drop(columns=["ctr_trend", "roas_trend", "spend_trend"]).to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download ad-level table as CSV", data=csv_bytes,
        file_name=f"meta_ads_check_{date_max.isoformat()}.csv", mime="text/csv",
    )

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
# TAB 4 — Creative (re-uploads/variants rolled up)
# ---------------------------------------------------------------------------
with tab_creative:
    st.subheader("Creative rollup")
    st.caption(
        "The same underlying creative is often re-uploaded as a new ad-entry (a duplicated \"ad copy\" "
        "for testing, or the same file re-used across ad sets) — `Ad name` alone over-counts how many "
        "distinct creatives are really running. Rows here are grouped by a parsed creative id: the "
        "`#TAG#` code embedded in the ad name when present (most reliable), otherwise the ad name with "
        "variant markers (`广告副本`, `Copy`) stripped off. A generic tag used across many unrelated "
        "images (e.g. a catch-all `#PIC#`) will still roll those up together even though they aren't the "
        "same creative — treat any single-tag group with an unusually high ad-entry count as a hint to "
        "open it up rather than as a guaranteed single creative."
    )
    creatives = az.creative_rollup(df)
    st.plotly_chart(ch.creative_spend_chart(creatives), use_container_width=True)

    by_type = creatives.groupby("creative_type", as_index=False).agg(
        spend=("spend", "sum"), revenue=("revenue", "sum"), n_ad_entries=("n_ad_entries", "sum"),
    )
    by_type["roas"] = by_type["revenue"] / by_type["spend"].replace(0, pd.NA)
    tc1, tc2 = st.columns(2)
    tc1.plotly_chart(ch.creative_type_bar(by_type, "spend", "Spend by creative type", "Spend (USD)"), use_container_width=True)
    tc2.plotly_chart(ch.creative_type_bar(by_type, "roas", "Blended ROAS by creative type", "ROAS (x)"), use_container_width=True)

    st.dataframe(
        creatives,
        use_container_width=True, hide_index=True,
        column_config={
            "creative_id": st.column_config.TextColumn("Creative"),
            "creative_type": st.column_config.TextColumn("Type"),
            "n_ad_entries": st.column_config.NumberColumn("Ad-entries"),
            "n_variants": st.column_config.NumberColumn("Of which variants"),
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
        },
    )

    st.divider()
    st.subheader("📐 Recommended ad naming template")
    st.caption(
        "Observed in the ad names actually in this account: creative-type tokens spelled several "
        "ways (`Ai`/`AI`/`ai`, `image`/`Image`), dates in two formats (`20260811` vs `2.28`), the same "
        "`#PIC#` tag reused across dozens of unrelated images (so it doesn't identify one creative), "
        "and re-uploads stacked as `- 广告副本 - 广告副本 - 广告副本...` up to six times deep. None of "
        "that breaks Meta, but all of it makes automated grouping (this app, or any future analysis) "
        "guess instead of know. A single consistent template fixes all four at once."
    )
    st.markdown(
        "```\n"
        "{TYPE}-{PRODUCT}-#{TYPE}{SEQ}#-{YYYYMMDD}-{DESCRIPTOR}[-V{n}]\n"
        "```"
    )
    naming_guide = pd.DataFrame([
        {"Content type": "AI-generated", "TYPE code": "AI", "Example": "AI-LNV-#AI0057#-20260827-暮雨-秋季新品"},
        {"Content type": "KOL / UGC", "TYPE code": "KOL", "Example": "KOL-LNV-#KOL0142#-20260827-claudia12xo"},
        {"Content type": "Static image", "TYPE code": "IMG", "Example": "IMG-YZJ-#IMG0033#-20260827-产品图-V2"},
        {"Content type": "Video (produced, non-AI/KOL)", "TYPE code": "VID", "Example": "VID-LNV-#VID0011#-20260827-开箱视频"},
        {"Content type": "Feed / catalog", "TYPE code": "FEED", "Example": "FEED-LNV-DPA-Broad"},
    ])
    st.dataframe(naming_guide, use_container_width=True, hide_index=True)
    st.markdown(
        "- **`{TYPE}`** — always the first token, always one of the five fixed codes above (uppercase). "
        "This is what `creative_type` classification keys off; a fixed vocabulary beats free text.\n"
        "- **`{PRODUCT}`** — a short account/product code (e.g. `LNV` for LinenVibes, `YZJ`) — makes "
        "the account filter and per-product rollups reliable without reading `Account name` separately.\n"
        "- **`#{TYPE}{SEQ}#`** — the creative's permanent tag: type prefix + a zero-padded sequence "
        "number, assigned **once** when the creative is first made. Reuse it only for a literal "
        "re-upload of that exact asset — never for a different image/video that happens to share a "
        "generic label like `#PIC#`. This is the single biggest lever: it's what makes the Creative "
        "Rollup table above actually mean 'one row = one creative.'\n"
        "- **`{YYYYMMDD}`** — one date format, always 8 digits, always the creative's original "
        "creation date. (`2.28` and `20260228` both showed up in this account's history — pick one.)\n"
        "- **`{DESCRIPTOR}`** — free text: model/creator name, concept, angle — whatever's useful to a "
        "human scanning the list. Order and content don't matter to the app; only the fields before it do.\n"
        "- **`[-V{n}]`** — only when re-entering the *identical* creative as a new ad-entry (a fresh "
        "test, a reset after Meta's algorithm deprioritized it). Increment a single integer "
        "(`-V2`, `-V3`) instead of stacking `- 广告副本` repeatedly — both this app and a human reading "
        "the name can tell at a glance how many versions exist.\n\n"
        "Feed/catalog ads are usually a standing, persistent ad rather than a one-off creative, so a "
        "stable descriptive name (audience + purpose) matters more than a date/sequence there."
    )
