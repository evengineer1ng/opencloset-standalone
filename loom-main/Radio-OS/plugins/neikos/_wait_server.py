import urllib.request, json, time, sys

print("Waiting for server...", flush=True)
for i in range(30):
    time.sleep(1)
    try:
        with urllib.request.urlopen("http://127.0.0.1:7700/api/state", timeout=2) as r:
            state = json.load(r)
            print(f"Server back at tick={state['tick']}", flush=True)
            sys.exit(0)
    except Exception as e:
        print(f"  [{i+1}s] still down: {type(e).__name__}", flush=True)

print("FAIL: server did not restart in 30s", flush=True)
sys.exit(1)
