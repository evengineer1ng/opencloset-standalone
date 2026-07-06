import urllib.request, json

API = 'http://127.0.0.1:7700'

# Get current state
r = urllib.request.urlopen(f'{API}/api/state')
state = json.load(r)
print(f"Location: {state['player_location']}, tick={state['tick']}")

# Check local node
r = urllib.request.urlopen(f'{API}/api/local')
local = json.load(r)
print(f"Node: {local['node_name']} ({local['node_type']})")
print(f"Neighbors: {local['neighbors']}")

# Move back to start to see what other types exist
# Let's check all nodes via a topology inspection -- use map endpoint
r = urllib.request.urlopen(f'{API}/api/map')
d = json.load(r)
nodes = d.get('nodes', [])
type_counts = {}
special_nodes = []
for n in nodes:
    nt = n.get('node_type', 'UNKNOWN')
    type_counts[nt] = type_counts.get(nt, 0) + 1
    if nt in ('FACILITY', 'LANDMARK', 'DUNGEON', 'ANOMALY_ZONE'):
        special_nodes.append(f"{n['node_id']}: {n['name']} ({nt})")

print("\nNode type distribution:")
for k, v in sorted(type_counts.items()):
    print(f"  {k}: {v}")

print("\nFragment-eligible nodes:")
for s in special_nodes[:20]:
    print(f"  {s}")
