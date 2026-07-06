import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
src = open('plugins/neikos/__init__.py', encoding='utf-8').read()
lines = src.split('\n')
for i, line in enumerate(lines):
    if 'standing_delta' in line or 'standing_shift' in line:
        # print context
        print(f'L{i+1}: {line.strip()[:120]}')
