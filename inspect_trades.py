import sqlite3
import sys

db_path = sys.argv[1] if len(sys.argv) > 1 else r'D:\openclaw\opencloset\ft_userdata\user_data\tradesv3.sqlite'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Get tables
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print(f"=== Tables in {db_path} ===")
for t in tables:
    print(f"  Table: {t[0]}")
    cursor.execute(f"PRAGMA table_info({t[0]})")
    cols = cursor.fetchall()
    for c in cols:
        print(f"    {c[1]} ({c[2]})")
    cursor.execute(f"SELECT COUNT(*) FROM {t[0]}")
    count = cursor.fetchone()[0]
    print(f"    Row count: {count}")
    # Sample first 3 rows
    cursor.execute(f"SELECT * FROM {t[0]} LIMIT 3")
    rows = cursor.fetchall()
    for r in rows:
        print(f"    Sample: {r}")
    print()

conn.close()
