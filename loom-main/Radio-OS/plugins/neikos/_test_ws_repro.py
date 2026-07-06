"""
Reproduce the exact Neikos server WS 403 in an isolated test.

The goal: build a stack that matches __init__.py exactly and see if WS works.
If this test works, the issue is something at startup-time in the real server.
If this fails, we can narrow down the cause.
"""
import asyncio
import json
import threading
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.middleware.cors import CORSMiddleware
import uvicorn
import websockets as wslib


# ── Exact replica of __init__.py server stack ──────────────────────────────

app = FastAPI(title="Neikos: Hundred Islands")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Simulated puck manager
class FakePM:
    def set_loop(self, loop): pass
    def register(self, pid, nid, ws): pass
    def unregister(self, pid): pass
    def on_interact(self, pid): pass
    def status(self): return {"connected": 1, "loop_ready": True, "pucks": []}


_pm = FakePM()


@app.get("/api/puck/status")
async def get_puck_status():
    return {"ok": True, **_pm.status()}


try:
    from fastapi import WebSocket, WebSocketDisconnect

    @app.websocket("/ws/puck")
    async def puck_ws(websocket: WebSocket):
        print("[WS] Connection attempt", flush=True)
        await websocket.accept()
        print("[WS] Accepted", flush=True)
        puck_id = None
        pm = _pm
        if pm:
            import asyncio as _aio
            pm.set_loop(_aio.get_running_loop())
        try:
            while True:
                data = await websocket.receive_json()
                print(f"[WS] Received: {data}", flush=True)
                if data.get("type") == "register":
                    puck_id = data["puck_id"]
                    node_id = data["node_id"]
                    if pm:
                        pm.register(puck_id, node_id, websocket)
                elif data.get("type") == "interact":
                    if pm and puck_id:
                        pm.on_interact(puck_id)
                elif data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            if pm and puck_id:
                pm.unregister(puck_id)

    print("[NK] WS route registered OK", flush=True)
except Exception as e:
    print(f"[NK] WS route FAILED: {e}", flush=True)


# ── Run test ────────────────────────────────────────────────────────────────

def run_server():
    config = uvicorn.Config(app, host="127.0.0.1", port=7796, log_level="info", ws="websockets")
    server = uvicorn.Server(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.serve())


t = threading.Thread(target=run_server, daemon=True)
t.start()
time.sleep(1.5)


async def test():
    uri = "ws://127.0.0.1:7796/ws/puck"
    print(f"\nConnecting to {uri}", flush=True)
    try:
        async with wslib.connect(uri, open_timeout=8) as ws:
            print("Connected OK", flush=True)
            await ws.send(json.dumps({"type": "ping"}))
            r = await asyncio.wait_for(ws.recv(), timeout=3)
            print(f"Got: {r}", flush=True)
            
            # Try register
            await ws.send(json.dumps({
                "type": "register",
                "puck_id": "puck-test-001",
                "node_id": "sp_0003",
            }))
            print("Sent register", flush=True)
            await asyncio.sleep(0.5)
            print("Test PASSED", flush=True)
            return True
    except wslib.exceptions.InvalidStatus as e:
        print(f"FAILED: HTTP {e.response.status_code}", flush=True)
        return False
    except Exception as ex:
        print(f"FAILED: {type(ex).__name__}: {ex}", flush=True)
        return False


ok = asyncio.run(test())
print(f"\nResult: {'PASS' if ok else 'FAIL'}", flush=True)
