"""The speech engine — applies a GRAMMAR to role rows. No domain words live here.

Two things stay separate, the way ``.oradio`` (the world) and ``.loom`` (the wish) do:

  - DOMAIN  = the tape's rows (actor/action/object/magnitude/valence). *What happened.*
              F1, heart rate, the market. The domain owns its facts and what's salient.
  - GRAMMAR = *how to say it* — intern, PA, town crier, prime minister. **Domain-agnostic.**
              A small declaration (data/grammars/*.json) an LLM can author in one shot.

One general engine; the same grammar speaks any tape, and swapping the grammar re-voices the
same tape. Verb tense comes from a shared *English* table (general, not domain) with a regular
fallback — never an F1 file. Pure stdlib (json/hashlib).

Roles ride on a candidate's ``tags`` as ``key:value`` (actor/action/object/magnitude/unit/
valence/pronoun/definite), so the frozen NormalizedCandidate contract is untouched.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Sequence

ROLE_KEYS = ("actor", "action", "object", "magnitude", "unit", "valence", "pronoun", "definite", "ordinal")

_ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth",
             6: "sixth", 7: "seventh", 8: "eighth", 9: "ninth", 10: "tenth"}


def ordinal_word(n: int) -> str:
    return _ORDINALS.get(int(n), f"{int(n)}th")

# A general English irregular-past table — shared infrastructure, NOT domain-specific.
# (Extended set lives in data/english/irregular_verbs.json; this is the standalone default.)
_DEFAULT_VERBS: Dict[str, str] = {
    "overtake": "overtook", "make": "made", "rise": "rose", "take": "took", "seize": "seized",
    "get": "got", "run": "ran", "hit": "hit", "set": "set", "put": "put", "pit": "pitted",
    "begin": "began", "build": "built", "fall": "fell", "go": "went", "come": "came",
    "break": "broke", "catch": "caught", "hold": "held", "lead": "led", "lose": "lost", "win": "won",
}

_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def number_to_words(n: int) -> str:
    n = int(n)
    if n < 0:
        return "minus " + number_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        rest = n % 10
        return _TENS[n // 10] + ("-" + _ONES[rest] if rest else "")
    if n < 1000:
        rest = n % 100
        return _ONES[n // 100] + " hundred" + (" " + number_to_words(rest) if rest else "")
    rest = n % 1000
    return number_to_words(n // 1000) + " thousand" + (" " + number_to_words(rest) if rest else "")


def article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def regular_past(verb: str) -> str:
    """Regular-English past for any verb the table doesn't cover — the floor, not the grammar.
    Handles monosyllabic consonant-doubling (drop->dropped, jam->jammed) without over-doubling
    polysyllables (open->opened, whisper->whispered)."""
    import re as _re
    v = (verb or "").strip()
    if not v:
        return v
    if v.endswith("e"):
        return v + "d"
    if len(v) > 2 and v[-1] == "y" and v[-2] not in "aeiou":
        return v[:-1] + "ied"
    # double a final consonant only for a single-vowel-group (monosyllabic) CVC word
    if (len(_re.findall(r"[aeiou]+", v)) == 1 and len(v) >= 3
            and v[-1] not in "aeiouwxy" and v[-2] in "aeiou" and v[-3] not in "aeiou"):
        return v + v[-1] + "ed"
    return v + "ed"


def _pick(pool: Sequence[str], *key: Any) -> str:
    """Deterministic choice from a pool: same key -> same pick (variety that replays exactly)."""
    if not pool:
        return ""
    h = int(hashlib.sha256(":".join(str(k) for k in key).encode("utf-8")).hexdigest()[:8], 16)
    return pool[h % len(pool)]


def roles_from_tags(tags: Sequence[str]) -> Dict[str, str]:
    """Extract role:value pairs from a candidate's tags (the contract-safe role channel)."""
    roles: Dict[str, str] = {}
    for tag in tags or ():
        if isinstance(tag, str) and ":" in tag:
            key, value = tag.split(":", 1)
            if key in ROLE_KEYS:
                roles[key] = value.replace("_", " ")
    return roles


class _Default(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _capitalize_sentences(s: str) -> str:
    out: List[str] = []
    cap = True
    for ch in s:
        if cap and ch.isalpha():
            out.append(ch.upper())
            cap = False
        else:
            out.append(ch)
        if ch in ".!?":
            cap = True
    return "".join(out)


class Grammar:
    """A domain-agnostic speaking style. All flavor is in the spec; none of it is a domain word."""

    def __init__(self, spec: Optional[Dict[str, Any]] = None, verbs: Optional[Dict[str, str]] = None) -> None:
        spec = spec or {}
        self.persona = spec.get("persona", "")
        self.opener = spec.get("opener", "")
        self.transitions = spec.get("transitions") or [""]
        self.codas = spec.get("codas") or {"*": [""]}
        self.article = bool(spec.get("article", True))
        self.tense = spec.get("tense", "past")
        self.form = spec.get("form", "{opener}{transition}{actor} {verb}{object}{magnitude}{coda}")
        self.reasons = spec.get("reasons") or {}   # causal reason-token -> phrasing, in THIS voice
        self.verbs = verbs or _DEFAULT_VERBS

    def reason_phrase(self, token: str) -> str:
        """How this voice phrases a causal reason token (e.g. 'fresh_tyres'). '' if unknown."""
        return self.reasons.get(token, "")

    def _ordinal_suffix(self, roles: Dict[str, str]) -> str:
        """Continuity from carried state: 'for the second time'. General across domains. Skipped
        when the object is a named counterpart (overtook Sainz), where it would read ambiguously."""
        o = roles.get("ordinal")
        obj = roles.get("object", "")
        if not o or (obj and obj[:1].isupper()):
            return ""
        try:
            n = int(o)
        except (TypeError, ValueError):
            return ""
        return f" for the {ordinal_word(n)} time" if n > 1 else ""

    @classmethod
    def from_file(cls, path: str, *, verbs: Optional[str] = None) -> "Grammar":
        with open(path, "r", encoding="utf-8") as f:
            spec = json.load(f)
        table = None
        if verbs:
            with open(verbs, "r", encoding="utf-8") as f:
                table = json.load(f)
        return cls(spec, table)

    def _verb(self, lemma: str) -> str:
        if self.tense == "present":
            return lemma
        return self.verbs.get(lemma) or regular_past(lemma)

    def _object_phrase(self, roles: Dict[str, str]) -> str:
        obj = roles.get("object")
        if not obj:
            return ""
        if roles.get("definite") == "1":
            return " the " + obj
        if obj[:1].isupper():          # proper noun -> no article
            return " " + obj
        if self.article:
            return " " + article(obj) + " " + obj
        return " " + obj

    def line(self, roles: Dict[str, str], *, prev_roles: Optional[Dict[str, str]] = None,
             position: int = 0, key: Any = 0) -> str:
        action = roles.get("action")
        if not action:
            return ""
        actor = roles.get("actor", "")
        if prev_roles and actor and prev_roles.get("actor") == actor and position > 0 and roles.get("pronoun"):
            actor = roles["pronoun"]

        magnitude = ""
        m = roles.get("magnitude")
        if m:
            unit = roles.get("unit", "")
            number = number_to_words(int(m)) if str(m).isdigit() else m
            magnitude = f" to {number}{(' ' + unit) if unit else ''}"

        slots = {
            "opener": _pick([self.opener, ""], "open", key) if self.opener else "",
            "transition": _pick(self.transitions, "trans", key, position) if position > 0 else "",
            "actor": actor,
            "verb": self._verb(action),
            "object": self._object_phrase(roles),
            "magnitude": magnitude + self._ordinal_suffix(roles),
            "coda": _pick(self.codas.get(roles.get("valence", ""), self.codas.get("*", [""])), "coda", key),
        }
        text = " ".join(self.form.format_map(_Default(slots)).split()).strip()
        text = _capitalize_sentences(text)
        # the coda owns terminal punctuation; only add a period if none is present
        if text and not text.endswith((".", "!", "?")):
            text += "."
        return text

    def clause(self, roles: Dict[str, str], *, subject: Optional[str] = None) -> str:
        """A bare clause for weaving into a thread — no opener/transition/coda/terminal period.
        A roleless event (a raw headline from another tape) folds in verbatim."""
        if not roles.get("action"):
            return (roles.get("body") or roles.get("title") or "").strip()
        actor = subject if subject is not None else roles.get("actor", "")
        core = f"{actor} {self._verb(roles['action'])}{self._object_phrase(roles)}"
        m = roles.get("magnitude")
        if m:
            unit = roles.get("unit", "")
            number = number_to_words(int(m)) if str(m).isdigit() else m
            core += f" to {number}{(' ' + unit) if unit else ''}"
        core += self._ordinal_suffix(roles)
        return " ".join(core.split()).strip()

    def narrate(self, role_seq: Sequence[Dict[str, str]]) -> List[str]:
        out: List[str] = []
        prev: Optional[Dict[str, str]] = None
        for i, roles in enumerate(role_seq):
            line = self.line(roles, prev_roles=prev, position=i, key=roles.get("_key", i))
            if line:
                out.append(line)
                prev = roles
        return out
