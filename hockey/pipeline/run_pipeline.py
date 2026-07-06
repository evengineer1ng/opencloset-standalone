"""Convenience entry point.

Usage:
    python run_pipeline.py --help
    python run_pipeline.py bootstrap --seasons 2020-21 2021-22
    python run_pipeline.py ingest-season --season 2024-25
    python run_pipeline.py ingest-game --game-id 2024020001
"""

from pipeline.cli import cli

if __name__ == "__main__":
    cli()
