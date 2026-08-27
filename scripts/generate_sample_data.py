"""
Generates a small, fully synthetic Meta Ads export for `sample_data/`.

Not real ad data — random names, random numbers, shaped like a real Meta
Ads Manager export (day x campaign x ad set x ad) so the app has something
to demo without needing anyone's real account data.

Run: python scripts/generate_sample_data.py
"""

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)

N_DAYS = 6
CAMPAIGNS = {
    "Prospecting - Broad": ["Adset A - 18-34", "Adset B - 35-54"],
    "Retargeting - 30d": ["Adset C - Cart abandoners", "Adset D - Viewed product"],
    "Catalog Sales": ["Adset E - DPA US"],
}
CREATIVE_TAGS = ["ai-video", "kol-ugc", "static-image", "catalog-feed"]

rows = []
today = pd.Timestamp("2026-08-27")
days = [today - pd.Timedelta(days=i) for i in range(N_DAYS)][::-1]

ad_id = 0
for campaign, adsets in CAMPAIGNS.items():
    camp_quality = rng.uniform(0.7, 1.4)  # some campaigns are just better than others
    for adset in adsets:
        n_ads = rng.integers(2, 5)
        for _ in range(n_ads):
            ad_id += 1
            tag = rng.choice(CREATIVE_TAGS)
            ad_name = f"AD{ad_id:04d}-{tag}-{rng.integers(1000,9999)}"
            base_spend = rng.uniform(15, 220)
            base_roas = max(0.2, rng.normal(2.3, 0.9) * camp_quality)
            fatigue = rng.choice([0, 1], p=[0.7, 0.3])  # 30% of ads decay over the window
            low_delivery = rng.choice([0, 1], p=[0.85, 0.15])

            for day_idx, day in enumerate(days):
                if low_delivery:
                    spend = round(max(0, base_spend * 0.08 * rng.uniform(0.3, 1.2)), 2)
                else:
                    decay = (1 - 0.12 * day_idx) if fatigue else 1.0
                    spend = round(max(0, base_spend * rng.uniform(0.7, 1.3) * decay), 2)
                if spend <= 0:
                    continue
                roas_today = max(0, base_roas * (1 - 0.15 * day_idx if fatigue else 1) * rng.uniform(0.6, 1.4))
                revenue = round(spend * roas_today, 2)
                purchases = max(0, round(revenue / rng.uniform(15, 45)))
                impressions = int(spend / rng.uniform(0.02, 0.06))
                ctr = max(0.3, rng.normal(6.0 if not fatigue else 6.0 - day_idx, 1.5))
                link_clicks = int(impressions * ctr / 100)
                reach = int(impressions / rng.uniform(1.0, 1.6))

                rows.append({
                    "Day": day.date().isoformat(),
                    "Campaign name": campaign,
                    "Ad set name": adset,
                    "Ad name": ad_name,
                    "Amount spent (USD)": spend,
                    "Purchases": purchases,
                    "Purchases conversion value": revenue,
                    "Impressions": impressions,
                    "Link clicks": link_clicks,
                    "CTR (link click-through rate)": round(ctr, 2),
                    "CPC (cost per link click)": round(spend / link_clicks, 2) if link_clicks else None,
                    "CPM (cost per 1,000 impressions)": round(spend / impressions * 1000, 2) if impressions else None,
                    "Reach": reach,
                    "Frequency": round(impressions / reach, 2) if reach else None,
                })

df = pd.DataFrame(rows)
out_path = "sample_data/sample_meta_ads_export.xlsx"
df.to_excel(out_path, index=False)
print(f"Wrote {out_path}: {len(df)} rows, {df['Ad name'].nunique()} ads, "
      f"{df['Ad set name'].nunique()} ad sets, {df['Campaign name'].nunique()} campaigns, "
      f"{df['Day'].nunique()} days")
