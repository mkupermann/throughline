"""Regression tests for ``gui/page_views``' app-globals proxy.

The proxy was introduced because ``sys.modules["__main__"]`` is not
guaranteed to point at the running ``gui/app.py`` namespace on every
Streamlit version. ``register_app(globals())`` hands the proxy a live
reference; pages then read symbols through ``app_ns().<attr>``.

These tests pin the contract without standing up Streamlit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# gui/ sits next to throughline/ at the repo root.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _fresh_gui_pages():
    """Reload ``gui.page_views`` so module-level state (``_app_globals``) resets."""
    import importlib
    # Drop cached module if present so we start with _app_globals = None.
    if "gui.page_views" in sys.modules:
        del sys.modules["gui.page_views"]
    if "gui" in sys.modules:
        # Re-importing the parent ensures namespace packages resolve cleanly.
        del sys.modules["gui"]
    return importlib.import_module("gui.page_views")


class TestAppNsProxy:
    def test_proxy_reads_live_globals_after_registration(self):
        pages = _fresh_gui_pages()
        fake_globals = {"st": "stub-streamlit", "q": lambda sql: [], "TEXT": "#fff"}
        pages.register_app(fake_globals)
        ns = pages.app_ns()
        assert ns.st == "stub-streamlit"
        assert ns.TEXT == "#fff"
        # Callables round-trip.
        assert ns.q("SELECT 1") == []

    def test_proxy_sees_later_mutations_of_the_same_dict(self):
        pages = _fresh_gui_pages()
        live = {"st": 1}
        pages.register_app(live)
        ns = pages.app_ns()
        live["q"] = "added later"
        # The proxy reads on every attribute access, not at registration time,
        # so symbols defined later in app.py are visible to pages.
        assert ns.q == "added later"

    def test_missing_attr_raises_clear_AttributeError(self):
        pages = _fresh_gui_pages()
        pages.register_app({"st": object()})
        ns = pages.app_ns()
        with pytest.raises(AttributeError, match="no global named"):
            _ = ns.does_not_exist

    def test_unregistered_proxy_raises_runtime_error(self):
        # Force a fresh module so _app_globals starts as None.
        pages = _fresh_gui_pages()
        ns = pages.app_ns()
        with pytest.raises(RuntimeError, match="register_app"):
            _ = ns.st
