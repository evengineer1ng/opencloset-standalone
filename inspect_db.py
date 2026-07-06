import sqlite3
import sys

db_paths = [
    r"D:\openclaw\opencloset\ft_userdata\user_data\tradesv3.sqlite",
    r"D:\openclaw\opencloset\ft_userdata\user_data\tradesv3_old_backup.sqlite",
    r"D:\openclaw\opencloset\aws_snapshot\instance2_user_data\user_data\tradesv3.sqlite",
]

for db_path in db_paths:
    print(f"\n{'='*60}")
    print(f"DB: {db_path}")
    print("="*60)
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = c.fetchall()
        print(f"Tables: {[t[0] for t in tables]}")
        for t in tables:
            tname = t[0]
            c.execute(f"PRAGMA table_info({tname})")
            cols = c.fetchall()
            print(f"\n  {tname} columns:")
            for col in cols:
                print(f"    {col[1]} ({col[2]})")
            c.execute(f"SELECT COUNT(*) FROM {tname}")
            count = c.fetchone()[0]
            print(f"  Row count: {count}")
            if tname == "trades":
                c.execute("SELECT * FROM trades LIMIT 3")
                rows = c.fetchall()
                c.execute("PRAGMA table_info(trades)")
                colnames = [col[1] for col in c.fetchall()]
                print(f"\n  Sample trades (first 3):")
                for row in rows:
                    for cn, val in zip(colnames, row):
                        print(f"    {cn}: {val}")
                    print("    ---")
                c.execute("SELECT DISTINCT strategy FROM trades")
                strategies = c.fetchall()
                print(f"\n  Distinct strategies: {[s[0] for s in strategies]}")
                c.execute("SELECT strategy, COUNT(*) FROM trades GROUP BY strategy")
                strat_counts = c.fetchall()
                print(f"  Trades per strategy:")
                for s, cnt in strat_counts:
                    print(f"    {s}: {cnt}")
        conn.close()
    except Exception as e:
        print(f"ERROR: {e}")
