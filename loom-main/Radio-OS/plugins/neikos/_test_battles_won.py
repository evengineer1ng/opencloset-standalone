"""Test battles_won trajectory tracking end-to-end."""
import sys, json, urllib.request, urllib.error
sys.path.insert(0, '.')

BASE = "http://127.0.0.1:7700"

def api_get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return json.load(r)

def api_post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req) as r:
        return json.load(r)

# --- Pre-battle state ---
state0 = api_get("/api/state")
traj0 = state0["trajectory"]
trainers_data = api_get("/api/trainers")
print(f"Before battle:")
print(f"  battles_won={traj0['battles_won']} battles_lost={traj0['battles_lost']}")
print(f"  player_wins={trainers_data['player_wins']} player_losses={trainers_data['player_losses']}")
print(f"  tick={state0['tick']}")

# Check team
team = state0.get("player_team", [])
print(f"  team_size={len(team)}")
for m in team:
    print(f"    {m['species_name']} Lv{m['level']} fatigue={m['fatigue']}")

if not team:
    print("ERROR: no team — cannot battle")
    sys.exit(1)

# Get trainers and pick one
trainers = trainers_data["trainers"]
# pick one near player rating
player_rating = trainers_data["player_rating"]
nearest = min(trainers, key=lambda t: abs(t["rating"] - player_rating))
print(f"\nBattling: {nearest['trainer_id']} (rating={nearest['rating']:.0f})")

result = api_post("/api/battle", {"trainer_id": nearest["trainer_id"]})
print(f"Battle result: winner={result.get('winner')} turns={result.get('turns')} ok={result.get('ok')}")
if result.get("error"):
    print(f"ERROR: {result['error']}")
    sys.exit(1)

# --- Post-battle state ---
state1 = api_get("/api/state")
traj1 = state1["trajectory"]
trainers1 = api_get("/api/trainers")
print(f"\nAfter battle:")
print(f"  battles_won={traj1['battles_won']} battles_lost={traj1['battles_lost']}")
print(f"  player_wins={trainers1['player_wins']} player_losses={trainers1['player_losses']}")

winner = result.get("winner")
if winner == "player":
    expected_wins = traj0["battles_won"] + 1
    expected_losses = traj0["battles_lost"]
    league_expected_wins = trainers_data["player_wins"] + 1
else:
    expected_wins = traj0["battles_won"]
    expected_losses = traj0["battles_lost"] + 1
    league_expected_wins = trainers_data["player_wins"]

print(f"\nExpected trajectory.battles_won={expected_wins} got={traj1['battles_won']}")
print(f"Expected trajectory.battles_lost={expected_losses} got={traj1['battles_lost']}")
print(f"Expected league player_wins={league_expected_wins} got={trainers1['player_wins']}")

ok = True
if traj1["battles_won"] != expected_wins:
    print(f"BUG: battles_won mismatch! expected={expected_wins} got={traj1['battles_won']}")
    ok = False
if traj1["battles_lost"] != expected_losses:
    print(f"BUG: battles_lost mismatch! expected={expected_losses} got={traj1['battles_lost']}")
    ok = False
if trainers1["player_wins"] != league_expected_wins:
    print(f"BUG: league player_wins mismatch! expected={league_expected_wins} got={trainers1['player_wins']}")
    ok = False

if ok:
    print("\nPASS: trajectory and league both updated correctly")
else:
    print("\nFAIL: mismatch found")
