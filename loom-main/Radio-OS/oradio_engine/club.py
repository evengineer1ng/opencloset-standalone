"""The Club — machine-level dependency resolution for `.oradio` files.

An `.oradio` is tiny because it ships *references + contracts*, not weights/voices/clips. The
**Club** is the membership that makes that work: it resolves every `.oradio`'s declared
dependencies from a machine-level memory, and **asks the human only when something is genuinely
new, or has changed/vanished since last time.** Configure once, reuse forever.

Resolution outcomes per dependency:
  - **resolved**  — built-in (default theme packs, simulated sources) or remembered + still valid.
  - **ask**       — new (never seen) or changed (signature differs) or vanished (path gone).
  - The Club never blocks the deterministic engine from running; asks are enhancements (voices,
    LLM, real hardware). A dep can be marked ``required`` to gate, but MK1 defaults to non-blocking.

This is the engine-native Club (mirrors the older `provisioning.py` / `oradio_resolver.py`
machine-membership idea, rebuilt clean for `oradio_engine`).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Built-in theme packs the Club ships so step 3 of the Loom form is never a wall.
# "ribbon" is the default-default; authors may pick another or bring their own loop.
DEFAULT_THEME_PACKS: Dict[str, Dict[str, Any]] = {
    "ribbon": {"kind": "ribbon", "base": "builtin:ribbon", "morph": "default", "builtin": True},
    "smoke": {"kind": "ribbon", "base": "builtin:smoke", "morph": "default", "builtin": True},
    "aurora": {"kind": "ribbon", "base": "builtin:aurora", "morph": "default", "builtin": True},
    "ember": {"kind": "ribbon", "base": "builtin:ember", "morph": "default", "builtin": True},
}
DEFAULT_THEME = "ribbon"

# Capability kinds the Club understands. `builtin` resolves with no ask (simulated / in-engine);
# `remembered` asks once then persists; `required` gates readiness if unresolved.
CAPABILITY_KINDS: Dict[str, Dict[str, Any]] = {
    "llm": {"builtin": False, "required": False, "prompt": "Point me at your local/cloud LLM endpoint."},
    "voices": {"builtin": False, "required": False, "prompt": "Show me where your voice models live."},
    "spatial_array": {"builtin": False, "required": False, "prompt": "Connect your spatial node array (simulated until then)."},
    "capture_card": {"builtin": False, "required": False, "prompt": "Connect your capture card (simulated until then)."},
    "gamepad": {"builtin": False, "required": False, "prompt": "Enable gamepad injection (simulated until then)."},
}


@dataclass
class EndpointRequest:
    """What a telemetry source intends to consume — ADVERTISED before it ever touches it."""

    name: str          # the telemetry node's name in the .oradio
    kind: str          # the source kind (e.g. "ring_telemetry")
    reads: str         # human-readable: what personal data it would observe
    sensitive: bool    # touches personal endpoints (needs consent) vs benign/simulated
    consented: bool    # has the human already allowed this kind, machine-level?


@dataclass
class ClubAsk:
    capability: str          # e.g. "voices", "theme:my_loop.mp4"
    kind: str                # "theme" | "llm" | "voices" | "spatial_array" | ...
    reason: str              # "new" | "changed" | "vanished"
    prompt: str


@dataclass
class ClubReport:
    ready: bool
    resolved: Dict[str, Any] = field(default_factory=dict)
    asks: List[ClubAsk] = field(default_factory=list)
    missing_required: List[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = [f"ready={self.ready}", f"resolved={list(self.resolved)}"]
        if self.asks:
            bits.append(f"asks={[a.capability for a in self.asks]}")
        if self.missing_required:
            bits.append(f"missing_required={self.missing_required}")
        return " · ".join(bits)


def _signature(value: Any) -> str:
    """A cheap change-signature: for a path, its size+mtime; else its repr."""
    if isinstance(value, str) and os.path.exists(value):
        st = os.stat(value)
        return f"{st.st_size}:{int(st.st_mtime)}"
    return repr(value)


class Club:
    """Machine-level membership + dependency resolver."""

    def __init__(self, store_path: Optional[str] = None) -> None:
        self._store_path = store_path or self._default_store_path()
        self._store: Dict[str, Any] = self._load()

    @staticmethod
    def _default_store_path() -> str:
        base = os.environ.get("ORADIO_CLUB_DIR") or os.path.join(
            os.path.expanduser("~"), ".oradio_club"
        )
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "club.json")

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self._store_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
        tmp = self._store_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(self._store, f, indent=2)
        os.replace(tmp, self._store_path)

    # -- machine memory (configure once, reuse forever) ------------------- #
    def remember(self, key: str, value: Any) -> None:
        self._store[key] = {"value": value, "sig": _signature(value), "ts": time.time()}
        self._save()

    def recall(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        return entry["value"] if entry else None

    def forget(self, key: str) -> None:
        self._store.pop(key, None)
        self._save()

    # -- telemetry consent (the responsible handshake) -------------------- #
    # Consent is keyed by source KIND ("you've allowed ring_telemetry before"), asked once,
    # machine-level, revocable. A shared .oradio NEVER touches a sensitive endpoint un-consented.
    def has_consent(self, kind: str) -> bool:
        return bool(self._store.get(f"consent:{kind}"))

    def grant_consent(self, kind: str) -> None:
        self._store[f"consent:{kind}"] = {"value": True, "ts": time.time()}
        self._save()

    def revoke_consent(self, kind: str) -> None:
        self._store.pop(f"consent:{kind}", None)
        self._save()

    def telemetry_manifest(self, descriptor: Any) -> List[EndpointRequest]:
        """ADVERTISE what an `.oradio` will try to consume — *before* it consumes anything.
        This is the inspect-before-run handshake for shared stations."""
        from oradio_engine.registry import SOURCE_META

        manifest: List[EndpointRequest] = []
        for t in getattr(descriptor, "telemetry", []) or []:
            meta = SOURCE_META.get(t.source, {"sensitive": False, "reads": ""})
            sensitive = bool(meta.get("sensitive"))
            manifest.append(EndpointRequest(
                name=t.name, kind=t.source, reads=str(meta.get("reads", "")),
                sensitive=sensitive,
                consented=(not sensitive) or self.has_consent(t.source),
            ))
        return manifest

    def _check_remembered(self, key: str) -> Optional[ClubAsk]:
        """None if resolved-and-valid; a ClubAsk (changed/vanished) otherwise. Caller handles 'new'."""
        entry = self._store.get(key)
        if entry is None:
            return None  # 'new' handled by caller
        value = entry["value"]
        # a remembered local path that no longer exists -> re-ask (only when it genuinely
        # vanished). URLs/endpoints (anything with a scheme) are not filesystem paths.
        is_local_path = (
            isinstance(value, str)
            and "://" not in value
            and ("/" in value or "\\" in value)
        )
        if is_local_path and not os.path.exists(value):
            return ClubAsk(capability=key, kind="path", reason="vanished",
                           prompt=f"I can't find '{value}' anymore — point me again?")
        # changed signature -> re-ask
        if entry.get("sig") != _signature(value):
            return ClubAsk(capability=key, kind="path", reason="changed",
                           prompt=f"'{key}' changed since last time — re-confirm?")
        return None

    # -- theme resolution ------------------------------------------------- #
    def resolve_theme(self, theme: Optional[str]) -> Dict[str, Any]:
        """A pack name (built-in) | a loop path (bring-your-own) | None -> default."""
        if not theme:
            return {"theme": DEFAULT_THEME, **DEFAULT_THEME_PACKS[DEFAULT_THEME], "source": "default"}
        if theme in DEFAULT_THEME_PACKS:
            return {"theme": theme, **DEFAULT_THEME_PACKS[theme], "source": "builtin"}
        # a custom loop path (bring-your-own)
        return {"theme": theme, "kind": "ribbon", "base": theme, "morph": "default",
                "builtin": False, "source": "custom"}

    # -- the one entry point ---------------------------------------------- #
    def resolve(self, descriptor: Any) -> ClubReport:
        """Resolve everything an `.oradio` needs. Returns readiness + asks (new/changed)."""
        report = ClubReport(ready=True)

        # Theme (from descriptor.raw 'theme', else default). Built-in packs never ask.
        raw = getattr(descriptor, "raw", {}) or {}
        theme = raw.get("theme")
        theme_res = self.resolve_theme(theme)
        report.resolved["theme"] = theme_res
        if theme_res["source"] == "custom":
            ask = self._resolve_dependency(f"theme:{theme}", "theme",
                                           CAPABILITY_KINDS.get("voices", {}).get("prompt", ""),
                                           required=False, value=theme)
            if ask:
                report.asks.append(ask)

        # Declared capabilities.
        for cap in getattr(descriptor, "club", []) or []:
            spec = CAPABILITY_KINDS.get(cap, {"builtin": False, "required": False,
                                              "prompt": f"Set up '{cap}'."})
            if spec.get("builtin"):
                report.resolved[cap] = {"status": "builtin"}
                continue
            ask = self._resolve_dependency(cap, cap, spec["prompt"], required=spec.get("required", False))
            if ask is None:
                report.resolved[cap] = {"status": "remembered", "value": self.recall(cap)}
            else:
                report.asks.append(ask)
                if spec.get("required"):
                    report.ready = False
                    report.missing_required.append(cap)
        return report

    def _resolve_dependency(self, key: str, kind: str, prompt: str, *, required: bool,
                            value: Any = None) -> Optional[ClubAsk]:
        """Returns None if resolved (remembered+valid); else a ClubAsk (new/changed/vanished)."""
        if key not in self._store:
            return ClubAsk(capability=key, kind=kind, reason="new", prompt=prompt)
        return self._check_remembered(key)
