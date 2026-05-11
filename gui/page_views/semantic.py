"""Semantic search page — pgvector cosine across chunks and messages."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pandas as pd

from gui.page_views import app_ns


def render() -> None:
    app = app_ns()
    st = app.st
    page_header = app.page_header
    get_conn = app.get_conn
    semantic_helper = app.semantic_helper
    render_export_buttons = app.render_export_buttons
    go_to_detail = app.go_to_detail
    DEMO_MODE = app.DEMO_MODE
    _demo_disabled_button = app._demo_disabled_button
    TEXT = app.TEXT
    TEXT_FAINT = app.TEXT_FAINT
    BORDER_MUTED = app.BORDER_MUTED

    page_header(
        "Semantic search",
        "Vector similarity across memory chunks and messages (pgvector cosine distance)",
    )

    if semantic_helper is None or not semantic_helper.backend_available():
        reason = ""
        if semantic_helper is not None and hasattr(semantic_helper, "last_reason"):
            reason = semantic_helper.last_reason() or ""
        msg = "Semantic backend unavailable."
        if reason:
            msg += f"\n\n**Reason:** {reason}"
        msg += (
            "\n\nOnce the backend is reachable, generate embeddings with: "
            "`.venv/bin/throughline embed --backend auto`."
        )
        st.error(msg)
        return

    conn = get_conn()
    n_emb = semantic_helper.count_embeddings(conn)

    info_col, btn_col = st.columns([3, 1])
    with info_col:
        st.markdown(
            f"""<div class="kai-list-card" style="display:flex;align-items:center;gap:18px;">
                <div><div style="font-size:11px;color:{TEXT_FAINT};text-transform:uppercase;letter-spacing:0.08em;">Backend</div>
                <div style="font-size:14px;color:{TEXT};font-weight:600;">{semantic_helper.backend_label()}</div></div>
                <div style="width:1px;height:32px;background:{BORDER_MUTED};"></div>
                <div><div style="font-size:11px;color:{TEXT_FAINT};text-transform:uppercase;letter-spacing:0.08em;">Embeddings</div>
                <div style="font-size:14px;color:{TEXT};font-weight:600;">{n_emb:,}</div></div>
            </div>""",
            unsafe_allow_html=True,
        )
    with btn_col:
        if DEMO_MODE:
            _demo_disabled_button("Refresh embeddings", key="refresh_embeddings_demo")
        elif st.button("Refresh embeddings", use_container_width=True):
            with st.spinner("Generating embeddings..."):
                p = subprocess.run(
                    ["python3",
                     str(Path(__file__).resolve().parents[2] / "scripts" / "generate_embeddings.py"),
                     "--backend", "auto"],
                    capture_output=True, text=True, timeout=1800,
                )
                if p.returncode == 0:
                    st.toast("Embeddings updated.")
                else:
                    st.error(f"Error:\n{p.stderr[-400:]}")

    st.markdown('<div style="height:0.75rem;"></div>', unsafe_allow_html=True)

    q_col, s_col = st.columns([8, 1])
    with q_col:
        sem_q = st.text_input(
            "sem_query",
            placeholder="Natural language query — e.g. 'why pgvector instead of Milvus' or 'project Alpha scheduling'",
            label_visibility="collapsed",
            key="sem_query",
        )
    with s_col:
        st.button("Search", type="primary", use_container_width=True, key="sem_search")

    sem_limit = st.slider("Results", 5, 50, 20, 5)

    if not sem_q:
        return

    with st.spinner("Embedding query..."):
        results = semantic_helper.semantic_search(conn, sem_q, limit=sem_limit)

    if not results:
        st.info("No hits. Either no embeddings for this backend or query returned nothing.")
        return

    mems = [r for r in results if r["source_type"] == "memory_chunk"]
    msgs = [r for r in results if r["source_type"] == "message"]
    st.success(f"{len(results)} hits")

    if mems:
        with st.expander(f"Memory chunks · {len(mems)}", expanded=True):
            df = pd.DataFrame([{
                "id": int(r["source_id"]),
                "category": r["category"],
                "project": r.get("project_name") or "",
                "distance": f"{float(r['distance']):.3f}",
                "similarity": f"{1 - float(r['distance']):.3f}",
                "content": (r["content"] or "")[:200],
            } for r in mems])
            render_export_buttons(
                df, key_prefix="sem_mems_x",
                filename_base=f"semantic_memory_{sem_q}",
                title=f"Semantic · Memory · {sem_q}",
            )
            sel_m = st.dataframe(
                df, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row",
                column_config={
                    "id": st.column_config.NumberColumn("ID", width="small"),
                    "category": st.column_config.TextColumn("Cat", width="small"),
                    "project": st.column_config.TextColumn("Project", width="small"),
                    "distance": st.column_config.TextColumn("Dist", width="small"),
                    "similarity": st.column_config.TextColumn("Sim", width="small"),
                    "content": st.column_config.TextColumn("Content", width="large"),
                },
                key="sem_mems",
            )
            if sel_m.selection and sel_m.selection.rows:
                go_to_detail("memory", int(df.iloc[sel_m.selection.rows[0]]["id"]))

    if msgs:
        with st.expander(f"Messages · {len(msgs)}", expanded=True):
            df = pd.DataFrame([{
                "msg_id": int(r["source_id"]),
                "conv_id": int(r.get("conversation_id") or 0),
                "role": r["category"],
                "project": r.get("project_name") or "",
                "distance": f"{float(r['distance']):.3f}",
                "similarity": f"{1 - float(r['distance']):.3f}",
                "content": (r["content"] or "")[:200],
            } for r in msgs])
            render_export_buttons(
                df, key_prefix="sem_msgs_x",
                filename_base=f"semantic_messages_{sem_q}",
                title=f"Semantic · Messages · {sem_q}",
            )
            sel_x = st.dataframe(
                df, use_container_width=True, hide_index=True,
                on_select="rerun", selection_mode="single-row",
                column_config={
                    "msg_id": st.column_config.NumberColumn("Msg", width="small"),
                    "conv_id": st.column_config.NumberColumn("Conv", width="small"),
                    "role": st.column_config.TextColumn("Role", width="small"),
                    "project": st.column_config.TextColumn("Project", width="small"),
                    "distance": st.column_config.TextColumn("Dist", width="small"),
                    "similarity": st.column_config.TextColumn("Sim", width="small"),
                    "content": st.column_config.TextColumn("Content", width="large"),
                },
                key="sem_msgs",
            )
            if sel_x.selection and sel_x.selection.rows:
                go_to_detail("conversation", int(df.iloc[sel_x.selection.rows[0]]["conv_id"]))
