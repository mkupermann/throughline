"""Enable ``python -m throughline <command>`` invocation."""

from __future__ import annotations

import sys

from throughline.cli import main


if __name__ == "__main__":
    sys.exit(main())
