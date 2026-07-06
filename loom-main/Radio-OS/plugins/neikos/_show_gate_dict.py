import sys, io, pathlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
src = pathlib.Path('plugins/neikos/__init__.py').read_text(encoding='utf-8')
lines = src.splitlines()
needle = "case 'memory_echo':"
for i, line in enumerate(lines, 1):
    if needle in line:
        print(f'line {i}: {line}')
        for j in range(max(0,i-3), min(len(lines), i+6)):
            print(f'  {j+1}: {lines[j]}')
        break
