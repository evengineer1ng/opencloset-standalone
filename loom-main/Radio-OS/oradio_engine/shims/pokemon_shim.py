"""Pokémon-via-video-capture — the 'sense + act' flagship, as the same binding machinery.

Three nodes, wired by bindings (declared in the `.oradio`):

  video_capture (eyes) --frame_to_observation--> navigator (brain) --action_to_button--> gamepad (hands)

Perception is VISION (capture card frames), not memory reads — so the eyes are a *transform*
(frame -> observation). Here the capture source is simulated (scripted scenes) and perception is
a pass-through; the real rig swaps in the capture card + an LLM-as-eyes perception transform, and
the navigator + gamepad are unchanged. The thesis under test: demote the LLM to eyes and let the
deterministic navigator do the playing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from oradio_engine.contract import (
    Determinism,
    NormalizedCandidate,
    OrganIdentity,
    TickDelta,
    normalize_event,
)
from oradio_engine.live import LiveFeedOrgan


# A toy deterministic navigation policy: perceived scene -> next button. The real navigator is a
# proper world-model; this proves the brain seam (it plans from perceived state, not from frames).
_DEFAULT_POLICY = {
    "starter_house": "up",
    "hallway": "up",
    "bedroom_door": "A",
    "front_door": "A",
    "outside": "right",
    "tall_grass": "A",
}


class NavigatorWorld:
    """The 'brain': consumes perceived game-state, emits the next action. Deterministic."""

    def __init__(self, name: str, *, seed: int = 0, policy: Optional[Dict[str, str]] = None) -> None:
        self._name = name
        self._seed = seed
        self._policy = dict(policy or _DEFAULT_POLICY)
        self._belief: Dict[str, Any] = {}
        self._tick = 0

    def identity(self) -> OrganIdentity:
        return OrganIdentity(name=self._name, determinism=Determinism.DETERMINISTIC, seed=self._seed)

    def apply_input(self, event: Dict[str, Any]) -> None:
        # a perceived observation from the eyes
        self._belief.update(event)

    def advance(self, to_tick: int) -> TickDelta:
        frm = self._tick
        self._tick = to_tick
        scene = self._belief.get("scene")
        button = self._policy.get(scene) if scene else None
        events: List[Dict[str, Any]] = []
        if button:
            events.append({
                "title": button,
                "body": f"navigate {scene} -> press {button}",
                "type": "action",
                "priority": 0.7,
                "tags": ["navigator", str(scene), button],
                "ts": float(to_tick),
            })
        return TickDelta(from_tick=frm, to_tick=to_tick, events=events, predictions=[], heat=0.0,
                         headline=f"{self._name} @ {scene}")

    def observe(self, delta: TickDelta) -> List[NormalizedCandidate]:
        return [normalize_event(self._name, delta.to_tick, i, e) for i, e in enumerate(delta.events)]

    def read_truth(self) -> Dict[str, Any]:
        return {"tick": self._tick, "scene": self._belief.get("scene"), "belief": dict(self._belief)}


class SimulatedCaptureSource:
    """The 'eyes' (simulated): scripted scenes stand in for capture-card frames + perception."""

    def __init__(self, frames: List[Dict[str, Any]]) -> None:
        self._frames = list(frames)
        self._i = 0

    def poll(self) -> List[Dict[str, Any]]:
        if not self._frames:
            return []
        frame = self._frames[min(self._i, len(self._frames) - 1)]
        self._i += 1
        scene = frame.get("scene", "frame")
        return [{
            "title": scene,
            "body": frame.get("desc", f"frame: {scene}"),
            "type": "frame",
            "priority": 0.4,
            "tags": ["capture", scene],
        }]


def make_navigator(name: str = "navigator", *, seed: int = 0, policy: Optional[Dict[str, str]] = None) -> NavigatorWorld:
    return NavigatorWorld(name, seed=seed, policy=policy)


def make_simulated_capture(name: str = "capture", *, frames: Optional[List[Dict[str, Any]]] = None) -> LiveFeedOrgan:
    default = [{"scene": "starter_house"}, {"scene": "hallway"}, {"scene": "front_door"}, {"scene": "outside"}]
    return LiveFeedOrgan(name, source=SimulatedCaptureSource(frames or default))
