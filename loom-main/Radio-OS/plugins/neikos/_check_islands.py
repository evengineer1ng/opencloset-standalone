import urllib.request, json
r = urllib.request.urlopen('http://127.0.0.1:7700/api/islands')
d = json.load(r)
islands = d.get('islands') or d
if isinstance(islands, list):
    print(f"count={len(islands)}, cache_ready={d.get('cache_ready')}, cached_count={d.get('cached_count')}")
else:
    print(json.dumps(d)[:200])
