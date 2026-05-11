"""Skills page — list every indexed skill (Claude Code SKILL.md files)."""

from __future__ import annotations


def render() -> None:
    # Imports are local to keep page modules cheap to load and to avoid
    # any startup-order coupling between app.py and the page modules.
    from gui.app import (
        BORDER_MUTED,
        TEXT,
        TEXT_FAINT,
        TEXT_MUTED,
        fmt_dt,
        go_to_detail,
        page_header,
        q,
        render_export_buttons,
        st,
    )

    page_header("Skills", "Registered skills available in your environment")
    with st.spinner("Loading skills..."):
        df = q(
            """SELECT id, name, description, use_count, last_used, version,
                      COALESCE(file_modified, last_used, created_at) AS sort_date
               FROM skills
               ORDER BY sort_date DESC NULLS LAST, use_count DESC NULLS LAST, name"""
        )
    if df.empty:
        st.info("No skills. Run the skill scanner from the Ingestion page.")
        return

    st.markdown(
        f'<div class="kai-section-label">{len(df)} skills</div>',
        unsafe_allow_html=True,
    )
    skills_export = df[
        ["id", "name", "description", "use_count", "last_used", "version"]
    ].copy()
    render_export_buttons(
        skills_export,
        key_prefix="skills_list",
        filename_base="skills",
        title="Skills",
    )
    cols = st.columns(3)
    for i, (_, row) in enumerate(df.iterrows()):
        col = cols[i % 3]
        with col:
            desc = row["description"] or "No description"
            desc_short = desc[:160] + ("..." if len(desc) > 160 else "")
            uc = int(row["use_count"] or 0)
            last = fmt_dt(row["last_used"], compact=True) if row["last_used"] else "never"
            st.markdown(
                f"""<div class="kai-list-card" style="min-height:180px;display:flex;flex-direction:column;">
                    <div style="font-size:14px;font-weight:600;color:{TEXT};margin-bottom:6px;letter-spacing:-0.01em;">{row['name']}</div>
                    <div style="font-size:12.5px;color:{TEXT_MUTED};line-height:1.5;flex:1;margin-bottom:12px;">{desc_short}</div>
                    <div style="display:flex;justify-content:space-between;font-size:11px;color:{TEXT_FAINT};border-top:1px solid {BORDER_MUTED};padding-top:10px;">
                        <span>used {uc}×</span>
                        <span>{last}</span>
                    </div>
                </div>""",
                unsafe_allow_html=True,
            )
            if st.button("Open", key=f"sk_open_{row['id']}"):
                go_to_detail("skill", int(row["id"]))
