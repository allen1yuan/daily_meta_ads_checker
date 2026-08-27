"""
Pure data-processing logic for the Meta Ads daily checker.

Deliberately kept Streamlit-free so it can be tested and reasoned about on
its own — `app.py` only calls into this module and draws the results.

Handles two shapes of Meta Ads Manager export:

1. **Daily** — one row per (ad, day), 3-7 days of history. Enables trend-based
   analysis: campaign Scale/Maintain/Cut from early-vs-late ROAS, ad-set
   day-over-day CPC/CTR anomaly detection, ad-level ROAS/CTR sparklines.
2. **Snapshot** — one row per ad, aggregated over a single reporting window
   (no Day column, or a Day/"Reporting starts" column with only one distinct
   value). Common when Meta breaks an export out by Campaign/Ad set/Ad
   without also adding a Day breakdown. Trend-based features aren't possible
   from a single point, so this mode falls back to threshold-based
   classification and says plainly where trend data is unavailable rather
   than fabricating a trend from one number.

`load_and_standardize` detects which shape it's looking at and returns a
`has_daily_granularity` flag; every downstream function takes that flag and
adjusts what it computes accordingly.

Core rules, applied consistently at every level (account / campaign / ad set
/ ad / creative):

1. Ratios are always a ratio-of-sums, never an average-of-ratios. ROAS, CTR,
   CPC, and CPM are recomputed from summed raw components after grouping —
   never averaged directly, which would silently misweight low-volume rows.
2. A verdict is only as good as the sample behind it. Every classification
   checks "is there enough spend/history to trust this number" *before* it
   checks "is the number good or bad." An ad that spent $3 with zero
   purchases is `low_delivery`, not `critical` — there is nothing reliable
   to measure yet, and the fix (check delivery/budget) is different from the
   fix for a steady-spend ad that is genuinely underperforming.
3. A totals/summary row (every hierarchy column blank, metrics populated —
   the row Meta puts at the top of some exports) is detected and excluded
   from every breakdown automatically, and used only as a sanity check
   against the sum of the real rows.
"""

from __future__ import annotations

import re
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


# ---------------------------------------------------------------------------
# Creative parsing — same conventions as the account's EDA notebook: a
# `#TAG#`-delimited creative code is the most reliable id when present;
# "variant" markers (广告副本 / "Copy") mark a re-upload of the same
# underlying creative rather than a new one.
# ---------------------------------------------------------------------------
_VARIANT_PATTERN = re.compile(r"(\s*-\s*广告副本)|(\s*-\s*copy\b)", re.IGNORECASE)
_TAG_PATTERN = re.compile(r"#([^#]+)#")


def classify_creative_type(name: str) -> str:
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


def extract_creative_tag(name: str) -> str | None:
    m = _TAG_PATTERN.search(name)
    return m.group(1).upper() if m else None


def is_variant_name(name: str) -> bool:
    return bool(_VARIANT_PATTERN.search(name))


def base_creative_name(name: str) -> str:
    """Strip repeated variant suffixes to approximate the underlying creative."""
    prev = None
    s = name
    while prev != s:
        prev = s
        s = _VARIANT_PATTERN.sub("", s).strip()
    return s or name


def creative_id_for(name: str) -> str:
    """Prefer the explicit #tag# code; fall back to the variant-stripped name."""
    tag = extract_creative_tag(name)
    return tag if tag else base_creative_name(name)


@dataclass
class LoadResult:
    df: pd.DataFrame
    resolved: dict[str, str | None]
    notes: list[str] = field(default_factory=list)
    has_daily_granularity: bool = True
    window_start: pd.Timestamp | None = None
    window_end: pd.Timestamp | None = None
    totals_row_check: dict | None = None  # {"in_file": {...}, "computed": {...}, "matches": bool}


def load_and_standardize(raw: pd.DataFrame) -> LoadResult:
    """
    Take a raw Meta Ads Manager export and return a standardized DataFrame
    with canonical columns: day, account, campaign, adset, ad, creative_id,
    creative_type, is_variant, spend, purchases, revenue, impressions,
    link_clicks, cpm, cpc, ctr, frequency, reach.
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
            "features (campaign trend, ad-set day-over-day anomalies, ad ROAS/CTR sparklines) aren't "
            "available and are replaced with single-window equivalents."
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

    # --- Creative parsing (level 4: below individual ad-entries) -----------
    df["creative_type"] = df["ad"].apply(classify_creative_type)
    df["is_variant"] = df["ad"].apply(is_variant_name)
    df["creative_id"] = df["ad"].apply(creative_id_for)

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
# Level 1 — Campaign: multi-day trend -> Scale / Maintain / Cut
# (falls back to a single-window threshold read when there's no day-over-day
# data to compute a trend from)
# ---------------------------------------------------------------------------
def classify_campaigns(
    df: pd.DataFrame,
    benchmark_roas: float,
    has_daily_granularity: bool = True,
    breakeven_roas: float = 1.0,
    min_window_spend: float = 15.0,
    min_active_days: int = 2,
    cut_decline_pct: float = 0.30,
) -> pd.DataFrame:
    rows = []
    for camp, g in df.groupby("campaign"):
        g = g.sort_values("day")
        days = sorted(g["day"].unique())
        n = len(days)
        total_spend = g["spend"].sum()
        total_revenue = g["revenue"].sum()
        overall_roas = total_revenue / total_spend if total_spend > 0 else np.nan
        overall_cpa = total_spend / g["purchases"].sum() if g["purchases"].sum() > 0 else np.nan

        day_gate_ok = n >= min_active_days if has_daily_granularity else True
        if not day_gate_ok or total_spend < min_window_spend:
            rows.append({
                "campaign": camp, "active_days": n, "spend": total_spend, "revenue": total_revenue,
                "roas": overall_roas, "cpa": overall_cpa, "early_roas": np.nan, "late_roas": np.nan,
                "trend_pct": np.nan, "recommendation": "Insufficient data",
                "rationale": f"Only {n} day(s) / ${total_spend:.0f} spend in this window — not enough to judge.",
            })
            continue

        if not has_daily_granularity:
            # Single-window snapshot: no trend possible, judge on the level alone.
            if overall_roas < breakeven_roas:
                rec, why = "Cut", f"ROAS {overall_roas:.2f}x is below break-even (single-window snapshot — no trend available)."
            elif overall_roas >= benchmark_roas:
                rec, why = "Scale", f"ROAS {overall_roas:.2f}x is at/above the {benchmark_roas:.1f}x benchmark (single-window snapshot — no trend available)."
            else:
                rec, why = "Maintain", f"ROAS {overall_roas:.2f}x is between break-even and benchmark (single-window snapshot — no trend available)."
            rows.append({
                "campaign": camp, "active_days": n, "spend": total_spend, "revenue": total_revenue,
                "roas": overall_roas, "cpa": overall_cpa, "early_roas": np.nan, "late_roas": np.nan,
                "trend_pct": np.nan, "recommendation": rec, "rationale": why,
            })
            continue

        half = max(1, n // 2)
        early_days, late_days = days[:half], days[half:]
        early = g[g["day"].isin(early_days)]
        late = g[g["day"].isin(late_days)]
        early_roas = early["revenue"].sum() / early["spend"].sum() if early["spend"].sum() > 0 else np.nan
        late_roas = late["revenue"].sum() / late["spend"].sum() if late["spend"].sum() > 0 else np.nan
        trend_pct = (late_roas - early_roas) / early_roas if (pd.notna(early_roas) and early_roas > 0) else np.nan

        if late_roas < breakeven_roas or (pd.notna(trend_pct) and trend_pct <= -cut_decline_pct):
            rec = "Cut"
            why = (f"Late-window ROAS {late_roas:.2f}x is below break-even." if late_roas < breakeven_roas
                   else f"ROAS fell {abs(trend_pct):.0%} from early to late window ({early_roas:.2f}x -> {late_roas:.2f}x).")
        elif late_roas >= benchmark_roas and (pd.isna(trend_pct) or trend_pct >= -0.05):
            rec = "Scale"
            why = f"Late-window ROAS {late_roas:.2f}x is at/above the {benchmark_roas:.1f}x benchmark and holding or rising."
        else:
            rec = "Maintain"
            why = f"Late-window ROAS {late_roas:.2f}x is below benchmark but above break-even and not sharply declining."

        rows.append({
            "campaign": camp, "active_days": n, "spend": total_spend, "revenue": total_revenue,
            "roas": overall_roas, "cpa": overall_cpa, "early_roas": early_roas, "late_roas": late_roas,
            "trend_pct": trend_pct, "recommendation": rec, "rationale": why,
        })
    return pd.DataFrame(rows).sort_values("spend", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Level 2 — Ad set: performance + day-over-day anomaly flags
# ---------------------------------------------------------------------------
def detect_adset_anomalies(
    df: pd.DataFrame,
    has_daily_granularity: bool = True,
    cpc_spike_pct: float = 0.50,
    ctr_drop_pct: float = 0.40,
    min_day_spend: float = 5.0,
) -> pd.DataFrame:
    """
    Day-over-day percent change in CPC and CTR per ad set. With only 3-7
    days per ad set a z-score is unstable (too few points to estimate a
    standard deviation), so this uses a direct percent-change threshold
    against the *previous* active day instead. Returns an empty frame
    (nothing to compare) when the file has no day-over-day granularity.
    """
    if not has_daily_granularity:
        return pd.DataFrame(columns=["adset", "day", "type", "detail"])

    daily = daily_rollup(df, ["adset"])
    flags = []
    for adset, g in daily.groupby("adset"):
        g = g.sort_values("day").reset_index(drop=True)
        g = g[g["spend"] >= min_day_spend]
        if len(g) < 2:
            continue
        g["cpc_pct_change"] = g["cpc"].pct_change()
        g["ctr_pct_change"] = g["ctr"].pct_change()
        cpc_spikes = g[g["cpc_pct_change"] > cpc_spike_pct]
        ctr_drops = g[g["ctr_pct_change"] < -ctr_drop_pct]
        for _, r in cpc_spikes.iterrows():
            flags.append({"adset": adset, "day": r["day"], "type": "CPC spike",
                          "detail": f"CPC +{r['cpc_pct_change']:.0%} to ${r['cpc']:.2f}"})
        for _, r in ctr_drops.iterrows():
            flags.append({"adset": adset, "day": r["day"], "type": "CTR drop",
                          "detail": f"CTR {r['ctr_pct_change']:.0%} to {r['ctr']:.2f}%"})
    return pd.DataFrame(flags)


def adset_summary(df: pd.DataFrame, anomalies: pd.DataFrame) -> pd.DataFrame:
    summary = window_rollup(df, ["adset"])
    if len(anomalies):
        counts = anomalies.groupby("adset").size().rename("anomaly_count")
        types = anomalies.groupby("adset")["type"].apply(lambda s: ", ".join(sorted(set(s)))).rename("anomaly_types")
        summary = summary.merge(counts, on="adset", how="left").merge(types, on="adset", how="left")
    else:
        summary["anomaly_count"] = 0
        summary["anomaly_types"] = ""
    summary["anomaly_count"] = summary["anomaly_count"].fillna(0).astype(int)
    summary["anomaly_types"] = summary["anomaly_types"].fillna("")
    return summary.sort_values("spend", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Level 3 — Ad: creative fatigue / winner table
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
) -> pd.DataFrame:
    """Per-ad rollup + status classification. Day-based signals (spend-trend,
    ROAS/CTR sparklines) are only computed when the file has day-over-day
    granularity; in snapshot mode the active-days gate is skipped too, since
    a single aggregated row can't tell us how many distinct days it covers."""
    rows = []
    for ad, g in df.groupby("ad"):
        g = g.sort_values("day")
        active_days = g["day"].nunique()
        total_spend = g["spend"].sum()
        total_revenue = g["revenue"].sum()
        roas = total_revenue / total_spend if total_spend > 0 else np.nan

        if has_daily_granularity:
            peak_daily_spend = g.groupby("day")["spend"].sum().max()
            daily_spend = g.groupby("day")["spend"].sum().sort_index()
            recent = daily_spend.tail(min(declining_lookback, len(daily_spend)))
            spend_trend_ratio = recent.mean() / peak_daily_spend if peak_daily_spend > 0 else np.nan
            delivery_declining = bool(pd.notna(spend_trend_ratio) and spend_trend_ratio < declining_spend_ratio)
            daily_ctr = daily_rollup(g, ["ad"]).sort_values("day")
            ctr_trend = daily_ctr["ctr"].fillna(0).tolist()
            roas_trend = daily_ctr["roas"].fillna(0).tolist()
            spend_trend = daily_ctr["spend"].fillna(0).tolist()
        else:
            spend_trend_ratio, delivery_declining = np.nan, False
            ctr_trend, roas_trend, spend_trend = [], [], []

        day_gate_ok = active_days >= min_active_days if has_daily_granularity else True
        if not day_gate_ok:
            status, rec = "insufficient_history", f"Wait — only {active_days} active day(s)."
        elif total_spend < min_window_spend:
            note = " Delivery is also actively declining." if delivery_declining else ""
            status, rec = "low_delivery", f"Check delivery/budget, not creative — only ${total_spend:.0f} spent.{note}"
        elif roas < breakeven_roas:
            note = " (delivery is also collapsing on its own)" if delivery_declining else ""
            status, rec = "critical", f"Refresh or pause — {roas:.2f}x, below break-even{note}."
        elif roas < benchmark_roas * refresh_ratio:
            note = " Delivery is also declining." if delivery_declining else ""
            status, rec = "warning", f"Watch closely — {roas:.2f}x, under {refresh_ratio:.0%} of benchmark.{note}"
        else:
            status, rec = "healthy", f"Healthy — {roas:.2f}x."

        rows.append({
            "ad": ad, "creative_type": g["creative_type"].iloc[0], "is_variant": bool(g["is_variant"].iloc[0]),
            "active_days": active_days, "spend": total_spend, "revenue": total_revenue,
            "roas": roas, "spend_trend_ratio": spend_trend_ratio, "delivery_declining": delivery_declining,
            "status": status, "recommendation": rec,
            "ctr_trend": ctr_trend, "roas_trend": roas_trend, "spend_trend": spend_trend,
        })

    out = pd.DataFrame(rows)
    status_order = {"critical": 0, "warning": 1, "low_delivery": 2, "insufficient_history": 3, "healthy": 4}
    out["_order"] = out["status"].map(status_order)
    return out.sort_values(["_order", "spend"], ascending=[True, False]).drop(columns="_order").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Level 4 — Creative: roll re-uploaded/duplicated ad-entries up to the
# underlying creative concept, independent of how many times it was re-run.
# ---------------------------------------------------------------------------
def creative_rollup(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("creative_id", as_index=False).agg(
        **{c: (c, "sum") for c in SUM_COLS},
        creative_type=("creative_type", "first"),
        n_ad_entries=("ad", "nunique"),
        n_variants=("is_variant", "sum"),
    )
    g = add_ratio_columns(g)
    return g.sort_values("spend", ascending=False).reset_index(drop=True)


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
