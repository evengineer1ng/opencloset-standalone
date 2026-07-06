import sys
import os
import threading
import queue

# Add workspace to path
sys.path.insert(0, r"C:\Users\evana\OneDrive\Documents\Radio-OS")

# Build a minimal runtime_stub and initialize the plugin
runtime_stub = {
    "station_id": "NeikosExpedition",
    "nk_cmd_q": queue.Queue(),
    "nk_ui_q": queue.Queue(),
}

# ── NPC Voice: wire Windows SAPI provider for standalone dev mode ──────────────
# Inject voice_provider before init_voice() so NPCVoiceQueue can synthesize.
from plugins.neikos.voice import WindowsSAPIProvider, init_voice as _init_voice
runtime_stub["voice_provider"] = WindowsSAPIProvider()
_init_voice(runtime_stub)
runtime_stub["nk_voice_inited"] = True
print("[NK Voice] Standalone SAPI voice provider initialized.", flush=True)

import plugins.neikos as nk
from plugins.neikos import NKController, _start_web_server, load_game_state, apply_saved_state

controller = NKController(runtime_stub, {})
runtime_stub["nk_controller"] = controller

# Auto-load saved state if available
_save_data = load_game_state()
_start_seed = _save_data.get("seed", 1) if _save_data else 1
controller.init_island(seed=_start_seed)

if _save_data:
    _ok = apply_saved_state(controller._state, _save_data)
    if _ok:
        print(f"[NK Save] Restored game state: seed={_start_seed}, tick={controller._state.tick}, "
              f"location={controller._state.player_location}", flush=True)
    else:
        print("[NK Save] Save file found but not applied (seed mismatch?)", flush=True)
else:
    print(f"[NK Save] No save file found, starting fresh (seed={_start_seed})", flush=True)

controller.start()

# Init ESP32 puck manager
from plugins.neikos.spatial.esp32 import init_puck_manager as _init_pm
_init_pm(controller)
print("[NK Puck] PuckManager initialized.", flush=True)

import threading
stop_event = threading.Event()
_start_web_server(stop_event, runtime_stub)
