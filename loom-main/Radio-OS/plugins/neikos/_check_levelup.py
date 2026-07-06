import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
src = open('plugins/neikos/__init__.py', encoding='utf-8').read()
needle = "type === 'level_up'"
idx = src.find(needle)
if idx != -1:
    print(src[max(0, idx-300):idx+500])
else:
    print("NOT FOUND")
