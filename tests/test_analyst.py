"""Analyst metrics on a small seeded synthetic match (no model, no data files)."""
import math

import pytest

from insidegame.analyst import analyse
from insidegame.simulate import generate

SEED, DURATION = 3, 120


@pytest.fixture(scope="module")
def match():
    return generate(SEED, DURATION)


@pytest.fixture(scope="module")
def analysis(match):
    return analyse(match)


def test_generation_is_deterministic(match):
    assert generate(SEED, DURATION)["events"] == match["events"]


def test_final_state_counts_the_events(analysis):
    events, final = analysis["events"], analysis["final"]
    for team in ("a", "b"):
        mine = [e for e in events if e["team"] == team]
        shots = [e for e in mine if e["kind"] == "shot"]
        passes = [e for e in mine if e["kind"] == "pass"]
        assert final[team]["passes"] == len(passes)
        assert final[team]["completed"] == sum(e["outcome"] == "complete" for e in passes)
        assert final[team]["shots"] == len(shots)
        assert final[team]["goals"] == sum(e["outcome"] == "goal" for e in shots)
        assert final[team]["xg"] == pytest.approx(sum(e["xg"] for e in shots), abs=0.01)
    assert final["a"]["possession_pct"] + final["b"]["possession_pct"] == 100


def test_running_state_never_decreases(analysis):
    keys = ("passes", "shots", "goals", "pressures", "regains")
    states = [e["state"] for e in analysis["events"]]
    for prev, cur in zip(states, states[1:]):
        for team in ("a", "b"):
            assert all(cur[team][k] >= prev[team][k] for k in keys)


def test_pass_and_shot_metrics(analysis):
    for e in analysis["events"]:
        mt = e["metrics"]
        if e["kind"] == "pass":
            assert mt["length_m"] == round(math.hypot(e["end_x"] - e["x"], e["end_y"] - e["y"]))
            assert 0 <= mt["difficulty"] <= 99
            assert mt["receiver_space_m"] >= 0 and isinstance(mt["under_pressure"], bool)
            assert f"{mt['length_m']} m" in e["fact"]["en"] and f"{mt['length_m']}米" in e["fact"]["zh"]
        elif e["kind"] == "shot":
            assert mt["xg"] == e["xg"] and mt["distance_m"] >= 0
        elif e["kind"] in ("interception", "tackle"):
            assert mt["in_attacking_half"] == (mt["metres_from_goal"] < 52.5)


def test_moments_are_traceable_to_events(match, analysis):
    ids = {e["id"] for e in analysis["events"]}
    players = {p["id"] for p in match["players"]}
    assert analysis["moments"], "a two-minute clip should yield at least one key moment"
    for m in analysis["moments"]:
        assert m["key_event"] in m["event_ids"] and set(m["event_ids"]) <= ids
        assert [f["event_id"] for f in m["facts"]] == m["event_ids"]
        assert set(m["players"]) <= players
        assert m["why"] and all(w["en"] and w["zh"] for w in m["why"])


def test_analysis_is_causal(match, analysis):
    """Values for an event must not depend on anything that happens after it."""
    cut = DURATION / 2
    early = analyse({**match, "events": [e for e in match["events"] if e["t"] <= cut]})["events"]
    assert early
    for full, partial in zip(analysis["events"], early):
        assert (full["metrics"], full["state"], full["fact"]) == (partial["metrics"], partial["state"], partial["fact"])
