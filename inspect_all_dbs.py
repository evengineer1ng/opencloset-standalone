import sqlite3
import json

db_paths = [
    r"D:\openclaw\opencloset\ft_userdata\user_data\tradesv3.sqlite",
    r"D:\openclaw\opencloset\ft_userdata\user_data\tradesv3_old_backup.sqlite",
    r"D:\openclaw\opencloset\aws_snapshot\instance2_user_data\user_data\tradesv3.sqlite",
    r"D:\openclaw\opencloset\aws_snapshot\instance2_user_data\user_data\wandasolotradesv3.sqlite",
]

results = {}

for db_path in db_paths:
    label = db_path.split("\\\\")[-1]
    try:
        conn = sqlite3.connect(db_path)
        c = conn.cursor()
        
        # Tables
        c.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in c.fetchall()]
        
        # Trade count
        trade_count = 0
        if 'trades' in tables:
            c.execute("SELECT COUNT(*) FROM trades")
            trade_count = c.fetchone()[0]
            
            # Strategy breakdown
            c.execute("SELECT strategy, COUNT(*) as cnt FROM trades GROUP BY strategy ORDER BY cnt DESC")
            strategies = {r[0]: r[1] for r in c.fetchall()}
            
            # Date range
            c.execute("SELECT MIN(open_date), MAX(close_date) FROM trades WHERE close_date IS NOT NULL")
            row = c.fetchone()
            date_range = (row[0], row[1])
            
            # Total profit
            c.execute("SELECT SUM(close_profit_abs) FROM trades WHERE close_date IS NOT NULL")
            total_profit = c.fetchone()[0]
            
            # Win/Loss
            c.execute("SELECT COUNT(*) FROM trades WHERE close_date IS NOT NULL AND close_profit > 0")
            wins = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM trades WHERE close_date IS NOT NULL AND close_profit <= 0")
            losses = c.fetchone()[0]
            
            # Open trades
            c.execute("SELECT COUNT(*) FROM trades WHERE is_open = 1")
            open_trades = c.fetchone()[0]
            
            # Exit reasons
            c.execute("SELECT exit_reason, COUNT(*) FROM trades WHERE close_date IS NOT NULL GROUP BY exit_reason ORDER BY COUNT(*) DESC")
            exit_reasons = {r[0]: r[1] for r in c.fetchall()}
            
            # Top pairs
            c.execute("SELECT pair, COUNT(*) as cnt, SUM(close_profit_abs) as total FROM trades WHERE close_date IS NOT NULL GROUP BY pair ORDER BY total DESC LIMIT 10")
            top_pairs = [{"pair": r[0], "trades": r[1], "total_profit": r[2]} for r in c.fetchall()]
        else:
            strategies = {}
            date_range = (None, None)
            total_profit = 0
            wins = 0
            losses = 0
            open_trades = 0
            exit_reasons = {}
            top_pairs = []
        
        # Order count
        order_count = 0
        if 'orders' in tables:
            c.execute("SELECT COUNT(*) FROM orders")
            order_count = c.fetchone()[0]
        
        conn.close()
        
        results[label] = {
            "tables": tables,
            "trade_count": trade_count,
            "order_count": order_count,
            "strategies": strategies,
            "date_range": date_range,
            "total_profit": total_profit,
            "wins": wins,
            "losses": losses,
            "open_trades": open_trades,
            "exit_reasons": exit_reasons,
            "top_pairs": top_pairs,
        }
    except Exception as e:
        results[label] = {"error": str(e)}

print(json.dumps(results, indent=2, default=str))