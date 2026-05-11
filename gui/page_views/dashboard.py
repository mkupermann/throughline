"""Dashboard page — top-level metrics, activity timeline, recent conversations."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go

from gui.page_views import app_ns


def render() -> None:
    app = app_ns()
    st = app.st
    q = app.q
    page_header = app.page_header
    fmt_dt = app.fmt_dt
    go_to_detail = app.go_to_detail
    plotly_dark_layout = app.plotly_dark_layout
    _live_conn = app._live_conn
    ACCENT = app.ACCENT
    BG = app.BG
    TEXT = app.TEXT
    TEXT_MUTED = app.TEXT_MUTED
    CATEGORY_COLORS = app.CATEGORY_COLORS

    page_header("Dashboard", "Overview of your knowledge base")

    with st.spinner("Loading metrics..."):
        k = q("""SELECT
            (SELECT count(*) FROM conversations) AS conv,
            (SELECT count(*) FROM messages) AS msg,
            (SELECT count(*) FROM skills) AS sk,
            (SELECT count(*) FROM memory_chunks) AS mem""")

    r = k.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Conversations", f"{int(r['conv']):,}")
    c2.metric("Messages",      f"{int(r['msg']):,}")
    c3.metric("Memory chunks", f"{int(r['mem']):,}")
    c4.metric("Skills",        f"{int(r['sk']):,}")

    # Memory Health card sourced from throughline.status.collect_status so
    # the CLI / MCP / GUI all read from one place.
    try:
        from throughline.status import collect_status as _collect_status
        _health = _collect_status(conn=_live_conn())
    except Exception:
        _health = None

    if _health and _health.get("db_reachable"):
        st.markdown("<div style='height:1.0rem;'></div>", unsafe_allow_html=True)
        st.markdown('<div class="kai-section-label">Memory health</div>',
                    unsafe_allow_html=True)
        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Embedding coverage",
                  f"{_health.get('embedding_coverage_pct', 0.0):.1f}%")
        h2.metric("Projects", f"{_health.get('projects_count', 0):,}")
        h3.metric("Contradictions",
                  f"{_health.get('contradictions_outstanding', 0):,}")
        last_refl = _health.get("last_reflection_at") or "—"
        if last_refl != "—":
            last_refl = fmt_dt(last_refl, compact=True)
        h4.metric("Last reflection", last_refl)

        # Drift-audit row — only render once an audit has actually run, so
        # fresh installs don't show a confusing "0 drifted out of 0" tile.
        last_audit = _health.get("last_audit_at")
        if last_audit:
            sampled = int(_health.get("last_audit_sampled", 0) or 0)
            drifted = int(_health.get("last_audit_drifted", 0) or 0)
            drift_pct = (100.0 * drifted / sampled) if sampled else 0.0
            a1, a2, a3 = st.columns(3)
            a1.metric("Last audit", fmt_dt(last_audit, compact=True))
            a2.metric("Chunks sampled", f"{sampled:,}")
            a3.metric(
                "Drifted",
                f"{drifted:,}",
                f"{drift_pct:.0f}% of sample" if sampled else "—",
                delta_color="inverse",
            )

    st.markdown("<div style='height:1.5rem;'></div>", unsafe_allow_html=True)

    left, right = st.columns([3, 2])
    with left:
        st.markdown('<div class="kai-section-label">Activity · last 14 days</div>', unsafe_allow_html=True)
        timeline = q("""
            SELECT date_trunc('day', started_at)::date AS day, count(*) AS n
            FROM conversations
            WHERE started_at >= now() - interval '14 days'
            GROUP BY day ORDER BY day
        """)
        if timeline.empty:
            st.info("No recent activity.")
        else:
            today = datetime.utcnow().date()
            all_days = pd.DataFrame({"day": [today - timedelta(days=i) for i in range(13, -1, -1)]})
            timeline["day"] = pd.to_datetime(timeline["day"]).dt.date
            df_full = all_days.merge(timeline, on="day", how="left").fillna(0)
            df_full["n"] = df_full["n"].astype(int)

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_full["day"], y=df_full["n"],
                mode="lines+markers",
                line=dict(color=ACCENT, width=2.5, shape="spline", smoothing=0.6),
                marker=dict(size=7, color=ACCENT, line=dict(width=2, color=BG)),
                fill="tozeroy", fillcolor="rgba(88, 166, 255, 0.12)",
                hovertemplate="<b>%{x|%b %d}</b><br>%{y} conversations<extra></extra>",
            ))
            plotly_dark_layout(fig, height=260)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    with right:
        st.markdown('<div class="kai-section-label">Memory by category</div>', unsafe_allow_html=True)
        mc = q("SELECT category::text AS category, count(*) AS n FROM memory_chunks GROUP BY category ORDER BY n DESC")
        if mc.empty:
            st.info("No memory chunks yet.")
        else:
            colors = [CATEGORY_COLORS.get(c, ACCENT) for c in mc["category"]]
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=mc["n"], y=mc["category"], orientation="h",
                marker=dict(color=colors, line=dict(width=0)),
                hovertemplate="<b>%{y}</b><br>%{x} chunks<extra></extra>",
                text=mc["n"], textposition="outside",
                textfont=dict(color=TEXT_MUTED, size=11),
            ))
            plotly_dark_layout(fig, height=260)
            fig.update_layout(
                yaxis=dict(autorange="reversed", gridcolor="rgba(0,0,0,0)", tickfont=dict(color=TEXT, size=12)),
                xaxis=dict(showgrid=False, showticklabels=False),
                margin=dict(l=10, r=40, t=10, b=10),
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    st.markdown('<div class="kai-section-label">Recent conversations</div>', unsafe_allow_html=True)
    df = q("SELECT id, summary, project_name, model, started_at, message_count "
           "FROM conversations ORDER BY started_at DESC LIMIT 5")
    if df.empty:
        st.info("No conversations.")
        return

    for _, row in df.iterrows():
        title = row["summary"] or f"Conversation #{row['id']}"
        col_card, col_btn = st.columns([10, 1])
        with col_card:
            st.markdown(
                f"""<div class="kai-list-card">
                    <div class="kai-list-title">{title}</div>
                    <div class="kai-list-meta">
                        #{row['id']} · {row['project_name'] or 'no project'} · {row['model'] or '—'}
                        · {row['message_count']} messages · {fmt_dt(row['started_at'])}
                    </div>
                </div>""",
                unsafe_allow_html=True,
            )
        with col_btn:
            if st.button("Open", key=f"dash_open_{row['id']}", use_container_width=True):
                go_to_detail("conversation", row["id"])
