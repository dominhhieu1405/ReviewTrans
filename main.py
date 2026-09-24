from __future__ import annotations

import sys
from pathlib import Path


def _entry() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-check":
        from reviewtrans.selfcheck import run

        return run(Path(sys.argv[2]), sys.argv[3:])
    from reviewtrans.ui.app import main

    return main()


if __name__ == "__main__":
    sys.exit(_entry())
