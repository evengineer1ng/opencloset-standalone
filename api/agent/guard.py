# TokenPressureMonitor — primitive context guard
#
# Hooks into ConversationRuntime to detect token pressure before overflow.
# Pure detection + signaling; no handoff/rollover logic (that's Phase 1).
#
# Design: two thresholds (warning ~75%, critical ~87%), pluggable callbacks.
# Token estimation: character-based ratio until tokenizer is available.

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token estimation (character-based fallback)
# ---------------------------------------------------------------------------

def estimate_tokens(text: str, *, language: str = "en") -> int:
    """Rough token estimate from character count.

    Ratios (approximate):
    - English text: ~4 chars per token
    - Code: ~3 chars per token
    - CJK: ~1.5 chars per token
    """
    if not text:
        return 0

    # Simple heuristic: detect if mostly code-like (high ratio of special chars)
    special_chars = sum(1 for c in text if c in "{}[]()<>;:=!&|~`#'\"\\")
    ratio = special_chars / max(len(text), 1)

    if ratio > 0.08:
        # Code-heavy
        return max(1, len(text) // 3)
    elif any("\u4e00" <= c <= "\u9fff" for c in text):
        # CJK detected
        return max(1, len(text) // 2)
    else:
        # English text
        return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Pressure levels
# ---------------------------------------------------------------------------

class PressureLevel:
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# TokenPressureMonitor
# ---------------------------------------------------------------------------

@dataclass
class TokenPressureMonitor:
    """Primitive token-pressure monitor. Hooks into ConversationRuntime.

    Thresholds:
    - warning_threshold: usage % to trigger warning (default 75%)
    - critical_threshold: usage % to trigger critical (default 87%)

    Callbacks (pluggable, set by caller):
    - on_warning(runtime): called when usage exceeds warning threshold
    - on_critical(runtime): called when usage exceeds critical threshold
    """

    warning_threshold: float = 75.0
    critical_threshold: float = 87.0
    on_warning: Callable | None = None
    on_critical: Callable | None = None

    # State tracking
    _last_level: str = field(default=PressureLevel.OK, init=False)
    _fired_warning: bool = field(default=False, init=False)
    _fired_critical: bool = field(default=False, init=False)

    def check(self, runtime) -> str:
        """Check token pressure against thresholds. Returns current level.

        Callbacks fire only on transition (not every check at same level).
        Resets _fired flags when pressure drops below threshold.
        """
        usage = runtime.usage_pct
        current_level = self._classify(usage)

        # Fire callback only on transition up
        if current_level != self._last_level:
            if current_level == PressureLevel.WARNING and not self._fired_warning:
                self._fired_warning = True
                logger.warning(
                    "Token pressure WARNING: %.1f%% usage (session %s, %d/%d tokens)",
                    usage, runtime.session_id, runtime.token_count, runtime.context_window,
                )
                if self.on_warning:
                    self.on_warning(runtime)

            elif current_level == PressureLevel.CRITICAL and not self._fired_critical:
                self._fired_critical = True
                logger.critical(
                    "Token pressure CRITICAL: %.1f%% usage (session %s, %d/%d tokens)",
                    usage, runtime.session_id, runtime.token_count, runtime.context_window,
                )
                if self.on_critical:
                    self.on_critical(runtime)

            # Reset flags when pressure drops
            if current_level != PressureLevel.WARNING:
                self._fired_warning = False
            if current_level != PressureLevel.CRITICAL:
                self._fired_critical = False

            self._last_level = current_level

        return current_level

    def _classify(self, usage_pct: float) -> str:
        if usage_pct >= self.critical_threshold:
            return PressureLevel.CRITICAL
        elif usage_pct >= self.warning_threshold:
            return PressureLevel.WARNING
        else:
            return PressureLevel.OK

    def reset(self) -> None:
        """Reset state (e.g., after rollover)."""
        self._last_level = PressureLevel.OK
        self._fired_warning = False
        self._fired_critical = False

    def status(self) -> dict:
        """Current monitor state for diagnostics."""
        return {
            "warning_threshold": self.warning_threshold,
            "critical_threshold": self.critical_threshold,
            "last_level": self._last_level,
            "fired_warning": self._fired_warning,
            "fired_critical": self._fired_critical,
        }


# ---------------------------------------------------------------------------
# Convenience: attach monitor to ConversationRuntime
# ---------------------------------------------------------------------------

def attach_monitor(
    runtime,
    *,
    warning_threshold: float = 75.0,
    critical_threshold: float = 87.0,
    on_warning: Callable | None = None,
    on_critical: Callable | None = None,
) -> TokenPressureMonitor:
    """Create and attach a monitor to a ConversationRuntime instance.

    Returns the monitor for callback configuration.
    """
    monitor = TokenPressureMonitor(
        warning_threshold=warning_threshold,
        critical_threshold=critical_threshold,
        on_warning=on_warning,
        on_critical=on_critical,
    )
    runtime.pressure_monitor = monitor

    # Hook into add_message: check after each message is added
    _orig_add_message = runtime.add_message
    def _add_message_with_check(msg, *, run_id=None):
        result = _orig_add_message(msg, run_id=run_id)
        monitor.check(runtime)
        return result
    runtime.add_message = _add_message_with_check

    return monitor
