"""Global search page — single query × multiple scopes, drill-through."""

from __future__ import annotations

from gui.page_views import app_ns


def render() -> None:
    app = app_ns()
    st = app.st
    q = app.q
    page_header = app.page_header
    render_export_buttons = app.render_export_buttons
    go_to_detail = app.go_to_detail

    page_header("Global search", "Search across all knowledge artifacts")

    col_q, col_btn = st.columns([8, 1])
    with col_q:
        search_term = st.text_input(
            "search",
            placeholder="Search conversations, memory, skills, projects, prompts...",
            label_visibility="collapsed",
            key="global_search",
        )
    with col_btn:
        st.button("Search", type="primary", use_container_width=True)

    st.markdown('<div class="kai-section-label">Scope</div>', unsafe_allow_html=True)
    scope_cols = st.columns(6)
    sc_conv = scope_cols[0].checkbox("Conversations", value=True)
    sc_msg  = scope_cols[1].checkbox("Messages",      value=True)
    sc_mem  = scope_cols[2].checkbox("Memory",        value=True)
    sc_sk   = scope_cols[3].checkbox("Skills",        value=True)
    sc_pr   = scope_cols[4].checkbox("Projects",      value=True)
    sc_pt   = scope_cols[5].checkbox("Prompts",       value=True)

    if not search_term:
        return

    term_like = f"%{search_term}%"
    total_hits = 0
    st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)

    if sc_conv:
        df_c = q("""
            SELECT id, summary, project_name, started_at, message_count
            FROM conversations
            WHERE summary ILIKE %s OR project_name ILIKE %s
            ORDER BY started_at DESC LIMIT 20
        """, (term_like, term_like))
        if not df_c.empty:
            total_hits += len(df_c)
            with st.expander(f"Conversations · {len(df_c)}", expanded=True):
                render_export_buttons(
                    df_c, key_prefix="srch_conv_x",
                    filename_base=f"search_conversations_{search_term}",
                    title=f"Search · Conversations · {search_term}",
                )
                sel_c = st.dataframe(
                    df_c, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row",
                    column_config={
                        "id": st.column_config.NumberColumn("ID", width="small"),
                        "summary": st.column_config.TextColumn("Title", width="large"),
                        "project_name": st.column_config.TextColumn("Project", width="small"),
                        "started_at": st.column_config.DatetimeColumn("Started"),
                        "message_count": st.column_config.NumberColumn("Msgs", width="small"),
                    },
                    key="srch_conv",
                )
                if sel_c.selection and sel_c.selection.rows:
                    go_to_detail("conversation", int(df_c.iloc[sel_c.selection.rows[0]]["id"]))

    if sc_msg:
        df_m = q("""
            SELECT m.conversation_id, c.summary AS titel, m.role::text,
                   substring(m.content, 1, 200) AS snippet, m.created_at
            FROM messages m JOIN conversations c ON c.id = m.conversation_id
            WHERE m.content ILIKE %s
            ORDER BY m.created_at DESC LIMIT 30
        """, (term_like,))
        if not df_m.empty:
            total_hits += len(df_m)
            with st.expander(f"Messages · {len(df_m)}", expanded=True):
                render_export_buttons(
                    df_m, key_prefix="srch_msg_x",
                    filename_base=f"search_messages_{search_term}",
                    title=f"Search · Messages · {search_term}",
                )
                sel_m = st.dataframe(
                    df_m, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row",
                    column_config={
                        "conversation_id": st.column_config.NumberColumn("Conv", width="small"),
                        "titel": st.column_config.TextColumn("Title", width="medium"),
                        "role": st.column_config.TextColumn("Role", width="small"),
                        "snippet": st.column_config.TextColumn("Snippet", width="large"),
                        "created_at": st.column_config.DatetimeColumn("Time", width="small"),
                    },
                    key="srch_msg",
                )
                if sel_m.selection and sel_m.selection.rows:
                    go_to_detail("conversation", int(df_m.iloc[sel_m.selection.rows[0]]["conversation_id"]))

    if sc_mem:
        df_mc = q("""
            SELECT id, category::text AS category, substring(content, 1, 200) AS content,
                   confidence, project_name, tags
            FROM memory_chunks
            WHERE content ILIKE %s OR %s = ANY(tags) OR project_name ILIKE %s
            ORDER BY confidence DESC LIMIT 30
        """, (term_like, search_term, term_like))
        if not df_mc.empty:
            total_hits += len(df_mc)
            with st.expander(f"Memory · {len(df_mc)}", expanded=True):
                df_mc_disp = df_mc.copy()
                df_mc_disp["tags"] = df_mc_disp["tags"].apply(lambda x: ", ".join(x) if isinstance(x, list) else "")
                render_export_buttons(
                    df_mc_disp, key_prefix="srch_mc_x",
                    filename_base=f"search_memory_{search_term}",
                    title=f"Search · Memory · {search_term}",
                )
                sel_mc = st.dataframe(
                    df_mc_disp, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row",
                    key="srch_mc",
                )
                if sel_mc.selection and sel_mc.selection.rows:
                    go_to_detail("memory", int(df_mc.iloc[sel_mc.selection.rows[0]]["id"]))

    if sc_sk:
        df_sk = q("""
            SELECT id, name, substring(description, 1, 200) AS description, use_count
            FROM skills
            WHERE name ILIKE %s OR description ILIKE %s
            ORDER BY COALESCE(file_modified, last_used, created_at) DESC NULLS LAST LIMIT 20
        """, (term_like, term_like))
        if not df_sk.empty:
            total_hits += len(df_sk)
            with st.expander(f"Skills · {len(df_sk)}", expanded=True):
                render_export_buttons(
                    df_sk, key_prefix="srch_sk_x",
                    filename_base=f"search_skills_{search_term}",
                    title=f"Search · Skills · {search_term}",
                )
                sel_sk = st.dataframe(
                    df_sk, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row",
                    key="srch_sk",
                )
                if sel_sk.selection and sel_sk.selection.rows:
                    go_to_detail("skill", int(df_sk.iloc[sel_sk.selection.rows[0]]["id"]))

    if sc_pr:
        df_pr = q("""
            SELECT id, name, description, status::text AS status
            FROM projects
            WHERE name ILIKE %s OR description ILIKE %s
            ORDER BY created_at DESC NULLS LAST LIMIT 20
        """, (term_like, term_like))
        if not df_pr.empty:
            total_hits += len(df_pr)
            with st.expander(f"Projects · {len(df_pr)}", expanded=True):
                render_export_buttons(
                    df_pr, key_prefix="srch_pr_x",
                    filename_base=f"search_projects_{search_term}",
                    title=f"Search · Projects · {search_term}",
                )
                sel_pr = st.dataframe(
                    df_pr, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row",
                    key="srch_pr",
                )
                if sel_pr.selection and sel_pr.selection.rows:
                    go_to_detail("project", int(df_pr.iloc[sel_pr.selection.rows[0]]["id"]))

    if sc_pt:
        df_pt = q("""
            SELECT id, name, category, substring(content, 1, 200) AS content, tags
            FROM prompts
            WHERE name ILIKE %s OR content ILIKE %s OR category ILIKE %s
            ORDER BY created_at DESC NULLS LAST LIMIT 20
        """, (term_like, term_like, term_like))
        if not df_pt.empty:
            total_hits += len(df_pt)
            with st.expander(f"Prompts · {len(df_pt)}", expanded=True):
                df_pt_disp = df_pt.copy()
                df_pt_disp["tags"] = df_pt_disp["tags"].apply(lambda x: ", ".join(x) if isinstance(x, list) else "")
                render_export_buttons(
                    df_pt_disp, key_prefix="srch_pt_x",
                    filename_base=f"search_prompts_{search_term}",
                    title=f"Search · Prompts · {search_term}",
                )
                sel_pt = st.dataframe(
                    df_pt_disp, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row",
                    key="srch_pt",
                )
                if sel_pt.selection and sel_pt.selection.rows:
                    go_to_detail("prompt", int(df_pt.iloc[sel_pt.selection.rows[0]]["id"]))

    if total_hits == 0:
        st.info(f"No results for '{search_term}'.")
    else:
        st.success(f"{total_hits} results across selected scopes.")
