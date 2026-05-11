"""Knowledge Graph page — entities, relationships, mentions extracted from
conversations. Live graph rendering via streamlit-agraph (optional)."""

from __future__ import annotations

import subprocess
import sys

import pandas as pd

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
    ACCENT = app.ACCENT
    BG = app.BG
    BORDER = app.BORDER
    TEXT = app.TEXT
    TEXT_MUTED = app.TEXT_MUTED
    ENTITY_COLORS = app.ENTITY_COLORS

    page_header("Knowledge Graph", "Entities, relationships and mentions extracted from conversations")

    stats = q("""
        SELECT
            (SELECT count(*) FROM entities) AS ents,
            (SELECT count(*) FROM relationships) AS rels,
            (SELECT count(*) FROM entity_mentions) AS ments,
            (SELECT count(DISTINCT source_id) FROM entity_mentions WHERE source_type='conversation') AS convs_analyzed
    """)
    if not stats.empty:
        sr = stats.iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Entities",              f"{int(sr['ents']):,}")
        c2.metric("Relationships",         f"{int(sr['rels']):,}")
        c3.metric("Mentions",              f"{int(sr['ments']):,}")
        c4.metric("Conversations analyzed", f"{int(sr['convs_analyzed']):,}")

    st.markdown("<div style='height:1rem;'></div>", unsafe_allow_html=True)

    with st.expander("Run entity extraction"):
        unprocessed = q("""
            SELECT count(*) AS c FROM conversations c
            WHERE NOT EXISTS (SELECT 1 FROM entity_mentions em WHERE em.source_type='conversation' AND em.source_id=c.id)
              AND c.message_count >= 3
        """)
        st.markdown(
            f'<div style="font-size:12px;color:{TEXT_MUTED};margin-bottom:10px;">'
            f'{int(unprocessed.iloc[0]["c"])} conversations pending extraction (≥3 messages)</div>',
            unsafe_allow_html=True,
        )
        if DEMO_MODE:
            _demo_disabled_button("Start entity extractor", key="start_entity_extractor_demo", use_container_width=False)
        elif st.button("Start entity extractor", type="primary"):
            subprocess.Popen(
                [sys.executable, str(SCRIPTS_ROOT / "extract_entities.py")],
                stdout=open("/tmp/extract_entities.log", "w"), stderr=subprocess.STDOUT,
            )
            st.toast("Running in background. Log: /tmp/extract_entities.log")

    st.markdown('<div class="kai-section-label">Search</div>', unsafe_allow_html=True)
    sc1, sc2, sc3 = st.columns([6, 2, 2])
    with sc1:
        kw_query = st.text_input(
            "Keyword(s)",
            placeholder="e.g. pgvector, Michael, VSE NET — comma- or space-separated",
            key="kg_kw_query",
            label_visibility="collapsed",
            help="Filters the graph to entities whose name matches any keyword, plus their direct neighbors.",
        )
    with sc2:
        kw_match_all = st.checkbox(
            "Match all words",
            value=False,
            key="kg_kw_all",
            help="Require every word to appear in the entity name (AND). Default: any match (OR).",
        )
    with sc3:
        kw_include_neighbors = st.checkbox(
            "Include neighbors",
            value=True,
            key="kg_kw_neighbors",
            help="Also include entities directly connected to a match.",
        )

    st.markdown('<div class="kai-section-label">Filters</div>', unsafe_allow_html=True)
    col_f1, col_f2, col_f3, col_f4 = st.columns(4)
    with col_f1:
        types = q("SELECT DISTINCT entity_type FROM entities ORDER BY entity_type")
        sel_type = st.selectbox("Entity type", ["All"] + (types["entity_type"].tolist() if not types.empty else []))
    with col_f2:
        projs = q("SELECT DISTINCT project_name FROM entities WHERE project_name IS NOT NULL ORDER BY project_name")
        sel_proj = st.selectbox("Project", ["All"] + (projs["project_name"].tolist() if not projs.empty else []))
    with col_f3:
        min_mentions = st.slider("Min. mentions", 1, 20, 1)
    with col_f4:
        max_nodes = st.slider("Max. nodes", 10, 400, 60)

    where = ["mention_count >= %s"]
    params_list = [min_mentions]
    if sel_type != "All":
        where.append("entity_type = %s")
        params_list.append(sel_type)
    if sel_proj != "All":
        where.append("project_name = %s")
        params_list.append(sel_proj)

    kw_terms: list[str] = []
    seed_ids: list[int] = []
    neighbor_ids: list[int] = []

    if kw_query and kw_query.strip():
        raw = [t.strip() for t in kw_query.replace(",", " ").split() if t.strip()]
        kw_terms = list(dict.fromkeys(raw))

    if kw_terms:
        joiner = " AND " if kw_match_all else " OR "
        kw_clause = "(" + joiner.join(["name ILIKE %s"] * len(kw_terms)) + ")"
        kw_params = [f"%{t}%" for t in kw_terms]
        seeds_df = q(
            f"SELECT id FROM entities WHERE {kw_clause}",
            kw_params,
        )
        seed_ids = [int(x) for x in seeds_df["id"].tolist()] if not seeds_df.empty else []

        if seed_ids and kw_include_neighbors:
            ids_tuple = f"({seed_ids[0]})" if len(seed_ids) == 1 else str(tuple(seed_ids))
            nbr_df = q(f"""
                SELECT to_entity AS id FROM relationships WHERE from_entity IN {ids_tuple}
                UNION
                SELECT from_entity AS id FROM relationships WHERE to_entity IN {ids_tuple}
            """)
            neighbor_ids = [int(x) for x in nbr_df["id"].tolist()] if not nbr_df.empty else []

        graph_ids = sorted(set(seed_ids) | set(neighbor_ids))
        if not graph_ids:
            st.warning(f"No entities match {kw_terms!r}.")
            ents_df = pd.DataFrame()
        else:
            ids_tuple = f"({graph_ids[0]})" if len(graph_ids) == 1 else str(tuple(graph_ids))
            where.append(f"id IN {ids_tuple}")
            where_sql = "WHERE " + " AND ".join(where)
            with st.spinner(f"Loading {len(graph_ids)} matched entities..."):
                ents_df = q(f"""
                    SELECT id, name, entity_type, project_name, mention_count, confidence, attributes,
                           COALESCE(last_seen, first_seen) AS sort_date
                    FROM entities {where_sql}
                    ORDER BY sort_date DESC NULLS LAST, mention_count DESC LIMIT %s
                """, params_list + [max_nodes])
            st.caption(
                f"Search · {len(seed_ids)} match"
                f"{'es' if len(seed_ids) != 1 else ''} for {kw_terms} "
                f"+ {len(neighbor_ids)} neighbor{'s' if len(neighbor_ids) != 1 else ''}"
            )
    else:
        where_sql = "WHERE " + " AND ".join(where)
        with st.spinner("Loading entities..."):
            ents_df = q(f"""
                SELECT id, name, entity_type, project_name, mention_count, confidence, attributes,
                       COALESCE(last_seen, first_seen) AS sort_date
                FROM entities {where_sql}
                ORDER BY sort_date DESC NULLS LAST, mention_count DESC LIMIT %s
            """, params_list + [max_nodes])

    if ents_df.empty:
        st.info("No entities match these filters. Run the entity extractor to populate.")
        return

    entity_ids = tuple(int(x) for x in ents_df["id"].tolist())
    rel_query_ids = f"({entity_ids[0]})" if len(entity_ids) == 1 else str(entity_ids)
    rels_df = q(f"""
        SELECT r.from_entity, r.to_entity, r.relation_type, r.confidence
        FROM relationships r
        WHERE r.from_entity IN {rel_query_ids} AND r.to_entity IN {rel_query_ids}
    """)

    st.markdown(
        f'<div class="kai-section-label">Graph · {len(ents_df)} nodes · {len(rels_df)} edges</div>',
        unsafe_allow_html=True,
    )

    try:
        from streamlit_agraph import agraph, Node, Edge, Config

        layout_col1, layout_col2, layout_col3 = st.columns([2, 2, 2])
        with layout_col1:
            graph_height = st.slider("Graph height (px)", 600, 1600, 900, 50, key="kg_height")
        with layout_col2:
            node_spacing = st.slider("Node spacing", 100, 600, 280, 20, key="kg_spacing",
                                     help="Higher = more space between nodes")
        with layout_col3:
            label_size = st.slider("Label font size", 10, 24, 14, 1, key="kg_label_size")

        seed_set = set(seed_ids) if kw_terms else set()
        nodes = []
        for _, e in ents_df.iterrows():
            eid = int(e["id"])
            is_seed = eid in seed_set
            size = min(20 + int(e["mention_count"]) * 4, 70)
            if is_seed:
                size = min(size + 12, 90)
            color = ENTITY_COLORS.get(e["entity_type"], TEXT_MUTED)
            label = e["name"][:30] + ("..." if len(e["name"]) > 30 else "")
            title_extra = "\n[search match]" if is_seed else ""
            nodes.append(Node(
                id=str(eid),
                label=label,
                size=size,
                color=color,
                title=(
                    f"{e['name']}\nType: {e['entity_type']}\n"
                    f"Project: {e['project_name'] or '—'}\n"
                    f"Mentions: {e['mention_count']}{title_extra}"
                ),
                shape="dot",
                borderWidth=4 if is_seed else 1,
                borderWidthSelected=5 if is_seed else 2,
                font={
                    "size": label_size + (2 if is_seed else 0),
                    "color": ACCENT if is_seed else TEXT,
                    "face": "Inter, sans-serif",
                    "strokeWidth": 3,
                    "strokeColor": BG,
                    "bold": is_seed,
                },
            ))

        edges = []
        for _, rel in rels_df.iterrows():
            edges.append(Edge(
                source=str(int(rel["from_entity"])),
                target=str(int(rel["to_entity"])),
                label=rel["relation_type"],
                color=BORDER,
                type="CURVE_SMOOTH",
                font={"size": max(10, label_size - 3), "color": TEXT_MUTED, "strokeWidth": 2, "strokeColor": BG, "align": "middle"},
            ))

        config = Config(
            width="100%",
            height=graph_height,
            directed=True,
            physics=True,
            hierarchical=False,
            nodeHighlightBehavior=True,
            highlightColor=ACCENT,
            collapsible=False,
            node={'labelProperty': 'label', 'renderLabel': True},
            link={'labelProperty': 'label', 'renderLabel': True},
            backgroundColor=BG,
            solver="forceAtlas2Based",
            forceAtlas2Based={
                "gravitationalConstant": -120,
                "centralGravity": 0.005,
                "springLength": node_spacing,
                "springConstant": 0.05,
                "damping": 0.6,
                "avoidOverlap": 1,
            },
            stabilization={
                "enabled": True,
                "iterations": 250,
                "updateInterval": 25,
                "fit": True,
            },
            interaction={
                "hover": True,
                "tooltipDelay": 150,
                "zoomView": True,
                "dragView": True,
                "navigationButtons": True,
            },
            minVelocity=0.5,
            maxVelocity=30,
        )

        clicked = agraph(nodes=nodes, edges=edges, config=config)
        if clicked:
            try:
                if isinstance(clicked, str):
                    go_to_detail("entity", int(clicked))
                elif isinstance(clicked, list) and clicked:
                    go_to_detail("entity", int(clicked[0]))
            except Exception:
                pass

        legend_html = " ".join(
            f'<span style="display:inline-flex;align-items:center;gap:6px;margin-right:16px;font-size:12px;color:{TEXT_MUTED};">'
            f'<span style="width:10px;height:10px;border-radius:50%;background:{c};display:inline-block;"></span>{t}'
            f'</span>' for t, c in ENTITY_COLORS.items()
        )
        st.markdown(f'<div style="margin-top:0.75rem;">{legend_html}</div>', unsafe_allow_html=True)

    except ImportError:
        st.warning("streamlit-agraph not installed. Showing table only.")

    st.markdown('<hr/>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="kai-section-label">Entities · {len(ents_df)}</div>',
        unsafe_allow_html=True,
    )
    display_df = ents_df[["id", "entity_type", "name", "project_name", "mention_count", "confidence"]].copy()
    render_export_buttons(
        display_df, key_prefix="kg_entities",
        filename_base="knowledge_graph_entities",
        title="Knowledge Graph — Entities",
    )
    sel = st.dataframe(
        display_df, use_container_width=True, hide_index=True,
        on_select="rerun", selection_mode="single-row",
        column_config={
            "id":            st.column_config.NumberColumn("ID", width="small"),
            "entity_type":   st.column_config.TextColumn("Type", width="small"),
            "name":          st.column_config.TextColumn("Name", width="large"),
            "project_name":  st.column_config.TextColumn("Project", width="small"),
            "mention_count": st.column_config.NumberColumn("Mentions", width="small"),
            "confidence":    st.column_config.NumberColumn("Conf", format="%.2f", width="small"),
        },
        key="df_entities",
    )
    if sel.selection and sel.selection.rows:
        go_to_detail("entity", int(ents_df.iloc[sel.selection.rows[0]]["id"]))
