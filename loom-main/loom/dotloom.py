"""The .loom file — the human declaration that GENERATES an .oradio (1:1).

A .loom is two fields:

    universe:     <natural language>     # the INTENT
    connections:  [<plugin>, ...]        # the PARTS

It is not an app. It's a tiny, embeddable declaration + generator: **one .loom makes
exactly one .oradio.** (Want many? build a spawner *around* the .loom — a later
problem, not loom's.)

The universe is INTENT, not a promise: it deterministically seeds the world and is
carried for any renderer to use, but what the universe *becomes* is realized through
the connections — the experiment the author arranges. loom stops at this declaration;
the engine / club / plugins do the rest.

A connection's *role* (world / source / effector) is inferred from the plugin itself
(the registry knows what it registered) — the author just lists plugins. An explicit
``as:`` overrides, for external plugins not yet fetched.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List

from oradio_engine.registry import ORGAN_KINDS, SOURCE_KINDS


def universe_seed(universe: str) -> int:
    """A deterministic 32-bit seed from the universe text — the intent's fingerprint."""
    digest = hashlib.sha256((universe or "").strip().encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _slug(universe: str) -> str:
    words = "".join(c if c.isalnum() or c.isspace() else " " for c in (universe or "")).split()
    return "-".join(words[:5]).lower() or "untitled-loom"


@dataclass
class Connection:
    plugin: str                                  # the kind/name, e.g. "simulated_spatial_array"
    name: str = ""
    as_role: str = ""                            # "world"|"source"|"effector" — usually inferred
    source: str = ""                             # optional github coordinate (github:owner/repo)
    sha256: str = ""
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def parse(cls, spec: Any) -> "Connection":
        if isinstance(spec, str):
            return cls(plugin=spec, name=spec)
        d = dict(spec)
        plugin = d.pop("plugin", None) or d.pop("kind", None) or d.pop("connect", None) or ""
        return cls(
            plugin=plugin,
            name=d.pop("name", plugin),
            as_role=d.pop("as", ""),
            source=d.pop("source", ""),
            sha256=d.pop("sha256", ""),
            params=d,                            # whatever's left = build params
        )

    def role(self) -> str:
        if self.as_role:
            return self.as_role
        if self.plugin in ORGAN_KINDS:
            return "world"
        if self.plugin in SOURCE_KINDS:
            return "source"
        return "source"                          # default: most permissive — just emit on the wire


@dataclass
class Loom:
    universe: str = ""
    connections: List[Connection] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Loom":
        return cls(
            universe=str(d.get("universe", "")).strip(),
            connections=[Connection.parse(c) for c in (d.get("connections") or [])],
        )


def loom_to_oradio(loom: Loom) -> Dict[str, Any]:
    """Generate exactly ONE .oradio descriptor from a .loom. Deterministic, 1:1."""
    seed = universe_seed(loom.universe)
    oradio: Dict[str, Any] = {"oradio": _slug(loom.universe), "intent": loom.universe}

    worlds: List[Dict[str, Any]] = []
    telemetry: List[Dict[str, Any]] = []
    effectors: List[Dict[str, Any]] = []
    for c in loom.connections:
        node: Dict[str, Any] = {"name": c.name or c.plugin, **c.params}
        if c.source:
            node["plugin"] = c.source
            if c.sha256:
                node["sha256"] = c.sha256
        role = c.role()
        if role == "world":
            worlds.append({"organ": c.plugin, "seed": seed, **node})
        elif role == "effector":
            effectors.append({"kind": c.plugin, **node})
        else:
            telemetry.append({"source": c.plugin, **node})

    if worlds:
        oradio["worlds"] = worlds
    if telemetry:
        oradio["telemetry"] = telemetry
    if effectors:
        oradio["effectors"] = effectors
    return oradio


def load_loom_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """A .loom dict -> the generated .oradio dict (the whole point, in one call)."""
    return loom_to_oradio(Loom.from_dict(d))


def load_loom_file(path: str) -> Dict[str, Any]:
    """Read a .loom file (YAML/JSON) -> the generated .oradio dict."""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
    except ImportError:
        import json
        data = json.loads(text)
    return load_loom_dict(data or {})
