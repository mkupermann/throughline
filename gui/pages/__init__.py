"""Streamlit page modules — one ``elif page == "X"`` body per file.

Each module exports a single ``render()`` function with no arguments and
fetches all helpers / constants from ``app_ns()`` below.

Why ``app_ns()`` instead of ``from gui.app import …``? Streamlit runs
``gui/app.py`` as the *main* script — its module name is ``__main__``,
not ``gui.app``. A page module that does ``from gui.app import q``
forces Python to import ``gui/app.py`` a *second* time under the proper
package name. That second execution re-runs every Streamlit call at
module scope (notably the sidebar's ``st.radio("nav", …)``) and the
runtime raises ``StreamlitDuplicateElementId``. Routing through the
already-loaded ``__main__`` module avoids the re-import entirely.

Adding a new page module:

1. Copy the ``elif page == "Foo":`` block out of ``gui/app.py`` into
   ``gui/pages/foo.py`` as ``def render() -> None:``. Indent everything
   one level.
2. At the top of ``render()``, pull symbols off the main app namespace::

       def render() -> None:
           app = app_ns()
           st, q, page_header = app.st, app.q, app.page_header
           # ... use them as before

3. In ``gui/app.py``, replace the ``elif`` body with::

       elif page == "Foo":
           from gui.pages.foo import render as _render_foo
           _render_foo()

4. Run the GUI and click the page in the sidebar to verify identical
   behaviour. Pages don't share state with each other, so they can be
   migrated one at a time without breaking the unmigrated ones.
"""

from __future__ import annotations

import sys
from types import ModuleType


def app_ns() -> ModuleType:
    """Return the running ``gui/app.py`` module so pages can read helpers from it.

    Streamlit executes the entry script as ``__main__``; that's where
    every helper, constant, and cached DB connection lives. Importing
    ``gui.app`` instead would re-execute the file and trip
    ``StreamlitDuplicateElementId`` on the sidebar widgets.
    """
    return sys.modules["__main__"]
