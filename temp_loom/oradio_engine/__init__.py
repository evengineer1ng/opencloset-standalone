"""oradio_engine — the universal simulation decoder (the ".oradio runtime").

This package is the central engine that lives in the *club*, not inside any `.oradio`.
The `.oradio` artifact is a tiny contract; this engine is the decoder that turns that
contract into a living, narrated, time-scrubbable world — without knowing what is being
simulated (the codec generality; see docs/SIMULATION_ENGINE.md).

Federation model (locked 2026-06-12): organs stay sovereign worlds; the engine gives
them a shared clock + an event bus + the telemetry contract, and narrates them as one.
A *shim* adapts an existing backend (ForkUniverse, Neikos, Oracle, FTB, ATL, MoCo) to
the five-verb :class:`SimulationOrgan` contract.
"""

from oradio_engine.contract import (
    Determinism,
    NormalizedCandidate,
    OrganIdentity,
    SimulationOrgan,
    TickDelta,
    normalize_event,
)
from oradio_engine.club import Club, ClubReport
from oradio_engine.descriptor import OradioDescriptor
from oradio_engine.dipole import DipoleDecl, DipoleMeter, DipoleReading, PoleMatcher
from oradio_engine.evidence import EvidenceService, Prediction, normalize_prediction
from oradio_engine.federation import Clock, FederationEngine
from oradio_engine.index import Address, Index, funnel, gate, lineage
from oradio_engine.lens import Lens, LensedOrgan, build_lens
from oradio_engine.live import IntakeTape, LiveFeedOrgan, LiveSource
from oradio_engine.loader import OpenResult, load_oradio, load_oradio_file, open_oradio
from oradio_engine.visual_index import VisualIndex, visual_seed
from oradio_engine.visual_tape import (
    DEFAULT_VISUAL_FAMILIES,
    VisualTapeEvent,
    VisualTapeLog,
    VisualTapeSnapshot,
    build_visual_snapshot,
    candidate_to_visual_events,
    descriptor_visual_families,
    truth_to_visual_events,
)
# NOTE: visual_thumbnail (PIL rasterization) is intentionally NOT imported here.
# `import oradio_engine` — the .oradio DECODER — must stay importable on stdlib +
# PyYAML alone. A heavy dep in the core forks the file format (you can version the
# software, never everyone's .oradio files). Rasterization is an ENDPOINT job:
# `from oradio_engine.visual_thumbnail import render_visual_frame, ...` when you mean
# to draw pixels. The deterministic visual *params* (visual_tape/visual_index) stay
# pure and exported above.

__all__ = [
    "Determinism",
    "NormalizedCandidate",
    "OrganIdentity",
    "SimulationOrgan",
    "TickDelta",
    "normalize_event",
    "PoleMatcher",
    "DipoleDecl",
    "DipoleReading",
    "DipoleMeter",
    "Clock",
    "FederationEngine",
    "EvidenceService",
    "Prediction",
    "normalize_prediction",
    "IntakeTape",
    "LiveFeedOrgan",
    "LiveSource",
    "Lens",
    "LensedOrgan",
    "build_lens",
    "OradioDescriptor",
    "load_oradio",
    "load_oradio_file",
    "open_oradio",
    "OpenResult",
    "Club",
    "ClubReport",
    "Index",
    "Address",
    "gate",
    "funnel",
    "lineage",
    "VisualIndex",
    "visual_seed",
    "DEFAULT_VISUAL_FAMILIES",
    "VisualTapeEvent",
    "VisualTapeLog",
    "VisualTapeSnapshot",
    "build_visual_snapshot",
    "candidate_to_visual_events",
    "descriptor_visual_families",
    "truth_to_visual_events",
]
