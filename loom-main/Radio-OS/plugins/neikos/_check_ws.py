import sys
with open('plugins/neikos/__init__.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find all lines near the websocket route that could indicate a 403 source
in_puck_ws = False
for i, line in enumerate(lines):
    if 'puck_ws' in line or '/ws/puck' in line:
        in_puck_ws = True
    if in_puck_ws:
        print(f'{i+1}: {line.rstrip()}')
        if i > 10165 and (not line.strip() or line.strip() == ''):
            if i > 10200:
                break
