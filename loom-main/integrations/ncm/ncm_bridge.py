#!/usr/bin/env python3
"""loom <-> Night City Motorsports relay.

The browser is sandboxed: a page can't poke a game process or write into the CET
mod folder (and a Pages https page can't even hit localhost). So this tiny local
relay is the only thing in the middle. It:

  * serves docs/ncm.html at http://localhost:8777  (so the portal is same-origin),
  * POST /cmd  -> writes <mod>/bridge/command.txt  (the in-game loom_bridge.lua reads it),
  * GET  /state -> returns <mod>/bridge/state.json  (what the mod published),
  * GET  /ping  -> lets the page light up LIVE mode.

No game controls cross this line — only STATE (open/stage/start/abort) and a
read-only snapshot. Stdlib only, in the spirit of the decoder.

    python integrations/ncm/ncm_bridge.py --mod "<...>/cyber_engine_tweaks/mods/MT_Ecosystem"

If --mod is omitted (or NCM_MOD_DIR unset) it still serves the portal in REPLAY mode.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from lap_review import review, example_session  # noqa: E402  (same dir)
DOCS = os.path.normpath(os.path.join(ROOT, "..", "..", "docs"))
STALE_SECONDS = 6.0  # if state.json is older than this, the game isn't ticking


class Bridge:
    def __init__(self, mod_dir: str) -> None:
        base = mod_dir if mod_dir else ROOT
        self.dir = os.path.join(base, "bridge")
        os.makedirs(self.dir, exist_ok=True)
        self.cmd_path = os.path.join(self.dir, "command.txt")
        self.state_path = os.path.join(self.dir, "state.json")
        self.sessions_dir = os.path.join(mod_dir, "telemetry", "sessions") if mod_dir else ""
        self.live = bool(mod_dir)
        self.seq = 0

    def list_sessions(self) -> list:
        out = []
        if self.sessions_dir and os.path.isdir(self.sessions_dir):
            for fn in sorted(os.listdir(self.sessions_dir), reverse=True):
                if not fn.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(self.sessions_dir, fn), encoding="utf-8") as f:
                        d = json.load(f)
                    out.append({"id": fn[:-5], "route": d.get("routeDisplayName") or d.get("routeId") or fn,
                                "best": d.get("bestLap")})
                except (OSError, json.JSONDecodeError, ValueError):
                    pass
        out.append({"id": "example", "route": "Watson Northside Loop (demo)", "best": 102.4})
        return out

    def review_session(self, sid: str) -> dict:
        if sid and sid != "example" and self.sessions_dir:
            fp = os.path.normpath(os.path.join(self.sessions_dir, sid + ".json"))
            if fp.startswith(os.path.normpath(self.sessions_dir)) and os.path.isfile(fp):
                try:
                    with open(fp, encoding="utf-8") as f:
                        return review(json.load(f))
                except (OSError, json.JSONDecodeError, ValueError):
                    pass
        return review(example_session())

    def send(self, intent: str, args: str = "") -> int:
        self.seq += 1
        with open(self.cmd_path, "w", encoding="utf-8") as f:
            f.write(f"seq={self.seq}\nintent={intent}\nargs={args}\n")
        return self.seq

    def read_state(self) -> dict:
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.loads(f.read())
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            return {"connected": False, "fresh": False}
        ts = float(data.get("ts", 0) or 0)
        data["fresh"] = bool(ts) and (time.time() - ts) < STALE_SECONDS
        return data


BR: Bridge = None  # type: ignore


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body, ctype: str = "application/json") -> None:
        b = body if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.startswith("/ping"):
            return self._send(200, json.dumps({"ok": True, "mode": "live" if BR.live else "replay", "seq": BR.seq}))
        if path.startswith("/state"):
            return self._send(200, json.dumps(BR.read_state()))
        if path.startswith("/sessions"):
            return self._send(200, json.dumps(BR.list_sessions()))
        if path.startswith("/session"):
            sid = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("id", [""])[0]
            return self._send(200, json.dumps(BR.review_session(sid)))
        # static (the portal + anything else under docs/)
        rel = "ncm.html" if path in ("/", "") else path.lstrip("/")
        fp = os.path.normpath(os.path.join(DOCS, rel))
        if not fp.startswith(DOCS) or not os.path.isfile(fp):
            return self._send(404, "not found", "text/plain")
        ctype = "text/html" if fp.endswith(".html") else "application/octet-stream"
        with open(fp, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_POST(self) -> None:
        if not self.path.startswith("/cmd"):
            return self._send(404, "{}")
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            data = json.loads(self.rfile.read(n) or b"{}") if n else {}
        except (json.JSONDecodeError, ValueError):
            data = {}
        intent = str(data.get("intent", "")).strip()
        if intent not in {"open", "stage", "start", "quali", "abort"}:
            return self._send(400, json.dumps({"error": "bad intent", "got": intent}))
        if not BR.live:
            return self._send(409, json.dumps({"error": "replay mode — no game attached (run with --mod)"}))
        seq = BR.send(intent, str(data.get("args", "")))
        self._send(200, json.dumps({"ok": True, "seq": seq, "intent": intent}))

    def log_message(self, *_a) -> None:  # quiet
        pass


def main() -> None:
    global BR
    ap = argparse.ArgumentParser(description="loom <-> Night City Motorsports relay")
    ap.add_argument("--mod", default=os.environ.get("NCM_MOD_DIR", ""),
                    help="path to the MT_Ecosystem mod folder (omit for replay-only)")
    ap.add_argument("--port", type=int, default=8777)
    args = ap.parse_args()
    BR = Bridge(args.mod)
    mode = f"LIVE  (mod: {args.mod})" if BR.live else "REPLAY  (no --mod given)"
    print(f"loom·ncm relay  ->  http://localhost:{args.port}   [{mode}]")
    print(f"bridge files: {BR.dir}")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
