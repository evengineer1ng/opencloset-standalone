# Full Inventory Summary - Algotrading Lab
# Generated from artifacts copied from two AWS instances

## Instance 1 (ft_userdata)
Location: D:\openclaw\opencloset\ft_userdata\user_data

### Config
- config.json (active)
- config.json.save (backup)

### Strategies
- spaybot.py (active strategy in config)
- symphony.py
- symphonyCOMPLETE.py
- symphonyMOREC.py
- sample_strategy.py

### Trade Database
- tradesv3.sqlite (active)
- tradesv3_old_backup.sqlite (backup)
- trade_report.sql

### Hyperopts
- sample_hyperopt.py

### Logs
- freqtrade.log (current)
- freqtrade.log.1 through freqtrade.log.10 (rotated)

## Instance 2 (aws_snapshot/instance2_user_data)
Location: D:\openclaw\opencloset\aws_snapshot\instance2_user_data\user_data

### Config
- config.json (active)

### Strategies
- jorts.py
- wanda.py
- sample_strategy.py
- spaybot.py
- symphony.py
- symphonyCOMPLETE.py
- symphonyMOREC.py

### Trade Database
- tradesv3.sqlite (active)

### Short Positions
- Multiple short position files (short_*.json)

## Key Observations
- Instance 1 runs "spaybot" strategy
- Instance 2 runs "jorts" strategy
- Both instances share spaybot, symphony variants, and sample_strategy
- Instance 2 has additional strategies: jorts, wanda
- Instance 2 tracks short positions separately
- Both have tradesv3.sqlite databases with trade history

## Next Steps
- Query trade databases for performance metrics
- Compare strategy configurations between instances
- Identify which strategies are profitable vs. not
- Consolidate or document strategy differences
