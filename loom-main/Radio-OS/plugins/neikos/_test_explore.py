import urllib.request, json

API = 'http://127.0.0.1:7700'

# Explore multiple times and look for fragment events
for i in range(10):
    req = urllib.request.Request(f'{API}/api/explore', data=b'{}', 
                                  headers={'Content-Type': 'application/json'}, method='POST')
    r = urllib.request.urlopen(req)
    d = json.load(r)
    evs = d.get('events', [])
    for ev in evs:
        if ev.get('type') not in ('explored',):
            print(f"Run {i+1}: {ev['type']}: {json.dumps(ev.get('data', {}))[:120]}")
    frag = [ev for ev in evs if 'fragment' in ev.get('type', '')]
    if frag:
        print(f"Run {i+1}: FRAGMENT! {frag}")
    else:
        print(f"Run {i+1}: explored only (tick={d['tick']})")
