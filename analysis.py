"""
Pure data-processing logic for the Meta Ads daily checker.

Deliberately kept Streamlit-free so it can be tested and reasoned about on
its own — `app.py` only calls into this module and draws the results.

Scope: ads-level data only (Account / Campaign / Ad set / Ad). Two
objectives: budget allocation at the Campaign and Ad set level, and granular
performance evaluation of individual ads.

Handles two shapes of Meta Ads Manager export:

1. **Daily** — one row per (ad, day), 3+ days of history. Enables trend-based
   analysis: a weighted-regression ROAS trend per campaign/ad set/ad, and
   pooled multivariate anomaly detection across ad sets.
2. **Snapshot** — one row per ad, aggregated over a single reporting window
   (no Day column, or only one distinct date). No trend is possible from one
   point, so this mode falls back to threshold classification on the window
   level and says plainly where trend data is unavailable.

`load_and_standardize` detects which shape it's looking at and returns a
`has_daily_granularity` flag; every downstream function takes that flag and
adjusts what it computes accordingly.

Core rules, applied consistently at every level (account / campaign / ad set
/ ad):

1. Ratios are always a ratio-of-sums, never an average-of-ratios. ROAS, CTR,
   CPC, and CPM are recomputed from summed raw components after grouping —
   never averaged directly, which would silently misweight low-volume rows.
2. A verdict is only as good as the sample behind it. Every classification
   checks "is there enough spend/history to trust this number" *before* it
   checks "is the number good or bad."
3. A trend is only trusted when it's actually a good fit to the data, not
   just whatever direction two arbitrary buckets happen to point. A
   weighted linear regression (numpy, weighted by daily spend) is fit
   through every available day; its R² gates whether the fitted trend
   is used at all. A noisy, non-monotonic run of days (e.g. a dip that
   already recovered) naturally gets a low R² and the classification
   falls back to the plain window-average ROAS instead of an unreliable
   direction — no special-casing needed for "what if the most recent day
   already turned around," the confidence gate handles it structurally.
4. A totals/summary row (every hierarchy column blank, metrics populated —
   the row Meta puts at the top of some exports) is detected and excluded
   from every breakdown automatically, and used only as a sanity check
   against the sum of the real rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Column resolution — real Meta Ads Manager exports vary in exact header
# text depending on which columns/breakdowns were selected. Each logical
# field lists every header text we've seen or reasonably expect; the first
# match (case-insensitive, whitespace-normalized) wins.
# ---------------------------------------------------------------------------
COLUMN_ALIASES: dict[str, list[str]] = {
    "day": ["day", "date"],
    "account": ["account name", "account"],
    "campaign": ["campaign name", "campaign"],
    "adset": ["ad set name", "adset name", "ad set"],
    "ad": ["ad name"],
    "spend": ["amount spent (usd)", "amount spent", "spend"],
    "purchases": ["purchases", "website purchases", "results"],
    "revenue": [
        "purchases conversion value", "website purchases conversion value",
        "purchase value", "conversion value", "revenue",
    ],
    "roas": [
        "roi", "roas", "purchase roas (return on ad spend)",
        "website purchase roas (return on ad spend)",
    ],
    "impressions": ["impressions"],
    "link_clicks": ["link clicks", "clicks (all)", "clicks"],
    "ctr": ["ctr (link click-through rate)", "ctr (all)", "ctr", "link ctr"],
    "cpc": ["cpc (cost per link click)", "cpc (all)", "cpc"],
    "cpm": ["cpm (cost per 1,000 impressions)", "cpm"],
    "frequency": ["frequency"],
    "reach": ["reach"],
    "reporting_starts": ["reporting starts"],
    "reporting_ends": ["reporting ends"],
}

REQUIRED_LOGICAL_FIELDS = ["spend"]


def _normalize(s: str) -> str:
    return " ".join(str(s).strip().lower().split())


def resolve_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """Map logical field name -> actual column name in `df` (or None)."""
    normalized_lookup = {_normalize(c): c for c in df.columns}
    resolved: dict[str, str | None] = {}
    for field_name, candidates in COLUMN_ALIASES.items():
        resolved[field_name] = next(
            (normalized_lookup[c] for c in candidates if c in normalized_lookup), None
        )
    return resolved


class ColumnResolutionError(ValueError):
    pass


def classify_creative_type(name: str) -> str:
    """Lightweight content-type tag inferred from the ad name itself (not a
    separate creative-level dataset) — useful context for the ad-level view."""
    s = name.lower()
    if "feed" in s or "目录" in name:
        return "Feed / catalog"
    if "kol" in s or "ugc" in s:
        return "KOL/UGC"
    if "ai" in s:
        return "AI-generated"
    if "image" in s or "图片" in s or "pic" in s:
        return "Static image"
    if "视频" in s or "video" in s:
        return "Video"
    return "Other"


@dataclass
class LoadResult:
    df: pd.DataFrame
    resolved: dict[str, str | None]
    notes: list[str] = field(default_factory=list)
    has_daily_granularity: bool = True
    window_start: pd.Timestamp | None = None
    window_end: pd.Timestamp | None = None
    totals_row_check: dict | None = None


def load_and_standardize(raw: pd.DataFrame) -> LoadResult:
    """
    Take a raw Meta Ads Manager export and return a standardized DataFrame
    with canonical columns: day, account, campaign, adset, ad, creative_type,
    spend, purchases, revenue, impressions, link_clicks, cpm, cpc, ctr,
    frequency, reach.
    """
    resolved = resolve_columns(raw)
    missing_required = [f for f in REQUIRED_LOGICAL_FIELDS if resolved[f] is None]
    if missing_required:
        raise ColumnResolutionError(
            f"Couldn't find required column(s) for: {', '.join(missing_required)}. "
            f"Columns found in file: {', '.join(map(str, raw.columns))}"
        )

    notes: list[str] = []

    # --- Detect and split off a totals/summary row: every hierarchy column
    # that IS present in the file is blank, but spend is populated. Common
    # as the first data row in Meta exports.
    hierarchy_cols_present = [resolved[l] for l in ("account", "campaign", "adset", "ad") if resolved[l]]
    totals_row_check = None
    working = raw
    if hierarchy_cols_present:
        is_blank_hierarchy = pd.concat(
            [raw[c].isna() for c in hierarchy_cols_present], axis=1
        ).all(axis=1)
        has_spend = pd.to_numeric(raw[resolved["spend"]], errors="coerce").fillna(0) > 0
        summary_mask = is_blank_hierarchy & has_spend
        if summary_mask.any():
            summary_rows = raw[summary_mask]
            working = raw[~summary_mask].copy()
            notes.append(
                f"Detected {summary_mask.sum()} summary/total row(s) (blank campaign/ad set/ad, "
                "populated totals) — excluded from every breakdown below and used only as a sanity check."
            )
            totals_row_check = {"summary_rows": summary_rows, "resolved": resolved}

    df = pd.DataFrame(index=working.index)

    # --- Day / granularity detection ---------------------------------------
    day_col = resolved["day"] or resolved["reporting_starts"]
    if day_col is not None:
        df["day"] = pd.to_datetime(working[day_col])
    elif resolved["reporting_ends"] is not None:
        df["day"] = pd.to_datetime(working[resolved["reporting_ends"]])
        notes.append("No Day column found — using 'Reporting ends' as a single snapshot date.")
    else:
        df["day"] = pd.Timestamp.today().normalize()
        notes.append("No Day/Reporting date column found — treating this file as a single undated snapshot.")

    has_daily_granularity = df["day"].nunique() > 1
    if not has_daily_granularity:
        notes.append(
            "This file has only a single reporting date/window (no day-by-day breakdown) — trend-based "
            "features (campaign/ad set trend, anomaly detection, ad ROAS/CTR history) aren't available "
            "and are replaced with single-window equivalents."
        )

    window_start = pd.to_datetime(working[resolved["reporting_starts"]]).min() if resolved["reporting_starts"] else df["day"].min()
    window_end = pd.to_datetime(working[resolved["reporting_ends"]]).max() if resolved["reporting_ends"] else df["day"].max()

    df["spend"] = pd.to_numeric(working[resolved["spend"]], errors="coerce").fillna(0.0)

    for level, label in [("account", "Account"), ("campaign", "Campaign"), ("adset", "Ad set"), ("ad", "Ad")]:
        col = resolved[level]
        if col is not None:
            df[level] = working[col].astype(str)
        else:
            df[level] = f"(no {label.lower()} column in file)"
            notes.append(
                f"No '{label} name' column found — this export can't be broken out by {label.lower()}, "
                f"so every row is grouped under a single placeholder {label.lower()}."
            )

    df["creative_type"] = df["ad"].apply(classify_creative_type)

    df["purchases"] = pd.to_numeric(working.get(resolved["purchases"]), errors="coerce").fillna(0.0) \
        if resolved["purchases"] else 0.0

    if resolved["revenue"]:
        df["revenue"] = pd.to_numeric(working[resolved["revenue"]], errors="coerce").fillna(0.0)
    elif resolved["roas"]:
        # Reconstruct revenue from a provided ROAS/ROI column so it can still
        # be summed correctly when aggregating (ROAS itself must never be summed).
        roas = pd.to_numeric(working[resolved["roas"]], errors="coerce").fillna(0.0)
        df["revenue"] = roas * df["spend"]
        notes.append("No purchase-value column found — revenue was reconstructed as ROAS × spend from the file's ROAS column.")
    else:
        df["revenue"] = 0.0
        notes.append("No purchase-value or ROAS column found — revenue/ROAS/CPA will show as zero throughout.")

    for field_name in ["impressions", "link_clicks", "frequency", "reach"]:
        col = resolved[field_name]
        df[field_name] = pd.to_numeric(working.get(col), errors="coerce").fillna(0.0) if col else 0.0

    if totals_row_check is not None:
        summary_rows = totals_row_check.pop("summary_rows")
        computed_spend = df["spend"].sum()
        file_spend = pd.to_numeric(summary_rows[resolved["spend"]], errors="coerce").sum()
        matches = abs(computed_spend - file_spend) < max(1.0, 0.01 * file_spend)
        totals_row_check.update({
            "file_spend": file_spend, "computed_spend": computed_spend, "matches": matches,
        })
        if not matches:
            notes.append(
                f"⚠️ The file's own summary-row spend (${file_spend:,.2f}) doesn't match the sum of the "
                f"detail rows (${computed_spend:,.2f}) — double check nothing else was filtered out of this export."
            )

    return LoadResult(
        df=df, resolved=resolved, notes=notes, has_daily_granularity=has_daily_granularity,
        window_start=window_start, window_end=window_end, totals_row_check=totals_row_check,
    )


# ---------------------------------------------------------------------------
# Aggregation — always sum raw components first, recompute ratios after.
# ---------------------------------------------------------------------------
def add_ratio_columns(g: pd.DataFrame) -> pd.DataFrame:
    g = g.copy()
    g["roas"] = np.where(g["spend"] > 0, g["revenue"] / g["spend"], np.nan)
    g["cpa"] = np.where(g["purchases"] > 0, g["spend"] / g["purchases"], np.nan)
    g["ctr"] = np.where(g["impressions"] > 0, g["link_clicks"] / g["impressions"] * 100, np.nan)
    g["cpc"] = np.where(g["link_clicks"] > 0, g["spend"] / g["link_clicks"], np.nan)
    g["cpm"] = np.where(g["impressions"] > 0, g["spend"] / g["impressions"] * 1000, np.nan)
    g["frequency"] = np.where(g["reach"] > 0, g["impressions"] / g["reach"], np.nan)
    return g


SUM_COLS = ["spend", "purchases", "revenue", "impressions", "link_clicks", "reach"]


def daily_rollup(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """One row per (group, day), summed then re-ratio'd."""
    g = df.groupby(group_cols + ["day"], as_index=False)[SUM_COLS].sum()
    return add_ratio_columns(g)


def window_rollup(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """One row per group across the whole window, summed then re-ratio'd."""
    g = df.groupby(group_cols, as_index=False)[SUM_COLS].sum()
    active_days = df.groupby(group_cols)["day"].nunique().rename("active_days")
    g = g.merge(active_days, on=group_cols, how="left")
    return add_ratio_columns(g)


# ---------------------------------------------------------------------------
# Weighted-regression trend — the core statistical upgrade. Used identically
# at the campaign, ad set, and ad level so "potential" means the same thing
# everywhere: a spend-weighted linear fit of daily ROAS across every
# available day (not just two arbitrary buckets), gated by how good that fit
# actually is (R²) before it's trusted for a decision.
# ---------------------------------------------------------------------------
@dataclass
class Trend:
    n: int
    slope_per_day: float = np.nan   # ROAS change per day, in x/day
    r2: float = np.nan              # weighted goodness-of-fit, 0-1
    fitted_start: float = np.nan    # fitted ROAS at the first day (>= 0, clipped)
    fitted_end: float = np.nan      # fitted ROAS at the last day (>= 0, clipped)
    confident: bool = False


def weighted_roas_trend(days: pd.Series, roas: pd.Series, weights: pd.Series,
                         min_points: int = 4, r2_threshold: float = 0.35) -> Trend:
    days = pd.to_datetime(pd.Series(days))
    roas = pd.Series(roas).to_numpy(dtype=float)
    weights = pd.Series(weights).to_numpy(dtype=float)
    mask = np.isfinite(roas) & np.isfinite(weights) & (weights >= 0)
    roas, weights = roas[mask], weights[mask]
    day_idx = (days[mask] - days[mask].min()).dt.days.to_numpy(dtype=float)
    n = len(roas)
    if n < 3 or weights.sum() <= 0:
        return Trend(n=n)

    w = np.sqrt(weights)
    coeffs = np.polyfit(day_idx, roas, deg=1, w=w)
    pred = np.polyval(coeffs, day_idx)
    wmean = np.average(roas, weights=weights)
    ss_tot = np.sum(weights * (roas - wmean) ** 2)
    ss_res = np.sum(weights * (roas - pred) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    fitted_start = max(0.0, float(np.polyval(coeffs, day_idx.min())))
    fitted_end = max(0.0, float(np.polyval(coeffs, day_idx.max())))
    confident = n >= min_points and r2 >= r2_threshold

    return Trend(n=n, slope_per_day=float(coeffs[0]), r2=float(r2),
                 fitted_start=fitted_start, fitted_end=fitted_end, confident=confident)


# ---------------------------------------------------------------------------
# Budget allocation — Campaign AND Ad set, same rule at both grains:
#
# 1. Cut — today's spend is over `cut_today_spend`, OR cumulative window
#    spend is over `cut_cumulative_spend`, AND there have been zero sales on
#    EACH of the last `cut_no_sales_days` calendar days in the file. Spend
#    is being wasted with no recent payoff.
# 2. Scale — otherwise, if today's ROAS is at/above the benchmark, OR the
#    weighted-regression trend (same fit used elsewhere) is confidently
#    improving. Either signal is a reason to push more budget in.
# 3. Maintain — otherwise: no cut signal, no scale signal — typically a
#    modest-spend entity where a quiet sales day isn't yet a red flag.
#
# Needs daily data for "today" and the no-sales-streak check. A single-
# window snapshot has no "today" to isolate, so it treats the whole window
# as today and the no-sales-streak check never fires (undefined for one
# data point) — Cut can't trigger from that path in snapshot mode.
# ---------------------------------------------------------------------------
def classify_budget_level(
    df: pd.DataFrame,
    level_col: str,
    benchmark_roas: float,
    has_daily_granularity: bool = True,
    cut_today_spend: float = 50.0,
    cut_cumulative_spend: float = 100.0,
    cut_no_sales_days: int = 3,
    r2_threshold: float = 0.35,
    reference_days: list | None = None,
) -> pd.DataFrame:
    all_days = sorted(pd.to_datetime(pd.Series(reference_days)).unique()) if reference_days is not None \
        else sorted(df["day"].unique())
    latest_day = all_days[-1] if len(all_days) else None
    last_n_days = all_days[-cut_no_sales_days:] if len(all_days) >= cut_no_sales_days else None

    rows = []
    for entity, g in df.groupby(level_col):
        g = g.sort_values("day")
        n_days = g["day"].nunique()
        total_spend = g["spend"].sum()
        total_revenue = g["revenue"].sum()
        total_purchases = g["purchases"].sum()
        overall_roas = total_revenue / total_spend if total_spend > 0 else np.nan
        overall_cpa = total_spend / total_purchases if total_purchases > 0 else np.nan

        if has_daily_granularity and latest_day is not None:
            today = g[g["day"] == latest_day]
            today_spend = today["spend"].sum()
            today_revenue = today["revenue"].sum()
            today_roas = today_revenue / today_spend if today_spend > 0 else np.nan
        else:
            today_spend, today_roas = total_spend, overall_roas

        no_sales_recent = False
        if has_daily_granularity and last_n_days is not None:
            purchases_by_day = g[g["day"].isin(last_n_days)].groupby("day")["purchases"].sum()
            no_sales_recent = all(purchases_by_day.get(d, 0.0) == 0.0 for d in last_n_days)

        trend = Trend(n=0)
        if has_daily_granularity:
            daily = g.groupby("day")[["spend", "revenue"]].sum()
            daily_roas = daily["revenue"] / daily["spend"].replace(0, np.nan)
            trend = weighted_roas_trend(daily.index.to_series(), daily_roas, daily["spend"], r2_threshold=r2_threshold)

        cut_condition = (today_spend > cut_today_spend or total_spend > cut_cumulative_spend) and no_sales_recent
        high_roi = pd.notna(today_roas) and today_roas >= benchmark_roas
        good_trend = trend.confident and trend.slope_per_day > 0

        if cut_condition:
            rec = "Cut"
            why = (f"${today_spend:.0f} spent today (${total_spend:.0f} cumulative) with zero sales across "
                   f"the last {cut_no_sales_days} days — spend isn't converting.")
        elif high_roi or good_trend:
            rec = "Scale"
            if high_roi:
                why = f"Today's ROAS {today_roas:.2f}x is at/above the {benchmark_roas:.1f}x benchmark."
            else:
                why = (f"Today's ROAS isn't yet at benchmark, but the trend across {trend.n} days is confidently "
                       f"improving (R²={trend.r2:.2f}, {trend.slope_per_day:+.3f}x/day).")
        else:
            rec = "Maintain"
            why = (f"No cut or scale signal — today's spend (${today_spend:.0f}) and trend don't call for a "
                   "change yet.")

        rows.append({
            level_col: entity, "active_days": n_days, "spend": total_spend, "revenue": total_revenue,
            "roas": overall_roas, "cpa": overall_cpa, "today_spend": today_spend, "today_roas": today_roas,
            "no_sales_recent": no_sales_recent, "trend_slope": trend.slope_per_day, "trend_r2": trend.r2,
            "trend_confident": trend.confident, "recommendation": rec, "rationale": why,
        })
    return pd.DataFrame(rows).sort_values("spend", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Anomaly detection — pooled multivariate outlier model (Isolation Forest)
# across every (group, day), z-scored within each group first (so "unusual"
# means unusual FOR THAT ad set/ad, not just a bigger one). Works at either
# grain via `group_col` ("adset" or "ad"). Falls back to a simple
# day-over-day percent-change rule when there isn't enough pooled data for a
# model to be meaningful, or no daily granularity at all.
# ---------------------------------------------------------------------------
def detect_anomalies(
    df: pd.DataFrame,
    group_col: str,
    has_daily_granularity: bool = True,
    min_day_spend: float = 5.0,
    contamination: float = 0.08,
    min_rows_for_ml: int = 30,
    min_days_per_group: int = 3,
) -> tuple[pd.DataFrame, str, pd.DataFrame | None]:
    """Returns (flags_df, method, scored_pool). method is 'ml', 'heuristic', or
    'unavailable'. scored_pool (only set for 'ml') carries every pooled
    (group, day) with its z-scores and anomaly flag — not just the flagged
    rows, so a scatter chart can show normal points too."""
    if not has_daily_granularity:
        return pd.DataFrame(columns=[group_col, "day", "type", "detail", "score"]), "unavailable", None

    daily = daily_rollup(df, [group_col])
    daily = daily[daily["spend"] >= min_day_spend].copy()

    eligible = daily.groupby(group_col)["day"].transform("count") >= min_days_per_group
    pool = daily[eligible].copy()

    if len(pool) >= min_rows_for_ml:
        from sklearn.ensemble import IsolationForest

        pool["z_ctr"] = pool.groupby(group_col)["ctr"].transform(lambda s: (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) > 0 else 0.0)
        pool["z_cpc"] = pool.groupby(group_col)["cpc"].transform(lambda s: (s - s.mean()) / s.std(ddof=0) if s.std(ddof=0) > 0 else 0.0)
        features = pool[["z_ctr", "z_cpc"]].fillna(0.0)

        model = IsolationForest(n_estimators=200, contamination=contamination, random_state=42)
        raw_labels = model.fit_predict(features)  # -1 = anomaly, 1 = normal
        pool["is_anomaly"] = raw_labels == -1
        pool["score"] = -model.score_samples(features)  # higher = more anomalous

        flagged = pool[pool["is_anomaly"]].copy()
        flags = []
        for _, r in flagged.iterrows():
            reasons = []
            if abs(r["z_ctr"]) >= 1.0:
                reasons.append(f"CTR {r['z_ctr']:+.1f}σ vs. its own average ({r['ctr']:.2f}%)")
            if abs(r["z_cpc"]) >= 1.0:
                reasons.append(f"CPC {r['z_cpc']:+.1f}σ vs. its own average (${r['cpc']:.2f})")
            if not reasons:
                reasons.append(f"CTR {r['ctr']:.2f}%, CPC ${r['cpc']:.2f} — unusual combination")
            flags.append({group_col: r[group_col], "day": r["day"], "type": "Anomaly",
                          "detail": "; ".join(reasons), "score": r["score"]})
        return pd.DataFrame(flags), "ml", pool

    # Fallback: day-over-day percent change, too little pooled data for ML to be meaningful.
    flags = []
    for key, g in daily.groupby(group_col):
        g = g.sort_values("day").reset_index(drop=True)
        if len(g) < 2:
            continue
        g["cpc_pct_change"] = g["cpc"].pct_change()
        g["ctr_pct_change"] = g["ctr"].pct_change()
        for _, r in g[g["cpc_pct_change"] > 0.5].iterrows():
            flags.append({group_col: key, "day": r["day"], "type": "CPC spike",
                          "detail": f"CPC +{r['cpc_pct_change']:.0%} to ${r['cpc']:.2f}", "score": np.nan})
        for _, r in g[g["ctr_pct_change"] < -0.4].iterrows():
            flags.append({group_col: key, "day": r["day"], "type": "CTR drop",
                          "detail": f"CTR {r['ctr_pct_change']:.0%} to {r['ctr']:.2f}%", "score": np.nan})
    return pd.DataFrame(flags), "heuristic", None


# ---------------------------------------------------------------------------
# Ad: granular performance evaluation
# ---------------------------------------------------------------------------
def classify_ads(
    df: pd.DataFrame,
    benchmark_roas: float,
    has_daily_granularity: bool = True,
    breakeven_roas: float = 1.0,
    min_window_spend: float = 15.0,
    min_active_days: int = 3,
    refresh_ratio: float = 0.7,
    declining_spend_ratio: float = 0.3,
    declining_lookback: int = 2,
    r2_threshold: float = 0.35,
) -> pd.DataFrame:
    """Per-ad rollup + status classification. Data sufficiency is checked
    before performance: an ad with too little history or spend is flagged
    as such, not judged as good or bad."""
    rows = []
    for ad, g in df.groupby("ad"):
        g = g.sort_values("day")
        active_days = g["day"].nunique()
        total_spend = g["spend"].sum()
        total_revenue = g["revenue"].sum()
        roas = total_revenue / total_spend if total_spend > 0 else np.nan

        trend = Trend(n=0)
        if has_daily_granularity:
            daily = g.groupby("day")[["spend", "revenue"]].sum()
            daily_roas = daily["revenue"] / daily["spend"].replace(0, np.nan)
            trend = weighted_roas_trend(daily.index.to_series(), daily_roas, daily["spend"], r2_threshold=r2_threshold)

            peak_daily_spend = daily["spend"].max()
            recent = daily["spend"].sort_index().tail(min(declining_lookback, len(daily)))
            spend_trend_ratio = recent.mean() / peak_daily_spend if peak_daily_spend > 0 else np.nan
            delivery_declining = bool(pd.notna(spend_trend_ratio) and spend_trend_ratio < declining_spend_ratio)
        else:
            spend_trend_ratio, delivery_declining = np.nan, False

        decision_level = trend.fitted_end if trend.confident else roas

        day_gate_ok = active_days >= min_active_days if has_daily_granularity else True
        if not day_gate_ok:
            status, rec = "insufficient_history", f"Wait — only {active_days} active day(s)."
        elif total_spend < min_window_spend:
            note = " Delivery is also actively declining." if delivery_declining else ""
            status, rec = "low_delivery", f"Check delivery/budget, not creative — only ${total_spend:.0f} spent.{note}"
        elif roas < breakeven_roas or decision_level < breakeven_roas:
            note = " (delivery is also collapsing on its own)" if delivery_declining else ""
            if trend.confident:
                rec_txt = f"Refresh or pause — {roas:.2f}x blended, trend confidently declining toward {trend.fitted_end:.2f}x{note}."
            else:
                rec_txt = f"Refresh or pause — {roas:.2f}x, below break-even{note}."
            status, rec = "critical", rec_txt
        elif decision_level < benchmark_roas * refresh_ratio:
            note = " Delivery is also declining." if delivery_declining else ""
            status, rec = "warning", f"Watch closely — {roas:.2f}x, under {refresh_ratio:.0%} of benchmark.{note}"
        else:
            status, rec = "healthy", f"Healthy — {roas:.2f}x."

        rows.append({
            "ad": ad, "creative_type": g["creative_type"].iloc[0],
            "active_days": active_days, "spend": total_spend, "revenue": total_revenue,
            "roas": roas, "trend_slope": trend.slope_per_day, "trend_r2": trend.r2,
            "trend_confident": trend.confident, "spend_trend_ratio": spend_trend_ratio,
            "delivery_declining": delivery_declining, "status": status, "recommendation": rec,
        })

    out = pd.DataFrame(rows)
    status_order = {"critical": 0, "warning": 1, "low_delivery": 2, "insufficient_history": 3, "healthy": 4}
    out["_order"] = out["status"].map(status_order)
    return out.sort_values(["_order", "spend"], ascending=[True, False]).drop(columns="_order").reset_index(drop=True)


def topline_kpis(df: pd.DataFrame) -> dict:
    spend = df["spend"].sum()
    revenue = df["revenue"].sum()
    purchases = df["purchases"].sum()
    return {
        "spend": spend,
        "revenue": revenue,
        "roas": revenue / spend if spend > 0 else np.nan,
        "cpa": spend / purchases if purchases > 0 else np.nan,
        "purchases": purchases,
    }
