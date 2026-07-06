"""Force a win scenario - use weakest trainer."""
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

# rest team first
state0 = api_get("/api/state")
print(f"Current node: {state0['player_location']}")

# pick weakest trainer
trainers_data = api_get("/api/trainers")
trainers = trainers_data["trainers"]
weakest = min(trainers, key=lambda t: t["rating"])
print(f"Weakest trainer: {weakest['trainer_id']} name={weakest['name']} rating={weakest['rating']:.0f}")

traj0 = state0["trajectory"]
print(f"Before: battles_won={traj0['battles_won']} battles_lost={traj0['battles_lost']}")
print(f"League: player_wins={trainers_data['player_wins']} player_losses={trainers_data['player_losses']}")

result = api_post("/api/battle", {"trainer_id": weakest["trainer_id"]})
winner = result.get("winner")
print(f"Result: winner={winner} turns={result.get('turns')}")
if result.get("error"):
    print(f"ERROR: {result['error']}")
    sys.exit(1)

state1 = api_get("/api/state")
traj1 = state1["trajectory"]
trainers1 = api_get("/api/trainers")
print(f"After: battles_won={traj1['battles_won']} battles_lost={traj1['battles_lost']}")
print(f"League: player_wins={trainers1['player_wins']} player_losses={trainers1['player_losses']}")

if winner == "player" and traj1["battles_won"] > traj0["battles_won"]:
    print("PASS: battles_won incremented on player win")
elif winner == "player":
    print(f"BUG: player won but battles_won unchanged ({traj0['battles_won']} -> {traj1['battles_won']})")
elif winner == "opponent" and traj1["battles_lost"] > traj0["battles_lost"]:
    print("(No win to test yet - still losing vs weakest)")
