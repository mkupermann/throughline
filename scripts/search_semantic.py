"""Compatibility alias for throughline.jobs.search_semantic."""

import sys
from pathlib import Path

_root = str(Path(__file__).resolve().parents[1])
if _root not in sys.path:
    sys.path.insert(0, _root)

from throughline.jobs import search_semantic as _job

sys.modules[__name__] = _job


if __name__ == "__main__":
    result = _job.main()
    if isinstance(result, int):
        raise SystemExit(result)
