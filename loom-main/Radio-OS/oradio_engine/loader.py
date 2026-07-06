"""The loader — decodes an `.oradio` descriptor into a running federation.

This is where "it's all `.oradios`" becomes true in code: a tiny declaration in, a wired
``FederationEngine`` out. Worlds and telemetry sources are resolved by kind from the registry;
the declared lens is applied over every organ's native projection via ``LensedOrgan``; the
evidence service is attached so any world's predictions are graded. No per-domain Python.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union

from oradio_engine.binding import Binding, build_effector, build_transform
from oradio_engine.club import Club, ClubReport
from oradio_engine.descriptor import OradioDescriptor
from oradio_engine.evidence import EvidenceService
from oradio_engine.federation import Clock, FederationEngine
from oradio_engine.lens import LensedOrgan, build_lens
from oradio_engine.registry import build_organ, build_source


def load_oradio(spec: Union[OradioDescriptor, Dict[str, Any]]) -> FederationEngine:
    """Decode a descriptor (or raw dict) into a runnable ``FederationEngine``."""
    desc = spec if isinstance(spec, OradioDescriptor) else OradioDescriptor.from_dict(spec)
    lens = build_lens(desc.lens)
    eng = FederationEngine(clock=Clock(), evidence=EvidenceService())

    for w in desc.worlds:
        organ = build_organ(w.organ, w.name, **w.params)
        eng.register(LensedOrgan(organ, lens))

    for t in desc.telemetry:
        source = build_source(t.source, t.name, **t.params)
        eng.register(LensedOrgan(source, lens))

    for e in desc.effectors:
        effector = build_effector(e.kind, e.name, **e.params)
        eng.register(LensedOrgan(effector, lens))

    for b in desc.bindings:
        transform = build_transform(b.transform, **b.params)
        eng.bindings.append(Binding(source=b.source, target=b.target, transform=transform, name=b.name))

    return eng


def _read_descriptor_file(path: str) -> dict:
    import json

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        return json.loads(text)


def load_oradio_file(path: str) -> FederationEngine:
    """Load an `.oradio` descriptor from a YAML or JSON file."""
    return load_oradio(_read_descriptor_file(path))


@dataclass
class OpenResult:
    """The MK1 power-on result: a resolved, runnable `.oradio`."""

    name: str
    ok: bool
    engine: Optional[FederationEngine]
    report: ClubReport
    descriptor: OradioDescriptor
    manifest: list = None          # what the station advertised it would consume
    withheld: list = None          # sensitive endpoints withheld for lack of consent


def open_oradio(spec, *, club: Optional[Club] = None, gate: bool = True,
                allow_sensitive: bool = False) -> OpenResult:
    """The full lifecycle: parse → ADVERTISE telemetry → consent-gate → Club resolves → build.

    The responsible handshake: a sensitive telemetry endpoint (your PC, ring, motion…) is
    **withheld unless consented** — the station still runs, just without that feed (degraded, not
    errored). ``allow_sensitive=True`` grants + remembers consent for this open (the "yes, go
    ahead" the UI collects). Benign/simulated sources never gate. Inspect a shared `.oradio` first
    with ``Club.telemetry_manifest(desc)`` — nothing is touched before it's advertised.
    """
    import dataclasses

    if isinstance(spec, str) and (spec.endswith(".oradio") or os.path.exists(spec)):
        spec = _read_descriptor_file(spec)
    desc = spec if isinstance(spec, OradioDescriptor) else OradioDescriptor.from_dict(spec)
    club = club or Club()

    # 1) advertise, then consent-gate the telemetry
    manifest = club.telemetry_manifest(desc)
    withheld = []
    allowed_names = set()
    for req in manifest:
        if req.consented or allow_sensitive:
            allowed_names.add(req.name)
            if allow_sensitive and req.sensitive and not req.consented:
                club.grant_consent(req.kind)   # explicit "yes" → remember, ask once
        else:
            withheld.append(req)               # withhold; run degraded, never error
    dropped = {req.name for req in withheld}
    desc = dataclasses.replace(
        desc,
        telemetry=[t for t in desc.telemetry if t.name in allowed_names],
        bindings=[b for b in desc.bindings if b.source not in dropped],
    )

    # 2) resolve the rest of the dependencies (theme/voices/llm…)
    report = club.resolve(desc)
    if gate and not report.ready:
        return OpenResult(desc.name, False, None, report, desc, manifest, withheld)

    engine = load_oradio(desc)
    return OpenResult(desc.name, True, engine, report, desc, manifest, withheld)
