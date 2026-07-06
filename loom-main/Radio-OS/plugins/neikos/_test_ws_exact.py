"""
Reproduce: WS route defined inside an inner try block (exactly as in __init__.py lines 10161-10205).
"""
import asyncio, json, threading, time
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.middleware.cors import CORSMiddleware
import uvicorn
import websockets as wslib

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Many routes first
for i in range(30):
    path = f"/api/test{i}"
    @app.get(path)
    async def handler(i=i):
        return {"i": i}

# Inner try block exactly as in __init__.py
try:
    from fastapi import WebSocket, WebSocketDisconnect

    @app.websocket("/ws/puck")
    async def puck_ws(websocket: WebSocket):
        await websocket.accept()
        puck_id = None
        try:
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif data.get("type") == "register":
                    puck_id = data.get("puck_id")
                    await websocket.send_json({"type": "registered", "node_id": data.get("node_id")})
        except WebSocketDisconnect:
            pass

    @app.get("/api/puck/status")
    async def get_puck_status():
        return {"ok": True, "connected": 0, "loop_ready": False, "pucks": []}

except Exception as _ws_err:
    print(f"[NK] WebSocket route setup FAILED: {_ws_err}", flush=True)


def run():
    config = uvicorn.Config(app, host="127.0.0.1", port=7799, log_level="error", ws="websockets")
    server = uvicorn.Server(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(server.serve())

t = threading.Thread(target=run, daemon=True)
t.start()
time.sleep(1.5)

async def test():
    uri = "ws://127.0.0.1:7799/ws/puck"
    print(f"Connecting to {uri}", flush=True)
    try:
        async with wslib.connect(uri, open_timeout=5) as ws:
            await ws.send(json.dumps({"type": "ping"}))
            r = await asyncio.wait_for(ws.recv(), timeout=3)
            print(f"OK: {r}", flush=True)
    except wslib.exceptions.InvalidStatus as e:
        print(f"FAIL HTTP {e.response.status_code}", flush=True)
    except Exception as ex:
        print(f"FAIL {type(ex).__name__}: {ex}", flush=True)

asyncio.run(test())
