"""The `.oradio` descriptor — the tiny declaration the engine decodes.

This is the codec's byte-format (owner-approved shape): an `.oradio` declares its
**world(s)**, **telemetry**, **lens**, **surfaces**, and **club** requirements — and nothing
else. All six domains are this same descriptor wired differently; the loader turns it into a
running federation. Authored by the Loom, resolved by the club, decoded here.

Example (YAML or dict)::

    oradio: home-region
    world:   { organ: neikos, seed: 42 }
    telemetry:
      - { source: simulated_spatial_array, nodes: [front_door, living_room, kitchen],
          binds: "presence -> location" }
    lens:    presence-as-movement
    surfaces: [voice, spatial_audio]
    club:    [llm, voices, spatial_array]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from oradio_engine.dipole import DipoleDecl

_RESERVED_WORLD = {"organ", "name"}
_RESERVED_TELEMETRY = {"source", "name", "binds"}
_RESERVED_EFFECTOR = {"kind", "name"}
_RESERVED_BINDING = {"from", "to", "transform", "name"}


@dataclass
class WorldDecl:
    organ: str
    name: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TelemetryDecl:
    source: str
    name: str
    binds: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EffectorDecl:
    kind: str
    name: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BindingDecl:
    source: str  # 'from'
    target: str  # 'to'
    transform: str
    name: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OradioDescriptor:
    name: str
    worlds: List[WorldDecl] = field(default_factory=list)
    telemetry: List[TelemetryDecl] = field(default_factory=list)
    effectors: List[EffectorDecl] = field(default_factory=list)
    bindings: List[BindingDecl] = field(default_factory=list)
    lens: Union[None, str, Dict[str, Any]] = None
    surfaces: List[str] = field(default_factory=list)
    club: List[str] = field(default_factory=list)
    dipole: Optional[DipoleDecl] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def _world_from(d: Dict[str, Any]) -> WorldDecl:
        organ = d["organ"]
        return WorldDecl(
            organ=organ,
            name=d.get("name", organ),
            params={k: v for k, v in d.items() if k not in _RESERVED_WORLD},
        )

    @staticmethod
    def _telemetry_from(d: Dict[str, Any]) -> TelemetryDecl:
        source = d["source"]
        return TelemetryDecl(
            source=source,
            name=d.get("name", source),
            binds=d.get("binds"),
            params={k: v for k, v in d.items() if k not in _RESERVED_TELEMETRY},
        )

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OradioDescriptor":
        worlds: List[WorldDecl] = []
        if "world" in d:
            worlds.append(cls._world_from(d["world"]))
        for w in d.get("worlds", []):
            worlds.append(cls._world_from(w))

        telemetry = [cls._telemetry_from(t) for t in d.get("telemetry", [])]
        if not worlds and not telemetry:
            # A "me.oradio" is valid with no pre-built world — it emerges from your telemetry.
            raise ValueError("an .oradio must declare at least one world or telemetry source")
        effectors = [
            EffectorDecl(kind=e["kind"], name=e.get("name", e["kind"]),
                         params={k: v for k, v in e.items() if k not in _RESERVED_EFFECTOR})
            for e in d.get("effectors", [])
        ]
        bindings = [
            BindingDecl(source=b["from"], target=b["to"], transform=b["transform"],
                        name=b.get("name", ""),
                        params={k: v for k, v in b.items() if k not in _RESERVED_BINDING})
            for b in d.get("bindings", [])
        ]
        # de-dup names across all registered nodes (federation keys by name)
        names = [w.name for w in worlds] + [t.name for t in telemetry] + [e.name for e in effectors]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate node names in .oradio: {names}")

        return cls(
            name=d.get("oradio") or d.get("name", "untitled"),
            worlds=worlds,
            telemetry=telemetry,
            effectors=effectors,
            bindings=bindings,
            lens=d.get("lens"),
            surfaces=list(d.get("surfaces", [])),
            club=list(d.get("club", [])),
            dipole=DipoleDecl.from_dict(d.get("dipole")),
            raw=dict(d),
        )
