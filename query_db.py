import sqlite3
import json

def query_db(db_path):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        result = {"db": db_path, "tables": tables, "data": {}}
        
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
            count = cursor.fetchone()[0]
            cursor.execute(f"SELECT * FROM [{table}] LIMIT 5")
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            result["data"][table] = {"count": count, "columns": columns, "sample": rows}
        
        conn.close()
        return result
    except Exception as e:
        return {"db": db_path, "error": str(e)}

local = query_db(r"D:\openclaw\opencloset\user_data\trades\tradesv30.local.db")
remote = query_db(r"D:\openclaw\opencloset\user_data\trades\tradesv30.remote.db")

print(json.dumps({"local": local, "remote": remote}, indent=2, default=str))