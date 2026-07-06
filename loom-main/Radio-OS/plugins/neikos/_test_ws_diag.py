"""
Trace the live Neikos server WebSocket connection via monkey-patching
to find WHERE the 403 originates.

Strategy: connect to port 7700 via HTTP API to trigger a diagnostic endpoint
that will tell us if the WS route is actually in the app.router.routes.
"""
import urllib.request
import json

BASE = "http://127.0.0.1:7700"

def api(path):
    r = urllib.request.urlopen(BASE + path, timeout=5)
    return json.loads(r.read())

# Check if the puck status endpoint works (it's in the same try block as the WS route)
status = api("/api/puck/status")
print(f"/api/puck/status: {status}")

# Check pucks list endpoint (slightly different - checks pm.pucks)
pucks = api("/api/pucks")
print(f"/api/pucks: {pucks}")

# Get openapi to see all routes
openapi = api("/openapi.json")
paths = sorted(openapi.get("paths", {}).keys())
print(f"\nRegistered HTTP routes ({len(paths)}):")
for p in paths:
    print(f"  {p}")

print("\nNote: WS routes don't appear in OpenAPI JSON.")
print("The 403 on /ws/puck indicates the route IS recognized but the ASGI app")
print("sends websocket.close (which uvicorn maps to 403).")
print()
print("This is likely a FastAPI router middleware issue where the WS handshake")
print("request is being processed by a dependency or exception handler that")
print("sends close before the handler runs.")
print()
print("Check: Is there an app-wide exception handler or dependency?")
print("Next step: restart server with debug logging and check for ASGI exceptions.")
