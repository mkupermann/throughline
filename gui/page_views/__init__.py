"""Streamlit page modules — one ``elif page == "X"`` body per file.

Each module exports a single ``render()`` function with no arguments and
fetches all helpers / constants from ``app_ns()`` below.

Why a registered-globals proxy instead of ``from gui.app import …``?
Streamlit runs ``gui/app.py`` as the entry script — a second
``from gui.app import …`` would re-execute it under the package name
and re-run every Streamlit call at module scope (notably the sidebar's
``st.radio("nav", …)``), tripping ``StreamlitDuplicateElementId``.

We also can't just read ``sys.modules["__main__"]``: in some Streamlit
versions the script runs inside an ``exec`` namespace that is *not*
installed under that key, so the lookup returns a module without
``st``/``q``/etc. and every page-helper access raises ``AttributeError``.

Instead, ``gui/app.py`` calls ``register_app(globals())`` once at
import time, handing us a live reference to its globals dict. Pages
read symbols through a proxy that looks them up in that dict on every
attribute access, so helpers defined *after* registration (``q``,
``page_header``, …) are still visible.

Adding a new page module:

1. Copy the ``elif page == "Foo":`` block out of ``gui/app.py`` into
   ``gui/page_views/foo.py`` as ``def render() -> None:``. Indent one level.
2. At the top of ``render()``, pull symbols off the app namespace::

       def render() -> None:
           app = app_ns()
           st, q, page_header = app.st, app.q, app.page_header
           # ... use them as before

3. In ``gui/app.py``, replace the ``elif`` body with::

       elif page == "Foo":
           from gui.page_views.foo import render as _render_foo
           _render_foo()
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

_app_globals: dict[str, Any] | None = None


def register_app(g: dict[str, Any]) -> None:
    """Called once from ``gui/app.py`` with its ``globals()`` dict.

    Pages then read ``st``/``q``/``page_header``/… through ``app_ns()``,
    which proxies attribute access into this live dict. The dict is the
    actual running namespace — any binding added later in ``app.py`` is
    immediately visible to pages.
    """
    global _app_globals
    _app_globals = g


class _AppProxy:
    """Read-only attribute view over the running ``gui/app.py`` globals."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        if _app_globals is None:
            raise RuntimeError(
                "gui.page_views.register_app(globals()) was never called from gui/app.py — "
                "page modules cannot resolve helpers without it."
            )
        try:
            return _app_globals[name]
        except KeyError as exc:
            raise AttributeError(
                f"gui/app.py has no global named {name!r}"
            ) from exc


_proxy = _AppProxy()


def app_ns() -> Any:
    """Return a proxy that exposes the live ``gui/app.py`` globals.

    The return type is intentionally ``Any``-ish (``SimpleNamespace``-shaped
    but backed by a live dict) so callers can write ``app.st``, ``app.q``,
    etc. without juggling dict lookups.
    """
    return _proxy


# ``SimpleNamespace`` import kept for backward-compat in case external
# code imported it from this module's earlier revision.
__all__ = ["register_app", "app_ns", "SimpleNamespace"]
