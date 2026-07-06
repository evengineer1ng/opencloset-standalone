import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
src = open('plugins/neikos/__init__.py', encoding='utf-8').read()
lines = src.split('\n')
for i, line in enumerate(lines):
    stripped = line.strip()
    if 'api/dialogue' in stripped or ('process_dialogue' in stripped and 'def' in stripped):
        print(f'L{i+1}: {stripped[:120]}')
