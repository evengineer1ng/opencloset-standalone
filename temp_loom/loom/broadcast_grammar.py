"""Broadcast Grammar: runtime-side transition detection for Radio OS.

The runtime decides when a transition is needed and why. A station meta-profile
decides how that transition should feel. The LLM, when present, only words the
already-structured request.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List, Optional


TRANSITION_REASONS = {
    "topic_shift",
    "signal_priority_shift",
    "segment_change",
    "heat_change",
    "story_completion",
    "story_return",
}

STYLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "mission_control": {
        "label": "Mission Control",
        "preferred_transitions": {
            "topic_shift": ["system_handoff", "channel_switch"],
            "signal_priority_shift": ["priority_override"],
            "segment_change": ["operational_update"],
            "heat_change": ["heat_rebalance"],
            "story_completion": ["status_closeout"],
            "story_return": ["return_to_channel"],
        },
        "phrases": {
            "topic_shift": "Switching channels.",
            "signal_priority_shift": "Priority override.",
            "segment_change": "Changing operating mode.",
            "heat_change": "Signal heat has shifted.",
            "story_completion": "Status closeout.",
            "story_return": "Returning to a previous channel.",
        },
    },
    "news_desk": {
        "label": "News Desk",
        "preferred_transitions": {
            "topic_shift": ["turning_now_to", "elsewhere_today"],
            "signal_priority_shift": ["breaking_update"],
            "segment_change": ["desk_handoff"],
            "heat_change": ["developing_story"],
            "story_completion": ["story_wrap"],
            "story_return": ["back_to_earlier_story"],
        },
        "phrases": {
            "topic_shift": "Turning now.",
            "signal_priority_shift": "A higher-priority update is developing.",
            "segment_change": "We shift from one block to the next.",
            "heat_change": "The center of gravity has moved.",
            "story_completion": "That story has reached a stopping point.",
            "story_return": "Returning to an earlier thread.",
        },
    },
    "sports_broadcast": {
        "label": "Sports Broadcast",
        "preferred_transitions": {
            "topic_shift": ["meanwhile", "around_the_league"],
            "signal_priority_shift": ["urgent_play"],
            "segment_change": ["back_to_the_booth"],
            "heat_change": ["momentum_shift"],
            "story_completion": ["final_whistle"],
            "story_return": ["back_to_the_action"],
        },
        "phrases": {
            "topic_shift": "Meanwhile.",
            "signal_priority_shift": "We have to jump to the live action.",
            "segment_change": "Let's shift the coverage.",
            "heat_change": "Momentum has changed.",
            "story_completion": "That sequence is wrapped.",
            "story_return": "Back to the action.",
        },
    },
    "casual_podcast": {
        "label": "Casual Podcast",
        "preferred_transitions": {
            "topic_shift": ["by_the_way", "speaking_of_that"],
            "signal_priority_shift": ["quick_interrupt"],
            "segment_change": ["new_thread"],
            "heat_change": ["attention_shift"],
            "story_completion": ["button_that_up"],
            "story_return": ["circle_back"],
        },
        "phrases": {
            "topic_shift": "By the way.",
            "signal_priority_shift": "Quick interrupt.",
            "segment_change": "Let's open a new thread.",
            "heat_change": "The attention has shifted.",
            "story_completion": "Let's button that up.",
            "story_return": "Let's circle back.",
        },
    },
    "hype_bro_radio": {
        "label": "Hype Bro Radio",
        "preferred_transitions": {
            "topic_shift": ["yo_hold_up", "speaking_of_wild"],
            "signal_priority_shift": ["hold_everything"],
            "segment_change": ["new_wave"],
            "heat_change": ["desk_lit_up"],
            "story_completion": ["that_one_landed"],
            "story_return": ["run_it_back"],
        },
        "phrases": {
            "topic_shift": "Yo, hold up.",
            "signal_priority_shift": "Hold everything.",
            "segment_change": "New wave.",
            "heat_change": "The desk just lit up.",
            "story_completion": "That one landed.",
            "story_return": "Run it back.",
        },
    },
}

DEFAULT_BROADCAST_GRAMMAR: Dict[str, Any] = {
    "style": "news_desk",
    "transition_style": "news_desk",
    "interruption_tolerance": 0.65,
    "recap_behavior": "brief",
    "callback_behavior": "return with one sentence of context",
    "segment_pacing": "steady",
    "urgency_handling": "interrupt for high-priority changes",
    "priority_shift_threshold": 22.0,
    "heat_shift_threshold": 0.28,
    "preferred_transitions": {},
    "phrases": {},
    "topic_labels": {},
    "source_topics": {},
}


def _slug(value: Any, fallback: str = "feed") -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    text = re.sub(r"_(feed|source|api|plugin|events?)$", "", text)
    return text or fallback


def humanize_topic(topic: Any) -> str:
    text = _slug(topic, "feed")
    return text.replace("_", " ").strip() or "feed"


def normalize_broadcast_grammar(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    grammar = deepcopy(DEFAULT_BROADCAST_GRAMMAR)
    incoming = raw if isinstance(raw, dict) else {}
    style = str(incoming.get("style") or incoming.get("transition_style") or grammar["style"]).strip().lower()
    style = style if style in STYLE_PRESETS else grammar["style"]
    preset = STYLE_PRESETS[style]
    grammar.update(preset)
    grammar.update({k: v for k, v in incoming.items() if v not in (None, "")})
    grammar["style"] = style
    grammar["transition_style"] = style
    for key in ("preferred_transitions", "phrases", "topic_labels", "source_topics"):
        merged = deepcopy(preset.get(key, {})) if isinstance(preset.get(key), dict) else {}
        if isinstance(incoming.get(key), dict):
            merged.update(incoming[key])
        grammar[key] = merged
    return grammar


def default_broadcast_grammar(style: str = "news_desk") -> Dict[str, Any]:
    return normalize_broadcast_grammar({"style": style})


def signal_snapshot(segment: Dict[str, Any], grammar: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    grammar = normalize_broadcast_grammar(grammar)
    source = _slug(segment.get("source") or "feed")
    topic_raw = (
        segment.get("topic")
        or segment.get("topic_key")
        or grammar.get("source_topics", {}).get(source)
        or source
    )
    topic = _slug(topic_raw, source)
    event_type = _slug(segment.get("event_type") or segment.get("type") or "post", "post")
    segment_kind = _slug(segment.get("segment") or segment.get("segment_type") or segment.get("block") or event_type, event_type)
    priority = _float(segment.get("priority", segment.get("heur", 50.0)), 50.0)
    heat = _float(segment.get("heat", segment.get("heat_score", priority / 100.0)), priority / 100.0)
    return {
        "topic": topic,
        "topic_label": topic_label(topic, grammar),
        "segment": segment_kind,
        "source": source,
        "event_type": event_type,
        "priority": priority,
        "heat": heat,
        "title": str(segment.get("title") or "").strip(),
    }


def topic_label(topic: str, grammar: Dict[str, Any]) -> str:
    labels = grammar.get("topic_labels", {}) if isinstance(grammar.get("topic_labels"), dict) else {}
    return str(labels.get(topic) or humanize_topic(topic))


def classify_transition(
    previous: Optional[Dict[str, Any]],
    current: Dict[str, Any],
    grammar: Optional[Dict[str, Any]] = None,
    recent_topics: Optional[List[str]] = None,
) -> Optional[str]:
    if not previous:
        return None
    grammar = normalize_broadcast_grammar(grammar)
    recent_topics = recent_topics or []
    priority_delta = current["priority"] - _float(previous.get("priority"), 50.0)
    heat_delta = current["heat"] - _float(previous.get("heat"), current["priority"] / 100.0)
    event_text = f"{current.get('event_type', '')} {current.get('title', '')}".lower()

    if any(word in event_text for word in ("complete", "completed", "finished", "done", "resolved")):
        return "story_completion"
    if (
        current["topic"] != previous.get("topic")
        and current["topic"] in recent_topics[:-1]
    ):
        return "story_return"
    if priority_delta >= _float(grammar.get("priority_shift_threshold"), 22.0):
        return "signal_priority_shift"
    if heat_delta >= _float(grammar.get("heat_shift_threshold"), 0.28):
        return "heat_change"
    if current["topic"] != previous.get("topic"):
        return "topic_shift"
    if current["segment"] != previous.get("segment"):
        return "segment_change"
    return None


def transition_request_for_segment(
    segment: Dict[str, Any],
    mem: Dict[str, Any],
    grammar: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    grammar = normalize_broadcast_grammar(grammar)
    state = mem.get("_broadcast_grammar_state", {}) if isinstance(mem.get("_broadcast_grammar_state"), dict) else {}
    previous = state.get("last_signal") if isinstance(state.get("last_signal"), dict) else None
    recent_topics = state.get("recent_topics") if isinstance(state.get("recent_topics"), list) else []
    current = signal_snapshot(segment, grammar)
    reason = classify_transition(previous, current, grammar, recent_topics)
    if not reason:
        return None
    mode = transition_mode(reason, grammar)
    priority_delta = current["priority"] - _float(previous.get("priority"), 50.0) if previous else 0.0
    heat_delta = current["heat"] - _float(previous.get("heat"), current["heat"]) if previous else 0.0
    return {
        "type": "transition",
        "transition_reason": reason,
        "transition_mode": mode,
        "from_topic": previous.get("topic") if previous else "",
        "from_topic_label": previous.get("topic_label") if previous else "",
        "to_topic": current["topic"],
        "to_topic_label": current["topic_label"],
        "from_segment": previous.get("segment") if previous else "",
        "to_segment": current["segment"],
        "heat_delta": round(heat_delta, 3),
        "priority_delta": round(priority_delta, 3),
        "style": grammar.get("style", "news_desk"),
        "interruption": reason in {"signal_priority_shift", "heat_change"},
    }


def record_broadcast_signal(
    segment: Dict[str, Any],
    mem: Dict[str, Any],
    grammar: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    grammar = normalize_broadcast_grammar(grammar)
    current = signal_snapshot(segment, grammar)
    state = mem.setdefault("_broadcast_grammar_state", {})
    recent = state.get("recent_topics") if isinstance(state.get("recent_topics"), list) else []
    recent.append(current["topic"])
    state["recent_topics"] = recent[-12:]
    state["last_signal"] = current
    return current


def transition_mode(reason: str, grammar: Dict[str, Any]) -> str:
    preferred = grammar.get("preferred_transitions", {})
    modes = preferred.get(reason) if isinstance(preferred, dict) else None
    if isinstance(modes, list) and modes:
        return str(modes[0])
    if isinstance(modes, str) and modes:
        return modes
    return reason


def format_transition_line(request: Optional[Dict[str, Any]], grammar: Optional[Dict[str, Any]] = None) -> str:
    if not request:
        return ""
    grammar = normalize_broadcast_grammar(grammar)
    reason = request.get("transition_reason", "topic_shift")
    phrases = grammar.get("phrases", {}) if isinstance(grammar.get("phrases"), dict) else {}
    phrase = str(phrases.get(reason) or "Turning now.")
    from_topic = str(request.get("from_topic_label") or request.get("from_topic") or "the last thread")
    to_topic = str(request.get("to_topic_label") or request.get("to_topic") or "the next thread")
    if reason == "signal_priority_shift":
        return f"{phrase} {to_topic} is taking priority over {from_topic}."
    if reason == "heat_change":
        return f"{phrase} {to_topic} is now the hotter signal."
    if reason == "segment_change":
        return f"{phrase} Moving from {request.get('from_segment', 'that block')} to {request.get('to_segment', 'the next block')}."
    if reason == "story_completion":
        return f"{phrase} We can move from {from_topic} to {to_topic}."
    if reason == "story_return":
        return f"{phrase} Back from {from_topic} to {to_topic}."
    return f"{phrase} From {from_topic} to {to_topic}."


def transition_prompt_block(request: Optional[Dict[str, Any]], grammar: Optional[Dict[str, Any]] = None) -> str:
    if not request:
        return "No transition request. Start directly with the current item."
    grammar = normalize_broadcast_grammar(grammar)
    return (
        "TRANSITION REQUEST (runtime decided; do not second-guess it):\n"
        f"- reason: {request.get('transition_reason')}\n"
        f"- mode: {request.get('transition_mode')}\n"
        f"- from: {request.get('from_topic_label')} / {request.get('from_segment')}\n"
        f"- to: {request.get('to_topic_label')} / {request.get('to_segment')}\n"
        f"- heat_delta: {request.get('heat_delta')}\n"
        f"- priority_delta: {request.get('priority_delta')}\n"
        f"- station transition style: {grammar.get('label') or grammar.get('style')}\n"
        "The first spoken line must perform this transition in the station's style."
    )


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except Exception:
        return fallback
