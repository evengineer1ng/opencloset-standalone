import sys, json
sys.stdout.reconfigure(encoding='utf-8')
import urllib.request

BASE = 'http://127.0.0.1:7700'

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.loads(r.read())

def post(path, body):
    import json as _j
    data = _j.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=5) as r:
        return _j.loads(r.read())

# Check /api/fragments raw response
frags = get('/api/fragments')
print('=== /api/fragments ===')
print(json.dumps(frags, indent=2)[:1000])

print()
print('=== /api/state recent_events ===')
s = get('/api/state')
evs = s.get('recent_events', [])
for ev in evs[-10:]:
    t = ev.get('type', '?')
    d = ev.get('data', {})
    print(f'  [{ev.get("tick","?")}] {t}: {str(d)[:100]}')

print()
print('=== POST /api/battle result (full) ===')
# Need a creature first
import time
m = get('/api/map')
if m.get('neighbors'):
    post('/api/move', {'node_id': m['neighbors'][0]})
    time.sleep(0.2)
post('/api/encounter', {})
time.sleep(0.2)
post('/api/capture', {'instance_id': 'wild_000001'})
time.sleep(0.2)
trainers = get('/api/trainers')
if trainers.get('trainers'):
    tid = trainers['trainers'][0]['trainer_id']
    r = post('/api/battle', {'trainer_id': tid})
    print(json.dumps(r, indent=2)[:2000])
