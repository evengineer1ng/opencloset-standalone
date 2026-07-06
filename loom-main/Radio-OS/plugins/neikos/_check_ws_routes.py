"""Check all registered routes on the FastAPI app."""
import urllib.request, json

# Hit a non-existent route to see if we get 404 vs 403
import urllib.error
for path in ['/ws/puck', '/ws/nonexistent', '/api/nonexistent']:
    try:
        req = urllib.request.Request(
            f'http://127.0.0.1:7700{path}',
            headers={'Upgrade': 'websocket', 'Connection': 'Upgrade',
                     'Sec-WebSocket-Key': 'dGhlIHNhbXBsZSBub25jZQ==',
                     'Sec-WebSocket-Version': '13'},
            method='GET'
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            print(f'{path}: {r.status}')
    except urllib.error.HTTPError as e:
        print(f'{path}: HTTP {e.code}')
    except Exception as e:
        print(f'{path}: {type(e).__name__}: {e}')
