import sqlite3
import json
import os

def query_db(db_path):
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row['name'] for row in cursor.fetchall()]
    
    result = {"tables": tables, "rows": {}}
    
    for table in tables:
        cursor.execute(f"SELECT * FROM [{table}]")
        rows = cursor.fetchall()
        if rows:
            result["rows"][table] = [dict(row) for row in rows]
            result["count"] = len(rows)
    
    conn.close()
    return result

local_db = r"D:\openclaw\opencloset\user_data\trades\tradesv30.local.db"
remote_db = r"D:\openclaw\opencloset\user_data\trades\tradesv30.remote.db"

local_data = query_db(local_db)
remote_data = query_db(remote_db)

output = {
    "local": local_data,
    "remote": remote_data
}

with open(r"D:\openclaw\opencloset\trade_data.json", "w") as f:
    json.dump(output, f, indent=2, default=str)

print("Done. Local tables:", local_data["tables"] if local_data else "N/A")
print("Remote tables:", remote_data["tables"] if remote_data else "N/A")
if local_data:
    for t, rows in local_data["rows"].items():
        print(f"  Local [{t}]: {len(rows)} rows")
if remote_data:
    for t, rows in remote_data["rows"].items():
        print(f"  Remote [{t}]: {len(rows)} rows")
