import urllib.request, json

API = 'http://127.0.0.1:7700'

# Check fragments endpoint
try:
    r = urllib.request.urlopen(f'{API}/api/fragments')
    d = json.load(r)
    print(json.dumps(d)[:500])
except Exception as e:
    print(f"No /api/fragments: {e}")

# Check state for fragment info
r = urllib.request.urlopen(f'{API}/api/state')
state = json.load(r)
print(f"\ndiscovered_species: {state.get('discovered_species', 'N/A')}")
print(f"discovered_fragments: {state.get('discovered_fragments', 'N/A')}")
print(f"island_fragments count: {state.get('island_fragments_count', 'N/A')}")
