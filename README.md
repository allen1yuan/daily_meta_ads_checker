# Meta Ads Daily Checker

A Streamlit app for monitoring, diagnosing, and allocating budget across Meta (Facebook/Instagram)
ads. Built for two shapes of Ads Manager export:

- **Daily** — a rolling **3–7 day** breakdown, one row per ad per day. Enables trend-based checks
  (campaign trend, day-over-day anomalies, ROAS/CTR sparklines).
- **Snapshot** — one row per ad aggregated over a single reporting window (no Day column — Meta's
  Campaign/Ad set/Ad breakdown without a Day breakdown added). Trend features aren't possible from
  one point, so the app falls back to threshold-based reads and says plainly where a trend isn't
  available, rather than fabricating one.

The app detects which shape it's looking at automatically and adjusts every tab accordingly.

Four levels, one file:

| Level | What it answers | How |
|---|---|---|
| **Campaign** | Where should budget move? | **Daily**: splits the window into an early/late half, compares late ROAS to the campaign's own early ROAS and to your benchmark. **Snapshot**: classifies on the window's ROAS alone. Either way: **Scale / Maintain / Cut**. |
| **Ad set** | What changed suddenly? | Day-over-day percent-change checks for CPC spikes and CTR drops (daily files only) — not a z-score, since 3–7 points isn't enough to estimate a stable standard deviation. |
| **Ad** | Which ad-entries are winning or fatiguing? | Per-ad ROAS vs. a benchmark; day-by-day ROAS/CTR sparklines and a spend-trend flag when daily data is available. |
| **Creative** | Which underlying creative concepts are winning, independent of how many times they were re-uploaded? | Ad-entries are rolled up by a parsed creative id — the `#TAG#` code in the ad name when present, otherwise the name with "ad copy"/"Copy" variant suffixes stripped. |

## Why this isn't just "compute ROAS and sort"

A short window (of either shape) makes mistakes easy to make by accident, and the app is built specifically to avoid them:

1. **A ratio needs enough data before it means anything.** An ad that spent $3 with zero purchases
   isn't "0.00x ROAS, critical" — it's an ad Meta's delivery system may have already throttled to
   near-nothing (audience exhaustion, learning-phase struggles, a low relevance score). Reporting a
   scary number from a tiny sample as if it were a performance verdict would point you at the wrong
   fix. So every level checks **data sufficiency before performance**: in daily files, too few
   active days → `insufficient_history`; either way, enough history but too little spend →
   `low_delivery`. Only ads/campaigns/ad sets that clear both bars get judged against the ROAS
   benchmark. (Snapshot files skip the active-days check specifically — a single aggregated row
   can't say how many distinct days it covers, so only the spend floor applies.)
2. **A short window can't build its own baseline.** A longer-run analysis can say "this ad's ROI
   at its own $100-spend mark was 3x" and compare today against that. A few days usually isn't
   enough spend to establish that baseline reliably. So this app compares against an
   **account-level benchmark ROAS** you set in the sidebar (informed by your own longer-run
   history) instead of trying to derive a per-ad baseline from too little data.
3. **A declining-spend ad isn't automatically a declining-creative ad.** In daily files, the ad
   table tracks `spend_trend_ratio` — recent daily spend vs. that ad's own peak day in the window —
   so a creative that's fatiguing (steady spend, falling ROAS) reads differently from one that's
   already being wound down by the algorithm (spend collapsing on its own). Same underlying ROAS
   number, different fix.
4. **`Ad name` is not a unique id, and it isn't the same thing as "creative."** The same ad name
   can cover more than one underlying ad-ID (grouped by summing before any ratio is recomputed),
   and the same creative is routinely re-uploaded as a fresh ad-entry for testing. The Creative tab
   rolls those back together so "how many distinct concepts are actually running" doesn't get
   inflated by re-uploads. (A generic tag shared across unrelated assets — a catch-all `#PIC#`, say
   — will still roll those together even though they aren't really one creative; the app flags
   unusually large ad-entry counts in that table as a hint to open the group up.)
5. **A totals row shouldn't be double-counted.** Meta exports commonly put a blank-hierarchy,
   populated-metrics summary row at the top of the file. It's detected and excluded from every
   breakdown automatically, and used only as a sanity check — reported back to you as a reconciled
   ✓ or a mismatch warning.

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
the file has more than one Account, a sidebar filter appears automatically.

No real ad data is bundled with this repo. `sample_data/sample_meta_ads_export.xlsx` is fully
synthetic (see `scripts/generate_sample_data.py`) — random names and numbers shaped like a real
export, safe to publish.

## Diagnosis logic, in the app

Each tab has an **"ℹ️ How this is decided"** expander at the top with the exact rule being applied
(Scale/Maintain/Cut, anomaly thresholds, ad status priority order) — open it before trusting a
verdict on data you haven't seen this app work on before. The short version is in the "Why this
isn't just compute ROAS and sort" section above; the in-app version is scoped to what that specific
tab is doing.

## Recommended ad naming template

Tested against a real account's ad-name history, three things repeatedly break automated grouping
(here and in any future analysis): creative-type tokens spelled inconsistently (`Ai`/`AI`/`ai`),
dates in two formats (`20260811` vs `2.28`), and a generic tag (`#PIC#`) reused across dozens of
unrelated images so it stops identifying one creative. A single template fixes all three:

```
{TYPE}-{PRODUCT}-#{TYPE}{SEQ}#-{YYYYMMDD}-{DESCRIPTOR}[-V{n}]
```

| Content type | `TYPE` | Example |
|---|---|---|
| AI-generated | `AI` | `AI-LNV-#AI0057#-20260827-暮雨-秋季新品` |
| KOL / UGC | `KOL` | `KOL-LNV-#KOL0142#-20260827-claudia12xo` |
| Static image | `IMG` | `IMG-YZJ-#IMG0033#-20260827-产品图-V2` |
| Video (produced) | `VID` | `VID-LNV-#VID0011#-20260827-开箱视频` |
| Feed / catalog | `FEED` | `FEED-LNV-DPA-Broad` (standing ad — no date/seq needed) |

The load-bearing rule: **`#{TYPE}{SEQ}#` is assigned once per creative and only reused for a
literal re-upload of that exact asset** — never for a different asset that happens to share a
generic label. That single discipline is what makes "one row = one creative" true in the Creative
Rollup tab. Re-uploads get a single incrementing `-V{n}` suffix instead of stacking `- 广告副本`
repeatedly, so both the app and a human scanning the list can tell at a glance how many versions
exist. Full rationale and per-type detail is in the app's Creative Rollup tab.

## Project layout

```
app.py                 Streamlit UI — four tabs, wires analysis.py + charts.py together
analysis.py             Pure pandas logic (no Streamlit import) — column resolution (incl. daily
                         vs. snapshot detection, totals-row handling, creative parsing),
                         aggregation, classification. Testable on its own.
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
  days** (daily files only), and the **anomaly thresholds** for ad-set CPC/CTR (daily files only)
  — all adjustable live, all re-run instantly against the loaded data.
