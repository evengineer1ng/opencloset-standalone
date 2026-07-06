"""
Radio OS — Shim Generator (the reverse shim / scout factory).

Generates a small, standalone, stdlib-only "scout" you drop into a scope (a folder, a log, a UDP
port, a command) that writes normalized observations to a RadioOS bridge folder. Radio OS's
`plugins/antenna_bridge.py` reads that folder. The two are the ends of one pipe:

    scout shim  ──writes──►  RadioOSBridge/<source>/events.jsonl  ──read by──►  antenna_bridge

Design goals (locked): **highly versatile yet lightweight.** Each generated shim is ONE file, pure
stdlib, ~50 lines, no install. And **consented + scoped**: a shim is pointed at exactly one scope
the operator chose — "I tuned an antenna to this project," never "watch my whole computer." Safe
transports are first-class; nothing captures broadly by default.

A shim only ever OBSERVES and emits raw typed observations (it never narrates, ranks, or — for a
harness — impersonates the thing it watches). That honesty is the same rule the antenna follows.

THE BRIDGE INTAKE CONTRACT (what every shim writes / antenna_bridge reads):
    * stream  : append one JSON object per line to  <bridge>/events.jsonl
                {"type": "...", "title": "...", "body": "...", "priority": 0-100, "ts": epoch, "id": "...", "tags": []}
                — all fields optional; `type` is the only thing that really matters.
    * snapshot: write the latest state to          <bridge>/state.json  (antenna diffs it)
    Default bridge dir: %USERPROFILE%/RadioOSBridge/<source>  (or an explicit path).

Transports (templates): file_watch · log_tail · command_output · udp_listen.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

# Schema both ends agree on (kept tiny on purpose).
OBSERVATION_FIELDS = ("type", "title", "body", "priority", "ts", "id", "tags")

# Shared prelude: bridge resolution + the single emit() every shim uses.
_PRELUDE = '''#!/usr/bin/env python3
"""Radio OS scout shim — transport: __TRANSPORT__ — source: __SOURCE__
Operator-placed and scoped to: __SCOPE_DESC__
Writes normalized observations to the RadioOS bridge; antenna_bridge reads them.
Stdlib only. It OBSERVES and emits typed observations — it does not narrate or impersonate.
Run:  python __FILENAME__
"""
import json, os, time

SOURCE = __SOURCE__
BRIDGE_DIR = __BRIDGE__  # explicit path, or "" → %USERPROFILE%/RadioOSBridge/<source>


def bridge_dir():
    if BRIDGE_DIR:
        return BRIDGE_DIR
    root = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(root, "RadioOSBridge", SOURCE)


def emit(obs):
    """Append one raw typed observation to events.jsonl. Heat hint = obs['priority'] (optional)."""
    d = bridge_dir()
    os.makedirs(d, exist_ok=True)
    obs.setdefault("ts", time.time())
    with open(os.path.join(d, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(obs, ensure_ascii=False) + "\\n")
'''

_BODY_FILE_WATCH = '''
SCOPE = __SCOPE__   # folder to watch (this scope only)
POLL = __POLL__


def _snapshot(folder):
    out = {}
    for root, _dirs, files in os.walk(folder):
        for fn in files:
            p = os.path.join(root, fn)
            try:
                out[p] = os.path.getmtime(p)
            except OSError:
                pass
    return out


def main():
    print(f"[scout {SOURCE}] file_watch {SCOPE} -> {bridge_dir()}")
    prev = _snapshot(SCOPE)
    while True:
        time.sleep(POLL)
        curr = _snapshot(SCOPE)
        for p in curr.keys() - prev.keys():
            emit({"type": "file_created", "title": os.path.basename(p), "body": p})
        for p in prev.keys() - curr.keys():
            emit({"type": "file_deleted", "title": os.path.basename(p), "body": p})
        for p in curr.keys() & prev.keys():
            if curr[p] != prev[p]:
                emit({"type": "file_changed", "title": os.path.basename(p), "body": p})
        prev = curr


if __name__ == "__main__":
    main()
'''

_BODY_LOG_TAIL = '''
SCOPE = __SCOPE__   # log file to tail (this file only)
POLL = __POLL__


def main():
    print(f"[scout {SOURCE}] log_tail {SCOPE} -> {bridge_dir()}")
    off = 0
    while True:
        try:
            size = os.path.getsize(SCOPE)
            if size < off:
                off = 0  # rotated/truncated
            with open(SCOPE, "rb") as f:
                f.seek(off)
                data = f.read()
                off = f.tell()
            for line in data.decode("utf-8", "replace").splitlines():
                line = line.strip()
                if line:
                    emit({"type": "log_line", "body": line[:1000]})
        except OSError:
            pass
        time.sleep(POLL)


if __name__ == "__main__":
    main()
'''

_BODY_COMMAND_OUTPUT = '''import subprocess

COMMAND = __COMMAND__   # the harness/command to RUN and narrate the output of (Lane 1: observe)


def main():
    print(f"[scout {SOURCE}] command_output: {COMMAND} -> {bridge_dir()}")
    emit({"type": "harness_started", "title": "command started", "body": COMMAND, "priority": 75})
    proc = subprocess.Popen(COMMAND, shell=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                emit({"type": "harness_output", "body": line[:1000]})
    finally:
        code = proc.wait()
        emit({"type": "harness_finished", "title": f"exited {code}", "body": COMMAND, "priority": 80})


if __name__ == "__main__":
    main()
'''

_BODY_UDP_LISTEN = '''import socket

PORT = __PORT__   # listen for telemetry/device packets on this UDP port (e.g. an ESP32 over LAN)


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("0.0.0.0", PORT))
    print(f"[scout {SOURCE}] udp_listen :{PORT} -> {bridge_dir()}")
    emit({"type": "scout_online", "title": f"listening udp:{PORT}"})
    while True:
        data, _addr = s.recvfrom(65535)
        text = data.decode("utf-8", "replace").strip()
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                emit(obj)  # device already speaks the observation schema
                continue
        except Exception:
            pass
        emit({"type": "packet", "body": text[:500]})


if __name__ == "__main__":
    main()
'''

TRANSPORTS: Dict[str, Dict[str, object]] = {
    "file_watch": {"body": _BODY_FILE_WATCH, "scope": "folder", "desc": "watch a folder for file create/modify/delete"},
    "log_tail": {"body": _BODY_LOG_TAIL, "scope": "file", "desc": "tail a log file, one observation per new line"},
    "command_output": {"body": _BODY_COMMAND_OUTPUT, "scope": "command", "desc": "run a command/harness and narrate its output (observe, never impersonate)"},
    "udp_listen": {"body": _BODY_UDP_LISTEN, "scope": "port", "desc": "receive UDP packets (sim telemetry, ESP32/network devices)"},
}


def list_transports() -> List[Dict[str, str]]:
    return [{"name": k, "scope": str(v["scope"]), "desc": str(v["desc"])} for k, v in TRANSPORTS.items()]


def generate_shim(
    transport: str,
    source: str,
    scope: str,
    *,
    bridge_dir: Optional[str] = None,
    poll_sec: int = 3,
    filename: str = "radio_os_scout.py",
) -> str:
    """Return the full text of a standalone scout shim. `scope` meaning depends on transport:
    folder | file | command | port."""
    if transport not in TRANSPORTS:
        raise ValueError(f"unknown transport '{transport}'. Options: {', '.join(TRANSPORTS)}")
    body = str(TRANSPORTS[transport]["body"])
    scope_kind = str(TRANSPORTS[transport]["scope"])

    # Body values (__SCOPE__/__BRIDGE__/__COMMAND__) go through repr() → backslash-safe. The only
    # RAW insertion is the docstring description, so sanitize it (Windows paths, stray quotes).
    safe_scope_desc = f"{scope_kind}={scope}".replace("\\", "/").replace('"""', "'''")
    text = _PRELUDE + body
    text = text.replace("__TRANSPORT__", transport)
    text = text.replace("__SOURCE__", repr(str(source)))
    text = text.replace("__BRIDGE__", repr(str(bridge_dir or "")))
    text = text.replace("__SCOPE_DESC__", safe_scope_desc)
    text = text.replace("__FILENAME__", filename)
    text = text.replace("__POLL__", str(max(1, int(poll_sec))))
    if scope_kind == "command":
        text = text.replace("__COMMAND__", repr(str(scope)))
    elif scope_kind == "port":
        text = text.replace("__PORT__", str(int(scope)))
    else:  # folder | file
        text = text.replace("__SCOPE__", repr(str(scope)))
    return text


def write_shim(out_path: str, transport: str, source: str, scope: str, **kw) -> str:
    filename = os.path.basename(out_path)
    text = generate_shim(transport, source, scope, filename=filename, **kw)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) < 4:
        print("Radio OS — shim generator")
        print("usage: python shim_generator.py <transport> <source> <scope> [out.py] [--bridge-dir DIR]")
        print("transports:")
        for t in list_transports():
            print(f"  {t['name']:16} scope={t['scope']:8} — {t['desc']}")
        raise SystemExit(2)
    transport, source, scope = sys.argv[1], sys.argv[2], sys.argv[3]
    out = sys.argv[4] if len(sys.argv) > 4 and not sys.argv[4].startswith("--") else f"scout_{source}.py"
    bdir = None
    if "--bridge-dir" in sys.argv:
        bdir = sys.argv[sys.argv.index("--bridge-dir") + 1]
    write_shim(out, transport, source, scope, bridge_dir=bdir)
    print(f"wrote {out}  (transport={transport}, source={source}, scope={scope})")
    print(f"run it where the scope lives:  python {out}")
