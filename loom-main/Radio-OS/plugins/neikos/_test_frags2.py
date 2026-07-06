import urllib.request, json

API = 'http://127.0.0.1:7700'

r = urllib.request.urlopen(f'{API}/api/fragments')
d = json.load(r)

print(f"Total: {d['total']}, Discovered: {d['discovered']}")
print()

# Find fragments with low unlock conditions
for frag in d.get('fragments', []):
    print(f"{frag['fragment_id']} ({frag['type']}) M={frag['mountain_code']} disc={frag['discovered']}")
