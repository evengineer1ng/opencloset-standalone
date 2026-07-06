import json
import os
import re
import sqlite3
import tempfile
import webbrowser
from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path


DB_PATHS = [
    Path(r"D:\openclaw\opencloset\aws_snapshot\instance2_user_data\user_data\wandasolotradesv3.sqlite"),
    Path(r"D:\openclaw\opencloset\aws_snapshot\instance2_user_data\user_data\tradesv3.sqlite"),
    Path(r"D:\openclaw\opencloset\ft_userdata\user_data\tradesv3_old_backup.sqlite"),
    Path(r"D:\openclaw\opencloset\ft_userdata\user_data\tradesv3.sqlite"),
]

STRATEGY_FOLDERS = [
    Path(r"D:\openclaw\opencloset\ft_userdata\user_data\strategies"),
    Path(r"D:\openclaw\opencloset\aws_snapshot\instance2_user_data\user_data\strategies"),
]

REPORT_JSON_PATH = Path(r"D:\openclaw\opencloset\trade_report.json")
REPORT_HTML_PATH = Path(r"D:\openclaw\opencloset\trade_report.html")


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def read_trades():
    all_trades = []
    db_summaries = []

    for db_path in DB_PATHS:
        if not db_path.exists():
            db_summaries.append(
                {
                    "db_path": str(db_path),
                    "db_name": db_path.name,
                    "exists": False,
                    "total_trades": 0,
                    "closed_trades": 0,
                    "open_trades": 0,
                    "strategies": [],
                }
            )
            continue

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, exchange, pair, base_currency, stake_currency, is_open,
                   fee_open, fee_open_cost, fee_open_currency,
                   fee_close, fee_close_cost, fee_close_currency,
                   open_rate, open_rate_requested, open_trade_value,
                   close_rate, close_rate_requested, realized_profit,
                   close_profit, close_profit_abs, stake_amount,
                   max_stake_amount, amount, amount_requested,
                   open_date, close_date, stop_loss, stop_loss_pct,
                   initial_stop_loss, initial_stop_loss_pct,
                   is_stop_loss_trailing, max_rate, min_rate,
                   exit_reason, exit_order_status, strategy, enter_tag,
                   timeframe, trading_mode, amount_precision, price_precision,
                   precision_mode, precision_mode_price, contract_size,
                   leverage, is_short, liquidation_price
            FROM trades
            """
        )
        rows = cursor.fetchall()

        cursor.execute(
            """
            SELECT COALESCE(strategy, '<null>') AS strategy_name, COUNT(*) AS trade_count
            FROM trades
            GROUP BY strategy_name
            ORDER BY trade_count DESC, strategy_name
            """
        )
        strategy_rows = cursor.fetchall()

        closed_count = 0
        for row in rows:
            trade = dict(row)
            trade["_db_source"] = db_path.name
            trade["_db_path"] = str(db_path)
            trade["exit_reason"] = trade.get("exit_reason") or "unknown"
            trade["strategy"] = trade.get("strategy") or "unknown"
            trade["pair"] = trade.get("pair") or "UNKNOWN"
            trade["enter_tag"] = trade.get("enter_tag") or "unknown"
            trade["is_short"] = bool(trade.get("is_short", 0))
            trade["close_profit"] = safe_float(trade.get("close_profit"))
            trade["close_profit_abs"] = safe_float(trade.get("close_profit_abs"))
            trade["stake_amount"] = safe_float(trade.get("stake_amount"))
            trade["profit_pct"] = round(trade["close_profit"] * 100.0, 4)

            open_dt = parse_dt(trade.get("open_date"))
            close_dt = parse_dt(trade.get("close_date"))
            if open_dt and close_dt:
                trade["duration_minutes"] = round((close_dt - open_dt).total_seconds() / 60.0, 1)
            else:
                trade["duration_minutes"] = 0.0

            if not trade.get("is_open"):
                closed_count += 1
                all_trades.append(trade)

        db_summaries.append(
            {
                "db_path": str(db_path),
                "db_name": db_path.name,
                "exists": True,
                "total_trades": len(rows),
                "closed_trades": closed_count,
                "open_trades": len(rows) - closed_count,
                "strategies": [
                    {"strategy": item["strategy_name"], "trade_count": item["trade_count"]}
                    for item in strategy_rows
                ],
            }
        )
        conn.close()

    return all_trades, db_summaries


def scan_strategy_metadata():
    by_strategy_name = {}
    all_entries = []

    for folder in STRATEGY_FOLDERS:
        if not folder.exists():
            continue
        for strategy_file in sorted(folder.glob("*.py")):
            try:
                content = strategy_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            class_match = re.search(r"^class\s+(\w+)\s*\(", content, re.MULTILINE)
            class_name = class_match.group(1) if class_match else strategy_file.stem
            docstring_match = re.search(r'"""(.*?)"""', content, re.DOTALL)
            timeframe_match = re.search(
                r"^\s*timeframe\s*=\s*['\"]([^'\"]+)['\"]",
                content,
                re.MULTILINE,
            )
            can_short_match = re.search(
                r"^\s*can_short\s*=\s*(True|False)",
                content,
                re.MULTILINE,
            )

            entry = {
                "file_stem": strategy_file.stem,
                "file_name": strategy_file.name,
                "class_name": class_name,
                "description": docstring_match.group(1).strip()[:300] if docstring_match else "",
                "timeframe": timeframe_match.group(1) if timeframe_match else "",
                "can_short": can_short_match.group(1) == "True" if can_short_match else None,
                "path": str(strategy_file),
                "folder": str(folder),
            }
            all_entries.append(entry)
            by_strategy_name[strategy_file.stem] = entry
            by_strategy_name[class_name] = entry

    return by_strategy_name, all_entries


def compute_strategy_stats(trades, strategy_lookup):
    strategy_stats = {}

    for trade in trades:
        strategy_name = trade["strategy"]
        stats = strategy_stats.setdefault(
            strategy_name,
            {
                "strategy": strategy_name,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "break_even": 0,
                "total_profit_pct": 0.0,
                "total_profit_abs": 0.0,
                "avg_profit_pct": 0.0,
                "avg_duration_min": 0.0,
                "long_trades": 0,
                "short_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "gross_profit_pct": 0.0,
                "gross_loss_pct": 0.0,
                "pairs": set(),
                "db_sources": set(),
            },
        )
        stats["total_trades"] += 1
        stats["total_profit_pct"] += trade["profit_pct"]
        stats["total_profit_abs"] += trade["close_profit_abs"]
        stats["avg_duration_min"] += trade["duration_minutes"]
        stats["pairs"].add(trade["pair"])
        stats["db_sources"].add(trade["_db_source"])

        if trade["profit_pct"] > 0:
            stats["wins"] += 1
            stats["gross_profit_pct"] += trade["profit_pct"]
        elif trade["profit_pct"] < 0:
            stats["losses"] += 1
            stats["gross_loss_pct"] += abs(trade["profit_pct"])
        else:
            stats["break_even"] += 1

        if trade["is_short"]:
            stats["short_trades"] += 1
        else:
            stats["long_trades"] += 1

    rows = []
    for strategy_name, stats in strategy_stats.items():
        total_trades = stats["total_trades"]
        stats["avg_profit_pct"] = round(stats["total_profit_pct"] / total_trades, 4) if total_trades else 0.0
        stats["avg_duration_min"] = round(stats["avg_duration_min"] / total_trades, 1) if total_trades else 0.0
        stats["win_rate"] = round((stats["wins"] / total_trades) * 100.0, 2) if total_trades else 0.0
        stats["profit_factor"] = (
            round(stats["gross_profit_pct"] / stats["gross_loss_pct"], 2)
            if stats["gross_loss_pct"] > 0
            else (999.0 if stats["gross_profit_pct"] > 0 else 0.0)
        )
        stats["unique_pairs"] = len(stats["pairs"])
        stats["pairs"] = sorted(stats["pairs"])
        stats["db_sources"] = sorted(stats["db_sources"])

        metadata = strategy_lookup.get(strategy_name)
        stats["strategy_file"] = metadata["file_name"] if metadata else None
        stats["strategy_class"] = metadata["class_name"] if metadata else None
        stats["strategy_timeframe"] = metadata["timeframe"] if metadata else None
        stats["strategy_can_short"] = metadata["can_short"] if metadata else None
        stats["strategy_description"] = metadata["description"] if metadata else ""
        stats["strategy_path"] = metadata["path"] if metadata else None
        stats["mapping_status"] = "mapped" if metadata else "unmapped"
        rows.append(stats)

    return sorted(rows, key=lambda item: (item["total_profit_abs"], item["win_rate"]), reverse=True)


def compute_pair_stats(trades):
    pair_stats = {}
    for trade in trades:
        pair_name = trade["pair"]
        stats = pair_stats.setdefault(
            pair_name,
            {
                "pair": pair_name,
                "trades": 0,
                "profit_pct": 0.0,
                "profit_abs": 0.0,
                "wins": 0,
                "losses": 0,
                "strategies": set(),
            },
        )
        stats["trades"] += 1
        stats["profit_pct"] += trade["profit_pct"]
        stats["profit_abs"] += trade["close_profit_abs"]
        stats["strategies"].add(trade["strategy"])
        if trade["profit_pct"] > 0:
            stats["wins"] += 1
        elif trade["profit_pct"] < 0:
            stats["losses"] += 1

    rows = []
    for stats in pair_stats.values():
        stats["avg_profit_pct"] = round(stats["profit_pct"] / stats["trades"], 4) if stats["trades"] else 0.0
        stats["win_rate"] = round((stats["wins"] / stats["trades"]) * 100.0, 2) if stats["trades"] else 0.0
        stats["strategies"] = sorted(stats["strategies"])
        rows.append(stats)
    return sorted(rows, key=lambda item: item["profit_abs"], reverse=True)


def compute_exit_stats(trades):
    exit_stats = {}
    for trade in trades:
        exit_reason = trade["exit_reason"]
        stats = exit_stats.setdefault(
            exit_reason,
            {
                "exit_reason": exit_reason,
                "count": 0,
                "profit_pct": 0.0,
                "profit_abs": 0.0,
                "wins": 0,
                "losses": 0,
                "strategies": set(),
            },
        )
        stats["count"] += 1
        stats["profit_pct"] += trade["profit_pct"]
        stats["profit_abs"] += trade["close_profit_abs"]
        stats["strategies"].add(trade["strategy"])
        if trade["profit_pct"] > 0:
            stats["wins"] += 1
        elif trade["profit_pct"] < 0:
            stats["losses"] += 1

    rows = []
    for stats in exit_stats.values():
        stats["avg_profit_pct"] = round(stats["profit_pct"] / stats["count"], 4) if stats["count"] else 0.0
        stats["win_rate"] = round((stats["wins"] / stats["count"]) * 100.0, 2) if stats["count"] else 0.0
        stats["strategies"] = sorted(stats["strategies"])
        rows.append(stats)
    return sorted(rows, key=lambda item: (item["count"], item["profit_abs"]), reverse=True)


def compute_equity_curve(trades):
    sorted_trades = sorted(trades, key=lambda item: item.get("close_date") or "")
    curve = []
    running_equity = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for trade in sorted_trades:
        running_equity += trade["close_profit_abs"]
        peak = max(peak, running_equity)
        drawdown = running_equity - peak
        max_drawdown = min(max_drawdown, drawdown)
        curve.append(
            {
                "date": trade.get("close_date") or "",
                "equity": round(running_equity, 4),
                "drawdown": round(drawdown, 4),
                "strategy": trade["strategy"],
                "pair": trade["pair"],
                "profit_abs": round(trade["close_profit_abs"], 4),
            }
        )

    return curve, round(abs(max_drawdown), 4)


def compute_buckets(trades):
    duration_buckets = {
        "0-30m": 0,
        "30m-2h": 0,
        "2h-8h": 0,
        "8h-24h": 0,
        "1-3d": 0,
        "3d+": 0,
    }
    profit_buckets = {
        "<-5%": 0,
        "-5% to -1%": 0,
        "-1% to 0%": 0,
        "0% to 1%": 0,
        "1% to 5%": 0,
        ">5%": 0,
    }

    for trade in trades:
        duration = trade["duration_minutes"]
        profit_pct = trade["profit_pct"]

        if duration < 30:
            duration_buckets["0-30m"] += 1
        elif duration < 120:
            duration_buckets["30m-2h"] += 1
        elif duration < 480:
            duration_buckets["2h-8h"] += 1
        elif duration < 1440:
            duration_buckets["8h-24h"] += 1
        elif duration < 4320:
            duration_buckets["1-3d"] += 1
        else:
            duration_buckets["3d+"] += 1

        if profit_pct < -5:
            profit_buckets["<-5%"] += 1
        elif profit_pct < -1:
            profit_buckets["-5% to -1%"] += 1
        elif profit_pct < 0:
            profit_buckets["-1% to 0%"] += 1
        elif profit_pct < 1:
            profit_buckets["0% to 1%"] += 1
        elif profit_pct < 5:
            profit_buckets["1% to 5%"] += 1
        else:
            profit_buckets[">5%"] += 1

    return duration_buckets, profit_buckets


def build_highlights(trades, strategy_scorecard, pair_rows, exit_rows, max_drawdown):
    highlights = []
    if not trades:
        return highlights

    total_profit_abs = sum(trade["close_profit_abs"] for trade in trades)
    best_strategy = strategy_scorecard[0] if strategy_scorecard else None
    worst_strategy = min(strategy_scorecard, key=lambda item: item["total_profit_abs"]) if strategy_scorecard else None
    best_pair = pair_rows[0] if pair_rows else None
    top_exit = exit_rows[0] if exit_rows else None
    sold_on_exchange = next((item for item in exit_rows if item["exit_reason"] == "sold_on_exchange"), None)

    if best_strategy:
        highlights.append(
            f"{best_strategy['strategy']} led with {best_strategy['total_profit_abs']:.2f} absolute profit across "
            f"{best_strategy['total_trades']} closed trades at a {best_strategy['win_rate']:.2f}% win rate."
        )
    if worst_strategy and worst_strategy["total_profit_abs"] < 0:
        highlights.append(
            f"{worst_strategy['strategy']} was the weakest realized contributor at {worst_strategy['total_profit_abs']:.2f}."
        )
    if best_pair and total_profit_abs:
        contribution_pct = (best_pair["profit_abs"] / total_profit_abs) * 100.0 if total_profit_abs else 0.0
        highlights.append(
            f"Top pair contribution came from {best_pair['pair']} with {best_pair['profit_abs']:.2f} "
            f"({contribution_pct:.1f}% of total realized profit)."
        )
    if top_exit:
        highlights.append(
            f"Most exits were {top_exit['exit_reason']} ({top_exit['count']} trades, {top_exit['avg_profit_pct']:.2f}% average)."
        )
    if sold_on_exchange and sold_on_exchange["avg_profit_pct"] < 0:
        highlights.append(
            f"'sold_on_exchange' stands out as a likely weak exit path: {sold_on_exchange['count']} trades at "
            f"{sold_on_exchange['avg_profit_pct']:.2f}% average."
        )
    highlights.append(f"Max realized drawdown on the closed-trade equity curve was {max_drawdown:.4f}.")
    return highlights


def make_svg_line(points, value_key, color, width=940, height=280):
    if not points:
        return f'<svg viewBox="0 0 {width} {height}" class="chart-svg"></svg>'

    values = [safe_float(point[value_key]) for point in points]
    min_val = min(values)
    max_val = max(values)
    span = max(max_val - min_val, 1e-9)
    chart_left = 24
    chart_top = 12
    chart_width = width - 48
    chart_height = height - 32
    coords = []

    for idx, value in enumerate(values):
        x = chart_left + (idx / max(len(values) - 1, 1)) * chart_width
        y = chart_top + chart_height - ((value - min_val) / span) * chart_height
        coords.append(f"{x:.2f},{y:.2f}")

    baseline_value = 0.0
    baseline_y = chart_top + chart_height - ((baseline_value - min_val) / span) * chart_height
    baseline_y = min(max(chart_top, baseline_y), chart_top + chart_height)

    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart-svg">'
        f'<line x1="{chart_left}" y1="{baseline_y:.2f}" x2="{chart_left + chart_width}" y2="{baseline_y:.2f}" '
        f'stroke="rgba(255,255,255,0.15)" stroke-width="1" />'
        f'<polyline fill="none" stroke="{color}" stroke-width="3" points="{" ".join(coords)}" />'
        "</svg>"
    )


def make_bar_rows(bucket_map, percent=False):
    total = sum(bucket_map.values()) or 1
    rows = []
    for label, count in bucket_map.items():
        width = (count / total) * 100.0
        suffix = "%" if percent else ""
        rows.append(
            "<div class='bar-row'>"
            f"<div class='bar-label'>{escape(label)}</div>"
            f"<div class='bar-track'><div class='bar-fill' style='width:{width:.2f}%'></div></div>"
            f"<div class='bar-value'>{count}{suffix}</div>"
            "</div>"
        )
    return "\n".join(rows)


def render_table(headers, rows):
    head_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    body_chunks = []
    for row in rows:
        body_chunks.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>")
    return (
        "<table><thead><tr>"
        + head_html
        + "</tr></thead><tbody>"
        + "".join(body_chunks)
        + "</tbody></table>"
    )


def build_html(report):
    score_rows = []
    for item in report["strategy_scorecard"]:
        mapped = "Yes" if item["mapping_status"] == "mapped" else "No"
        score_rows.append(
            [
                escape(item["strategy"]),
                escape(item.get("strategy_file") or "unmapped"),
                mapped,
                str(item["total_trades"]),
                f"{item['win_rate']:.2f}%",
                f"{item['total_profit_abs']:.4f}",
                f"{item['avg_profit_pct']:.4f}%",
                f"{item['profit_factor']:.2f}" if item["profit_factor"] != 999.0 else "Inf",
                str(item["short_trades"]),
                str(item["long_trades"]),
                f"{item['avg_duration_min']:.1f}",
            ]
        )

    pair_rows = []
    for item in report["pair_contribution"][:20]:
        pair_rows.append(
            [
                escape(item["pair"]),
                str(item["trades"]),
                f"{item['profit_abs']:.4f}",
                f"{item['avg_profit_pct']:.4f}%",
                f"{item['win_rate']:.2f}%",
                escape(", ".join(item["strategies"][:4])),
            ]
        )

    exit_rows = []
    for item in report["exit_reason_audit"]:
        exit_rows.append(
            [
                escape(item["exit_reason"]),
                str(item["count"]),
                f"{item['profit_abs']:.4f}",
                f"{item['avg_profit_pct']:.4f}%",
                f"{item['win_rate']:.2f}%",
                escape(", ".join(item["strategies"][:4])),
            ]
        )

    db_rows = []
    for item in report["db_summary"]:
        db_rows.append(
            [
                escape(item["db_name"]),
                str(item["total_trades"]),
                str(item["closed_trades"]),
                str(item["open_trades"]),
                escape(", ".join(f"{row['strategy']} ({row['trade_count']})" for row in item["strategies"][:5])),
            ]
        )

    unresolved_rows = []
    for item in report["strategy_scorecard"]:
        if item["mapping_status"] == "unmapped":
            unresolved_rows.append([escape(item["strategy"]), str(item["total_trades"]), escape(", ".join(item["db_sources"]))])

    summary = report["summary"]
    equity_svg = make_svg_line(report["equity_curve"], "equity", "#60d394")
    drawdown_svg = make_svg_line(report["equity_curve"], "drawdown", "#ff5964")

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Freqtrade History Report</title>
  <style>
    :root {{
      --bg: #0f1720;
      --panel: #16212d;
      --panel-2: #1b2a39;
      --ink: #e8f0f7;
      --muted: #9ab0c3;
      --line: rgba(255,255,255,0.08);
      --good: #60d394;
      --warn: #ffca3a;
      --bad: #ff5964;
      --accent: #4ea8de;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Arial, sans-serif;
      background:
        radial-gradient(circle at top right, rgba(78,168,222,0.22), transparent 28%),
        linear-gradient(180deg, #081018 0%, var(--bg) 100%);
      color: var(--ink);
    }}
    .wrap {{ max-width: 1500px; margin: 0 auto; padding: 24px; }}
    h1, h2, h3 {{ margin: 0 0 10px; }}
    p, li {{ color: var(--muted); }}
    .hero {{ display: flex; justify-content: space-between; gap: 24px; align-items: flex-start; margin-bottom: 20px; }}
    .hero-copy {{ max-width: 950px; }}
    .hero-copy p {{ margin: 6px 0; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin: 18px 0 26px;
    }}
    .card, .panel {{
      background: linear-gradient(180deg, rgba(255,255,255,0.03), rgba(255,255,255,0.01));
      border: 1px solid var(--line);
      border-radius: 16px;
      box-shadow: 0 20px 40px rgba(0,0,0,0.18);
    }}
    .card {{ padding: 18px; }}
    .metric-label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em; }}
    .metric-value {{ font-size: 30px; font-weight: 700; margin-top: 8px; }}
    .metric-note {{ margin-top: 6px; font-size: 13px; color: var(--muted); }}
    .section {{ margin: 18px 0; }}
    .panel {{ padding: 18px; overflow: hidden; }}
    .two-col {{ display: grid; grid-template-columns: 1.6fr 1fr; gap: 18px; }}
    .chart-svg {{ width: 100%; height: auto; display: block; background: rgba(255,255,255,0.01); border-radius: 12px; }}
    .chart-caption {{ display: flex; justify-content: space-between; color: var(--muted); font-size: 12px; margin-top: 8px; }}
    .insight-list {{ margin: 8px 0 0; padding-left: 18px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }}
    th, td {{ padding: 10px 12px; border-bottom: 1px solid var(--line); vertical-align: top; text-align: left; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; }}
    tr:hover td {{ background: rgba(255,255,255,0.02); }}
    .bars {{ display: grid; gap: 10px; }}
    .bar-row {{ display: grid; grid-template-columns: 110px 1fr 64px; gap: 10px; align-items: center; }}
    .bar-label, .bar-value {{ font-size: 13px; color: var(--ink); }}
    .bar-track {{ height: 12px; background: rgba(255,255,255,0.08); border-radius: 999px; overflow: hidden; }}
    .bar-fill {{ height: 100%; background: linear-gradient(90deg, var(--accent), var(--good)); border-radius: 999px; }}
    .muted {{ color: var(--muted); }}
    .foot {{ margin-top: 20px; font-size: 12px; color: var(--muted); }}
    @media (max-width: 1200px) {{
      .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .two-col {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 720px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .hero {{ flex-direction: column; }}
      .wrap {{ padding: 16px; }}
      .bar-row {{ grid-template-columns: 92px 1fr 56px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-copy">
        <h1>Freqtrade History Report</h1>
        <p>Built from 4 SQLite trade databases and correlated against the strategy files in both <code>user_data/strategies</code> folders.</p>
        <p>Closed trades analyzed: <strong>{summary['total_trades']}</strong>. Mapped strategies: <strong>{summary['mapped_strategy_count']}</strong> of <strong>{summary['strategy_count']}</strong>.</p>
      </div>
      <div class="panel" style="min-width:320px;">
        <h3>Database Coverage</h3>
        {render_table(["DB", "Total", "Closed", "Open", "Top strategies"], db_rows)}
      </div>
    </section>

    <section class="grid">
      <div class="card">
        <div class="metric-label">Total Realized Profit</div>
        <div class="metric-value">{summary['total_profit_abs']:.4f}</div>
        <div class="metric-note">Across all closed trades</div>
      </div>
      <div class="card">
        <div class="metric-label">Win Rate</div>
        <div class="metric-value">{summary['overall_win_rate']:.2f}%</div>
        <div class="metric-note">{summary['total_wins']} wins / {summary['total_losses']} losses / {summary['total_break_even']} flat</div>
      </div>
      <div class="card">
        <div class="metric-label">Max Drawdown</div>
        <div class="metric-value">{summary['max_drawdown']:.4f}</div>
        <div class="metric-note">Closed-trade equity curve</div>
      </div>
      <div class="card">
        <div class="metric-label">Distinct Strategies</div>
        <div class="metric-value">{summary['strategy_count']}</div>
        <div class="metric-note">Distinct traded strategies across the four DBs</div>
      </div>
    </section>

    <section class="section two-col">
      <div class="panel">
        <h2>Equity + Drawdown Curve Viewer</h2>
        {equity_svg}
        <div class="chart-caption"><span>Equity</span><span>Final equity: {summary['final_equity']:.4f}</span></div>
        <div style="height:14px;"></div>
        {drawdown_svg}
        <div class="chart-caption"><span>Drawdown</span><span>Worst drawdown: {summary['max_drawdown']:.4f}</span></div>
      </div>
      <div class="panel">
        <h2>Key Insights</h2>
        <ul class="insight-list">
          {"".join(f"<li>{escape(item)}</li>" for item in report["highlights"])}
        </ul>
      </div>
    </section>

    <section class="section panel">
      <h2>Strategy Scorecard</h2>
      <p class="muted">One-page summary of realized strategy behavior, including file/class mapping where available.</p>
      {render_table(["Strategy", "Strategy File", "Mapped", "Trades", "Win Rate", "Profit Abs", "Avg Profit", "Profit Factor", "Shorts", "Longs", "Avg Min"], score_rows)}
    </section>

    <section class="section two-col">
      <div class="panel">
        <h2>Pair Contribution Table</h2>
        <p class="muted">Where the edge actually came from.</p>
        {render_table(["Pair", "Trades", "Profit Abs", "Avg Profit", "Win Rate", "Strategies"], pair_rows)}
      </div>
      <div class="panel">
        <h2>Exit Reason Audit</h2>
        <p class="muted">Often exposes weak exits quickly.</p>
        {render_table(["Exit Reason", "Trades", "Profit Abs", "Avg Profit", "Win Rate", "Strategies"], exit_rows)}
      </div>
    </section>

    <section class="section two-col">
      <div class="panel">
        <h2>Trade Distribution Explorer</h2>
        <p class="muted">Duration buckets</p>
        <div class="bars">{make_bar_rows(report["trade_distribution"]["by_duration"])}</div>
      </div>
      <div class="panel">
        <h2>Trade Distribution Explorer</h2>
        <p class="muted">Profit buckets</p>
        <div class="bars">{make_bar_rows(report["trade_distribution"]["by_profit"])}</div>
      </div>
    </section>

    <section class="section panel">
      <h2>Strategy Mapping Gaps</h2>
      <p class="muted">These traded strategy names did not resolve to a current local strategy file/class match.</p>
      {render_table(["Strategy", "Trades", "Seen In"], unresolved_rows or [["None", "-", "-"]])}
    </section>

    <div class="foot">
      Generated from local trade databases and strategy sources. Files written: <code>{escape(str(REPORT_JSON_PATH))}</code> and <code>{escape(str(REPORT_HTML_PATH))}</code>.
    </div>
  </div>
</body>
</html>
"""


def build_report():
    trades, db_summary = read_trades()
    strategy_lookup, strategy_files = scan_strategy_metadata()
    strategy_scorecard = compute_strategy_stats(trades, strategy_lookup)
    pair_contribution = compute_pair_stats(trades)
    exit_reason_audit = compute_exit_stats(trades)
    equity_curve, max_drawdown = compute_equity_curve(trades)
    duration_buckets, profit_buckets = compute_buckets(trades)

    total_wins = sum(1 for trade in trades if trade["profit_pct"] > 0)
    total_losses = sum(1 for trade in trades if trade["profit_pct"] < 0)
    total_break_even = sum(1 for trade in trades if trade["profit_pct"] == 0)
    mapped_strategy_count = sum(1 for row in strategy_scorecard if row["mapping_status"] == "mapped")

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total_trades": len(trades),
            "total_wins": total_wins,
            "total_losses": total_losses,
            "total_break_even": total_break_even,
            "overall_win_rate": round((total_wins / len(trades)) * 100.0, 2) if trades else 0.0,
            "total_profit_abs": round(sum(trade["close_profit_abs"] for trade in trades), 4),
            "avg_profit_pct": round(sum(trade["profit_pct"] for trade in trades) / len(trades), 4) if trades else 0.0,
            "max_drawdown": max_drawdown,
            "final_equity": round(equity_curve[-1]["equity"], 4) if equity_curve else 0.0,
            "strategy_count": len(strategy_scorecard),
            "mapped_strategy_count": mapped_strategy_count,
            "strategy_file_count": len(strategy_files),
            "pair_count": len(pair_contribution),
            "exit_reason_count": len(exit_reason_audit),
        },
        "db_summary": db_summary,
        "strategy_scorecard": strategy_scorecard,
        "pair_contribution": pair_contribution,
        "exit_reason_audit": exit_reason_audit,
        "equity_curve": equity_curve,
        "trade_distribution": {
            "by_duration": duration_buckets,
            "by_profit": profit_buckets,
        },
        "strategy_files": strategy_files,
        "top_trades": sorted(trades, key=lambda item: item["close_profit_abs"], reverse=True)[:20],
        "worst_trades": sorted(trades, key=lambda item: item["close_profit_abs"])[:20],
        "highlights": build_highlights(trades, strategy_scorecard, pair_contribution, exit_reason_audit, max_drawdown),
    }
    return report


def main():
    report = build_report()
    REPORT_JSON_PATH.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    REPORT_HTML_PATH.write_text(build_html(report), encoding="utf-8")

    print(f"Closed trades analyzed: {report['summary']['total_trades']}")
    print(f"Strategies traded: {report['summary']['strategy_count']}")
    print(f"Mapped strategies: {report['summary']['mapped_strategy_count']}")
    print(f"Total realized profit: {report['summary']['total_profit_abs']:.4f}")
    print(f"Win rate: {report['summary']['overall_win_rate']:.2f}%")
    print(f"Max drawdown: {report['summary']['max_drawdown']:.4f}")
    print(f"JSON report: {REPORT_JSON_PATH}")
    print(f"HTML report: {REPORT_HTML_PATH}")

    try:
        webbrowser.open(REPORT_HTML_PATH.resolve().as_uri(), new=1)
        print("Opened HTML report in the default browser.")
    except Exception as exc:
        print(f"Could not auto-open HTML report: {exc}")


if __name__ == "__main__":
    main()
