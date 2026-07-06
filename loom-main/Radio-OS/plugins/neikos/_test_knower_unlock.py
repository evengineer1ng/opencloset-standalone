"""
Verify the Knower unlock path end-to-end by:
1. Moving to a FACILITY/LANDMARK and exploring repeatedly to build research_investment + exploration_depth
2. Checking /api/knower after each batch of moves/explores
"""
import urllib.request, json, time

API = 'http://127.0.0.1:7700'

def get(path):
    r = urllib.request.urlopen(f'{API}{path}')
    return json.load(r)

def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f'{API}{path}', data=data,
                                  headers={'Content-Type': 'application/json'}, method='POST')
    r = urllib.request.urlopen(req)
    return json.load(r)

# Check initial state
state = get('/api/state')
traj = state['trajectory']
print(f"Start: exploration_depth={traj['exploration_depth']:.1f}, research_investment={traj['research_investment']:.1f}")
print(f"Player location: {state['player_location']}")

knower = get('/api/knower')
print(f"Knower: {knower['archetype']}, unlocked={knower['is_unlocked']}")
print(f"  Thresholds: {knower['unlock_thresholds']}")

# Get map to find a LANDMARK or FACILITY node reachable from current location
mdata = get('/api/map')
nodes = {n['node_id']: n for n in mdata['nodes']}

# Find a path to a LANDMARK/FACILITY node
# BFS from current location
from collections import deque
start = state['player_location']
target_types = {'LANDMARK', 'FACILITY'}
queue = deque([(start, [start])])
visited = {start}
path_to_landmark = None

while queue:
    cur, path = queue.popleft()
    n = nodes.get(cur, {})
    if n.get('node_type') in target_types and cur != start:
        path_to_landmark = path
        break
    for nb in n.get('neighbors', []):
        if nb not in visited:
            visited.add(nb)
            queue.append((nb, path + [nb]))

if path_to_landmark:
    print(f"\nPath to {nodes[path_to_landmark[-1]]['name']} ({nodes[path_to_landmark[-1]]['node_type']}): {path_to_landmark}")
    # Navigate there
    for nid in path_to_landmark[1:]:
        r = post('/api/move', {'node_id': nid})
        print(f"  Moved to {nid}: ok={r.get('ok')}")
    
    # Now explore 10 times at the landmark
    target_node = path_to_landmark[-1]
    print(f"\nExploring at {nodes[target_node]['name']} 10 times...")
    for i in range(10):
        r = post('/api/explore')
        evtypes = [e['type'] for e in r.get('events', [])]
        print(f"  Explore {i+1}: {evtypes}")
    
    # Check trajectory
    state = get('/api/state')
    traj = state['trajectory']
    print(f"\nAfter exploring: exploration_depth={traj['exploration_depth']:.1f}, research_investment={traj['research_investment']:.1f}")
    
    # Check knower
    knower = get('/api/knower')
    print(f"Knower unlocked: {knower['is_unlocked']}")
    if knower['is_unlocked']:
        print("  ✅ Knower unlock path VERIFIED")
        # Try talking to knower
        talk = post('/api/knower/talk', {'fragment_index': 0})
        print(f"  Talk result: event_type={talk.get('event_type')}")
        if talk.get('event_type') == 'knower_dialogue':
            print(f"  ✅ Dialogue returned: {str(talk.get('dialogue', ''))[:80]}...")
    else:
        print(f"  Still locked. Thresholds: {knower['unlock_thresholds']}")
else:
    print("No LANDMARK/FACILITY reachable from current location")
