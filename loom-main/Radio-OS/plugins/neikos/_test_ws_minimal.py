"""
Minimal test: does a FastAPI WebSocket route registered inside a try/except 
work when reached via uvicorn with wsproto?
"""
import sys
import asyncio
import threading
import socket
import base64
import os

sys.path.insert(0, r"C:\Users\evana\OneDrive\Documents\Radio-OS")

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

app = FastAPI()

@app.websocket("/ws/test")
async def ws_test(websocket: WebSocket):
    print("WS /ws/test: accepted", flush=True)
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        print("WS disconnected", flush=True)

# Start server in thread
ready = threading.Event()

def run_server():
    config = uvicorn.Config(app, host="127.0.0.1", port=7777, log_level="info", ws="wsproto")
    server = uvicorn.Server(config)
    
    original_startup = server.startup
    async def patched_startup():
        await original_startup()
        ready.set()
    server.startup = patched_startup
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(server.serve())
    finally:
        loop.close()

t = threading.Thread(target=run_server, daemon=True)
t.start()
ready.wait(timeout=5.0)

# Now test the WebSocket
def test_ws():
    key = base64.b64encode(os.urandom(16)).decode()
    s = socket.create_connection(("127.0.0.1", 7777))
    request = (
        f"GET /ws/test HTTP/1.1\r\n"
        f"Host: 127.0.0.1:7777\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    s.sendall(request.encode())
    s.settimeout(3.0)
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(4096)
        if not chunk:
            break
        resp += chunk
    print(f"Response: {resp[:300]}")
    s.close()

import time
time.sleep(1.0)
test_ws()
