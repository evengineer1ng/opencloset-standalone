import sqlite3
import json
import os

db_path = r"D:\openclaw\opencloset\ft_userdata\user_data\tradesv3.sqlite"

if not os.path.exists(db_path):
    # Try alternative paths
    for p in [
        r"D:\openclaw\opencloset\ft_userdata\tradesv3.sqlite",
        r"D:\openclaw\opencloset\user_data\tradesv3.sqlite",
        r"D:\openclaw\opencloset\aws_snapshot\tradesv3.sqlite",
    ]:
        if os.path.exists(p):
            db_path = p
            break

print(f"Using DB: {db_path}")

conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Get tables
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r['name'] for r in cur.fetchall()]
print("Tables:", tables)

# Query trades
cur.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 200")
rows = cur.fetchall()

trades = []
for r in rows:
    trade = dict(r)
    # Parse JSON fields
    for key in ['strategy', 'order_pair', 'open_rate', 'close_rate', 'profit_ratio', 'stake_amount']:
        if key in trade and isinstance(trade[key], str):
            try:
                trade[key] = json.loads(trade[key])
            except:
                pass
    trades.append(trade)

conn.close()

# Write output
with open(r"D:\openclaw\opencloset\trade_data.json", "w") as f:
    json.dump({"tables": tables, "trades": trades, "count": len(trades)}, f, indent=2, default=str)

print(f"Exported {len(trades)} trades to trade_data.json")
print("Sample trade keys:", list(trades[0].keys()) if trades else "NO TRADES")
if trades:
    print("First trade:", json.dumps(trades[0], indent=2, default=str)[:500])
