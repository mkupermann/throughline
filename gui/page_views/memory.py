"""Memory page — extracted chunks browser with category filter, search,
new-chunk form, and bulk-forget expander."""

from __future__ import annotations

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
    chip = app.chip
    forget_chunks = app.forget_chunks
    _live_conn = app._live_conn
    DEMO_MODE = app.DEMO_MODE
    CATS = app.CATS
    CATEGORY_COLORS = app.CATEGORY_COLORS
    ACCENT = app.ACCENT
    TEXT = app.TEXT
    TEXT_MUTED = app.TEXT_MUTED
    TEXT_FAINT = app.TEXT_FAINT

    page_header("Memory", "Extracted decisions, patterns, insights and context")

    STATUS_OPTS = ["active", "all", "superseded", "merged", "stale"]
    fcol1, fcol2, fcol3, fcol4 = st.columns([2, 2, 3, 2])
    with fcol1:
        sel_cat = st.selectbox("Category", ["All"] + CATS)
    with fcol2:
        sel_proj = st.text_input("Project filter", placeholder="e.g. Alpha")
    with fcol3:
        sel_search = st.text_input("Search content")
    with fcol4:
        sel_status = st.selectbox("Status", STATUS_OPTS, index=0,
                                  help="Default 'active' hides superseded/merged/stale chunks.")

    w, params_list = [], []
    if sel_cat != "All":
        w.append("category = %s"); params_list.append(sel_cat)
    if sel_proj:
        w.append("project_name ILIKE %s"); params_list.append(f"%{sel_proj}%")
    if sel_search:
        w.append("content ILIKE %s"); params_list.append(f"%{sel_search}%")
    if sel_status == "active":
        w.append("COALESCE(status, 'active') = 'active'")
    elif sel_status != "all":
        w.append("status = %s"); params_list.append(sel_status)
    where = ("WHERE " + " AND ".join(w)) if w else ""

    with st.expander("New memory chunk"):
        with st.form("new_mc"):
            nc = st.text_area("Content", height=120)
            ca, cb = st.columns(2)
            ncat = ca.selectbox("Category", CATS, key="nc_cat")
            nproj = cb.text_input("Project", key="nc_proj")
            ntags = st.text_input("Tags (comma-separated)")
            nconf = st.slider("Confidence", 0.0, 1.0, 0.80, 0.05)
            if st.form_submit_button("Create", type="primary"):
                tags = [t.strip() for t in ntags.split(",") if t.strip()]
                msg = dml(
                    "INSERT INTO memory_chunks (content, category, tags, confidence, project_name, source_type) "
                    "VALUES (%s, %s, %s, %s, %s, 'manual')",
                    (nc, ncat, tags, nconf, nproj or None),
                )
                st.toast(msg)
                st.rerun()

    with st.expander("Forget chunks (cascade-delete with audit)"):
        if DEMO_MODE:
            st.info(
                "Disabled in demo mode. Locally, this expander cascade-deletes "
                "memory chunks and their embeddings, with a mandatory reason "
                "logged in `memory_reflections`. Run Throughline on your own "
                "machine to use it."
            )
        else:
            with st.form("bulk_forget_mc"):
                forget_ids_raw = st.text_input(
                    "Chunk IDs",
                    placeholder="e.g. 1234, 1287, 1290 — comma- or space-separated",
                    help="Cascade-deletes each chunk and its embeddings. Logs a row in memory_reflections.",
                )
                forget_reason = st.text_input(
                    "Reason (required)",
                    placeholder="Why are these being forgotten? (audit trail)",
                )
                if st.form_submit_button("Forget selected", type="primary"):
                    ids: list[int] = []
                    for tok in forget_ids_raw.replace(",", " ").split():
                        try:
                            ids.append(int(tok))
                        except ValueError:
                            pass
                    if not ids:
                        st.toast("No valid IDs.", icon="⚠️")
                    elif not forget_reason.strip():
                        st.toast("Reason is required.", icon="⚠️")
                    else:
                        try:
                            res = forget_chunks(_live_conn(), ids, reason=forget_reason.strip())
                            st.toast(
                                f"Forgotten {res['chunks']} chunk(s), "
                                f"{res['embeddings']} embedding(s) — audit #{res['reflection_id']}"
                            )
                            st.rerun()
                        except Exception as e:
                            st.toast(f"Error: {e}", icon="⚠️")

    with st.spinner("Loading memory..."):
        df = q(
            f"SELECT id, category::text AS category, content, confidence, project_name, tags, created_at "
            f"FROM memory_chunks {where} ORDER BY created_at DESC LIMIT 300",
            params_list or None,
        )

    st.markdown(
        f'<div class="kai-section-label">{len(df)} chunks</div>',
        unsafe_allow_html=True,
    )

    if df.empty:
        st.info("No memory chunks match.")
        return

    export_df = df.copy()
    export_df["tags"] = export_df["tags"].apply(
        lambda x: ", ".join(x) if isinstance(x, list) else (x or "")
    )
    render_export_buttons(
        export_df, key_prefix="memory_list",
        filename_base="memory_chunks",
        title="Memory chunks",
    )
    cols = st.columns(2)
    for i, (_, row) in enumerate(df.iterrows()):
        col = cols[i % 2]
        with col:
            cat = row["category"]
            color = CATEGORY_COLORS.get(cat, ACCENT)
            content_short = (row["content"] or "")[:280] + ("..." if row["content"] and len(row["content"]) > 280 else "")
            tags_html = " ".join(chip(t) for t in (row["tags"] or [])[:5])
            proj_html = (
                f'<span style="color:{TEXT_MUTED};font-size:11px;margin-left:10px;">· {row["project_name"]}</span>'
                if row["project_name"] else ""
            )
            conf_val = float(row['confidence']) if row['confidence'] is not None else 0
            st.markdown(
                f"""<div class="kai-list-card" style="min-height:150px;display:flex;flex-direction:column;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
                        <div>{badge(cat, color)}{proj_html}</div>
                        <div style="font-size:11px;color:{TEXT_FAINT};">conf {conf_val:.2f} · #{row['id']}</div>
                    </div>
                    <div style="font-size:13px;color:{TEXT};line-height:1.55;flex:1;margin-bottom:10px;">{content_short}</div>
                    <div>{tags_html}</div>
                </div>""",
                unsafe_allow_html=True,
            )
            if st.button("Open", key=f"mem_open_{row['id']}"):
                go_to_detail("memory", int(row["id"]))
