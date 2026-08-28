# Meta Ads Daily Checker

A Streamlit app for budget allocation and ad performance evaluation from a Meta (Facebook/Instagram)
Ads Manager export — ads-level data (Account / Campaign / Ad set / Ad), no separate creative-level
export required. Two objectives, five tabs:

| Tab | Objective | How |
|---|---|---|
| **Hierarchy** | Where does the whole account stand, top to bottom? | A top-down "mind map" — Total → Campaigns, drill into a campaign → its Ad sets — colored by the same Cut/Maintain/Scale call as the two tabs below. |
| **Campaign Budget** | Where should budget move? | Spend + no-recent-sales triggers **Cut**; a high or improving ROAS triggers **Scale**; otherwise **Maintain**. |
| **Ad Set Budget & Anomalies** | Same budget question at the ad-set grain, plus: what changed suddenly? | Same rule for budget; a pooled multivariate outlier model (Isolation Forest) flags anomalous ad-set-days on CTR + CPC jointly. |
| **Ad Performance** | Which individual ads need attention, and how do they rank? | Lists only the anomalous ads (funnel or trend view, your choice), plus a full ranking table sortable on multiple columns at once. |
| **Methodology** | What's actually driving every number on this page? | Every algorithm above, in plain language, with your current sidebar thresholds filled in. |

Built for two shapes of Ads Manager export, detected automatically:
- **Daily** — one row per (ad, day), 3+ days of history. Every tab uses the full trend/anomaly logic below.
- **Snapshot** — one row per ad aggregated over a single reporting window (no Day column). No trend
  or day-over-day comparison is possible from one point, so every tab falls back to a threshold read
  on the window level and says plainly where a feature isn't available, rather than fabricating one.

## The algorithms

**Campaign & Ad set budget — Cut / Maintain / Scale**, same rule at both grains and reused by the
Hierarchy tab's coloring:

1. **Cut** — today's spend is over a threshold (default \$50), or cumulative window spend is over a
   threshold (default \$100), *and* there have been zero sales on each of the last 3 days in the
   file. Spend without recent payoff.
2. **Scale** — otherwise, if today's ROAS is at/above your benchmark, or a spend-weighted linear
   regression fit through every day's ROAS (R² ≥ 0.35, 4+ days — the same trend used for the
   quadrant chart's filled/hollow marker) is confidently improving.
3. **Maintain** — otherwise.

"Today" is fixed once from the whole loaded file's most recent date, not inferred per entity — so
an ad set with no rows on the very last day still gets the correct reference point instead of a
stale one. Single-window snapshots treat the whole window as "today" and skip the no-sales-streak
check (undefined for one data point), so Cut can't trigger there.

**Anomaly detection (Ad set and Ad tabs)** — every pooled (group, day)'s CTR and CPC are z-scored
*within that group's own history* (so "unusual" means unusual for that specific ad set/ad, not just
a bigger one), then an Isolation Forest flags days that are jointly unusual — catching e.g. "CPC
drifted up *and* CTR drifted down together" even if neither alone crosses a hard threshold. Needs a
reasonable pool (30+ rows) to be meaningful; below that it falls back to a simple day-over-day
percent-change rule, and with a snapshot file it's unavailable entirely (nothing to compare). The
model always trains on the full pooled window, but only the **latest day's** flags are shown — a
history of already-resolved anomalies isn't the actionable view.

**Ad ranking** — no model, a plain multi-column sort: pick **all days** (window totals) or a
**single day**, then pick sort columns in priority order (each with its own high→low / low→high
direction) — later picks only break ties within earlier ones.

Every tab has an **"ℹ️ How this is decided"** expander with the exact rule in effect for the file
you loaded — open it before trusting a verdict on data you haven't seen this app work on before.

## Design

Minimalist, plot-first. The Hierarchy tab is a **top-down mind map** — one small "star" at a time (a
parent node, its direct children fanned out below, connected by lines), not the whole account
crammed into one chart. The Campaign/Ad set tabs lead with a **quadrant scatter** — x = ROAS
(performance), y = the fitted trend slope (potential/direction), bubble size = spend, color =
recommendation, **filled marker = trend trusted, hollow = too noisy to trust**. The y-axis is
auto-ranged to the 2nd–98th percentile so one volatile low-day-count outlier can't flatten every
other point. Full tables are one click away in a collapsed expander, for anyone who wants the exact
numbers.

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
app.py                 Streamlit UI — five tabs, wires analysis.py + charts.py together
analysis.py             Pure pandas/numpy/sklearn logic (no Streamlit import) — column resolution
                         (daily vs. snapshot detection, totals-row handling), the weighted-trend
                         regression, Cut/Maintain/Scale budget rule, ML anomaly detection (ad set
                         and ad grain), ad status classification. Testable on its own.
charts.py                Plotly figure builders — mind-map star, quadrant scatter, anomaly scatter,
                         ad funnel, consistent color tokens, outlier-robust axis ranging.
scripts/generate_sample_data.py   Regenerates the synthetic demo file.
sample_data/             Synthetic demo export.
.streamlit/config.toml   Theme.
```

## Tuning it to your account

Every threshold lives in the sidebar and is passed straight into `analysis.py` — nothing is
hardcoded: **benchmark/target ROAS** (set this from your own longer-run history, not from the short
window loaded here), **break-even ROAS** (Ad tab only), **ad refresh trigger %**, **minimum window
spend / active days to judge** (Ad tab only), **anomaly sensitivity** (Ad set + Ad tabs), and the
**Campaign/Ad set Cut rule's** two spend thresholds. The regression's trust threshold (R² ≥ 0.35,
4+ days) and the Cut rule's "last 3 days" window are module constants in `analysis.py`/`app.py`
rather than sidebar controls, to keep the control panel from getting cluttered.
