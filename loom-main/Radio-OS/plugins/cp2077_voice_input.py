"""
Cyberpunk 2077 — Player Voice Input Feed Plugin
=================================================
Listens on the microphone for the player speaking and emits a StationEvent
so ARIA can respond conversationally — like talking to JARVIS or Navi.

HOW IT WORKS
  1. Continuously monitors the microphone for speech using VAD (Voice Activity
     Detection) based on RMS energy threshold.
  2. When speech is detected, records until silence returns.
  3. Passes the audio to STT (whisper.cpp or speech_recognition library).
  4. Emits a StationEvent(source="cp2077_voice_input", type="player_spoke")
     with the transcribed text in the payload.
  5. ARIA's meta plugin (cp2077_jarvis.py) picks this up and generates a reply.

STT BACKENDS (in order of preference)
  1. whisper.cpp — fastest, most accurate, runs locally.
     Set env vars: WHISPER_CPP_BIN and WHISPER_CPP_MODEL
     Or configure whisper_bin / whisper_model in the manifest feed config.
  2. speech_recognition (Google / offline Sphinx fallback)
     Install: pip install SpeechRecognition

DEPENDENCIES
  pip install sounddevice numpy

OPTIONAL (better VAD)
  pip install webrtcvad   — Google WebRTC VAD for more accurate speech detection
"""

from __future__ import annotations

import io
import os
import sys
import time
import wave
import threading
import tempfile
import subprocess
from typing import Any, Dict, Optional

PLUGIN_NAME = "cp2077_voice_input"
PLUGIN_DESC = "Mic-based player voice input — player speaks, ARIA responds."
IS_FEED     = True

FEED_DEFAULTS: Dict[str, Any] = {
    "enabled":          False,
    "vad_silence_sec":  0.8,
    "min_speech_sec":   0.4,
    "stt_backend":      "auto",
    "whisper_bin":      "",
    "whisper_model":    "",
    "device_index":     -1,
    "sample_rate":      16000,
}

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import sounddevice as sd
    HAS_SD = True
except ImportError:
    HAS_SD = False

try:
    import webrtcvad
    HAS_WEBRTCVAD = True
except ImportError:
    HAS_WEBRTCVAD = False


# ---------------------------------------------------------------------------
# STT helpers
# ---------------------------------------------------------------------------

def _resolve_whisper(cfg: Dict[str, Any]):
    """Return (bin_path, model_path) or (None, None)."""
    whisper_bin = (str(cfg.get("whisper_bin", "") or "").strip()
                   or os.environ.get("WHISPER_CPP_BIN", "").strip())
    model_path  = (str(cfg.get("whisper_model", "") or "").strip()
                   or os.environ.get("WHISPER_CPP_MODEL", "").strip())
    if whisper_bin and os.path.exists(whisper_bin) and model_path and os.path.exists(model_path):
        return whisper_bin, model_path
    return None, None


def _transcribe_whisper(wav_path: str, whisper_bin: str, model_path: str) -> str:
    try:
        result = subprocess.run(
            [whisper_bin, "-m", model_path, "-f", wav_path, "-otxt",
             "--no-prints", "--language", "en"],
            capture_output=True, text=True, timeout=30,
        )
        txt_path = wav_path + ".txt"
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8") as fh:
                text = fh.read().strip()
            os.remove(txt_path)
            return text
        # Fallback: parse stdout
        lines = [l.strip() for l in result.stdout.splitlines()
                 if l.strip() and not l.startswith("[")]
        return " ".join(lines).strip()
    except Exception:
        return ""


def _transcribe_sr(wav_path: str) -> str:
    try:
        import speech_recognition as sr  # type: ignore
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            audio = recognizer.record(source)
        try:
            return recognizer.recognize_google(audio)
        except Exception:
            try:
                return recognizer.recognize_sphinx(audio)
            except Exception:
                return ""
    except Exception:
        return ""


def _transcribe(wav_path: str, cfg: Dict[str, Any]) -> str:
    backend = str(cfg.get("stt_backend", "auto") or "auto").lower()

    whisper_bin, model_path = _resolve_whisper(cfg)

    if backend in ("whisper_cpp", "auto") and whisper_bin:
        text = _transcribe_whisper(wav_path, whisper_bin, model_path)
        if text:
            return text

    if backend in ("speech_recognition", "auto"):
        text = _transcribe_sr(wav_path)
        if text:
            return text

    return ""


# ---------------------------------------------------------------------------
# WAV writer helper
# ---------------------------------------------------------------------------

def _write_wav(path: str, frames: list, sample_rate: int) -> None:
    if not HAS_NUMPY:
        return
    import numpy as np  # already checked
    audio = np.concatenate(frames, axis=0).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)   # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(audio.tobytes())


# ---------------------------------------------------------------------------
# Energy-based VAD
# ---------------------------------------------------------------------------

class EnergyVAD:
    """Simple RMS energy threshold VAD."""

    def __init__(self, sample_rate: int = 16000, frame_ms: int = 20,
                 energy_threshold: float = 300.0,
                 silence_sec: float = 0.8):
        self.sample_rate      = sample_rate
        self.frame_size       = int(sample_rate * frame_ms / 1000)
        self.energy_threshold = energy_threshold
        self.silence_frames   = int(silence_sec * 1000 / frame_ms)

    def is_speech(self, frame: "np.ndarray") -> bool:
        if not HAS_NUMPY:
            return False
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        return rms > self.energy_threshold


# ---------------------------------------------------------------------------
# Feed worker
# ---------------------------------------------------------------------------

def feed_worker(cfg: Dict[str, Any], runtime: Dict[str, Any]) -> None:
    """Main mic listening loop."""
    global _log_fn

    event_q      = runtime.get("event_q")
    StationEvent = runtime.get("StationEvent")
    log          = runtime.get("log", print)

    if not HAS_SD or not HAS_NUMPY:
        log("cp2077_voice", "WARNING: sounddevice or numpy not installed — voice input disabled.")
        log("cp2077_voice", "Install with: pip install sounddevice numpy")
        return

    if event_q is None or StationEvent is None:
        log("cp2077_voice", "ERROR: runtime missing event_q / StationEvent — aborting")
        return

    sample_rate  = int(cfg.get("sample_rate", 16000))
    device_idx   = cfg.get("device_index", -1)
    silence_sec  = float(cfg.get("vad_silence_sec", 0.8))
    min_speech_s = float(cfg.get("min_speech_sec", 0.4))
    frame_ms     = 20   # VAD frame duration in ms
    frame_size   = int(sample_rate * frame_ms / 1000)

    if device_idx == -1:
        device_idx = None   # sounddevice default

    vad = EnergyVAD(sample_rate=sample_rate, silence_sec=silence_sec)
    silence_frames_needed = vad.silence_frames
    min_speech_frames     = int(min_speech_s * 1000 / frame_ms)

    log("cp2077_voice", f"Mic listener active — VAD silence={silence_sec}s, "
                         f"min_speech={min_speech_s}s, device={device_idx}")

    # Shared state between callback and main loop
    _shared: Dict[str, Any] = {"recording": [], "in_speech": False,
                                "silence_count": 0, "speech_ready": None}
    _lock = threading.Lock()

    def _audio_callback(indata: "np.ndarray", frames: int, t: Any, status: Any):
        frame = (indata[:, 0] * 32767).astype("int16")
        is_s  = vad.is_speech(frame)

        with _lock:
            if is_s:
                _shared["in_speech"]    = True
                _shared["silence_count"] = 0
                _shared["recording"].append(frame.copy())
            else:
                if _shared["in_speech"]:
                    _shared["recording"].append(frame.copy())
                    _shared["silence_count"] += 1
                    if _shared["silence_count"] >= silence_frames_needed:
                        # Speech ended — hand off for STT
                        if len(_shared["recording"]) >= min_speech_frames:
                            _shared["speech_ready"] = list(_shared["recording"])
                        _shared["recording"]    = []
                        _shared["in_speech"]    = False
                        _shared["silence_count"] = 0

    try:
        with sd.InputStream(
            device=device_idx,
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            blocksize=frame_size,
            callback=_audio_callback,
        ):
            while True:
                time.sleep(0.1)
                with _lock:
                    frames_ready = _shared.pop("speech_ready", None)

                if frames_ready is None:
                    continue

                # Transcribe in the feed thread (not in the audio callback)
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".wav", delete=False, prefix="aria_stt_"
                )
                tmp_path = tmp.name
                tmp.close()

                try:
                    _write_wav(tmp_path, frames_ready, sample_rate)
                    text = _transcribe(tmp_path, cfg).strip()
                finally:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

                if not text:
                    continue

                log("cp2077_voice", f"Player said: {text!r}")

                # Emit to ARIA
                try:
                    evt = StationEvent(
                        source="cp2077_voice_input",
                        type="player_spoke",
                        priority=88.0,
                        payload={"text": text},
                    )
                    event_q.put(evt)
                except Exception as exc:
                    log("cp2077_voice", f"event emit error: {exc}")

    except Exception as exc:
        log("cp2077_voice", f"InputStream error: {exc}")
        log("cp2077_voice", "Voice input disabled for this session.")
