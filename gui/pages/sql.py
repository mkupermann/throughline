"""SQL console — direct read/write access to the underlying Postgres."""

from __future__ import annotations


def render() -> None:
    from gui.app import TEXT, dml, page_header, q, st

    page_header("SQL console", "Direct SQL access — no undo")

    st.markdown('<div class="kai-section-label">Query</div>', unsafe_allow_html=True)
    sql = st.text_area(
        "sql",
        height=180,
        placeholder="SELECT * FROM conversations ORDER BY started_at DESC LIMIT 10;",
        label_visibility="collapsed",
        key="sql_editor",
    )

    bcol1, bcol2, _ = st.columns([1, 1, 6])
    run_clicked = bcol1.button("Run", type="primary", use_container_width=True)
    clear_clicked = bcol2.button("Clear", use_container_width=True)
    if clear_clicked:
        st.rerun()

    if run_clicked:
        if not sql.strip():
            st.error("Enter SQL.")
        else:
            upper = sql.strip().upper()
            is_read = (
                upper.startswith("SELECT")
                or upper.startswith("WITH")
                or upper.startswith("EXPLAIN")
            )
            if is_read:
                try:
                    with st.spinner("Running query..."):
                        df = q(sql)
                    st.success(f"{len(df)} rows")
                    st.dataframe(df, use_container_width=True, hide_index=True)
                except Exception as e:
                    st.error(str(e))
            else:
                st.info(dml(sql))

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.markdown('<div class="kai-section-label">Snippets</div>', unsafe_allow_html=True)
    sn_cols = st.columns(2)
    snippets = [
        ("Recent conversations",
         "SELECT * FROM conversations ORDER BY started_at DESC LIMIT 20;"),
        ("Memory by category",
         "SELECT category::text, count(*) FROM memory_chunks GROUP BY category ORDER BY count DESC;"),
        ("Top projects",
         "SELECT project_name, count(*) AS sessions FROM conversations GROUP BY project_name ORDER BY sessions DESC LIMIT 20;"),
        ("Messages by role",
         "SELECT role::text, count(*) FROM messages GROUP BY role;"),
    ]
    for i, (label, sq) in enumerate(snippets):
        with sn_cols[i % 2]:
            with st.container(border=True):
                st.markdown(
                    f'<div style="font-size:12px;font-weight:600;color:{TEXT};margin-bottom:6px;">{label}</div>',
                    unsafe_allow_html=True,
                )
                st.code(sq, language="sql")
