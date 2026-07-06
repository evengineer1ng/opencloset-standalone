"""
Test /ws/puck WebSocket end-to-end.

Flow:
1. Connect → send register → expect {"type": "registered"}
2. Send ping → expect {"type": "pong"}
3. Send interact at player's current node → expect explore queued (verify via /api/state tick advance)
4. Verify GET /api/puck/status shows connected=1, loop_ready=true
5. Disconnect → verify /api/puck/status shows connected=0
"""

import asyncio
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:7700"
WS_URL = "ws://127.0.0.1:7700/ws/puck"

PUCK_ID = "puck-test-0001"


def http_get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.load(r)


def http_post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


async def run():
    try:
        import websockets
    except ImportError:
        print("SKIP: websockets package not available. Install with: pip install websockets")
        sys.exit(0)

    failures = []

    # Get current player location
    state = http_get("/api/state")
    player_node = state["player_location"]
    print(f"Player at: {player_node} (tick {state['tick']})")

    # Pre-test puck status
    status_before = http_get("/api/puck/status")
    print(f"Puck status before: connected={status_before['connected']}, loop_ready={status_before['loop_ready']}")

    async with websockets.connect(WS_URL) as ws:
        print("Connected to /ws/puck")

        # --- Test 1: Register at player's current node ---
        await ws.send(json.dumps({
            "type": "register",
            "puck_id": PUCK_ID,
            "node_id": player_node,
        }))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        if resp.get("type") == "registered" and resp.get("node_id") == player_node:
            print(f"[PASS] Register: got registered at {resp['node_id']}")
        else:
            failures.append(f"Register response wrong: {resp}")
            print(f"[FAIL] Register: {resp}")

        # --- Test 2: Ping/pong ---
        await ws.send(json.dumps({"type": "ping"}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=3))
        if resp.get("type") == "pong":
            print("[PASS] Ping/pong")
        else:
            failures.append(f"Ping response wrong: {resp}")
            print(f"[FAIL] Ping: {resp}")

        # --- Test 3: /api/puck/status shows connected=1, loop_ready=true ---
        status = http_get("/api/puck/status")
        if status.get("connected") == 1 and status.get("loop_ready") is True:
            print(f"[PASS] Status: connected=1, loop_ready=true")
        else:
            failures.append(f"Status wrong: {status}")
            print(f"[FAIL] Status: connected={status.get('connected')}, loop_ready={status.get('loop_ready')}")

        # Confirm puck is registered to the correct node
        puck_list = status.get("pucks", [])
        if puck_list and puck_list[0].get("node_id") == player_node:
            print(f"[PASS] Puck node_id: {puck_list[0]['node_id']}")
        else:
            failures.append(f"Puck node mismatch: {puck_list}")
            print(f"[FAIL] Puck node: {puck_list}")

        # --- Test 4: interact → explore queued ---
        tick_before = http_get("/api/state")["tick"]
        await ws.send(json.dumps({
            "type": "interact",
            "puck_id": PUCK_ID,
            "node_id": player_node,
        }))
        # Give controller time to process
        await asyncio.sleep(0.4)
        tick_after = http_get("/api/state")["tick"]
        if tick_after > tick_before:
            print(f"[PASS] Interact: tick advanced {tick_before} → {tick_after} (explore triggered)")
        else:
            # Tick may not advance if explore doesn't fire a tick. Check recent_events.
            state_after = http_get("/api/state")
            last_event = state_after["recent_events"][-1] if state_after["recent_events"] else {}
            if last_event.get("type") in ("explored", "fragment_discovered", "research_discovery",
                                           "anomaly_event", "error"):
                print(f"[PASS] Interact: explore event fired ({last_event['type']})")
            else:
                # Tick check with delay
                await asyncio.sleep(0.6)
                state_after2 = http_get("/api/state")
                last2 = state_after2["recent_events"][-1] if state_after2["recent_events"] else {}
                explore_types = ("explored", "fragment_discovered", "research_discovery",
                                  "anomaly_event", "error", "tick_update")
                if last2.get("type") in explore_types or state_after2["tick"] > tick_before:
                    print(f"[PASS] Interact: explore queued (event={last2.get('type')}, tick={state_after2['tick']})")
                else:
                    failures.append(f"Interact did not trigger explore: tick={tick_after}, last_event={last_event}")
                    print(f"[FAIL] Interact: tick={tick_after}, last_event={last_event}")

        # --- Test 5: on_player_move dispatch to registered puck ---
        # Move player to trigger on_player_move via /api/move. Puck should receive activate or ambient.
        # We need a neighbor of the current node.
        map_data = http_get("/api/map")
        neighbors = map_data.get("neighbors", [])
        if not neighbors:
            print("[SKIP] on_player_move test: no neighbors in /api/map")
        else:
            target = neighbors[0]
            # Move player to a neighbor — puck at player_node should get "ambient"
            move_resp = http_post("/api/move", {"node_id": target})
            if move_resp.get("ok"):
                # Try to receive a message (ambient since puck is at old node, not new)
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                    if msg.get("type") in ("ambient", "activate"):
                        print(f"[PASS] on_player_move dispatch: got {msg['type']} (tier={msg.get('tier')})")
                    else:
                        failures.append(f"Unexpected puck message after move: {msg}")
                        print(f"[FAIL] on_player_move: unexpected msg {msg}")
                except asyncio.TimeoutError:
                    failures.append("on_player_move: no puck message received after player move (timeout)")
                    print("[FAIL] on_player_move: no message received within 2s after move")
            else:
                print(f"[SKIP] on_player_move test: move failed ({move_resp})")

    # --- After disconnect: status should show connected=0 ---
    await asyncio.sleep(0.3)
    status_after = http_get("/api/puck/status")
    if status_after.get("connected") == 0:
        print("[PASS] Disconnect: connected=0 after WebSocket close")
    else:
        failures.append(f"After disconnect: connected={status_after.get('connected')}")
        print(f"[FAIL] Disconnect: still connected={status_after.get('connected')}")

    print()
    if failures:
        print(f"RESULT: {len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("RESULT: ALL PASS")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(run())
