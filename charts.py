"""Plotly chart builders. Colors are fixed and consistent with the
`status` semantics used throughout analysis.py — never re-cycled per plot."""

import plotly.graph_objects as go

INK = {
    "primary": "#0b0b0b", "secondary": "#52514e", "muted": "#898781",
    "grid": "#e1e0d9", "baseline": "#c3c2b7", "surface": "#fcfcfb",
}
CAT = {"blue": "#2a78d6", "orange": "#eb6834", "aqua": "#1baf7a"}
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


def daily_spend_chart(daily_df):
    fig = go.Figure()
    fig.add_bar(x=daily_df["day"], y=daily_df["spend"], marker_color=CAT["blue"], name="Spend")
    fig.update_layout(**BASE_LAYOUT, title="Daily spend", yaxis_title="Spend (USD)", height=320)
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
    fig.update_layout(**BASE_LAYOUT, title="Daily ROAS", yaxis_title="ROAS (x)", height=320)
    return fig


def campaign_recommendation_chart(camp_df):
    d = camp_df.sort_values("spend")
    colors = [STATUS_COLORS.get(r, INK["muted"]) for r in d["recommendation"]]
    fig = go.Figure(go.Bar(
        x=d["spend"], y=d["campaign"], orientation="h", marker_color=colors,
        text=[f"{r}" for r in d["recommendation"]], textposition="outside",
    ))
    fig.update_layout(**BASE_LAYOUT, title="Spend by campaign, colored by recommendation",
                       xaxis_title="Spend (USD)", height=max(280, 60 * len(d)))
    return fig


def ad_roas_ctr_chart(days, roas, ctr, breakeven=1.0, benchmark=None):
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=days, y=roas, mode="lines+markers", name="ROAS",
                              line=dict(color=CAT["orange"], width=2.5)))
    fig.add_hline(y=breakeven, line_color=INK["baseline"], line_width=1, annotation_text="break-even")
    if benchmark:
        fig.add_hline(y=benchmark, line_color=STATUS["good"], line_width=1, line_dash="dot",
                      annotation_text=f"{benchmark:.1f}x benchmark")
    fig.update_layout(**BASE_LAYOUT, title="Daily ROAS", yaxis_title="ROAS (x)", height=280)

    fig2 = go.Figure()
    fig2.add_trace(go.Scatter(x=days, y=ctr, mode="lines+markers", name="CTR",
                               line=dict(color=CAT["aqua"], width=2.5)))
    fig2.update_layout(**BASE_LAYOUT, title="Daily CTR", yaxis_title="CTR (%)", height=280)
    return fig, fig2


def status_count_chart(status_counts, order, labels, colors):
    fig = go.Figure(go.Bar(
        x=[labels[s] for s in order], y=[status_counts.get(s, 0) for s in order],
        marker_color=[colors[s] for s in order],
        text=[status_counts.get(s, 0) for s in order], textposition="outside",
    ))
    fig.update_layout(**BASE_LAYOUT, title="Ad status breakdown", height=340, showlegend=False)
    return fig
