#!/usr/bin/env python3
"""Export shared Loom query codec assets and embed the official spec in single-file HTML surfaces."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOOM_MAIN = ROOT / "loom-main"
if str(LOOM_MAIN) not in sys.path:
    sys.path.insert(0, str(LOOM_MAIN))

from oradio_engine.codec import codec_manifest_dict, js_codec_snippet  # noqa: E402
from oradio_engine.packet import CODEC_VERSION, PACKET_VERSION  # noqa: E402

MARKER_START = "/* OFFICIAL_CODEC_SPEC_START */"
MARKER_END = "/* OFFICIAL_CODEC_SPEC_END */"


def replace_between(text: str, payload: str) -> str:
    start = text.find(MARKER_START)
    end = text.find(MARKER_END)
    if start < 0 or end < 0 or end < start:
        raise ValueError("official codec markers not found")
    head = text[: start + len(MARKER_START)]
    tail = text[end:]
    return head + "\n" + payload + "\n" + tail


def main() -> int:
    manifest = codec_manifest_dict()
    artifacts_dir = ROOT / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "loom_query_codec_manifest.json").write_text(
        json.dumps(
            {
                "packet_version": PACKET_VERSION,
                "codec_version": CODEC_VERSION,
                "codec_manifest": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (artifacts_dir / "loom_query_codec_assets.js").write_text(
        js_codec_snippet() + f"\nconst OFFICIAL_PACKET_VERSION={json.dumps(PACKET_VERSION)};\n",
        encoding="utf-8",
    )
    payload = js_codec_snippet() + f"\nconst OFFICIAL_PACKET_VERSION={json.dumps(PACKET_VERSION)};"
    for rel in ("booth-timestamp-pitch.html", "loom_timestamp_decoder.html"):
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")
        path.write_text(replace_between(text, payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
