import urllib.request, json

API = 'http://127.0.0.1:7700'

# Do a fresh explore and look at the raw event data
req = urllib.request.Request(f'{API}/api/explore', data=b'{}',
                              headers={'Content-Type': 'application/json'}, method='POST')
r = urllib.request.urlopen(req)
d = json.load(r)
print("Full response:")
print(json.dumps(d, indent=2))
