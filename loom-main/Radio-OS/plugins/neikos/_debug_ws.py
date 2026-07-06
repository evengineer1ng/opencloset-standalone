"""
Debug the running server's route table and websocket behavior.
Adds a temporary /api/debug/wsroutes endpoint by hot-patching the app.
"""
import sys
import urllib.request
import json
import socket
import base64
import os

BASE = "http://127.0.0.1:7700"


def http_get(path):
    with urllib.request.urlopen(f"{BASE}{path}", timeout=5) as r:
        return json.loads(r.read())


def try_ws(path="/ws/puck"):
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [
        f"GET {path} HTTP/1.1",
        "Host: 127.0.0.1:7700",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
        "Origin: http://127.0.0.1:7700",
        "",
        "",
    ]
    req = "\r\n".join(lines)
    s = socket.socket()
    s.connect(("127.0.0.1", 7700))
    s.settimeout(5)
    s.send(req.encode())
    try:
        resp = s.recv(4096).decode(errors="replace")
        return resp.split("\r\n")[0]
    except Exception as e:
        return f"Error: {e}"
    finally:
        s.close()


# Check server is up
try:
    state = http_get("/api/state")
    print(f"Server up: seed={state['seed']}, tick={state['tick']}")
except Exception as e:
    print(f"Server down: {e}")
    sys.exit(1)

# Check puck status
status = http_get("/api/puck/status")
print(f"Puck status: {status}")

# Test websocket paths
for path in ["/ws/puck", "/ws/nonexistent", "/api/state"]:
    result = try_ws(path)
    print(f"  WS upgrade {path}: {result}")

# OpenAPI paths
spec = http_get("/openapi.json")
paths = sorted(spec["paths"].keys())
print(f"\nOpenAPI paths ({len(paths)} total):")
for p in paths:
    if "puck" in p or "ws" in p:
        print(f"  ** {p}")

# Check if /ws/puck shows 101 after manual registration check
# The 403 for /api/state (not a WS endpoint) vs /ws/puck confirms routing
print("\nConclusion:")
if try_ws("/ws/puck").startswith("HTTP/1.1 403"):
    # Both registered and non-registered paths return 403
    # This is a wsproto-level rejection, not routing
    print("Both /ws/puck AND non-existent paths return 403.")
    print("This means the ASGI app is sending websocket.close before accept.")
    print("Most likely cause: puck_ws function is raising an exception before websocket.accept()")
    print("OR the FastAPI app's middleware is closing the connection.")
