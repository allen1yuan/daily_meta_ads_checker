"""Plotly chart builders. Minimalist, plot-first design: one high-value
figure per question, status colors fixed and never re-cycled per plot."""

import numpy as np
import pandas as pd
import plotly.graph_objects as go

INK = {
    "primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781",
    "grid": "#e1e0d9", "baseline": "#c3c2b7", "surface": "#fcfcfb",
}
CAT = {"blue": "#2a78d6", "orange": "#eb6834", "aqua": "#1baf7a", "violet": "#4a3aa7", "yellow": "#eda100"}
STATUS = {"good": "#0ca30c", "warning": "#fab219", "critical": "#d03b3b", "muted": "#898781"}

STATUS_COLORS = {
    "Scale": STATUS["good"], "Maintain": CAT["blue"], "Cut": STATUS["critical"],
    "Insufficient data": STATUS["muted"],
    "critical": STATUS["critical"], "warning": STATUS["warning"],
    "low_delivery": STATUS["muted"], "insufficient_history": INK["grid"], "healthy": STATUS["good"],
}

BASE_LAYOUT = dict(
    plot_bgcolor=INK["surface"], paper_bgcolor=INK["surface"],
    font=dict(color=INK["primary"], family="Helvetica Neue, Arial, sans-serif", size=13),
    margin=dict(l=10, r=10, t=40, b=10),
    xaxis=dict(gridcolor=INK["grid"], zeroline=False, showline=True, linecolor=INK["baseline"]),
    yaxis=dict(gridcolor=INK["grid"], zeroline=False),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
)


def _robust_y_range(values, pad_frac=0.25, min_half_span=0.5):
    """A trend slope fit from only 3-5 noisy days can occasionally swing to
    an extreme value that would otherwise stretch the y-axis and flatten
    every other point near zero — the same 'one outlier hides the trend'
    problem as elsewhere in this project. Range the axis off the 2nd-98th
    percentile instead of the true min/max; the point itself still plots
    (and its real value is in the hover), it just doesn't dictate the scale."""
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return [-1, 1]
    lo, hi = np.nanpercentile(v, [2, 98])
    lo, hi = min(lo, 0), max(hi, 0)  # always include the zero reference line
    half_span = max((hi - lo) / 2 * (1 + pad_frac), min_half_span)
    mid = (hi + lo) / 2
    return [mid - half_span, mid + half_span]


def _sqrt_size(values, min_size=8, max_size=42):
    v = np.clip(np.asarray(values, dtype=float), 0, None)
    if v.max() <= 0:
        return np.full_like(v, min_size)
    return min_size + np.sqrt(v / v.max()) * (max_size - min_size)


def daily_spend_chart(daily_df):
    fig = go.Figure()
    fig.add_bar(x=daily_df["day"], y=daily_df["spend"], marker_color=CAT["blue"], name="Spend")
    fig.update_layout(**BASE_LAYOUT, title="Daily spend", yaxis_title="Spend (USD)", height=280)
    return fig


def daily_roas_chart(daily_df, breakeven=1.0, benchmark=None):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=daily_df["day"], y=daily_df["roas"], mode="lines+markers",
                              line=dict(color=CAT["orange"], width=2.5), marker=dict(size=6), name="ROAS"))
    fig.add_hline(y=breakeven, line_color=INK["baseline"], line_width=1,
                  annotation_text="break-even", annotation_position="right")
    if benchmark:
        fig.add_hline(y=benchmark, line_color=STATUS["good"], line_width=1, line_dash="dot",
                      annotation_text=f"{benchmark:.1f}x benchmark", annotation_position="right")
    fig.update_layout(**BASE_LAYOUT, title="Daily ROAS", yaxis_title="ROAS (x)", height=280)
    return fig


def budget_quadrant(df, entity_col, benchmark_roas, breakeven_roas, title, label_top_n=8):
    """The primary budget-allocation visual: x = blended ROAS (performance),
    y = fitted trend slope in ROAS-x/day (potential/direction), bubble size =
    spend, color = recommendation. A filled marker means the trend was
    confident enough to use; a hollow marker means the day-to-day pattern
    was too noisy to fit a direction, so the recommendation rests on the
    ROAS level alone. 'Insufficient data' rows are excluded (nothing
    reliable to plot) — their count is reported by the caller separately."""
    d = df[df["recommendation"] != "Insufficient data"].copy()
    if d.empty:
        return None
    sizes = _sqrt_size(d["spend"])
    colors = [STATUS_COLORS.get(r, INK["muted"]) for r in d["recommendation"]]
    symbols = ["circle" if c else "circle-open" for c in d["trend_confident"]]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=d["roas"], y=d["trend_slope"].fillna(0.0), mode="markers",
        marker=dict(size=sizes, color=colors, symbol=symbols, line=dict(width=1.5, color=colors)),
        customdata=np.stack([d[entity_col], d["spend"], d["roas"], d["trend_slope"].fillna(0.0)], axis=-1),
        hovertemplate="<b>%{customdata[0]}</b><br>Spend $%{customdata[1]:,.0f}<br>ROAS %{customdata[2]:.2f}x"
                      "<br>Trend %{customdata[3]:+.3f}x/day<extra></extra>",
        showlegend=False,
    ))
    top = d.nlargest(label_top_n, "spend")
    fig.add_trace(go.Scatter(
        x=top["roas"], y=top["trend_slope"].fillna(0.0), mode="text",
        text=[str(t)[:24] for t in top[entity_col]], textposition="top center",
        textfont=dict(size=10, color=INK["secondary"]), showlegend=False,
    ))
    fig.add_vline(x=breakeven_roas, line_color=INK["baseline"], line_width=1,
                  annotation_text="break-even", annotation_position="top")
    fig.add_vline(x=benchmark_roas, line_color=STATUS["good"], line_width=1, line_dash="dot",
                  annotation_text=f"{benchmark_roas:.1f}x benchmark", annotation_position="top")
    fig.add_hline(y=0, line_color=INK["baseline"], line_width=1)

    for rec, color in [("Scale", STATUS["good"]), ("Maintain", CAT["blue"]), ("Cut", STATUS["critical"])]:
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", marker=dict(size=10, color=color), name=rec))

    fig.update_layout(**BASE_LAYOUT, title=title, xaxis_title="ROAS (performance)",
                       yaxis_title="Trend, ROAS x/day (potential)", height=440)
    fig.update_yaxes(range=_robust_y_range(d["trend_slope"]))
    return fig


def anomaly_scatter(pool_df, title="Ad set anomaly detection (CTR vs. CPC)"):
    """Multivariate view of every pooled ad-set-day: x = CPC, y = CTR, sized
    by spend, flagged points (from the Isolation Forest) in red."""
    d = pool_df.copy()
    sizes = _sqrt_size(d["spend"], min_size=6, max_size=30)
    colors = [STATUS["critical"] if a else INK["muted"] for a in d["is_anomaly"]]
    opacities = [0.9 if a else 0.45 for a in d["is_anomaly"]]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=d["cpc"], y=d["ctr"], mode="markers",
        marker=dict(size=sizes, color=colors, opacity=opacities, line=dict(width=0)),
        customdata=np.stack([d["adset"], d["day"].dt.strftime("%Y-%m-%d"), d["spend"]], axis=-1),
        hovertemplate="<b>%{customdata[0]}</b> (%{customdata[1]})<br>CPC $%{x:.2f}<br>CTR %{y:.2f}%"
                      "<br>Spend $%{customdata[2]:,.0f}<extra></extra>",
        showlegend=False,
    ))
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", marker=dict(size=10, color=STATUS["critical"]), name="Flagged"))
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", marker=dict(size=10, color=INK["muted"]), name="Normal"))
    fig.update_layout(**BASE_LAYOUT, title=title, xaxis_title="CPC (USD)", yaxis_title="CTR (%)", height=420)
    return fig


def ad_performance_scatter(ads_df, benchmark_roas, breakeven_roas, title="Ad performance", label_top_n=6):
    """Same quadrant language as the budget charts, at the ad grain: x =
    ROAS, y = fitted trend slope, size = spend, color = status."""
    d = ads_df[~ads_df["status"].isin(["insufficient_history", "low_delivery"])].copy()
    if d.empty:
        return None
    sizes = _sqrt_size(d["spend"])
    colors = [STATUS_COLORS.get(s, INK["muted"]) for s in d["status"]]
    symbols = ["circle" if c else "circle-open" for c in d["trend_confident"]]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=d["roas"], y=d["trend_slope"].fillna(0.0), mode="markers",
        marker=dict(size=sizes, color=colors, symbol=symbols, line=dict(width=1.5, color=colors)),
        customdata=np.stack([d["ad"], d["spend"], d["roas"], d["trend_slope"].fillna(0.0)], axis=-1),
        hovertemplate="<b>%{customdata[0]}</b><br>Spend $%{customdata[1]:,.0f}<br>ROAS %{customdata[2]:.2f}x"
                      "<br>Trend %{customdata[3]:+.3f}x/day<extra></extra>",
        showlegend=False,
    ))
    top = d.nlargest(label_top_n, "spend")
    fig.add_trace(go.Scatter(
        x=top["roas"], y=top["trend_slope"].fillna(0.0), mode="text",
        text=[str(t)[:20] for t in top["ad"]], textposition="top center",
        textfont=dict(size=9, color=INK["secondary"]), showlegend=False,
    ))
    fig.add_vline(x=breakeven_roas, line_color=INK["baseline"], line_width=1, annotation_text="break-even")
    fig.add_vline(x=benchmark_roas, line_color=STATUS["good"], line_width=1, line_dash="dot",
                  annotation_text=f"{benchmark_roas:.1f}x benchmark")
    fig.add_hline(y=0, line_color=INK["baseline"], line_width=1)

    for label, color in [("Critical", STATUS["critical"]), ("Warning", STATUS["warning"]), ("Healthy", STATUS["good"])]:
        fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers", marker=dict(size=10, color=color), name=label))

    fig.update_layout(**BASE_LAYOUT, title=title, xaxis_title="ROAS (performance)",
                       yaxis_title="Trend, ROAS x/day (potential)", height=440)
    fig.update_yaxes(range=_robust_y_range(d["trend_slope"]))
    return fig


def mind_map_level(root_label, root_metrics, children, title):
    """A single top-down 'mind map' fan: one parent node at the top, a line
    down to each of its direct children below, children sized by spend and
    colored by their own Cut/Maintain/Scale bucket. `children` needs columns
    label/spend/revenue/roas/bucket (cpa/ctr/cpc optional, shown on hover
    when present). No numeric axes and no legend traces sharing the plot's
    coordinate space — both caused the earlier icicle version's title/plot
    overlap; the color key lives in the caller's caption instead."""
    bucket_colors = {**STATUS_COLORS, "root": INK["secondary"], "Insufficient data": INK["grid"]}
    n = len(children)
    if n == 0:
        return None

    def fmt(v):
        return f"${v:,.0f}" if pd.notna(v) else "—"

    def roas_fmt(v):
        return f"{v:.2f}x" if pd.notna(v) else "—"

    xs = [i - (n - 1) / 2 for i in range(n)]
    sizes = _sqrt_size(children["spend"], min_size=14, max_size=46)
    colors = [bucket_colors.get(b, INK["muted"]) for b in children["bucket"]]

    child_hover = []
    for _, r in children.iterrows():
        lines = [f"<b>{r['label']}</b>", f"Spend {fmt(r['spend'])} · Revenue {fmt(r['revenue'])} · ROAS {roas_fmt(r['roas'])}"]
        extra = [f"{lbl} {fmt(r[col])}" if col != "ctr" else f"CTR {r[col]:.2f}%"
                 for col, lbl in [("cpa", "CPA"), ("ctr", "CTR"), ("cpc", "CPC")]
                 if col in r.index and pd.notna(r[col])]
        if extra:
            lines.append(" · ".join(extra))
        lines.append(str(r["bucket"]))
        child_hover.append("<br>".join(lines))

    root_hover = (f"<b>{root_label}</b><br>Spend {fmt(root_metrics.get('spend'))} · "
                  f"Revenue {fmt(root_metrics.get('revenue'))} · ROAS {roas_fmt(root_metrics.get('roas'))}")

    fig = go.Figure()
    edge_x, edge_y = [], []
    for x in xs:
        edge_x += [0, x, None]
        edge_y += [1, 0, None]
    fig.add_trace(go.Scatter(x=edge_x, y=edge_y, mode="lines", line=dict(color=INK["baseline"], width=1.5),
                              hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(
        x=[0], y=[1], mode="markers+text",
        marker=dict(size=54, color=bucket_colors["root"], line=dict(width=2, color=INK["surface"])),
        text=[str(root_label)[:26]], textposition="top center", textfont=dict(size=13, color=INK["primary"]),
        customdata=[root_hover], hovertemplate="%{customdata}<extra></extra>", showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=xs, y=[0.0] * n, mode="markers+text",
        marker=dict(size=sizes, color=colors, line=dict(width=1.5, color=colors)),
        text=[str(t)[:18] for t in children["label"]], textposition="bottom center",
        textfont=dict(size=10, color=INK["secondary"]),
        customdata=child_hover, hovertemplate="%{customdata}<extra></extra>", showlegend=False,
    ))

    layout = {k: v for k, v in BASE_LAYOUT.items() if k not in ("xaxis", "yaxis", "margin", "legend")}
    fig.update_layout(
        **layout, title=title, height=380, margin=dict(l=10, r=10, t=60, b=60), showlegend=False,
        xaxis=dict(visible=False, range=[-n / 2 - 0.8, n / 2 + 0.8]),
        yaxis=dict(visible=False, range=[-0.45, 1.28]),
    )
    return fig


def budget_bar_snapshot(df, entity_col, title):
    """Snapshot-mode fallback (no trend axis available): spend by entity,
    colored by recommendation — still a plot, not a table."""
    d = df[df["recommendation"] != "Insufficient data"].sort_values("spend").tail(20)
    if d.empty:
        return None
    colors = [STATUS_COLORS.get(r, INK["muted"]) for r in d["recommendation"]]
    fig = go.Figure(go.Bar(
        x=d["spend"], y=d[entity_col], orientation="h", marker_color=colors,
        text=d["recommendation"], textposition="outside",
    ))
    fig.update_layout(**BASE_LAYOUT, title=title, xaxis_title="Spend (USD)",
                       height=max(320, 28 * len(d)))
    return fig


def status_count_chart(status_counts, order, labels, colors):
    fig = go.Figure(go.Bar(
        x=[labels[s] for s in order], y=[status_counts.get(s, 0) for s in order],
        marker_color=[colors[s] for s in order],
        text=[status_counts.get(s, 0) for s in order], textposition="outside",
    ))
    fig.update_layout(**BASE_LAYOUT, title="Ad status breakdown", height=300, showlegend=False)
    return fig


def ad_roas_ctr_chart(days, roas, ctr, breakeven=1.0, benchmark=None):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=days, y=roas, mode="lines+markers", name="ROAS",
                              line=dict(color=CAT["orange"], width=2.5)))
    fig.add_hline(y=breakeven, line_color=INK["baseline"], line_width=1, annotation_text="break-even")
    if benchmark:
        fig.add_hline(y=benchmark, line_color=STATUS["good"], line_width=1, line_dash="dot",
                      annotation_text=f"{benchmark:.1f}x benchmark")
    fig.update_layout(**BASE_LAYOUT, title="Daily ROAS", yaxis_title="ROAS (x)", height=260)

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=days, y=ctr, mode="lines+markers", name="CTR",
                               line=dict(color=CAT["aqua"], width=2.5)))
    fig2.update_layout(**BASE_LAYOUT, title="Daily CTR", yaxis_title="CTR (%)", height=260)
    return fig, fig2
