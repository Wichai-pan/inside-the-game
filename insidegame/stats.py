"""Full-match statistics from DFL tracking and event data, built on kloppy and databallpy.

- kloppy reads the event feed and orients every action so its team attacks left to right, which
  the expected-threat (xT) grid needs.
- databallpy reads the 25 Hz tracking feed (with ball status and possession), smooths velocities and
  measures distance by speed band and the pressure on a player at a given frame.
- The DFL event XML supplies attributes neither library exposes (official xG, tackle outcomes, assists).
- xT uses Karun Singh's open 12x8 grid (https://karun.in/blog/expected-threat.html).

    .venv/bin/python -m insidegame.stats
"""
import json
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
HSR, SPRINT, VMAX = 5.5, 7.0, 12.5  # m/s: high-speed running (19.8 km/h), sprinting (25.2 km/h), cap
SEGMENTS = [(1, 0, 15), (1, 15, 30), (1, 30, 99), (2, 45, 60), (2, 60, 75), (2, 75, 999)]


def xt_grid():
    # Karun Singh's open 12x8 expected-threat grid (https://karun.in/blog/expected-threat.html), shipped with the package
    return np.array(json.loads((Path(__file__).with_name("resources") / "open_xt_12x8_v1.json").read_text()))


def xt_value(grid, x, y):
    return grid[min(7, max(0, int(y * 8))), min(11, max(0, int(x * 12)))]


def raw_attributes():
    """Per DFL EventId: official shot xG/outcome/assist, tackle winners, recoveries, fouls, crosses, plus
    the event's x as a 0-1 share of the pitch in the acting team's attacking direction."""
    root = ET.parse(DATA / "events.xml").getroot()
    left = {}  # section -> team defending the left goal
    second_start = None
    for e in root.findall("Event"):
        if len(e) and e[0].tag == "KickOff" and e[0].get("GameSection"):
            left[e[0].get("GameSection")] = e[0].get("TeamLeft")
            if e[0].get("GameSection") == "secondHalf":
                second_start = e.get("EventTime")
    out = {}
    for e in root.findall("Event"):
        if not len(e):
            continue
        main, eid = e[0], e.get("EventId")
        rec = {"tag": main.tag, "section": "secondHalf" if second_start and e.get("EventTime") >= second_start else "firstHalf"}
        for sub in main.iter():
            if sub.tag == "ShotAtGoal":
                res = sub[0].tag if len(sub) else ""
                rec.update(shot=True, xg=float(sub.get("xG", 0)), outcome=res, player=sub.get("Player"),
                           team=sub.get("Team"), assist=sub.get("AssistShotAtGoal"),
                           head=sub.get("TypeOfShot") == "head", penalty=sub.get("BuildUp") == "penaltyKick")
            elif sub.tag == "Cross":
                rec["cross"] = True
        if main.tag == "TacklingGame" and main.get("PossessionChange") == "true":
            rec.update(tackle_won=main.get("Winner"), team=main.get("WinnerTeam"))
        if main.tag == "BallClaiming":
            rec.update(claim=main.get("Player"), team=main.get("Team"), claim_type=main.get("Type"))
        if main.tag == "Foul":
            rec.update(foul=main.get("Fouler"), team=main.get("TeamFouler"))
        if main.tag == "Caution":
            rec.update(card=main.get("Player"), team=main.get("Team"), color=main.get("CardColor"))
        if e.get("X-Position") and rec.get("team"):
            x = float(e.get("X-Position")) / 105
            rec["x"] = x if left.get(rec["section"]) == rec["team"] else 1 - x
        out[eid] = rec
    return out


def minute(event):
    return event.timestamp.total_seconds() / 60 + (45 if event.period.id == 2 else 0)


def segment_of(period, mins):
    for i, (p, lo, hi) in enumerate(SEGMENTS):
        if p == period and lo <= mins < hi:
            return i
    return None


def segment_label(i):
    p, lo, hi = SEGMENTS[i]
    return f"{lo}-{hi if hi < 99 else ('45+' if p == 1 else '90+')}"


def build():
    from databallpy import get_saved_game, get_game
    from kloppy import sportec

    saved = DATA / "dbp_game"
    game = get_saved_game(str(saved)) if saved.exists() else get_game(
        tracking_data_loc=str(DATA / "tracking.xml"), tracking_metadata_loc=str(DATA / "meta.xml"),
        event_data_loc=str(DATA / "events.xml"), event_metadata_loc=str(DATA / "meta.xml"),
        tracking_data_provider="dfl", event_data_provider="dfl", check_quality=False)
    td = game.tracking_data
    teams = {game.home_team_id: {"name": game.home_team_name, "side": "home"},
             game.away_team_id: {"name": game.away_team_name, "side": "away"}}
    side_team = {v["side"]: k for k, v in teams.items()}

    from .dfl import metadata
    short = {pid: p["name"] for pid, p in metadata()[1].items()}
    roster = {}
    for side, frame in (("home", game.home_players), ("away", game.away_players)):
        for _, p in frame.iterrows():
            roster[p["id"]] = {"name": short.get(p["id"], p["full_name"]), "team": side_team[side], "number": int(p["shirt_num"]),
                               "position": p["position"], "column": f"{side}_{int(p['shirt_num'])}"}

    # --- Tracking: minutes, distance, high-speed running, sprints, top speed ------------------
    columns = [p["column"] for p in roster.values() if f"{p['column']}_x" in td.columns]
    td.add_velocity(column_ids=columns, filter_type="savitzky_golay", max_velocity=VMAX, allow_overwrite=True)
    dist = td.get_covered_distance(columns, velocity_intervals=((HSR, VMAX), (SPRINT, VMAX)))
    fps = td.frame_rate
    physical = {}
    for pid, p in roster.items():
        c = p["column"]
        if c not in dist.index:
            continue
        v = td[f"{c}_velocity"].to_numpy()
        on = ~np.isnan(td[f"{c}_x"].to_numpy())
        fast = np.nan_to_num(v) >= SPRINT
        edges = np.flatnonzero(np.diff(np.r_[0, fast.astype(int), 0]))
        runs = (edges[1::2] - edges[::2]) / fps
        physical[pid] = {"minutes": round(on.sum() / fps / 60), "distance_km": round(dist.loc[c, "total_distance"] / 1000, 2),
                         "hsr_m": round(dist.loc[c, f"total_distance_velocity_{HSR}_{VMAX}"]),
                         "sprint_m": round(dist.loc[c, f"total_distance_velocity_{SPRINT}_{VMAX}"]),
                         "sprints": int((runs >= 1.0).sum()), "top_speed_kmh": round(np.nanmax(v) * 3.6, 1)}

    # Team possession and field tilt from ball-in-play frames.
    alive = td["ball_status"] == "alive"
    period = td["period_id"].to_numpy()
    start = dict(zip(game.periods["period_id"], game.periods["start_frame"]))
    mins = (td["frame"].to_numpy() - np.vectorize(lambda p: start.get(p, 0))(period)) / fps / 60 + np.where(period == 2, 45, 0)
    seg = np.array([segment_of(p, m) if p in (1, 2) else None for p, m in zip(period, mins)], dtype=object)
    poss = td["team_possession"].to_numpy()
    ball_x = td["ball_x"].to_numpy()
    home_dir = np.vectorize(lambda p: _home_direction(td, game, p) if p in (1, 2) else 1)(period)

    # --- Events (kloppy, oriented to the acting team) ------------------------------------
    ev = sportec.load_event(event_data=str(DATA / "events.xml"), meta_data=str(DATA / "meta.xml"))
    ev = ev.filter(lambda e: e.team is not None).transform(to_orientation="ACTION_EXECUTING_TEAM")
    raw = raw_attributes()
    grid = xt_grid()
    frame_index = dict(zip(td["frame"].to_numpy(), td.index.to_numpy()))

    team_stats = {t: defaultdict(float) for t in teams}
    seg_stats = {t: [defaultdict(float) for _ in SEGMENTS] for t in teams}
    player_ev = defaultdict(lambda: defaultdict(float))
    goals, shots_log = [], []
    pressure_on = defaultdict(list)
    for e in ev.records:
        t, pid = e.team.team_id, e.player.player_id if e.player else None
        m = minute(e)
        s = segment_of(e.period.id, m)
        r = raw.get(e.event_id, {})
        kind = e.event_type.name
        S, P = team_stats[t], player_ev[pid]
        G = seg_stats[t][s] if s is not None else defaultdict(float)
        if kind == "PASS":
            ok = e.result is not None and e.result.name == "COMPLETE"
            for d in (S, P, G):
                d["passes"] += 1
                d["passes_ok"] += ok
            if r.get("cross"):
                S["crosses"] += 1
                P["crosses"] += 1
            if ok and e.receiver_coordinates:
                x0, y0 = e.coordinates.x, e.coordinates.y
                x1, y1 = e.receiver_coordinates.x, e.receiver_coordinates.y
                gain = xt_value(grid, x1, y1) - xt_value(grid, x0, y0)
                for d in (S, P, G):
                    d["xt"] += gain
                if x0 < 2 / 3 <= x1:
                    for d in (S, P, G):
                        d["final_third_entries"] += 1
                if (x1 - x0) * 105 >= 10 and x1 > 0.5:  # at least 10 m forward, ending in the opponent half
                    P["progressive_passes"] += 1
                    S["progressive_passes"] += 1
            if pid in roster and roster[pid]["column"] in columns:
                f = round(start[e.period.id] + e.timestamp.total_seconds() * fps)
                if f in frame_index:
                    pressure_on[t].append(td.get_pressure_on_player(frame_index[f], roster[pid]["column"], pitch_size=[105, 68]))
        elif kind == "SHOT":
            xg = r.get("xg", 0.0)
            goal = r.get("outcome") == "SuccessfulShot"
            on_target = r.get("outcome") in ("SuccessfulShot", "SavedShot")
            for d in (S, P, G):
                d["shots"] += 1
                d["xg"] += xg
                d["on_target"] += on_target
                d["goals"] += goal
            if r.get("assist") in roster:
                player_ev[r["assist"]]["key_passes"] += 1
                if goal:
                    player_ev[r["assist"]]["assists"] += 1
            entry = {"minute": round(m, 1), "team": teams[t]["name"], "player": roster.get(pid, {}).get("name"),
                     "xg": round(xg, 3), "outcome": r.get("outcome"), "head": r.get("head", False),
                     "penalty": r.get("penalty", False),
                     "assist": roster.get(r.get("assist"), {}).get("name")}
            shots_log.append(entry)
            if goal:
                goals.append(entry)
    # Defensive actions come straight from the DFL feed: kloppy does not attach players to tackles.
    defensive = []
    for r in raw.values():
        for key, stat in (("tackle_won", "tackles_won"), ("claim", "ball_recoveries"), ("foul", "fouls")):
            if r.get(key) in roster and r.get("team") in teams:
                team_stats[r["team"]][stat] += 1
                player_ev[r[key]][stat] += 1
                if "x" in r:
                    defensive.append((r["team"], r["x"]))

    # PPDA: opponent passes in their own 60% per defensive action of ours in that zone (our x > 0.4).
    ppda = {}
    for t in teams:
        opp = next(o for o in teams if o != t)
        opp_passes = sum(1 for e in ev.records if e.team.team_id == opp and e.event_type.name == "PASS" and e.coordinates.x < 0.6)
        actions = sum(1 for team, x in defensive if team == t and x > 0.4)
        ppda[t] = round(opp_passes / actions, 1) if actions else None

    # --- Assemble tables -----------------------------------------------------------------
    def possession(mask):
        h = ((poss == "home") & mask & alive.to_numpy()).sum()
        a = ((poss == "away") & mask & alive.to_numpy()).sum()
        return {side_team["home"]: round(100 * h / max(h + a, 1)), side_team["away"]: round(100 * a / max(h + a, 1))}

    def tilt(mask):
        """Share of ball-in-play time each team had the ball in its attacking third."""
        att_home = alive.to_numpy() & mask & (poss == "home") & (ball_x * home_dir > 105 / 6)
        att_away = alive.to_numpy() & mask & (poss == "away") & (ball_x * home_dir < -105 / 6)
        h, a = att_home.sum(), att_away.sum()
        return {side_team["home"]: round(100 * h / max(h + a, 1)), side_team["away"]: round(100 * a / max(h + a, 1))}

    full_mask = np.ones(len(td), dtype=bool)
    team_table = []
    for t, meta in teams.items():
        S = team_stats[t]
        squad = [pid for pid in physical if roster[pid]["team"] == t]
        team_table.append({
            "team": meta["name"], "goals": int(S["goals"]), "xg": round(S["xg"], 2), "shots": int(S["shots"]),
            "on_target": int(S["on_target"]), "possession_pct": possession(full_mask)[t], "field_tilt_pct": tilt(full_mask)[t],
            "passes": int(S["passes"]), "pass_pct": round(100 * S["passes_ok"] / max(S["passes"], 1)),
            "progressive_passes": int(S["progressive_passes"]), "final_third_entries": int(S["final_third_entries"]),
            "crosses": int(S["crosses"]), "xt_from_passes": round(S["xt"], 2), "tackles_won": int(S["tackles_won"]),
            "ball_recoveries": int(S["ball_recoveries"]), "fouls": int(S["fouls"]), "ppda": ppda[t],
            "pressure_on_own_passes": round(float(np.mean(pressure_on[t])), 2) if pressure_on[t] else None,
            "distance_km": round(sum(physical[p]["distance_km"] for p in squad), 1),
            "hsr_km": round(sum(physical[p]["hsr_m"] for p in squad) / 1000, 1),
            "sprints": sum(physical[p]["sprints"] for p in squad)})

    segments = []
    for i in range(len(SEGMENTS)):
        mask = seg == i
        row = {"minutes": segment_label(i)}
        for t, meta in teams.items():
            G = seg_stats[t][i]
            key = meta["side"]
            row[key] = {"possession_pct": possession(mask)[t], "field_tilt_pct": tilt(mask)[t], "passes": int(G["passes"]),
                        "pass_pct": round(100 * G["passes_ok"] / max(G["passes"], 1)), "shots": int(G["shots"]),
                        "xg": round(G["xg"], 2), "xt": round(G["xt"], 2), "final_third_entries": int(G["final_third_entries"]),
                        "goals": int(G["goals"])}
        segments.append(row)

    players = []
    for pid, p in roster.items():
        if pid not in physical or physical[pid]["minutes"] == 0:
            continue
        E = player_ev.get(pid, {})
        passes = int(E.get("passes", 0))
        players.append({"player": p["name"], "team": teams[p["team"]]["name"], "number": p["number"], "position": p["position"],
                        **physical[pid], "passes": passes,
                        "pass_pct": round(100 * E.get("passes_ok", 0) / passes) if passes else None,
                        "progressive_passes": int(E.get("progressive_passes", 0)), "xt": round(E.get("xt", 0), 3),
                        "shots": int(E.get("shots", 0)), "xg": round(E.get("xg", 0), 2), "goals": int(E.get("goals", 0)),
                        "key_passes": int(E.get("key_passes", 0)), "assists": int(E.get("assists", 0)),
                        "tackles_won": int(E.get("tackles_won", 0)), "ball_recoveries": int(E.get("ball_recoveries", 0))})
    players.sort(key=lambda r: (r["team"], -r["minutes"], r["number"]))
    return {"match": f"{game.home_team_name} {game.home_score}-{game.away_score} {game.away_team_name}",
            "source": "DFL open data (Bassek et al. 2025, CC BY 4.0); xT grid: Karun Singh (open_xt_12x8_v1)",
            "teams": team_table, "segments": segments, "players": players, "goals": goals, "shots": shots_log,
            "definitions": {"hsr": "distance above 19.8 km/h", "sprints": "runs above 25.2 km/h lasting at least 1 s",
                            "field_tilt": "share of ball-in-play time with the ball in that team's attacking third",
                            "ppda": "opponent passes in their own 60% of the pitch per defensive action (tackle won, recovery, foul) there",
                            "xt": "expected threat added by completed passes (open 12x8 grid)",
                            "pressure_on_own_passes": "databallpy pressure on the passer at the moment of the pass (higher = more pressed)"}}


_DIRECTION = {}


def _home_direction(td, game, period):
    """+1 if the home side attacks +x in this period (its keeper stands on the left), else -1."""
    if period not in _DIRECTION:
        gk = next(f"home_{int(p['shirt_num'])}" for _, p in game.home_players.iterrows()
                  if p["position"] == "goalkeeper" and p["starter"])
        xs = td[td["period_id"] == period][f"{gk}_x"].dropna()
        _DIRECTION[period] = 1 if xs.iloc[: 25 * 60].mean() < 0 else -1
    return _DIRECTION[period]


def from_match(match):
    """The same statistics computed from a pipeline match bundle (5 Hz positions + converted events), so
    stitched synthetic matches get a stats layer too. Team "a" attacks +x, team "b" attacks -x.

    Differences from `build()`: velocities come from 1 s displacements at 5 Hz, restart frames between
    stitched spells are excluded from possession, and pressure on the passer is replaced by the distance
    to the nearest opponent when the pass is played.
    """
    hz, frames, events = match["hz"], match["frames"], sorted(match["events"], key=lambda e: e["t"])
    teams = {t["id"]: t for t in match["teams"]}
    names = {p["id"]: p for p in match["players"]}
    direction = {"a": 1, "b": -1}
    grid = xt_grid()
    pos = np.array([f["players"] for f in frames], float)      # frames x players x 2
    ball = np.array([f["ball"] for f in frames], float)
    live = np.array([not f.get("dead") for f in frames])
    n = len(frames)
    # segments: six equal blocks of whole minutes
    block = max(1, int(np.ceil(match["duration"] / 60 / 6)))
    seg_of = lambda t: min(int(t / 60 // block), 5)
    seg_labels = [f"{i * block}-{min((i + 1) * block, int(np.ceil(match['duration'] / 60)))}" for i in range(6)]

    # possession per frame from the last on-ball event
    owner = np.full(n, "", dtype=object)
    on_ball = [e for e in events if e["kind"] not in ("pressure", "foul", "clearance")]
    j, cur = 0, ""
    for k in range(n):
        while j < len(on_ball) and on_ball[j]["t"] <= k / hz:
            cur = on_ball[j]["team"]
            j += 1
        owner[k] = cur if live[k] else ""
    seg_idx = np.array([seg_of(k / hz) for k in range(n)])

    def share(mask, attacking_third=False):
        out = {}
        for t in teams:
            m = mask & (owner == t)
            if attacking_third:
                m &= ball[:, 0] * direction[t] > 105 / 6
            out[t] = m.sum()
        total = sum(out.values()) or 1
        return {t: round(100 * v / total) for t, v in out.items()}

    # physical
    # Physical numbers use live play only: restart blends between stitched spells are not real running.
    step = np.r_[np.zeros((1, pos.shape[1])), np.hypot(*np.moveaxis(np.diff(pos, axis=0), 2, 0))]
    speed = np.zeros_like(step)
    speed[hz:] = np.hypot(*np.moveaxis(pos[hz:] - pos[:-hz], 2, 0))  # metres per second over 1 s
    window_live = np.convolve(live.astype(int), np.ones(hz + 1, int), "full")[:n] == hz + 1
    step[~live] = 0
    speed[~window_live] = 0
    physical = {}
    for i, p in enumerate(match["players"]):
        v = speed[:, i]
        fast = v >= SPRINT
        edges = np.flatnonzero(np.diff(np.r_[0, fast.astype(int), 0]))
        runs = (edges[1::2] - edges[::2]) / hz
        physical[p["id"]] = {"minutes": round(live.sum() / hz / 60), "distance_km": round(step[:, i].sum() / 1000, 2),
                             "hsr_m": round(step[:, i][v >= HSR].sum()), "sprint_m": round(step[:, i][fast].sum()),
                             "sprints": int((runs >= 1.0).sum()), "top_speed_kmh": round(float(v.max()) * 3.6, 1)}

    def xt(x, y, team):
        d = direction[team]
        return xt_value(grid, (x * d + 52.5) / 105, (y * d + 34) / 68)

    T = {t: defaultdict(float) for t in teams}
    G = {t: [defaultdict(float) for _ in range(6)] for t in teams}
    P = defaultdict(lambda: defaultdict(float))
    space = {t: [] for t in teams}
    shots_log, goals, score = [], [], {"a": 0, "b": 0}
    defensive = []
    index = {p["id"]: i for i, p in enumerate(match["players"])}
    for e in events:
        t, pid, k = e["team"], e["actor"], e["kind"]
        S, Q, Gs = T[t], P[pid], G[t][seg_of(e["t"])]
        if k == "pass":
            ok = e["outcome"] == "complete"
            for d in (S, Q, Gs):
                d["passes"] += 1
                d["passes_ok"] += ok
            if e.get("cross"):
                S["crosses"] += 1
                Q["crosses"] += 1
            f = min(n - 1, round(e["t"] * hz))
            opp = [index[o["id"]] for o in match["players"] if o["team"] != t]
            space[t].append(float(np.min(np.hypot(*(pos[f, opp] - pos[f, index[pid]]).T))))
            if ok:
                gain = xt(e["end_x"], e["end_y"], t) - xt(e["x"], e["y"], t)
                for d in (S, Q, Gs):
                    d["xt"] += gain
                x0, x1 = e["x"] * direction[t], e["end_x"] * direction[t]
                if x0 < 105 / 6 <= x1:
                    for d in (S, Q, Gs):
                        d["final_third_entries"] += 1
                if x1 - x0 >= 10 and x1 > 0:
                    S["progressive_passes"] += 1
                    Q["progressive_passes"] += 1
        elif k == "shot":
            goal, on = e["outcome"] == "goal", e["outcome"] in ("goal", "saved")
            for d in (S, Q, Gs):
                d["shots"] += 1
                d["xg"] += e.get("xg", 0)
                d["on_target"] += on
                d["goals"] += goal
            if e.get("assist"):
                P[e["assist"]]["key_passes"] += 1
                P[e["assist"]]["assists"] += goal
            entry = {"minute": round(e["t"] / 60, 1), "team": teams[t]["name"], "player": names[pid]["name"],
                     "xg": round(e.get("xg", 0), 3), "outcome": {"goal": "SuccessfulShot", "saved": "SavedShot",
                                                               "blocked": "BlockedShot"}.get(e["outcome"], "ShotWide"),
                     "head": e.get("body_part") == "head", "penalty": e.get("penalty", False),
                     "assist": names[e["assist"]]["name"] if e.get("assist") else None}
            shots_log.append(entry)
            if goal:
                goals.append(entry)
        elif k in ("tackle", "interception", "foul"):
            stat = {"tackle": "tackles_won", "interception": "ball_recoveries", "foul": "fouls"}[k]
            S[stat] += 1
            Q[stat] += 1
            defensive.append((t, e["x"] * direction[t]))

    ppda = {}
    for t in teams:
        o = "b" if t == "a" else "a"
        opp_passes = sum(1 for e in events if e["team"] == o and e["kind"] == "pass" and e["x"] * direction[o] < 63 - 52.5)
        actions = sum(1 for team, x in defensive if team == t and x > 42 - 52.5)
        ppda[t] = round(opp_passes / actions, 1) if actions else None

    full = live.copy()
    team_table = []
    for t, meta in teams.items():
        S = T[t]
        squad = [p["id"] for p in match["players"] if p["team"] == t]
        team_table.append({
            "team": meta["name"], "call": meta.get("call", meta["name"]), "color": meta.get("color"),
            "goals": int(S["goals"]), "xg": round(S["xg"], 2), "shots": int(S["shots"]), "on_target": int(S["on_target"]),
            "possession_pct": share(full)[t], "field_tilt_pct": share(full, True)[t], "passes": int(S["passes"]),
            "pass_pct": round(100 * S["passes_ok"] / max(S["passes"], 1)), "progressive_passes": int(S["progressive_passes"]),
            "final_third_entries": int(S["final_third_entries"]), "crosses": int(S["crosses"]),
            "xt_from_passes": round(S["xt"], 2), "tackles_won": int(S["tackles_won"]),
            "ball_recoveries": int(S["ball_recoveries"]), "fouls": int(S["fouls"]), "ppda": ppda[t],
            "opponent_distance_at_pass_m": round(float(np.median(space[t])), 1) if space[t] else None,
            "distance_km": round(sum(physical[p]["distance_km"] for p in squad), 1),
            "hsr_km": round(sum(physical[p]["hsr_m"] for p in squad) / 1000, 1),
            "sprints": sum(physical[p]["sprints"] for p in squad)})
    segments = []
    for i in range(6):
        mask = live & (seg_idx == i)
        row = {"minutes": seg_labels[i], "seconds": round(mask.sum() / hz)}
        for t, side in (("a", "home"), ("b", "away")):
            g = G[t][i]
            row[side] = {"possession_pct": share(mask)[t], "field_tilt_pct": share(mask, True)[t], "passes": int(g["passes"]),
                         "pass_pct": round(100 * g["passes_ok"] / max(g["passes"], 1)), "shots": int(g["shots"]),
                         "xg": round(g["xg"], 2), "xt": round(g["xt"], 2), "final_third_entries": int(g["final_third_entries"]),
                         "goals": int(g["goals"])}
        segments.append(row)
    players = []
    for p in match["players"]:
        Q = P.get(p["id"], {})
        passes = int(Q.get("passes", 0))
        players.append({"player": p["name"], "team": teams[p["team"]]["name"], "number": p["number"], "position": p["role"],
                        **physical[p["id"]], "passes": passes,
                        "pass_pct": round(100 * Q.get("passes_ok", 0) / passes) if passes else None,
                        "progressive_passes": int(Q.get("progressive_passes", 0)), "xt": round(Q.get("xt", 0), 3),
                        "shots": int(Q.get("shots", 0)), "xg": round(Q.get("xg", 0), 2), "goals": int(Q.get("goals", 0)),
                        "key_passes": int(Q.get("key_passes", 0)), "assists": int(Q.get("assists", 0)),
                        "tackles_won": int(Q.get("tackles_won", 0)), "ball_recoveries": int(Q.get("ball_recoveries", 0))})
    a, b = match["teams"]
    return {"match": f"{a['name']} {int(T['a']['goals'])}-{int(T['b']['goals'])} {b['name']}", "source": match["source"],
            "duration_minutes": round(match["duration"] / 60, 1), "teams": team_table, "segments": segments,
            "players": players, "goals": goals, "shots": shots_log,
            "definitions": {"hsr": "distance above 19.8 km/h", "sprints": "runs above 25.2 km/h lasting at least 1 s",
                            "field_tilt": "share of ball-in-play time with the ball in that team's attacking third",
                            "ppda": "opponent passes in their own 60% of the pitch per defensive action there",
                            "xt": "expected threat added by completed passes (open 12x8 grid)",
                            "opponent_distance_at_pass_m": "median distance from the passer to the nearest opponent"}}


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--match", help="Pipeline match bundle (e.g. docs/synthetic/remix-42.json); omit for the real DFL match")
    args = parser.parse_args()
    if args.match:
        path = Path(args.match)
        stats = from_match(json.loads(path.read_text()))
        out = path.with_name(path.stem + "-stats.json")
    else:
        stats = build()
        out = ROOT / "docs" / "real" / "dfl-full-stats.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=1))
    print(out)


if __name__ == "__main__":
    main()
