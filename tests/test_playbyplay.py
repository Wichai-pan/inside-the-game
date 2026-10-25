"""Caption scheduling: one speaker, urgent calls cut in, analysis that play has overtaken is dropped."""
import pytest

from insidegame.playbyplay import PRIORITY, schedule

from .conftest import MATCH_IDS, load_bundle

STATE = {"t": 0, **{k: {"passes": 0, "completed": 0, "shots": 0, "goals": 0, "pass_pct": None,
                        "possession_pct": 50, "recent_possession_pct": 50} for k in ("a", "b")}}
LONG = " ".join(["word"] * 26)  # about ten seconds to say


def tiny_match(events, duration):
    """Four players, a still ball, and only the event fields the caption track reads."""
    players = [{"id": pid, "team": pid[0], "name": name} for pid, name in
               (("a1", "Ada Kole"), ("a2", "Ben Ruiz"), ("b1", "Cal Voss"), ("b2", "Dan Pike"))]
    teams = [{"id": "a", "name": "Alpha", "short": "ALP"}, {"id": "b", "name": "Beta", "short": "BET"}]
    hz = 5
    frames = [{"ball": [0.0, 0.0]} for _ in range(int(duration * hz) + 1)]
    full = [{"id": f"e{i}", "state": STATE, "target": None, **e} for i, e in enumerate(events)]
    return {"players": players, "teams": teams, "hz": hz, "frames": frames, "duration": duration}, full


def moment(t, seconds, text=LONG):
    c = {"text": text, "source": "agent:test", "seconds": seconds}
    return {"id": f"m{t}", "t": t, "commentary": {"casual": {"en": c}}}


def complete_pass(t, end_t, actor="a1", target="a2"):
    return {"kind": "pass", "team": actor[0], "actor": actor, "target": target, "t": t, "end_t": end_t,
            "outcome": "complete", "metrics": {"length_m": 10}}


def spoken(track):
    return sorted((c for c in track if not c.get("dropped")), key=lambda c: c["t"])


def assert_no_overlap(track):
    said = spoken(track)
    for a, b in zip(said, said[1:]):
        assert a["end"] <= b["t"] + 1e-6, f"{a['text']!r} ({a['t']}-{a['end']}) overlaps {b['text']!r} ({b['t']})"


def test_urgent_call_cuts_into_analysis():
    match, events = tiny_match([complete_pass(8, 9),
                                {"kind": "tackle", "team": "b", "actor": "b1", "target": "a2", "t": 14}], 16)
    track = schedule(match, events, [moment(10, 0.5)], "casual", "en")
    analysis = next(c for c in track if c["voice"] == "analysis")
    tackle = next(c for c in track if c.get("priority") == PRIORITY["tackle"])
    assert not analysis.get("dropped") and analysis["cut"] is True
    assert analysis["end"] == tackle["t"] == 14
    assert_no_overlap(track)


def test_analysis_overtaken_by_a_later_shot_is_dropped():
    shot = {"kind": "shot", "team": "a", "actor": "a1", "t": 7, "end_t": 8, "outcome": "wide"}
    match, events = tiny_match([shot], 18)
    track = schedule(match, events, [moment(5, 10)], "casual", "en")  # ready at 15, after the shot at 8
    analysis = [c for c in track if c["voice"] == "analysis"]
    assert len(analysis) == 1 and analysis[0]["dropped"] is True


def test_queued_analysis_is_dropped_when_a_shot_cuts_in():
    shot = {"kind": "shot", "team": "a", "actor": "a1", "t": 9.5, "end_t": 10, "outcome": "wide"}
    match, events = tiny_match([complete_pass(8, 9), shot], 14)
    track = schedule(match, events, [moment(9, 0.2)], "casual", "en")  # ready while "Kole to Ruiz." is said
    analysis = [c for c in track if c["voice"] == "analysis"]
    assert len(analysis) == 1 and analysis[0]["dropped"] is True
    assert_no_overlap(track)


def test_routine_pass_is_skipped_while_speaker_is_busy():
    match, events = tiny_match([complete_pass(8, 9), complete_pass(9, 9.5, "a2", "a1")], 12)
    track = schedule(match, events, [], "casual", "en")
    assert [c["text"] for c in track] == ["Kole to Ruiz."]


def test_deferred_call_then_urgent_cut_in_does_not_overlap():
    fail = {"kind": "pass", "team": "a", "actor": "a1", "target": "a2", "t": 10, "end_t": 11, "outcome": "cut_out"}
    press = {"kind": "pressure", "team": "b", "actor": "b1", "target": "a1", "t": 12.0}
    tackle = {"kind": "tackle", "team": "b", "actor": "b2", "target": "a2", "t": 12.3}
    match, events = tiny_match([fail, press, tackle], 16)
    assert_no_overlap(schedule(match, events, [], "casual", "zh"))


VARIANTS = [(mid, p, l) for mid in MATCH_IDS for p in ("casual", "analyst") for l in ("en", "zh")]


@pytest.mark.parametrize("mid, profile, lang", VARIANTS)
def test_published_caption_tracks(mid, profile, lang):
    match = load_bundle(f"{mid}.json")
    track = schedule(match, match["events"], match["moments"], profile, lang)
    assert track == match["captions"][profile][lang], "captions are stale: rebuild the bundle"
    ready = [c for c in track if c["voice"] == "analysis" and not c.get("dropped")]
    assert all(c["t"] >= c["ready"] - 1e-6 for c in ready), "analysis said before it was generated"
    # a line may finish after the final whistle, but none starts after it (late analysis is dropped instead)
    assert all(0 <= c["t"] <= match["duration"] for c in track if not c.get("dropped"))
    assert all(c["t"] <= c["end"] for c in track)
    assert_no_overlap(track)
