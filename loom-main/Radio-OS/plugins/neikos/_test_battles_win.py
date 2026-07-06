"""Rest team + fight to get a win, verify battles_won tracks."""
import sys, json, urllib.request
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

state0 = api_get("/api/state")
print(f"Current node: {state0['player_location']} ({state0.get('node_name', '?')})")
team = state0["player_team"]
print("Team fatigue:")
for m in team[:3]:
    print(f"  {m['species_name']} Lv{m['level']} fatigue={m['fatigue']}")

# Move to start node (settlement) to rest
print("\nMoving toward s0_0001 (start/settlement)...")
move_r = api_post("/api/move", {"node_id": "sp_0003"})
print(f"  Move sp_0003: ok={move_r.get('ok')} node={move_r.get('node_name')}")
move_r = api_post("/api/move", {"node_id": "sp_0002"})
print(f"  Move sp_0002: ok={move_r.get('ok')} node={move_r.get('node_name')}")
move_r = api_post("/api/move", {"node_id": "s0_0001"})
print(f"  Move s0_0001: ok={move_r.get('ok')} node={move_r.get('node_name')}")

# Rest
rest_r = api_post("/api/rest", {})
print(f"Rest result: {rest_r.get('result')} detail={rest_r.get('detail')}")
state1 = api_get("/api/state")
team1 = state1["player_team"]
print("Team fatigue after rest:")
for m in team1[:3]:
    print(f"  {m['species_name']} Lv{m['level']} fatigue={m['fatigue']}")

# Rest again for full recovery
rest_r = api_post("/api/rest", {})
print(f"Rest 2: {rest_r.get('result')}")
state2 = api_get("/api/state")

# Move to battle
move_r = api_post("/api/move", {"node_id": "sp_0002"})
print(f"\nMoved to sp_0002: {move_r.get('node_name')}")

# Get weakest trainer
trainers_data = api_get("/api/trainers")
trainers = trainers_data["trainers"]
weakest = min(trainers, key=lambda t: t["rating"])
print(f"Target: {weakest['name']} (id={weakest['trainer_id']} rating={weakest['rating']:.0f})")

traj0 = state2["trajectory"]
print(f"Pre-battle: battles_won={traj0['battles_won']} battles_lost={traj0['battles_lost']}")

# Try battles until we win
for attempt in range(5):
    result = api_post("/api/battle", {"trainer_id": weakest["trainer_id"]})
    if result.get("error"):
        print(f"ERROR: {result['error']}")
        break
    winner = result.get("winner")
    turns = result.get("turns")
    state_x = api_get("/api/state")
    traj_x = state_x["trajectory"]
    trd_x = api_get("/api/trainers")
    print(f"Battle {attempt+1}: winner={winner} turns={turns} | trajectory.battles_won={traj_x['battles_won']} battles_lost={traj_x['battles_lost']} | league_wins={trd_x['player_wins']}")
    if winner == "player":
        if traj_x["battles_won"] > traj0["battles_won"]:
            print("PASS: battles_won correctly incremented!")
        else:
            print(f"BUG: player won (league++) but trajectory.battles_won stuck at {traj_x['battles_won']}")
        break
    # rest between battles
    api_post("/api/rest", {})
    traj0 = traj_x
