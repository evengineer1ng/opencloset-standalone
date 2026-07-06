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


def _tape_to_speech(grammar: str = "", verbs: str = "", **_) -> Transform:
    """Deterministic narration: read the row's roles (from its tags), speak them in a GRAMMAR.

    The grammar is a small domain-agnostic declaration (data/grammars/*.json) — the *voice*
    (intern/PA/town crier). The domain lives in the rows, never here. Swap the ``grammar`` file
    and the same tape is re-voiced; point any tape at the same grammar and it speaks. No domain
    words. See oradio_engine/speech.py (lazy import keeps the decoder pure)."""
    from oradio_engine.speech import Grammar, roles_from_tags

    voice = Grammar.from_file(grammar, verbs=verbs or None) if grammar else Grammar()
    state: Dict[str, Any] = {"prev": None, "i": 0}

    def t(c: NormalizedCandidate) -> Optional[Dict[str, Any]]:
        roles = roles_from_tags(c.tags)
        if not roles.get("action"):
            return None
        line = voice.line(roles, prev_roles=state["prev"], position=state["i"], key=c.post_id)
        state["prev"] = roles
        state["i"] += 1
        return {"text": line} if line else None

    return t


def _play_to_call(**_) -> Transform:
    # The DETERMINISTIC half: a play row becomes a flat PA call spoken verbatim. The play
    # text already carries no running score, so the call ("Made Three Point Jumper ...")
    # narrates the moment without spelling the result ahead of itself.
    return lambda c: {"text": c.body} if c.type == "play" else None


def _strip_think(text: str) -> str:
    """Remove <think>…</think> reasoning blocks (qwen3/r1 emit them by default) and stray
    open tags, so only the spoken monologue survives."""
    import re
    text = re.sub(r"(?is)<think>.*?</think>", "", text)
    text = re.sub(r"(?is)<think>.*", "", text)        # unterminated (truncated) block
    return text.strip()


def _play_to_mindset(
    intent: str = "",
    endpoint: str = "",
    model: str = "",
    temperature: float = 0.85,
    num_predict: int = 120,
    think: bool = False,
    **_,
) -> Transform:
    """The LIVE half: each play becomes an interior reaction *about* that moment.

    This is the seam where the universe prompt (carried into the descriptor as ``intent``)
    finally does real work and the LLM finally has a job: given the play that just happened
    and the register the author asked for, voice one mind in the arena. The deterministic
    play spine and this generated interiority coexist on one bus — the two determinism
    classes, audible together.

    Talks to an Ollama ``/api/generate`` endpoint. ``num_predict`` keeps interiors short (a
    line or two, fast); ``think=False`` + ``_strip_think`` keep reasoning models' scratchpad
    out of the spoken line. With no ``endpoint`` it degrades to a clearly-marked placeholder
    so the wiring runs end-to-end before an LLM is attached. The HTTP call is lazy stdlib
    (``urllib``) — ``import oradio_engine`` stays pure.
    """

    def _generate(moment: str) -> str:
        if not endpoint:
            return f"(interior — awaiting llm) on: {moment}"
        try:
            import json as _json
            import urllib.request as _urllib
            prompt = (
                f"{intent}\n\nThe moment in the game: {moment}\n\n"
                "Voice ONE short interior reaction from a single mind in the arena — a player, "
                "a coach, or a fan. Their thought, the vibe, NOT what they said aloud. No "
                "preamble, no quotation marks. 1-2 sentences."
            )
            body = {
                "model": model or "llama3.1:8b",
                "prompt": prompt,
                "stream": False,
                "think": think,
                "options": {"temperature": temperature, "num_predict": num_predict},
            }
            payload = _json.dumps(body).encode("utf-8")
            req = _urllib.Request(endpoint, data=payload, headers={"Content-Type": "application/json"})
            resp = _json.load(_urllib.urlopen(req, timeout=60))
            out = _strip_think(str(resp.get("response") or resp.get("text") or ""))
            return out or f"(interior) on: {moment}"
        except Exception as exc:
            return f"(interior — llm unreachable: {type(exc).__name__}) on: {moment}"

    def t(c: NormalizedCandidate) -> Optional[Dict[str, Any]]:
        if c.type != "play":
            return None
        return {"text": _generate(c.body)}

    return t


register_transform("presence_to_signal", _presence_to_signal)
register_transform("presence_to_speech", _presence_to_speech)
register_transform("frame_to_observation", _frame_to_observation)
register_transform("action_to_button", _action_to_button)
register_transform("tape_to_speech", _tape_to_speech)    # deterministic domain-agnostic narration
register_transform("play_to_call", _play_to_call)        # deterministic PA call (verbatim)
register_transform("play_to_mindset", _play_to_mindset)  # live interior monologue (LLM)


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
