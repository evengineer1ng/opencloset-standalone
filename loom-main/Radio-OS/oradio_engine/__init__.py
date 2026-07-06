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
from oradio_engine.evidence import EvidenceService, Prediction, normalize_prediction
from oradio_engine.federation import Clock, FederationEngine
from oradio_engine.index import Address, Index, funnel, gate, lineage
from oradio_engine.lens import Lens, LensedOrgan, build_lens
from oradio_engine.live import IntakeTape, LiveFeedOrgan, LiveSource
from oradio_engine.loader import OpenResult, load_oradio, load_oradio_file, open_oradio

__all__ = [
    "Determinism",
    "NormalizedCandidate",
    "OrganIdentity",
    "SimulationOrgan",
    "TickDelta",
    "normalize_event",
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
]
