"""Trigger NK server reload via /api/_reload endpoint."""
import urllib.request
import json
import time

print("Triggering reload...")
try:
    req = urllib.request.Request(
        "http://127.0.0.1:7700/api/_reload",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        print("Reload response:", json.load(r))
except Exception as e:
    print(f"Reload trigger: {e} (server may be restarting)")

# Wait for server to come back
print("Waiting for server to restart...")
for i in range(15):
    time.sleep(1)
    try:
        with urllib.request.urlopen("http://127.0.0.1:7700/api/state", timeout=2) as r:
            state = json.load(r)
            print(f"Server back online at tick {state['tick']}")
            break
    except Exception:
        print(f"  ...waiting ({i+1}s)")
else:
    print("Server did not come back in 15s")
