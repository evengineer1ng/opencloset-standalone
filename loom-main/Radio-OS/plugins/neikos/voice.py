from __future__ import annotations
import threading, queue
from typing import Optional, Dict, Any

# NPC archetype → voice profile mapping
# Generic roles:
NPC_VOICE_MAP = {
    "ELDER":        {"speed": 0.85, "pitch": -2,  "style": "wise"},
    "SCIENTIST":    {"speed": 1.0,  "pitch": 0,   "style": "clinical"},
    "REBEL":        {"speed": 1.1,  "pitch": 1,   "style": "terse"},
    "CARTOGRAPHER": {"speed": 0.9,  "pitch": -1,  "style": "precise"},
    "GHOST":        {"speed": 0.75, "pitch": -4,  "style": "haunted"},
    "TRAINER":      {"speed": 1.0,  "pitch": 0,   "style": "neutral"},
    "DEFAULT":      {"speed": 1.0,  "pitch": 0,   "style": "neutral"},
    # Knower archetypes (K1–K5) mapped to distinct voice profiles:
    # K1 – Retired League Archivist: precise, measured, institutional cadence
    "RETIRED_ARCHIVIST":   {"speed": 0.9,  "pitch": -1,  "style": "precise"},
    # K2 – Regional Gym Leader: confident, neutral pace
    "REGIONAL_GYM_LEADER": {"speed": 1.0,  "pitch": 0,   "style": "neutral"},
    # K3 – Isolated Researcher: clinical, slightly fast (nervous energy)
    "ISOLATED_RESEARCHER": {"speed": 1.05, "pitch": 0,   "style": "clinical"},
    # K4 – Elderly Wild Zone Hermit: slow, gravelly, wise
    "ELDERLY_HERMIT":      {"speed": 0.80, "pitch": -3,  "style": "wise"},
    # K5 – Anonymous Signal Voice: haunted, slow, distorted
    "ANONYMOUS_SIGNAL":    {"speed": 0.70, "pitch": -5,  "style": "haunted"},
}


class NPCVoiceQueue:
    """Thread-safe queue for NPC dialogue TTS requests."""

    def __init__(self, runtime_stub: Dict[str, Any]):
        self._q: queue.Queue = queue.Queue()
        self._runtime = runtime_stub
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def speak(self, text: str, archetype: str = "DEFAULT", npc_name: str = ""):
        """Queue a TTS request for an NPC line."""
        profile = NPC_VOICE_MAP.get(archetype, NPC_VOICE_MAP["DEFAULT"])
        self._q.put({"text": text, "profile": profile, "npc_name": npc_name})

    def _worker(self):
        while True:
            item = self._q.get()
            try:
                self._synthesize(item)
            except Exception as e:
                print(f"[NK Voice] Error: {e}")
            self._q.task_done()

    def _synthesize(self, item: dict):
        """Synthesize speech via voice_provider if available.

        Supports two provider shapes:
        1. Radio OS full provider: synthesize() returns audio bytes → audio_queue.
        2. WindowsSAPIProvider: synthesize() is fire-and-forget (returns None).
           No audio_queue needed; SAPI plays directly through the system speaker.
        """
        vp = self._runtime.get("voice_provider")
        if vp is None:
            print(f"[NK Voice] {item['npc_name']}: {item['text']}")
            return
        try:
            if hasattr(vp, "synthesize"):
                result = vp.synthesize(
                    text=item["text"],
                    speed=item["profile"]["speed"],
                    pitch=item["profile"]["pitch"],
                )
                if result is not None:
                    # Radio OS provider: result is audio bytes, push to queue
                    aq = self._runtime.get("audio_queue")
                    if aq:
                        aq.put({"type": "tts", "audio": result, "npc": item["npc_name"]})
                # else: SAPI provider already speaking asynchronously — nothing to do
            else:
                print(f"[NK Voice] {item['npc_name']}: {item['text']}")
        except Exception as e:
            print(f"[NK Voice] Synthesis failed: {e}")


_voice_queue: Optional[NPCVoiceQueue] = None


# ── Windows SAPI provider (standalone / dev mode) ─────────────────────────────

class WindowsSAPIProvider:
    """
    Minimal voice_provider for standalone Windows dev mode.
    Uses PowerShell + System.Speech.Synthesis.SpeechSynthesizer.
    speak() is fire-and-forget (Popen, non-blocking).
    synthesize(text, speed, pitch) is required by NPCVoiceQueue._synthesize.
    """

    def synthesize(self, text: str, speed: float = 1.0, pitch: int = 0) -> None:
        """
        Speak ``text`` via Windows SAPI asynchronously.
        speed < 1 → slower (Rate -5..0), speed > 1 → faster (Rate 0..10).
        pitch is ignored (SAPI doesn't expose pitch easily without COM automation).
        """
        import subprocess, shlex

        # Map speed to SAPI Rate: 1.0 → 0, 0.75 → -3, 1.15 → 2, clamped -10..10
        rate = int(round((speed - 1.0) * 10))
        rate = max(-10, min(10, rate))

        # Escape single-quotes for PowerShell string safety
        safe_text = text.replace("'", "''")

        ps_cmd = (
            "Add-Type -AssemblyName System.Speech; "
            "$sp = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$sp.Rate = {rate}; "
            f"$sp.Speak('{safe_text}');"
        )

        try:
            subprocess.Popen(
                ["powershell", "-NonInteractive", "-Command", ps_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            print(f"[NK Voice SAPI] Failed to launch speech: {e}")


def init_voice(runtime_stub: Dict[str, Any]) -> NPCVoiceQueue:
    global _voice_queue
    _voice_queue = NPCVoiceQueue(runtime_stub)
    return _voice_queue


def speak_npc(text: str, archetype: str = "DEFAULT", npc_name: str = ""):
    if _voice_queue:
        _voice_queue.speak(text, archetype, npc_name)
    else:
        print(f"[NK Voice uninit] {npc_name}: {text}")
