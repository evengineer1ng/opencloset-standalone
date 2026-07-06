import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
src = open('plugins/neikos/__init__.py', encoding='utf-8').read()
lines = src.split('\n')
for i, line in enumerate(lines):
    if 'elif action ==' in line or ('if action ==' in line and 'cmd' in lines[i-5:i+1]):
        print(f'L{i+1}: {line.strip()[:80]}')
