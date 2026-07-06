#!/usr/bin/env python3
"""Descriptor-style `.oradio` first-run gate.

This is the Loom-native counterpart to ``club_gate.py`` for packaged stations. It checks
the engine-native club asks plus the local voice/Piper assets needed to honor a descriptor's
promised playback experience before the descriptor player launches.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import provisioning
from oradio_engine import Club, OradioDescriptor, open_oradio


def descriptor_gate_needs(descriptor: Dict[str, Any], *, club: Optional[Club] = None) -> Dict[str, Any]:
    club = club or Club()
    result = open_oradio(descriptor, club=club, gate=False)

    voice_cfg = descriptor.get("voice") if isinstance(descriptor.get("voice"), dict) else {}
    voice_requested = (
        voice_cfg.get("provider") not in (None, "", "none")
        or "voice" in (descriptor.get("surfaces") or [])
    )
    provider = str(voice_cfg.get("provider") or "none").strip().lower()
    voices_ready = bool(provisioning.get_voices_dirs())
    piper_needed = provider == "piper"
    piper_ready = bool(provisioning.get_piper_bin())

    return {
        "open_result": result,
        "engine_ready": result.engine is not None,
        "club_ready": result.report.ready,
        "club_asks": list(result.report.asks),
        "withheld": list(result.withheld or []),
        "voice_requested": bool(voice_requested),
        "voice_provider": provider,
        "voices_ready": voices_ready,
        "piper_needed": piper_needed,
        "piper_ready": piper_ready,
        "ready": bool(
            result.engine is not None
            and result.report.ready
            and (not voice_requested or (voices_ready and (not piper_needed or piper_ready)))
        ),
    }


def show_descriptor_club_gate(
    descriptor_path: Path,
    *,
    club: Optional[Club] = None,
    descriptor: Optional[Dict[str, Any]] = None,
) -> bool:
    """Show the one-time setup gate. Returns True when playback may proceed."""

    club = club or Club()
    descriptor = descriptor or _read_descriptor(descriptor_path)
    state = descriptor_gate_needs(descriptor, club=club)
    if state["ready"]:
        return True

    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except Exception:
        return False

    UI = {
        "bg": "#272822",
        "panel": "#2d2e27",
        "text": "#f8f8f2",
        "muted": "#75715e",
        "accent": "#66d9ef",
        "good": "#a6e22e",
    }

    root = tk.Tk()
    root.title("The Loom — one-time setup")
    root.geometry("620x460")
    root.configure(bg=UI["bg"])

    tk.Label(
        root,
        text="This loom is almost ready — just one-time machine setup.",
        font=("Segoe UI", 16, "bold"),
        fg=UI["text"],
        bg=UI["bg"],
    ).pack(anchor="w", padx=18, pady=(18, 4))
    tk.Label(
        root,
        text="After this, future descriptor .oradios reuse the same club settings.",
        font=("Segoe UI", 10),
        fg=UI["muted"],
        bg=UI["bg"],
    ).pack(anchor="w", padx=18)

    status_var = tk.StringVar()
    body = tk.Frame(root, bg=UI["bg"])
    body.pack(fill="both", expand=True, padx=18, pady=12)

    def refresh() -> None:
        nonlocal state
        state = descriptor_gate_needs(descriptor, club=club)
        lines = []
        if state["club_asks"]:
            for ask in state["club_asks"]:
                if ask.capability == "llm":
                    lines.append(f"• optional club ask: {ask.prompt}")
        if state["voice_requested"] and not state["voices_ready"]:
            lines.append("• voice models are not remembered yet")
        if state["voice_requested"] and state["piper_needed"] and not state["piper_ready"]:
            lines.append("• Piper binary is not remembered yet")
        if state["ready"]:
            status_var.set("You’re all set. Opening the loom…")
            root.after(700, root.destroy)
            return
        status_var.set("Still needed:\n" + ("\n".join(lines) if lines else "• playback requirements unresolved"))

    def pick_voices() -> None:
        folder = filedialog.askdirectory(parent=root, title="Where are your voice models?")
        if not folder:
            return
        res = provisioning.save_voices_dir(folder)
        if not res.get("ok"):
            messagebox.showwarning("Voices", res.get("error", "Could not use that folder."), parent=root)
            return
        refresh()

    def pick_piper() -> None:
        path = filedialog.askopenfilename(parent=root, title="Where is your Piper binary?")
        if not path:
            return
        res = provisioning.save_piper_bin(path)
        if not res.get("ok"):
            messagebox.showwarning("Piper", res.get("error", "Could not use that file."), parent=root)
            return
        refresh()

    if state["voice_requested"] and not state["voices_ready"]:
        tk.Button(
            body,
            text="Show me where your voice models are…",
            command=pick_voices,
            bg=UI["accent"],
            fg="#000",
            relief="flat",
            padx=12,
            pady=8,
        ).pack(anchor="w", pady=(6, 4))

    if state["voice_requested"] and state["piper_needed"] and not state["piper_ready"]:
        tk.Button(
            body,
            text="Show me your Piper binary…",
            command=pick_piper,
            bg=UI["panel"],
            fg=UI["text"],
            relief="flat",
            padx=12,
            pady=6,
        ).pack(anchor="w", pady=4)

    if any(ask.capability == "llm" for ask in state["club_asks"]):
        tk.Label(
            body,
            text="LLM setup is still optional for this milestone. Voice and visuals will still run.",
            fg=UI["muted"],
            bg=UI["bg"],
            font=("Segoe UI", 10),
            wraplength=560,
            justify="left",
        ).pack(anchor="w", pady=(10, 2))

    tk.Label(
        body,
        textvariable=status_var,
        fg=UI["good"],
        bg=UI["bg"],
        justify="left",
        font=("Segoe UI", 10),
        wraplength=560,
    ).pack(anchor="w", pady=(18, 0))

    btns = tk.Frame(root, bg=UI["bg"])
    btns.pack(fill="x", padx=18, pady=(0, 16))
    tk.Button(btns, text="Set up later", command=root.destroy, bg=UI["panel"], fg=UI["muted"], relief="flat").pack(side="right")

    refresh()
    root.mainloop()
    return bool(state["ready"])


def _read_descriptor(path: Path) -> Dict[str, Any]:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("Descriptor must decode to an object.")
    return data
