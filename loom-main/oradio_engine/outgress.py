"""Outgress — the fill-only renderer that turns a deterministic judge ruling into a
human-readable final answer, and records what synthesis could not supply.

Design contract (the "absolute key"): the LLM may not invent. So the canonical answer is
a *deterministic* fill of an authored template, assembled only from the ruling's fields. An
LLM, if present, is allowed to do exactly one thing — re-phrase that already-grounded text —
and every content word it emits is checked against an allowed vocabulary (the query + the
ruling fields + our own house templates). A rephrase that introduces any new content token is
rejected and the deterministic text ships instead. So "cannot invent" is *verified*, not
hoped: see :func:`ground_check`.

The second job is feedback, not vibes. A template asks for slots; when the ruling has nothing
to fill a slot with, that is a precise, recordable fact about where deterministic synthesis is
weak for this query class. :func:`render` emits structured gap records (starved slots, forced
template downgrades, and the define-query "neighborhood-but-no-address" case) that
:func:`write_gaps` appends to a ledger — a falsifiable backlog of what Atlas should learn next.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# tokenization + grounding
# ---------------------------------------------------------------------------

# Function words are never "facts"; the LLM may use them freely. Everything else it emits has
# to come from the allowed vocabulary or it counts as invention.
_STOP = set(
    "a an and are as at be been being but by for from had has have he her his i in into is it "
    "its of on or our she so that the their them then there these they this those to was we were "
    "what when where which who why will with you your not no nor can cannot do does did done not "
    "than then over under between about above below up down out off only just also more most "
    "such per via given onto if while because".split()
)

_TOKEN_RE = re.compile(r"[^a-z0-9.]+")


def tokenize(text: str) -> List[str]:
    out: List[str] = []
    for raw in _TOKEN_RE.sub(" ", str(text or "").lower()).split():
        tok = raw.strip(".")
        if tok:
            out.append(tok)
    return out


def content_tokens(text: str) -> set:
    return {t for t in tokenize(text) if t not in _STOP and len(t) > 1}


def ground_check(text: str, allowed: set) -> Tuple[bool, List[str]]:
    """Return (ok, invented_tokens). `ok` means every content token came from `allowed`."""
    invented = sorted(content_tokens(text) - allowed)
    return (not invented), invented


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Template:
    id: str
    transform: str          # exact transform, or "*" for any
    status: str             # exact status, or "*" for any
    body: str               # str.format string over the slot names
    required: Tuple[str, ...] = ()   # slots that must be non-empty for this template to apply
    soft: bool = True       # may an LLM re-phrase this rendering?


# The grid is data: filling it toward the full ~50 cells (transform x status x boundary) is
# adding rows here, nothing else. These are the high-value cells plus a satisfiable catch-all
# for every status, so selection can always fall back to something grounded.
TEMPLATES: List[Template] = [
    # ---- define -----------------------------------------------------------
    Template(
        "define.supported",
        "define", "supported",
        "On '{focus}', Atlas admits a settled answer (confidence {confidence}). {admitted} "
        "Citations: {citations}.",
        required=("admitted", "citations"),
    ),
    Template(
        "define.partial",
        "define", "partial",
        "On '{focus}', Atlas can place the material but not yet define it. The closest named "
        "match is {top_title} in the {top_region} region (match {top_score}). {unresolved} "
        "Treat this as a cluster to refine, not a finished definition.",
        required=("top_title", "unresolved"),
    ),
    Template(
        "define.inconclusive",
        "define", "inconclusive",
        "On '{focus}', Atlas finds related material but cannot justify a definition "
        "(confidence {confidence}). The nearest match is {top_title} ({top_region}). "
        "This is a bounded placement only.",
        required=("top_title",),
    ),
    # ---- rank / count / time (factual, address-bearing transforms) --------
    Template(
        "rank.supported",
        "rank", "supported",
        "On '{focus}', Atlas ranks {top_title} first ({top_region}, score {top_score}). "
        "{admitted} Citations: {citations}.",
        required=("top_title", "admitted"),
    ),
    Template(
        "count.supported",
        "count", "supported",
        "On '{focus}', Atlas reports a settled count (confidence {confidence}). {admitted} "
        "Citations: {citations}.",
        required=("admitted",),
    ),
    Template(
        "sequence.supported",
        "sequence", "supported",
        "On '{focus}', Atlas establishes the order (confidence {confidence}). {admitted}",
        required=("admitted",),
    ),
    # time shares the chronology shape with sequence
    Template(
        "time.supported",
        "time", "supported",
        "On '{focus}', Atlas establishes the timing (confidence {confidence}). {admitted}",
        required=("admitted",),
    ),
    # ---- summary / interpretive / causal ---------------------------------
    Template(
        "summary.supported",
        "summary", "supported",
        "On '{focus}', Atlas supports a summary (confidence {confidence}). {admitted} "
        "Related material: {related}. Citations: {citations}.",
        required=("admitted",),
    ),
    Template(
        "summary.partial",
        "summary", "partial",
        "On '{focus}', Atlas gives a partial summary. {admitted} Open points: {unresolved}. "
        "Related material: {related}.",
        required=("unresolved",),
    ),
    Template(
        "causal.partial",
        "causal", "partial",
        "On '{focus}', Atlas can name the neighborhood but not settle the cause. "
        "{unresolved} Related material: {related}.",
        required=("unresolved",),
    ),
    # ---- per-status catch-alls (require only always-present slots) --------
    Template(
        "any.supported",
        "*", "supported",
        "Atlas supports an answer on '{focus}' (confidence {confidence}). {admitted}",
        required=("admitted",),
    ),
    Template(
        "any.supported.bare",
        "*", "supported",
        "Atlas supports a bounded answer on '{focus}' (confidence {confidence}), best "
        "summarized by {top_title} in the {top_region} region.",
        required=("top_title",),
    ),
    Template(
        "any.partial",
        "*", "partial",
        "Atlas gives a partial ruling on '{focus}' (confidence {confidence}). The match is "
        "bounded and not fully settled.",
        required=(),
    ),
    Template(
        "any.inconclusive",
        "*", "inconclusive",
        "Atlas is inconclusive on '{focus}' (confidence {confidence}). It can place the query "
        "near related material but cannot justify a direct answer.",
        required=(),
    ),
    Template(
        "any.unsupported",
        "*", "unsupported",
        "Atlas has no admitted material for '{focus}', so the court declines to answer. "
        "Nothing was retrieved to stand on.",
        required=(),
    ),
    # absolute floor — applies to any status if nothing else matched
    Template(
        "any.any",
        "*", "*",
        "On '{focus}', Atlas returns a {status} ruling at confidence {confidence}; the answer "
        "is bounded to retrieved material only.",
        required=(),
    ),
]

# The "house vocabulary": every content word we authored into templates is trusted style, not
# a fact about the world. The LLM smoother may use these plus anything from the live source.
HOUSE_VOCAB: set = set()
for _t in TEMPLATES:
    HOUSE_VOCAB |= content_tokens(re.sub(r"\{[^}]+\}", " ", _t.body))


# ---------------------------------------------------------------------------
# slot derivation
# ---------------------------------------------------------------------------

# Slots whose emptiness is a *synthesis gap* (atlas had nothing to put here) rather than a
# benign formatting blank.
_GAP_SLOTS = ("top_title", "top_region", "admitted", "related", "unresolved", "citations")


_SLOT_REF_RE = re.compile(r"\{(\w+)\}")


def _referenced_slots(template: Template) -> set:
    return set(_SLOT_REF_RE.findall(template.body)) | set(template.required)


def derive_slots(query: str, ruling: Dict[str, Any]) -> Tuple[Dict[str, str], List[str]]:
    top = ruling.get("top_hit") or {}
    slots = {
        "query": str(query or "").strip(),
        "focus": str(ruling.get("focus") or query or "").strip(),
        "transform": str(ruling.get("transform") or "summary"),
        "status": str(ruling.get("status") or "inconclusive"),
        "confidence": f"{float(ruling.get('confidence') or 0.0):.2f}",
        "top_title": str(top.get("title") or "").strip(),
        "top_region": str(top.get("region") or "").strip(),
        "top_score": f"{float(top.get('score') or 0.0):.3f}",
        "admitted": " ".join(str(s).strip() for s in (ruling.get("admitted_findings") or []) if str(s).strip()),
        "related": ", ".join(str(s).strip() for s in (ruling.get("related_material") or []) if str(s).strip()),
        "unresolved": " ".join(str(s).strip() for s in (ruling.get("unresolved_points") or []) if str(s).strip()),
        "citations": ", ".join(str(s).strip() for s in (ruling.get("citations") or []) if str(s).strip()),
    }
    empties = [k for k in _GAP_SLOTS if not slots.get(k)]
    return slots, empties


# ---------------------------------------------------------------------------
# template selection
# ---------------------------------------------------------------------------


def _specificity(t: Template, transform: str, status: str) -> float:
    score = 0.0
    score += 2.0 if t.transform == transform else (0.0 if t.transform == "*" else -100.0)
    score += 1.0 if t.status == status else (0.0 if t.status == "*" else -100.0)
    score += 0.1 * len(t.required)  # prefer the richest template we can actually fill
    return score


def select_template(transform: str, status: str, available: set) -> Tuple[Template, Template]:
    """Pick the richest template that matches and whose required slots are all available.

    Returns (used, ideal): `ideal` is the most specific template that *matches* regardless of
    whether its slots were fillable; if it differs from `used`, synthesis forced a downgrade.
    """
    matches = [t for t in TEMPLATES if _specificity(t, transform, status) > -1]
    matches.sort(key=lambda t: _specificity(t, transform, status), reverse=True)
    ideal = matches[0]
    for t in matches:
        if all(slot in available for slot in t.required):
            return t, ideal
    # the absolute floor requires nothing, so this is unreachable, but stay total
    return TEMPLATES[-1], ideal


# ---------------------------------------------------------------------------
# result + render
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OutgressResult:
    answer: str
    template_id: str
    ideal_template_id: str
    deterministic: str
    smoothed: bool
    grounding: Dict[str, Any]
    starved_slots: List[str]
    gaps: List[Dict[str, Any]]
    usage: Optional[Dict[str, Any]] = None
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "template_id": self.template_id,
            "ideal_template_id": self.ideal_template_id,
            "deterministic": self.deterministic,
            "smoothed": self.smoothed,
            "grounding": self.grounding,
            "starved_slots": self.starved_slots,
            "gaps": self.gaps,
            "usage": self.usage,
            "error": self.error,
        }


def _source_text(query: str, ruling: Dict[str, Any], slots: Dict[str, str]) -> str:
    parts = [str(query or ""), slots.get("focus", "")]
    parts.extend(slots.get(k, "") for k in _GAP_SLOTS)
    return " ".join(parts)


def _is_generic_concept(ruling: Dict[str, Any]) -> bool:
    obj = str((ruling.get("top_hit") or {}).get("object") or "").strip().lower()
    return obj.endswith("concept")


def _build_gaps(query: str, ruling: Dict[str, Any], starved: List[str],
                used: Template, ideal: Template) -> List[Dict[str, Any]]:
    base = {
        "ts": _now_iso(),
        "query": str(query or "").strip(),
        "focus": str(ruling.get("focus") or query or "").strip(),
        "transform": str(ruling.get("transform") or ""),
        "status": str(ruling.get("status") or ""),
    }
    gaps: List[Dict[str, Any]] = []
    for slot in starved:
        gaps.append({**base, "kind": "starved_slot", "slot": slot,
                     "detail": f"synthesis produced no '{slot}' for this query class"})
    if used.id != ideal.id:
        gaps.append({**base, "kind": "template_downgrade", "ideal_template": ideal.id,
                     "used_template": used.id,
                     "detail": "ideal template could not be filled; fell back to a thinner one"})
    if base["transform"] == "define" and _is_generic_concept(ruling):
        top = ruling.get("top_hit") or {}
        gaps.append({**base, "kind": "missing_address",
                     "region": str(top.get("region") or ""),
                     "detail": "atlas returned a generic concept hit (right neighborhood, no "
                               "within-region definition); needs a definition statement for the focus"})
    return gaps


# A smoother takes (deterministic_text, allowed_vocab_sorted) and returns (text, usage_dict).
Smoother = Callable[[str, List[str]], Tuple[str, Dict[str, Any]]]

_LEAK_RE = re.compile(r"(?is)<think>|thinking process|here'?s? (?:a |my )?thinking")


def render(query: str, ruling: Dict[str, Any], *, smoother: Optional[Smoother] = None) -> OutgressResult:
    slots, empties = derive_slots(query, ruling)
    transform = slots["transform"]
    status = slots["status"]
    available = set(slots) - set(empties)
    used, ideal = select_template(transform, status, available)
    deterministic = re.sub(r"\s+", " ", used.body.format(**slots)).strip()
    # A slot is "starved" only if the *ideal* narrative for this ruling actually needed it but
    # synthesis left it empty — not merely any blank field. (A supported answer with no
    # unresolved points is correct, not a gap.) This forces template downgrades and thin
    # answers, and is the precise, recordable thing synthesis should learn to fill.
    needed = _referenced_slots(ideal) | _referenced_slots(used)
    starved = [k for k in empties if k in needed]
    gaps = _build_gaps(query, ruling, starved, used, ideal)

    answer = deterministic
    smoothed = False
    grounding: Dict[str, Any] = {"ok": True, "invented": [], "checked": False}
    usage: Optional[Dict[str, Any]] = None
    error = ""

    if smoother is not None and used.soft:
        allowed = HOUSE_VOCAB | content_tokens(_source_text(query, ruling, slots))
        try:
            cand_text, usage = smoother(deterministic, sorted(allowed))
        except Exception as exc:  # smoothing is best-effort; never break the deterministic answer
            error = f"smoother_error:{type(exc).__name__}"
            cand_text = ""
        cand = re.sub(r"(?is)<think>.*?</think>", "", str(cand_text or "")).strip()
        ok, invented = ground_check(cand, allowed)
        leaked = bool(_LEAK_RE.search(cand))
        grounding = {"ok": ok, "invented": invented, "checked": True, "leaked": leaked}
        if cand and ok and not leaked:
            answer = cand
            smoothed = True
        elif cand and not ok:
            error = error or "smoother_invented_tokens"
        elif leaked:
            error = error or "smoother_leaked_reasoning"

    return OutgressResult(
        answer=answer,
        template_id=used.id,
        ideal_template_id=ideal.id,
        deterministic=deterministic,
        smoothed=smoothed,
        grounding=grounding,
        starved_slots=starved,
        gaps=gaps,
        usage=usage,
        error=error,
    )


# ---------------------------------------------------------------------------
# gap ledger
# ---------------------------------------------------------------------------


def write_gaps(path: Path, gaps: Sequence[Dict[str, Any]]) -> None:
    if not gaps:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for gap in gaps:
            handle.write(json.dumps(gap, ensure_ascii=False) + "\n")


def summarize_gaps(path: Path) -> Dict[str, Any]:
    """Roll the ledger up into a prioritized backlog: which gap kinds, slots, and query
    classes recur most. This is the 'what to improve next' view over the feedback."""
    path = Path(path)
    if not path.exists():
        return {"total": 0, "by_kind": {}, "by_slot": {}, "by_transform": {}, "missing_address_focus": {}}
    by_kind: Dict[str, int] = {}
    by_slot: Dict[str, int] = {}
    by_transform: Dict[str, int] = {}
    missing_focus: Dict[str, int] = {}
    total = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        total += 1
        kind = str(rec.get("kind") or "")
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if rec.get("slot"):
            by_slot[rec["slot"]] = by_slot.get(rec["slot"], 0) + 1
        if rec.get("transform"):
            by_transform[rec["transform"]] = by_transform.get(rec["transform"], 0) + 1
        if kind == "missing_address" and rec.get("focus"):
            missing_focus[rec["focus"]] = missing_focus.get(rec["focus"], 0) + 1

    def _top(d: Dict[str, int]) -> Dict[str, int]:
        return dict(sorted(d.items(), key=lambda kv: kv[1], reverse=True))

    return {
        "total": total,
        "by_kind": _top(by_kind),
        "by_slot": _top(by_slot),
        "by_transform": _top(by_transform),
        "missing_address_focus": _top(missing_focus),
    }
