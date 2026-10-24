"""Synthetic matches stitched from real movement (see DATA_PLAN.md).

Each source match (DFL open data, CC BY 4.0) is cut into live-ball sequences at dead balls. A seeded
generator picks sequences from all sources, assigns each side to one of two fictional clubs, maps the
real players onto fictional players by playing position, and joins the sequences into a new match
with short blended restarts. The seed also scripts the storyline (how many goals, for whom, when).
The result is the same match format as `dfl.load`, so the whole pipeline runs on it unchanged.

    .venv/bin/python -m insidegame.remix --seed 7 --duration 600
"""
import argparse
import json
import random
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from .dfl import convert_events
from .simulate import FORMATION, ROLES, TEAMS

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "data" / "dfl"
HZ, STEP = 5, 5           # output rate, and source frames per output frame (25 Hz -> 5 Hz)
MIN_SEQ, LEAD_IN, BLEND, MAX_FILLER = 12.0, 1.0, 2.4, 70.0   # seconds
RESTART_SPEED, MAX_STEP = 7.0, 3.0  # m/s players jog to a restart; m per 0.2 s above which a sample is a glitch
SCRIPTS = [(2, 1), (1, 1), (1, 2), (2, 0), (0, 1), (3, 2), (1, 0), (2, 2)]


def _is_goal(e):
    shot = e.find(".//ShotAtGoal")
    return shot is not None and len(shot) and shot[0].tag == "SuccessfulShot"


class Source:
    """One real match: 5 Hz positions in databallpy's normalised frame (home attacks +x) and its events."""

    def __init__(self, match_id):
        from databallpy import get_saved_game

        self.id = match_id
        folder = SOURCES / match_id
        game = get_saved_game(str(folder / "dbp_game"))
        td = game.tracking_data
        td = td[td["frame"] % STEP == 0].reset_index(drop=True)
        self.team_side = {game.home_team_id: "home", game.away_team_id: "away"}
        self.players = {}
        for side, frame in (("home", game.home_players), ("away", game.away_players)):
            for _, p in frame.iterrows():
                col = f"{side}_{int(p['shirt_num'])}"
                if f"{col}_x" in td.columns:
                    self.players[p["id"]] = {"side": side, "col": col, "gk": p["position"] == "goalkeeper"}
        self.xy = {pid: np.stack([td[f"{p['col']}_x"].to_numpy(float), td[f"{p['col']}_y"].to_numpy(float)], 1)
                   for pid, p in self.players.items()}
        self.ball = np.stack([td["ball_x"].to_numpy(float), td["ball_y"].to_numpy(float)], 1)
        self.alive = (td["ball_status"] == "alive").to_numpy()
        self.period = td["period_id"].to_numpy()
        # rows between the halves carry no timestamp; carry the last one so the series stays sorted
        times = td["datetime"].dt.tz_convert("UTC").dt.tz_localize(None).ffill().bfill().to_numpy()
        self.events = []  # (row, element)
        for e in ET.parse(folder / "events.xml").getroot().findall("Event"):
            if not len(e) or e[0].tag == "Delete":
                continue
            stamp = e.get("CalculatedTimestamp") or e.get("EventTime")
            when = pd.Timestamp(stamp).tz_convert("UTC").tz_localize(None).to_datetime64()
            row = int(np.searchsorted(times, when))
            if 0 <= row < len(times):
                self.events.append((row, e))
        self.events.sort(key=lambda x: x[0])
        self.sequences = self._cut()

    def _cut(self):
        edges = np.flatnonzero(np.diff(np.r_[0, self.alive.astype(int), 0]))
        out = []
        for start, end in zip(edges[::2], edges[1::2]):
            spell_goal = any(start - 3 * HZ <= r < end + 3 and _is_goal(e) for r, e in self.events)
            spell_kickoff = any(start - 3 * HZ <= r < start + 2 * HZ and e[0].tag == "KickOff" for r, e in self.events)
            if (end - start) / HZ < (0.4 if spell_goal else 3.0 if spell_kickoff else MIN_SEQ):
                continue
            start = max(0, start - int((3.0 if spell_goal else LEAD_IN) * HZ))  # show the set-up of a quick goal
            mid = (start + end) // 2
            on = {pid for pid, xy in self.xy.items() if not np.isnan(xy[start:end]).all(1).any()}
            if sum(self.players[p]["side"] == "home" for p in on) != 11 or sum(self.players[p]["side"] == "away" for p in on) != 11:
                continue  # skip spells with a missing player (red card, substitution mid-spell)
            if self.period[mid] not in (1, 2):
                continue
            evs = [(min(r, end - 1), e)
                   for r, e in self.events if start <= r < end + 3]  # a goal is stamped as the ball goes dead
            restart = [e for r, e in self.events if start - 3 * HZ <= r < start + 2 * HZ]
            goals = [e.find(".//ShotAtGoal") for _, e in evs if _is_goal(e)]
            out.append({"source": self, "start": int(start), "end": int(end), "players": sorted(on), "events": evs,
                        "goal_team": self.team_side.get(goals[0].get("Team")) if goals else None,
                        "goals": len(goals), "shots": sum(e.find(".//ShotAtGoal") is not None for _, e in evs),
                        "kickoff_team": next((self.team_side.get(e[0].get("Team") or (e[0].find("Play").get("Team") if e[0].find("Play") is not None else None))
                                              for e in restart if e[0].tag == "KickOff"), None)})
        return out


def fictional_roster(rng):
    players, teams = [], []
    for team in TEAMS:
        names = team["names"][:]
        rng.shuffle(names)
        for n in range(11):
            players.append({"id": f"{team['id']}{n}", "team": team["id"], "number": n + 1, "role": ROLES[n], "name": names[n]})
        teams.append({"id": team["id"], "name": team["name"], "call": team["name"].replace(" United", ""), "short": team["short"],
                      "color": team["color"]})
    return players, teams


def assign(seq, side, fictional_team, flip):
    """Map the real players of one side onto fictional slots by playing position (Hungarian matching)."""
    src = seq["source"]
    pids = [p for p in seq["players"] if src.players[p]["side"] == side]
    sign = -1 if flip else 1
    d = 1 if fictional_team == "a" else -1
    gk = next((p for p in pids if src.players[p]["gk"]), None)
    if gk is None:  # fall back to the deepest player
        gk = min(pids, key=lambda p: np.nanmean(src.xy[p][seq["start"]:seq["end"], 0]) * sign * d)
    outfield = [p for p in pids if p != gk]
    mean = np.array([np.nanmean(src.xy[p][seq["start"]:seq["end"]], 0) * sign for p in outfield])
    mean -= mean.mean(0)
    slots = np.array([[x * d, y * d] for x, y in FORMATION[1:]], float)
    slots -= slots.mean(0)
    cost = ((mean[:, None, :] - slots[None, :, :]) ** 2).sum(2)
    rows, cols = linear_sum_assignment(cost)
    mapping = {gk: f"{fictional_team}0"}
    for r, c in zip(rows, cols):
        mapping[outfield[r]] = f"{fictional_team}{c + 1}"
    return mapping


def plan(sources, seed, duration):
    """Pick and order sequences so the match tells the scripted story."""
    rng = random.Random(seed)
    seqs = [s for src in sources for s in src.sequences]
    goals_pool = [s for s in seqs if s["goals"] == 1]
    kickoffs = [s for s in seqs if s["kickoff_team"] and not s["goals"]]
    others = [s for s in seqs if not s["goals"] and not s["kickoff_team"]
              and (s["end"] - s["start"]) / HZ <= MAX_FILLER]  # long spells would crowd out the story
    rng.shuffle(goals_pool), rng.shuffle(kickoffs), rng.shuffle(others)
    script = rng.choice([s for s in SCRIPTS if sum(s) <= len(goals_pool)])
    scorers = ["a"] * script[0] + ["b"] * script[1]
    rng.shuffle(scorers)
    goal_times = sorted(rng.uniform(90, duration - 40) for _ in scorers)

    chosen, t = [], 0.0
    def take(seq, team_for_home):
        nonlocal t
        chosen.append((seq, team_for_home))
        t += (seq["end"] - seq["start"]) / HZ + BLEND * 2  # restarts average longer than the minimum blend

    first = kickoffs.pop() if kickoffs else others.pop()
    take(first, rng.choice("ab"))
    gi = 0
    while (t < duration - 10 and others) or gi < len(scorers):
        if gi < len(scorers) and (t >= goal_times[gi] or t >= duration - 40 or not others):
            g = goals_pool.pop()
            scorer = scorers[gi]
            take(g, scorer if g["goal_team"] == "home" else ("b" if scorer == "a" else "a"))
            conceding = "b" if scorer == "a" else "a"
            if kickoffs:  # the conceding side restarts from the centre spot
                k = kickoffs.pop()
                take(k, conceding if k["kickoff_team"] == "home" else scorer)
            gi += 1
        elif others:
            take(others.pop(), rng.choice("ab"))
        else:
            break
    return chosen, script


def stitch(chosen, players, rng):
    index = {p["id"]: i for i, p in enumerate(players)}
    frames, records, provenance = [], [], []
    for n, (seq, home_team) in enumerate(chosen):
        src = seq["source"]
        flip = home_team == "b"   # databallpy's home side attacks +x; club "a" must attack +x
        sign = -1 if flip else 1
        away_team = "b" if home_team == "a" else "a"
        mapping = {**assign(seq, "home", home_team, flip), **assign(seq, "away", away_team, flip)}
        block = []
        for r in range(seq["start"], seq["end"]):
            pos = [[0.0, 0.0]] * len(players)
            for pid, fid in mapping.items():
                xy = src.xy[pid][r]
                prev = np.array(block[-1]["players"][index[fid]]) * sign if block else None
                if np.isnan(xy).any() or (prev is not None and np.hypot(*(xy - prev)) > MAX_STEP):
                    xy = prev if prev is not None else np.zeros(2)  # tracking gap or glitch: hold position
                pos[index[fid]] = [round(float(xy[0]) * sign, 2), round(float(xy[1]) * sign, 2)]
            b = src.ball[r]
            block.append({"ball": [round(float(b[0]) * sign, 2), round(float(b[1]) * sign, 2)], "players": pos})
        if frames:  # dead-ball restart: players jog from the end of one spell to the start of the next
            a, z = frames[-1], block[0]
            far = max(np.hypot(pz[0] - pa[0], pz[1] - pa[1]) for pa, pz in zip(a["players"], z["players"]))
            steps = int(max(BLEND, min(10.0, far / RESTART_SPEED)) * HZ)
            for k in range(1, steps + 1):
                q = k / (steps + 1)
                q = q * q * (3 - 2 * q)  # ease in and out
                frames.append({"ball": [round(a["ball"][i] + (z["ball"][i] - a["ball"][i]) * q, 2) for i in (0, 1)],
                               "players": [[round(pa[i] + (pz[i] - pa[i]) * q, 2) for i in (0, 1)]
                                           for pa, pz in zip(a["players"], z["players"])], "dead": True})
        offset = len(frames) / HZ
        team_map = {tid: (home_team if side == "home" else away_team) for tid, side in src.team_side.items()}
        for r, e in seq["events"]:
            records.append({"t": offset + (r - seq["start"]) / HZ, "elem": e, "team": team_map.get,
                            "person": mapping.get})
        provenance.append({"t": round(offset, 1), "seconds": round(len(block) / HZ, 1), "source": src.id,
                           "period": int(src.period[seq["start"]]), "home_side_plays_as": home_team})
        frames.extend(block)
    for i, f in enumerate(frames):
        f["t"] = round(i / HZ, 2)
    records.sort(key=lambda r: r["t"])
    return frames, records, provenance


_SOURCES = {}


def load_sources(ids=None):
    ids = ids or sorted(p.name for p in SOURCES.iterdir() if (p / "dbp_game").exists())
    for i in ids:
        if i not in _SOURCES:
            _SOURCES[i] = Source(i)
    return [_SOURCES[i] for i in ids]


def generate(seed, duration=600, sources=None):
    rng = random.Random(seed)
    sources = load_sources(sources)
    players, teams = fictional_roster(rng)
    chosen, script = plan(sources, seed, duration)
    frames, records, provenance = stitch(chosen, players, rng)
    events = convert_events(frames, HZ, players, records, id_prefix=f"r{seed}-")
    return {"seed": seed, "duration": round(len(frames) / HZ, 1), "t0": 0, "hz": HZ, "synthetic": True,
            "source": "Synthetic match stitched from real movement in the DFL open data (Bassek et al. 2025, CC BY 4.0); "
                      "teams and players are fictional",
            "pitch": {"length": 105.0, "width": 68.0}, "teams": teams, "players": players,
            "events": events, "frames": frames, "script": {"a": script[0], "b": script[1]}, "provenance": provenance}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--duration", type=int, default=600)
    args = parser.parse_args()
    m = generate(args.seed, args.duration)
    goals = [e for e in m["events"] if e["kind"] == "shot" and e["outcome"] == "goal"]
    print(f"seed {m['seed']}: {m['duration']} s, {len(m['events'])} events, script {m['script']}, "
          f"goals {[(round(g['t']), g['team']) for g in goals]}, spells {len(m['provenance'])} from "
          f"{sorted({p['source'] for p in m['provenance']})}")


if __name__ == "__main__":
    main()
