import urllib.request, json

API = 'http://127.0.0.1:7700'

r = urllib.request.urlopen(f'{API}/api/state')
state = json.load(r)
print("recent_events count:", len(state.get('recent_events', [])))
recent = state.get('recent_events', [])
for ev in recent[-5:]:
    print(f"  type={ev['type']}, tick={ev['tick']}")
