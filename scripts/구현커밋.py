#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import os
import sys


CANONICAL_ENTRYPOINT = Path(__file__).resolve().with_name("codex_flow.py")


def main() -> int:
    """Korean alias wrapper for the canonical 구현커밋 runtime."""
    if not CANONICAL_ENTRYPOINT.exists():
        sys.stderr.write(f"구현커밋 runtime was not found at {CANONICAL_ENTRYPOINT}\n")
        return 127
    os.execv(sys.executable, [sys.executable, str(CANONICAL_ENTRYPOINT), *sys.argv[1:]])
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
