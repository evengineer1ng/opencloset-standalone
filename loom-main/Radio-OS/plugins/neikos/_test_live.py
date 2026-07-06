"""Quick live test script for anomaly_exposure and explore flow."""
import urllib.request
import json

API = 'http://127.0.0.1:7700'

def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f'{API}{path}', data=data, headers={'Content-Type':'application/json'}, method='POST')
    r = urllib.request.urlopen(req)
    return json.loads(r.read())

def get(path):
    r = urllib.request.urlopen(f'{API}{path}')
    return json.loads(r.read())

# Check current state
state = get('/api/state')
print('Current node:', state['player_location'])
print('Trajectory anomaly_exposure:', state['trajectory']['anomaly_exposure'])
print('Trajectory anomaly_events:', state['trajectory']['anomaly_events'])
print('Trajectory exploration_depth:', state['trajectory']['exploration_depth'])

# Do 20 explores and check for anomaly events
anomaly_count = 0
for i in range(20):
    result = post('/api/explore')
    events = result.get('events', [])
    for ev in events:
        if ev.get('type') == 'anomaly_event':
            anomaly_count += 1
            print(f'  Anomaly event at explore {i+1}!')
        if ev.get('type') == 'fragment_discovered':
            title = ev.get('data', {}).get('title', '?')
            print(f'  Fragment discovered at explore {i+1}: {title}')

state2 = get('/api/state')
print(f'\nAfter 20 explores:')
print('anomaly_exposure:', state2['trajectory']['anomaly_exposure'])
print('anomaly_events:', state2['trajectory']['anomaly_events'])
print('anomaly events fired:', anomaly_count)

# Check neighbors and try to move to a WILD_ZONE
map_data = get('/api/map')
print('\nNeighbors:', map_data.get('neighbors'))

# Try to move to each neighbor and check biome instability
for nbr in map_data.get('neighbors', []):
    move_result = post('/api/move', {'node_id': nbr})
    if move_result.get('ok'):
        local = get('/api/local')
        biome = local.get('biome', {})
        print(f'  {nbr} ({local.get("node_type")}): instability={biome.get("instability_bias", "?")}')
        # Move back
        post('/api/move', {'node_id': state['player_location']})
        break
