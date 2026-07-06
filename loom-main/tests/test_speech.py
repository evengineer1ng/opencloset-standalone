"""The speech engine — domain (rows) and grammar (style) are separate; no domain words here."""

from __future__ import annotations

from oradio_engine.binding import build_transform
from oradio_engine.contract import NormalizedCandidate
from oradio_engine.speech import Grammar, number_to_words, regular_past, roles_from_tags

VERBS = {"overtake": "overtook", "rise": "rose"}
INTERN = {"form": "{transition}{actor} {verb}{object}{magnitude}{coda}",
          "transitions": ["", ""], "codas": {"*": ["."], "hype": ["!"]}}
CRIER = {"opener": "Hear ye! ", "form": "{opener}{actor} {verb}{object}{magnitude}{coda}",
         "codas": {"*": ["."]}}


def test_number_to_words():
    assert number_to_words(3) == "three"
    assert number_to_words(72) == "seventy-two"
    assert number_to_words(118) == "one hundred eighteen"


def test_regular_past_fallback():
    assert regular_past("clock") == "clocked"
    assert regular_past("settle") == "settled"
    assert regular_past("drop") == "dropped"      # monosyllabic CVC doubles
    assert regular_past("jam") == "jammed"
    assert regular_past("open") == "opened"        # polysyllable does NOT over-double
    assert regular_past("whisper") == "whispered"


def test_roles_from_tags_is_contract_safe_channel():
    roles = roles_from_tags(["nba", "actor:Towns", "action:make", "object:three", "junk"])
    assert roles == {"actor": "Towns", "action": "make", "object": "three"}


def test_one_grammar_speaks_two_domains():
    g = Grammar(INTERN, VERBS)  # ONE grammar, zero domain code below
    assert g.line({"action": "overtake", "actor": "Verstappen", "object": "Norris"}, key="a") == "Verstappen overtook Norris."
    assert g.line({"action": "rise", "actor": "your heart", "magnitude": "118", "unit": "bpm"}, key="b") == "Your heart rose to one hundred eighteen bpm."


def test_swapping_grammar_revoices_the_same_row():
    row = {"action": "overtake", "actor": "Verstappen", "object": "Norris", "valence": "hype"}
    intern = Grammar(INTERN, VERBS).line(row, key="k")
    crier = Grammar(CRIER, VERBS).line(row, key="k")
    assert intern != crier
    assert crier.startswith("Hear ye!")


def test_no_domain_lexicon_general_table_then_regular_fallback():
    g = Grammar({"form": "{actor} {verb}{object}"}, {"overtake": "overtook"})
    assert g.line({"action": "overtake", "actor": "X", "object": "Y"}, key="k") == "X overtook Y."
    assert g.line({"action": "clock", "actor": "X", "object": "a lap"}, key="k").startswith("X clocked")  # regular


def test_proper_noun_no_article_common_gets_article():
    g = Grammar({"form": "{actor} {verb}{object}"}, {"overtake": "overtook"})
    assert g.line({"action": "overtake", "actor": "X", "object": "Norris"}, key="k") == "X overtook Norris."
    assert "a backmarker" in g.line({"action": "overtake", "actor": "X", "object": "backmarker"}, key="k")


def test_cohesion_uses_pronoun_for_repeated_actor():
    g = Grammar({"form": "{actor} {verb}{object}"}, {})
    rows = [{"action": "rebound", "actor": "Wembanyama", "object": "board", "pronoun": "he", "_key": "1"},
            {"action": "rebound", "actor": "Wembanyama", "object": "board", "pronoun": "he", "_key": "2"}]
    lines = g.narrate(rows)
    assert lines[0].startswith("Wembanyama")
    assert lines[1].startswith("He") and "Wembanyama" not in lines[1]


def test_deterministic_replay():
    g = Grammar(INTERN, VERBS)
    rows = [{"action": "overtake", "actor": "A", "object": "B", "valence": "hype", "_key": "1"}]
    assert g.narrate(rows) == g.narrate(rows)


def test_ordinal_gives_continuity_general():
    g = Grammar({"form": "{actor} {verb}{object}{magnitude}{coda}"}, {"pit": "pitted"})
    # carried state -> continuity, domain-agnostic
    assert g.line({"action": "pit", "actor": "Hadjar", "ordinal": "2"}, key="k") == "Hadjar pitted for the second time."
    assert g.line({"action": "spike", "actor": "your heart", "ordinal": "3"}, key="k") == "Your heart spiked for the third time."
    # first time -> no suffix; named counterpart -> suppressed (avoids "overtook Sainz for the 2nd time" ambiguity)
    assert g.line({"action": "pit", "actor": "X", "ordinal": "1"}, key="k") == "X pitted."
    assert "time" not in g.line({"action": "overtake", "actor": "X", "object": "Sainz", "ordinal": "5"}, key="k")


def test_transform_reads_roles_and_uses_grammar_file():
    t = build_transform("tape_to_speech", grammar="data/grammars/intern.json",
                        verbs="data/english/irregular_verbs.json")
    c = NormalizedCandidate("p", "court", "", "", 0.5, 1.0, "f1",
                            ("actor:Verstappen", "action:overtake", "object:Norris"))
    out = t(c)
    assert out and out["text"].startswith("Verstappen overtook Norris")
    assert t(NormalizedCandidate("p2", "court", "", "", 0.5, 1.0, "f1", ("nba",))) is None
