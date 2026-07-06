"""Simulate exact ws route registration as in __init__.py to catch errors."""
import sys
sys.path.insert(0, r'C:\Users\evana\OneDrive\Documents\Radio-OS')

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

app = FastAPI(title="Neikos test")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

try:
    from fastapi import WebSocket, WebSocketDisconnect
    from plugins.neikos.spatial.esp32 import get_puck_manager as _gpm
    print("Imports OK")

    @app.websocket("/ws/puck")
    async def puck_ws(websocket: WebSocket):
        pass

    @app.get("/api/puck/status")
    async def get_puck_status():
        pm = _gpm()
        return {"ok": True, "connected": pm.connected_count() if pm else 0}

    print("Route registration OK")
    print("Routes:", [r.path for r in app.routes])

except Exception as e:
    import traceback
    print("FAILED:", e)
    traceback.print_exc()
