"""Check if /ws/puck is actually registered in the running FastAPI app."""
import urllib.request
import json

# Get all routes from openapi.json
r = urllib.request.urlopen('http://127.0.0.1:7700/openapi.json', timeout=5)
spec = json.loads(r.read())
paths = list(spec.get('paths', {}).keys())
print("All registered API paths:")
for p in sorted(paths):
    print(f"  {p}")

# WebSocket routes don't appear in OpenAPI, but we can check if uvicorn server
# knows about them via internal state. Let's try a different approach:
# check if there's a debug log in the server's stdout by checking the NK debug route
try:
    r2 = urllib.request.urlopen('http://127.0.0.1:7700/api/debug/routes', timeout=5)
    print("Debug routes:", r2.read().decode()[:500])
except Exception as e:
    print(f"No /api/debug/routes endpoint: {e}")

# Also try to see what Starlette returns for a non-existent WS path vs the registered one
import socket, base64, os

def try_ws_path(path):
    key = base64.b64encode(os.urandom(16)).decode()
    lines = [
        f'GET {path} HTTP/1.1',
        'Host: 127.0.0.1:7700',
        'Upgrade: websocket',
        'Connection: Upgrade',
        f'Sec-WebSocket-Key: {key}',
        'Sec-WebSocket-Version: 13',
        'Origin: http://127.0.0.1:7700',
        '',
        '',
    ]
    req = '\r\n'.join(lines)
    s = socket.socket()
    s.connect(('127.0.0.1', 7700))
    s.settimeout(5)
    s.send(req.encode())
    try:
        resp = s.recv(4096).decode(errors='replace')
        status_line = resp.split('\r\n')[0]
        return status_line
    except Exception as e:
        return f"Error: {e}"
    finally:
        s.close()

print("\nWebSocket upgrade responses:")
print(f"  /ws/puck: {try_ws_path('/ws/puck')}")
print(f"  /ws/nonexistent: {try_ws_path('/ws/nonexistent')}")
print(f"  /ws/test: {try_ws_path('/ws/test')}")
