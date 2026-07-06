"""Quick test: which uvicorn ws= impl actually accepts websocket connections?"""
import asyncio
import json
import threading
import time
import sys

from fastapi import FastAPI, WebSocket
import uvicorn

app2 = FastAPI()

@app2.websocket("/ws/test")
async def ws_test(ws: WebSocket):
    await ws.accept()
    d = await ws.receive_json()
    await ws.send_json({"echo": d})


def run_server(ws_impl, port):
    config = uvicorn.Config(app2, host="127.0.0.1", port=port, log_level="error", ws=ws_impl)
    server = uvicorn.Server(config)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.serve())
    finally:
        loop.close()


async def test_impl(name, port):
    import websockets as wslib
    try:
        async with wslib.connect(f"ws://127.0.0.1:{port}/ws/test", open_timeout=4) as ws:
            await ws.send(json.dumps({"x": 1}))
            r = await asyncio.wait_for(ws.recv(), timeout=3)
            print(f"{name}: OK -> {r}", flush=True)
            return True
    except Exception as e:
        print(f"{name}: FAIL -> {type(e).__name__}: {e}", flush=True)
        return False


impls = [("websockets", 7791), ("wsproto", 7792), ("auto", 7793)]

for impl, port in impls:
    t = threading.Thread(target=run_server, args=(impl, port), daemon=True)
    t.start()

time.sleep(2.0)

results = []
for impl, port in impls:
    ok = asyncio.run(test_impl(impl, port))
    results.append((impl, ok))

print("\nSummary:")
for impl, ok in results:
    print(f"  {impl}: {'PASS' if ok else 'FAIL'}")
