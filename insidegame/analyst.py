"""Interpret a raw synthetic feed: per-event metrics, running team state and key moments.

Everything here is deterministic and causal: a value computed for an event uses only that
event, earlier events, and positions up to the event's end time. Each moment keeps the event
IDs behind every fact, so generated commentary can be checked against them.
"""
import math

HALF_X = 52.5
WINDOW = 60.0  # seconds used for "recent" possession share and pressure counts


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _direction(team):
    return 1 if team == "a" else -1


def _other(team):
    return "b" if team == "a" else "a"


def _clock(t):
    return f"{int(t // 60)}:{int(t % 60):02d}"


class Analyst:
    def __init__(self, match):
        self.m = match
        self.players = {p["id"]: p for p in match["players"]}
        self.index = {p["id"]: i for i, p in enumerate(match["players"])}
        self.teams = {t["id"]: t for t in match["teams"]}

    # -- names and positions -------------------------------------------------------------
    def name(self, pid):
        return self.players[pid]["name"]

    def team_name(self, tid):
        return self.teams[tid]["name"]

    def frame(self, t):
        hz = self.m["hz"]
        return self.m["frames"][max(0, min(len(self.m["frames"]) - 1, round(t * hz)))]

    def pos(self, pid, t):
        return tuple(self.frame(t)["players"][self.index[pid]])

    def nearest_opponent(self, team, point, t):
        f = self.frame(t)
        return min(_dist(f["players"][self.index[pid]], point)
                   for pid, p in self.players.items() if p["team"] != team)

    # -- per-event metrics ------------------------------------------------------------------
    def metrics(self, e, pressed):
        d = _direction(e["team"])
        out = {}
        if e["kind"] == "pass":
            start, end = (e["x"], e["y"]), (e["end_x"], e["end_y"])
            length = _dist(start, end)
            flight = max(e["end_t"] - e["t"], 0.1)
            gap = self.nearest_opponent(e["team"], end, e["end_t"])
            progress = (end[0] - start[0]) * d
            final_third = end[0] * d > HALF_X / 3
            difficulty = (15 + 1.1 * length + (18 if pressed else 0) + max(0, 8 - gap) * 4
                          + (12 if final_third else 0) + max(0, progress) * 0.3)
            out = {"length_m": round(length), "ball_speed_kmh": round(length / flight * 3.6),
                   "progress_m": round(progress), "receiver_space_m": round(gap, 1),
                   "difficulty": int(min(99, difficulty)), "into_final_third": final_third,
                   "under_pressure": pressed}
        elif e["kind"] == "shot":
            start, end = (e["x"], e["y"]), (e["end_x"], e["end_y"])
            flight = max(e["end_t"] - e["t"], 0.05)
            out = {"distance_m": round(_dist(start, (HALF_X * d, 0))),
                   "shot_speed_kmh": round(_dist(start, end) / flight * 3.6),
                   "xg": e["xg"], "under_pressure": pressed}
        elif e["kind"] in ("interception", "tackle"):
            out = {"metres_from_goal": round(HALF_X - e["x"] * d),
                   "in_attacking_half": e["x"] * d > 0}
        return out

    # -- facts ------------------------------------------------------------------------------
    def fact(self, e):
        """English and Chinese renderings of one event; numbers are the analyst's own metrics."""
        a, team = self.name(e["actor"]), self.teams[e["team"]]["short"]
        tgt = self.name(e["target"]) if e.get("target") else ""
        mt = e["metrics"]
        k = e["kind"]
        if k == "pass":
            ok = e["outcome"] == "complete"
            en = (f"{a} ({team}) passes to {tgt}: {'complete' if ok else 'cut out'}, "
                  f"{mt['length_m']} m at {mt['ball_speed_kmh']} km/h, difficulty {mt['difficulty']}/100")
            zh = (f"{a}（{team}）传给{tgt}，{'成功' if ok else '被断'}，"
                  f"{mt['length_m']}米，球速{mt['ball_speed_kmh']}公里/小时，难度{mt['difficulty']}/100")
        elif k == "shot":
            res_en = {"goal": "GOAL", "saved": f"saved by {self.name(e.get('keeper', e['actor']))}",
                      "wide": "off target", "blocked": f"blocked by {self.name(e.get('blocker', e['actor']))}"}
            res_zh = {"goal": "进球", "saved": f"被{self.name(e.get('keeper', e['actor']))}扑出",
                      "wide": "偏出", "blocked": f"被{self.name(e.get('blocker', e['actor']))}封堵"}
            en = (f"{a} ({team}) shoots from {mt['distance_m']} m at {mt['shot_speed_kmh']} km/h, "
                  f"xG {mt['xg']:.2f}: {res_en[e['outcome']]}")
            zh = (f"{a}（{team}）在{mt['distance_m']}米处射门，球速{mt['shot_speed_kmh']}公里/小时，"
                  f"预期进球{mt['xg']:.2f}：{res_zh[e['outcome']]}")
            if e["outcome"] == "goal":
                s = e["score_after"]
                en += f". Score: {self.team_name('a')} {s['a']}-{s['b']} {self.team_name('b')}"
                zh += f"。比分：{self.team_name('a')} {s['a']}-{s['b']} {self.team_name('b')}"
        elif k in ("interception", "tackle"):
            half_en = "in the opponent half" if mt["in_attacking_half"] else "in their own half"
            half_zh = "在对方半场" if mt["in_attacking_half"] else "在本方半场"
            what_en = "intercepts the pass" if k == "interception" else f"tackles {tgt} and wins the ball"
            what_zh = "断下传球" if k == "interception" else f"抢断{tgt}"
            en = f"{a} ({team}) {what_en} {half_en}, {mt['metres_from_goal']} m from the opponent goal"
            zh = f"{a}（{team}）{half_zh}{what_zh}，距对方球门{mt['metres_from_goal']}米"
        elif k == "pressure":
            en, zh = f"{a} ({team}) presses {tgt}", f"{a}（{team}）上前逼抢{tgt}"
        elif k == "kickoff":
            en, zh = f"{self.team_name(e['team'])} kick off", f"{self.team_name(e['team'])}开球"
        else:
            en, zh = f"{a} ({team}) restarts play", f"{a}（{team}）重新开始比赛"
        return {"event_id": e["id"], "t": e["t"], "en": en, "zh": zh}

    # -- running state ----------------------------------------------------------------------
    def run(self):
        events = [dict(e) for e in sorted(self.m["events"], key=lambda e: e["t"])]
        state = {tid: {"passes": 0, "completed": 0, "shots": 0, "on_target": 0, "goals": 0,
                       "xg": 0.0, "pressures": 0, "regains": 0, "possession_s": 0.0}
                 for tid in ("a", "b")}
        spells = []          # (team, start, end) possession spells
        holder, since = None, 0.0
        snapshots = []
        for i, e in enumerate(events):
            pressed = any(p["kind"] == "pressure" and p["target"] == e["actor"] and abs(p["t"] - e["t"]) < 0.5
                          for p in events[max(0, i - 3):i + 1])
            e["metrics"] = self.metrics(e, pressed)
            s = state[e["team"]]
            if e["kind"] == "pass":
                s["passes"] += 1
                s["completed"] += e["outcome"] == "complete"
            elif e["kind"] == "shot":
                s["shots"] += 1
                s["xg"] += e["xg"]
                s["on_target"] += e["outcome"] in ("goal", "saved")
                s["goals"] += e["outcome"] == "goal"
            elif e["kind"] == "pressure":
                s["pressures"] += 1
            elif e["kind"] in ("interception", "tackle"):
                s["regains"] += 1
            # possession: the team of any on-ball action holds the ball (pressure is off-ball)
            if e["kind"] != "pressure":
                if holder is None:
                    holder, since = e["team"], e["t"]
                elif e["team"] != holder:
                    spells.append((holder, since, e["t"]))
                    state[holder]["possession_s"] += e["t"] - since
                    holder, since = e["team"], e["t"]
            e["state"] = self.snapshot(state, spells, holder, since, e["t"])
            e["fact"] = self.fact(e)
            snapshots.append(e["state"])
        end = self.m["duration"]
        if holder:
            spells.append((holder, since, end))
            state[holder]["possession_s"] += end - since
        final = self.snapshot(state, spells[:-1], holder, since, end)
        moments = self.moments(events, spells)
        return {"events": events, "moments": moments, "final": final, "spells": spells}

    def snapshot(self, state, spells, holder, since, t):
        share = self.possession_share(spells, holder, since, 0, t)
        recent = self.possession_share(spells, holder, since, max(0, t - WINDOW), t)
        out = {"t": round(t, 2)}
        for tid in ("a", "b"):
            s = state[tid]
            out[tid] = {**{k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()
                           if k != "possession_s"},
                        "pass_pct": round(100 * s["completed"] / s["passes"]) if s["passes"] else None,
                        "possession_pct": share[tid], "recent_possession_pct": recent[tid]}
        return out

    @staticmethod
    def possession_share(spells, holder, since, t0, t1):
        held = {"a": 0.0, "b": 0.0}
        for team, s, e in list(spells) + ([(holder, since, t1)] if holder else []):
            held[team] += max(0.0, min(e, t1) - max(s, t0))
        total = held["a"] + held["b"]
        if not total:
            return {"a": 50, "b": 50}
        a = round(100 * held["a"] / total)
        return {"a": a, "b": 100 - a}

    # -- key moments ------------------------------------------------------------------------
    def possession_chain(self, events, i):
        """On-ball events of the attacking team's current spell up to event i, plus the regain."""
        team = events[i]["team"]
        chain = []
        for e in reversed(events[:i + 1]):
            if e["kind"] == "pressure":
                if e["team"] != team:
                    chain.append(e)
                continue
            if e["team"] != team:
                break
            chain.append(e)
            if e["kind"] in ("interception", "tackle", "kickoff", "goal_kick", "keeper_restart"):
                break
        return list(reversed(chain))

    def moments(self, events, spells):
        cands = []
        for i, e in enumerate(events):
            k, mt = e["kind"], e["metrics"]
            if k == "shot":
                score = 100 if e["outcome"] == "goal" else 40 + 80 * e["xg"]
                cands.append((score, i, "goal" if e["outcome"] == "goal" else "shot"))
            elif k in ("interception", "tackle"):
                pressed = any(p["kind"] == "pressure" and p["team"] == e["team"] and 0 <= e["t"] - p["t"] <= 3
                              for p in events[max(0, i - 4):i])
                if pressed:
                    cands.append((50, i, "pressure_win"))
                elif mt["metres_from_goal"] <= 40:
                    cands.append((42, i, "high_regain"))
            elif k == "pass" and e["outcome"] == "complete" and mt["difficulty"] >= 75:
                cands.append((30 + (mt["difficulty"] - 75), i, "difficult_pass"))
            if k == "pass" and e["outcome"] == "complete":
                chain = [c for c in self.possession_chain(events, i) if c["kind"] == "pass"]
                if len(chain) == 6 and all(c["outcome"] == "complete" for c in chain):
                    cands.append((38, i, "long_spell"))
            if k in ("interception", "tackle"):
                recent = [s for s in spells if e["t"] - 20 <= s[2] <= e["t"]]
                if len(recent) >= 3:
                    cands.append((33, i, "chaos"))
        chosen = []
        for score, i, kind in sorted(cands, key=lambda c: -c[0]):
            t = events[i].get("end_t", events[i]["t"])
            if all(abs(t - c["t"]) >= 10 for c in chosen) and len(chosen) < 9:
                chosen.append(self.moment(events, i, kind, spells, score))
        return sorted(chosen, key=lambda m: m["t"])

    def moment(self, events, i, kind, spells, score):
        e = events[i]
        team, other = e["team"], _other(e["team"])
        chain = self.possession_chain(events, i)
        if kind in ("pressure_win", "high_regain", "chaos"):
            chain = [p for p in events[max(0, i - 3):i] if p["kind"] == "pressure" and p["team"] == team] + [e]
            if kind == "chaos":
                chain = [x for x in events[max(0, i - 8):i + 1]
                         if x["kind"] in ("interception", "tackle") and e["t"] - x["t"] <= 20]
        if kind in ("difficult_pass", "long_spell"):
            chain = [c for c in chain if c["kind"] != "pressure"][-6:]
        elif len(chain) > 7:  # keep the origin of the attack and its last actions
            chain = chain[:1] + chain[-6:]
        t_show = round(max(x.get("end_t", x["t"]) for x in chain) + 0.3, 2)
        st = e["state"]
        why = []
        tn, on = self.team_name(team), self.team_name(other)
        mt = e["metrics"]
        passes = [c for c in chain if c["kind"] == "pass" and c["team"] == team]
        if kind in ("goal", "shot"):
            xg = mt["xg"]
            if kind == "goal" and xg < 0.12:
                why.append({"code": "low_xg_goal", "en": f"Scored from a {xg:.2f} xG chance: most attempts from there do not go in.",
                            "zh": f"这次射门的预期进球只有{xg:.2f}，同样位置的大多数射门不会进。"})
            elif xg >= 0.25:
                why.append({"code": "big_chance", "en": f"A {xg:.2f} xG chance: about {round(xg * 100)} goals per 100 such shots.",
                            "zh": f"预期进球{xg:.2f}，同类机会每100次约有{round(xg * 100)}次进球。"})
            else:
                why.append({"code": "half_chance", "en": f"Only a {xg:.2f} xG chance: a speculative attempt.",
                            "zh": f"预期进球只有{xg:.2f}，属于机会不大的尝试。"})
            start = chain[0]
            if start["kind"] in ("interception", "tackle"):
                gap = round(e["t"] - start["t"])
                why.append({"code": "from_regain", "en": f"The attack began with {self.name(start['actor'])} winning the ball {gap} seconds earlier.",
                            "zh": f"这次进攻始于{gap}秒前{self.name(start['actor'])}夺回球权。"})
            elif len(passes) >= 4:
                dur = round(e["t"] - passes[0]["t"])
                why.append({"code": "patient_buildup", "en": f"{tn} built it through {len(passes)} passes over {dur} seconds.",
                            "zh": f"{tn}通过{len(passes)}次传球、用时{dur}秒组织起这次进攻。"})
            if kind == "goal":
                s = e["score_after"]
                lead = "level" if s["a"] == s["b"] else ("a" if s["a"] > s["b"] else "b")
                if lead == "level":
                    why.append({"code": "score_level", "en": f"The match is level at {s['a']}-{s['b']}.",
                                "zh": f"比分扳平为{s['a']}-{s['b']}。"})
                else:
                    why.append({"code": "score_lead", "en": f"{self.team_name(lead)} lead {max(s.values())}-{min(s.values())}.",
                                "zh": f"{self.team_name(lead)}以{max(s.values())}-{min(s.values())}领先。"})
        elif kind == "pressure_win":
            presser = next(p for p in chain if p["kind"] == "pressure")
            why.append({"code": "pressure_paid_off", "en": f"Pressure from {self.name(presser['actor'])} forced the turnover almost at once.",
                        "zh": f"{self.name(presser['actor'])}的逼抢几乎立刻迫使对方丢球。"})
        if kind in ("pressure_win", "high_regain"):
            if mt["metres_from_goal"] <= 40:
                why.append({"code": "high_regain", "en": f"The ball was won {mt['metres_from_goal']} m from goal, where a regain can become a chance quickly.",
                            "zh": f"球在距球门{mt['metres_from_goal']}米处夺回，这个位置夺回球权很快就能形成机会。"})
            elif mt["in_attacking_half"]:
                why.append({"code": "midfield_regain", "en": f"{tn} win it back just inside the opponent half, {mt['metres_from_goal']} m from goal.",
                            "zh": f"{tn}在刚过中线的位置夺回球权，距球门{mt['metres_from_goal']}米。"})
            else:
                why.append({"code": "deep_regain", "en": f"{tn} win it back in their own half and can now start an attack.",
                            "zh": f"{tn}在本方半场夺回球权，可以重新组织进攻。"})
        if kind == "difficult_pass":
            why.append({"code": "difficult_pass", "en": f"A {mt['length_m']} m pass rated {mt['difficulty']}/100 for difficulty, into {mt['receiver_space_m']} m of space.",
                        "zh": f"这脚{mt['length_m']}米的传球难度评分{mt['difficulty']}/100，接球人身边只有{mt['receiver_space_m']}米空间。"})
        if kind == "long_spell":
            dur = round(e["t"] - passes[0]["t"])
            why.append({"code": "control", "en": f"{tn} have strung together {len(passes)} straight passes in {dur} seconds: they control the tempo.",
                        "zh": f"{tn}{dur}秒内连续完成{len(passes)}次传球，正在掌控节奏。"})
        if kind == "chaos":
            n = len([s for s in spells if e["t"] - 20 <= s[2] <= e["t"]])
            why.append({"code": "chaos", "en": f"Possession has changed {n} times in 20 seconds: neither side is in control.",
                        "zh": f"20秒内球权转换了{n}次，双方都没有控制住局面。"})
        recent = st[team]["recent_possession_pct"]
        if e["t"] >= 30 and (recent >= 60 or recent <= 40):
            leader = team if recent >= 60 else other
            why.append({"code": "recent_possession", "en": f"{self.team_name(leader)} have had {max(recent, 100 - recent)}% of the ball in the last minute.",
                        "zh": f"最近一分钟{self.team_name(leader)}的控球率为{max(recent, 100 - recent)}%。"})
        headline = {
            "goal": ("Goal", "进球"), "shot": ("Chance", "射门机会"),
            "pressure_win": ("Pressure pays off", "逼抢奏效"), "high_regain": ("High regain", "前场夺回球权"),
            "difficult_pass": ("Pass of the clip", "高难度传球"), "long_spell": ("In control", "控制节奏"),
            "chaos": ("Chaos", "攻防混乱"),
        }[kind]
        involved = []
        for c in chain:
            for key in ("actor", "target", "keeper", "blocker"):
                if c.get(key) and c[key] not in involved:
                    involved.append(c[key])
        return {
            "id": f"m-{e['id']}", "t": t_show, "clock": _clock(t_show), "kind": kind,
            "priority": round(score), "team": team, "outcome": e.get("outcome"), "headline": {"en": headline[0], "zh": headline[1]},
            "key_event": e["id"], "event_ids": [c["id"] for c in chain],
            "facts": [c["fact"] for c in chain], "why": why, "players": involved,
            "metrics": mt, "state": st,
        }


def analyse(match):
    return Analyst(match).run()
