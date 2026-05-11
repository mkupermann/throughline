"""Streamlit page modules — one ``elif page == "X"`` body per file.

Each module exports a single ``render()`` function with no arguments. The
function depends on the same module-level globals that ``gui/app.py``
historically defined (``st``, ``q``, ``dml``, ``page_header``,
``render_export_buttons``, ``go_to_detail``, the colour constants, …),
so a page module just imports them from ``gui.app`` at function-call
time.

Adding a new page module:

1. Copy the ``elif page == "Foo":`` block out of ``gui/app.py`` into
   ``gui/pages/foo.py`` as ``def render() -> None:``. Indent everything
   one level. Add ``from gui.app import <names>`` for every symbol the
   block touches.
2. In ``gui/app.py``, replace the ``elif`` body with::

       elif page == "Foo":
           from gui.pages.foo import render as _render_foo
           _render_foo()

3. Run the GUI and click the page in the sidebar to verify identical
   behaviour. Pages don't share state with each other, so they can be
   migrated one at a time without breaking the unmigrated ones.

The currently-extracted pages (see ``gui/pages/*.py``) are the proof of
concept; the remaining bodies still live in ``gui/app.py`` and will be
ported in follow-up commits.
"""
