from __future__ import annotations

import argparse
import logging
import threading
import time
from dataclasses import dataclass

from api.api.rollover import RolloverConflictError, RolloverResult, create_rollover_successor

logger = logging.getLogger(__name__)


@dataclass
class WatchdogPollResult:
    checked_sessions: int
    triggered_rollovers: list[RolloverResult]


class SessionWatchdog:
    """Poll session planning state and trigger rollover when thresholds are exceeded."""

    def __init__(self, app, *, poll_interval_seconds: float = 30.0):
        self.app = app
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> WatchdogPollResult:
        rows = self.app.db.execute(
            "SELECT id FROM sessions WHERE status = 'active' AND rolled_over_to IS NULL ORDER BY created_at ASC"
        ).fetchall()
        triggered: list[RolloverResult] = []

        for row in rows:
            session_id = row["id"]
            if not self.app.planning.should_rollover(session_id):
                continue
            if self.app.run_manager.get_active_run(session_id):
                logger.info("Skipping rollover for %s because a run is still active", session_id)
                continue

            try:
                triggered.append(create_rollover_successor(self.app, session_id))
            except RolloverConflictError as exc:
                logger.info("Skipping rollover for %s: %s", session_id, exc)

        return WatchdogPollResult(
            checked_sessions=len(rows),
            triggered_rollovers=triggered,
        )

    def run_forever(self) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(self.poll_interval_seconds)

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_background_loop, name="opencloset-watchdog", daemon=True)
        self._thread.start()

    def stop_background(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run_background_loop(self) -> None:
        from api.api.app import create_app

        isolated_app = create_app(db_path=self.app.config["DB_PATH"], start_background_workers=False)
        isolated_watchdog = isolated_app.watchdog
        isolated_watchdog.poll_interval_seconds = self.poll_interval_seconds
        try:
            while not self._stop_event.is_set():
                isolated_watchdog.poll_once()
                self._stop_event.wait(self.poll_interval_seconds)
        finally:
            isolated_app.close()


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Poll OpenCloset sessions and trigger rollovers when needed.")
    parser.add_argument("--db", dest="db_path", default=None, help="Path to the OpenCloset SQLite database.")
    parser.add_argument(
        "--poll-interval",
        dest="poll_interval_seconds",
        type=float,
        default=30.0,
        help="Seconds between watchdog polls.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single poll and exit.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    from api.api.app import create_app

    app = create_app(db_path=args.db_path, start_background_workers=False)
    watchdog = SessionWatchdog(app, poll_interval_seconds=args.poll_interval_seconds)
    try:
        if args.once:
            result = watchdog.poll_once()
            logger.info(
                "Watchdog checked %d session(s) and triggered %d rollover(s)",
                result.checked_sessions,
                len(result.triggered_rollovers),
            )
            return 0

        watchdog.run_forever()
        return 0
    finally:
        app.close()


if __name__ == "__main__":
    raise SystemExit(main())