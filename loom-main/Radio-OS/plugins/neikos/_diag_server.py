"""
Diagnostic standalone server - identical to _webserver_standalone.py but with 
extra WS route debug logging.
"""
import sys
import os
import threading
import queue
import logging

logging.basicConfig(level=logging.DEBUG, stream=sys.stdout, format='%(name)s: %(message)s')

# Add workspace to path
sys.path.insert(0, r"C:\Users\evana\OneDrive\Documents\Radio-OS")

runtime_stub = {
    "station_id": "NeikosExpedition",
    "nk_cmd_q": queue.Queue(),
    "nk_ui_q": queue.Queue(),
}

from plugins.neikos.voice import WindowsSAPIProvider, init_voice as _init_voice
runtime_stub["voice_provider"] = WindowsSAPIProvider()
_init_voice(runtime_stub)
runtime_stub["nk_voice_inited"] = True

import plugins.neikos as nk
from plugins.neikos import NKController, load_game_state, apply_saved_state

controller = NKController(runtime_stub, {})
runtime_stub["nk_controller"] = controller

_save_data = load_game_state()
_start_seed = _save_data.get("seed", 1) if _save_data else 1
controller.init_island(seed=_start_seed)

if _save_data:
    apply_saved_state(controller._state, _save_data)

controller.start()

from plugins.neikos.spatial.esp32 import init_puck_manager as _init_pm
_init_pm(controller)
print("[NK Puck] PuckManager initialized.", flush=True)

# Now build the FastAPI app ourselves (bypassing _start_web_server)
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="Neikos Diagnostic")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.get("/api/state")
async def get_state():
    st = controller._state
    if not st:
        return {"error": "no state"}
    return {"player_location": st.player_location, "tick": st.tick}

@app.get("/api/puck/status")
async def get_puck_status():
    from plugins.neikos.spatial.esp32 import get_puck_manager
    pm = get_puck_manager()
    if pm is None:
        return {"ok": False, "error": "no pm"}
    return {"ok": True, **pm.status()}

print("[DIAG] Registering WS route...", flush=True)

@app.websocket("/ws/puck")
async def puck_ws(websocket: WebSocket):
    print(f"[WS] incoming connection! scope type={websocket.scope.get('type')}", flush=True)
    await websocket.accept()
    print("[WS] accepted", flush=True)
    puck_id = None
    from plugins.neikos.spatial.esp32 import get_puck_manager
    pm = get_puck_manager()
    if pm:
        import asyncio as _aio
        pm.set_loop(_aio.get_running_loop())
    try:
        while True:
            data = await websocket.receive_json()
            print(f"[WS] received: {data}", flush=True)
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif data.get("type") == "register":
                puck_id = data["puck_id"]
                if pm:
                    pm.register(puck_id, data["node_id"], websocket)
                await websocket.send_json({"type": "registered", "node_id": data["node_id"]})
    except WebSocketDisconnect:
        if pm and puck_id:
            pm.unregister(puck_id)
        print("[WS] disconnected", flush=True)

print("[DIAG] WS route registered", flush=True)
print(f"[DIAG] App routes: {[getattr(r, 'path', '?') for r in app.routes]}", flush=True)

import asyncio
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
config = uvicorn.Config(app, host="127.0.0.1", port=7801, log_level="debug", ws="websockets")
server = uvicorn.Server(config)
loop.run_until_complete(server.serve())
