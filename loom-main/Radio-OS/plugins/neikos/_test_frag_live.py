import urllib.request, json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

for i in range(20):
    data = b'{}'
    req = urllib.request.Request(
        'http://127.0.0.1:7700/api/explore',
        data=data,
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    r = urllib.request.urlopen(req, timeout=10)
    d = json.loads(r.read())
    events = d.get('events', [])
    for e in events:
        if e['type'] == 'fragment_discovered':
            print('FRAGMENT:', json.dumps(e['data'], indent=2)[:600])
        elif e['type'] not in ('explored', 'tick_update', 'anomaly_event'):
            print('Event:', e['type'], '-', json.dumps(e.get('data', {}))[:120])
