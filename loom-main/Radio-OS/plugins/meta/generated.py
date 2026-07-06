"""
Generated Meta Plugin — the second half of the Loop: meaning.

Authorship as a *spec*, not hand-written Python. The antenna profiles a foreign system; the
generator (generate_meta_plugin_spec) drafts a `meta_plugin_spec.json` — what each source means,
who the actors are, the lens/headline, the tone — and this plugin READS that spec to narrate.

Quality lives in the spec, and the spec is a plain editable JSON the author refines while the
simulator plays back. Narration works deterministically from the spec (templates) so the loop is
verifiable with no LLM installed; when the runtime provides an LLM it is used for richer commentary.

Selected by a station manifest with `meta_plugin: generated`.
"""
from typing import Any, Dict, List, Optional, Tuple
import importlib.util
import json
import os

try:
    from bookmark import MetaPluginBase
except Exception:  # standalone / test
    from abc import ABC, abstractmethod

    class MetaPluginBase(ABC):  # minimal stub mirroring the real contract
        @abstractmethod
        def initialize(self, runtime_context, cfg, mem): ...
        @abstractmethod
        def shutdown(self): ...
        def curate_candidates(self, candidates, state): return []
        def generate_script(self, segment, state): return None
        def generate_narration(self, events, context): return []
        def delegate_decision(self, available_actions, state, identity, focus): return None


try:
    from broadcast_grammar import (
        default_broadcast_grammar,
        format_transition_line,
        normalize_broadcast_grammar,
        record_broadcast_signal,
        transition_prompt_block,
        transition_request_for_segment,
    )
except Exception:
    helper_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "broadcast_grammar.py"))
    helper_spec = importlib.util.spec_from_file_location("broadcast_grammar", helper_path)
    if helper_spec is None or helper_spec.loader is None:
        raise
    helper_mod = importlib.util.module_from_spec(helper_spec)
    helper_spec.loader.exec_module(helper_mod)
    default_broadcast_grammar = helper_mod.default_broadcast_grammar
    format_transition_line = helper_mod.format_transition_line
    normalize_broadcast_grammar = helper_mod.normalize_broadcast_grammar
    record_broadcast_signal = helper_mod.record_broadcast_signal
    transition_prompt_block = helper_mod.transition_prompt_block
    transition_request_for_segment = helper_mod.transition_request_for_segment


try:
    import signal_heat
except Exception:
    heat_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "signal_heat.py"))
    heat_spec = importlib.util.spec_from_file_location("signal_heat", heat_path)
    if heat_spec is None or heat_spec.loader is None:
        raise
    signal_heat = importlib.util.module_from_spec(heat_spec)
    heat_spec.loader.exec_module(signal_heat)


# =====================================================================
# THE GENERATOR — signature → authorship spec (loud and proud, editable)
# =====================================================================
def _humanize(source: str) -> str:
    return source.replace("api_", "").replace("_", " ").strip().title() or source


def generate_meta_plugin_spec(signature: Dict[str, Any], station_name: str = "Station",
                              voices: Optional[List[str]] = None) -> Dict[str, Any]:
    """Draft the authorship spec from the antenna's Signature Profile. Deterministic + transparent:
    it states, per source, which fields it will read and the lens/headline it will narrate through.
    The author then edits this JSON to taste; the simulator plays back the result immediately."""
    voices = voices or ["host"]
    sources: Dict[str, Any] = {}
    notes: List[str] = []
    for source, prof in (signature.get("endpoints") or {}).items():
        if not prof.get("ok"):
            notes.append(f"{source}: skipped ({prof.get('reason')}) — not narratable.")
            continue
        fm = prof.get("field_map", {})
        label = _humanize(source)
        sources[source] = {
            "label": label,
            "lens": f"an update from {label}",
            "headline": "{title}",
            "fields": {k: fm.get(k) for k in ("title", "body", "actor", "priority", "id", "ts")},
            "min_priority": 0,
            # Signal heat: how loud this world is, when it may interrupt, and how fast it cools.
            # Airtime is emergent — a source that just lit up earns it; a quiet one recedes.
            "heat": signal_heat.default_source_heat(),
        }
        notes.append(f"{source}: {prof.get('count', 0)} item(s); reading title='{fm.get('title')}', "
                     f"body='{fm.get('body')}', actor='{fm.get('actor')}'.")
    return {
        "station": station_name,
        "tone": (f"You are the live radio host of {station_name}, narrating a living system so a "
                 "listener understands what is happening and why. Conversational, concise, never read raw data."),
        "voices": voices,
        "broadcast_grammar": {
            **default_broadcast_grammar("news_desk"),
            "_editable": (
                "Station identity for how the show moves: transition style, interruption tolerance, "
                "callbacks, recaps, segment pacing, urgency handling, source_topics, and topic_labels."
            ),
        },
        "sources": sources,
        "signal_heat": {
            **signal_heat.default_heat_config(),
            "_editable": (
                "How airtime is shared as the world changes. Global defaults here; per-source overrides "
                "live under each source's 'heat'. half_life_sec=how fast a world cools, gain=how loud it "
                "is, quiet_floor=how quiet before it goes silent, interrupt_threshold=when it may cut in."
            ),
        },
        "_notes": notes,
        "_editable": "Edit lens/headline/tone and the field choices per source, then re-listen in the simulator.",
    }


# =====================================================================
# THE PLUGIN — reads the spec and narrates
# =====================================================================
class GeneratedMetaPlugin(MetaPluginBase):
    def __init__(self):
        self.context: Dict[str, Any] = {}
        self.cfg: Dict[str, Any] = {}
        self.mem: Dict[str, Any] = {}
        self.spec: Dict[str, Any] = {}
        self.log_func = print

    # ---- lifecycle ----
    def initialize(self, runtime_context: Dict[str, Any], cfg: Dict[str, Any], mem: Dict[str, Any]) -> None:
        self.context = runtime_context or {}
        self.cfg = cfg or {}
        self.mem = mem or {}
        self.log_func = self.context.get("log", print)
        self.spec = self._load_spec()
        n = len(self.spec.get("sources", {}))
        self._log("meta", f"GeneratedMetaPlugin initialized · station='{self.spec.get('station')}' · {n} source(s) · "
                          f"narration={'llm' if self._has_llm() else 'template'}")

    def shutdown(self) -> None:
        self._log("meta", "GeneratedMetaPlugin shutting down")

    def _log(self, ch: str, msg: str) -> None:
        try:
            self.log_func(ch, msg)
        except Exception:
            pass

    def _has_llm(self) -> bool:
        return callable(self.context.get("llm_generate"))

    def _load_spec(self) -> Dict[str, Any]:
        # explicit path in manifest, else STATION_DIR/meta_plugin_spec.json
        path = (self.cfg.get("meta_plugin_spec") or "").strip()
        if not path:
            sdir = os.environ.get("STATION_DIR") or "."
            path = os.path.join(sdir, "meta_plugin_spec.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            self._log("meta", f"no meta_plugin_spec.json ({e}); narrating with defaults")
            return {"station": self.cfg.get("station", {}).get("name", "Station"), "sources": {}, "voices": ["host"]}

    def _source_spec(self, source: str) -> Dict[str, Any]:
        return (self.spec.get("sources", {}) or {}).get(source, {})

    def _lead_voice(self, segment: Dict[str, Any]) -> str:
        roles = self.context.get("LIVE_ROLES")
        if isinstance(roles, dict):
            roles = list(roles.keys())
        if isinstance(roles, list) and roles:
            return roles[0]
        voices = self.spec.get("voices") or ["host"]
        return voices[0]

    def _broadcast_grammar(self) -> Dict[str, Any]:
        return normalize_broadcast_grammar(self.spec.get("broadcast_grammar", {}))

    # ---- curate (signal heat: rank by time-decayed source activity, attach the source lens) ----
    def curate_candidates(self, candidates: List[Dict[str, Any]], mem: Dict[str, Any]) -> List[Dict[str, Any]]:
        fresh = [c for c in candidates if isinstance(c, dict)]
        cfg = signal_heat.normalize_heat_config(self.spec.get("signal_heat", {}), self.spec.get("sources", {}))
        state = mem.setdefault("_signal_heat_state", {}) if isinstance(mem, dict) else {}
        now = signal_heat.now_ts()
        # Fold each genuinely-new observation into its source's heat exactly once (priority hint →
        # decaying quantity), then decay everything to now. Silence is a valid state.
        seen = state.setdefault("seen", []) if isinstance(state, dict) else []
        signal_heat.decay_heat(state, now, cfg)
        for c in fresh:
            pid = c.get("post_id") or c.get("id") or id(c)
            if pid in seen:
                continue
            signal_heat.bump_heat(state, c, cfg, now)
            seen.append(pid)
        del seen[:-256]
        if not fresh or signal_heat.is_silent(state, cfg, now):
            return []
        # Rank by blended priority + live source heat; quiet sources recede; survivors carry heat/interrupt.
        ranked = signal_heat.rank_candidates(fresh, state, cfg, now)
        out: List[Dict[str, Any]] = []
        for c in ranked[:8]:
            src = c.get("source", "feed")
            sspec = self._source_spec(src)
            if float(c.get("heur", c.get("priority", 50)) or 50) < float(sspec.get("min_priority", 0) or 0):
                continue
            merged = dict(c)
            merged.setdefault("post_id", c.get("post_id") or c.get("id"))
            merged["angle"] = sspec.get("lens", f"an update from {src}")
            merged["why"] = ""
            merged["key_points"] = []
            merged["lead_voice"] = self._lead_voice(c)
            merged["move"] = "explore"
            out.append(merged)
        return out

    # ---- produce one segment's spoken packet ----
    def generate_script(self, segment: Dict[str, Any], mem: Dict[str, Any]) -> Dict[str, Any]:
        grammar = self._broadcast_grammar()
        transition = transition_request_for_segment(segment, mem, grammar)
        segment = dict(segment)
        if transition:
            segment["transition_request"] = transition
        if self._has_llm():
            pkt = self._llm_packet(segment, grammar=grammar, transition=transition)
            if pkt:
                record_broadcast_signal(segment, mem, grammar)
                return pkt
        pkt = self._template_packet(segment, grammar=grammar, transition=transition)
        record_broadcast_signal(segment, mem, grammar)
        return pkt

    def _template_packet(
        self,
        segment: Dict[str, Any],
        *,
        grammar: Optional[Dict[str, Any]] = None,
        transition: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        grammar = grammar or self._broadcast_grammar()
        src = segment.get("source", "feed")
        sspec = self._source_spec(src)
        title = str(segment.get("title", "")).strip()
        body = str(segment.get("body", "")).strip()
        lens = sspec.get("lens", "an update")
        # one-to-two sentence radio-style intro + body trimmed to a couple sentences
        intro = f"Here's {lens}: {title}." if title else f"Here's {lens}."
        transition_line = format_transition_line(transition, grammar)
        if transition_line:
            intro = f"{transition_line} {intro}"
        summary = body
        if len(summary) > 400:
            cut = summary[:400]
            summary = cut[: cut.rfind(". ") + 1] if ". " in cut else cut + "…"
        return {"host_intro": intro, "summary": summary, "panel": [], "host_takeaway": "", "transition_request": transition or {}}

    def _llm_packet(
        self,
        segment: Dict[str, Any],
        *,
        grammar: Optional[Dict[str, Any]] = None,
        transition: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        grammar = grammar or self._broadcast_grammar()
        src = segment.get("source", "feed")
        sspec = self._source_spec(src)
        system = (self.spec.get("tone", "You are a radio host narrating a live system.")
                  + f"\nThis item is {sspec.get('lens', 'an update')}."
                  + "\n\n" + transition_prompt_block(transition, grammar)
                  + "\nThe runtime decides whether and why a transition is required; obey that request if present."
                  + "\nReturn STRICT JSON: {\"lead_line\":\"...\",\"followup_line\":\"...\","
                    "\"supporting_lines\":[{\"voice\":\"host\",\"line\":\"...\"}],\"takeaway\":\"...\"}"
                  + "\nSpoken words only; insights and commentary, never raw data; do not invent facts.")
        user = (f"TITLE: {segment.get('title','')}\n\nSOURCE MATERIAL:\n{str(segment.get('body',''))[:800]}")
        try:
            raw = self.context["llm_generate"](
                user, system,
                model=(self.context.get("cfg_get", lambda *_: "")("models.host", "") or "gpt-3.5-turbo"),
                num_predict=320, temperature=0.6, timeout=60, force_json=True,
            )
            data = self.context.get("parse_json_lenient", lambda x: None)(raw) if raw else None
            if not isinstance(data, dict) or not (data.get("lead_line") or data.get("followup_line")):
                return None
            return {
                "host_intro": str(data.get("lead_line", "")).strip(),
                "summary": str(data.get("followup_line", "")).strip(),
                "panel": data.get("supporting_lines", []) if isinstance(data.get("supporting_lines"), list) else [],
                "host_takeaway": str(data.get("takeaway", "")).strip(),
                "transition_request": transition or {},
            }
        except Exception as e:  # noqa: BLE001
            self._log("meta", f"llm packet failed ({e}); using template")
            return None
