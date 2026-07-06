"""Try to add debug output to the running server's WS route registration."""
import urllib.request, json

# Check if there's any exception logged in the server stderr
with open('plugins/neikos/_server.err', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

if 'WebSocket route setup failed' in content:
    print("WebSocket route setup FAILED:")
    idx = content.find('WebSocket route setup failed')
    print(content[idx:idx+500])
elif 'puck' in content.lower():
    print("Puck-related error:")
    for line in content.split('\n'):
        if 'puck' in line.lower():
            print(line)
else:
    print("No WebSocket errors in stderr. Last 20 lines:")
    lines = content.strip().split('\n')
    print('\n'.join(lines[-20:]))
