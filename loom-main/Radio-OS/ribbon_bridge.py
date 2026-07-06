"""RibbonOS event bridge — the seam between a Radio OS station (Python, headless) and the RibbonOS
ribbon (Godot).

The front/back split made concrete: **bookmark.py / Radio OS produces state; RibbonOS renders the
feeling.** This emits a one-way JSON-lines event stream over UDP localhost; the Godot `RadioBridge`
autoload consumes it and drives the ribbon's reactions (heat → intensity, subtitle → caption,
transition/transport → ripple).

The contract — five event types:

    {"type":"heat","value":0.0-1.0,"source":"..."}
    {"type":"subtitle","text":"...","voice":"host"}
    {"type":"transition","reason":"topic_shift","style":"news_desk"}
    {"type":"transport","action":"play|pause|fast_forward|rewind","rate":1.0}
    {"type":"station","id":"...","name":"..."}

Run the demo (no station needed) and watch the ribbon react in Godot:

    python ribbon_bridge.py --demo

The demo drives the ribbon from the REAL signal-heat engine (`signal_heat.py`): it bumps a source on
scripted bursts and emits the genuinely-decaying heat, so what the ribbon does is what the engine says.
"""
from __future__ import annotations

import json
import math
import socket
import time
from typing import Any, Dict, Optional

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 47921


# ── the contract (pure builders — testable without a socket) ─────────────────
def heat_event(value: float, source: str = "") -> Dict[str, Any]:
    return {"type": "heat", "value": round(max(0.0, min(1.0, float(value))), 4), "source": str(source)}


def subtitle_event(text: str, voice: str = "host") -> Dict[str, Any]:
    return {"type": "subtitle", "text": str(text), "voice": str(voice)}


def transition_event(reason: str, style: str = "") -> Dict[str, Any]:
    return {"type": "transition", "reason": str(reason), "style": str(style)}


def transport_event(action: str, rate: float = 1.0) -> Dict[str, Any]:
    return {"type": "transport", "action": str(action), "rate": float(rate)}


def station_event(station_id: str, name: str) -> Dict[str, Any]:
    return {"type": "station", "id": str(station_id), "name": str(name)}


def encode(event: Dict[str, Any]) -> bytes:
    """One JSON object per line — the wire format the Godot side splits on '\\n'."""
    return (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")


class RibbonStream:
    """A thin UDP sender to the RibbonOS ribbon."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, event: Dict[str, Any]) -> None:
        self.sock.sendto(encode(event), self.addr)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# ── demo: drive the ribbon from the real heat engine ─────────────────────────
_DEMO_LINES = [
    ("host", "Good evening — you're tuned into the simulation."),
    ("host", "The trading desk is quiet for now. We're watching."),
    ("host", "Breaking — the desk just lit up. Cosmo and Wanda surging into first."),
    ("host", "Momentum building. Timmy answers with a compression entry of his own."),
    ("host", "And it cools. The desk settles, the standings hold for a beat."),
    ("host", "Elsewhere, a quiet thread stirs — weather shifting over the harbor."),
]


def run_demo(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, seconds: float = 9999.0) -> None:
    """Emit a living stream: real decaying heat (bumped on bursts) + narration + transitions."""
    import signal_heat  # the real engine drives the ribbon

    stream = RibbonStream(host, port)
    cfg = signal_heat.normalize_heat_config({"half_life_sec": 6.0}, {})
    state: Dict[str, Any] = {}
    stream.send(station_event("DemoFM", "Demo FM"))
    print(f"ribbon_bridge: streaming demo → udp://{host}:{port} (Ctrl-C to stop)")

    t0 = time.time()
    line_i = 0
    next_line = t0 + 1.0
    next_burst = t0 + 3.0
    try:
        while time.time() - t0 < seconds:
            now = time.time()
            # scripted bursts: a source "lights up", then the engine decays it
            if now >= next_burst:
                signal_heat.bump_heat(state, {"source": "trades", "priority": 95}, cfg, now)
                stream.send(transition_event("heat_change", "sports_broadcast"))
                next_burst = now + 7.0
            heat = signal_heat.source_heat(state, "trades", cfg, now)
            stream.send(heat_event(heat, "trades"))
            # narration cadence
            if now >= next_line:
                voice, text = _DEMO_LINES[line_i % len(_DEMO_LINES)]
                stream.send(subtitle_event(text, voice))
                line_i += 1
                next_line = now + 3.2
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nribbon_bridge: stopped")
    finally:
        stream.close()


def main(argv) -> int:
    host, port = DEFAULT_HOST, DEFAULT_PORT
    if "--port" in argv:
        port = int(argv[argv.index("--port") + 1])
    if "--demo" in argv:
        run_demo(host, port)
        return 0
    print(__doc__)
    print("\n(no --demo given; nothing to do)")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
