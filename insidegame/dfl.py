"""Adapter: a window of the open DFL match (Köln–Bayern, 2022/23) in the pipeline's match format.

Source: Bassek et al., "An integrated dataset of spatiotemporal and event data in elite soccer",
Scientific Data 2025, CC BY 4.0. Download the files with `python3 commentary.py fetch`.

Event locations come from the tracked ball (25 Hz, sampled at 5 Hz) rather than the event feed's
own coordinates, so events and positions share one frame. Reception points, ball and shot speeds
are measured from tracking. DFL has no pressure events; a pressure event is derived when the
player who wins the ball was within 4 m of the previous ball carrier just before, and is marked
`derived`.
"""
import json
import math
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KICKOFF = datetime.fromisoformat("2023-05-27T13:30:12.220+00:00")
FIRST_FRAME, FPS, HZ = 10000, 25, 5
HALF_X = 52.5

ROLE = {"TW": "GK", "RV": "RB", "LV": "LB", "IVR": "CB", "IVL": "CB", "IVZ": "CB", "DMR": "DM", "DML": "DM",
        "DMZ": "DM", "ZO": "AM", "ORM": "RW", "OLM": "LW", "STZ": "ST", "STL": "ST", "STR": "ST"}
SHOT_OUTCOME = {"SuccessfulShot": "goal", "SavedShot": "saved", "ShotWide": "wide", "BlockedShot": "blocked",
                "ShotWoodWork": "woodwork"}
SET_PIECE = {"KickOff": "kickoff", "ThrowIn": "throw_in", "FreeKick": "free_kick", "CornerKick": "corner",
             "GoalKick": "goal_kick"}


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def metadata():
    root = ET.parse(DATA / "meta.xml").getroot()
    teams, players = {}, {}
    for team in root.findall(".//Team"):
        tid = team.get("TeamId")
        teams[tid] = {"name": team.get("TeamName"), "role": team.get("Role")}
        for p in team.findall(".//Player"):
            short = p.get("Shortname") or p.get("LastName")
            if ". " in short:  # "Y. Sommer" -> "Yann Sommer"; long legal first names stay short
                short = f"{p.get('FirstName').split()[0]} {p.get('LastName')}"
            players[p.get("PersonId")] = {
                "name": short, "team": tid,
                "number": int(p.get("ShirtNumber")), "role": ROLE.get(p.get("PlayingPosition"), "SUB")}
    return teams, players


def tracking(t0, t1):
    """Positions at 5 Hz for the first-half window, cached as JSON after the first slow pass."""
    cache = DATA / f"dfl_tracking_{t0}_{t1}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    lo, hi = FIRST_FRAME + t0 * FPS, FIRST_FRAME + t1 * FPS
    step = FPS // HZ
    frames, current = {}, None
    for phase, elem in ET.iterparse(DATA / "tracking.xml", events=("start", "end")):
        if phase == "start" and elem.tag == "FrameSet":
            current = (elem.get("TeamId"), elem.get("PersonId"), elem.get("GameSection"))
        elif phase == "end" and elem.tag == "Frame":
            if current and current[2] == "firstHalf":
                n = int(elem.get("N"))
                if lo <= n <= hi and (n - FIRST_FRAME) % step == 0:
                    key = "ball" if current[0].upper() == "BALL" else current[1]
                    if current[0] != "referee":
                        frames.setdefault(n, {})[key] = (float(elem.get("X")), float(elem.get("Y")))
            elem.clear()
        elif phase == "end" and elem.tag == "FrameSet":
            elem.clear()
            current = None
    out = {str((n - FIRST_FRAME) / FPS): v for n, v in sorted(frames.items())}
    cache.write_text(json.dumps(out))
    return out


def raw_events(t0, t1):
    out = []
    for e in ET.parse(DATA / "events.xml").getroot().findall("Event"):
        if not len(e) or e[0].tag == "Delete":
            continue
        if e.get("CalculatedFrame"):
            t = (int(e.get("CalculatedFrame")) - FIRST_FRAME) / FPS
        else:
            t = (datetime.fromisoformat(e.get("EventTime")) - KICKOFF).total_seconds()
        if t0 <= t <= t1:
            out.append((t, e))
    return sorted(out, key=lambda x: x[0])


def convert_events(frames, hz, players, records, id_prefix="dfl-"):
    """DFL event XML -> pipeline events on a given frame timeline.

    `records` are dicts {t, elem, team, person}: t is seconds on the frame timeline, `team` maps a DFL
    team id to "a"/"b" and `person` maps a DFL person id to a pipeline player id (None if not on the
    pitch). The real-match loader and the stitcher both use this, each with its own mappings.
    """
    index = {p["id"]: i for i, p in enumerate(players)}

    def frame_at(t):
        return frames[max(0, min(len(frames) - 1, round(t * hz)))]

    def ball(t):
        return tuple(frame_at(t)["ball"])

    def pos(pid, t):
        return tuple(frame_at(t)["players"][index[pid]])

    def kick_time(pid, t):
        """Event clocks drift from tracking: take the last sample near t where the ball is at the player."""
        best, best_d = t, 99.0
        for k in range(-5, 6):
            s = t + k / hz
            d = _dist(ball(s), pos(pid, s))
            if d < best_d - 0.3 or (d < 1.5 and d <= best_d + 0.3):
                best, best_d = s, min(d, best_d)
        return best

    def reception(pid, t, until):
        """First sampled time after the kick when the ball has travelled and is within 2 m of pid."""
        start = ball(t)
        s = t + 1 / hz
        while s <= until:
            if _dist(ball(s), pos(pid, s)) < 2.0 and _dist(ball(s), start) > 2.5:
                return s
            s += 1 / hz
        return until

    events, score, holder = [], {"a": 0, "b": 0}, None

    def add(t, kind, team, actor, **fields):
        bx, by = ball(t)
        events.append({"id": f"{id_prefix}{fields.pop('eid')}", "t": round(t, 2), "kind": kind, "team": team,
                       "actor": actor, "x": round(bx, 2), "y": round(by, 2), **fields})

    for i, rec in enumerate(records):
        t, e, team_of, who = rec["t"], rec["elem"], rec["team"], rec["person"]
        main, eid = e[0], e.get("EventId")
        tag = main.tag
        nxt = next((r["t"] for r in records[i + 1:] if r["t"] > t + 0.05), t + 3)
        play = main if tag == "Play" else main.find("Play")
        shot = main if tag == "ShotAtGoal" else main.find(".//ShotAtGoal")  # penalties nest the shot
        if play is not None and who(play.get("Player")):
            team = team_of(play.get("Team"))
            actor, target = who(play.get("Player")), who(play.get("Recipient"))
            ok = play.get("Evaluation") == "successfullyCompleted"
            t = kick_time(actor, t)
            end_t = min(reception(target, t, nxt + 1.5), t + 5) if ok and target else max(nxt, t + 0.4)
            end = ball(end_t)
            fields = {"target": target, "outcome": "complete" if ok else "incomplete",
                      "end_x": round(end[0], 2), "end_y": round(end[1], 2), "end_t": round(end_t, 2)}
            if play.find("Cross") is not None:
                fields["cross"] = True
            if tag in SET_PIECE:
                fields["set_piece"] = SET_PIECE[tag]
            if play.get("Height") == "high":
                fields["lofted"] = True
            add(t, "pass", team, actor, eid=eid, **fields)
            holder = team if ok else holder
        elif shot is not None and who(shot.get("Player")):
            team, shooter = team_of(shot.get("Team")), who(shot.get("Player"))
            t = kick_time(shooter, t)
            res = shot[0] if len(shot) else None
            outcome = SHOT_OUTCOME.get(res.tag if res is not None else "", "wide")
            # Shot speed: fastest ball displacement over 0.2 s in the first 0.8 s after the strike.
            speeds = [_dist(ball(t + k / hz), ball(t + (k + 1) / hz)) * hz for k in range(4)]
            end_t = t + 0.8
            for k in range(1, 15):  # ball reaches the goal line, or stops moving
                if abs(ball(t + k / hz)[0]) >= HALF_X - 0.5 or _dist(ball(t + k / hz), ball(t + (k - 1) / hz)) < 0.2:
                    end_t = t + k / hz
                    break
            end = ball(end_t)
            fields = {"outcome": "wide" if outcome == "woodwork" else outcome, "xg": round(float(shot.get("xG", 0.05)), 3),
                      "end_x": round(end[0], 2), "end_y": round(end[1], 2), "end_t": round(end_t, 2),
                      "shot_speed_ms": round(max(speeds), 1), "body_part": "head" if shot.get("TypeOfShot") == "head" else "foot",
                      "dfl_pressure": float(shot.get("Pressure", 0)), "counter_attack": shot.get("CounterAttack") == "true",
                      "woodwork": outcome == "woodwork", "penalty": shot.get("BuildUp") == "penaltyKick"}
            if res is not None and who(res.get("GoalKeeper")):
                fields["keeper"] = who(res.get("GoalKeeper"))
            if res is not None and res.tag == "BlockedShot" and who(res.get("Player")):
                fields["blocker"] = who(res.get("Player"))
            if outcome == "goal":
                score[team] += 1
                fields["score_after"] = dict(score)
            if who(shot.get("AssistShotAtGoal")):
                fields["assist"] = who(shot.get("AssistShotAtGoal"))
            add(t, "shot", team, shooter, eid=eid, **fields)
        elif tag == "TacklingGame" and main.get("PossessionChange") == "true" and who(main.get("Winner")):
            team = team_of(main.get("WinnerTeam"))
            add(t, "tackle", team, who(main.get("Winner")), eid=eid, target=who(main.get("Loser")), outcome="won")
            holder = team
        elif tag == "BallClaiming" and who(main.get("Player")):
            team = team_of(main.get("Team"))
            if holder and team != holder:
                add(t, "interception", team, who(main.get("Player")), eid=eid, outcome="won")
            holder = team
        elif tag == "Foul" and who(main.get("Fouler")):
            add(t, "foul", team_of(main.get("TeamFouler")), who(main.get("Fouler")), eid=eid,
                target=who(main.get("Fouled")))
        elif tag == "OtherBallAction" and main.get("DefensiveClearance") == "true" and who(main.get("Player")):
            add(t, "clearance", team_of(main.get("Team")), who(main.get("Player")), eid=eid)

    # Derived pressure: the eventual ball winner closed down the previous carrier just before.
    derived = []
    for i, e in enumerate(events):
        if e["kind"] not in ("tackle", "interception"):
            continue
        prev = next((p for p in reversed(events[:i]) if p["kind"] in ("pass", "clearance") and p["team"] != e["team"]), None)
        if not prev:
            continue
        carrier = prev["actor"]
        for back in (1.5, 1.0, 0.5):
            if e["t"] - back > prev["t"] - 1 and _dist(pos(e["actor"], e["t"] - back), pos(carrier, e["t"] - back)) < 4:
                derived.append({"id": f"{e['id']}-p", "t": round(e["t"] - back, 2), "kind": "pressure", "team": e["team"],
                                "actor": e["actor"], "target": carrier, "x": e["x"], "y": e["y"], "derived": True})
                break
    return sorted(events + derived, key=lambda e: e["t"])


def load(t0=0, t1=600):
    teams, people = metadata()
    track = tracking(t0, t1)
    times = sorted(float(k) for k in track)
    on_pitch = sorted({pid for f in track.values() for pid in f if pid != "ball"},
                      key=lambda pid: (people[pid]["team"], people[pid]["number"]))
    events_xml = raw_events(t0, t1)
    kick = next(e for _, e in events_xml if e[0].tag == "KickOff")
    left = kick[0].get("TeamLeft")  # defends the left goal, so attacks +x in the first half
    team_key = {left: "a", next(t for t in teams if t != left): "b"}

    players = []
    for pid in on_pitch:
        p = people[pid]
        players.append({"id": pid, "team": team_key[p["team"]], "number": p["number"], "role": p["role"],
                        "name": p["name"]})
    index = {p["id"]: i for i, p in enumerate(players)}

    frames, last = [], {}
    for t in times:
        raw = track[f"{t}"] if f"{t}" in track else track[str(t)]
        last.update(raw)
        frames.append({"t": round(t - t0, 2), "ball": [round(c, 2) for c in last.get("ball", (0, 0))],
                       "players": [[round(c, 2) for c in last.get(pid, (0, 0))] for pid in on_pitch]})

    records = [{"t": t - t0, "elem": e, "team": team_key.get, "person": lambda pid: pid if pid in index else None}
               for t, e in events_xml]
    events = convert_events(frames, HZ, players, records)

    out_teams = []
    for tid, key in sorted(team_key.items(), key=lambda kv: kv[1]):
        name = teams[tid]["name"]
        out_teams.append({"id": key, "name": name, "call": "Bayern" if "Bayern" in name else "Köln",
                          "short": "BAY" if "Bayern" in name else "KOE",
                          "color": "#e8434a" if "Bayern" in name else "#f2f2f2"})
    return {"seed": "dfl", "duration": t1 - t0, "t0": t0, "hz": HZ, "synthetic": False,
            "source": "DFL open data, Köln–Bayern 2022/23 (Bassek et al. 2025, CC BY 4.0)",
            "pitch": {"length": 105.0, "width": 68.0}, "teams": out_teams, "players": players,
            "events": events, "frames": frames}
