"""
Verify ESP32 puck WebSocket flow end-to-end:
  1. Connect via ws://127.0.0.1:7700/ws/puck
  2. Register puck at player's current node
  3. Confirm puck status shows connected + loop_ready
  4. Ping/pong test
  5. Interact (button press) -> should queue explore action
  6. Move player -> on_player_move should fire, puck should receive activate msg
"""
import asyncio
import json
import urllib.request

BASE = "http://127.0.0.1:7700"
WS_URI = "ws://127.0.0.1:7700/ws/puck"

def get_json(path):
    r = urllib.request.urlopen(BASE + path)
    return json.loads(r.read())

def post_json(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST")
    r = urllib.request.urlopen(req)
    return json.loads(r.read())

async def main():
    try:
        import websockets
    except ImportError:
        print("SKIP: websockets not installed (pip install websockets)")
        return

    # Get current state
    st = get_json("/api/state")
    player_loc = st["player_location"]
    tick_before = st["tick"]
    print(f"[1] Player at: {player_loc}, tick={tick_before}")

    print(f"[2] Connecting to {WS_URI}")
    async with websockets.connect(WS_URI) as ws:
        print("[2] Connected")

        # Register puck at player's node
        await ws.send(json.dumps({"type": "register", "puck_id": "test-puck-01", "node_id": player_loc}))
        print(f"[3] Registered puck at {player_loc}")

        # Small delay, then check puck status
        await asyncio.sleep(0.2)
        puck_st = get_json("/api/puck/status")
        print(f"[4] Puck status: connected={puck_st['connected']}, loop_ready={puck_st['loop_ready']}")
        print(f"    Pucks: {puck_st['pucks']}")
        assert puck_st["connected"] == 1, f"Expected 1 connected puck, got {puck_st['connected']}"
        assert puck_st["loop_ready"] == True, f"Loop not ready: {puck_st}"

        # Ping/pong
        await ws.send(json.dumps({"type": "ping"}))
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            parsed = json.loads(msg)
            print(f"[5] Pong received: {parsed}")
            assert parsed.get("type") == "pong", f"Expected pong, got {parsed}"
        except asyncio.TimeoutError:
            print("[5] FAIL: No pong within 2s")
            return

        # Interact (button press) - should trigger explore at player's node
        await ws.send(json.dumps({"type": "interact", "puck_id": "test-puck-01", "node_id": player_loc}))
        print("[6] Sent interact (button press)")
        await asyncio.sleep(0.6)

        st2 = get_json("/api/state")
        tick_after = st2["tick"]
        print(f"[6] Tick after interact: {tick_after} (was {tick_before})")
        evts = [e["type"] for e in st2.get("recent_events", [])]
        print(f"    Recent event types: {evts}")
        if tick_after > tick_before:
            print("[6] PASS: interact triggered game action (tick advanced)")
        else:
            print("[6] WARN: tick did not advance - explore may have failed silently")
            # Could be non-explorable node type; check what type player is at
            map_data = get_json("/api/map")
            for node in map_data.get("nodes", []):
                if node.get("node_id") == player_loc:
                    print(f"    Node type: {node.get('node_type')}")
                    break

        # Now move to an adjacent node - should receive activate message on puck
        neighbors = st2.get("neighbors") or map_data.get("neighbors", [])
        if not neighbors:
            map_data = get_json("/api/map")
            neighbors = map_data.get("neighbors", [])
        
        if neighbors:
            target = neighbors[0]
            print(f"[7] Moving player to {target} to test on_player_move broadcast")
            move_result = post_json("/api/move", {"node_id": target})
            print(f"    Move result: ok={move_result.get('ok')}, loc={move_result.get('player_location')}")
            
            # Puck should receive activate or ambient message
            try:
                msg2 = await asyncio.wait_for(ws.recv(), timeout=2.0)
                parsed2 = json.loads(msg2)
                print(f"[7] Received WS message after move: {parsed2}")
                msg_type = parsed2.get("type")
                if msg_type in ("activate", "ambient"):
                    print(f"[7] PASS: on_player_move broadcast works (type={msg_type})")
                else:
                    print(f"[7] UNEXPECTED message type: {msg_type}")
            except asyncio.TimeoutError:
                print("[7] FAIL: No WS message received after player move (2s timeout)")
        else:
            print("[7] SKIP: No neighbors to move to")

    # After disconnect, puck should be unregistered
    await asyncio.sleep(0.2)
    puck_st2 = get_json("/api/puck/status")
    print(f"[8] After disconnect: connected={puck_st2['connected']} (expected 0)")
    if puck_st2["connected"] == 0:
        print("[8] PASS: Puck unregistered on disconnect")
    else:
        print("[8] FAIL: Puck still registered after WS close")

    print("\nDone.")

asyncio.run(main())
