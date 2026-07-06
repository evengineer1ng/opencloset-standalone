import re, pathlib, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
src = pathlib.Path('plugins/neikos/__init__.py').read_text(encoding='utf-8', errors='replace')

lines = src.split('\n')
for i, line in enumerate(lines):
    if 'evolv' in line.lower() and any(k in line for k in ['def ', 'push_ui', 'evolved', 'evo_level', 'check_evo']):
        print(f'{i}: {line[:120]}')
