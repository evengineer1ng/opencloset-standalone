#!/usr/bin/env python3
"""Compatibility entrypoint for the official Loom query codec."""

from __future__ import annotations

import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
LOOM_MAIN = HERE / "loom-main"
if str(LOOM_MAIN) not in sys.path:
    sys.path.insert(0, str(LOOM_MAIN))

from oradio_engine.query_codec import (  # noqa: E402
    compat_query_packet,
    export_packet,
    main_compat,
    query_tapes,
    render_evidence,
    render_meaning,
)

build_packet = compat_query_packet

__all__ = [
    "build_packet",
    "compat_query_packet",
    "export_packet",
    "main_compat",
    "query_tapes",
    "render_evidence",
    "render_meaning",
]


def main(argv: list[str] | None = None) -> int:
    return main_compat(argv)


if __name__ == "__main__":
    raise SystemExit(main())
