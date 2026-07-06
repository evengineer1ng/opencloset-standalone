"""Test all endpoints."""
import urllib.request, json

API = 'http://127.0.0.1:7700'

def get(path):
    try:
        r = urllib.request.urlopen(f'{API}{path}')
        return json.loads(r.read()), None
    except Exception as e:
        return None, str(e)

endpoints = ['/api/outcome', '/api/factions', '/api/species', '/api/pucks', '/api/islands', '/api/trainers']
for ep in endpoints:
    d, err = get(ep)
    if err:
        print('FAIL ' + ep + ': ' + err[:80])
    else:
        if isinstance(d, dict):
            ks = list(d.keys())
        else:
            ks = 'array[' + str(len(d)) + ']'
        print('OK   ' + ep + ': ' + str(ks)[:80])
