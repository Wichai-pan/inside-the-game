"""Two-voice caption track: instant play-by-play from events plus the agents' analysis lines.

Play-by-play is templated from each event as soon as the event is known (a pass is called when it
is received, a shot when its outcome is known), so it adds no model latency. Analysis lines come
from the agent workflow and enter the track only once they would really be ready: the moment's
time plus the measured generation time. A single speaker plays the track, as a voice would:

- each caption lasts as long as it takes to say (about 2.6 words/s in English, 4.5 characters/s
  in Chinese);
- urgent calls (shots, goals, ball wins, fouls) cut in; routine passes are only called when the
  speaker is free, and repeated names are shortened ("Kimmich to Pavard." then "Upamecano.");
- an analysis line waits for a gap of play-by-play and is dropped if it is still unsaid 15 s
  after it was ready, because by then play has moved on.
"""

import re

PRIORITY = {"filler": 1, "goal": 10, "shot": 9, "tackle": 6, "interception": 6, "foul": 6, "set_piece": 5, "cross": 5,
            "long": 4, "pressure": 3, "pass": 2, "clearance": 4, "fail": 4}
STALE_AFTER = 15.0


def _sur(name):
    parts = name.split()
    return " ".join(parts[1:]) if len(parts) > 1 and parts[-2][0].islower() else parts[-1]  # "de Ligt"


def say_time(text, lang):
    if lang == "en":
        return 0.4 + len(text.split()) / 2.6
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-zÀ-ÿ]+", text))  # names stay in Latin letters
    return 0.4 + cjk / 4.5 + latin / 2.6


def calls(match, events):
    """Play-by-play candidates: (time, priority, en, zh, event_id, last-named player)."""
    names = {p["id"]: _sur(p["name"]) for p in match["players"]}
    teams = {t["id"]: t.get("call", t["name"]) for t in match["teams"]}
    out = []
    for i, e in enumerate(events):
        k, a = e["kind"], names.get(e["actor"], "")
        b = names.get(e.get("target") or "", "")
        nxt = events[i + 1] if i + 1 < len(events) else None
        if k == "pass":
            sp = e.get("set_piece")
            if sp == "kickoff":
                out.append((e["t"], PRIORITY["set_piece"], f"Kick-off, {teams[e['team']]} get us going.",
                            f"{teams[e['team']]}开球，比赛开始。", e["id"], a))
            if sp and sp != "kickoff":
                label_en = {"throw_in": "Throw-in", "free_kick": "Free kick", "corner": "Corner",
                            "goal_kick": "Goal kick"}[sp]
                label_zh = {"throw_in": "界外球", "free_kick": "任意球", "corner": "角球", "goal_kick": "球门球"}[sp]
                out.append((e["t"], PRIORITY["set_piece"], f"{label_en}, {teams[e['team']]}. {a} takes it.",
                            f"{teams[e['team']]}{label_zh}，{a} 主罚。", e["id"], a))
            if e["outcome"] != "complete":
                cutter = names.get(nxt["actor"], "") if nxt and nxt["kind"] in ("interception", "tackle") else ""
                if cutter:
                    continue  # the ball win that follows is called instead
                out.append((min(e["end_t"], e["t"] + 1.5), PRIORITY["fail"], f"{a} gives it away.", f"{a} 传球失误。", e["id"], a))
            elif e.get("cross"):
                out.append((e["end_t"], PRIORITY["cross"], f"{a} crosses... {b}!", f"{a} 传中…… {b}！", e["id"], b))
            elif e["metrics"]["length_m"] >= 30:
                out.append((e["end_t"], PRIORITY["long"], f"Long ball from {a}, {b} gets it.",
                            f"{a} 长传，{b} 拿到球。", e["id"], b))
            else:
                out.append((e["end_t"], PRIORITY["pass"], f"{a} to {b}.", f"{a} 传给 {b}。", e["id"], b))
        elif k == "shot":
            head_en, head_zh = ("with the header", "头球") if e.get("body_part") == "head" else ("shoots", "射门")
            res = e["outcome"]
            if res == "goal":
                s = e["score_after"]
                ta, tb = teams["a"], teams["b"]
                en = f"{a} {head_en}... GOAL! {ta} {s['a']}, {tb} {s['b']}."
                zh = f"{a} {head_zh}……球进了！{ta} {s['a']} 比 {s['b']} {tb}。"
            elif res == "saved":
                kp = names.get(e.get("keeper") or "", "the keeper")
                en, zh = f"{a} {head_en}... saved by {kp}!", f"{a} {head_zh}……被 {kp} 扑出！"
            elif res == "blocked":
                bl = names.get(e.get("blocker") or "", "a defender")
                en, zh = f"{a} {head_en}... blocked by {bl}!", f"{a} {head_zh}……被 {bl} 挡出！"
            else:
                post = e.get("woodwork")
                en = f"{a} {head_en}... {'off the post!' if post else 'just wide.'}"
                zh = f"{a} {head_zh}……{'击中门框！' if post else '偏出了。'}"
            out.append((e["end_t"], PRIORITY["goal" if res == "goal" else "shot"], en, zh, e["id"], a))
        elif k in ("tackle", "interception"):
            if k == "tackle" and b:
                en, zh = f"{a} wins it off {b}!", f"{a} 从 {b} 脚下抢到球！"
            else:
                en, zh = f"Intercepted by {a}.", f"{a} 断下传球。"
            out.append((e["t"], PRIORITY[k], en, zh, e["id"], a))
        elif k == "foul":
            en = f"Foul by {a}{' on ' + b if b else ''}."
            zh = f"{a}{'对 ' + b if b else ''}犯规。"
            out.append((e["t"], PRIORITY["foul"], en, zh, e["id"], b or a))
        elif k == "clearance":
            out.append((e["t"], PRIORITY["clearance"], f"{a} clears.", f"{a} 解围。", e["id"], a))
        elif k == "pressure":
            out.append((e["t"], PRIORITY["pressure"], f"{a} closes him down.", f"{a} 上前逼抢。", e["id"], a))
    return sorted(out + fillers(match, events, out), key=lambda c: c[0])


def fillers(match, events, out, gap=8.0):
    """Context lines for quiet spells, from the running state and where the ball is."""
    teams = {t["id"]: t.get("call", t["name"]) for t in match["teams"]}
    hz, frames = match["hz"], match["frames"]
    times = sorted(c[0] for c in out)
    goals = [e["end_t"] for e in events if e["kind"] == "shot" and e["outcome"] == "goal"]
    lines, k = [], 0
    for a, b in zip([0.0] + times, times + [match["duration"]]):
        t = a + 2.0
        while b - t >= gap / 2 and t < match["duration"] - 2:
            prev = [e for e in events if e["t"] <= t and e["kind"] not in ("pressure", "foul", "clearance")]
            if not prev:
                break
            if any(0 <= t - g < 30 for g in goals):  # celebrations and the restart: let the goal breathe
                t += gap
                continue
            st, team = prev[-1]["state"], prev[-1]["team"]
            bx = frames[min(len(frames) - 1, round(t * hz))]["ball"][0] * (1 if team == "a" else -1)
            n, s = teams[team], st[team]
            options = []
            if bx < -17.5:
                options.append((f"{n} patient at the back.", f"{n} 在后场耐心倒脚。"))
            elif bx < 17.5:
                options.append((f"{n} probing through midfield.", f"{n} 在中场寻找机会。"))
            else:
                options.append((f"{n} in the final third now.", f"{n} 已经推进到前场三分之一。"))
            if t > 60:
                options.append((f"{n} have had {s['possession_pct']}% of the ball so far.",
                                f"{n} 目前控球率 {s['possession_pct']}%。"))
            if s.get("pass_pct") and s["passes"] >= 10:
                options.append((f"{n} completing {s['pass_pct']}% of their passes.",
                                f"{n} 传球成功率 {s['pass_pct']}%。"))
            if t > 60 and s["recent_possession_pct"] >= 70:
                options.append((f"{n} are dominating the ball right now.", f"{n} 这段时间牢牢控制着球权。"))
            en, zh = options[k % len(options)]
            k += 1
            recent = {l[2] for l in lines if t - l[0] < 40}
            for _ in range(len(options)):
                if en not in recent:
                    break
                en, zh = options[k % len(options)]
                k += 1
            if en in recent:
                t += gap
                continue
            lines.append((round(t, 2), PRIORITY["filler"], en, zh, None, None))
            t += gap
    return lines


def schedule(match, events, moments, profile, lang):
    """One speaker's caption track for a viewer profile and language."""
    pbp = calls(match, events)
    colour = []
    for m in moments:
        c = m["commentary"][profile][lang]
        colour.append({"ready": round(m["t"] + c.get("seconds", 0), 2), "moment": m["id"], "text": c["text"],
                       "source": c["source"], "delay": c.get("seconds", 0)})
    colour.sort(key=lambda c: c["ready"])
    track, busy_until, last_named = [], 0.0, None
    queue, i, ci, end = [], 0, 0, match["duration"]
    while i < len(pbp) or ci < len(colour) or queue:
        t_pbp = pbp[i][0] if i < len(pbp) else end + 99
        t_col = colour[ci]["ready"] if ci < len(colour) else end + 99
        if queue:
            c = queue[0]
            start = max(busy_until, c["ready"])
            routine_next = i < len(pbp) and pbp[i][1] < PRIORITY["set_piece"]
            if start <= min(t_pbp, t_col) or (routine_next and start <= t_col) or (i >= len(pbp) and ci >= len(colour)):
                queue.pop(0)
                if start - c["ready"] > STALE_AFTER or start >= end:
                    track.append({"t": round(start, 2), "end": round(start, 2), "voice": "analysis", "dropped": True,
                                  "moment": c["moment"], "text": c["text"], "ready": c["ready"]})
                    continue
                dur = say_time(c["text"], lang)
                track.append({"t": round(start, 2), "end": round(min(start + dur, end), 2), "voice": "analysis",
                              "text": c["text"], "moment": c["moment"], "source": c["source"],
                              "ready": c["ready"], "delay": c["delay"]})
                busy_until, last_named = start + dur, None
                continue
        if t_col <= t_pbp and ci < len(colour):
            c = colour[ci]
            ci += 1
            moment_t = c["ready"] - c["delay"]
            if any(x["voice"] == "pbp" and x["priority"] >= PRIORITY["shot"] and x["t"] > moment_t for x in track):
                track.append({"t": c["ready"], "end": c["ready"], "voice": "analysis", "dropped": True,
                              "moment": c["moment"], "text": c["text"], "ready": c["ready"]})
            else:
                queue.append(c)
            continue
        if i >= len(pbp):
            break
        when, pr, en, zh, eid, named = pbp[i]
        i += 1
        text = en if lang == "en" else zh
        short = False
        if pr <= PRIORITY["pass"]:
            if when < busy_until:
                continue  # speaker is busy: skip routine passes
            if pr == PRIORITY["pass"] and last_named and text.startswith(last_named + " "):
                text = text.split(" ", 2)[-1].rstrip(".。") + ("." if lang == "en" else "。")  # "Pavard."
                short = True
        elif when < busy_until:
            if pr >= 6 and track:  # urgent calls cut in, analysis included
                track[-1]["end"] = round(when, 2)
                track[-1]["cut"] = True
            elif when + 0.8 < busy_until:
                continue
            else:
                when = busy_until
        if pr >= PRIORITY["shot"]:  # a shot or goal overtakes analysis of earlier moments still waiting
            for c in queue:
                track.append({"t": round(when, 2), "end": round(when, 2), "voice": "analysis", "dropped": True,
                              "moment": c["moment"], "text": c["text"], "ready": c["ready"]})
            queue.clear()
        dur = say_time(text, lang)
        track.append({"t": round(when, 2), "end": round(when + dur, 2), "voice": "pbp", "text": text,
                      "event": eid, "priority": pr, **({"short": True} if short else {})})
        busy_until, last_named = when + dur, named
    return track
