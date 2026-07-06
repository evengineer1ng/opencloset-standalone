"""
Verify the Knower unlock path end-to-end.
- Drives player to explore 38+ unique nodes (exploration_depth >= 30)
- Explores 2+ FACILITY/LANDMARK nodes (research_investment >= 20)
- Confirms /api/knower returns is_unlocked: true
"""
import urllib.request
import urllib.error
import json
import time
import sys
from collections import deque

BASE = "http://127.0.0.1:7700/api"


def get(path):
    with urllib.request.urlopen(f"{BASE}{path}") as r:
        return json.loads(r.read())


def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def bfs_path(node_dict, start, target):
    """Return shortest path from start to target as list of node_ids."""
    visited = {start}
    queue = deque([[start]])
    while queue:
        path = queue.popleft()
        node = path[-1]
        if node == target:
            return path
        for nb in node_dict.get(node, {}).get("neighbors", []):
            if nb not in visited:
                visited.add(nb)
                queue.append(path + [nb])
    return None


def bfs_farthest(node_dict, start, visited_nodes):
    """Find a node not yet visited that is reachable from start."""
    seen = {start}
    queue = deque([[start]])
    while queue:
        path = queue.popleft()
        node = path[-1]
        if node not in visited_nodes and node != start:
            return path  # first unvisited reachable node
        for nb in node_dict.get(node, {}).get("neighbors", []):
            if nb not in seen:
                seen.add(nb)
                queue.append(path + [nb])
    return None


def move_to(node_id):
    result = post("/move", {"node_id": node_id})
    return result.get("ok") == True


def explore_current():
    result = post("/explore", {})
    return result


def main():
    # ── Load map ──────────────────────────────────────────────
    map_data = get("/map")
    node_list = map_data.get("nodes", [])
    if isinstance(node_list, list):
        node_dict = {n["node_id"]: n for n in node_list}
    else:
        node_dict = node_list  # dict already

    facility_nodes = {
        nid for nid, n in node_dict.items()
        if n.get("node_type") in ("FACILITY", "LANDMARK")
    }
    print(f"[knower-verify] Map loaded: {len(node_dict)} nodes, {len(facility_nodes)} FACILITY/LANDMARK")

    # ── Current state ──────────────────────────────────────────
    state = get("/state")
    traj = state.get("trajectory", {})
    print(f"[knower-verify] Start: exploration_depth={traj.get('exploration_depth',0):.1f} "
          f"research_investment={traj.get('research_investment',0):.1f} "
          f"nodes_explored={traj.get('nodes_explored',0)}")

    current = state.get("player_location", "")
    visited = set()
    visited.add(current)
    research_explores = 0

    # ── Phase 1: reach a FACILITY and explore it twice ─────────
    # Find nearest FACILITY not yet explored
    sorted_facs = list(facility_nodes)
    target_fac = sorted_facs[0]

    print(f"[knower-verify] Routing to first FACILITY: {target_fac}")
    path = bfs_path(node_dict, current, target_fac)
    if not path:
        print(f"[knower-verify] ERROR: No path to {target_fac}")
        sys.exit(1)

    # Move along path (skip first element = current)
    for nid in path[1:]:
        ok = move_to(nid)
        visited.add(nid)
        current = nid
        time.sleep(0.15)
        if not ok:
            print(f"[knower-verify] Move to {nid} failed — continuing")

    # Explore the facility
    print(f"[knower-verify] Exploring FACILITY {current}")
    explore_current()
    research_explores += 1
    time.sleep(0.2)

    # Head to second FACILITY for 2nd +10 research
    target_fac2 = None
    for nid in sorted_facs:
        if nid != target_fac:
            target_fac2 = nid
            break

    if target_fac2:
        print(f"[knower-verify] Routing to second FACILITY: {target_fac2}")
        path2 = bfs_path(node_dict, current, target_fac2)
        if path2:
            for nid in path2[1:]:
                ok = move_to(nid)
                visited.add(nid)
                current = nid
                time.sleep(0.15)
            print(f"[knower-verify] Exploring FACILITY {current}")
            explore_current()
            research_explores += 1
            time.sleep(0.2)

    # ── Phase 2: move to 38 unique nodes total ─────────────────
    print(f"[knower-verify] Visited so far: {len(visited)} nodes")
    moves_needed = max(0, 38 - len(visited))
    print(f"[knower-verify] Need {moves_needed} more unique nodes for exploration_depth >= 30")

    stall_count = 0
    while len(visited) < 38 and stall_count < 5:
        path_to_new = bfs_farthest(node_dict, current, visited)
        if not path_to_new:
            stall_count += 1
            print(f"[knower-verify] No more unvisited nodes reachable (stall {stall_count})")
            break

        for nid in path_to_new[1:]:
            if len(visited) >= 38:
                break
            ok = move_to(nid)
            visited.add(nid)
            current = nid
            time.sleep(0.12)

        stall_count = 0

    # ── Check trajectory ───────────────────────────────────────
    state2 = get("/state")
    traj2 = state2.get("trajectory", {})
    ed = traj2.get("exploration_depth", 0)
    ri = traj2.get("research_investment", 0)
    print(f"[knower-verify] Final: exploration_depth={ed:.1f} research_investment={ri:.1f} "
          f"nodes_explored={traj2.get('nodes_explored',0)}")

    # ── Check knower ────────────────────────────────────────────
    knower = get("/knower")
    unlocked = knower.get("is_unlocked", False)
    print(f"[knower-verify] Knower is_unlocked: {unlocked}")
    print(f"[knower-verify] Knower unlock_thresholds: {knower.get('unlock_thresholds')}")
    print(f"[knower-verify] Knower location: {knower.get('location_node_id')}")

    if ed >= 30 and ri >= 20:
        if unlocked:
            print("[knower-verify] ✓ PASS — thresholds met, knower unlocked correctly")
        else:
            print("[knower-verify] ✗ FAIL — thresholds met but knower still locked!")
            sys.exit(2)
    else:
        print(f"[knower-verify] ✗ FAIL — thresholds NOT met: need ed>=30 ri>=20, got {ed:.1f}/{ri:.1f}")
        sys.exit(3)

    # ── Try talking to the Knower ──────────────────────────────
    print("[knower-verify] Testing /api/knower/talk ...")
    talk_result = post("/knower/talk", {"fragment_index": 0})
    print(f"[knower-verify] talk result: {json.dumps(talk_result)[:300]}")

    if talk_result.get("error"):
        print(f"[knower-verify] ✗ FAIL — talk returned error: {talk_result['error']}")
        sys.exit(4)
    else:
        print("[knower-verify] ✓ Knower dialogue returned successfully")

    print("[knower-verify] ✓ ALL CHECKS PASSED — Knower unlock path fully verified")


if __name__ == "__main__":
    main()
