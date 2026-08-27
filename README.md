# Meta Ads Daily Checker

A Streamlit app for monitoring, diagnosing, and allocating budget across Meta (Facebook/Instagram)
ads — built for the **3–7 day rolling export** you'd realistically pull every morning, not a
full historical dump.

Three levels, one file:

| Level | What it answers | How |
|---|---|---|
| **Campaign** | Where should budget move? | Splits each campaign's window into an early half and late half, compares late-window ROAS to the campaign's own early ROAS and to your benchmark, and recommends **Scale / Maintain / Cut**. |
| **Ad set** | What changed suddenly? | Day-over-day percent-change checks for CPC spikes and CTR drops — not a z-score, since 3–7 points isn't enough to estimate a stable standard deviation. |
| **Ad** | Which creatives are winning or fatiguing? | Per-ad ROAS vs. a benchmark, with day-by-day ROAS/CTR sparklines right in the table so a fatigue trend is visible at a glance. |

## Why this isn't just "compute ROAS and sort"

A short window makes two mistakes very easy to make by accident, and the app is built specifically to avoid them:

1. **A ratio needs enough data before it means anything.** An ad that spent $3 over 5 days with
   zero purchases isn't "0.00x ROAS, critical" — it's an ad Meta's delivery system may have
   already throttled to near-nothing (audience exhaustion, learning-phase struggles, a low
   relevance score). Reporting a scary number from a tiny sample as if it were a performance
   verdict would point you at the wrong fix. So every level checks **data sufficiency before
   performance**: too few active days → `insufficient_history`; enough days but too little spend
   → `low_delivery`. Only ads/campaigns/ad sets that clear both bars get judged against the ROAS
   benchmark.
2. **A short window can't build its own baseline.** A longer-run analysis can say "this ad's ROI
   at its own $100-spend mark was 3x" and compare today against that. Three to seven days usually
   isn't enough spend to establish that baseline reliably. So this app compares against an
   **account-level benchmark ROAS** you set in the sidebar (informed by your own longer-run
   history) instead of trying to derive a per-ad baseline from too little data.
3. **A declining-spend ad isn't automatically a declining-creative ad.** The ad table tracks
   `spend_trend_ratio` — recent daily spend vs. that ad's own peak day in the window — so a
   creative that's fatiguing (steady spend, falling ROAS) reads differently from one that's
   already being wound down by the algorithm (spend collapsing on its own). Same underlying
   ROAS number, different fix.

## Quickstart

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then either click **"Use bundled sample data"** in the sidebar (synthetic, safe to explore
immediately), or upload your own export.

## Expected input

A Meta Ads Manager export (`.xlsx` or `.csv`) with **one row per ad per day**, covering roughly
the last 3–7 days. Meta lets you add multiple name columns (Campaign, Ad set, Ad) alongside a
Day breakdown in a single export — that combined shape is what this app is built for. Column
header text varies by export settings; the app matches common phrasings automatically
(`analysis.py::COLUMN_ALIASES`) and will tell you plainly if something required is missing.

**Required:** `Day` (or `Date`), `Amount spent (USD)`.
**Strongly recommended:** `Campaign name`, `Ad set name`, `Ad name`, `Purchases conversion value`
(or a ROAS/ROI column), `Impressions`, `Link clicks`, `CTR`, `CPC`, `Reach`, `Frequency`.

If a hierarchy column (Campaign/Ad set) is missing, that level's rows all collapse into one
placeholder group and the app tells you so — it degrades gracefully rather than failing.

No real ad data is bundled with this repo. `sample_data/sample_meta_ads_export.xlsx` is fully
synthetic (see `scripts/generate_sample_data.py`) — random names and numbers shaped like a real
export, safe to publish.

## Project layout

```
app.py                 Streamlit UI — three tabs, wires analysis.py + charts.py together
analysis.py             Pure pandas logic (no Streamlit import) — column resolution, aggregation,
                         classification. Testable on its own.
charts.py                Plotly figure builders, consistent color tokens.
scripts/generate_sample_data.py   Regenerates the synthetic demo file.
sample_data/             Synthetic demo export.
.streamlit/config.toml   Theme.
```

## Tuning it to your account

Every threshold lives in the sidebar and is passed straight into `analysis.py` — nothing is
hardcoded:

- **Benchmark / target ROAS** — set this from your own longer-run analysis of where ROAS
  typically converges for this account, not from the short window loaded here.
- **Break-even ROAS**, **refresh trigger %**, **minimum window spend to judge**, **minimum active
  days**, and the **anomaly thresholds** for ad-set CPC/CTR — all adjustable live, all re-run
  instantly against the loaded data.
