#!/usr/bin/env python3
"""The earnest first-run club gate.

When a machine isn't ready for a station — no LLM membership, or voice models it can't find — we do
NOT nag on every open and we do NOT fail silently. We ask ONCE, kindly, remember the answer
machine-level (the "club"), and from then on every future .oradio just works.

This is the small Tk surface for that moment. It is defensive: if Tk is unavailable or anything goes
wrong it returns False and the caller falls back to the (already earnest) CLI hints. The substance —
persistence + resolution — lives in provisioning.py and oradio_resolver.py and is fully tested without
a GUI; this file only presents it.

Owner-verifiable on its own:
    python club_gate.py path/to/Station.oradio
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import provisioning


def gate_needs(readiness: Dict[str, Any]) -> Dict[str, bool]:
    blocking = [str(b).lower() for b in readiness.get("blocking", [])]
    return {
        "llm": any("llm" in b for b in blocking),
        "voices": any("voice" in b for b in blocking),
        "piper": any("piper" in b for b in blocking),
    }


def show_club_gate(readiness: Dict[str, Any], package_path: Optional[Path] = None) -> bool:
    """Show the gate for whatever's missing. Returns True if the machine is now ready, else False.
    Any failure (no display, Tk missing) returns False so the caller can fall back to the CLI flow."""
    def _antenna_needs():
        if not package_path:
            return []
        try:
            import antenna_resolver
            manifest = antenna_resolver._manifest_from(Path(package_path))
            return [r for r in antenna_resolver.resolve_station_antennas(manifest)
                    if r["status"] in antenna_resolver.PROBLEM_STATUSES]
        except Exception:
            return []

    if not any(gate_needs(readiness).values()) and not _antenna_needs():
        return True
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except Exception:
        return False

    try:
        import oradio_resolver
    except Exception:
        oradio_resolver = None  # type: ignore
    try:
        import antenna_resolver
    except Exception:
        antenna_resolver = None  # type: ignore

    UI = {"bg": "#272822", "panel": "#2d2e27", "text": "#f8f8f2", "muted": "#75715e", "accent": "#66d9ef", "good": "#a6e22e"}
    state = {"ready": bool(readiness.get("ready")), "readiness": readiness, "ant_needs": _antenna_needs()}

    root = tk.Tk()
    root.title("Radio OS — one-time setup")
    root.geometry("560x420")
    root.configure(bg=UI["bg"])

    tk.Label(root, text="Let's get you set up — just once.", font=("Segoe UI", 16, "bold"), fg=UI["text"], bg=UI["bg"]).pack(anchor="w", padx=18, pady=(18, 4))
    tk.Label(root, text="After this, every station you open reuses these settings.", font=("Segoe UI", 10), fg=UI["muted"], bg=UI["bg"]).pack(anchor="w", padx=18)

    status_var = tk.StringVar()
    body = tk.Frame(root, bg=UI["bg"])
    body.pack(fill="both", expand=True, padx=18, pady=12)

    def reresolve() -> None:
        if oradio_resolver and package_path:
            try:
                state["readiness"] = oradio_resolver.resolve_station(Path(package_path), check_llm=True)
                state["ready"] = bool(state["readiness"].get("ready"))
            except Exception:
                pass
        state["ant_needs"] = _antenna_needs()
        refresh()

    def refresh() -> None:
        needs = gate_needs(state["readiness"])
        ant = state.get("ant_needs", [])
        if state["ready"] and not any(needs.values()) and not ant:
            status_var.set("You're all set ✓  Opening the station…")
            root.after(700, root.destroy)
            return
        bits = []
        if needs["llm"]:
            bits.append("• AI provider not set up")
        if needs["voices"]:
            bits.append("• voice models not found")
        if needs["piper"]:
            bits.append("• Piper (offline TTS) not found")
        for a in ant:
            bits.append(f"• antenna '{a['antenna']}' — {a['message']}")
        status_var.set("Still to set up (antennas are optional — you can open anyway):\n" + "\n".join(bits))

    def pick_voices() -> None:
        folder = filedialog.askdirectory(parent=root, title="Where are your voice models?")
        if not folder:
            return
        res = provisioning.save_voices_dir(folder)
        if not res.get("ok"):
            messagebox.showwarning("Voices", res.get("error", "Could not use that folder."), parent=root)
            return
        messagebox.showinfo("Voices", f"Got it — remembered {res.get('voice_files', 0)} voice file(s). I won't ask again.", parent=root)
        reresolve()

    def pick_piper() -> None:
        path = filedialog.askopenfilename(parent=root, title="Where is your Piper binary?")
        if not path:
            return
        res = provisioning.save_piper_bin(path)
        if not res.get("ok"):
            messagebox.showwarning("Piper", res.get("error", "Could not use that file."), parent=root)
            return
        reresolve()

    def pick_antenna(a) -> None:
        if a.get("kind") == "folder":
            p = filedialog.askdirectory(parent=root, title=f"Where is '{a['antenna']}' on this machine?")
        else:
            p = filedialog.askopenfilename(parent=root, title=f"Where is '{a['antenna']}' on this machine?")
        if not p:
            return
        if antenna_resolver:
            res = antenna_resolver.remember_antenna_target(a["key"], p)
            if not res.get("ok"):
                messagebox.showwarning("Antenna", res.get("error", "Could not use that."), parent=root)
                return
        reresolve()

    needs = gate_needs(readiness)
    if needs["llm"]:
        tk.Label(body, text="I noticed there's no AI provider set up yet.", fg=UI["text"], bg=UI["bg"], font=("Segoe UI", 10)).pack(anchor="w", pady=(6, 2))
        tk.Label(body, text="Set it up in the Library/Studio Tune-In, or run:  oradio_player.py --tune-in", fg=UI["muted"], bg=UI["bg"], font=("Consolas", 9)).pack(anchor="w")
    if needs["voices"]:
        tk.Button(body, text="Show me where your voice models are…", command=pick_voices, bg=UI["accent"], fg="#000", relief="flat", padx=12, pady=8).pack(anchor="w", pady=(12, 4))
    if needs["piper"]:
        tk.Button(body, text="Show me your Piper binary…", command=pick_piper, bg=UI["panel"], fg=UI["text"], relief="flat", padx=12, pady=6).pack(anchor="w", pady=4)
    for a in state.get("ant_needs", []):
        if a.get("kind") in ("folder", "file", "files"):
            tk.Button(body, text=f"Show me where '{a['antenna']}' is…", command=lambda a=a: pick_antenna(a),
                      bg=UI["panel"], fg=UI["text"], relief="flat", padx=12, pady=6).pack(anchor="w", pady=4)

    tk.Label(body, textvariable=status_var, fg=UI["good"], bg=UI["bg"], justify="left", font=("Segoe UI", 10)).pack(anchor="w", pady=(16, 0))

    btns = tk.Frame(root, bg=UI["bg"])
    btns.pack(fill="x", padx=18, pady=(0, 16))
    tk.Button(btns, text="Set up later", command=root.destroy, bg=UI["panel"], fg=UI["muted"], relief="flat").pack(side="right")

    refresh()
    root.mainloop()
    return bool(state["ready"])


def main(argv) -> int:
    if not argv:
        print("usage: python club_gate.py path/to/Station.oradio", file=sys.stderr)
        return 2
    import oradio_resolver
    readiness = oradio_resolver.resolve_station(Path(argv[0]), check_llm=True)
    print("ready" if show_club_gate(readiness, Path(argv[0])) else "not ready (deferred)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
