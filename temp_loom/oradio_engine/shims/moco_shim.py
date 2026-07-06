"""MoCo shim — the LIVE 'sense' organ (real-world motion -> classified intent).

MoCo reads live motion and classifies it into controller intent. At runtime it writes a JSON
telemetry SNAPSHOT (overwritten in place ~5x/sec) at logs/ui_runtime/<process_id>.json with:
  - ``recognition``: committed_label / top_label + top_score   (the classification)
  - ``output``: active_action, axes, active_buttons             (the controller intent)

So the adapter is a ``LiveSource`` that polls that snapshot and emits a candidate when the
classified intent *changes* (snapshots repeat ~5x/sec; we want transitions, not frames). It
reuses the proven ``LiveFeedOrgan`` for tape/record/replay — MoCo is the canonical LIVE source
(the input is *you moving*: nondeterministic, recorded once, replayable from the tape). No
mediapipe needed to build or test the adapter; only MoCo's own runtime needs py3.11.

    make_moco_organ(name, telemetry_path=...)  -> LiveFeedOrgan over MoCoTelemetrySource

The classifier's ``top_score`` is the natural confidence — MoCo's classifications are gradable
claims, so this is a future provider for the evidence service too (resolution = a later join).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from oradio_engine.live import LiveFeedOrgan


def _read_telemetry_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None  # mid-write; the writer uses atomic replace, but be defensive


def _intent_from_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract the classified intent from a MoCo runtime payload."""
    recognition = payload.get("recognition") or {}
    output = payload.get("output") or {}
    label = recognition.get("committed_label") or recognition.get("top_label")
    action = output.get("active_action")
    buttons = tuple(output.get("active_buttons") or [])
    if not label and not action and not buttons:
        return None  # no committed intent this frame
    score = float(recognition.get("top_score") or 0.0)
    return {
        "label": label,
        "action": action,
        "buttons": buttons,
        "score": score,
        "axes": output.get("axes") or {},
        "ts": payload.get("updated_at"),
    }


class MoCoTelemetrySource:
    """Polls MoCo's telemetry snapshot and emits a candidate on each intent *change*."""

    def __init__(
        self,
        *,
        telemetry_path: Optional[str] = None,
        reader: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    ) -> None:
        if reader is None and telemetry_path is None:
            raise ValueError("MoCoTelemetrySource needs a telemetry_path or a reader")
        self._reader = reader or (lambda p=Path(telemetry_path): _read_telemetry_file(p))
        self._last_key: Optional[Tuple] = None

    def poll(self) -> List[Dict[str, Any]]:
        payload = self._reader()
        if not payload:
            return []
        intent = _intent_from_payload(payload)
        if intent is None:
            return []
        key = (intent["label"], intent["action"], intent["buttons"])
        if key == self._last_key:
            return []  # unchanged intent — not a new event
        self._last_key = key
        label = intent["label"] or intent["action"] or "intent"
        return [{
            "title": str(label),
            "body": f"action={intent['action']} score={intent['score']:.2f} axes={intent['axes']}",
            "type": "intent",
            "priority": intent["score"],
            "ts": intent["ts"],
            "tags": ["moco", str(intent["label"] or ""), str(intent["action"] or "")],
        }]


def make_moco_organ(
    name: str = "moco",
    *,
    telemetry_path: Optional[str] = None,
    reader: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
) -> LiveFeedOrgan:
    """Build a LIVE MoCo organ over its telemetry snapshot (or an injected reader)."""
    source = MoCoTelemetrySource(telemetry_path=telemetry_path, reader=reader)
    return LiveFeedOrgan(name, source=source)
