import urllib.request, json
data = json.dumps({"raw_content": "test tick", "source_mode": "typed", "project": "TradeObservation"}).encode()
req = urllib.request.Request("http://127.0.0.1:8000/api/captures", data=data, headers={"Content-Type": "application/json"}, method="POST")
resp = urllib.request.urlopen(req)
print(resp.status, json.loads(resp.read()))