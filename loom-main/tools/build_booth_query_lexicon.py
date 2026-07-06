from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def normalize(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[^a-z0-9+/-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


FIELD_BASE = {
    "pts": ["pts", "point", "points", "score", "scored", "scoring"],
    "reb": ["reb", "rebs", "rebound", "rebounds", "board", "boards"],
    "ast": ["ast", "asts", "assist", "assists", "pass", "passes", "setup", "setups"],
    "stl": ["stl", "steal", "steals", "takeaway", "takeaways"],
    "blk": ["blk", "block", "blocks", "rejection", "rejections"],
    "to": ["to", "tos", "turnover", "turnovers", "giveaway", "giveaways", "cough up"],
    "pf": ["pf", "foul", "fouls", "personal", "personals"],
    "min": ["min", "mins", "minute", "minutes", "time", "runtime"],
    "fg": ["fg", "field goal", "field goals", "shooting", "made"],
    "3pt": ["3pt", "3 pts", "3 point", "3 pointer", "3 pointers", "three point", "three pointer", "threes"],
    "ft": ["ft", "free throw", "free throws"],
    "plusminus": ["plus minus", "+/-", "plusminus", "plus-minus", "plus"],
}

INTENT_BASE = {
    "max": ["most", "highest", "best", "leader", "leads", "lead", "top", "won", "winner"],
    "min": ["least", "fewest", "lowest", "worst"],
    "count": ["how many", "count", "number of", "total"],
    "zero": ["zero", "without", "none"],
    "summarize": ["summarize", "summary", "overview", "recap", "what happened", "bigger picture"],
    "first": ["first", "earliest", "start", "opening"],
    "last": ["last", "latest", "end", "final"],
    "next": ["after", "next", "following", "what happened next"],
}

ACTION_BASE = {
    "overtake": ["overtake", "overtakes", "pass", "passes", "passed"],
    "pit": ["pit", "pit stop", "pitted"],
    "clock": ["clock", "clocks", "clocked", "set", "posted"],
    "make": ["make", "made", "score", "scores", "scored", "hit", "hits"],
    "rebound": ["rebound", "rebounds", "board", "boards"],
    "assist": ["assist", "assists", "setup", "set up", "dime", "dimes"],
    "steal": ["steal", "steals", "takeaway", "takeaways"],
    "block": ["block", "blocks", "reject", "rejection"],
    "win": ["win", "wins", "won", "victory"],
    "report": ["report", "reports", "reported", "explain", "explains"],
}

MIRROR_BASE = {
    "family": {
        "broadcast": ["who", "won", "winner", "latest", "headline", "top", "leader", "score", "most"],
        "academic": ["why", "how", "compare", "compute", "baseline", "justify", "evidence", "infer", "truth", "honest"],
        "emergency": ["urgent", "alarm", "warning", "help", "fire", "rescue", "now", "immediately"],
        "ceremonial": ["hear ye", "hark", "behold", "decree", "proclaim"],
        "locker_room": ["lets go", "team", "game", "beat them", "win it", "clutch", "huge"],
        "shop_floor": ["build", "shift", "line", "crew", "fix", "repair", "move it", "status"],
    },
    "role": {
        "engineer": ["api", "deploy", "latency", "retry", "worker", "schema", "compute", "system", "trace", "query engine"],
        "firefighter": ["fire", "smoke", "rescue", "evacuate", "alarm", "dispatch", "incident"],
        "astronaut": ["orbit", "mission", "launch", "telemetry", "guidance", "booster", "pad", "flight"],
        "town_crier": ["hear ye", "hark", "proclaim", "decree", "my good people"],
        "coach": ["team", "locker room", "game", "win", "score", "bench", "clutch"],
        "professor": ["why", "explain", "compare", "analyze", "infer", "baseline", "truth", "evidence"],
        "detective": ["who did", "suspect", "motive", "case", "clue", "evidence", "noir"],
        "announcer": ["who won", "who scored", "leader", "most points", "latest", "headline"],
        "sergeant": ["report", "status", "move", "copy that", "hold position", "squad"],
        "politician": ["my friends", "let me be clear", "for the record"],
        "office": ["fyi", "circling back", "per the update", "please advise"],
    },
    "voiceprint": {
        "heroic": ["hero", "save", "rescue", "clutch", "mission accomplished"],
        "bureaucratic": ["baseline", "policy", "process", "status", "official", "documented"],
        "panicked": ["urgent", "now", "help", "alarm", "what happened", "immediately"],
        "exhausted": ["again", "still", "another", "tired", "exhausted"],
        "smug": ["obviously", "clearly", "of course", "as expected"],
        "reverent": ["please", "kindly", "respectfully", "solemnly", "honestly"],
        "wry": ["sure", "apparently", "supposedly", "funny enough"],
        "rookie": ["trying", "learning", "first time", "new here"],
    },
}

GLOSS_RULES = {
    "fieldAliases": {
        "pts": [r"\bscore\b", r"\bpoint\b", r"\bcount\b"],
        "reb": [r"\brebound\b", r"\bboard\b"],
        "ast": [r"\bassist\b", r"\bhelp\b"],
        "stl": [r"\bsteal\b", r"\btake away\b", r"\btakeaway\b"],
        "blk": [r"\bblock\b", r"\bstop\b"],
        "to": [r"\bturnover\b", r"\bgive away\b", r"\bgiveaway\b"],
        "pf": [r"\bfoul\b"],
        "min": [r"\bminute\b", r"\btime\b"],
        "fg": [r"\bfield goal\b", r"\bshoot\b", r"\bshot\b"],
        "3pt": [r"\bthree point\b", r"\b3 point\b", r"\bthree-pointer\b", r"\bthree pointer\b"],
        "ft": [r"\bfree throw\b"],
        "plusminus": [r"\bplus minus\b"],
    },
    "queryIntentAliases": {
        "max": [r"\bhighest\b", r"\bbest\b", r"\blead\b", r"\btop\b", r"\bmost\b"],
        "min": [r"\blowest\b", r"\bleast\b", r"\bfewest\b", r"\bworst\b"],
        "count": [r"\bnumber\b", r"\bcount\b", r"\btotal\b"],
        "summarize": [r"\bsummary\b", r"\boverview\b", r"\brecap\b", r"\breport\b"],
        "next": [r"\bfollowing\b", r"\bafter\b"],
    },
    "actionAliases": {
        "overtake": [r"\bpass\b", r"\bovertake\b"],
        "pit": [r"\bpit stop\b", r"\bpit\b"],
        "clock": [r"\bfastest lap\b", r"\bclock\b", r"\bposted\b"],
        "make": [r"\bscore\b", r"\bmake\b", r"\bhit\b"],
        "rebound": [r"\brebound\b", r"\bboard\b"],
        "assist": [r"\bassist\b", r"\bsetup\b"],
        "steal": [r"\bsteal\b", r"\btake away\b", r"\btakeaway\b"],
        "block": [r"\bblock\b", r"\breject\b"],
        "win": [r"\bwin\b", r"\bvictory\b"],
        "report": [r"\breport\b", r"\bexplain\b"],
    },
}


def should_keep_lemma(lemma: str) -> bool:
    if not lemma:
        return False
    if len(lemma) < 2 or len(lemma) > 32:
        return False
    if lemma.startswith("http://") or lemma.startswith("https://"):
        return False
    return True


def dedupe_sorted(values: list[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for value in sorted(values):
        key = normalize(value)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def build_maps(rows: list[dict]) -> dict:
    field_aliases = {key: set(vals) for key, vals in FIELD_BASE.items()}
    intent_aliases = {key: set(vals) for key, vals in INTENT_BASE.items()}
    action_aliases = {key: set(vals) for key, vals in ACTION_BASE.items()}
    mirror_hints = {bucket: {key: set(vals) for key, vals in groups.items()} for bucket, groups in MIRROR_BASE.items()}

    for row in rows:
        lemma = normalize(str(row.get("lemma", "")))
        gloss = normalize(str(row.get("gloss", "")))
        cat = normalize(str(row.get("catLabel", "")))
        if not should_keep_lemma(lemma):
            continue

        for key, patterns in GLOSS_RULES["fieldAliases"].items():
            if any(re.search(pattern, gloss) for pattern in patterns):
                field_aliases[key].add(lemma)
        for key, patterns in GLOSS_RULES["queryIntentAliases"].items():
            if any(re.search(pattern, gloss) for pattern in patterns):
                intent_aliases[key].add(lemma)
        for key, patterns in GLOSS_RULES["actionAliases"].items():
            if any(re.search(pattern, gloss) for pattern in patterns):
                action_aliases[key].add(lemma)

        if cat == "verb":
            if re.search(r"\bprovide information\b|\breport\b|\bexplain\b", gloss):
                action_aliases["report"].add(lemma)
            if re.search(r"\bscore\b|\bhit\b|\bmake\b", gloss):
                action_aliases["make"].add(lemma)
            if re.search(r"\bwin\b|\bvictory\b", gloss):
                action_aliases["win"].add(lemma)

        if cat in {"verb", "noun", "adjective", "adverb", "interjection"}:
            if re.search(r"\binvestigation\b|\binfer\b|\btruth\b|\banalysis\b", gloss):
                mirror_hints["family"]["academic"].add(lemma)
                mirror_hints["role"]["professor"].add(lemma)
            if re.search(r"\balarm\b|\burgent\b|\bwarning\b|\brescue\b|\bfire\b", gloss):
                mirror_hints["family"]["emergency"].add(lemma)
                mirror_hints["role"]["firefighter"].add(lemma)
                mirror_hints["voiceprint"]["panicked"].add(lemma)
            if re.search(r"\breport\b|\bheadline\b|\bbroadcast\b", gloss):
                mirror_hints["family"]["broadcast"].add(lemma)
                mirror_hints["role"]["announcer"].add(lemma)

    return {
        "fieldAliases": {key: dedupe_sorted(list(vals)) for key, vals in field_aliases.items()},
        "queryIntentAliases": {key: dedupe_sorted(list(vals)) for key, vals in intent_aliases.items()},
        "actionAliases": {key: dedupe_sorted(list(vals)) for key, vals in action_aliases.items()},
        "mirrorHints": {
            bucket: {key: dedupe_sorted(list(vals)) for key, vals in groups.items()}
            for bucket, groups in mirror_hints.items()
        },
    }


def stats_for(payload: dict) -> dict:
    return {
        "fieldAliasTerms": sum(len(v) for v in payload["fieldAliases"].values()),
        "intentAliasTerms": sum(len(v) for v in payload["queryIntentAliases"].values()),
        "actionAliasTerms": sum(len(v) for v in payload["actionAliases"].values()),
        "mirrorHintTerms": sum(len(v) for groups in payload["mirrorHints"].values() for v in groups.values()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a compact booth query lexicon from a large lexeme dump.")
    parser.add_argument(
        "--source",
        default=r"C:\Users\evana\Downloads\query.json",
        help="Path to the source lexeme JSON dump.",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[1] / "docs" / "booth-query-lexicon.json"),
        help="Path to write the reduced booth lexicon JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = Path(args.source)
    output = Path(args.output)
    rows = json.loads(source.read_text(encoding="utf-8"))
    payload = build_maps(rows)
    result = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "sourceRows": len(rows),
        "stats": stats_for(payload),
        **payload,
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {output} from {source} with {len(rows)} source rows")
    print(json.dumps(result["stats"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
