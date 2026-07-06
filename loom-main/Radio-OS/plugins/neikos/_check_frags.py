import pathlib, sys, re
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
src = pathlib.Path('plugins/neikos/__init__.py').read_text(encoding='utf-8', errors='replace')
lines = src.split('\n')
pool_start = next(i for i, l in enumerate(lines) if 'FRAGMENT_POOL: List' in l)

active = {'M2', 'M3', 'M6', 'M7', 'M9', 'M10', 'M11', 'M12', 'M14', 'M15', 'M16', 'M18'}

for i, line in enumerate(lines[pool_start:pool_start+600], pool_start):
    if 'NarrativeFragment(' in line:
        block = ' '.join(lines[i:i+10])
        fid = re.search(r'"(F\d+)"', block)
        cond = re.search(r'"(M\d+)"\s*,\s*\{([^}]*)\}', block)
        if fid and cond:
            fid_s = fid.group(1)
            mtn_s = cond.group(1)
            cond_s = cond.group(2).strip()
            if mtn_s in active:
                print(f'{fid_s} {mtn_s}: {cond_s}')
