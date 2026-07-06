import sys
sys.stdout.reconfigure(encoding='utf-8')
with open('plugins/neikos/__init__.py', 'r', encoding='utf-8') as f:
    content = f.read()
lines = content.split('\n')

# Find the actual /api/fragments route handler
for i, line in enumerate(lines):
    stripped = line.strip()
    if 'fragments' in stripped and '@app' in stripped:
        for j in range(max(0,i-1), min(len(lines), i+40)):
            safe = lines[j].encode('ascii','replace').decode()
            print(f'{j+1}: {safe}')
        print()
