"""Stats layer computed from a published match bundle."""
import pytest

from insidegame.stats import from_match

from .conftest import MATCH_IDS, load_bundle




@pytest.fixture(scope="module", params=MATCH_IDS)
def pair(request):
    match = load_bundle(f"{request.param}.json")
    return match, from_match(match), request.param


def test_goals_agree_with_the_feed(pair):
    match, stats, _ = pair
    final = match["final_stats"]
    assert [t["goals"] for t in stats["teams"]] == [final["a"]["goals"], final["b"]["goals"]]
    assert len(stats["goals"]) == sum(t["goals"] for t in stats["teams"])
    assert sum(p["goals"] for p in stats["players"]) == len(stats["goals"])
    a, b = match["teams"]
    assert stats["match"] == f"{a['name']} {final['a']['goals']}-{final['b']['goals']} {b['name']}"


def test_shots_and_players(pair):
    match, stats, _ = pair
    assert len(stats["shots"]) == sum(t["shots"] for t in stats["teams"])
    assert len(stats["players"]) == len(match["players"])
    for p in stats["players"]:
        assert p["distance_km"] >= 0 and p["sprints"] >= 0 and p["top_speed_kmh"] < 45
        assert p["pass_pct"] is None or 0 <= p["pass_pct"] <= 100


def test_shares_and_segments(pair):
    _, stats, _ = pair
    assert sum(t["possession_pct"] for t in stats["teams"]) == 100
    assert len(stats["segments"]) == 6
    for seg in stats["segments"]:
        if seg["seconds"]:
            assert seg["home"]["possession_pct"] + seg["away"]["possession_pct"] == 100
    for side in ("home", "away"):
        assert sum(s[side]["goals"] for s in stats["segments"]) == stats["teams"][0 if side == "home" else 1]["goals"]


def test_published_stats_file_is_up_to_date(pair):
    _, stats, mid = pair
    assert stats == load_bundle(f"{mid}-stats.json"), "re-run: python -m insidegame.stats --match docs/synthetic/<id>.json"
