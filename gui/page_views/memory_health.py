"""Memory Health page — status counts, top-accessed chunks, reflection
trigger, recent reflections, supersede/merge link viewer."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from gui.page_views import app_ns


def render() -> None:
    app = app_ns()
    st = app.st
    q = app.q
    page_header = app.page_header
    render_export_buttons = app.render_export_buttons
    go_to_detail = app.go_to_detail
    DEMO_MODE = app.DEMO_MODE
    _demo_disabled_button = app._demo_disabled_button
    SCRIPTS_ROOT = app.SCRIPTS_ROOT

    page_header("Memory health", "Self-reflecting memory engine — status, reflections and maintenance")

    stats = q("""
        SELECT
          COUNT(*) FILTER (WHERE COALESCE(status,'active')='active')    AS active,
          COUNT(*) FILTER (WHERE status='superseded')                   AS superseded,
          COUNT(*) FILTER (WHERE status='merged')                       AS merged,
          COUNT(*) FILTER (WHERE status='stale')                        AS stale,
          COUNT(*)                                                      AS total
        FROM memory_chunks
    """)
    r = stats.iloc[0]
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Active",     f"{int(r['active']):,}")
    m2.metric("Superseded", f"{int(r['superseded']):,}")
    m3.metric("Merged",     f"{int(r['merged']):,}")
    m4.metric("Stale",      f"{int(r['stale']):,}")
    m5.metric("Total",      f"{int(r['total']):,}")

    st.markdown('<div class="kai-section-label">Top accessed chunks</div>', unsafe_allow_html=True)
    hot = q("""
        SELECT id, category::text AS category, substring(content, 1, 120) AS content,
               access_count, last_accessed
        FROM memory_chunks
        WHERE COALESCE(access_count, 0) > 0
          AND COALESCE(status, 'active') = 'active'
        ORDER BY access_count DESC, last_accessed DESC NULLS LAST
        LIMIT 10
    """)
    if hot.empty:
        st.info("No access tracks yet — query.py search updates these counters.")
    else:
        render_export_buttons(
            hot, key_prefix="mh_hot",
            filename_base="memory_top_accessed",
            title="Memory — Top accessed chunks",
        )
        sel_h = st.dataframe(
            hot, use_container_width=True, hide_index=True,
            on_select="rerun", selection_mode="single-row",
            column_config={
                "id":            st.column_config.NumberColumn("ID", width="small"),
                "category":      st.column_config.TextColumn("Category", width="small"),
                "content":       st.column_config.TextColumn("Content", width="large"),
                "access_count":  st.column_config.NumberColumn("Access", width="small"),
                "last_accessed": st.column_config.DatetimeColumn("Last", width="small"),
            },
            key="df_hot",
        )
        if sel_h.selection and sel_h.selection.rows:
            go_to_detail("memory", int(hot.iloc[sel_h.selection.rows[0]]["id"]))

    st.markdown('<hr/>', unsafe_allow_html=True)
    st.markdown('<div class="kai-section-label">Reflection</div>', unsafe_allow_html=True)

    SCRIPT = str(SCRIPTS_ROOT / "reflect_memory.py")
    rc1, rc2, rc3 = st.columns([2, 2, 1])
    with rc1:
        mode_choice = st.selectbox("Mode", ["all", "dedup", "contradictions", "stale", "consolidate"])
    with rc2:
        limit_choice = st.slider("Limit (pairs / candidates)", 5, 200, 40, 5)
    with rc3:
        dry_run = st.checkbox("Dry run", value=False)

    if DEMO_MODE:
        _demo_disabled_button("Run reflection now", key="run_reflection_demo", use_container_width=False)
    elif st.button("Run reflection now", type="primary"):
        args = [sys.executable, SCRIPT, "--limit", str(limit_choice)]
        if mode_choice != "all":
            args += ["--mode", mode_choice]
        if dry_run:
            args += ["--dry-run"]
        log_dir = Path.home() / "Library/Application Support/Claude/scheduler/logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "reflect_memory.log"
        with open(log_file, "ab") as f:
            subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT)
        st.toast(f"Reflection started · log: {log_file}")

    st.markdown('<hr/>', unsafe_allow_html=True)

    st.markdown('<div class="kai-section-label">Recent reflections</div>', unsafe_allow_html=True)
    limit_log = st.slider("Show", 10, 200, 50, 10, key="refl_log_limit")
    refl = q("""
        SELECT id, reflection_type, action_taken, array_length(affected_chunks,1) AS n_chunks,
               affected_chunks, confidence, created_at, substring(reasoning, 1, 200) AS reasoning
        FROM memory_reflections
        ORDER BY created_at DESC
        LIMIT %s
    """, (limit_log,))

    if refl.empty:
        st.info("No reflections yet.")
    else:
        agg = q("""
            SELECT reflection_type, COUNT(*) AS n,
                   COUNT(*) FILTER (WHERE action_taken LIKE 'merged%%' OR action_taken LIKE '%%superseded%%'
                                    OR action_taken IN ('marked_stale','created_super_chunk')) AS mutated
            FROM memory_reflections GROUP BY reflection_type ORDER BY n DESC
        """)
        if not agg.empty:
            agg_cols = st.columns(min(4, len(agg)))
            for i, (_, row_a) in enumerate(agg.iterrows()):
                if i >= 4:
                    break
                agg_cols[i].metric(
                    row_a["reflection_type"],
                    f"{int(row_a['n'])}",
                    f"mutated {int(row_a['mutated'])}",
                )

        refl_disp = refl.copy()
        refl_disp["affected_chunks"] = refl_disp["affected_chunks"].apply(
            lambda x: ",".join(str(i) for i in x) if isinstance(x, list) else ""
        )
        refl_export = refl_disp[["id", "reflection_type", "action_taken", "n_chunks",
                                 "affected_chunks", "confidence", "created_at", "reasoning"]]
        render_export_buttons(
            refl_export, key_prefix="mh_refl",
            filename_base="memory_reflections",
            title="Memory — Reflections",
        )
        st.dataframe(
            refl_export,
            use_container_width=True, hide_index=True,
            column_config={
                "id":              st.column_config.NumberColumn("ID", width="small"),
                "reflection_type": st.column_config.TextColumn("Type", width="small"),
                "action_taken":    st.column_config.TextColumn("Action", width="medium"),
                "n_chunks":        st.column_config.NumberColumn("#", width="small"),
                "affected_chunks": st.column_config.TextColumn("Chunks"),
                "confidence":      st.column_config.NumberColumn("Conf", format="%.2f", width="small"),
                "created_at":      st.column_config.DatetimeColumn("Time", width="small"),
                "reasoning":       st.column_config.TextColumn("Reasoning", width="large"),
            },
        )

    st.markdown('<hr/>', unsafe_allow_html=True)
    st.markdown('<div class="kai-section-label">Supersede and merge links</div>', unsafe_allow_html=True)
    links = q("""
        SELECT mc.id, mc.status, mc.superseded_by, mc.merged_from,
               substring(mc.content, 1, 80) AS content, mc.created_at
        FROM memory_chunks mc
        WHERE mc.status IN ('superseded', 'merged')
           OR (mc.merged_from IS NOT NULL AND array_length(mc.merged_from, 1) > 0)
        ORDER BY mc.created_at DESC
        LIMIT 100
    """)
    if links.empty:
        st.info("No supersede or merge links.")
        return

    links_disp = links.copy()
    links_disp["merged_from"] = links_disp["merged_from"].apply(
        lambda x: ",".join(str(i) for i in x) if isinstance(x, list) and x else ""
    )
    render_export_buttons(
        links_disp, key_prefix="mh_links",
        filename_base="memory_supersede_merge_links",
        title="Memory — Supersede / Merge links",
    )
    st.dataframe(links_disp, use_container_width=True, hide_index=True)
