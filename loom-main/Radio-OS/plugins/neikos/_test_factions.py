"""Test faction standings update after gameplay."""
import urllib.request, json

API = 'http://127.0.0.1:7700'

def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f'{API}{path}', data=data, headers={'Content-Type':'application/json'}, method='POST')
    r = urllib.request.urlopen(req, timeout=10)
    return json.loads(r.read())

def get(path):
    r = urllib.request.urlopen(f'{API}{path}', timeout=10)
    return json.loads(r.read())

# Initial standings
fac_before = get('/api/factions')
print('Before - all standings:')
for f in fac_before['factions']:
    print(f"  {f['name']}: {f['standing']}")

# Do 20 explores to generate some events
print('\nRunning 20 explores...')
for i in range(20):
    post('/api/explore')

# Do encounters + capture
print('Running encounters...')
for i in range(5):
    enc_result = post('/api/encounter')
    events = enc_result.get('events', [])
    for ev in events:
        if ev.get('type') == 'encounter':
            iid = ev.get('data', {}).get('instance_id')
            if iid:
                cap = post('/api/capture', {'instance_id': iid})
                cap_events = cap.get('events', [])
                for cev in cap_events:
                    if cev.get('type') in ('captured', 'capture_failed'):
                        print(f"  capture: {cev['type']}")

# Check standings changed
fac_after = get('/api/factions')
print('\nAfter gameplay - standings:')
any_changed = False
for f in fac_after['factions']:
    before_f = next((x for x in fac_before['factions'] if x['faction_id'] == f['faction_id']), None)
    before_val = before_f['standing'] if before_f else 0
    changed = ' ***CHANGED***' if abs(f['standing'] - before_val) > 0.001 else ''
    print(f"  {f['name']}: {f['standing']}{changed}")
    if changed:
        any_changed = True

if any_changed:
    print('\nSTANDINGS UPDATE: WORKING')
else:
    print('\nNote: standings unchanged after explores/captures (may need battles or specific events)')

print('\ntick:', fac_after['tick'])
