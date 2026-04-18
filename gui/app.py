#!/usr/bin/env python3
"""Claude Memory — Premium Streamlit GUI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import psycopg2
import psycopg2.extras
import streamlit as st

# ── Load local semantic helper ────────────────────────────────────────────────
_GUI_DIR = os.path.dirname(os.path.abspath(__file__))
if _GUI_DIR not in sys.path:
    sys.path.insert(0, _GUI_DIR)
try:
    import semantic_helper  # noqa: E402
except Exception:
    semantic_helper = None

# ── Project paths (resolved relative to this file) ────────────────────────────
# Allow env override for unusual deployments; fall back to repo layout.
_DEFAULT_PROJECT_ROOT = Path(_GUI_DIR).resolve().parent
PROJECT_ROOT = Path(os.environ.get("CLAUDE_MEMORY_ROOT", str(_DEFAULT_PROJECT_ROOT))).resolve()
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Claude Memory",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Design Tokens ─────────────────────────────────────────────────────────────
BG           = "#0D1117"
BG_ELEVATED  = "#161B22"
BG_HOVER     = "#1C2128"
BORDER       = "#30363D"
BORDER_MUTED = "#21262D"
TEXT         = "#C9D1D9"
TEXT_MUTED   = "#8B949E"
TEXT_FAINT   = "#6E7681"
ACCENT       = "#58A6FF"
ACCENT_HOVER = "#79B8FF"
SUCCESS      = "#7EE787"
WARNING      = "#D29922"
DANGER       = "#F85149"
PURPLE       = "#BC8CFF"
ORANGE       = "#FFA657"
PINK         = "#FF7B72"
TEAL         = "#56D4DD"

CATEGORY_COLORS = {
    "decision":        "#58A6FF",
    "pattern":         "#BC8CFF",
    "insight":         "#7EE787",
    "preference":      "#FFA657",
    "contact":         "#FF7B72",
    "error_solution":  "#F85149",
    "project_context": "#79C0FF",
    "workflow":        "#D2A8FF",
}

STATUS_COLORS = {
    "active":     SUCCESS,
    "paused":     WARNING,
    "completed":  ACCENT,
    "archived":   TEXT_FAINT,
    "superseded": WARNING,
    "merged":     PURPLE,
    "stale":      TEXT_FAINT,
}

ENTITY_COLORS = {
    "person":       "#FF7B72",
    "project":      "#56D4DD",
    "technology":   "#7EE787",
    "decision":     "#D29922",
    "concept":      "#BC8CFF",
    "organization": "#FFA657",
}

CATS = ["decision", "pattern", "insight", "preference", "contact", "error_solution", "project_context", "workflow"]


# ══════════════════════════════════════════════════════════════════════════════
# CSS — Premium Dark Theme
# ══════════════════════════════════════════════════════════════════════════════
def inject_css() -> None:
    st.markdown(f"""
    <style>
    @import url('https://rsms.me/inter/inter.css');

    html, body, [class*="css"], [data-testid="stAppViewContainer"] {{
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
        -webkit-font-smoothing: antialiased;
        font-feature-settings: "cv02","cv03","cv04","cv11";
    }}

    [data-testid="stAppViewContainer"] {{ background: {BG}; }}

    .main .block-container {{
        padding-top: 2.5rem;
        padding-bottom: 4rem;
        max-width: 1400px;
    }}

    h1 {{
        font-size: 28px !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em !important;
        color: {TEXT} !important;
        margin-bottom: 0.5rem !important;
        padding-top: 0 !important;
    }}
    h2 {{
        font-size: 20px !important;
        font-weight: 600 !important;
        letter-spacing: -0.01em !important;
        color: {TEXT} !important;
        margin-top: 1.5rem !important;
        margin-bottom: 0.75rem !important;
    }}
    h3 {{
        font-size: 16px !important;
        font-weight: 600 !important;
        color: {TEXT} !important;
    }}

    [data-testid="stCaptionContainer"], .stCaption, small {{
        font-size: 12px !important;
        color: {TEXT_MUTED} !important;
        letter-spacing: 0.01em;
    }}

    [data-testid="stSidebar"] {{
        background: {BG_ELEVATED};
        border-right: 1px solid {BORDER_MUTED};
    }}
    [data-testid="stSidebar"] .block-container {{ padding-top: 1.5rem; }}
    [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {{
        font-size: 11px !important;
        font-weight: 600 !important;
        letter-spacing: 0.08em !important;
        text-transform: uppercase;
        color: {TEXT_FAINT} !important;
    }}

    [data-testid="stSidebar"] [role="radiogroup"] {{ gap: 2px; }}
    [data-testid="stSidebar"] [role="radiogroup"] > label {{
        padding: 8px 12px;
        border-radius: 6px;
        border: 1px solid transparent;
        cursor: pointer;
        transition: all 0.15s ease;
    }}
    [data-testid="stSidebar"] [role="radiogroup"] > label:hover {{ background: {BG_HOVER}; }}
    [data-testid="stSidebar"] [role="radiogroup"] > label[data-checked="true"] {{
        background: rgba(88, 166, 255, 0.1);
        border: 1px solid rgba(88, 166, 255, 0.3);
    }}
    [data-testid="stSidebar"] [role="radiogroup"] > label p {{
        font-size: 13px !important;
        font-weight: 500 !important;
    }}

    [data-testid="stBaseButton-primary"], .stButton > button {{
        font-family: 'Inter', sans-serif !important;
        font-size: 13px !important;
        font-weight: 500 !important;
        border-radius: 6px !important;
        border: 1px solid {BORDER} !important;
        background: {BG_ELEVATED} !important;
        color: {TEXT} !important;
        padding: 6px 14px !important;
        transition: all 0.15s ease !important;
        letter-spacing: 0.01em;
        box-shadow: none !important;
    }}
    .stButton > button:hover {{
        background: {BG_HOVER} !important;
        border-color: {TEXT_FAINT} !important;
        color: {TEXT} !important;
    }}
    [data-testid="stBaseButton-primary"] {{
        background: {ACCENT} !important;
        border-color: {ACCENT} !important;
        color: #0D1117 !important;
        font-weight: 600 !important;
    }}
    [data-testid="stBaseButton-primary"]:hover {{
        background: {ACCENT_HOVER} !important;
        border-color: {ACCENT_HOVER} !important;
    }}

    .stTextInput > div > div > input,
    .stTextArea > div > div > textarea,
    .stSelectbox > div > div,
    .stNumberInput > div > div > input {{
        background: {BG_ELEVATED} !important;
        border: 1px solid {BORDER} !important;
        color: {TEXT} !important;
        border-radius: 6px !important;
        font-family: 'Inter', sans-serif !important;
        font-size: 13px !important;
        transition: border-color 0.15s ease;
    }}
    .stTextInput > div > div > input:focus,
    .stTextArea > div > div > textarea:focus {{
        border-color: {ACCENT} !important;
        box-shadow: 0 0 0 3px rgba(88, 166, 255, 0.15) !important;
    }}
    .stTextArea > div > div > textarea {{
        font-family: 'SF Mono', Monaco, Consolas, 'Liberation Mono', monospace !important;
        font-size: 12.5px !important;
    }}

    div[data-testid="stVerticalBlockBorderWrapper"] {{
        background: {BG_ELEVATED};
        border: 1px solid {BORDER_MUTED} !important;
        border-radius: 8px !important;
        padding: 1rem !important;
        transition: border-color 0.15s ease;
    }}
    div[data-testid="stVerticalBlockBorderWrapper"]:hover {{ border-color: {BORDER} !important; }}

    [data-testid="stMetric"] {{
        background: {BG_ELEVATED};
        border: 1px solid {BORDER_MUTED};
        border-radius: 8px;
        padding: 1.25rem 1.5rem;
        transition: all 0.15s ease;
    }}
    [data-testid="stMetric"]:hover {{ border-color: {BORDER}; }}
    [data-testid="stMetricLabel"] {{
        font-size: 12px !important;
        font-weight: 500 !important;
        color: {TEXT_MUTED} !important;
        letter-spacing: 0.03em;
        text-transform: uppercase;
    }}
    [data-testid="stMetricValue"] {{
        font-size: 32px !important;
        font-weight: 700 !important;
        color: {TEXT} !important;
        letter-spacing: -0.02em;
        line-height: 1.1;
    }}

    [data-testid="stDataFrame"] {{
        border: 1px solid {BORDER_MUTED};
        border-radius: 8px;
        overflow: hidden;
    }}
    [data-testid="stDataFrame"] [role="columnheader"] {{
        background: {BG_ELEVATED} !important;
        color: {TEXT_MUTED} !important;
        font-size: 11px !important;
        font-weight: 600 !important;
        letter-spacing: 0.05em;
        text-transform: uppercase;
    }}

    [data-testid="stExpander"] {{
        background: {BG_ELEVATED};
        border: 1px solid {BORDER_MUTED} !important;
        border-radius: 8px !important;
    }}
    [data-testid="stExpander"] summary {{
        font-size: 13px !important;
        font-weight: 500 !important;
        color: {TEXT} !important;
    }}
    [data-testid="stExpander"] summary:hover {{ color: {ACCENT} !important; }}

    pre, code {{
        font-family: 'SF Mono', Monaco, Consolas, 'Liberation Mono', monospace !important;
        font-size: 12.5px !important;
    }}
    .stCodeBlock, pre {{
        background: #010409 !important;
        border: 1px solid {BORDER_MUTED} !important;
        border-radius: 8px !important;
    }}

    [data-testid="stAlert"] {{
        border-radius: 8px;
        border: 1px solid {BORDER_MUTED};
        font-size: 13px;
    }}

    [data-testid="stChatMessage"] {{
        background: {BG_ELEVATED};
        border: 1px solid {BORDER_MUTED};
        border-radius: 8px;
        padding: 1rem;
    }}

    .kai-brand {{
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 0 0 1.25rem 0;
        margin-bottom: 1rem;
        border-bottom: 1px solid {BORDER_MUTED};
    }}
    .kai-brand-mark {{
        width: 32px; height: 32px;
        border-radius: 7px;
        background: linear-gradient(135deg, {ACCENT} 0%, {PURPLE} 100%);
        display: flex; align-items: center; justify-content: center;
        font-family: 'SF Mono', monospace;
        font-weight: 700;
        font-size: 15px;
        color: #0D1117;
        letter-spacing: -0.03em;
    }}
    .kai-brand-text {{ display: flex; flex-direction: column; line-height: 1.15; }}
    .kai-brand-title {{
        font-size: 14px;
        font-weight: 600;
        color: {TEXT};
        letter-spacing: -0.01em;
    }}
    .kai-brand-sub {{
        font-size: 11px;
        color: {TEXT_FAINT};
        font-weight: 500;
        letter-spacing: 0.02em;
    }}

    .kai-health {{
        display: flex; align-items: center; gap: 8px;
        padding: 10px 12px;
        background: {BG};
        border: 1px solid {BORDER_MUTED};
        border-radius: 6px;
        font-size: 11px;
        color: {TEXT_MUTED};
        letter-spacing: 0.01em;
    }}
    .kai-dot {{
        width: 8px; height: 8px; border-radius: 50%;
        flex-shrink: 0;
    }}
    .kai-dot-ok  {{ background: {SUCCESS}; box-shadow: 0 0 8px rgba(126, 231, 135, 0.5); }}
    .kai-dot-err {{ background: {DANGER};  box-shadow: 0 0 8px rgba(248, 81, 73, 0.5); }}

    .kai-header {{
        display: flex;
        align-items: flex-end;
        justify-content: space-between;
        padding-bottom: 1.5rem;
        margin-bottom: 1.5rem;
        border-bottom: 1px solid {BORDER_MUTED};
    }}
    .kai-subtitle {{
        font-size: 14px;
        color: {TEXT_MUTED};
        font-weight: 400;
        margin-top: 0.35rem;
        letter-spacing: 0.01em;
    }}

    .kai-breadcrumbs {{
        display: flex; align-items: center; gap: 8px;
        font-size: 12px;
        color: {TEXT_MUTED};
        letter-spacing: 0.02em;
        margin-bottom: 0.75rem;
    }}
    .kai-breadcrumbs .sep {{ color: {TEXT_FAINT}; }}

    .kai-chip {{
        display: inline-block;
        padding: 2px 8px;
        margin: 2px 4px 2px 0;
        border-radius: 4px;
        font-size: 11px;
        font-weight: 500;
        background: {BG_HOVER};
        color: {TEXT_MUTED};
        border: 1px solid {BORDER_MUTED};
        letter-spacing: 0.01em;
    }}

    .kai-section-label {{
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: {TEXT_FAINT};
        margin-bottom: 0.75rem;
        margin-top: 0.5rem;
    }}

    .kai-list-card {{
        padding: 14px 16px;
        background: {BG_ELEVATED};
        border: 1px solid {BORDER_MUTED};
        border-radius: 8px;
        margin-bottom: 8px;
        transition: all 0.15s ease;
    }}
    .kai-list-card:hover {{
        border-color: {BORDER};
        background: {BG_HOVER};
    }}
    .kai-list-title {{
        font-size: 14px;
        font-weight: 500;
        color: {TEXT};
        margin-bottom: 4px;
        line-height: 1.4;
    }}
    .kai-list-meta {{
        font-size: 11.5px;
        color: {TEXT_MUTED};
        letter-spacing: 0.01em;
    }}

    .kai-status-dot {{
        display: inline-block;
        width: 8px; height: 8px;
        border-radius: 50%;
        margin-right: 6px;
        vertical-align: middle;
    }}

    hr {{
        border: none !important;
        border-top: 1px solid {BORDER_MUTED} !important;
        margin: 1.5rem 0 !important;
    }}

    #MainMenu, footer {{ visibility: hidden; }}
    [data-testid="stDecoration"] {{ display: none; }}

    [data-testid="stSlider"] > div > div > div > div {{ background: {ACCENT}; }}

    [data-testid="stForm"] {{
        border: 1px solid {BORDER_MUTED};
        border-radius: 8px;
        padding: 1rem 1.25rem;
        background: {BG_ELEVATED};
    }}

    .stTabs [data-baseweb="tab-list"] {{
        gap: 4px;
        border-bottom: 1px solid {BORDER_MUTED};
    }}
    .stTabs [data-baseweb="tab"] {{
        font-size: 13px !important;
        font-weight: 500 !important;
        color: {TEXT_MUTED} !important;
        padding: 10px 14px !important;
    }}
    .stTabs [aria-selected="true"] {{ color: {TEXT} !important; }}
    </style>
    """, unsafe_allow_html=True)


inject_css()


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════
def open_in_finder(path: str) -> bool:
    if not path:
        return False
    p = Path(path)
    if p.exists():
        if p.is_file():
            subprocess.run(["open", "-R", str(p)])
        else:
            subprocess.run(["open", str(p)])
        return True
    return False


@st.cache_resource
def get_conn():
    host = os.environ.get("PGHOST", "localhost")
    port = int(os.environ.get("PGPORT", "5432"))
    dbname = os.environ.get("PGDATABASE", "claude_memory")
    user = os.environ.get("PGUSER", os.environ.get("USER", "postgres"))
    try:
        return psycopg2.connect(host=host, port=port, dbname=dbname, user=user)
    except psycopg2.OperationalError as e:
        st.error(
            f"Cannot connect to PostgreSQL at {host}:{port}/{dbname}. "
            f"Is it running? Try: `docker compose up -d` "
            f"(or `brew services start postgresql@16`).\n\n"
            f"Underlying error: {e}"
        )
        st.stop()
        raise


def _live_conn():
    """Immer garantiert lebende Connection — reconnectet bei Bedarf."""
    conn = get_conn()
    try:
        # Schneller Health-Check
        with conn.cursor() as c:
            c.execute("SELECT 1")
        return conn
    except Exception:
        # Tote Connection → Cache leeren, neu bauen
        try:
            conn.close()
        except Exception:
            pass
        get_conn.clear()
        return get_conn()


def q(sql: str, params=None) -> pd.DataFrame:
    last_err = None
    for attempt in range(2):
        conn = _live_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return pd.DataFrame(cur.fetchall())
        except (psycopg2.InterfaceError, psycopg2.OperationalError) as e:
            last_err = e
            try:
                conn.close()
            except Exception:
                pass
            get_conn.clear()
            continue
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
    raise last_err


def dml(sql: str, params=None) -> str:
    last_err = None
    for attempt in range(2):
        conn = _live_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                n = cur.rowcount
            conn.commit()
            return f"OK — {n} row(s)"
        except (psycopg2.InterfaceError, psycopg2.OperationalError) as e:
            last_err = e
            try:
                conn.close()
            except Exception:
                pass
            get_conn.clear()
            continue
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            return f"Error: {e}"
    return f"Error: {last_err}"


def db_healthy() -> bool:
    try:
        q("SELECT 1 AS ok")
        return True
    except Exception:
        return False


def fmt_dt(x, compact: bool = False) -> str:
    if x is None:
        return "—"
    try:
        if isinstance(x, float) and pd.isna(x):
            return "—"
    except Exception:
        pass
    try:
        s = str(x)
        return s[:10] if compact else s[:16]
    except Exception:
        return "—"


def badge(text: str, color: str = ACCENT) -> str:
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:99px;'
            f'font-size:10.5px;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;'
            f'background:{color}22;color:{color};border:1px solid {color}40;">{text}</span>')


def chip(text: str) -> str:
    return f'<span class="kai-chip">{text}</span>'


def status_dot(color: str) -> str:
    return f'<span class="kai-status-dot" style="background:{color};box-shadow:0 0 6px {color}80;"></span>'


def breadcrumbs(*items: str) -> None:
    parts = []
    for i, it in enumerate(items):
        if i > 0:
            parts.append('<span class="sep">/</span>')
        parts.append(f'<span>{it}</span>')
    st.markdown(f'<div class="kai-breadcrumbs">{" ".join(parts)}</div>', unsafe_allow_html=True)


def page_header(title: str, subtitle: str | None = None) -> None:
    sub = f'<div class="kai-subtitle">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<div class="kai-header"><div><h1 style="margin:0;">{title}</h1>{sub}</div></div>',
        unsafe_allow_html=True,
    )


def plotly_dark_layout(fig: go.Figure, height: int = 300) -> go.Figure:
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, sans-serif", size=12, color=TEXT),
        margin=dict(l=10, r=10, t=10, b=10),
        height=height,
        hoverlabel=dict(
            bgcolor=BG_ELEVATED,
            bordercolor=BORDER,
            font=dict(family="Inter", size=12, color=TEXT),
        ),
        xaxis=dict(gridcolor=BORDER_MUTED, linecolor=BORDER_MUTED,
                   tickfont=dict(color=TEXT_MUTED, size=11), zeroline=False),
        yaxis=dict(gridcolor=BORDER_MUTED, linecolor=BORDER_MUTED,
                   tickfont=dict(color=TEXT_MUTED, size=11), zeroline=False),
        showlegend=False,
    )
    return fig


# ══════════════════════════════════════════════════════════════════════════════
# ROUTING
# ══════════════════════════════════════════════════════════════════════════════
query_params = st.query_params
detail_type = query_params.get("type")
detail_id = query_params.get("id")


def go_to_detail(entity_type: str, entity_id) -> None:
    st.query_params.update({"type": entity_type, "id": str(entity_id)})
    st.rerun()


def go_back() -> None:
    st.query_params.clear()
    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
NAV_LABELS = [
    "Dashboard",
    "Calendar",
    "Search",
    "Semantic",
    "Conversations",
    "Memory",
    "Memory Health",
    "Skills",
    "Knowledge Graph",
    "Projects",
    "Prompts",
    "Scheduler",
    "Ingestion",
    "SQL",
]

with st.sidebar:
    st.markdown(
        """
        <div class="kai-brand">
          <div class="kai-brand-mark">C</div>
          <div class="kai-brand-text">
            <div class="kai-brand-title">Claude Memory</div>
            <div class="kai-brand-sub">Knowledge Base</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if detail_type:
        if st.button("Back to overview", use_container_width=True):
            go_back()
        page = None
    else:
        page = st.radio("nav", NAV_LABELS, label_visibility="collapsed")

    st.markdown('<div style="height:1.25rem"></div>', unsafe_allow_html=True)
    healthy = db_healthy()
    if healthy:
        db_label = os.environ.get("PGDATABASE", "claude_memory")
        st.markdown(
            f"""<div class="kai-health">
                <span class="kai-dot kai-dot-ok"></span>
                <span>PostgreSQL · {db_label}</span>
            </div>""",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """<div class="kai-health">
                <span class="kai-dot kai-dot-err"></span>
                <span>DB unreachable</span>
            </div>""",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# DETAIL VIEWS
# ══════════════════════════════════════════════════════════════════════════════
if detail_type == "conversation":
    conv_id = int(detail_id)
    conv = q("SELECT * FROM conversations WHERE id = %s", (conv_id,))
    if conv.empty:
        st.error("Conversation not found.")
    else:
        c = conv.iloc[0]
        title_text = c["summary"] if c["summary"] else f"Conversation #{conv_id}"
        breadcrumbs("Conversations", f"#{conv_id}")

        head_l, head_r = st.columns([8, 2])
        with head_l:
            st.markdown(f"<h1>{title_text}</h1>", unsafe_allow_html=True)
            st.markdown(
                f'<div class="kai-subtitle">ID #{conv_id} · {c["project_name"] or "no project"} · {c["model"] or "—"}</div>',
                unsafe_allow_html=True,
            )
        with head_r:
            del_col, regen_col = st.columns(2)
            with del_col:
                if st.button("Delete", key=f"del_c_{conv_id}", use_container_width=True):
                    dml("DELETE FROM conversations WHERE id = %s", (conv_id,))
                    go_back()
            with regen_col:
                if st.button("Regen title", key=f"regen_{conv_id}", use_container_width=True):
                    dml("UPDATE conversations SET summary = NULL WHERE id = %s", (conv_id,))
                    subprocess.Popen(
                        [sys.executable, str(SCRIPTS_ROOT / "generate_titles.py")],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                    st.toast("Title regeneration started.")

        st.markdown('<hr/>', unsafe_allow_html=True)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Messages", int(c["message_count"] or 0))
        m2.metric("Tokens in", int(c["token_count_in"] or 0))
        m3.metric("Tokens out", int(c["token_count_out"] or 0))
        m4.metric("Started", fmt_dt(c["started_at"]))

        with st.expander("Edit title"):
            new_title = st.text_input("Summary", value=c["summary"] or "", key=f"title_{conv_id}")
            if st.button("Save title", key=f"savet_{conv_id}", type="primary"):
                dml("UPDATE conversations SET summary = %s WHERE id = %s", (new_title, conv_id))
                st.toast("Title updated")
                st.rerun()

        if c["project_path"] and c["project_path"] != "/":
            pcol, bcol = st.columns([5, 1])
            with pcol:
                st.markdown('<div class="kai-section-label">Project path</div>', unsafe_allow_html=True)
                st.code(c["project_path"], language=None)
            with bcol:
                st.markdown('<div style="height:1.9rem"></div>', unsafe_allow_html=True)
                if st.button("Open in Finder", key=f"finder_{conv_id}", use_container_width=True):
                    if open_in_finder(c["project_path"]):
                        st.toast("Opened in Finder")
                    else:
                        st.toast("Path not found")

        with st.expander("Metadata"):
            st.json({
                "session_id": str(c["session_id"]),
                "project_path": c["project_path"],
                "entrypoint": c["entrypoint"],
                "git_branch": c["git_branch"],
                "started_at": str(c["started_at"]),
                "ended_at": str(c["ended_at"]),
                "cost_usd": float(c["cost_usd"]) if c["cost_usd"] else None,
                "tags": list(c["tags"]) if c["tags"] is not None else [],
            })

        chunks = q(
            "SELECT id, category::text, content, tags, confidence "
            "FROM memory_chunks WHERE source_type='conversation' AND source_id = %s",
            (conv_id,),
        )
        if not chunks.empty:
            st.markdown(
                f'<div class="kai-section-label">Extracted memory chunks — {len(chunks)}</div>',
                unsafe_allow_html=True,
            )
            for _, mc in chunks.iterrows():
                cat = mc["category"]
                color = CATEGORY_COLORS.get(cat, ACCENT)
                tags_html = " ".join(chip(t) for t in (mc["tags"] or []))
                st.markdown(
                    f"""<div class="kai-list-card">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                            <div>{badge(cat, color)}</div>
                            <div style="font-size:11px;color:{TEXT_FAINT}">conf {mc['confidence']:.2f}</div>
                        </div>
                        <div style="font-size:13px;color:{TEXT};line-height:1.55;margin-bottom:8px;">{mc['content']}</div>
                        <div>{tags_html}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )

        col_head, col_order = st.columns([4, 1])
        with col_head:
            st.markdown(
                f'<div class="kai-section-label">Messages — {c["message_count"]}</div>',
                unsafe_allow_html=True,
            )
        with col_order:
            msg_order = st.radio(
                "Order",
                ["newest", "oldest"],
                format_func=lambda x: "Newest first" if x == "newest" else "Oldest first",
                horizontal=True,
                label_visibility="collapsed",
                key=f"msg_order_{conv_id}",
            )

        order_sql = "DESC" if msg_order == "newest" else "ASC"
        msgs = q(
            f"SELECT role::text, content, tool_name, created_at "
            f"FROM messages WHERE conversation_id = %s ORDER BY created_at {order_sql}",
            (conv_id,),
        )
        for _, m in msgs.iterrows():
            role = m["role"]
            content = m["content"] or ""
            if role == "user":
                with st.chat_message("user"):
                    st.markdown(content if len(content) <= 5000 else content[:5000] + "\n\n*[truncated]*")
            elif role == "assistant":
                with st.chat_message("assistant"):
                    st.markdown(content if len(content) <= 5000 else content[:5000] + "\n\n*[truncated]*")
            elif role == "tool_result":
                with st.expander(f"Tool: {m['tool_name'] or '?'}"):
                    st.code(content[:2000])

elif detail_type == "memory":
    mc_id = int(detail_id)
    mc = q("SELECT * FROM memory_chunks WHERE id = %s", (mc_id,))
    if mc.empty:
        st.error("Memory chunk not found.")
    else:
        r = mc.iloc[0]
        breadcrumbs("Memory", f"#{mc_id}")
        head_l, head_r = st.columns([8, 1])
        with head_l:
            st.markdown(f"<h1>Memory chunk #{mc_id}</h1>", unsafe_allow_html=True)
            cat = str(r["category"])
            color = CATEGORY_COLORS.get(cat, ACCENT)
            src_str = f'#{int(r["source_id"])}' if r["source_id"] else ""
            st.markdown(
                f'<div style="margin-top:0.5rem;">{badge(cat, color)}'
                f'<span style="margin-left:10px;color:{TEXT_MUTED};font-size:12px;">source: {r["source_type"]} {src_str}</span></div>',
                unsafe_allow_html=True,
            )
        with head_r:
            if st.button("Delete", key=f"del_m_{mc_id}", use_container_width=True):
                dml("DELETE FROM memory_chunks WHERE id = %s", (mc_id,))
                go_back()

        st.markdown('<hr/>', unsafe_allow_html=True)

        with st.form(f"edit_mc_{mc_id}"):
            ec = st.text_area("Content", value=r["content"], height=180)
            ca1, ca2 = st.columns(2)
            with ca1:
                cur_cat = str(r["category"])
                ecat = st.selectbox("Category", CATS, index=CATS.index(cur_cat) if cur_cat in CATS else 0)
            with ca2:
                eproj = st.text_input("Project", value=r["project_name"] or "")
            current_tags = list(r["tags"]) if r["tags"] is not None else []
            etags = st.text_input("Tags (comma-separated)", value=", ".join(current_tags))
            econf = st.slider("Confidence", 0.0, 1.0, float(r["confidence"] or 0.8), 0.05)
            if st.form_submit_button("Save changes", type="primary"):
                tags = [t.strip() for t in etags.split(",") if t.strip()]
                msg = dml(
                    "UPDATE memory_chunks SET content=%s, category=%s, tags=%s, confidence=%s, project_name=%s WHERE id=%s",
                    (ec, ecat, tags, econf, eproj or None, mc_id),
                )
                st.toast(msg)

        st.markdown('<hr/>', unsafe_allow_html=True)
        st.markdown('<div class="kai-section-label">Metadata</div>', unsafe_allow_html=True)
        st.json({
            "source_type": r["source_type"],
            "source_id": int(r["source_id"]) if r["source_id"] else None,
            "created_at": str(r["created_at"]),
            "expires_at": str(r["expires_at"]) if r["expires_at"] else None,
        })

        if r["source_type"] == "conversation" and r["source_id"]:
            if st.button(f"Open source conversation #{int(r['source_id'])}"):
                go_to_detail("conversation", r["source_id"])

elif detail_type == "skill":
    sk_id = int(detail_id)
    sk = q("SELECT * FROM skills WHERE id = %s", (sk_id,))
    if sk.empty:
        st.error("Skill not found.")
    else:
        r = sk.iloc[0]
        breadcrumbs("Skills", r["name"])
        st.markdown(f"<h1>{r['name']}</h1>", unsafe_allow_html=True)
        st.markdown(
            f'<div class="kai-subtitle">Version {r["version"] or "1.0.0"}</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<hr/>', unsafe_allow_html=True)

        m1, m2, m3 = st.columns(3)
        m1.metric("Use count", int(r["use_count"] or 0))
        m2.metric("Version", r["version"] or "1.0.0")
        m3.metric("Last used", fmt_dt(r["last_used"]) if r["last_used"] else "—")

        st.markdown('<div class="kai-section-label">Description</div>', unsafe_allow_html=True)
        st.markdown(r["description"] or "*No description*")

        triggers = list(r["triggers"]) if r["triggers"] is not None else []
        if triggers:
            st.markdown('<div class="kai-section-label">Triggers</div>', unsafe_allow_html=True)
            st.markdown(" ".join(chip(t) for t in triggers), unsafe_allow_html=True)

        st.markdown('<div class="kai-section-label">Path</div>', unsafe_allow_html=True)
        pc, oc = st.columns([5, 1])
        with pc:
            st.code(r["path"], language=None)
        with oc:
            if st.button("Open in Finder", key=f"sk_finder_{sk_id}", use_container_width=True):
                if open_in_finder(r["path"]):
                    st.toast("Opened in Finder")
                else:
                    st.toast("Path not found")

        skill_md_path = Path(r["path"]) / "SKILL.md"
        if skill_md_path.exists():
            with st.expander("SKILL.md"):
                st.code(skill_md_path.read_text(encoding="utf-8"), language="markdown")

elif detail_type == "project":
    pr_id = int(detail_id)
    pr = q("SELECT * FROM projects WHERE id = %s", (pr_id,))
    if pr.empty:
        st.error("Project not found.")
    else:
        r = pr.iloc[0]
        breadcrumbs("Projects", r["name"])
        head_l, head_r = st.columns([8, 1])
        with head_l:
            st.markdown(f"<h1>{r['name']}</h1>", unsafe_allow_html=True)
            sc = STATUS_COLORS.get(str(r["status"]), ACCENT)
            st.markdown(
                f'<div style="margin-top:0.5rem;">{badge(str(r["status"]), sc)}</div>',
                unsafe_allow_html=True,
            )
        with head_r:
            if st.button("Delete", key=f"del_p_{pr_id}", use_container_width=True):
                dml("DELETE FROM projects WHERE id = %s", (pr_id,))
                go_back()

        st.markdown('<hr/>', unsafe_allow_html=True)

        STATUS_OPTS = ["active", "paused", "completed", "archived"]
        with st.form(f"edit_pr_{pr_id}"):
            ename = st.text_input("Name", value=r["name"])
            edesc = st.text_area("Description", value=r["description"] or "", height=90)
            estatus = st.selectbox(
                "Status", STATUS_OPTS,
                index=STATUS_OPTS.index(str(r["status"])) if str(r["status"]) in STATUS_OPTS else 0,
            )
            contacts_val = r["contacts"] if isinstance(r["contacts"], (dict, list)) else []
            econtacts = st.text_area("Contacts (JSON)", value=json.dumps(contacts_val, indent=2), height=140)
            decisions_val = r["decisions"] if isinstance(r["decisions"], (dict, list)) else []
            edecisions = st.text_area("Decisions (JSON)", value=json.dumps(decisions_val, indent=2), height=140)
            if st.form_submit_button("Save changes", type="primary"):
                try:
                    cj = json.loads(econtacts)
                    dj = json.loads(edecisions)
                    msg = dml(
                        "UPDATE projects SET name=%s, description=%s, status=%s, contacts=%s, decisions=%s WHERE id=%s",
                        (ename, edesc, estatus, json.dumps(cj), json.dumps(dj), pr_id),
                    )
                    st.toast(msg)
                except json.JSONDecodeError as e:
                    st.error(f"JSON error: {e}")

elif detail_type == "prompt":
    p_id = int(detail_id)
    pr = q("SELECT * FROM prompts WHERE id = %s", (p_id,))
    if pr.empty:
        st.error("Prompt not found.")
    else:
        r = pr.iloc[0]
        breadcrumbs("Prompts", r["name"])
        head_l, head_r = st.columns([8, 1])
        with head_l:
            st.markdown(f"<h1>{r['name']}</h1>", unsafe_allow_html=True)
            st.markdown(
                f'<div class="kai-subtitle">{r["category"] or "uncategorized"} · used {r["usage_count"]}×</div>',
                unsafe_allow_html=True,
            )
        with head_r:
            if st.button("Delete", key=f"del_pt_{p_id}", use_container_width=True):
                dml("DELETE FROM prompts WHERE id = %s", (p_id,))
                go_back()

        st.markdown('<hr/>', unsafe_allow_html=True)

        with st.form(f"edit_p_{p_id}"):
            ename = st.text_input("Name", value=r["name"])
            ecat = st.text_input("Category", value=r["category"] or "")
            econtent = st.text_area("Content", value=r["content"] or "", height=300)
            current_tags = list(r["tags"]) if r["tags"] is not None else []
            etags = st.text_input("Tags", value=", ".join(current_tags))
            if st.form_submit_button("Save changes", type="primary"):
                tags = [t.strip() for t in etags.split(",") if t.strip()]
                msg = dml(
                    "UPDATE prompts SET name=%s, category=%s, content=%s, tags=%s WHERE id=%s",
                    (ename, ecat, econtent, tags, p_id),
                )
                st.toast(msg)

elif detail_type == "entity":
    ent_id = int(detail_id)
    ent = q("SELECT * FROM entities WHERE id = %s", (ent_id,))
    if ent.empty:
        st.error("Entity not found.")
    else:
        r = ent.iloc[0]
        breadcrumbs("Knowledge Graph", r["name"])
        head_l, head_r = st.columns([8, 1])
        with head_l:
            st.markdown(f"<h1>{r['name']}</h1>", unsafe_allow_html=True)
            ec = ENTITY_COLORS.get(r["entity_type"], ACCENT)
            proj_txt = f" · {r['project_name']}" if r["project_name"] else ""
            st.markdown(
                f'<div style="margin-top:0.5rem;">{badge(r["entity_type"], ec)}'
                f'<span style="margin-left:10px;color:{TEXT_MUTED};font-size:12px;">ID #{ent_id}{proj_txt}</span></div>',
                unsafe_allow_html=True,
            )
        with head_r:
            if st.button("Delete", key=f"del_e_{ent_id}", use_container_width=True):
                dml("DELETE FROM entities WHERE id = %s", (ent_id,))
                go_back()

        st.markdown('<hr/>', unsafe_allow_html=True)

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Type", r["entity_type"])
        m2.metric("Mentions", int(r["mention_count"] or 0))
        m3.metric("Confidence", f"{float(r['confidence'] or 0):.2f}")
        m4.metric("First seen", fmt_dt(r["first_seen"], compact=True))

        attrs = r["attributes"] if isinstance(r["attributes"], dict) else {}
        if attrs:
            with st.expander("Attributes"):
                st.json(attrs)

        out_rels = q("""
            SELECT r.id, r.relation_type, e.id AS to_id, e.name AS to_name, e.entity_type AS to_type,
                   r.confidence
            FROM relationships r
            JOIN entities e ON e.id = r.to_entity
            WHERE r.from_entity = %s
            ORDER BY r.confidence DESC
        """, (ent_id,))

        in_rels = q("""
            SELECT r.id, r.relation_type, e.id AS from_id, e.name AS from_name, e.entity_type AS from_type,
                   r.confidence
            FROM relationships r
            JOIN entities e ON e.id = r.from_entity
            WHERE r.to_entity = %s
            ORDER BY r.confidence DESC
        """, (ent_id,))

        col_o, col_i = st.columns(2)
        with col_o:
            st.markdown(
                f'<div class="kai-section-label">Outgoing · {len(out_rels)}</div>',
                unsafe_allow_html=True,
            )
            for _, rel in out_rels.iterrows():
                ec2 = ENTITY_COLORS.get(rel["to_type"], ACCENT)
                cc1, cc2 = st.columns([5, 1])
                with cc1:
                    st.markdown(
                        f"""<div class="kai-list-card">
                            <div style="font-size:11px;color:{TEXT_FAINT};margin-bottom:4px;">{rel['relation_type']} →</div>
                            <div style="font-size:13px;color:{TEXT};">{rel['to_name']} {badge(rel['to_type'], ec2)}</div>
                            <div style="font-size:11px;color:{TEXT_FAINT};margin-top:4px;">conf {rel['confidence']:.2f}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )
                with cc2:
                    if st.button("Open", key=f"out_{rel['id']}"):
                        go_to_detail("entity", rel["to_id"])

        with col_i:
            st.markdown(
                f'<div class="kai-section-label">Incoming · {len(in_rels)}</div>',
                unsafe_allow_html=True,
            )
            for _, rel in in_rels.iterrows():
                ec2 = ENTITY_COLORS.get(rel["from_type"], ACCENT)
                cc1, cc2 = st.columns([5, 1])
                with cc1:
                    st.markdown(
                        f"""<div class="kai-list-card">
                            <div style="font-size:11px;color:{TEXT_FAINT};margin-bottom:4px;">← {rel['relation_type']}</div>
                            <div style="font-size:13px;color:{TEXT};">{rel['from_name']} {badge(rel['from_type'], ec2)}</div>
                            <div style="font-size:11px;color:{TEXT_FAINT};margin-top:4px;">conf {rel['confidence']:.2f}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )
                with cc2:
                    if st.button("Open", key=f"in_{rel['id']}"):
                        go_to_detail("entity", rel["from_id"])

        mentions = q("""
            SELECT em.id, em.source_type, em.source_id, em.context_snippet, em.created_at,
                   c.summary AS conv_title, c.project_name AS conv_project
            FROM entity_mentions em
            LEFT JOIN conversations c ON c.id = em.source_id AND em.source_type = 'conversation'
            WHERE em.entity_id = %s
            ORDER BY em.created_at DESC
        """, (ent_id,))

        if not mentions.empty:
            st.markdown(
                f'<div class="kai-section-label">Timeline · {len(mentions)} mentions</div>',
                unsafe_allow_html=True,
            )
            for _, m in mentions.iterrows():
                cc1, cc2 = st.columns([9, 1])
                with cc1:
                    title = m["conv_title"] or f'{m["source_type"]} #{m["source_id"]}'
                    snippet = m["context_snippet"] or ""
                    st.markdown(
                        f"""<div class="kai-list-card">
                            <div style="font-size:11px;color:{TEXT_FAINT};margin-bottom:4px;">
                                {fmt_dt(m['created_at'])} · {m['source_type']} #{m['source_id']} · {m['conv_project'] or '—'}
                            </div>
                            <div style="font-size:13px;color:{TEXT};font-weight:500;margin-bottom:6px;">{title}</div>
                            <div style="font-size:12.5px;color:{TEXT_MUTED};line-height:1.5;border-left:2px solid {BORDER};padding-left:10px;">{snippet}</div>
                        </div>""",
                        unsafe_allow_html=True,
                    )
                with cc2:
                    if m["source_type"] == "conversation" and st.button("Open", key=f"em_{m['id']}"):
                        go_to_detail("conversation", m["source_id"])

# ══════════════════════════════════════════════════════════════════════════════
# LIST VIEWS
# ══════════════════════════════════════════════════════════════════════════════
elif page == "Dashboard":
    page_header("Dashboard", "Overview of your knowledge base")

    with st.spinner("Loading metrics..."):
        k = q("""SELECT
            (SELECT count(*) FROM conversations) AS conv,
            (SELECT count(*) FROM messages) AS msg,
            (SELECT count(*) FROM skills) AS sk,
            (SELECT count(*) FROM memory_chunks) AS mem""")

    r = k.iloc[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Conversations", f"{int(r['conv']):,}")
    c2.metric("Messages",      f"{int(r['msg']):,}")
    c3.metric("Memory chunks", f"{int(r['mem']):,}")
    c4.metric("Skills",        f"{int(r['sk']):,}")

    st.markdown("<div style='height:1.5rem;'></div>", unsafe_allow_html=True)

    left, right = st.columns([3, 2])
    with left:
        st.markdown('<div class="kai-section-label">Activity · last 14 days</div>', unsafe_allow_html=True)
        timeline = q("""
            SELECT date_trunc('day', started_at)::date AS day, count(*) AS n
            FROM conversations
            WHERE started_at >= now() - interval '14 days'
            GROUP BY day ORDER BY day
        """)
        if timeline.empty:
            st.info("No recent activity.")
        else:
            today = datetime.utcnow().date()
            all_days = pd.DataFrame({"day": [today - timedelta(days=i) for i in range(13, -1, -1)]})
            timeline["day"] = pd.to_datetime(timeline["day"]).dt.date
            df_full = all_days.merge(timeline, on="day", how="left").fillna(0)
            df_full["n"] = df_full["n"].astype(int)

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=df_full["day"], y=df_full["n"],
                mode="lines+markers",
                line=dict(color=ACCENT, width=2.5, shape="spline", smoothing=0.6),
                marker=dict(size=7, color=ACCENT, line=dict(width=2, color=BG)),
                fill="tozeroy", fillcolor="rgba(88, 166, 255, 0.12)",
                hovertemplate="<b>%{x|%b %d}</b><br>%{y} conversations<extra></extra>",
            ))
            plotly_dark_layout(fig, height=260)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    with right:
        st.markdown('<div class="kai-section-label">Memory by category</div>', unsafe_allow_html=True)
        mc = q("SELECT category::text AS category, count(*) AS n FROM memory_chunks GROUP BY category ORDER BY n DESC")
        if mc.empty:
            st.info("No memory chunks yet.")
        else:
            colors = [CATEGORY_COLORS.get(c, ACCENT) for c in mc["category"]]
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=mc["n"], y=mc["category"], orientation="h",
                marker=dict(color=colors, line=dict(width=0)),
                hovertemplate="<b>%{y}</b><br>%{x} chunks<extra></extra>",
                text=mc["n"], textposition="outside",
                textfont=dict(color=TEXT_MUTED, size=11),
            ))
            plotly_dark_layout(fig, height=260)
            fig.update_layout(
                yaxis=dict(autorange="reversed", gridcolor="rgba(0,0,0,0)", tickfont=dict(color=TEXT, size=12)),
                xaxis=dict(showgrid=False, showticklabels=False),
                margin=dict(l=10, r=40, t=10, b=10),
            )
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    st.markdown('<div class="kai-section-label">Recent conversations</div>', unsafe_allow_html=True)
    df = q("SELECT id, summary, project_name, model, started_at, message_count "
           "FROM conversations ORDER BY started_at DESC LIMIT 5")
    if df.empty:
        st.info("No conversations.")
    else:
        for _, row in df.iterrows():
            title = row["summary"] or f"Conversation #{row['id']}"
            col_card, col_btn = st.columns([10, 1])
            with col_card:
                st.markdown(
                    f"""<div class="kai-list-card">
                        <div class="kai-list-title">{title}</div>
                        <div class="kai-list-meta">
                            #{row['id']} · {row['project_name'] or 'no project'} · {row['model'] or '—'}
                            · {row['message_count']} messages · {fmt_dt(row['started_at'])}
                        </div>
                    </div>""",
                    unsafe_allow_html=True,
                )
            with col_btn:
                if st.button("Open", key=f"dash_open_{row['id']}", use_container_width=True):
                    go_to_detail("conversation", row["id"])

elif page == "Calendar":
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

    # Memory category filter (only active if Memory chunks selected)
    all_cats = ["decision", "pattern", "insight", "preference", "contact", "error_solution", "project_context", "workflow"]
    cat_filter_list = []
    if show_mem:
        cat_filter_list = st.multiselect(
            "Memory categories (empty = all)",
            all_cats,
            default=[],
        )

    # Build date filter — event_date = wann WIRKLICH passiert (nicht DB-Insert)
    from datetime import datetime, timedelta
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
    # Für Memory: Datum der Quell-Conversation (wenn vorhanden), sonst created_at
    event_date_expr = "COALESCE(c.started_at, mc.created_at)"
    where_date_mem = f"{event_date_expr} >= '{cutoff}'::timestamptz" if cutoff else "TRUE"

    events = []

    # Conversations als Events
    if show_conv:
        df_conv = q(f"""
            SELECT c.id, c.summary, c.project_name, c.model, c.message_count,
                   c.started_at, c.ended_at
            FROM conversations c
            WHERE {where_date_conv}
            ORDER BY c.started_at
        """)
        for _, r in df_conv.iterrows():
            # Farbe pro Projekt (hash-basiert)
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

            # Duration in minutes
            try:
                dur_min = (ended - started).total_seconds() / 60 if ended else 0
            except Exception:
                dur_min = 0

            # Short sessions (< 15 min) → all-day event on that day
            # Medium sessions (15 min – 12h) → timed block
            # Long sessions (>= 12h, e.g. multi-day plans) → all-day spanning start..end+1
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
                # FullCalendar all-day end is EXCLUSIVE → add 1 day
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

    # Memory Chunks als Marker-Events (Punkt-Events) —
    # event_date = started_at der Quell-Conversation (wann wirklich passiert),
    # sonst Fallback auf mc.created_at
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

    # Skills — prefer file_modified (real creation), fallback to last_used, then created_at
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

    # Projects (created_at)
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

    # Prompts (created_at)
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

    # Entities (first_seen) — from Knowledge Graph
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
            pass  # entities table may not exist yet

    # Memory Reflections (dedup, contradictions, etc.)
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

    # Ingestion events
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

    # Sinnvolles Initialdatum: neuester Event (damit Nutzer immer was sieht)
    initial_date = None
    if events:
        dates = sorted([e.get("start", "") for e in events if e.get("start")], reverse=True)
        if dates:
            initial_date = dates[0][:10]  # YYYY-MM-DD

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

    # Breakdown per source
    from collections import Counter
    breakdown = Counter(e.get("extendedProps", {}).get("type", "unknown") for e in events)
    summary = ", ".join(f"{v} {k}" for k, v in breakdown.most_common())
    st.markdown(f"**{len(events)} events** — {summary or '(none)'}")

    # Warn about selected-but-empty categories
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

    # Klick-Handling: Event anklicken → Detail-View öffnen
    if result and result.get("callback") == "eventClick":
        ev = result.get("eventClick", {}).get("event", {})
        ext = ev.get("extendedProps", {})
        etype = ext.get("type")
        eid = ext.get("entity_id")
        if eid:
            if etype in ("conversation", "memory", "skill", "project", "prompt", "entity"):
                go_to_detail(etype, int(eid))

    # Legend
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

elif page == "Search":
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

    if search_term:
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

elif page == "Semantic":
    page_header("Semantic search", "Vector similarity across memory chunks and messages (pgvector cosine distance)")

    if semantic_helper is None or not semantic_helper.backend_available():
        st.error(
            "Semantic backend unavailable. Set OPENAI_API_KEY or run Ollama "
            "(`brew services start ollama`, then `ollama pull nomic-embed-text`). "
            "Then generate embeddings: `python3 scripts/generate_embeddings.py --backend ollama`."
        )
    else:
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
            if st.button("Refresh embeddings", use_container_width=True):
                with st.spinner("Generating embeddings..."):
                    p = subprocess.run(
                        ["python3",
                         str(Path(__file__).resolve().parent.parent / "scripts" / "generate_embeddings.py"),
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

        if sem_q:
            with st.spinner("Embedding query..."):
                results = semantic_helper.semantic_search(conn, sem_q, limit=sem_limit)

            if not results:
                st.info("No hits. Either no embeddings for this backend or query returned nothing.")
            else:
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

elif page == "Conversations":
    page_header("Conversations", "All recorded Claude Code sessions")

    with st.spinner("Loading filters..."):
        projs = q("SELECT DISTINCT project_name FROM conversations WHERE project_name IS NOT NULL ORDER BY project_name")
        mods = q("SELECT DISTINCT model FROM conversations WHERE model IS NOT NULL ORDER BY model")

    fcol1, fcol2, fcol3 = st.columns([2, 2, 3])
    with fcol1:
        sel_p = st.selectbox("Project", ["All"] + projs["project_name"].dropna().tolist())
    with fcol2:
        sel_m = st.selectbox("Model", ["All"] + mods["model"].dropna().tolist())
    with fcol3:
        search = st.text_input("Search in messages", placeholder="Full-text search...")

    w, params_list = [], []
    if sel_p != "All":
        w.append("c.project_name = %s"); params_list.append(sel_p)
    if sel_m != "All":
        w.append("c.model = %s"); params_list.append(sel_m)
    if search:
        w.append("c.id IN (SELECT DISTINCT conversation_id FROM messages WHERE content ILIKE %s)")
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
    else:
        st.markdown(
            f'<div class="kai-section-label">{len(df)} conversations · click a row to open</div>',
            unsafe_allow_html=True,
        )
        sel = st.dataframe(
            df, use_container_width=True, hide_index=True, height=620,
            on_select="rerun", selection_mode="single-row",
            column_config={
                "id":         st.column_config.NumberColumn("ID", width="small"),
                "title":      st.column_config.TextColumn("Title", width="large"),
                "project":    st.column_config.TextColumn("Project", width="medium"),
                "model":      st.column_config.TextColumn("Model", width="small"),
                "started_at": st.column_config.DatetimeColumn("Started", width="small"),
                "messages":   st.column_config.NumberColumn("Msgs", width="small"),
            },
            key="df_conv",
        )
        if sel.selection and sel.selection.rows:
            go_to_detail("conversation", int(df.iloc[sel.selection.rows[0]]["id"]))

elif page == "Memory":
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
    else:
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

elif page == "Memory Health":
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

    if st.button("Run reflection now", type="primary"):
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
        st.dataframe(
            refl_disp[["id", "reflection_type", "action_taken", "n_chunks",
                       "affected_chunks", "confidence", "created_at", "reasoning"]],
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
    else:
        links_disp = links.copy()
        links_disp["merged_from"] = links_disp["merged_from"].apply(
            lambda x: ",".join(str(i) for i in x) if isinstance(x, list) and x else ""
        )
        st.dataframe(links_disp, use_container_width=True, hide_index=True)

elif page == "Skills":
    page_header("Skills", "Registered skills available in your environment")
    with st.spinner("Loading skills..."):
        df = q("""SELECT id, name, description, use_count, last_used, version,
                         COALESCE(file_modified, last_used, created_at) AS sort_date
                  FROM skills
                  ORDER BY sort_date DESC NULLS LAST, use_count DESC NULLS LAST, name""")
    if df.empty:
        st.info("No skills. Run the skill scanner from the Ingestion page.")
    else:
        st.markdown(
            f'<div class="kai-section-label">{len(df)} skills</div>',
            unsafe_allow_html=True,
        )
        cols = st.columns(3)
        for i, (_, row) in enumerate(df.iterrows()):
            col = cols[i % 3]
            with col:
                desc = (row["description"] or "No description")
                desc_short = desc[:160] + ("..." if len(desc) > 160 else "")
                uc = int(row["use_count"] or 0)
                last = fmt_dt(row["last_used"], compact=True) if row["last_used"] else "never"
                st.markdown(
                    f"""<div class="kai-list-card" style="min-height:180px;display:flex;flex-direction:column;">
                        <div style="font-size:14px;font-weight:600;color:{TEXT};margin-bottom:6px;letter-spacing:-0.01em;">{row['name']}</div>
                        <div style="font-size:12.5px;color:{TEXT_MUTED};line-height:1.5;flex:1;margin-bottom:12px;">{desc_short}</div>
                        <div style="display:flex;justify-content:space-between;font-size:11px;color:{TEXT_FAINT};border-top:1px solid {BORDER_MUTED};padding-top:10px;">
                            <span>used {uc}×</span>
                            <span>{last}</span>
                        </div>
                    </div>""",
                    unsafe_allow_html=True,
                )
                if st.button("Open", key=f"sk_open_{row['id']}"):
                    go_to_detail("skill", int(row["id"]))

elif page == "Knowledge Graph":
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
        if st.button("Start entity extractor", type="primary"):
            subprocess.Popen(
                [sys.executable, str(SCRIPTS_ROOT / "extract_entities.py")],
                stdout=open("/tmp/extract_entities.log", "w"), stderr=subprocess.STDOUT,
            )
            st.toast("Running in background. Log: /tmp/extract_entities.log")

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
        max_nodes = st.slider("Max. nodes", 10, 200, 60)

    where = ["mention_count >= %s"]
    params_list = [min_mentions]
    if sel_type != "All":
        where.append("entity_type = %s")
        params_list.append(sel_type)
    if sel_proj != "All":
        where.append("project_name = %s")
        params_list.append(sel_proj)
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
    else:
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

            # Layout-Optionen oberhalb
            layout_col1, layout_col2, layout_col3 = st.columns([2, 2, 2])
            with layout_col1:
                graph_height = st.slider("Graph height (px)", 600, 1600, 900, 50, key="kg_height")
            with layout_col2:
                node_spacing = st.slider("Node spacing", 100, 600, 280, 20, key="kg_spacing",
                                         help="Higher = more space between nodes")
            with layout_col3:
                label_size = st.slider("Label font size", 10, 24, 14, 1, key="kg_label_size")

            nodes = []
            for _, e in ents_df.iterrows():
                # Größere Basis-Size für bessere Lesbarkeit
                size = min(20 + int(e["mention_count"]) * 4, 70)
                color = ENTITY_COLORS.get(e["entity_type"], TEXT_MUTED)
                label = e["name"][:30] + ("..." if len(e["name"]) > 30 else "")
                nodes.append(Node(
                    id=str(int(e["id"])),
                    label=label,
                    size=size,
                    color=color,
                    title=f"{e['name']}\nType: {e['entity_type']}\nProject: {e['project_name'] or '—'}\nMentions: {e['mention_count']}",
                    shape="dot",
                    font={"size": label_size, "color": TEXT, "face": "Inter, sans-serif", "strokeWidth": 3, "strokeColor": BG},
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

            # Physics deutlich lockerer — Knoten abstoßen, längere Springs
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
                # Vis.js Physics — starke Abstoßung
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

elif page == "Projects":
    page_header("Projects", "Tracked projects and their context")

    with st.expander("New project"):
        with st.form("new_pr"):
            pn = st.text_input("Name")
            pd_text = st.text_area("Description", height=80)
            ps = st.selectbox("Status", ["active", "paused", "completed", "archived"])
            pc = st.text_area("Contacts (JSON)", value="[]", height=80)
            if st.form_submit_button("Create", type="primary"):
                try:
                    cj = json.loads(pc)
                    msg = dml(
                        "INSERT INTO projects (name, description, status, contacts) VALUES (%s, %s, %s, %s)",
                        (pn, pd_text, ps, json.dumps(cj)),
                    )
                    st.toast(msg)
                    st.rerun()
                except json.JSONDecodeError:
                    st.error("Contacts must be valid JSON.")

    with st.spinner("Loading projects..."):
        df = q("""SELECT id, name, description, status::text AS status, created_at
                  FROM projects
                  ORDER BY created_at DESC NULLS LAST, name""")
    if df.empty:
        st.info("No projects.")
    else:
        st.markdown(
            f'<div class="kai-section-label">{len(df)} projects</div>',
            unsafe_allow_html=True,
        )
        cols = st.columns(2)
        for i, (_, row) in enumerate(df.iterrows()):
            col = cols[i % 2]
            with col:
                sc = STATUS_COLORS.get(row["status"], ACCENT)
                desc = row["description"] or "No description"
                desc_short = desc[:220] + ("..." if len(desc) > 220 else "")
                st.markdown(
                    f"""<div class="kai-list-card" style="min-height:150px;display:flex;flex-direction:column;">
                        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;">
                            <div style="font-size:15px;font-weight:600;color:{TEXT};letter-spacing:-0.01em;">{row['name']}</div>
                            <div>{badge(row["status"], sc)}</div>
                        </div>
                        <div style="font-size:12.5px;color:{TEXT_MUTED};line-height:1.55;flex:1;">{desc_short}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
                if st.button("Open", key=f"pr_open_{row['id']}"):
                    go_to_detail("project", int(row["id"]))

elif page == "Prompts":
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

    # Sort-Controls
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
        cat_filter = st.selectbox("Category", ["All"] + cats_df["category"].dropna().tolist())
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
    else:
        st.markdown(
            f'<div class="kai-section-label">{len(df)} prompts</div>',
            unsafe_allow_html=True,
        )
        cols = st.columns(2)
        for i, (_, row) in enumerate(df.iterrows()):
            col = cols[i % 2]
            with col:
                cat_html = badge(row["category"], PURPLE) if row["category"] else ""
                tags_html = " ".join(chip(t) for t in (row["tags"] or [])[:4])
                preview = (row["content"] or "")[:200] + ("..." if row["content"] and len(row["content"]) > 200 else "")
                # Datum formatieren
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

elif page == "Scheduler":
    page_header("Scheduler", "launchd-backed recurring Claude tasks")

    import yaml

    TASKS_YAML = Path.home() / "Library/Application Support/Claude/scheduler/config/tasks.yaml"
    LOGS_DIR = Path.home() / "Library/Application Support/Claude/scheduler/logs"

    try:
        with open(TASKS_YAML) as f:
            tasks_cfg = yaml.safe_load(f)
        tasks = tasks_cfg.get("tasks", [])
    except Exception as e:
        st.error(f"tasks.yaml could not be read: {e}")
        tasks = []

    try:
        result = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=5)
        launchd_jobs = {}
        for line in result.stdout.split("\n"):
            if "com.claude.scheduler" in line:
                parts = line.split()
                if len(parts) >= 3:
                    launchd_jobs[parts[2]] = {"pid": parts[0], "exit_code": parts[1]}
    except Exception:
        launchd_jobs = {}

    tm1, tm2 = st.columns(2)
    tm1.metric("Configured tasks", len(tasks))
    tm2.metric("launchd registered", len(launchd_jobs))

    st.markdown("<div style='height:0.5rem;'></div>", unsafe_allow_html=True)
    ac1, ac2, _ = st.columns([2, 2, 8])
    with ac1:
        if st.button("Reinstall launchd", use_container_width=True):
            try:
                r = subprocess.run(
                    [str(Path.home() / ".claude/skills/claude-scheduler/scripts/install-schedule.sh"), "install"],
                    capture_output=True, text=True, timeout=30,
                )
                st.code(r.stdout + r.stderr)
            except Exception as e:
                st.error(str(e))
    with ac2:
        if st.button("Uninstall all", use_container_width=True):
            try:
                r = subprocess.run(
                    [str(Path.home() / ".claude/skills/claude-scheduler/scripts/install-schedule.sh"), "uninstall"],
                    capture_output=True, text=True, timeout=30,
                )
                st.code(r.stdout + r.stderr)
            except Exception as e:
                st.error(str(e))

    st.markdown('<hr/>', unsafe_allow_html=True)

    for task in tasks:
        name = task.get("name", "?")
        schedule = task.get("schedule", "?")
        enabled = task.get("enabled", True)
        description = task.get("description", "")
        label = f"com.claude.scheduler.{name.lower().replace(' ', '-')}"
        is_loaded = label in launchd_jobs

        if enabled and is_loaded:
            dot_color, state_text = SUCCESS, "Active"
        elif not enabled:
            dot_color, state_text = WARNING, "Paused"
        else:
            dot_color, state_text = DANGER, "Not loaded"

        pid = launchd_jobs.get(label, {}).get("pid", "—")

        with st.container(border=True):
            c1, c2, c3 = st.columns([6, 2, 2])
            with c1:
                st.markdown(
                    f"""<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
                        {status_dot(dot_color)}
                        <span style="font-size:15px;font-weight:600;color:{TEXT};">{name}</span>
                        {badge(state_text, dot_color)}
                    </div>
                    <div style="font-size:12.5px;color:{TEXT_MUTED};margin-bottom:8px;">{description}</div>
                    <div style="font-size:11px;color:{TEXT_FAINT};font-family:'SF Mono',monospace;">
                        cron {schedule} · PID {pid}
                    </div>""",
                    unsafe_allow_html=True,
                )
            with c2:
                if st.button("Run now", key=f"run_{name}", use_container_width=True):
                    try:
                        subprocess.run(
                            [str(Path.home() / ".claude/skills/claude-scheduler/scripts/run-task.sh"), name],
                            capture_output=True, text=True, timeout=5,
                        )
                        st.toast(f"Started: {name}")
                    except Exception as e:
                        st.error(str(e))
            with c3:
                if enabled:
                    if st.button("Pause", key=f"off_{name}", use_container_width=True):
                        for t in tasks:
                            if t.get("name") == name:
                                t["enabled"] = False
                        with open(TASKS_YAML, "w") as f:
                            yaml.safe_dump({"tasks": tasks}, f, allow_unicode=True)
                        st.rerun()
                else:
                    if st.button("Enable", key=f"on_{name}", use_container_width=True):
                        for t in tasks:
                            if t.get("name") == name:
                                t["enabled"] = True
                        with open(TASKS_YAML, "w") as f:
                            yaml.safe_dump({"tasks": tasks}, f, allow_unicode=True)
                        st.rerun()

            with st.expander("Prompt"):
                st.code(task.get("prompt", ""), language="markdown")

            log_base = LOGS_DIR / f"{name.lower().replace(' ', '-')}"
            stdout_log = Path(str(log_base) + "-stdout.log")
            stderr_log = Path(str(log_base) + "-stderr.log")
            if stdout_log.exists() or stderr_log.exists():
                with st.expander("Recent logs"):
                    if stdout_log.exists():
                        st.markdown('<div class="kai-section-label">stdout</div>', unsafe_allow_html=True)
                        st.code(stdout_log.read_text()[-2000:] or "(empty)")
                    if stderr_log.exists():
                        st.markdown('<div class="kai-section-label">stderr</div>', unsafe_allow_html=True)
                        st.code(stderr_log.read_text()[-2000:] or "(empty)")

elif page == "Ingestion":
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
            if st.button("Run session ingestion", key="ing_sess", use_container_width=True, type="primary"):
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
            if st.button("Run skill scanner", key="ing_sk", use_container_width=True, type="primary"):
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
            if st.button("Run memory extraction", key="ing_mem", use_container_width=True, type="primary"):
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
            if st.button("Run title generation", key="ing_title", use_container_width=True, type="primary"):
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

elif page == "SQL":
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
            is_read = upper.startswith("SELECT") or upper.startswith("WITH") or upper.startswith("EXPLAIN")
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

    st.markdown('<hr/>', unsafe_allow_html=True)
    st.markdown('<div class="kai-section-label">Snippets</div>', unsafe_allow_html=True)
    sn_cols = st.columns(2)
    snippets = [
        ("Recent conversations", "SELECT * FROM conversations ORDER BY started_at DESC LIMIT 20;"),
        ("Memory by category",   "SELECT category::text, count(*) FROM memory_chunks GROUP BY category ORDER BY count DESC;"),
        ("Top projects",         "SELECT project_name, count(*) AS sessions FROM conversations GROUP BY project_name ORDER BY sessions DESC LIMIT 20;"),
        ("Messages by role",     "SELECT role::text, count(*) FROM messages GROUP BY role;"),
    ]
    for i, (label, sq) in enumerate(snippets):
        with sn_cols[i % 2]:
            with st.container(border=True):
                st.markdown(
                    f'<div style="font-size:12px;font-weight:600;color:{TEXT};margin-bottom:6px;">{label}</div>',
                    unsafe_allow_html=True,
                )
                st.code(sq, language="sql")
