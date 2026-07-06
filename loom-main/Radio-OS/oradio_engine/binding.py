"""The binding layer — declared, bidirectional routing between nodes.

A binding routes a source's observations into a target's ``apply_input`` via a declared
transform. This is the generalization of cross-organ ripple, and it makes telemetry actually
*drive* worlds (not just share a bus). It is bidirectional by construction:

  - inbound  : telemetry  -> world      (presence -> a world move; a captured frame -> a
               perceived game-state)
  - outbound : world      -> effector   (a world's action -> a gamepad button; -> a voice line)

Both the spatial house and "an `.oradio` plays Pokémon Scarlet via video capture" are the same
shape: observations drive a world that acts back into the space. The LLM-as-eyes thesis is just
a choice of transform — perception (frame -> observation) is a transform; the world is the brain;
the effector is the hands. Swap the transform/effector for real adapters (capture card, gamepad
injection) and nothing else changes.

Transforms and effectors are declared by kind, so the routing lives in the `.oradio`, not Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from oradio_engine.contract import (
    Determinism,
    NormalizedCandidate,
    OrganIdentity,
    TickDelta,
    normalize_event,
)

# A transform turns a source candidate into an apply_input event for the target (or None).
Transform = Callable[[NormalizedCandidate], Optional[Dict[str, Any]]]


@dataclass
class Binding:
    """A resolved route: candidates from ``source`` -> ``target.apply_input`` via ``transform``."""

    source: str
    target: str
    transform: Transform
    name: str = ""


# --------------------------------------------------------------------------- #
# Transform registry (declared by kind)
# --------------------------------------------------------------------------- #
BIND_TRANSFORMS: Dict[str, Callable[..., Transform]] = {}


def register_transform(kind: str, factory: Callable[..., Transform]) -> None:
    BIND_TRANSFORMS[kind] = factory


def build_transform(kind: str, **params: Any) -> Transform:
    if kind not in BIND_TRANSFORMS:
        raise KeyError(f"unknown bind transform {kind!r}; known: {sorted(BIND_TRANSFORMS)}")
    return BIND_TRANSFORMS[kind](**params)


def _presence_to_signal(**_) -> Transform:
    # IRL spatial: 'you are at <node>' becomes a world signal (e.g. an Oracle reacts to presence).
    return lambda c: (
        {"intent": "presence", "node": c.title, "magnitude": c.priority}
        if c.type == "presence" else None
    )


def _frame_to_observation(scene_from: str = "title", **_) -> Transform:
    # Virtual (video capture): a captured frame becomes a perceived game-state observation.
    # This is the LLM-as-eyes seam — swap for an `llm_perception` transform for real frames.
    def t(c: NormalizedCandidate) -> Optional[Dict[str, Any]]:
        if c.type != "frame":
            return None
        scene = c.title if scene_from == "title" else (c.tags[-1] if c.tags else None)
        return {"observation": c.body, "scene": scene}
    return t


def _presence_to_speech(**_) -> Transform:
    # IRL spatial: entering a room makes the house speak (outbound, to a voice effector).
    return lambda c: {"text": f"Someone just entered the {c.title}."} if c.type == "presence" else None


def _action_to_button(**_) -> Transform:
    # Outbound: a world's chosen action becomes a gamepad press.
    return lambda c: {"button": c.title} if c.type == "action" else None


register_transform("presence_to_signal", _presence_to_signal)
register_transform("presence_to_speech", _presence_to_speech)
register_transform("frame_to_observation", _frame_to_observation)
register_transform("action_to_button", _action_to_button)


# --------------------------------------------------------------------------- #
# Effectors — surfaces that ACT on the space (gamepad, voice). Targets, not worlds.
# --------------------------------------------------------------------------- #
class Effector:
    """A ``SimulationOrgan`` that only consumes ``apply_input`` and acts. Records every input
    (so the actuation is auditable/replayable) and surfaces a confirmation candidate next tick."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.received: List[Dict[str, Any]] = []
        self._pending: List[Dict[str, Any]] = []

    def identity(self) -> OrganIdentity:
        return OrganIdentity(name=self._name, determinism=Determinism.LIVE, seed=None)

    def advance(self, to_tick: int) -> TickDelta:
        events = self._pending
        self._pending = []
        return TickDelta(from_tick=to_tick - 1, to_tick=to_tick, events=events, predictions=[], heat=0.0,
                         headline=f"{self._name}: {len(self.received)} acts")

    def observe(self, delta: TickDelta) -> List[NormalizedCandidate]:
        return [normalize_event(self._name, delta.to_tick, i, e) for i, e in enumerate(delta.events)]

    def read_truth(self) -> Dict[str, Any]:
        return {"received": len(self.received), "last": self.received[-1] if self.received else None}

    def apply_input(self, event: Dict[str, Any]) -> None:
        self.received.append(event)
        conf = self._confirm(event)
        if conf is not None:
            self._pending.append(conf)

    def _confirm(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return None


class GamepadEffector(Effector):
    """Records button presses. The real one injects to the Switch via the capture/injection
    harness; here it records, so the eyes->brain->hands loop is provable in-repo."""

    def _confirm(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        btn = event.get("button")
        if btn is None:
            return None
        return {"title": f"press {btn}", "body": f"gamepad press {btn}", "type": "button_press",
                "priority": 0.5, "tags": ["gamepad", str(btn)]}


class VoiceEffector(Effector):
    """Records spoken lines (stand-in for TTS / spatial audio surface)."""

    def _confirm(self, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        text = event.get("text") or event.get("body") or event.get("node")
        if not text:
            return None
        return {"title": "spoke", "body": str(text), "type": "spoken", "priority": 0.4,
                "tags": ["voice"]}


EFFECTOR_KINDS: Dict[str, Callable[..., Any]] = {
    "gamepad": lambda name, **_: GamepadEffector(name),
    "voice": lambda name, **_: VoiceEffector(name),
}


def build_effector(kind: str, name: str, **params: Any) -> Any:
    if kind not in EFFECTOR_KINDS:
        raise KeyError(f"unknown effector kind {kind!r}; known: {sorted(EFFECTOR_KINDS)}")
    return EFFECTOR_KINDS[kind](name, **params)
