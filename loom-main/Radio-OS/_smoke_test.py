import sys, json, time
sys.stdout.reconfigure(encoding='utf-8')
import urllib.request, urllib.parse

BASE = 'http://127.0.0.1:7700'

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.loads(r.read())

def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())

print('=== PLAYTHROUGH SMOKE TEST ===')
s = get('/api/state')
print(f'Start: tick={s["tick"]}, loc={s["player_location"]}, team={s["player_team_size"]}')

# Move to first neighbor
m = get('/api/map')
neighbors = m.get('neighbors', [])
print(f'Neighbors: {neighbors[:3]}')
if neighbors:
    r = post('/api/move', {'node_id': neighbors[0]})
    print(f'Move to {neighbors[0]}: ok={r.get("ok")}, loc={r.get("player_location")}')
    time.sleep(0.3)

# Try explore
r = post('/api/explore', {})
print(f'Explore: events={r.get("events_count", 0)}, types={[e.get("type") for e in r.get("events", [])]}')
time.sleep(0.3)

# Try encounter
r = post('/api/encounter', {})
enc = r.get('encounter')
print(f'Encounter: events={r.get("events_count",0)}, enc={enc}')
time.sleep(0.3)

if enc and enc.get('instance_id'):
    iid = enc['instance_id']
    r = post('/api/capture', {'instance_id': iid})
    print(f'Capture {iid}: result={r.get("result")}, team_size={r.get("team_size")}')
    time.sleep(0.3)

# Check state after actions
s = get('/api/state')
print(f'State: tick={s["tick"]}, rating={s.get("player_rating")}, wins={s.get("player_wins")}, losses={s.get("player_losses")}')
print(f'Team: {[f"{c["species_name"]} Lv{c["level"]} fat={c["fatigue"]}" for c in s.get("player_team", [])]}')
print(f'Traj: depth={s["trajectory"]["exploration_depth"]:.1f}, research={s["trajectory"]["research_investment"]:.1f}')

# Try battle if we have a creature
if s['player_team_size'] > 0:
    trainers = get('/api/trainers')
    if trainers.get('trainers'):
        tid = trainers['trainers'][0]['trainer_id']
        r = post('/api/battle', {'trainer_id': tid})
        events = r.get('events', [])
        br = next((e for e in events if e.get('type') == 'battle_result'), None)
        if br:
            d = br['data']
            print(f'Battle vs {d.get("opponent_name")}: winner={d.get("winner")}, turns={d.get("turns")}, rating={d.get("player_rating_after")}')
        else:
            print(f'Battle response: {r}')
        time.sleep(0.3)

# Check knower state
kn = get('/api/knower')
print(f'Knower: is_unlocked={kn.get("is_unlocked")}, thresholds={kn.get("unlock_thresholds")}')

# Check fragments
frags = get('/api/fragments')
print(f'Fragments: total={frags.get("total_fragments")}, discovered={frags.get("discovered_count")}')

# Check expedition
exp = get('/api/expedition/status')
print(f'Expedition: ngp_run={exp.get("ngp_run")}, profile={exp.get("saved_profile") is not None}')

# Check outcome
out = get('/api/outcome')
print(f'Outcome: role={out.get("narrative_role")}, archetype={out.get("personal_archetype")}')

print()
print('=== STATE FIELDS CHECK ===')
s2 = get('/api/state')
expected = ['seed','tick','island_name','player_location','containment_tier','trajectory',
            'player_team_size','player_team','recent_events','player_rating','player_wins','player_losses',
            'ledger','discovered_species']
for k in expected:
    present = k in s2
    val = s2.get(k)
    if isinstance(val, (list, dict)):
        val = f'({type(val).__name__}, len={len(val)})'
    print(f'  {k}: {"OK" if present else "MISSING"} = {repr(val)[:60] if present else "---"}')
