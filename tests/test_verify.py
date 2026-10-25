"""The deterministic fact checker: every rule that can reject a generated line."""
import pytest

from insidegame.agents import allowed_for, fallback_line, verify

ROSTER = [{"id": "a1", "name": "Callum Ashby"}, {"id": "b1", "name": "Luca Brennan"},
          {"id": "b2", "name": "Marco Azevedo"}]
TEAMS = ["Harbor City", "Ridgeway United"]
FACTS_EN = ("Callum Ashby (HAR) shoots from 9 m at 51 km/h, xG 0.15: blocked by Marco Azevedo. "
            "Harbor City have strung together 4 straight passes.")
FACTS_ZH = "Callum Ashby（HAR）在9米处射门，球速51公里/小时，预期进球0.15：被Marco Azevedo封堵"


def check(text, lang="en", facts=FACTS_EN, involved=("a1", "b2"), limit=26):
    return verify(text, lang, facts, set(involved), ROSTER, limit, teams=TEAMS)


@pytest.mark.parametrize("text", [
    "Ashby shoots from 9 m but Azevedo blocks it.",
    "Ashby's 0.15 xG effort is blocked after four straight passes.",  # number word that is in the facts
    "Harbor City work it to Ashby, whose 51 km/h strike is blocked.",
])
def test_supported_lines_pass(text):
    assert check(text) == []


def test_empty_and_too_long():
    assert check("") == ["empty output"]
    assert any(r.startswith("too long") for r in check("Ashby " * 30))


def test_uninvolved_player_is_rejected():
    reasons = check("Brennan reads it and Ashby is blocked.")
    assert "mentions Luca Brennan, who is not in this moment" in reasons


@pytest.mark.parametrize("text, number", [("Ashby fires from 12 m.", "12"), ("Ashby after five passes.", "5")])
def test_numbers_must_come_from_the_facts(text, number):
    assert f"number {number} is not in the facts" in check(text)


def test_number_reused_with_another_unit_is_rejected():
    # 51 is in the facts, but as km/h, not metres
    assert check("Ashby shoots from 51 m.") == ["51 m does not match any measurement in the facts"]


@pytest.mark.parametrize("text, term", [("Ashby with a header, blocked.", "header"),
                                        ("Ashby blocked by the goalkeeper.", "goalkeeper"),
                                        ("Ashby scores!", "scores")])
def test_unsupported_terms_are_rejected(text, term):
    assert f"claims '{term}' which the facts do not support" in check(text)


def test_terms_in_the_facts_are_allowed():
    assert check("A header from Ashby is blocked.", facts=FACTS_EN + " Ashby header.") == []


def test_chinese_rules():
    assert check("Ashby 射门被 Azevedo 封堵。", "zh", FACTS_ZH, limit=60) == []
    assert check("Ashby shoots", "zh", FACTS_ZH, limit=60) == ["not written in Chinese"]
    assert "claims '门将' which the facts do not support" in check("Ashby射门，门将扑救。", "zh", FACTS_ZH, limit=60)


@pytest.mark.parametrize("text", ["阿什比射门被封堵。", "Callum·Ashby射门被封堵。"])
def test_chinese_must_keep_latin_player_names(text):
    assert "player names must stay in Latin letters" in check(text, "zh", FACTS_ZH, limit=60)


def test_chinese_must_keep_latin_team_names():
    facts = FACTS_ZH + "。Harbor City 连续传球"
    assert "team names must stay in Latin letters" in check("哈伯城队 Ashby 射门被封堵。", "zh", facts, limit=60)


def test_goal_moment_allows_scoring_words_and_fallback_passes(bundle):
    goal = next(m for m in bundle["moments"] if m["kind"] == "goal")
    scorer = next(p for p in bundle["players"] if p["id"] == goal["players"][0])["name"].split()[-1]
    teams = [n for t in bundle["teams"] for n in (t["name"], t["short"])]
    allowed = {lang: allowed_for(goal, lang) for lang in ("en", "zh")}
    assert verify(f"{scorer} scores!", "en", allowed["en"], set(goal["players"]), bundle["players"], 26, teams) == []
    for profile, lang in (("casual", "en"), ("analyst", "en"), ("casual", "zh"), ("analyst", "zh")):
        line = fallback_line(goal, profile, lang)
        assert verify(line, lang, allowed[lang], set(goal["players"]), bundle["players"], 400, teams) == []


def test_chinese_line_without_any_name_is_not_a_translated_name():
    assert check("预期进球只有0.15，属于机会不大的尝试。", "zh", FACTS_ZH, limit=60) == []
