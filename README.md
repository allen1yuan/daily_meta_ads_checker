# Meta Ads Daily Checker

A Streamlit app for budget allocation and ad performance evaluation from a Meta (Facebook/Instagram)
Ads Manager export — ads-level data (Account / Campaign / Ad set / Ad), no separate creative-level
export required. Two objectives, three tabs:

| Tab | Objective | How |
|---|---|---|
| **Campaign Budget** | Where should budget move? | A spend-weighted linear regression fit through every day's ROAS classifies **Scale / Maintain / Cut**. |
| **Ad Set Budget & Anomalies** | Same budget question at the ad-set grain, plus: what changed suddenly? | Same regression for budget; a pooled multivariate outlier model (Isolation Forest) flags anomalous ad-set-days on CTR + CPC jointly. |
| **Ad Performance** | Which individual ads are winning or need attention? | Same regression-based status, checked for data sufficiency first. |

Built for two shapes of Ads Manager export, detected automatically:
- **Daily** — one row per (ad, day), 3+ days of history. Every tab uses the full trend-based logic below.
- **Snapshot** — one row per ad aggregated over a single reporting window (no Day column). No trend
  is possible from one point, so every tab falls back to a threshold read on the window level and
  says plainly where trend-based features aren't available, rather than fabricating a trend.

## The algorithm

Every level (campaign, ad set, ad) is classified the same way:

1. **Data sufficiency, checked before performance.** An ad/campaign/ad set with too little history
   or spend is `Insufficient data`, not `Cut` — an ROAS computed from a handful of dollars is
   sampling noise, not a verdict.
2. **A spend-weighted linear regression** (numpy, weighted least-squares) is fit through *every*
   available day's ROAS — not two arbitrary early/late buckets. Its R² gates whether the fitted
   trend is trusted at all: a noisy, non-monotonic run of days (e.g. a mid-window dip that already
   recovered) naturally scores a low R² and the classification falls back to the plain
   window-average ROAS instead of an unreliable direction. No special-casing needed for "what if
   the most recent day already turned around" — the confidence gate handles it structurally. (An
   earlier version of this app used a hand-rolled early-half-vs-late-half split with a bolted-on
   "did the last day recover" rescue rule; it worked, but the regression approach handles the same
   case — and a genuine accelerating decline the split missed — for free, from one principle.)
3. **Scale / Maintain / Cut** — Cut if blended ROAS is below break-even, or a confident trend
   projects below it. Scale if the confident current level (fitted, or blended when the trend isn't
   trustworthy) is at/above your benchmark. Maintain otherwise.
4. **Ad set anomalies** — every pooled ad-set-day's CTR and CPC are z-scored *within that ad set*
   (so "unusual" means unusual for it, not just a bigger one), then an Isolation Forest flags days
   that are jointly unusual — catching e.g. "CPC drifted up *and* CTR drifted down together" even
   if neither alone crosses a hard threshold. Needs a reasonable pool (30+ ad-set-days) to be
   meaningful; below that it falls back to a simple day-over-day percent-change rule, and with a
   snapshot file it's unavailable entirely (nothing to compare).

Every tab has an **"ℹ️ How this is decided"** expander with the exact rule in effect for the file
you loaded — open it before trusting a verdict on data you haven't seen this app work on before.

## Design

Minimalist, plot-first: one high-value chart answers each tab's question, not a wall of tables. The
core visual is a **quadrant scatter** — x = ROAS (performance), y = the fitted trend slope
(potential/direction), bubble size = spend, color = recommendation, **filled marker = trend
trusted, hollow = too noisy to trust** (so the chart is honest about its own confidence, not just
its verdict). The y-axis is auto-ranged to the 2nd–98th percentile so one volatile low-day-count
outlier can't flatten every other point — the point still plots and its real value is in the hover,
it just doesn't dictate the scale. Full tables are one click away in a collapsed expander under each
chart, for anyone who wants the exact numbers.

## Quickstart

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then either click **"Use bundled sample data"** in the sidebar (synthetic daily-mode example, safe
to explore immediately), or upload your own export of either shape.

## Expected input

A Meta Ads Manager export (`.xlsx` or `.csv`), daily or snapshot shape (see above). Meta lets you
add multiple name columns (Account, Campaign, Ad set, Ad) alongside an optional Day breakdown in a
single export — that combined shape is what this app is built for. Column header text varies by
export settings; the app matches common phrasings automatically (`analysis.py::COLUMN_ALIASES`)
and will tell you plainly if something required is missing.

**Required:** `Amount spent (USD)`.
**Strongly recommended:** `Day` (omit for a snapshot export), `Account name`, `Campaign name`,
`Ad set name`, `Ad name`, `Purchases conversion value` (or a ROAS/ROI column), `Impressions`,
`Link clicks`, `CTR`, `CPC`, `Reach`, `Frequency`.

If a hierarchy column (Account/Campaign/Ad set) is missing, that level's rows all collapse into
one placeholder group and the app tells you so — it degrades gracefully rather than failing. If
the file has more than one Account, a sidebar filter appears automatically. A blank-hierarchy
totals row at the top of the file (common in Meta exports) is detected, excluded from every
breakdown, and used as a reconciliation check instead.

No real ad data is bundled with this repo. `sample_data/sample_meta_ads_export.xlsx` is fully
synthetic (see `scripts/generate_sample_data.py`) — random names and numbers shaped like a real
export, safe to publish.

## Project layout

```
app.py                 Streamlit UI — three tabs, wires analysis.py + charts.py together
analysis.py             Pure pandas/numpy/sklearn logic (no Streamlit import) — column resolution
                         (daily vs. snapshot detection, totals-row handling), the weighted-trend
                         regression, budget classification, ML anomaly detection. Testable on its own.
charts.py                Plotly figure builders — quadrant scatter, anomaly scatter, consistent
                         color tokens, outlier-robust axis ranging.
scripts/generate_sample_data.py   Regenerates the synthetic demo file.
sample_data/             Synthetic demo export.
.streamlit/config.toml   Theme.
```

## Tuning it to your account

Every threshold lives in the sidebar and is passed straight into `analysis.py` — nothing is
hardcoded: **benchmark/target ROAS** (set this from your own longer-run history, not from the short
window loaded here), **break-even ROAS**, **ad refresh trigger %**, **minimum window spend to
judge**, **minimum active days** (daily files only), and **ad-set anomaly sensitivity** (daily
files with enough pooled history only). The regression's trust threshold (R² ≥ 0.35, 4+ days) is a
module constant in `analysis.py` rather than a sidebar control, to keep the control panel minimal.
