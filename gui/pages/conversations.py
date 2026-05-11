"""Conversations page — list every ingested session with filters."""

from __future__ import annotations


def render() -> None:
    from gui.app import (
        go_to_detail,
        page_header,
        q,
        render_export_buttons,
        st,
    )

    page_header("Conversations", "Every ingested session — filterable by project, model, and tool")

    with st.spinner("Loading filters..."):
        projs = q(
            "SELECT DISTINCT project_name FROM conversations "
            "WHERE project_name IS NOT NULL ORDER BY project_name"
        )
        mods = q(
            "SELECT DISTINCT model FROM conversations "
            "WHERE model IS NOT NULL ORDER BY model"
        )

    fcol1, fcol2, fcol3 = st.columns([2, 2, 3])
    with fcol1:
        proj_values = (
            projs["project_name"].dropna().tolist()
            if "project_name" in projs.columns
            else []
        )
        sel_p = st.selectbox("Project", ["All"] + proj_values)
    with fcol2:
        model_values = (
            mods["model"].dropna().tolist() if "model" in mods.columns else []
        )
        sel_m = st.selectbox("Model", ["All"] + model_values)
    with fcol3:
        search = st.text_input("Search in messages", placeholder="Full-text search...")

    w: list[str] = []
    params_list: list[str] = []
    if sel_p != "All":
        w.append("c.project_name = %s")
        params_list.append(sel_p)
    if sel_m != "All":
        w.append("c.model = %s")
        params_list.append(sel_m)
    if search:
        w.append(
            "c.id IN (SELECT DISTINCT conversation_id FROM messages WHERE content ILIKE %s)"
        )
        params_list.append(f"%{search}%")
    where = ("WHERE " + " AND ".join(w)) if w else ""

    with st.spinner("Querying conversations..."):
        df = q(
            f"SELECT c.id, c.summary AS title, c.project_name AS project, c.model, "
            f"c.started_at, c.message_count AS messages FROM conversations c "
            f"{where} ORDER BY c.started_at DESC LIMIT 500",
            params_list or None,
        )

    if df.empty:
        st.info("No conversations match.")
        return

    st.markdown(
        f'<div class="kai-section-label">{len(df)} conversations · click a row to open</div>',
        unsafe_allow_html=True,
    )
    render_export_buttons(
        df,
        key_prefix="conv_list",
        filename_base="conversations",
        title="Conversations",
    )
    sel = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        height=620,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "title": st.column_config.TextColumn("Title", width="large"),
            "project": st.column_config.TextColumn("Project", width="medium"),
            "model": st.column_config.TextColumn("Model", width="small"),
            "started_at": st.column_config.DatetimeColumn("Started", width="small"),
            "messages": st.column_config.NumberColumn("Msgs", width="small"),
        },
        key="df_conv",
    )
    if sel.selection and sel.selection.rows:
        go_to_detail("conversation", int(df.iloc[sel.selection.rows[0]]["id"]))
