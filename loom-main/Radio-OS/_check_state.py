import sys, json
sys.stdout.reconfigure(encoding='utf-8')
import urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:7700/api/state', timeout=3) as r:
        d = json.loads(r.read())
    print('TICK:', d.get('tick'))
    print('ISLAND:', d.get('island_name'))
    print('PLAYER_LOC:', d.get('player_location'))
    print('TEAM_SIZE:', d.get('player_team_size'))
    print('TEAM:', json.dumps(d.get('player_team', []), indent=2))
    print('RATING:', d.get('player_rating'))
    print('WINS:', d.get('player_wins'))
    print('LOSSES:', d.get('player_losses'))
    traj = d.get('trajectory', {})
    print('TRAJECTORY:')
    for k,v in traj.items():
        print(f'  {k}: {v}')
    print()
    evs = d.get('recent_events', [])
    print(f'RECENT_EVENTS ({len(evs)} total, last 5):')
    for ev in evs[-5:]:
        t = ev.get('type', '?')
        dd = str(ev.get('data', {}))[:80]
        print(f'  {t}: {dd}')
except Exception as e:
    print('ERROR:', e)
