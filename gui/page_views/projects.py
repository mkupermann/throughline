"""Projects page — curated project list with sort, Cards/List view,
new-project form, synthesised activity blurbs, drill-through."""

from __future__ import annotations

import json

import pandas as pd

from gui.page_views import app_ns


def render() -> None:
    app = app_ns()
    st = app.st
    q = app.q
    dml = app.dml
    page_header = app.page_header
    render_export_buttons = app.render_export_buttons
    go_to_detail = app.go_to_detail
    badge = app.badge
    fmt_dt = app.fmt_dt
    STATUS_COLORS = app.STATUS_COLORS
    ACCENT = app.ACCENT
    TEXT = app.TEXT
    TEXT_MUTED = app.TEXT_MUTED

    page_header("Projects", "Tracked projects and their context")

    with st.expander("New project"):
        with st.form("new_pr"):
            pn = st.text_input("Name")
            pd_text = st.text_area("Description", height=80)
            ps = st.selectbox("Status", ["active", "paused", "completed", "archived"])
            pc = st.text_area("Contacts (JSON)", value="[]", height=80)
            if st.form_submit_button("Create", type="primary"):
                try:
                    cj = json.loads(pc)
                    msg = dml(
                        "INSERT INTO projects (name, description, status, contacts) VALUES (%s, %s, %s, %s)",
                        (pn, pd_text, ps, json.dumps(cj)),
                    )
                    st.toast(msg)
                    st.rerun()
                except json.JSONDecodeError:
                    st.error("Contacts must be valid JSON.")

    sort_col, view_col = st.columns([3, 2])
    with sort_col:
        sort_by = st.selectbox(
            "Sort by",
            options=[
                ("last_activity", "Recent activity"),
                ("created_at",    "Created"),
                ("name",          "Name (A→Z)"),
                ("chunks_count",  "Memory volume"),
                ("status",        "Status"),
            ],
            format_func=lambda x: x[1],
            key="proj_sort",
        )
    with view_col:
        view_mode = st.radio(
            "View",
            options=["Cards", "List"],
            horizontal=True,
            key="proj_view",
        )

    sort_key = sort_by[0]
    sort_dir = "ASC" if sort_key == "name" else "DESC"

    sql = f"""
        SELECT
            p.id,
            p.name,
            p.description,
            p.status::text                                       AS status,
            p.created_at,
            COALESCE(mc.chunks_count, 0)                         AS chunks_count,
            COALESCE(cv.conversations_count, 0)                  AS conversations_count,
            GREATEST(mc.last_activity, cv.last_activity)         AS last_activity,
            LEAST(mc.first_activity, cv.first_activity)          AS first_activity
        FROM projects p
        LEFT JOIN (
            SELECT project_name,
                   count(*)        AS chunks_count,
                   max(created_at) AS last_activity,
                   min(created_at) AS first_activity
            FROM memory_chunks
            WHERE project_name IS NOT NULL
            GROUP BY project_name
        ) mc ON mc.project_name = p.name
        LEFT JOIN (
            SELECT project_name,
                   count(*)        AS conversations_count,
                   max(started_at) AS last_activity,
                   min(started_at) AS first_activity
            FROM conversations
            WHERE project_name IS NOT NULL
            GROUP BY project_name
        ) cv ON cv.project_name = p.name
        ORDER BY {sort_key} {sort_dir} NULLS LAST, p.name ASC
    """
    with st.spinner("Loading projects..."):
        df = q(sql)
    if df.empty:
        st.info("No projects.")
        return

    st.markdown(
        f'<div class="kai-section-label">{len(df)} projects · sorted by {sort_by[1].lower()}</div>',
        unsafe_allow_html=True,
    )
    render_export_buttons(
        df, key_prefix="projects_list",
        filename_base="projects",
        title="Projects",
    )

    def _activity_blurb(row) -> str:
        chunks = int(row.get("chunks_count") or 0)
        convs = int(row.get("conversations_count") or 0)
        parts: list[str] = []
        if chunks:
            parts.append(f"{chunks:,} chunk{'s' if chunks != 1 else ''}")
        if convs:
            parts.append(f"{convs:,} conversation{'s' if convs != 1 else ''}")
        la = row.get("last_activity")
        if la is not None and not pd.isna(la):
            parts.append(f"last active {fmt_dt(la, compact=True)}")
        if not parts:
            return "No description · no activity recorded yet"
        return " · ".join(parts)

    def _row_description(row) -> str:
        """Return a printable description.

        ``pandas.NaN`` is a truthy float, so ``row['description'] or fallback``
        leaks NaN through and breaks str slicing. Check ``pd.isna`` first."""
        raw = row.get("description")
        if raw is None or (isinstance(raw, float) and pd.isna(raw)):
            return _activity_blurb(row)
        text = str(raw).strip()
        return text or _activity_blurb(row)

    if view_mode == "Cards":
        cols = st.columns(2)
        for i, (_, row) in enumerate(df.iterrows()):
            col = cols[i % 2]
            with col:
                sc = STATUS_COLORS.get(row["status"], ACCENT)
                desc = _row_description(row)
                desc_short = desc[:220] + ("…" if len(desc) > 220 else "")
                st.markdown(
                    f"""<div class="kai-list-card" style="min-height:150px;display:flex;flex-direction:column;">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
                            <div style="font-size:15px;font-weight:600;color:{TEXT};letter-spacing:-0.01em;">{row['name']}</div>
                            <div>{badge(row["status"], sc)}</div>
                        </div>
                        <div style="font-size:12.5px;color:{TEXT_MUTED};line-height:1.55;flex:1;">{desc_short}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
                if st.button("Open", key=f"pr_open_{row['id']}"):
                    go_to_detail("project", int(row["id"]))
    else:
        for _, row in df.iterrows():
            sc = STATUS_COLORS.get(row["status"], ACCENT)
            desc = _row_description(row)
            desc_short = desc[:140] + ("…" if len(desc) > 140 else "")
            col_card, col_btn = st.columns([10, 1])
            with col_card:
                st.markdown(
                    f"""<div class="kai-list-card" style="padding:12px 16px;">
                        <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
                            <div style="display:flex;flex-direction:column;min-width:0;flex:1;">
                                <div style="font-size:14px;font-weight:600;color:{TEXT};letter-spacing:-0.01em;">{row['name']}</div>
                                <div style="font-size:12px;color:{TEXT_MUTED};line-height:1.4;margin-top:3px;">{desc_short}</div>
                            </div>
                            <div style="flex-shrink:0;">{badge(row["status"], sc)}</div>
                        </div>
                    </div>""",
                    unsafe_allow_html=True,
                )
            with col_btn:
                if st.button("Open", key=f"pr_open_{row['id']}", use_container_width=True):
                    go_to_detail("project", int(row["id"]))
