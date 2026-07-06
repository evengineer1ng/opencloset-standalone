"""Cross-platform speech-out, so loom speaks on Windows, macOS, Linux, and Android (Termux).

`say(text)` picks a backend; env LOOM_TTS overrides (sapi|termux|say|espeak|none). Endpoint helper
— the deterministic engine never imports this. On Android, install Termux + the Termux:API app and
loom speaks via `termux-tts-speak` with no GPU and no model.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys


def backend() -> str:
    b = os.environ.get("LOOM_TTS", "").strip().lower()
    if b:
        return b
    if sys.platform == "win32":
        return "sapi"
    if sys.platform == "darwin":
        return "say"
    if shutil.which("termux-tts-speak"):
        return "termux"
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        return "espeak"
    return "none"


def say(text: str, voice: str = "") -> None:
    if not text or not text.strip():
        return
    b = backend()
    try:
        if b == "termux":
            subprocess.run(["termux-tts-speak", text], check=False)
        elif b == "say":
            subprocess.run(["say"] + (["-v", voice] if voice else []) + [text], check=False)
        elif b == "espeak":
            subprocess.run([shutil.which("espeak-ng") or "espeak", text], check=False)
        elif b == "sapi":
            import sounddevice as sd
            from voice_provider import SapiProvider
            audio, sr = SapiProvider().synthesize(voice or "host", text, {})  # empty map -> default voice
            sd.play(audio, sr, blocking=True)
        # "none": stay silent (no backend available)
    except Exception:
        pass
