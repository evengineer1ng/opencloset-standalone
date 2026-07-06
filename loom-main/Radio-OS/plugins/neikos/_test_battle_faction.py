"""Test faction standings via battles."""
import urllib.request, json

API = 'http://127.0.0.1:7700'

def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f'{API}{path}', data=data, headers={'Content-Type':'application/json'}, method='POST')
    r = urllib.request.urlopen(req, timeout=15)
    return json.loads(r.read())

def get(path):
    r = urllib.request.urlopen(f'{API}{path}', timeout=10)
    return json.loads(r.read())

# Check team
state = get('/api/state')
print('Team size:', state['player_team_size'])
print('Player location:', state['player_location'])

# Load trainers
trainers = get('/api/trainers')
print('Trainers:', len(trainers.get('trainers', [])))
if trainers.get('trainers'):
    tid = trainers['trainers'][0]['trainer_id']
    print('Fighting:', trainers['trainers'][0]['name'])

    # Faction standings before
    fac_before = get('/api/factions')
    before_vals = {f['faction_id']: f['standing'] for f in fac_before['factions']}

    # Battle
    result = post('/api/battle', {'trainer_id': tid})
    print('Battle result:', result.get('winner'), 'turns:', result.get('turns'))

    # Faction standings after
    fac_after = get('/api/factions')
    print('\nFaction standings after battle:')
    for f in fac_after['factions']:
        before = before_vals.get(f['faction_id'], 0.0)
        delta = f['standing'] - before
        delta_str = f" (delta: {delta:+.4f})" if abs(delta) > 0.001 else ""
        print(f"  {f['name']}: {f['standing']:.4f}{delta_str}")
else:
    print('No trainers available (need creatures on team)')
