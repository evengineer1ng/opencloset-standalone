"""Verify FastAPI WebSocket works in this Python env."""
import asyncio
import json
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import threading, time

app2 = FastAPI()

@app2.websocket('/ws/test')
async def ws_test(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            await ws.send_json({'echo': data})
    except WebSocketDisconnect:
        pass

def run():
    uvicorn.run(app2, host='0.0.0.0', port=7798, log_level='error')

t = threading.Thread(target=run, daemon=True)
t.start()
time.sleep(2)

import websockets as wss

async def check():
    try:
        async with wss.connect('ws://127.0.0.1:7798/ws/test') as ws:
            await ws.send(json.dumps({"hi": 1}))
            r = await asyncio.wait_for(ws.recv(), timeout=3)
            print('OK:', r)
    except Exception as e:
        print('FAIL:', type(e).__name__, e)

asyncio.run(check())
