"""
End-to-end WebSocket /ws/puck verification.

Tests:
1. Connect, register, get 'registered' response
2. Ping → pong
3. Interact trigger fires explore on controller (observe recent_events)
4. /api/puck/status shows connected=1
5. Disconnect → connected=0
"""
import asyncio
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:7700"

def api(path, method="GET", body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())

async def run():
    try:
        import websockets
    except ImportError:
        print("SKIP: websockets package not installed (pip install websockets)")
        sys.exit(0)

    errors = []

    # 1. Get a node_id to register at (use current player location)
    state = api("/api/state")
    player_loc = state["player_location"]
    print(f"Player is at: {player_loc}")

    # 2. Connect WebSocket
    uri = "ws://127.0.0.1:7700/ws/puck"
    async with websockets.connect(uri) as ws:
        # 3. Register
        await ws.send(json.dumps({
            "type": "register",
            "puck_id": "test-puck-01",
            "node_id": player_loc,
        }))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
        print(f"Register response: {resp}")
        if resp.get("type") != "registered":
            errors.append(f"Expected 'registered', got: {resp}")
        elif resp.get("node_id") != player_loc:
            errors.append(f"node_id mismatch: {resp.get('node_id')} != {player_loc}")
        else:
            print("✅ Register OK")

        # 4. Check /api/puck/status shows 1 connected
        status = api("/api/puck/status")
        print(f"Puck status: {status}")
        if status.get("connected") != 1:
            errors.append(f"Expected connected=1, got: {status.get('connected')}")
        else:
            print("✅ /api/puck/status connected=1 OK")

        # 5. Ping → pong
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
        print(f"Ping/pong: {pong}")
        if pong.get("type") != "pong":
            errors.append(f"Expected pong, got: {pong}")
        else:
            print("✅ Ping/pong OK")

        # 6. Interact: player is at player_loc, puck is at player_loc → should trigger explore
        before_state = api("/api/state")
        before_events = len(before_state.get("recent_events", []))
        before_tick = before_state.get("tick", 0)

        await ws.send(json.dumps({
            "type": "interact",
            "puck_id": "test-puck-01",
            "node_id": player_loc,
        }))

        # Wait briefly for controller to process
        await asyncio.sleep(0.4)
        after_state = api("/api/state")
        after_tick = after_state.get("tick", 0)
        after_events = after_state.get("recent_events", [])

        print(f"Ticks before/after interact: {before_tick} → {after_tick}")
        # Interact at player location should queue an explore, advancing tick
        if after_tick > before_tick:
            print("✅ Interact triggered explore (tick advanced)")
        else:
            # Maybe node has no encounter table or explore fires a no-op
            # Check if any new event appeared
            if len(after_events) > before_events:
                print("✅ Interact triggered event (no tick advance but new event)")
            else:
                errors.append(f"Interact did not advance tick or events: tick={after_tick}, events={len(after_events)}")

        # 7. Player move → puck at target should receive activate message
        # Move to a neighbor
        map_data = api("/api/map")
        neighbors = map_data.get("neighbors", [])
        if neighbors:
            target_node = neighbors[0]
            # Register a second "puck" at that node to capture the activate message
            async with websockets.connect(uri) as ws2:
                await ws2.send(json.dumps({
                    "type": "register",
                    "puck_id": "test-puck-02",
                    "node_id": target_node,
                }))
                reg2 = json.loads(await asyncio.wait_for(ws2.recv(), timeout=3.0))
                print(f"Puck 2 registered at {target_node}: {reg2}")

                # Now move player to target_node
                move_result = api("/api/move", method="POST", body={"node_id": target_node})
                print(f"Move result: {move_result.get('ok')} → {move_result.get('player_location')}")

                if move_result.get("ok"):
                    # puck-02 (at target) should receive 'activate'
                    # puck-01 (at old location) should receive 'ambient'
                    try:
                        msg2 = json.loads(await asyncio.wait_for(ws2.recv(), timeout=3.0))
                        print(f"Puck 2 got: {msg2}")
                        if msg2.get("type") == "activate":
                            print("✅ Target puck received 'activate' on player move")
                        else:
                            errors.append(f"Expected activate on puck2, got: {msg2}")
                    except asyncio.TimeoutError:
                        errors.append("Puck 2 did not receive activate message within 3s")

                    try:
                        msg1 = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                        print(f"Puck 1 got: {msg1}")
                        if msg1.get("type") == "ambient":
                            print("✅ Origin puck received 'ambient' on player move")
                        else:
                            errors.append(f"Expected ambient on puck1, got: {msg1}")
                    except asyncio.TimeoutError:
                        errors.append("Puck 1 did not receive ambient message within 3s")
        else:
            print("SKIP: no neighbors to test activate/ambient flow")

    # After disconnect, check status
    await asyncio.sleep(0.3)
    status2 = api("/api/puck/status")
    print(f"Post-disconnect puck status: {status2}")
    if status2.get("connected") == 0:
        print("✅ Disconnect → connected=0 OK")
    else:
        errors.append(f"Expected connected=0 after disconnect, got: {status2.get('connected')}")

    print()
    if errors:
        print(f"❌ {len(errors)} FAILURES:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("✅ ALL PUCK WS TESTS PASSED")

asyncio.run(run())
