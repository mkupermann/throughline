"""Calendar page — conversations, memory and decisions plotted over time.

Renders a FullCalendar-compatible event stream via streamlit-calendar.
Each toggleable source (Conversations, Memory, Skills, Projects, Prompts,
Entities, Reflections, Ingestion) contributes its own events; click-
through routes back to the relevant detail page.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

import pandas as pd

from gui.page_views import app_ns


def render() -> None:
    app = app_ns()
    st = app.st
    q = app.q
    page_header = app.page_header
    go_to_detail = app.go_to_detail

    from streamlit_calendar import calendar as sc_calendar

    page_header("Calendar", "Conversations, memory and decisions over time")

    col_mode, col_range, col_types = st.columns([2, 2, 3])
    with col_mode:
        view_mode = st.selectbox(
            "View",
            ["dayGridMonth", "timeGridWeek", "timeGridDay", "listWeek"],
            format_func=lambda x: {
                "dayGridMonth": "Month",
                "timeGridWeek": "Week",
                "timeGridDay": "Day",
                "listWeek": "List",
            }[x],
            index=0,
        )
    with col_range:
        date_range = st.selectbox(
            "Range (filter)",
            ["all", "last_90d", "last_30d", "current_month"],
            format_func=lambda x: {
                "current_month": "Current month",
                "last_30d": "Last 30 days",
                "last_90d": "Last 90 days",
                "all": "All",
            }[x],
            index=0,
            help="Loads all events in the selected range. Use calendar navigation to move between months/weeks."
        )
    with col_types:
        c_a, c_b = st.columns(2)
        with c_a:
            show_conv = st.checkbox("Conversations", value=True)
            show_mem = st.checkbox("Memory", value=True)
            show_skills = st.checkbox("Skills (last used)", value=False)
            show_projects = st.checkbox("Projects", value=False)
        with c_b:
            show_prompts = st.checkbox("Prompts", value=False)
            show_entities = st.checkbox("Entities (first seen)", value=False)
            show_reflections = st.checkbox("Reflections", value=False)
            show_ingestion = st.checkbox("Ingestion events", value=False)

    all_cats = ["decision", "pattern", "insight", "preference", "contact", "error_solution", "project_context", "workflow"]
    cat_filter_list = []
    if show_mem:
        cat_filter_list = st.multiselect(
            "Memory categories (empty = all)",
            all_cats,
            default=[],
        )

    if date_range == "current_month":
        now = datetime.now()
        cutoff = now.replace(day=1).strftime("%Y-%m-%d")
    elif date_range == "last_30d":
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    elif date_range == "last_90d":
        cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    else:
        cutoff = None

    where_date_conv = f"c.started_at >= '{cutoff}'::timestamptz" if cutoff else "TRUE"
    event_date_expr = "COALESCE(c.started_at, mc.created_at)"
    where_date_mem = f"{event_date_expr} >= '{cutoff}'::timestamptz" if cutoff else "TRUE"

    events = []

    if show_conv:
        df_conv = q(f"""
            SELECT c.id, c.summary, c.project_name, c.model, c.message_count,
                   c.started_at, c.ended_at
            FROM conversations c
            WHERE {where_date_conv}
            ORDER BY c.started_at
        """)
        for _, r in df_conv.iterrows():
            project = r["project_name"] if isinstance(r["project_name"], str) and r["project_name"] else "unknown"
            project_colors = {
                "notes": "#58A6FF",
                "claude-memory": "#7EE787",
                "wiki": "#FFA657",
                "workspace": "#D2A8FF",
                "data-stack": "#F97583",
                "unknown": "#8B949E",
                "plugins": "#79C0FF",
            }
            color = project_colors.get(project, "#58A6FF")

            title = r["summary"] if isinstance(r["summary"], str) and r["summary"].strip() else f"Session #{r['id']}"
            if len(title) > 60:
                title = title[:57] + "..."

            started = r["started_at"] if r["started_at"] is not None and not pd.isna(r["started_at"]) else None
            ended = r["ended_at"] if r["ended_at"] is not None and not pd.isna(r["ended_at"]) else started
            if not started:
                continue

            try:
                dur_min = (ended - started).total_seconds() / 60 if ended else 0
            except Exception:
                dur_min = 0

            if dur_min < 15:
                start_iso = started.strftime("%Y-%m-%d")
                end_iso = None
                all_day = True
            elif dur_min < 12 * 60:
                start_iso = started.isoformat()
                end_iso = ended.isoformat() if ended else None
                all_day = False
            else:
                start_iso = started.strftime("%Y-%m-%d")
                end_date = ended.date() + timedelta(days=1) if ended else None
                end_iso = end_date.isoformat() if end_date else None
                all_day = True

            events.append({
                "id": f"conv_{r['id']}",
                "title": title,
                "start": start_iso,
                "end": end_iso,
                "allDay": all_day,
                "backgroundColor": color,
                "borderColor": color,
                "textColor": "#0D1117",
                "extendedProps": {
                    "type": "conversation",
                    "entity_id": int(r["id"]),
                    "project": project,
                    "model": r["model"] if isinstance(r["model"], str) and r["model"] else "-",
                    "messages": int(r["message_count"]) if not pd.isna(r["message_count"]) else 0,
                    "duration_min": round(dur_min, 1),
                },
            })

    if show_mem:
        cat_where = ""
        if cat_filter_list:
            cats_sql = ",".join(f"'{c}'" for c in cat_filter_list)
            cat_where = f"AND mc.category::text IN ({cats_sql})"

        df_mem = q(f"""
            SELECT mc.id, mc.content, mc.category::text AS category,
                   mc.confidence, mc.project_name,
                   {event_date_expr} AS event_date,
                   mc.source_type, mc.source_id
            FROM memory_chunks mc
            LEFT JOIN conversations c
              ON mc.source_type = 'conversation' AND mc.source_id = c.id
            WHERE {where_date_mem}
              AND COALESCE(mc.status, 'active') = 'active'
              {cat_where}
            ORDER BY event_date
            LIMIT 1000
        """)
        cat_colors = {
            "decision": "#F97583",
            "pattern": "#D2A8FF",
            "insight": "#7EE787",
            "preference": "#FFA657",
            "contact": "#79C0FF",
            "error_solution": "#FFCA28",
            "project_context": "#58A6FF",
            "workflow": "#B392F0",
        }
        for _, r in df_mem.iterrows():
            content = r["content"] if isinstance(r["content"], str) else ""
            if not content.strip():
                continue
            title = content[:80] + ("..." if len(content) > 80 else "")
            start = r["event_date"].isoformat() if r["event_date"] and not pd.isna(r["event_date"]) else None
            color = cat_colors.get(r["category"], "#8B949E")
            if start:
                events.append({
                    "id": f"mem_{r['id']}",
                    "title": f"[{r['category']}] {title}",
                    "start": start,
                    "backgroundColor": color,
                    "borderColor": color,
                    "textColor": "#0D1117",
                    "extendedProps": {
                        "type": "memory",
                        "entity_id": int(r["id"]),
                        "category": r["category"],
                        "project": r["project_name"] if isinstance(r["project_name"], str) and r["project_name"] else "-",
                        "confidence": float(r["confidence"]) if not pd.isna(r["confidence"]) else 0.0,
                    },
                })

    if show_skills:
        df_sk = q(f"""
            SELECT id, name, description, use_count,
                   COALESCE(file_modified, last_used, created_at) AS event_date,
                   CASE WHEN last_used IS NOT NULL THEN 'used'
                        WHEN file_modified IS NOT NULL THEN 'file'
                        ELSE 'scanned'
                   END AS src_type
            FROM skills
            WHERE COALESCE(file_modified, last_used, created_at) IS NOT NULL
              {f"AND COALESCE(file_modified, last_used, created_at) >= '{cutoff}'::timestamptz" if cutoff else ""}
            ORDER BY event_date
        """)
        for _, r in df_sk.iterrows():
            if pd.isna(r["event_date"]):
                continue
            events.append({
                "id": f"skill_{r['id']}",
                "title": f"Skill: {r['name']}",
                "start": r["event_date"].strftime("%Y-%m-%d"),
                "allDay": True,
                "backgroundColor": "#BC8CFF",
                "borderColor": "#BC8CFF",
                "textColor": "#0D1117",
                "extendedProps": {
                    "type": "skill",
                    "entity_id": int(r["id"]),
                    "use_count": int(r["use_count"]) if not pd.isna(r["use_count"]) else 0,
                    "source": r["src_type"],
                },
            })

    if show_projects:
        df_pr = q(f"""
            SELECT id, name, description, status::text AS status, created_at
            FROM projects
            WHERE created_at IS NOT NULL
              {f"AND created_at >= '{cutoff}'::timestamptz" if cutoff else ""}
        """)
        status_colors = {"active": "#7EE787", "paused": "#FFA657", "completed": "#58A6FF", "archived": "#8B949E"}
        for _, r in df_pr.iterrows():
            if pd.isna(r["created_at"]):
                continue
            color = status_colors.get(r["status"], "#7EE787")
            events.append({
                "id": f"proj_{r['id']}",
                "title": f"Project: {r['name']}",
                "start": r["created_at"].strftime("%Y-%m-%d"),
                "allDay": True,
                "backgroundColor": color,
                "borderColor": color,
                "textColor": "#0D1117",
                "extendedProps": {
                    "type": "project",
                    "entity_id": int(r["id"]),
                    "status": r["status"],
                },
            })

    if show_prompts:
        df_pt = q(f"""
            SELECT id, name, category, created_at
            FROM prompts
            WHERE created_at IS NOT NULL
              {f"AND created_at >= '{cutoff}'::timestamptz" if cutoff else ""}
        """)
        for _, r in df_pt.iterrows():
            if pd.isna(r["created_at"]):
                continue
            events.append({
                "id": f"prompt_{r['id']}",
                "title": f"Prompt: {r['name']}",
                "start": r["created_at"].strftime("%Y-%m-%d"),
                "allDay": True,
                "backgroundColor": "#FF7B72",
                "borderColor": "#FF7B72",
                "textColor": "#0D1117",
                "extendedProps": {
                    "type": "prompt",
                    "entity_id": int(r["id"]),
                    "category": r["category"] if isinstance(r["category"], str) and r["category"] else "-",
                },
            })

    if show_entities:
        try:
            df_ent = q(f"""
                SELECT id, name, entity_type, mention_count, first_seen
                FROM entities
                WHERE first_seen IS NOT NULL
                  {f"AND first_seen >= '{cutoff}'::timestamptz" if cutoff else ""}
                ORDER BY mention_count DESC
                LIMIT 300
            """)
            ent_colors = {
                "person": "#79C0FF",
                "project": "#7EE787",
                "technology": "#D2A8FF",
                "decision": "#F97583",
                "concept": "#FFA657",
                "organization": "#FFCA28",
            }
            for _, r in df_ent.iterrows():
                if pd.isna(r["first_seen"]):
                    continue
                color = ent_colors.get(r["entity_type"], "#8B949E")
                events.append({
                    "id": f"ent_{r['id']}",
                    "title": f"{r['entity_type']}: {r['name']}",
                    "start": r["first_seen"].strftime("%Y-%m-%d"),
                    "allDay": True,
                    "backgroundColor": color,
                    "borderColor": color,
                    "textColor": "#0D1117",
                    "extendedProps": {
                        "type": "entity",
                        "entity_id": int(r["id"]),
                        "entity_type": r["entity_type"],
                        "mentions": int(r["mention_count"]) if not pd.isna(r["mention_count"]) else 0,
                    },
                })
        except Exception:
            pass

    if show_reflections:
        try:
            df_ref = q(f"""
                SELECT id, reflection_type, action_taken, reasoning, created_at
                FROM memory_reflections
                WHERE created_at IS NOT NULL
                  {f"AND created_at >= '{cutoff}'::timestamptz" if cutoff else ""}
                ORDER BY created_at
            """)
            for _, r in df_ref.iterrows():
                if pd.isna(r["created_at"]):
                    continue
                events.append({
                    "id": f"ref_{r['id']}",
                    "title": f"{r['reflection_type']}: {r['action_taken']}",
                    "start": r["created_at"].isoformat(),
                    "allDay": False,
                    "backgroundColor": "#F778BA",
                    "borderColor": "#F778BA",
                    "textColor": "#0D1117",
                    "extendedProps": {
                        "type": "reflection",
                        "entity_id": int(r["id"]),
                    },
                })
        except Exception:
            pass

    if show_ingestion:
        df_ing = q(f"""
            SELECT id, file_path, record_count, ingested_at
            FROM ingestion_log
            WHERE ingested_at IS NOT NULL
              {f"AND ingested_at >= '{cutoff}'::timestamptz" if cutoff else ""}
            ORDER BY ingested_at
        """)
        for _, r in df_ing.iterrows():
            if pd.isna(r["ingested_at"]):
                continue
            fname = str(r["file_path"]).split("/")[-1] if isinstance(r["file_path"], str) and r["file_path"] else "-"
            rec_count = int(r["record_count"]) if not pd.isna(r["record_count"]) else 0
            events.append({
                "id": f"ing_{r['id']}",
                "title": f"Ingest: {fname} ({rec_count} rec)",
                "start": r["ingested_at"].isoformat(),
                "allDay": False,
                "backgroundColor": "#8B949E",
                "borderColor": "#8B949E",
                "textColor": "#0D1117",
                "extendedProps": {
                    "type": "ingestion",
                    "entity_id": int(r["id"]),
                },
            })

    initial_date = None
    if events:
        dates = sorted([e.get("start", "") for e in events if e.get("start")], reverse=True)
        if dates:
            initial_date = dates[0][:10]

    calendar_options = {
        "initialView": view_mode,
        "initialDate": initial_date or datetime.now().strftime("%Y-%m-%d"),
        "headerToolbar": {
            "left": "prev,next today",
            "center": "title",
            "right": "dayGridMonth,timeGridWeek,timeGridDay,listWeek",
        },
        "slotMinTime": "06:00:00",
        "slotMaxTime": "22:00:00",
        "locale": "en",
        "firstDay": 1,
        "nowIndicator": True,
        "navLinks": True,
        "weekNumbers": True,
        "dayMaxEvents": True,
        "height": 800,
        "buttonText": {
            "today": "Today",
            "month": "Month",
            "week": "Week",
            "day": "Day",
            "list": "List",
        },
        "allDayText": "All-day",
    }

    custom_css = """
        .fc {
            background-color: #0D1117;
            color: #C9D1D9;
            font-family: -apple-system, BlinkMacSystemFont, 'Inter', sans-serif;
        }
        .fc-theme-standard td, .fc-theme-standard th,
        .fc-theme-standard .fc-scrollgrid {
            border-color: #30363D;
        }
        .fc-col-header-cell-cushion,
        .fc-daygrid-day-number,
        .fc-list-day-cushion {
            color: #C9D1D9;
        }
        .fc-day-today {
            background-color: rgba(88, 166, 255, 0.08) !important;
        }
        .fc-button {
            background-color: #161B22 !important;
            border-color: #30363D !important;
            color: #C9D1D9 !important;
        }
        .fc-button:hover {
            background-color: #21262D !important;
        }
        .fc-button-active {
            background-color: #58A6FF !important;
            color: #0D1117 !important;
        }
        .fc-event {
            cursor: pointer;
            border-radius: 4px;
            padding: 2px 4px;
            font-size: 0.82rem;
        }
        .fc-list-event-title {
            color: #C9D1D9;
        }
        .fc-list-day-side-text,
        .fc-list-day-text {
            color: #C9D1D9;
        }
    """

    breakdown = Counter(e.get("extendedProps", {}).get("type", "unknown") for e in events)
    summary = ", ".join(f"{v} {k}" for k, v in breakdown.most_common())
    st.markdown(f"**{len(events)} events** — {summary or '(none)'}")

    empty_categories = []
    if show_skills and breakdown.get("skill", 0) == 0:
        empty_categories.append("Skills")
    if show_projects and breakdown.get("project", 0) == 0:
        empty_categories.append("Projects (table is empty)")
    if show_prompts and breakdown.get("prompt", 0) == 0:
        empty_categories.append("Prompts (table is empty)")
    if show_entities and breakdown.get("entity", 0) == 0:
        empty_categories.append("Entities")
    if show_reflections and breakdown.get("reflection", 0) == 0:
        empty_categories.append("Reflections")
    if show_ingestion and breakdown.get("ingestion", 0) == 0:
        empty_categories.append("Ingestion")
    if empty_categories:
        st.warning("No events for selected categories: " + ", ".join(empty_categories)
                   + ". Run the corresponding ingestion/scan scripts on the Ingestion page.")

    def _scrub_nan(obj):
        if isinstance(obj, float) and pd.isna(obj):
            return None
        if isinstance(obj, dict):
            return {k: _scrub_nan(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_scrub_nan(v) for v in obj]
        return obj

    events = _scrub_nan(events)

    result = sc_calendar(
        events=events,
        options=calendar_options,
        custom_css=custom_css,
        key="memory_calendar",
    )

    if result and result.get("callback") == "eventClick":
        ev = result.get("eventClick", {}).get("event", {})
        ext = ev.get("extendedProps", {})
        etype = ext.get("type")
        eid = ext.get("entity_id")
        if eid:
            if etype in ("conversation", "memory", "skill", "project", "prompt", "entity"):
                go_to_detail(etype, int(eid))

    st.markdown("---")
    lc1, lc2, lc3 = st.columns(3)
    with lc1:
        st.markdown("**Project colors (conversations)**")
        st.markdown(
            """
            <div style="display:flex;flex-wrap:wrap;gap:8px;">
              <span style="background:#58A6FF;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">notes</span>
              <span style="background:#7EE787;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">claude-memory</span>
              <span style="background:#FFA657;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">wiki</span>
              <span style="background:#D2A8FF;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">workspace</span>
              <span style="background:#F97583;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">data-stack</span>
              <span style="background:#79C0FF;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">plugins</span>
              <span style="background:#8B949E;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">other</span>
            </div>
            """, unsafe_allow_html=True
        )
    with lc2:
        st.markdown("**Memory categories**")
        st.markdown(
            """
            <div style="display:flex;flex-wrap:wrap;gap:8px;">
              <span style="background:#F97583;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">decision</span>
              <span style="background:#D2A8FF;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">pattern</span>
              <span style="background:#7EE787;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">insight</span>
              <span style="background:#FFA657;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">preference</span>
              <span style="background:#79C0FF;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">contact</span>
              <span style="background:#FFCA28;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">error_solution</span>
              <span style="background:#58A6FF;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">project_context</span>
              <span style="background:#B392F0;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">workflow</span>
            </div>
            """, unsafe_allow_html=True
        )
    with lc3:
        st.markdown("**Other sources**")
        st.markdown(
            """
            <div style="display:flex;flex-wrap:wrap;gap:8px;">
              <span style="background:#BC8CFF;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">skill</span>
              <span style="background:#7EE787;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">project</span>
              <span style="background:#FF7B72;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">prompt</span>
              <span style="background:#79C0FF;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">entity</span>
              <span style="background:#F778BA;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">reflection</span>
              <span style="background:#8B949E;color:#0D1117;padding:2px 8px;border-radius:4px;font-size:0.8rem;">ingestion</span>
            </div>
            """, unsafe_allow_html=True
        )
