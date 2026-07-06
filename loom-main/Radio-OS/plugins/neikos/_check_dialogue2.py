import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
src = open('plugins/neikos/__init__.py', encoding='utf-8').read()
lines = src.split('\n')
# Find DialogueDelta
for i, line in enumerate(lines):
    if 'DialogueDelta' in line or 'dialogue_ideology' in line:
        print(f'L{i+1}: {line.strip()[:120]}')
