"""Ingestion page — run the pipeline scripts that populate the database."""

from __future__ import annotations

import subprocess

from gui.page_views import app_ns


def render() -> None:
    app = app_ns()
    st = app.st
    q = app.q
    page_header = app.page_header
    SCRIPTS_ROOT = app.SCRIPTS_ROOT
    TEXT = app.TEXT
    TEXT_MUTED = app.TEXT_MUTED
    TEXT_FAINT = app.TEXT_FAINT
    DEMO_MODE = app.DEMO_MODE
    _demo_disabled_button = app._demo_disabled_button

    page_header("Ingestion", "Run pipeline scripts to populate the database")

    SCRIPTS_DIR = SCRIPTS_ROOT

    r1c1, r1c2 = st.columns(2)
    with r1c1:
        with st.container(border=True):
            counts = q("SELECT count(*) AS c FROM conversations")
            n = int(counts.iloc[0]["c"])
            st.markdown(
                f"""<div style="font-size:11px;color:{TEXT_FAINT};letter-spacing:0.08em;text-transform:uppercase;margin-bottom:6px;">Sessions</div>
                <div style="font-size:28px;font-weight:700;color:{TEXT};line-height:1.1;margin-bottom:4px;">{n:,}</div>
                <div style="font-size:12px;color:{TEXT_MUTED};margin-bottom:14px;">Import JSONL sessions from ~/.claude/projects/</div>""",
                unsafe_allow_html=True,
            )
            if DEMO_MODE:
                _demo_disabled_button("Run session ingestion", key="ing_sess")
            elif st.button("Run session ingestion", key="ing_sess", use_container_width=True, type="primary"):
                with st.spinner("Ingesting sessions..."):
                    r = subprocess.run(
                        ["python3", str(SCRIPTS_DIR / "ingest_sessions.py")],
                        capture_output=True, text=True, timeout=300,
                    )
                    st.code(r.stdout[-3000:] if r.stdout else r.stderr[-3000:])
    with r1c2:
        with st.container(border=True):
            counts = q("SELECT count(*) AS c FROM skills")
            n = int(counts.iloc[0]["c"])
            st.markdown(
                f"""<div style="font-size:11px;color:{TEXT_FAINT};letter-spacing:0.08em;text-transform:uppercase;margin-bottom:6px;">Skills</div>
                <div style="font-size:28px;font-weight:700;color:{TEXT};line-height:1.1;margin-bottom:4px;">{n:,}</div>
                <div style="font-size:12px;color:{TEXT_MUTED};margin-bottom:14px;">Scan SKILL.md files for metadata</div>""",
                unsafe_allow_html=True,
            )
            if DEMO_MODE:
                _demo_disabled_button("Run skill scanner", key="ing_sk")
            elif st.button("Run skill scanner", key="ing_sk", use_container_width=True, type="primary"):
                with st.spinner("Scanning skills..."):
                    r = subprocess.run(
                        ["python3", str(SCRIPTS_DIR / "scan_skills.py")],
                        capture_output=True, text=True, timeout=60,
                    )
                    st.code(r.stdout[-3000:] if r.stdout else r.stderr[-3000:])

    r2c1, r2c2 = st.columns(2)
    with r2c1:
        with st.container(border=True):
            counts = q("SELECT count(*) AS c FROM memory_chunks")
            n = int(counts.iloc[0]["c"])
            unp = q("""SELECT count(*) AS c FROM conversations c WHERE NOT EXISTS (
                SELECT 1 FROM memory_chunks mc WHERE mc.source_type='conversation' AND mc.source_id=c.id
            ) AND c.message_count >= 5""")
            pending = int(unp.iloc[0]["c"])
            st.markdown(
                f"""<div style="font-size:11px;color:{TEXT_FAINT};letter-spacing:0.08em;text-transform:uppercase;margin-bottom:6px;">Memory extraction</div>
                <div style="font-size:28px;font-weight:700;color:{TEXT};line-height:1.1;margin-bottom:4px;">{n:,}</div>
                <div style="font-size:12px;color:{TEXT_MUTED};margin-bottom:14px;">{pending} conversations pending (≥5 msgs)</div>""",
                unsafe_allow_html=True,
            )
            if DEMO_MODE:
                _demo_disabled_button("Run memory extraction", key="ing_mem")
            elif st.button("Run memory extraction", key="ing_mem", use_container_width=True, type="primary"):
                subprocess.Popen(
                    ["python3", str(SCRIPTS_DIR / "extract_memory.py")],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                st.toast("Memory extraction started in background.")
    with r2c2:
        with st.container(border=True):
            nt = q("SELECT count(*) AS c FROM conversations WHERE (summary IS NULL OR summary = '') AND message_count >= 2")
            n = int(nt.iloc[0]["c"])
            st.markdown(
                f"""<div style="font-size:11px;color:{TEXT_FAINT};letter-spacing:0.08em;text-transform:uppercase;margin-bottom:6px;">Titles missing</div>
                <div style="font-size:28px;font-weight:700;color:{TEXT};line-height:1.1;margin-bottom:4px;">{n:,}</div>
                <div style="font-size:12px;color:{TEXT_MUTED};margin-bottom:14px;">Generate titles via Claude CLI (~10s/conv)</div>""",
                unsafe_allow_html=True,
            )
            if DEMO_MODE:
                _demo_disabled_button("Run title generation", key="ing_title")
            elif st.button("Run title generation", key="ing_title", use_container_width=True, type="primary"):
                subprocess.Popen(
                    ["python3", str(SCRIPTS_DIR / "generate_titles.py")],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                st.toast("Title generation started in background.")

    st.markdown('<hr/>', unsafe_allow_html=True)
    st.markdown('<div class="kai-section-label">Ingestion log — last 10</div>', unsafe_allow_html=True)
    log = q("SELECT file_path, record_count, ingested_at FROM ingestion_log ORDER BY ingested_at DESC LIMIT 10")
    if log.empty:
        st.info("No log entries yet.")
    else:
        st.dataframe(log, use_container_width=True, hide_index=True)
