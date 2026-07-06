import sqlite3
import json
import os

db_path = r"D:\openclaw\opencloset\ft_userdata\user_data\tradesv3.sqlite"
backup_path = r"D:\openclaw\opencloset\ft_userdata\user_data\tradesv3_old_backup.sqlite"

results = {}

for label, path in [("main", db_path), ("backup", backup_path)]:
    if not os.path.exists(path):
        results[label] = {"error": "file not found"}
        continue
    try:
        conn = sqlite3.connect(path)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in c.fetchall()]
        results[label] = {"tables": tables}
        
        for t in tables:
            c.execute(f"PRAGMA table_info({t})")
            cols = [r[1] for r in c.fetchall()]
            c.execute(f"SELECT COUNT(*) FROM {t}")
            count = c.fetchone()[0]
            results[label][t] = {"columns": cols, "row_count": count}
            
            if count > 0:
                c.execute(f"SELECT * FROM {t} LIMIT 5")
                results[label][t]["sample_rows"] = [list(r) for r in c.fetchall()]
        conn.close()
    except Exception as e:
        results[label]["error"] = str(e)

print(json.dumps(results, indent=2, default=str))