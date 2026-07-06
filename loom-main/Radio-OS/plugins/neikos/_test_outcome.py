"""Check outcome and faction_standings in state."""
import urllib.request, json

API = 'http://127.0.0.1:7700'

def get(path):
    r = urllib.request.urlopen(f'{API}{path}')
    return json.loads(r.read())

outcome = get('/api/outcome')
print('OUTCOME:')
print(json.dumps(outcome, indent=2))

state = get('/api/state')
print('\nFACTION STANDINGS in state:')
# look for faction_standings
for k, v in state.items():
    if 'faction' in k.lower():
        print(' ', k, ':', v)
