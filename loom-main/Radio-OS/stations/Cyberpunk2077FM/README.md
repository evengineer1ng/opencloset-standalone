# Night City FM — Cyberpunk 2077 ARIA Companion

## What is this?

**ARIA** (Autonomous Reconnaissance and Intelligence Assistant) is your in-game AI companion for Cyberpunk 2077, running inside Radio OS.

Think **JARVIS from Iron Man** crossed with **Navi from Ocarina of Time**:
- Calls out threats, enemies, health warnings, and wanted level changes **as they happen**
- Notifies you of points of interest nearby
- Reacts to quest updates, item pickups, and location changes with contextual commentary
- **Listens to your mic** and responds conversationally when you talk

---

## Quick Setup

### 1. Install Cyber Engine Tweaks (CET)
Download and install [Cyber Engine Tweaks](https://www.nexusmods.com/cyberpunk2077/mods/107) — the most popular CP2077 mod tool.

### 2. Install the RadioOS Bridge Mod

Copy the CET mod to your CP2077 install:

```
%CP2077_INSTALL%\bin\x64\plugins\cyber_engine_tweaks\mods\RadioOSBridge\init.lua
```

The file is at `stations/Cyberpunk2077FM/cet_mod/init.lua` in this repo.

This mod writes your live game state to:
```
%USERPROFILE%\RadioOSBridge\cp2077_state.json
```

### 3. Set Up Voice Input (Optional but recommended)

Install dependencies:
```powershell
.\radioenv\Scripts\Activate.ps1
pip install sounddevice numpy
```

For best speech recognition, install [whisper.cpp](https://github.com/ggerganov/whisper.cpp) and set:
```powershell
$env:WHISPER_CPP_BIN   = "C:\path\to\whisper.exe"
$env:WHISPER_CPP_MODEL = "C:\path\to\ggml-base.en.bin"
```

Or fall back to Google Speech Recognition:
```powershell
pip install SpeechRecognition
```

### 4. Enable the Feeds

Open `stations/Cyberpunk2077FM/manifest.yaml` and set:
```yaml
feeds:
  cp2077_sdk:
    enabled: true
  cp2077_voice_input:
    enabled: true   # if you want voice responses
```

### 5. Launch

```powershell
python shell_bookmark.py
```

Select **Night City FM** from the station list.

---

## Testing Without CP2077

Enable simulation mode to test ARIA without the game running:

```yaml
feeds:
  cp2077_sdk:
    enabled: true
    sim_mode: true
```

ARIA will receive simulated game events (combat, location changes, quest updates, etc.) so you can verify voice and LLM output.

---

## How it Works

```
CP2077 Game
    └─ CET mod (init.lua)
           └─ writes cp2077_state.json every 0.5s
                  └─ plugins/cp2077_sdk.py  ← polls file, detects changes
                         └─ StationEvent → event_q
                                └─ plugins/meta/cp2077_jarvis.py  ← ARIA!
                                       └─ LLM generates response
                                              └─ TTS → you hear ARIA speak

Microphone
    └─ plugins/cp2077_voice_input.py  ← VAD + STT
           └─ StationEvent(type="player_spoke")
                  └─ ARIA replies conversationally
```

---

## Customizing ARIA

Edit the system prompt in `manifest.yaml` under `prompts.host_system` to change ARIA's personality, tone, or how she refers to you.

Adjust pacing in the `companion:` section:
- `min_gap_sec` — minimum silence between callouts (default 4s)
- `ambient_gap_sec` — how long before ARIA makes an unprompted observation (default 90s)

## Voices

ARIA uses the `am_onyx` Kokoro TTS voice by default (deep, analytical). Change it in the manifest under `audio.voices.aria`.
