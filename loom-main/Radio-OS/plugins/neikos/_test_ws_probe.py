"""Test whether the LIVE server returns 403 and probe why."""
import asyncio
import json
import websockets

async def test():
    print("--- no origin, direct ---")
    try:
        async with websockets.connect("ws://127.0.0.1:7700/ws/puck") as ws:
            await ws.send(json.dumps({"type": "ping"}))
            r = await asyncio.wait_for(ws.recv(), timeout=3)
            print("  OK:", r)
    except websockets.exceptions.InvalidStatus as e:
        print(f"  HTTP {e.response.status_code}")
    except Exception as e:
        print(f"  {type(e).__name__}: {e}")

    print("--- existing endpoint /api/state as HTTP works ---")
    import urllib.request
    r = urllib.request.urlopen("http://127.0.0.1:7700/api/state")
    print(f"  HTTP {r.status}")

asyncio.run(test())
