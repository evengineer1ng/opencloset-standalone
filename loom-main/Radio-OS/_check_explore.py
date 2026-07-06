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

# Fresh state, move, then explore multiple times
s = get('/api/state')
print(f'Start: tick={s["tick"]}')

m = get('/api/map')
if m.get('neighbors'):
    post('/api/move', {'node_id': m['neighbors'][0]})
    time.sleep(0.3)

# Explore response - check ALL fields
r = post('/api/explore', {})
print('EXPLORE RESPONSE KEYS:', list(r.keys()))
print('FULL RESPONSE:', json.dumps(r, indent=2)[:1500])

# Check what the /api/explore endpoint looks like in the code
# Also look at 'events_count' specifically
