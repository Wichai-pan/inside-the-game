"""Seeded synthetic football feed: raw event stream plus 22-player and ball positions.

Everything here is fictional. The simulator produces only what a data provider would send
(event type, actors, locations, timestamps, outcomes). Derived statistics live in analyst.py.

Pitch coordinates are metres with the centre spot at the origin: x in [-52.5, 52.5],
y in [-34, 34]. Team "a" attacks +x, team "b" attacks -x, for the whole clip.
"""
import math
import random

HZ = 5
MAX_SPEED = 8.5  # m/s, a fast sprint
SHAPE_LAG = 2.5  # s, how slowly a team's block follows the ball
LENGTH, WIDTH = 105.0, 68.0
HALF_X, HALF_Y = LENGTH / 2, WIDTH / 2

TEAMS = [
    {"id": "a", "name": "Harbor City", "short": "HAR", "color": "#f4c56e",
     "names": ["Tomas Reyna", "Callum Ashby", "Idris Okafor", "Lenny Voss", "Mateo Quill",
               "Rafe Delmont", "Sami Halloran", "Joel Brandt", "Nico Ferreira", "Owen Kessler",
               "Ade Bankole"]},
    {"id": "b", "name": "Ridgeway United", "short": "RDG", "color": "#63c8d0",
     "names": ["Pieter Lund", "Marco Azevedo", "Kian Morrow", "Felix Strand", "Dario Pell",
               "Hugo Lindqvist", "Yusuf Arslan", "Elias Varga", "Theo Marchetti", "Luca Brennan",
               "Ravi Kendrick"]},
]

# 4-3-3 for a team attacking +x: GK, back four, midfield three, front three.
FORMATION = [(-48, 0), (-34, -22), (-36, -8), (-36, 8), (-34, 22),
             (-16, -15), (-18, 0), (-16, 15), (4, -21), (8, 0), (4, 21)]
ROLES = ["GK", "RB", "CB", "CB", "LB", "CM", "DM", "CM", "RW", "ST", "LW"]


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _dist(p, q):
    return math.hypot(p[0] - q[0], p[1] - q[1])


def _direction(team):
    return 1 if team == "a" else -1


def _other(team):
    return "b" if team == "a" else "a"


class Match:
    def __init__(self, seed, duration):
        self.rng = random.Random(seed)
        self.seed, self.duration = seed, duration
        self.players = []
        for team in TEAMS:
            names = team["names"][:]
            self.rng.shuffle(names)
            for n in range(11):
                self.players.append({"id": f"{team['id']}{n}", "team": team["id"], "number": n + 1,
                                     "role": ROLES[n], "name": names[n]})
        self.jitter = {p["id"]: (self.rng.uniform(-2.5, 2.5), self.rng.uniform(-2.5, 2.5),
                                 self.rng.uniform(0, 6.28)) for p in self.players}
        self.ball_segments = []   # (t0, t1, p0, p1)
        self.overrides = []       # (player, t0, t1, p0, p1): player forced along a path
        self.events = []
        self.score = {"a": 0, "b": 0}
        self.smoothed_ball = None  # set at export: team shapes follow a lagged ball, not every pass

    # Shape: formation shifted toward the ball, mirrored for team b, with gentle drift.
    def shape(self, pid, t, ball):
        p = next(q for q in self.players if q["id"] == pid)
        bx, by = FORMATION[p["number"] - 1]
        d = _direction(p["team"])
        jx, jy, ph = self.jitter[pid]
        shift = 0.0 if p["role"] == "GK" else 0.5 * ball[0] * d
        if self.in_possession(t) == p["team"]:  # the side on the ball pushes up and runs forward
            shift += {"RW": 12, "ST": 14, "LW": 12, "CM": 7, "DM": 4}.get(p["role"], 2)
        x = (bx + jx + shift + 1.6 * math.sin(t / 6 + ph)) * d
        y = by + jy + 0.25 * ball[1] + 1.4 * math.cos(t / 7 + ph)
        if p["role"] == "GK":
            x = (-HALF_X + 4) * d
            y = _clamp(ball[1] * 0.15, -4, 4)
        return (_clamp(x, -HALF_X + 1, HALF_X - 1), _clamp(y, -HALF_Y + 1, HALF_Y - 1))

    def in_possession(self, t):
        team = None
        for e in self.events:
            if e["t"] > t:
                break
            if e["kind"] != "pressure":
                team = e["team"]
        return team

    def ball_at(self, t):
        for t0, t1, p0, p1 in self.ball_segments:
            if t0 <= t <= t1:
                q = 0 if t1 == t0 else (t - t0) / (t1 - t0)
                return (p0[0] + (p1[0] - p0[0]) * q, p0[1] + (p1[1] - p0[1]) * q)
        return self.ball_segments[-1][3] if self.ball_segments else (0.0, 0.0)

    def position(self, pid, t):
        ball = self.smoothed_ball[round(t * HZ)] if self.smoothed_ball else self.ball_at(t)
        base = self.shape(pid, t, ball)
        last = None
        for o in self.overrides:
            if o[0] != pid:
                continue
            if o[1] <= t <= o[2]:
                q = 0 if o[2] == o[1] else (t - o[1]) / (o[2] - o[1])
                return (o[3][0] + (o[4][0] - o[3][0]) * q, o[3][1] + (o[4][1] - o[3][1]) * q)
            if o[2] < t and (last is None or o[2] > last[2]):
                last = o
        if last and t - last[2] < 2.5:  # ease back into shape after an action
            w = 1 - (t - last[2]) / 2.5
            return (base[0] * (1 - w) + last[4][0] * w, base[1] * (1 - w) + last[4][1] * w)
        return base

    def nearest(self, team, point, t, exclude=()):
        options = [p["id"] for p in self.players if p["team"] == team and p["id"] not in exclude]
        return min(options, key=lambda pid: _dist(self.position(pid, t), point))

    def add(self, t, kind, team, actor, **fields):
        self.events.append({"id": f"s{self.seed}-e{len(self.events) + 1:03d}", "t": round(t, 2),
                            "kind": kind, "team": team, "actor": actor, **fields})

    def move_ball(self, t0, t1, p0, p1, carrier=None):
        self.ball_segments.append((t0, t1, p0, p1))
        if carrier:
            self.overrides.append((carrier, t0, t1, p0, p1))

    def run(self):
        rng = self.rng
        team = rng.choice("ab")
        t, ball = 0.0, (0.0, 0.0)
        carrier = f"{team}9"
        self.add(t, "kickoff", team, carrier, x=0.0, y=0.0)
        pressured_by = None
        self.last_passer = None
        while t < self.duration - 4:
            d = _direction(team)
            # Carry: move with the ball toward goal with some lateral wander.
            carry_time = rng.uniform(0.8, 2.6)
            speed = rng.uniform(3.5, 6.5)
            nx = _clamp(ball[0] + d * speed * carry_time * rng.uniform(0.4, 1.0), -HALF_X + 3, HALF_X - 3)
            ny = _clamp(ball[1] + rng.uniform(-4, 4), -HALF_Y + 2, HALF_Y - 2)
            self.move_ball(t, t + carry_time, ball, (nx, ny), carrier)
            t, ball = t + carry_time, (nx, ny)
            if t >= self.duration - 4:
                break
            defender = self.nearest(_other(team), ball, t)
            gap = _dist(self.position(defender, t), ball)
            pressured_by = None
            if gap < 9 and rng.random() < 0.45:
                pressured_by = defender
                close = (ball[0] - d * 1.5, ball[1] + rng.uniform(-1, 1))
                self.overrides.append((defender, t - 0.8, t + 0.4, self.position(defender, t - 0.8), close))
                self.add(t, "pressure", _other(team), defender, target=carrier,
                         x=round(ball[0], 2), y=round(ball[1], 2))
                if rng.random() < 0.3:
                    self.add(t + 0.4, "tackle", _other(team), defender, target=carrier, outcome="won",
                             x=round(ball[0], 2), y=round(ball[1], 2))
                    t += 0.4
                    self.move_ball(t - 0.4, t, ball, ball)
                    team, carrier = _other(team), defender
                    continue
            attack_x = ball[0] * d
            shot_chance = 0.7 if attack_x > 30 and abs(ball[1]) < 22 else 0.12 if attack_x > 20 else 0
            if rng.random() < shot_chance:
                t, ball, team, carrier = self.shoot(t, ball, team, carrier, pressured_by is not None)
                continue
            t, ball, team, carrier = self.pass_ball(t, ball, team, carrier, pressured_by is not None)
        return self.export()

    def pass_ball(self, t, ball, team, carrier, pressured):
        rng, d = self.rng, _direction(team)
        mates = [p["id"] for p in self.players if p["team"] == team and p["id"] != carrier
                 and p["role"] != "GK"]
        scored = []
        for pid in mates:
            pos = self.shape(pid, t + 1, ball)
            length = _dist(pos, ball)
            if not 6 < length < 42:
                continue
            progress = (pos[0] - ball[0]) * d
            weight = max(0.05, 1 + progress / 15 - abs(length - 18) / 25)
            if pid == self.last_passer:
                weight *= 0.25
            scored.append((weight, pid, pos, length))
        if not scored:
            scored = [(1, mates[0], self.shape(mates[0], t + 1, ball), 10.0)]
        weights = [s[0] for s in scored]
        _, receiver, target, length = rng.choices(scored, weights)[0]
        speed = _clamp(rng.gauss(9 + length * 0.28, 1.5), 7, 24)
        flight = length / speed
        into_final_third = target[0] * d > 17.5
        p_ok = 1.02 - 0.006 * length - (0.16 if pressured else 0) - (0.08 if into_final_third else 0)
        if rng.random() < p_ok:
            self.overrides.append((receiver, t, t + flight, self.position(receiver, t), target))
            self.move_ball(t, t + flight, ball, target)
            self.last_passer = carrier
            self.add(t, "pass", team, carrier, target=receiver, outcome="complete",
                     x=round(ball[0], 2), y=round(ball[1], 2), end_x=round(target[0], 2),
                     end_y=round(target[1], 2), end_t=round(t + flight, 2))
            return t + flight, target, team, receiver
        # Failed pass: nearest opponent to the receiving point cuts it out on the line.
        cutter = self.nearest(_other(team), target, t + flight)
        cpos = self.position(cutter, t + flight * 0.7)
        q = _clamp(((cpos[0] - ball[0]) * (target[0] - ball[0]) + (cpos[1] - ball[1]) * (target[1] - ball[1]))
                   / max(length ** 2, 1), 0.35, 1.0)
        cut = (ball[0] + (target[0] - ball[0]) * q, ball[1] + (target[1] - ball[1]) * q)
        t_cut = t + flight * q
        self.overrides.append((cutter, t, t_cut, self.position(cutter, t), cut))
        self.move_ball(t, t_cut, ball, cut)
        self.add(t, "pass", team, carrier, target=receiver, outcome="incomplete",
                 x=round(ball[0], 2), y=round(ball[1], 2), end_x=round(cut[0], 2),
                 end_y=round(cut[1], 2), end_t=round(t_cut, 2))
        self.add(t_cut, "interception", _other(team), cutter, target=carrier, outcome="won",
                 x=round(cut[0], 2), y=round(cut[1], 2))
        return t_cut, cut, _other(team), cutter

    def shoot(self, t, ball, team, carrier, pressured):
        rng, d = self.rng, _direction(team)
        goal = (HALF_X * d, 0.0)
        dist = _dist(ball, goal)
        angle = abs(math.atan2(7.32 * (goal[0] - ball[0]) * d,
                               (goal[0] - ball[0]) ** 2 + ball[1] ** 2 - (7.32 / 2) ** 2))
        xg = _clamp(1 / (1 + math.exp(-(-1.2 + 1.9 * angle - 0.06 * dist - (0.35 if pressured else 0)))), 0.02, 0.8)
        speed = _clamp(rng.gauss(26, 4), 15, 36)
        roll = rng.random()
        defending = _other(team)
        keeper = f"{defending}0"
        if roll < xg:
            outcome, end = "goal", (goal[0], rng.uniform(-3, 3))
        elif roll < xg + (1 - xg) * 0.45:
            outcome, end = "saved", (goal[0] - d * 1.2, rng.uniform(-2.5, 2.5))
        elif roll < xg + (1 - xg) * 0.6:
            outcome, end = "blocked", (ball[0] + d * 2.5, ball[1] + rng.uniform(-1, 1))
        else:
            outcome, end = "wide", (goal[0], rng.choice([-1, 1]) * rng.uniform(4.5, 9))
        flight = _dist(ball, end) / speed
        self.move_ball(t, t + flight, ball, end)
        fields = {"outcome": outcome, "x": round(ball[0], 2), "y": round(ball[1], 2),
                  "end_x": round(end[0], 2), "end_y": round(end[1], 2), "end_t": round(t + flight, 2),
                  "xg": round(xg, 3)}
        if outcome == "saved":
            fields["keeper"] = keeper
            self.overrides.append((keeper, t, t + flight, self.position(keeper, t), end))
        if outcome == "blocked":
            fields["blocker"] = self.nearest(defending, end, t + flight, exclude=(keeper,))
        if outcome == "goal":
            self.score[team] += 1
            fields["score_after"] = dict(self.score)
        self.add(t, "shot", team, carrier, **fields)
        t += flight
        if outcome == "goal":
            self.move_ball(t, t + 6, end, (0.0, 0.0))
            t += 6
            self.add(t, "kickoff", defending, f"{defending}9", x=0.0, y=0.0)
            return t, (0.0, 0.0), defending, f"{defending}9"
        if outcome == "blocked":
            return t, end, defending, fields["blocker"]
        # Saved or wide: the keeper restarts from the six-yard area.
        restart = ((HALF_X - 5) * d, rng.uniform(-6, 6))
        self.move_ball(t, t + 3, end, restart)
        t += 3
        self.add(t, "goal_kick" if outcome == "wide" else "keeper_restart", defending, keeper,
                 x=round(restart[0], 2), y=round(restart[1], 2))
        return t, restart, defending, keeper

    def export(self):
        frames = []
        steps = int(self.duration * HZ)
        step_max = MAX_SPEED / HZ
        alpha = 1 / (SHAPE_LAG * HZ)
        self.smoothed_ball, avg = [], self.ball_at(0)
        for i in range(steps + 1):
            b = self.ball_at(i / HZ)
            avg = (avg[0] + (b[0] - avg[0]) * alpha, avg[1] + (b[1] - avg[1]) * alpha)
            self.smoothed_ball.append(avg)
        current = [self.position(p["id"], 0) for p in self.players]
        for i in range(steps + 1):
            t = i / HZ
            ball = self.ball_at(t)
            # Players chase their target position at no more than a sprinting speed, so shape changes
            # after a turnover look like runs rather than jumps. Events were fixed during simulation.
            for k, p in enumerate(self.players):
                tx, ty = self.position(p["id"], t)
                cx, cy = current[k]
                gap = math.hypot(tx - cx, ty - cy)
                q = 1 if gap <= step_max else step_max / gap
                current[k] = (cx + (tx - cx) * q, cy + (ty - cy) * q)
            frames.append({"t": round(t, 2), "ball": [round(ball[0], 2), round(ball[1], 2)],
                           "players": [[round(c, 2) for c in pos] for pos in current]})
        return {
            "seed": self.seed, "duration": self.duration, "hz": HZ, "synthetic": True,
            "pitch": {"length": LENGTH, "width": WIDTH},
            "teams": [{k: v for k, v in team.items() if k != "names"} for team in TEAMS],
            "players": self.players, "events": self.events, "frames": frames,
        }


def generate(seed, duration=180):
    """Return one seeded fictional match clip: metadata, raw events and positional frames."""
    return Match(seed, duration).run()
