"""Regression test for ``gui/app.py``'s import-path bootstrap.

Streamlit launches ``streamlit run gui/app.py`` with only the *script's*
directory (``gui/``) guaranteed on ``sys.path`` — not the repo root. Older
Streamlit releases also added the CWD, which masked the fact that
``gui/app.py`` imports ``gui.page_views`` (a package that lives at the repo
root and is deliberately excluded from the installed wheel). On Streamlit
≥1.x this regressed into ``ModuleNotFoundError: No module named 'gui'``.

``app.py`` must therefore put the repo root on ``sys.path`` *before* it
imports ``gui.page_views``. This test reproduces Streamlit's launch path in a
subprocess and asserts the real header of ``app.py`` resolves the import.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "gui" / "app.py"
GUI_DIR = ROOT / "gui"


def test_app_header_resolves_gui_package_without_repo_root_on_path(tmp_path):
    src = APP_PY.read_text().splitlines()
    # Take the file header up to and including the gui.page_views import — the
    # bootstrap under test must live before that line for the import to work.
    cut = next(i for i, line in enumerate(src) if "from gui.page_views import" in line)
    header = "\n".join(src[: cut + 1])

    prog = "\n".join(
        [
            "import sys, os",
            # Mimic Streamlit: drop the repo root, then prepend the script's dir.
            f"sys.path = [p for p in sys.path if os.path.realpath(p) != os.path.realpath({str(ROOT)!r})]",
            f"sys.path.insert(0, {str(GUI_DIR)!r})",
            f"exec(compile({header!r}, {str(APP_PY)!r}, 'exec'))",
            "print('IMPORT_OK')",
        ]
    )

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    result = subprocess.run(
        [sys.executable, "-c", prog],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),  # not the repo root, so CWD can't smuggle it onto the path
        env=env,
    )
    assert "IMPORT_OK" in result.stdout, (
        "gui/app.py failed to import gui.page_views when launched the way "
        f"Streamlit launches it.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
