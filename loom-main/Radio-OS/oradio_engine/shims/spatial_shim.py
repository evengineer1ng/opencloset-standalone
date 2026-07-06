"""Simulated spatial array — a LIVE presence source with no hardware.

The spatial-house `.oradio` declares ``telemetry: [{source: simulated_spatial_array, ...}]``
and the engine produces presence telemetry emergently — proving "can the Loom + club empower
your `.oradio` to listen to your spatial array" *before* any ESP32 exists. The real array is
the same shape: a LiveSource whose poll() reports which node currently senses you. Swap this
factory for the hardware adapter and nothing else changes.

This source only *senses presence*; turning presence into world movement (the binding
``presence -> location``) is the loader/binding layer, not this source.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from oradio_engine.live import LiveFeedOrgan


class SimulatedSpatialArraySource:
    """Walks a fixed path of nodes, reporting presence at one node per poll."""

    def __init__(self, nodes: List[str], dwell: int = 1) -> None:
        if not nodes:
            raise ValueError("a spatial array needs at least one node")
        self._nodes = list(nodes)
        self._dwell = max(1, dwell)
        self._poll_count = 0

    def poll(self) -> List[Dict[str, Any]]:
        idx = (self._poll_count // self._dwell) % len(self._nodes)
        node = self._nodes[idx]
        self._poll_count += 1
        return [{
            "title": node,
            "body": f"presence sensed at {node}",
            "type": "presence",
            "priority": 0.5,
            "tags": ["spatial", "presence", node],
            "node": node,
        }]


def make_simulated_spatial_array(
    name: str = "spatial",
    *,
    nodes: Optional[List[str]] = None,
    dwell: int = 1,
) -> LiveFeedOrgan:
    source = SimulatedSpatialArraySource(nodes or ["front_door", "living_room", "kitchen"], dwell=dwell)
    return LiveFeedOrgan(name, source=source)
