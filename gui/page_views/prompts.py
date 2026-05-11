"""Prompts page — reusable prompt library with sort / filter / search."""

from __future__ import annotations

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
    chip = app.chip
    PURPLE = app.PURPLE
    TEXT = app.TEXT
    TEXT_MUTED = app.TEXT_MUTED
    TEXT_FAINT = app.TEXT_FAINT
    BORDER_MUTED = app.BORDER_MUTED

    page_header("Prompts", "Reusable prompt library")

    with st.expander("New prompt"):
        with st.form("new_p"):
            prn = st.text_input("Name")
            prc = st.text_input("Category")
            prt = st.text_area("Content", height=200)
            prtags = st.text_input("Tags (comma-separated)")
            if st.form_submit_button("Create", type="primary"):
                tags = [t.strip() for t in prtags.split(",") if t.strip()]
                msg = dml(
                    "INSERT INTO prompts (name, category, content, tags) VALUES (%s, %s, %s, %s)",
                    (prn, prc, prt, tags),
                )
                st.toast(msg)
                st.rerun()

    sort_col, cat_col, search_col = st.columns([2, 2, 4])
    with sort_col:
        sort_by = st.selectbox(
            "Sort by",
            ["created_newest", "created_oldest", "updated_newest", "used_most", "name_az"],
            format_func=lambda x: {
                "created_newest": "Created (newest first)",
                "created_oldest": "Created (oldest first)",
                "updated_newest": "Updated (newest first)",
                "used_most": "Used (most first)",
                "name_az": "Name (A-Z)",
            }[x],
            index=0,
        )
    with cat_col:
        cats_df = q("SELECT DISTINCT category FROM prompts WHERE category IS NOT NULL ORDER BY category")
        cat_values = cats_df["category"].dropna().tolist() if "category" in cats_df.columns else []
        cat_filter = st.selectbox("Category", ["All"] + cat_values)
    with search_col:
        search_text = st.text_input("Search name/content", placeholder="...")

    order_map = {
        "created_newest": "created_at DESC NULLS LAST, id DESC",
        "created_oldest": "created_at ASC NULLS LAST, id ASC",
        "updated_newest": "COALESCE(updated_at, created_at) DESC NULLS LAST, id DESC",
        "used_most": "usage_count DESC NULLS LAST, name",
        "name_az": "name ASC",
    }
    where = []
    params = []
    if cat_filter != "All":
        where.append("category = %s"); params.append(cat_filter)
    if search_text:
        where.append("(name ILIKE %s OR content ILIKE %s)")
        params += [f"%{search_text}%", f"%{search_text}%"]
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with st.spinner("Loading prompts..."):
        df = q(
            f"""SELECT id, name, category, usage_count, tags, content,
                       created_at, updated_at
                FROM prompts
                {where_sql}
                ORDER BY {order_map[sort_by]}""",
            params or None,
        )
    if df.empty:
        st.info("No prompts match the filters.")
        return

    st.markdown(
        f'<div class="kai-section-label">{len(df)} prompts</div>',
        unsafe_allow_html=True,
    )
    prompts_export = df.copy()
    prompts_export["tags"] = prompts_export["tags"].apply(
        lambda x: ", ".join(x) if isinstance(x, list) else (x or "")
    )
    render_export_buttons(
        prompts_export, key_prefix="prompts_list",
        filename_base="prompts",
        title="Prompts",
    )
    cols = st.columns(2)
    for i, (_, row) in enumerate(df.iterrows()):
        col = cols[i % 2]
        with col:
            cat_html = badge(row["category"], PURPLE) if row["category"] else ""
            tags_html = " ".join(chip(t) for t in (row["tags"] or [])[:4])
            preview = (row["content"] or "")[:200] + ("..." if row["content"] and len(row["content"]) > 200 else "")
            date_str = ""
            if row["created_at"] is not None and not pd.isna(row["created_at"]):
                date_str = row["created_at"].strftime("%Y-%m-%d %H:%M")
            st.markdown(
                f"""<div class="kai-list-card" style="min-height:180px;display:flex;flex-direction:column;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                        <div style="font-size:14px;font-weight:600;color:{TEXT};">{row['name']}</div>
                        <div>{cat_html}</div>
                    </div>
                    <div style="font-size:11px;color:{TEXT_FAINT};margin-bottom:6px;">created {date_str or '—'}</div>
                    <div style="font-size:12.5px;color:{TEXT_MUTED};line-height:1.5;flex:1;font-family:'SF Mono',monospace;margin-bottom:10px;">{preview}</div>
                    <div style="display:flex;justify-content:space-between;align-items:center;border-top:1px solid {BORDER_MUTED};padding-top:10px;">
                        <div>{tags_html}</div>
                        <div style="font-size:11px;color:{TEXT_FAINT};">used {int(row['usage_count'] or 0)}×</div>
                    </div>
                </div>""",
                unsafe_allow_html=True,
            )
            if st.button("Open", key=f"pt_open_{row['id']}"):
                go_to_detail("prompt", int(row["id"]))
