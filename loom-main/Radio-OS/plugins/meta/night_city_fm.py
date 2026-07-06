"""
Night City FM — Meta Plugin for Radio OS

Gives the station its cyberpunk radio identity: sardonic host persona,
Night City-flavored cold opens, and no corp interference.

Meta plugin name (maps to manifest key): night_city_fm
"""

from typing import Any, Dict, List, Optional
import random
import time

try:
    from bookmark import MetaPluginBase
except ImportError:
    # Loaded before bookmark is in sys.modules (development / test context).
    # Provide a minimal stub so the module still importable.
    from abc import ABC, abstractmethod

    class MetaPluginBase(ABC):  # type: ignore[no-redef]
        @abstractmethod
        def initialize(self, runtime_context, cfg, mem): ...
        @abstractmethod
        def shutdown(self): ...


# ---------------------------------------------------------------------------
# Cold Open Pool
# ---------------------------------------------------------------------------

_COLD_OPENS: List[str] = [
    (
        "Night City FM. Ghost-streaming from a datavault under Corpo Plaza. "
        "I'm Nyx, and the city's got more bugs tonight than a Militech firmware push. "
        "Stay frosty, choombas — the signal's encrypted, the sponsors are flatlined, "
        "and the truth is still trading at premium eddies."
    ),
    (
        "Signal's live. Night City FM on the dark frequency. "
        "I'm Nyx — your fixer for the news that doesn't show up on the NET. "
        "The city never sleeps, it just flatlines between corpo power surges. "
        "Plug in, tune out, and let's ride."
    ),
    (
        "You're dialed into the only station the NCPD can't trace and corps can't buy. "
        "Night City FM. I'm Nyx, and the city's talking tonight — "
        "the kind of talk that doesn't end with a corpo press release. "
        "Let's see what Night City dragged in."
    ),
    (
        "Nyx here, broadcasting live from somewhere the Trauma Team won't pick you up. "
        "Night City FM, cutting through the noise so you don't have to. "
        "The city's got stories. We've got time. "
        "Strap in, choomba."
    ),
    (
        "Night City FM — still on air, still unjacked. "
        "I'm Nyx. You found the frequency the corps keep trying to kill. "
        "Tonight we're talking about what's real in a city that sells you "
        "chrome dreams at street-cred prices."
    ),
]


# ---------------------------------------------------------------------------
# Meta Plugin Class
# ---------------------------------------------------------------------------

class NightCityFMPlugin(MetaPluginBase):
    """
    Meta plugin for Night City FM.

    Responsibilities:
    - Deliver a thematic cold open on station start.
    - Remain lean: let bookmark.py's core host pipeline handle scripting.
      Override generate_script() here if you want deeper cyberpunk
      prompt injection into every segment.
    """

    def initialize(
        self,
        runtime_context: Dict[str, Any],
        cfg: Dict[str, Any],
        mem: Dict[str, Any],
    ) -> None:
        self.runtime = runtime_context
        self.cfg = cfg
        self.mem = mem
        self._log = runtime_context.get("log", print)
        self._log("night_city_fm", "Night City FM meta plugin online. Dark frequency active.")

    def shutdown(self) -> None:
        self._log("night_city_fm", "Night City FM signing off. Stay frosty, choombas.")

    # ------------------------------------------------------------------
    # Cold Open  (called once at boot when the queue is empty)
    # ------------------------------------------------------------------

    def cold_open(self) -> Optional[str]:
        """Return a Night City-flavored station intro."""
        # Rotate through the pool so each launch sounds different.
        idx = int(time.time() / 3600) % len(_COLD_OPENS)
        return _COLD_OPENS[idx]

    # ------------------------------------------------------------------
    # Optional: add a cyberpunk lens to incoming segments.
    # Uncomment and expand if you want every segment to pass through
    # an LLM rewrite that enforces Night City slang and tone.
    # ------------------------------------------------------------------

    # def generate_script(
    #     self,
    #     segment: Dict[str, Any],
    #     state: Dict[str, Any],
    # ) -> Optional[Dict[str, Any]]:
    #     """
    #     Optionally reframe each segment through a cyberpunk lens.
    #     Return None to fall through to bookmark.py's default scripting.
    #     """
    #     return None  # Default: use bookmark.py pipeline unchanged
