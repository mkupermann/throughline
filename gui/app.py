#!/usr/bin/env python3
"""Throughline — Persistent long-term memory for Claude Code (Streamlit GUI)."""

from __future__ import annotations

import io
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

# ── Hand our live globals to gui/page_views/* so they can read st/q/page_header/…
# without re-importing this module (re-import would re-run the sidebar widgets
# and trip StreamlitDuplicateElementId). See gui/page_views/__init__.py for why we
# can't just use sys.modules["__main__"].
from gui.page_views import register_app as _register_app  # noqa: E402
_register_app(globals())

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
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from forget import forget_chunks, forget_entity  # noqa: E402

# ── PII redaction for the GUI display surface ─────────────────────────────────
# throughline.pii.redact also runs server-side before extraction (default-on).
# In the GUI we additionally redact the *displayed* raw message bodies, since
# a Streamlit viewer would otherwise show any tokens that scrolled past in a
# Bash output. Toggle in the sidebar; default ON.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
try:
    from throughline.pii import redact as _pii_redact  # noqa: E402
except Exception:
    _pii_redact = None  # package not installed in editable mode — toggle disabled


def _maybe_redact(text: str | None) -> str:
    """Apply pii.redact() to *text* iff the sidebar toggle is on AND the
    package is importable. Falls through unchanged otherwise."""
    if not text or _pii_redact is None:
        return text or ""
    if st.session_state.get("gui_redact_secrets", True):
        return _pii_redact(text)
    return text


# ── Demo mode (read-only deploys) ─────────────────────────────────────────────
# When THROUGHLINE_DEMO_MODE=1 (or true/yes/on), every button that mutates the
# database or spawns a pipeline subprocess against the host filesystem renders
# as a disabled stand-in with a tooltip explaining why. The seeded demo dataset
# on a public host (e.g. kupermann.com/memory/) stays intact, and visitors can
# still see what each button would do without being able to break the seed.
DEMO_MODE = os.environ.get("THROUGHLINE_DEMO_MODE", "").lower() in ("1", "true", "yes", "on")


def _demo_disabled_button(
    label: str,
    *,
    key: str | None = None,
    use_container_width: bool = True,
    reason: str = (
        "Disabled in demo mode. This button would mutate the database or "
        "spawn a pipeline subprocess. Run Throughline locally (see README) "
        "to use it."
    ),
) -> bool:
    """Render a disabled stand-in for a real button. Always returns False."""
    st.button(
        label,
        key=key,
        disabled=True,
        use_container_width=use_container_width,
        help=reason,
    )
    return False


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Throughline",
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


def fmt_count(x) -> str:
    """Humanise a count: 0 → '0', 1234 → '1,234', 1.3M / 4.2K / 5.7B.

    Used wherever the raw integer is too noisy (token totals on the
    Conversation detail page hit the billions on long-lived sessions).
    Threshold for compaction is 10,000 — below that the comma-grouped
    integer is still readable.
    """
    if x is None:
        return "—"
    try:
        if isinstance(x, float) and pd.isna(x):
            return "—"
        n = int(x)
    except Exception:
        return "—"
    abs_n = abs(n)
    if abs_n < 10_000:
        return f"{n:,}"
    if abs_n < 1_000_000:
        return f"{n/1_000:.1f} K"
    if abs_n < 1_000_000_000:
        return f"{n/1_000_000:.1f} M"
    return f"{n/1_000_000_000:.2f} B"


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
# EXPORT (CSV / Excel / PDF)
# ══════════════════════════════════════════════════════════════════════════════
def _df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def _df_to_excel_bytes(df: pd.DataFrame, sheet_name: str = "data") -> bytes | None:
    try:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            safe = sheet_name[:31] if sheet_name else "data"
            df.to_excel(w, sheet_name=safe, index=False)
        return buf.getvalue()
    except Exception:
        return None


def _df_to_pdf_bytes(df: pd.DataFrame, title: str = "Export") -> bytes | None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
        )
    except Exception:
        return None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=10 * mm, rightMargin=10 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=title,
    )
    styles = getSampleStyleSheet()
    cell_style = styles["BodyText"]
    cell_style.fontSize = 7
    cell_style.leading = 9

    def _cell(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        s = str(v)
        if len(s) > 220:
            s = s[:217] + "..."
        return Paragraph(
            s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"),
            cell_style,
        )

    headers = [str(c) for c in df.columns]
    data = [headers] + [[_cell(v) for v in row] for row in df.itertuples(index=False)]

    avail = landscape(A4)[0] - 20 * mm
    n_cols = max(1, len(headers))
    col_w = [avail / n_cols] * n_cols

    tbl = Table(data, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0),  colors.HexColor("#161B22")),
        ("TEXTCOLOR",    (0, 0), (-1, 0),  colors.HexColor("#C9D1D9")),
        ("FONTNAME",     (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0), (-1, -1), 7),
        ("ALIGN",        (0, 0), (-1, -1), "LEFT"),
        ("VALIGN",       (0, 0), (-1, -1), "TOP"),
        ("GRID",         (0, 0), (-1, -1), 0.25, colors.HexColor("#30363D")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#F6F8FA")]),
    ]))

    title_style = styles["Heading2"]
    title_style.fontSize = 12
    elements = [
        Paragraph(title, title_style),
        Paragraph(
            f"<font size=8 color='#6E7681'>"
            f"{len(df)} rows · exported {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            f"</font>",
            styles["BodyText"],
        ),
        Spacer(1, 6),
        tbl,
    ]
    doc.build(elements)
    return buf.getvalue()


def render_export_buttons(
    df: pd.DataFrame,
    key_prefix: str,
    filename_base: str,
    title: str | None = None,
) -> None:
    """Render CSV / Excel / PDF download buttons for `df`.

    Excel needs `openpyxl`; PDF needs `reportlab`. Missing deps degrade gracefully
    — those buttons just disappear with a small caption hint.
    """
    if df is None or df.empty:
        return

    pretty = title or filename_base.replace("_", " ").title()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_base = "".join(c if c.isalnum() or c in "-_" else "_" for c in filename_base)

    csv_bytes = _df_to_csv_bytes(df)
    xlsx_bytes = _df_to_excel_bytes(df, sheet_name=safe_base[:31] or "data")
    pdf_bytes = _df_to_pdf_bytes(df, title=pretty)

    available = 1 + (1 if xlsx_bytes else 0) + (1 if pdf_bytes else 0)
    cols = st.columns([1] * available + [max(1, 8 - available)])
    i = 0
    cols[i].download_button(
        label="Export CSV",
        data=csv_bytes,
        file_name=f"{safe_base}_{ts}.csv",
        mime="text/csv",
        key=f"{key_prefix}_csv",
        use_container_width=True,
    )
    i += 1
    if xlsx_bytes:
        cols[i].download_button(
            label="Export Excel",
            data=xlsx_bytes,
            file_name=f"{safe_base}_{ts}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key_prefix}_xlsx",
            use_container_width=True,
        )
        i += 1
    if pdf_bytes:
        cols[i].download_button(
            label="Export PDF",
            data=pdf_bytes,
            file_name=f"{safe_base}_{ts}.pdf",
            mime="application/pdf",
            key=f"{key_prefix}_pdf",
            use_container_width=True,
        )

    missing = []
    if not xlsx_bytes:
        missing.append("openpyxl (Excel)")
    if not pdf_bytes:
        missing.append("reportlab (PDF)")
    if missing:
        cols[-1].caption("Install: pip install " + " ".join(m.split()[0] for m in missing))


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
            <div class="kai-brand-title">Throughline</div>
            <div class="kai-brand-sub">Knowledge Base</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if DEMO_MODE:
        st.markdown(
            f"""<div style="margin: 8px 0 14px 0; padding: 8px 12px;
                background: rgba(184, 83, 46, 0.12);
                border: 1px solid rgba(184, 83, 46, 0.35);
                border-radius: 6px; font-size: 11px; color: {TEXT_MUTED};
                line-height: 1.45;">
                <b style="color:{TEXT};">Demo mode</b> — buttons that would
                mutate the database or run pipeline scripts are disabled.
                Run Throughline locally (see <a href="https://github.com/mkupermann/throughline"
                style="color:{ACCENT};">README</a>) to use them.
            </div>""",
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

    # PII redaction toggle for the displayed message bodies. Default ON.
    # Distinct from THROUGHLINE_REDACT_PII (server-side, runs before
    # extraction) — this one redacts what the *viewer* sees in raw messages.
    if _pii_redact is None:
        st.caption(
            "Display redaction unavailable — install the project (`pip install -e .`) "
            "to enable secret-redaction in raw message views."
        )
    else:
        st.checkbox(
            "Redact secrets in views",
            value=st.session_state.get("gui_redact_secrets", True),
            key="gui_redact_secrets",
            help=(
                "Apply throughline.pii.redact to raw message bodies before display. "
                "Independent of the server-side redaction that runs before "
                "memory/entity extraction (THROUGHLINE_REDACT_PII)."
            ),
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
        m1.metric("Messages", fmt_count(c["message_count"]))
        m2.metric("Tokens in", fmt_count(c["token_count_in"]))
        m3.metric("Tokens out", fmt_count(c["token_count_out"]))
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
            content = _maybe_redact(m["content"] or "")
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
            if DEMO_MODE:
                _demo_disabled_button("Forget", key=f"del_m_{mc_id}",
                    reason="Disabled in demo mode — would cascade-delete the chunk + its embeddings.")
            elif st.button(
                "Forget",
                key=f"del_m_{mc_id}",
                use_container_width=True,
                help="Cascade-delete this chunk and its embeddings, with audit row in memory_reflections.",
            ):
                try:
                    res = forget_chunks(_live_conn(), [mc_id], reason="GUI memory detail forget")
                    st.toast(
                        f"Forgotten — {res['chunks']} chunk(s), "
                        f"{res['embeddings']} embedding(s) — audit #{res['reflection_id']}"
                    )
                except Exception as e:
                    st.toast(f"Error: {e}", icon="⚠️")
                else:
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
        pr_name = r["name"]
        breadcrumbs("Projects", pr_name)
        head_l, head_r = st.columns([8, 1])
        with head_l:
            st.markdown(f"<h1>{pr_name}</h1>", unsafe_allow_html=True)
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

        # ── Per-project artifact counts (one round-trip; used for tab labels) ──
        # Conversations / skills / prompts are matched against project_path /
        # path / source_path with TWO patterns: the literal project name and
        # the hyphens-to-slashes variant. The variant exists because
        # `scripts/ingest_sessions.py` derives project_path from Claude Code's
        # session-hash by replacing every '-' with '/', so a real repo like
        # `claude-memory-db` ends up stored as `.../claude/memory/db/...`.
        # The literal-name match handles correctly-stored data; the variant
        # match catches the hyphen-mangled rows. Both run as one ILIKE OR.
        # See `scripts/ingest_sessions.py:255` — the underlying bug is being
        # tracked separately; this UI logic is forwards-compatible.
        name_var = pr_name.replace("-", "/")
        counts_df = q(
            """
            SELECT
                (SELECT count(*) FROM memory_chunks
                    WHERE project_name = %(name)s)                                   AS chunks,
                (SELECT count(*) FROM conversations
                    WHERE project_name = %(name)s
                       OR project_path ILIKE '%%/' || %(name)s || '/%%'
                       OR project_path ILIKE '%%/' || %(name)s
                       OR project_path ILIKE '%%/' || %(var)s  || '/%%'
                       OR project_path ILIKE '%%/' || %(var)s)                       AS convs,
                (SELECT count(*) FROM entities
                    WHERE project_name = %(name)s)                                   AS entities,
                (SELECT count(*) FROM skills
                    WHERE path ILIKE '%%/' || %(name)s || '/%%'
                       OR path ILIKE '%%/' || %(name)s
                       OR path ILIKE '%%/' || %(var)s  || '/%%'
                       OR path ILIKE '%%/' || %(var)s)                               AS skills,
                (SELECT count(*) FROM prompts
                    WHERE source_path ILIKE '%%/' || %(name)s || '/%%'
                       OR source_path ILIKE '%%/' || %(name)s
                       OR source_path ILIKE '%%/' || %(var)s  || '/%%'
                       OR source_path ILIKE '%%/' || %(var)s)                        AS prompts,
                (SELECT count(*) FROM memory_reflections r
                 WHERE EXISTS (
                     SELECT 1 FROM unnest(r.affected_chunks) ac
                     JOIN memory_chunks mc ON mc.id = ac
                     WHERE mc.project_name = %(name)s
                 ))                                                                  AS reflections
            """,
            {"name": pr_name, "var": name_var},
        )
        c = counts_df.iloc[0]
        n_chunks = int(c["chunks"])
        n_convs = int(c["convs"])
        n_ents = int(c["entities"])
        n_skills = int(c["skills"])
        n_prompts = int(c["prompts"])
        n_refl = int(c["reflections"])

        tab_overview, tab_mem, tab_conv, tab_ent, tab_sk, tab_pr, tab_refl = st.tabs([
            "Overview",
            f"Memory ({n_chunks})",
            f"Conversations ({n_convs})",
            f"Entities ({n_ents})",
            f"Skills ({n_skills})",
            f"Prompts ({n_prompts})",
            f"Reflections ({n_refl})",
        ])

        with tab_overview:
            STATUS_OPTS = ["active", "paused", "completed", "archived"]
            with st.form(f"edit_pr_{pr_id}"):
                ename = st.text_input("Name", value=pr_name)
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

        with tab_mem:
            if n_chunks == 0:
                st.info("No memory chunks reference this project.")
            else:
                df_mem = q(
                    """
                    SELECT id, category::text AS category, content, confidence, created_at
                    FROM memory_chunks
                    WHERE project_name = %s
                    ORDER BY created_at DESC NULLS LAST
                    LIMIT 500
                    """,
                    (pr_name,),
                )
                render_export_buttons(
                    df_mem, key_prefix=f"proj_{pr_id}_mem",
                    filename_base=f"{pr_name}_memory",
                    title=f"Memory chunks · {pr_name}",
                )
                sel = st.dataframe(
                    df_mem, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row",
                    column_config={
                        "id":         st.column_config.NumberColumn("ID", width="small"),
                        "category":   st.column_config.TextColumn("Category", width="small"),
                        "content":    st.column_config.TextColumn("Content", width="large"),
                        "confidence": st.column_config.NumberColumn("Conf", format="%.2f", width="small"),
                        "created_at": st.column_config.DatetimeColumn("Created", width="small"),
                    },
                    key=f"df_proj_{pr_id}_mem",
                )
                if sel.selection and sel.selection.rows:
                    go_to_detail("memory_chunk", int(df_mem.iloc[sel.selection.rows[0]]["id"]))
                if n_chunks > 500:
                    st.caption(f"Showing newest 500 of {n_chunks:,}.")

        with tab_conv:
            if n_convs == 0:
                st.info("No conversations recorded for this project. "
                        "Conversations are matched on `project_path`; if your "
                        "repo's name contains hyphens that have been mangled "
                        "to slashes by the ingest pipeline, check the JSONL "
                        "files for the real `cwd` value.")
            else:
                df_conv = q(
                    """
                    SELECT id, summary, model, started_at, message_count, project_path
                    FROM conversations
                    WHERE project_name = %(name)s
                       OR project_path ILIKE '%%/' || %(name)s || '/%%'
                       OR project_path ILIKE '%%/' || %(name)s
                       OR project_path ILIKE '%%/' || %(var)s  || '/%%'
                       OR project_path ILIKE '%%/' || %(var)s
                    ORDER BY started_at DESC NULLS LAST
                    LIMIT 500
                    """,
                    {"name": pr_name, "var": name_var},
                )
                render_export_buttons(
                    df_conv, key_prefix=f"proj_{pr_id}_conv",
                    filename_base=f"{pr_name}_conversations",
                    title=f"Conversations · {pr_name}",
                )
                sel = st.dataframe(
                    df_conv, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row",
                    column_config={
                        "id":            st.column_config.NumberColumn("ID", width="small"),
                        "summary":       st.column_config.TextColumn("Summary", width="large"),
                        "model":         st.column_config.TextColumn("Model", width="small"),
                        "started_at":    st.column_config.DatetimeColumn("Started", width="small"),
                        "message_count": st.column_config.NumberColumn("Messages", width="small"),
                        "project_path":  st.column_config.TextColumn("Path", width="medium"),
                    },
                    key=f"df_proj_{pr_id}_conv",
                )
                if sel.selection and sel.selection.rows:
                    go_to_detail("conversation", int(df_conv.iloc[sel.selection.rows[0]]["id"]))
                if n_convs > 500:
                    st.caption(f"Showing newest 500 of {n_convs:,}.")

        with tab_ent:
            if n_ents == 0:
                st.info("No knowledge-graph entities tagged with this project.")
            else:
                df_ent = q(
                    """
                    SELECT id, entity_type, name, mention_count, confidence
                    FROM entities
                    WHERE project_name = %s
                    ORDER BY mention_count DESC NULLS LAST, name
                    LIMIT 500
                    """,
                    (pr_name,),
                )
                render_export_buttons(
                    df_ent, key_prefix=f"proj_{pr_id}_ent",
                    filename_base=f"{pr_name}_entities",
                    title=f"Entities · {pr_name}",
                )
                sel = st.dataframe(
                    df_ent, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row",
                    column_config={
                        "id":            st.column_config.NumberColumn("ID", width="small"),
                        "entity_type":   st.column_config.TextColumn("Type", width="small"),
                        "name":          st.column_config.TextColumn("Name", width="large"),
                        "mention_count": st.column_config.NumberColumn("Mentions", width="small"),
                        "confidence":    st.column_config.NumberColumn("Conf", format="%.2f", width="small"),
                    },
                    key=f"df_proj_{pr_id}_ent",
                )
                if sel.selection and sel.selection.rows:
                    go_to_detail("entity", int(df_ent.iloc[sel.selection.rows[0]]["id"]))

        with tab_sk:
            if n_skills == 0:
                st.info("No project-local skills found. Match is by directory "
                        "component (`…/<name>/.claude/skills/…`); skills under "
                        "`~/.claude/skills/` are global and shown on the Skills page.")
            else:
                df_sk = q(
                    """
                    SELECT id, name, version, description, use_count, last_used, path
                    FROM skills
                    WHERE path ILIKE '%%/' || %(name)s || '/%%'
                       OR path ILIKE '%%/' || %(name)s
                       OR path ILIKE '%%/' || %(var)s  || '/%%'
                       OR path ILIKE '%%/' || %(var)s
                    ORDER BY use_count DESC NULLS LAST, name
                    LIMIT 500
                    """,
                    {"name": pr_name, "var": name_var},
                )
                render_export_buttons(
                    df_sk, key_prefix=f"proj_{pr_id}_sk",
                    filename_base=f"{pr_name}_skills",
                    title=f"Skills · {pr_name}",
                )
                sel = st.dataframe(
                    df_sk, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row",
                    column_config={
                        "id":          st.column_config.NumberColumn("ID", width="small"),
                        "name":        st.column_config.TextColumn("Name", width="medium"),
                        "version":     st.column_config.TextColumn("Ver", width="small"),
                        "description": st.column_config.TextColumn("Description", width="large"),
                        "use_count":   st.column_config.NumberColumn("Uses", width="small"),
                        "last_used":   st.column_config.DatetimeColumn("Last used", width="small"),
                        "path":        st.column_config.TextColumn("Path", width="medium"),
                    },
                    key=f"df_proj_{pr_id}_sk",
                )
                if sel.selection and sel.selection.rows:
                    go_to_detail("skill", int(df_sk.iloc[sel.selection.rows[0]]["id"]))

        with tab_pr:
            if n_prompts == 0:
                st.info("No prompts found whose source path contains this project name. "
                        "`prompts.source_path` typically points at a project's "
                        "`CLAUDE.md`; if your repo doesn't have one, none will be linked.")
            else:
                df_pr = q(
                    """
                    SELECT id, name, category, content, usage_count, source_path
                    FROM prompts
                    WHERE source_path ILIKE '%%/' || %(name)s || '/%%'
                       OR source_path ILIKE '%%/' || %(name)s
                       OR source_path ILIKE '%%/' || %(var)s  || '/%%'
                       OR source_path ILIKE '%%/' || %(var)s
                    ORDER BY usage_count DESC NULLS LAST, name
                    LIMIT 500
                    """,
                    {"name": pr_name, "var": name_var},
                )
                render_export_buttons(
                    df_pr, key_prefix=f"proj_{pr_id}_pr",
                    filename_base=f"{pr_name}_prompts",
                    title=f"Prompts · {pr_name}",
                )
                sel = st.dataframe(
                    df_pr, use_container_width=True, hide_index=True,
                    on_select="rerun", selection_mode="single-row",
                    column_config={
                        "id":          st.column_config.NumberColumn("ID", width="small"),
                        "name":        st.column_config.TextColumn("Name", width="medium"),
                        "category":    st.column_config.TextColumn("Category", width="small"),
                        "content":     st.column_config.TextColumn("Content", width="large"),
                        "usage_count": st.column_config.NumberColumn("Uses", width="small"),
                        "source_path": st.column_config.TextColumn("Path", width="medium"),
                    },
                    key=f"df_proj_{pr_id}_pr",
                )
                if sel.selection and sel.selection.rows:
                    go_to_detail("prompt", int(df_pr.iloc[sel.selection.rows[0]]["id"]))

        with tab_refl:
            if n_refl == 0:
                st.info("No reflection events have touched this project's chunks.")
            else:
                df_refl = q(
                    """
                    SELECT DISTINCT r.id, r.reflection_type, r.action_taken,
                           r.reasoning, r.confidence, r.created_at
                    FROM memory_reflections r
                    JOIN unnest(r.affected_chunks) AS ac ON TRUE
                    JOIN memory_chunks mc ON mc.id = ac
                    WHERE mc.project_name = %s
                    ORDER BY r.created_at DESC
                    LIMIT 500
                    """,
                    (pr_name,),
                )
                render_export_buttons(
                    df_refl, key_prefix=f"proj_{pr_id}_refl",
                    filename_base=f"{pr_name}_reflections",
                    title=f"Reflections · {pr_name}",
                )
                st.dataframe(
                    df_refl, use_container_width=True, hide_index=True,
                    column_config={
                        "id":              st.column_config.NumberColumn("ID", width="small"),
                        "reflection_type": st.column_config.TextColumn("Type", width="small"),
                        "action_taken":    st.column_config.TextColumn("Action", width="small"),
                        "reasoning":       st.column_config.TextColumn("Reasoning", width="large"),
                        "confidence":      st.column_config.NumberColumn("Conf", format="%.2f", width="small"),
                        "created_at":      st.column_config.DatetimeColumn("When", width="small"),
                    },
                )
                if n_refl > 500:
                    st.caption(f"Showing newest 500 of {n_refl:,}.")

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
            if DEMO_MODE:
                _demo_disabled_button("Forget", key=f"del_e_{ent_id}",
                    reason="Disabled in demo mode — would cascade-delete the entity + its mentions + relationships.")
            elif st.button(
                "Forget",
                key=f"del_e_{ent_id}",
                use_container_width=True,
                help="Cascade-delete this entity, its mentions and relationships, with audit row in memory_reflections.",
            ):
                try:
                    res = forget_entity(_live_conn(), ent_id, reason="GUI entity detail forget")
                    st.toast(
                        f"Forgotten — {res['mentions']} mention(s), "
                        f"{res['relationships']} rel(s) — audit #{res['reflection_id']}"
                    )
                except Exception as e:
                    st.toast(f"Error: {e}", icon="⚠️")
                else:
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
    from gui.page_views.dashboard import render as _render_dashboard
    _render_dashboard()

elif page == "Calendar":
    from gui.page_views.calendar import render as _render_calendar
    _render_calendar()

elif page == "Search":
    from gui.page_views.search import render as _render_search
    _render_search()

elif page == "Semantic":
    from gui.page_views.semantic import render as _render_semantic
    _render_semantic()

elif page == "Conversations":
    from gui.page_views.conversations import render as _render_conversations
    _render_conversations()

elif page == "Memory":
    from gui.page_views.memory import render as _render_memory
    _render_memory()

elif page == "Memory Health":
    from gui.page_views.memory_health import render as _render_memory_health
    _render_memory_health()

elif page == "Skills":
    # Page body extracted to gui/page_views/skills.py — see gui/page_views/__init__.py
    # for the migration recipe. The remaining `elif page == …` bodies
    # below will be ported following the same pattern.
    from gui.page_views.skills import render as _render_skills
    _render_skills()

elif page == "Knowledge Graph":
    from gui.page_views.knowledge_graph import render as _render_knowledge_graph
    _render_knowledge_graph()

elif page == "Projects":
    from gui.page_views.projects import render as _render_projects
    _render_projects()

elif page == "Prompts":
    from gui.page_views.prompts import render as _render_prompts
    _render_prompts()

elif page == "Scheduler":
    from gui.page_views.scheduler import render as _render_scheduler
    _render_scheduler()

elif page == "Ingestion":
    from gui.page_views.ingestion import render as _render_ingestion
    _render_ingestion()

elif page == "SQL":
    from gui.page_views.sql import render as _render_sql
    _render_sql()
