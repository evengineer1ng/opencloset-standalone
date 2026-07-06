import urllib.request, json, sys, time
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def api(path, data=None, method=None):
    if data is not None:
        req = urllib.request.Request(
            f'http://127.0.0.1:7700{path}',
            data=json.dumps(data).encode(),
            headers={'Content-Type': 'application/json'},
            method=method or 'POST'
        )
    else:
        req = urllib.request.Request(f'http://127.0.0.1:7700{path}')
    r = urllib.request.urlopen(req, timeout=10)
    return json.loads(r.read())

def move(node_id):
    d = api('/api/move', {'node_id': node_id})
    if not d.get('ok'):
        print(f'Move failed: {d.get("events", [])}')
    return d.get('ok', False)

def explore():
    d = api('/api/explore', {})
    events = d.get('events', [])
    for e in events:
        if e['type'] == 'fragment_discovered':
            print(f'FRAGMENT: [{e["data"]["type"]}] {e["data"]["title"]}')
            print(f'  Body: {e["data"]["body"][:200]}')
        elif e['type'] not in ('explored', 'tick_update', 'anomaly_event'):
            print(f'Event: {e["type"]}')
    return events

print('=== Building trajectory via start node explores ===')
# Do 7 explores at start to simulate some trajectory
for i in range(7):
    explore()
    time.sleep(0.1)

# Check trajectory
d = api('/api/state')
traj = d.get('trajectory', {})
print(f'After 7 explores: nodes_explored={traj.get("nodes_explored")}, research_investment={traj.get("research_investment")}')

print('\n=== Moving to PATH node sp_0002 ===')
ok = move('sp_0002')
print('Move ok:', ok)

# More explores at PATH
for i in range(5):
    explore()
    time.sleep(0.1)

d = api('/api/state')
traj = d.get('trajectory', {})
print(f'At PATH: nodes_explored={traj.get("nodes_explored")}, ri={traj.get("research_investment")}, ed={traj.get("exploration_depth")}')

print('\n=== Moving to WILD_ZONE br_0012 ===')
ok = move('br_0012')
print('Move ok:', ok)

if ok:
    print('\n=== Exploring WILD_ZONE (fragment should fire if conditions met) ===')
    for i in range(3):
        print(f'Explore {i+1}:')
        explore()
        time.sleep(0.1)

    d = api('/api/state')
    traj = d.get('trajectory', {})
    frags = d.get('discovered_fragments', [])
    print(f'\nFinal: frags_found={frags}, nodes_explored={traj.get("nodes_explored")}, ri={traj.get("research_investment")}')
