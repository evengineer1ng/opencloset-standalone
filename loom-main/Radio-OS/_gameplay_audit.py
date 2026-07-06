import sys, json, time
sys.stdout.reconfigure(encoding='utf-8')
import urllib.request

BASE = 'http://127.0.0.1:7700'

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.loads(r.read())

def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())

# Full playthrough to find real gameplay issues
s = get('/api/state')
print(f'=== FULL GAMEPLAY AUDIT ===')
print(f'Start: tick={s["tick"]}, loc={s["player_location"]}')

# 1. Move to first neighbor
m = get('/api/map')
neighbors = m.get('neighbors', [])
print(f'Neighbors from start: {neighbors}')

# Move around a lot to build exploration_depth
loc = s['player_location']
for i in range(10):
    m = get('/api/map')
    nb = m.get('neighbors', [])
    # pick neighbor different from current
    target = next((n for n in nb if n != loc), nb[0] if nb else None)
    if target:
        r = post('/api/move', {'node_id': target})
        loc = r.get('player_location', loc)
        time.sleep(0.1)

s2 = get('/api/state')
print(f'After 10 moves: tick={s2["tick"]}, depth={s2["trajectory"]["exploration_depth"]:.1f}')

# Encounter and capture
post('/api/encounter', {})
time.sleep(0.2)
r = post('/api/capture', {'instance_id': 'wild_000001'})
print(f'Capture wild_000001: {r.get("result")}')
time.sleep(0.2)

# Explore a bunch
for _ in range(5):
    r = post('/api/explore', {})
    evtypes = [e['type'] for e in r.get('events', [])]
    if any(t not in ('explored', 'tick_update') for t in evtypes):
        print(f'Explore interesting events: {evtypes}')
    time.sleep(0.15)

s3 = get('/api/state')
print(f'After explores: research={s3["trajectory"]["research_investment"]:.1f}')

# Check what happens with level-up - make creature near level up
print()
print('=== LEVEL UP TEST ===')
# Check XP of current team
for c in s3.get('player_team', []):
    print(f'  {c["species_name"]} Lv{c["level"]}: xp={c["xp"]}, floor={c["xp_floor"]}, to_next={c["xp_to_next"]}')

# Battle a trainer
trainers = get('/api/trainers')
if trainers.get('trainers'):
    t0 = trainers['trainers'][0]
    print(f'Trainer: {t0["name"]} rating={t0["rating"]:.0f}')
    r = post('/api/battle', {'trainer_id': t0['trainer_id']})
    evtypes = [e['type'] for e in r.get('events', [])]
    print(f'Battle events: {evtypes}')
    # Check for level_up events
    for ev in r.get('events', []):
        if ev['type'] == 'level_up':
            print(f'  LEVEL UP: {ev["data"]}')
    print(f'Battle result: winner={r.get("winner")}, rating={r.get("player_rating")}')
    time.sleep(0.3)

s4 = get('/api/state')
print(f'After battle: rating={s4.get("player_rating")}, wins={s4.get("player_wins")}, losses={s4.get("player_losses")}')

# Attempt to navigate to a node type with higher interest
print()
print('=== NODE TYPE EXPLORATION ===')
m2 = get('/api/map')
nodes = m2.get('nodes', [])
node_types = {}
for n in nodes:
    nt = n.get('node_type', '?')
    node_types[nt] = node_types.get(nt, 0) + 1
print(f'Node types: {node_types}')
# Find a LANDMARK or FACILITY
landmarks = [n for n in nodes if n.get('node_type') in ('LANDMARK', 'FACILITY')]
print(f'Landmarks/Facilities: {len(landmarks)}')
if landmarks:
    print(f'  Example: {landmarks[0].get("node_id")} - {landmarks[0].get("name")}')

# Check trainers endpoint in detail
print()
print('=== TRAINERS ===')
tr = get('/api/trainers')
print(f'Trainers count: {len(tr.get("trainers", []))}')
if tr.get('trainers'):
    for t in tr['trainers'][:3]:
        print(f'  {t["name"]} - {t.get("tier", "?")} - rating {t["rating"]:.0f} - {t.get("profile",{}).get("aggression","?")} agg')
