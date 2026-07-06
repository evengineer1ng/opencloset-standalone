"""Check if the WS route is actually registered in the running server."""
import urllib.request, json

# Use /api/_reload endpoint with a debug check to see all registered routes
# Actually let's just add a debug endpoint 

# Instead, let's see if we can test the app directly
import sys, os, queue
sys.path.insert(0, '.')

import plugins.neikos as nk
from fastapi import FastAPI, WebSocket
from starlette.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

@app.websocket('/ws/puck')
async def puck_ws(websocket: WebSocket):
    print('WS: accept called')
    await websocket.accept()
    await websocket.send_json({'type': 'ok'})
    await websocket.close()

print('Routes in fresh app:')
for route in app.routes:
    rtype = type(route).__name__
    path = getattr(route, 'path', '?')
    methods = getattr(route, 'methods', '?')
    print(f'  {rtype}: {path} {methods}')
