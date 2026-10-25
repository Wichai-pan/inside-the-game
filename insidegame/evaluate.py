"""Measure the factual errors in generated commentary, recaps and studio analysis.

Every agent-written text in a bundle is checked against the structured feed it was written from, for the
error types seen in review:

- scoring_team: a team said to level, lead or win when it did not ("makes Harbor City level" when
  Ridgeway scored, "Harbor City deserved the win" in a draw);
- passer_receiver: "A to B", "A sets up B", "A 传给 B" when A never completed a pass to B in the move;
- shooter: "A shoots/fires/scores", "A 射门/破门" when A was not the shooter (or the scorer);
- invented_event: counter-attack, header, penalty, cross, save, woodwork, set piece, half-time, dribble,
  rebound and long-range claims the feed does not record;
- comparison: "X had more xG/possession/progressive passes", "X pressed harder" when the numbers say
  the opposite, and ratios ("three times the xG") that do not hold;
- number: a number used for the wrong metric (shot distance taken from a pass, "9 比 6" called
  possession, last-minute possession presented as the match share);
- player_team: a player placed in the wrong team ("Delmont harassed Harbor City" for a Harbor City player);
- colour: invented narrative (fatigue, collapse, titles, world-class strike/世界波, miracle, crowd,
  home/away, morale);
- zh_names: a Chinese text that translates or drops the Latin player/team names.

The checks are deterministic. `--llm` additionally re-runs the extraction-style claim auditor from
insidegame/agents.py on each live line, as a second opinion that does not depend on these patterns.

    .venv/bin/python -m insidegame.evaluate remix-42 remix-21 remix-7 --label baseline
"""
import argparse
import asyncio
import json
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLES = ROOT / "docs" / "synthetic"
REPORTS = ROOT / "data" / "eval"
TYPES = ("scoring_team", "passer_receiver", "shooter", "invented_event", "comparison", "number", "possession_window",
         "player_team",
         "colour", "zh_names")
NUM_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
NOT_LATIN = r"(?<![A-Za-zÀ-ÿ'])"
NOT_LATIN_AFTER = r"(?![A-Za-zÀ-ÿ])"


# -- tokenising names and clauses ----------------------------------------------------------------
def _surname(name):
    parts = name.split()
    return " ".join(parts[1:]) if len(parts) > 1 and parts[-2][0].islower() else parts[-1]


class Names:
    """Replace player and team mentions with placeholders: <P=id> and <T=id>."""

    def __init__(self, players, teams):
        self.players = {p["id"]: p for p in players}
        self.teams = {t["id"]: t for t in teams}
        pats = []
        for p in players:
            pats += [(p["name"], f"<P={p['id']}>"), (_surname(p["name"]), f"<P={p['id']}>")]
        for t in teams:
            for n in {t["name"], t.get("call") or t["name"], t.get("short") or t["name"]}:
                pats.append((n, f"<T={t['id']}>"))
        pats.sort(key=lambda x: -len(x[0]))
        self.regex = re.compile("|".join(NOT_LATIN + re.escape(n) + NOT_LATIN_AFTER for n, _ in pats))
        self.lookup = {n.lower(): tok for n, tok in pats}
        self.label = {**{p["id"]: p["name"] for p in players}, **{t["id"]: t["name"] for t in teams}}

    def tag(self, text):
        return self.regex.sub(lambda m: self.lookup[m.group(0).lower()], text)

    def team_of(self, pid):
        return self.players[pid]["team"]


def _num_words(text):
    return re.sub(r"\b(" + "|".join(NUM_WORDS) + r")\b", lambda m: NUM_WORDS[m.group(1).lower()], text, flags=re.I)


SPLIT_EN = re.compile(r"(?<!\d)[.;!?](?!\d)|[.;!?](?=\s)|[,—–]|\s\b(?:but|yet|while|whereas|although|though|despite|after|as|before|"
                      r"then|and then|which|when|so)\b|:(?=\s)", re.I)
SPLIT_ZH = re.compile(r"[。；！？，、：;!?]|(?<!\d)[.,](?!\d)|但|却|而|随后|然后|之后|此前|最终")


def clauses(text, lang):
    parts = (SPLIT_EN if lang == "en" else SPLIT_ZH).split(text)
    return [p.strip() for p in parts if p and p.strip()]


def sentences(text):
    return [s.strip() for s in re.split(r"(?<!\d)[.。;；](?!\d)|[.;](?=\s)|[。；]", text) if s.strip()]


TOKEN = re.compile(r"<([PT])=([^>]+)>")


def tokens(clause):
    return [(m.group(1), m.group(2), m.start(), m.end()) for m in TOKEN.finditer(clause)]


# -- context ------------------------------------------------------------------------------------
def _count(items):
    out = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


def score_progress(goals, teams):
    """Teams that ever led, teams that scored an equaliser, final leader."""
    s = {t: 0 for t in teams}
    led, levelled = set(), set()
    for g in goals:
        s[g] += 1
        a, b = teams
        if s[a] == s[b]:
            levelled.add(g)
        else:
            led.add(a if s[a] > s[b] else b)
    a, b = teams
    return led, levelled, (None if s[a] == s[b] else a if s[a] > s[b] else b)


def _led_before(score, scorer, teams):
    before = {t: score[t] - (t == scorer) for t in teams}
    a, b = teams
    return set() if before[a] == before[b] else {a if before[a] > before[b] else b}


def moment_context(bundle, moment, names):
    events = {e["id"]: e for e in bundle["events"]}
    chain = [events[i] for i in moment["event_ids"] if i in events]
    key = events[moment["key_event"]]
    passes = [e for e in chain if e["kind"] == "pass"]
    shots = [e for e in chain if e["kind"] == "shot"]
    teams = [t["id"] for t in bundle["teams"]]
    score = key.get("score_after") if key["kind"] == "shot" else None
    st = moment["state"]
    score = score or {t: st[t]["goals"] for t in teams}
    leader = None if score[teams[0]] == score[teams[1]] else max(teams, key=lambda t: score[t])
    goal = moment["kind"] == "goal"
    codes = {w["code"] for w in moment["why"]}
    support = {
        "counter": "from_regain" in codes or any(e.get("counter_attack") for e in chain),
        "header": any(e.get("body_part") == "head" for e in shots),
        "penalty": any(e.get("penalty") for e in shots),
        "cross": any(e.get("cross") for e in passes),
        "corner": any(e.get("set_piece") == "corner" for e in passes),
        "free_kick": any(e.get("set_piece") == "free_kick" for e in passes),
        "throw_in": any(e.get("set_piece") == "throw_in" for e in passes),
        "save": any(e["outcome"] == "saved" for e in shots),
        "woodwork": any(e.get("woodwork") for e in shots),
        "foul": any(e["kind"] == "foul" for e in chain),
        "long_range": any(e["metrics"]["distance_m"] >= 24 for e in shots),
        "close_range": any(e["metrics"]["distance_m"] <= 11 for e in shots),
        "one_two": any((p["actor"], p.get("target")) == (q.get("target"), q["actor"]) for p in passes for q in passes),
    }
    facts = [f[k] for f in moment["facts"] + moment["why"] + moment.get("key_facts", []) for k in ("en", "zh")]
    possession = {t: {st[t]["possession_pct"], st[t]["recent_possession_pct"]} for t in teams}
    return {
        "surface": "live", "teams": teams, "involved": set(moment["players"]),
        "pairs": {(e["actor"], e["target"]) for e in passes if e["outcome"] == "complete" and e.get("target")},
        "shooters": {e["actor"] for e in shots}, "scorers": {key["actor"]} if goal else set(),
        "shot_moment": moment["kind"] in ("goal", "shot"),
        "led": {leader} if leader else set(), "levelled": {key["team"]} if goal and leader is None else set(),
        "scored": {key["team"]} if goal else set(), "winner": None, "final": False,
        "led_before": _led_before(score, key["team"], teams) if goal else None,
        "support": support, "facts": facts, "metrics": {
            "xg": {t: st[t]["xg"] for t in teams}, "shots": {t: st[t]["shots"] for t in teams},
            "possession": possession},
        "recent_possession": {t: st[t]["recent_possession_pct"] for t in teams},
        "overall_possession": {t: st[t]["possession_pct"] for t in teams},
        "names_in_facts": any(t[0] == "P" for f in facts for t in tokens(names.tag(f))),
    }


def recap_context(bundle, names):
    events = bundle["events"]
    teams = [t["id"] for t in bundle["teams"]]
    shots = [e for e in events if e["kind"] == "shot"]
    goals = [e for e in shots if e["outcome"] == "goal"]
    led, levelled, winner = score_progress([g["team"] for g in goals], teams)
    passes = [e for m in bundle["moments"] for e in events if e["id"] in m["event_ids"] and e["kind"] == "pass"]
    chains = [e for m in bundle["moments"] for e in events if e["id"] in m["event_ids"]]
    f = bundle["final_stats"]
    facts = [f["en"], f["zh"]] + [x[k] for m in bundle["moments"] for x in m["facts"] + m["why"] for k in ("en", "zh")]
    support = {k: any(v) for k, v in {
        "counter": [w["code"] == "from_regain" for m in bundle["moments"] for w in m["why"]],
        "header": [e.get("body_part") == "head" for e in shots], "penalty": [e.get("penalty") for e in shots],
        "cross": [e.get("cross") for e in passes], "corner": [e.get("set_piece") == "corner" for e in passes],
        "free_kick": [e.get("set_piece") == "free_kick" for e in passes],
        "throw_in": [e.get("set_piece") == "throw_in" for e in passes],
        "save": [e["outcome"] == "saved" for e in shots], "woodwork": [e.get("woodwork") for e in shots],
        "foul": [e["kind"] == "foul" for e in chains], "long_range": [e["metrics"]["distance_m"] >= 24 for e in shots],
        "close_range": [e["metrics"]["distance_m"] <= 11 for e in shots], "one_two": [False]}.items()}
    a, b = f["a"], f["b"]
    return {
        "surface": "recap", "teams": teams, "involved": {p["id"] for p in bundle["players"]},
        "pairs": {(e["actor"], e["target"]) for e in passes if e["outcome"] == "complete" and e.get("target")},
        "shooters": {e["actor"] for e in shots}, "scorers": {g["actor"] for g in goals}, "shot_moment": True,
        "goal_counts": _count([g["actor"] for g in goals]), "team_goals": _count([g["team"] for g in goals]),
        "led": led, "levelled": levelled, "scored": {g["team"] for g in goals}, "winner": winner, "final": True,
        "support": support, "facts": facts, "metrics": {
            "xg": {"a": a["xg"], "b": b["xg"]}, "shots": {"a": a["shots"], "b": b["shots"]},
            "possession": {"a": {a["possession_pct"]}, "b": {b["possession_pct"]}},
            "on_target": {"a": a["on_target"], "b": b["on_target"]}, "pass_pct": {"a": a["pass_pct"], "b": b["pass_pct"]}},
        "names_in_facts": True,
    }


def studio_players(stats):
    teams = [{"id": "a", "name": stats["teams"][0]["team"], "call": stats["teams"][0].get("call"), "short": None},
             {"id": "b", "name": stats["teams"][1]["team"], "call": stats["teams"][1].get("call"), "short": None}]
    for t in teams:
        t["short"] = t["call"] or t["name"]
    side = {t["name"]: t["id"] for t in teams}
    players = [{"id": p["player"], "name": p["player"], "team": side[p["team"]]} for p in stats["players"]]
    return players, teams, side


def studio_context(stats, section, side):
    teams = ["a", "b"]
    ta, tb = stats["teams"]
    goals = stats["goals"]
    led, levelled, winner = score_progress([side[g["team"]] for g in goals], teams)
    pens = {side[s["team"]]: s["xg"] for s in stats["shots"] if s.get("penalty")}
    np_xg = {"a": round(ta["xg"] - pens.get("a", 0), 2), "b": round(tb["xg"] - pens.get("b", 0), 2)}
    pick = lambda k: {"a": ta[k], "b": tb[k]}
    facts = section["facts"]["en"] + section["facts"]["zh"]
    alltext = " ".join(facts).lower()
    metrics = {"xg": pick("xg"), "np_xg": np_xg, "possession": {t: {v} for t, v in pick("possession_pct").items()},
               "shots": pick("shots"), "on_target": pick("on_target"), "progressive": pick("progressive_passes"),
               "crosses": pick("crosses"), "entries": pick("final_third_entries"), "tilt": pick("field_tilt_pct"),
               "goals": pick("goals"), "tackles": pick("tackles_won")}
    if ta.get("ppda") is not None and tb.get("ppda") is not None:
        metrics["ppda"] = pick("ppda")
    shots = stats["shots"]
    return {
        "surface": "studio", "section": section["id"], "teams": teams, "involved": set(section["players"]),
        "pairs": {(g["assist"], g["player"]) for g in goals if g.get("assist")},
        "shooters": {s["player"] for s in shots}, "scorers": {g["player"] for g in goals}, "shot_moment": True,
        "goal_counts": _count([g["player"] for g in goals]), "team_goals": _count([side[g["team"]] for g in goals]),
        "led": led, "levelled": levelled, "scored": {side[g["team"]] for g in goals}, "winner": winner, "final": True,
        "support": {"counter": False, "header": any(s.get("head") for s in shots),
                    "penalty": any(s.get("penalty") for s in shots), "cross": True,
                    "corner": False, "free_kick": False, "throw_in": False, "save": "save" in alltext,
                    "woodwork": False, "foul": False, "long_range": False, "close_range": False, "one_two": False},
        "facts": facts, "metrics": metrics, "names_in_facts": bool(section["players"]),
        "segment_section": section["id"] == "momentum",
    }


# -- checks -------------------------------------------------------------------------------------
LEVEL = {"en": r"\blevel(?:s|led|ed|ling|ing)?\b|\bequali[sz]\w*|\bpulls? (?:one|it) back|\btie[sd]?\b|\btying\b",
         "zh": r"扳平|追平|扳回"}
EXTEND = {"en": r"\bextend\w*\b(?:\s+\w+){0,2}\s+lead|\bdoubl\w+ (?:the|their) (?:lead|advantage)|\bstretch\w* (?:the|their) lead",
          "zh": r"扩大(?:领先|优势)|领先优势扩大"}
LEAD = {"en": r"\b(?:lead|leads|leading|led|ahead|in front)\b(?! to\b| by\b)", "zh": r"领先|反超|占先"}
WIN = {"en": r"\bwon (?:the match|the game|it)\b|\bwin(?:s)? (?:the match|the game)\b|\bvictory\b|\bdeserved (?:the|to|their) win\b|"
             r"\bdeserved winners\b|\bdeserved the victory\b|\b(?:beat|beats|defeated)\s+<T",
       "zh": r"获胜|赢下|赢得比赛|击败|取胜|胜利|拿下比赛"}
METRIC_GUARD = re.compile(r"xG|expected goals|chance|possession|PPDA|press|progress|cross|shot|预期进球|控球|逼抢|推进|传中|射门|机会", re.I)


def L(ctx, x):
    return ctx.get("label", {}).get(x, x)


def _names(ctx, ids, empty):
    return " and ".join(sorted(L(ctx, i) for i in ids)) or empty


def _claimed_team(clause, span):
    toks = [t for t in tokens(clause) if t[0] == "T"]
    before = [t for t in toks if t[3] <= span[0]]
    after = [t for t in toks if t[2] >= span[1]]
    if before:
        return before[-1][1]
    return after[0][1] if after else None


def check_scoring_team(tagged, lang, ctx):
    errs = []
    for c in clauses(tagged, lang):
        for m in re.finditer(LEVEL[lang], c, re.I):
            team = _claimed_team(c, m.span())
            if team and ctx["levelled"] is not None and team not in ctx["levelled"] and not (
                    lang == "zh" and m.group(0) == "扳回" and team in ctx["scored"]):
                errs.append(f"says {L(ctx, team)} drew level, but {_names(ctx, ctx['levelled'], 'no team')} drew level")
        for m in re.finditer(EXTEND[lang], c, re.I):
            team = _claimed_team(c, m.span())
            if ctx.get("led_before") is not None and (team or "?") not in ctx["led_before"]:
                errs.append("says a lead was extended, but " + (
                    f"{_names(ctx, ctx['led_before'], '')} led before this goal" if ctx["led_before"] else
                    "no team led before this goal"))
        for m in re.finditer(LEAD[lang], c, re.I):
            if METRIC_GUARD.search(c):
                break  # "ahead on xG" is a comparison, checked below
            team = _claimed_team(c, m.span())
            if team and team not in ctx["led"]:
                errs.append(f"says {L(ctx, team)} lead, but {_names(ctx, ctx['led'], 'no team')} led")
        if ctx["final"]:
            for m in re.finditer(WIN[lang], c, re.I):
                if re.search(r"(?:没有|未能|未|无|不|neither|no team|nobody|nor)\W*\w*\W*$", c[max(0, m.start() - 12):m.start()], re.I):
                    continue  # "neither team won", "没有球队获胜"
                if re.match(r"beat|beats|defeated|击败", m.group(0), re.I):
                    before = [t for t in tokens(c) if t[0] == "T" and t[3] <= m.start()]
                    after = [t for t in tokens(c[m.start():]) if t[0] == "T"]
                    loser = after[0][1] if after else None
                    if ctx["winner"] is None or (before and before[-1][1] != ctx["winner"]) or loser == ctx["winner"]:
                        errs.append(f"says {L(ctx, loser) if loser else 'a team'} was beaten, but "
                                    + ("the match was a draw" if ctx["winner"] is None else f"{L(ctx, ctx['winner'])} won"))
                    continue
                team = _claimed_team(c, m.span())
                if ctx["winner"] is None or (team and team != ctx["winner"]):
                    errs.append(f"says {L(ctx, team) if team else 'a team'} won, but "
                                + ("the match was a draw" if ctx["winner"] is None else f"{L(ctx, ctx['winner'])} won"))
    return errs


PASS_EN = (r"<P=([^>]+)>(?:'s)?(?:(?:\s+[a-z\-]+){0,3}?\s+(?:passes(?: to)?|passed to|finds|found|sets up|set up|setting up|"
           r"feeds|fed|picks out|picked out|plays in|played in|tees up|teed up|releases|released|slides in|assists)|"
           r"(?:\s+[\w\-]+){0,3}?\s+(?:pass|ball|cross|delivery|feed)(?:\s+[a-z]+)?\s+(?:to|for|into)|\s+(?:to|for))\s+<P=([^>]+)>")
PASS_ZH = r"<P=([^>]+)>\s*(?:[^，。<A-Za-z]{0,6}?)(?:传给|传球给|传中给|分球给|直塞给|送出助攻给|助攻|找到|传向|妙传|喂给)\s*<P=([^>]+)>"
SET_UP_BY = r"(?:set up|teed up|assisted|fed) by <P=([^>]+)>"


def check_passes(tagged, lang, ctx):
    errs = []
    for c in clauses(tagged, lang):
        for m in re.finditer(PASS_EN if lang == "en" else PASS_ZH, c):
            a, b = m.group(1), m.group(2)
            if (a, b) not in ctx["pairs"]:
                errs.append(f"{L(ctx, a)} did not pass to {L(ctx, b)} in this move")
    return errs


SHOT_EN = (r"<P=([^>]+)>(?:'s)?(?:\s+[\w\-\.%]+){0,4}?\s+(?:shot|shoots|shooting|strike|strikes|struck|fires|fired|effort|"
           r"attempt|header|heads|headed|finish|finishes|finished|drills|drilled|curls|curled|volley|volleys|hits|smashes|"
           r"slots|blasts|scores|scored|scoring|nets|netted)\b|<P=([^>]+)>'s(?:\s+[\w\-\.%]+){0,3}?\s+goal\b")
SHOT_ZH = r"<P=([^>]+)>(?:[^，。<A-Za-z]{0,10}?)(?:射门|头球|破门|打入|打进|攻门|推射|抽射|远射|劲射|起脚|进球|建功|命中)"
SCORE_VERB = re.compile(r"scores|scored|scoring|nets|netted|goal|破门|打入|打进|进球|建功")


def check_shooter(tagged, lang, ctx):
    errs = []
    for c in sentences(tagged):
        for m in re.finditer(SHOT_EN if lang == "en" else SHOT_ZH, c):
            pid = m.group(1) or m.group(2) if lang == "en" else m.group(1)
            if re.match(r"(?:\s+[\w\-\.%,]+){0,4}?\s+(?:pass|ball|cross)", c[m.end():]):
                continue
            if re.search(r"(?:blocked|saved|denied|stopped) by\s*$|被\s*$", c[:m.start()]):
                continue
            if ctx["surface"] == "live" and not ctx["shot_moment"]:
                errs.append(f"{L(ctx, pid)} did not shoot: there is no shot in this moment")
            elif SCORE_VERB.search(m.group(0)) and ctx["scorers"] and pid not in ctx["scorers"]:
                errs.append(f"{L(ctx, pid)} did not score")
            elif pid not in ctx["shooters"]:
                errs.append(f"{L(ctx, pid)} did not take the shot")
    return errs


EVENT_TERMS = {
    "counter": (r"counter[- ]?attack\w*|\bcounter\b|on the break|\bbreakaway|\bbreak\b", r"反击|快攻|防守反击"),
    "header": (r"\bheader\b|\bheaded\b|\bheads\b(?! up)|\bnods?\b", r"头球|头槌|甩头"),
    "penalty": (r"penalt\w+|spot[- ]kick|from the spot|\bspot\b", r"点球|十二码"),
    "cross": (r"\bcross(?:es|ed|ing)?\b|\bdelivery\b", r"传中"),
    "corner": (r"\bcorner\b", r"角球"),
    "free_kick": (r"free[- ]kick", r"任意球"),
    "throw_in": (r"throw[- ]in", r"界外球"),
    "save": (r"\bsave[sd]?\b|\bkeeper\b|goalkeeper|\bdenied\b|\bstopper\b", r"扑救|扑出|门将|守门员"),
    "woodwork": (r"\bpost\b|crossbar|woodwork|\bthe bar\b", r"门柱|横梁|门框"),
    "foul": (r"\bfoul\w*|\bcard\b|booked|booking", r"犯规|黄牌|红牌"),
    "offside": (r"offside", r"越位"),
    "half_time": (r"half[- ]?time|first half|second half|the break\b|interval", r"上半场|下半场|中场休息|半场结束|易边"),
    "dribble": (r"dribbl\w+|takes on|beats (?:his|the) man|nutmeg|skips past|jinks", r"过人|盘带|突破|穿裆"),
    "rebound": (r"rebound|second ball|follow[- ]up|tap[- ]in", r"补射|二点球|补门"),
    "long_range": (r"long[- ]range|from distance|from range|from outside the box", r"远射|禁区外"),
    "close_range": (r"close range|point[- ]blank|six[- ]yard", r"近距离|门前|小禁区"),
    "one_two": (r"one[- ]two|give[- ]and[- ]go|wall pass", r"二过一|撞墙"),
}


def check_events(text, lang, ctx):
    errs = []
    fact_low = " ".join(ctx["facts"]).lower()
    for key, (en, zh) in EVENT_TERMS.items():
        pat = en if lang == "en" else zh
        m = re.search(pat, text, re.I)
        if not m or ctx["support"].get(key):
            continue
        if re.search(pat, fact_low, re.I):
            continue  # the facts themselves use the term
        if key == "save" and re.search(r"\bsave[sd]?\b", text, re.I) and re.search(r"saved? (?:the|his|their) (?:day|side)", text, re.I) is None \
                and ctx["surface"] != "live":
            pass
        errs.append(f"claims {key} ('{m.group(0)}')")
    return errs


COLOUR = {
    "en": (r"fatigue|\btired|tiring|exhaust\w*|heavy legs|\blegs\b|collaps\w*|crumbl\w*|capitulat\w*|implod\w*|\btitles?\b|"
           r"champion\w*|\bleague\b|world[- ]class|wonder[- ]?(?:strike|goal)|worldie|screamer|thunderbolt|"
           r"stunning|sensational|spectacular|miracle|miraculous|\brare\b|\bmagic\w*|unstoppable|heroic|\bhero\b|"
           r"\bcrowd\b|\bfans\b|supporters|\bhome side\b|\baway side\b|the hosts|the visitors|visiting|\bat home\b|"
           r"on the road|weather|\brain\b|morale|confidence|nerves|nervy|derby|relegation|injur\w*|frustrat\w*|"
           r"desperat\w*|panic\w*|composure|\bclinical\b|ruthless|lethal|wasteful|profligate|luck\w*|fortunate|"
           r"\bstatement\b|masterclass|midfield battle|in midfield|on the wing|out wide|flank"),
    "zh": (r"世界波|神仙球|惊天|奇迹|神奇|疲劳|疲惫|体能|体力|崩盘|崩溃|溃败|冠军|联赛|积分榜|排名|士气|信心|球迷|"
           r"观众|主场|客场|主队|客队|天气|伤病|绝杀|罕见|德比|心态|梦幻|致命|幸运|运气|侥幸|冷静|浪费|神来之笔|"
           r"精彩绝伦|不可思议|中场缠斗|边路"),
}


def check_colour(text, lang, ctx):
    fact_low = " ".join(ctx["facts"]).lower()
    out = []
    for m in re.finditer(COLOUR[lang], text, re.I):
        if m.group(0).lower() not in fact_low:
            out.append(f"invented colour '{m.group(0)}'")
    return out


METRICS = {  # key: (en pattern, zh pattern)
    "np_xg": (r"open[- ]play|without penalties|non[- ]penalty|excluding penalties", r"非点球|去掉点球|运动战"),
    "xg": (r"\bxG\b|expected goals|chance quality|chance value|quality of chances|better chances|bigger chances", r"预期进球|xG|机会质量"),
    "ppda": (r"\bPPDA\b|\bpress\w*", r"PPDA|逼抢|压迫"),
    "possession": (r"possession|of the ball", r"控球"),
    "progressive": (r"progressive|progression|progress", r"推进"),
    "crosses": (r"\bcross\w*", r"传中"),
    "on_target": (r"on target", r"射正"),
    "shots": (r"\bshots?\b|\battempts?\b|\bshooting\b|\boutshot\b", r"射门"),
    "entries": (r"final[- ]third entries|\bentries\b|into the final third", r"进入前场"),
    "tilt": (r"field tilt|territor\w*", r"场地倾斜|领地"),
    "tackles": (r"tackl\w+", r"抢断"),
}
MORE = {"en": r"\bmore\b|\bhigher\b|\bgreater\b|\bbetter\b|\bsuperior\b|\boutpac\w+|\bout-?creat\w+|\boutshot\b|\bedge[ds]?\b|"
              r"\bdominat\w+|\bbigger\b|\bharder\b|\bmost\b|\bbeat\b|\bsurpass\w*|\bahead\b|\bmore aggressive\w*|\bintense\b",
        "zh": r"更多|更高|更优|多于|高于|占优|领先|碾压|压制|优势|更凶|更积极|更强|更猛|主导|占据上风|胜过|超过"}
LESS = {"en": r"\bfewer\b|\bless\b|\blower\b|\bworse\b|\binferior\b|\btrail\w*|\bbehind\b",
        "zh": r"更少|更低|少于|低于|不如|落后"}
RATIO = {"en": r"\b(\d+(?:\.\d+)?|twice|double|three|four|five) times\b|\bdouble\b|\btwice\b|\btriple\b|\bhalf as\b",
         "zh": r"(两|三|四|五|\d+)倍|翻倍|一半"}
ZH_NUM = {"两": 2, "三": 3, "四": 4, "五": 5}


def _metrics_in(c, lang, collapse=True):
    found = []
    for k, pats in METRICS.items():
        m = re.search(pats[0 if lang == "en" else 1], c, re.I)
        if m:
            found.append((k, m))
    keys = [k for k, _ in found]
    if not collapse:
        return found
    if "np_xg" in keys:
        found = [x for x in found if x[0] != "xg"]
    if "on_target" in keys:
        found = [x for x in found if x[0] != "shots"]
    if "ppda" in keys and "possession" in keys and lang == "en":
        found = [x for x in found if not (x[0] == "possession" and x[1].group(0).lower() == "the ball")]
    return found


def _val(v):
    return max(v) if isinstance(v, set) else v


def check_comparisons(tagged, lang, ctx):
    errs = []
    if ctx.get("segment_section"):
        return errs  # momentum compares spells of the match, not full-match totals
    units = [(c, sent) for sent in sentences(tagged) for c in clauses(sent, lang)]
    for c, sent in units:
        found = [(k, m) for k, m in _metrics_in(c, lang) if k in ctx["metrics"]]
        teams = [t for t in tokens(c) if t[0] == "T"]
        if len(found) != 1 or not teams:
            continue
        key, mm = found[0]
        if key == "xg" and "np_xg" in ctx["metrics"] and re.search(METRICS["np_xg"][0 if lang == "en" else 1], sent[:sent.find(c) + len(c)], re.I):
            key = "np_xg"  # "without penalties" earlier in the sentence
        vals = ctx["metrics"][key]
        more = re.search(MORE[lang], c, re.I)
        less = re.search(LESS[lang], c, re.I)
        word = more or less
        if word and "<P=" in c[min(word.end(), mm.start()):max(word.start(), mm.end())]:
            continue  # "more danger with Halloran's shot": the comparison is not about the metric
        if word and "<P=" in c[:word.start()]:
            continue  # "Voss (Harbor City) won the most tackles": a player, not a team, is compared
        if lang == "zh" and key == "ppda" and re.search(r"PPDA\s*更低|PPDA\s*低", c):
            more, less = more or re.search(r"更低|低", c), None
        word = more or less
        prior = [t for t in teams if word and t[3] <= word.start()]
        subject = (prior[-1] if prior else teams[0])[1]
        other = "b" if subject == "a" else "a"
        if key == "possession":
            sv, ov = vals[subject], vals[other]
            if more and not less and not any(x > 50 for x in sv):
                errs.append(f"{subject} did not have more {key}")
            continue
        sv, ov = _val(vals[subject]), _val(vals[other])
        lower_better = key == "ppda" and not re.search(r"higher PPDA|PPDA\s*更高", c, re.I)
        if key == "ppda" and re.search(r"lower PPDA|PPDA\s*更低", c, re.I):
            lower_better = True
        claim = None
        if more and not less:
            claim = "more"
        elif less and not more:
            claim = "less"
        if claim and sv != ov:
            first = sv < ov if lower_better else sv > ov
            if (claim == "more") != first:
                errs.append(f"says {L(ctx, subject)} had {claim} {key}, but it was {sv} vs {ov}")
        elif claim and sv == ov:
            errs.append(f"says {L(ctx, subject)} had {claim} {key}, but both had {sv}")
        r = re.search(RATIO[lang], c, re.I)
        if r and sv and ov and key not in ("ppda",):
            word = (r.group(1) if r.lastindex else r.group(0)) or r.group(0)
            factor = {"twice": 2, "double": 2, "triple": 3, "three": 3, "four": 4, "five": 5, "half as": 0.5,
                      "翻倍": 2, "一半": 0.5}.get(word.lower(), None)
            if factor is None:
                factor = ZH_NUM.get(word) or float(word)
            real = max(sv, ov) / min(sv, ov) if min(sv, ov) else 99
            if abs(real - factor) / factor > 0.25:
                errs.append(f"ratio {factor}x for {key}, actual {real:.1f}x")
    return errs


NUMBER = re.compile(r"(?<![\d.:])(?<!\d-)(\d+(?:\.\d+)?)(?![\d:]|-\d|st\b|nd\b|rd\b|th\b|[- ]?(?:difficulty|rated))")
FACT_NUMBER = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d])")
UNIT_METRIC = [(r"\s*(?:km/h|公里/小时|公里每小时)", "speed"), (r"\s*(?:km\b|公里)", "km"),
               (r"\s*(?:m\b|metres?\b|meters?\b|米)", "metres"), (r"\s*(?:seconds?|secs?|秒)", "seconds"),
               (r"\s*(?:minutes?|分钟)", "minutes"), (r"\s*/\s*100", "difficulty"), (r"\s*(?:passes|次传球|脚传球)", "passes")]
ACTIONS = {"en": [("shot", r"shot|shoot|strike|struck|header|heads|effort|attempt|finish|scor|penalt"),
                  ("pass", r"pass|ball|cross|feed|find|to <P")],
           "zh": [("shot", r"射门|头球|破门|打入|打进|攻门|推射|抽射|远射|点球"), ("pass", r"传|长传|分球")]}


def attributions(clause, lang, numbers=None):
    """(number, metrics, strict) for each number in a clause. A unit right after the number fixes its metric
    (strict, with the action of the clause for distances and speeds); otherwise the number may belong to any
    metric named in the clause."""
    out = []
    mets = sorted({k for k, _ in _metrics_in(clause, lang, collapse=numbers is None)})
    for m in (numbers or NUMBER).finditer(clause):
        if clause[:m.start()].endswith("第") or re.search(r"(?:难度|difficulty|rated)\s*$", clause[:m.start()]):
            continue
        n = f"{float(m.group(1)):g}"
        rest = clause[m.end():m.end() + 12]
        unit = next((u for pat, u in UNIT_METRIC if re.match(pat, rest, re.I)), None)
        if unit in ("seconds", "minutes", "passes", "difficulty"):
            continue
        if unit == "km":
            out.append((n, ("km:any",), True))
        elif unit in ("metres", "speed"):
            acts = [a for a, pat in ACTIONS[lang] if re.search(pat, clause, re.I)]
            out.append((n, (unit + ":" + (acts[0] if len(acts) == 1 else "any"),), True))
        elif mets:
            out.append((n, tuple(mets), False))
    return out


def fact_attributions(ctx):
    out = set()
    for f in ctx["facts"]:
        for s in sentences(f):
            lang = "zh" if re.search(r"[一-鿿]", s) else "en"
            for n, metrics, strict in attributions(s, lang, FACT_NUMBER):
                for m in metrics:
                    out.add((n, m))
            for n in FACT_NUMBER.findall(s):
                out.add((f"{float(n):g}", "any"))
    return out


SAME_METRIC = {"xg": ("np_xg",), "np_xg": ("xg",)}


def check_numbers(text, lang, ctx):
    errs = []
    facts = fact_attributions(ctx)
    for c in clauses(text, lang):
        for n, metrics, strict in attributions(c, lang):
            if (n, "any") not in facts:
                continue  # numbers absent from the facts are the rule checker's job
            if not strict and float(n).is_integer() and float(n) <= 12 and not set(metrics) & {"possession", "tilt", "ppda"}:
                continue  # small counts ("two shots", "four passes") are too ambiguous to attribute
            ok = any((n, m) in facts or any((n, x) in facts for x in SAME_METRIC.get(m, ())) for m in metrics)
            if strict and not ok:  # the action is unknown on one side: any measurement of that kind will do
                unit, act = metrics[0].split(":")
                ok = (n, unit + ":any") in facts or (act == "any" and any(
                    k[0] == n and k[1].startswith(unit + ":") for k in facts))
            if not ok:
                errs.append(f"{n} is not the {' or '.join(metrics)}")
    return errs


def check_possession_window(text, lang, ctx):
    """Possession over the last minute presented as the match share."""
    errs = []
    if ctx["surface"] != "live":
        return errs
    rec, over = ctx["recent_possession"], ctx["overall_possession"]
    qualifier = r"last minute|last 60|recent|this spell|right now|lately|spell|最近|这段时间|近一分钟|一分钟|过去一分钟|此前一分钟"
    for c in clauses(text, lang):
        if re.search(METRICS["possession"][0 if lang == "en" else 1], c, re.I) and not re.search(qualifier, c, re.I):
            for m in re.finditer(r"(\d+)\s*%", c):
                v = int(m.group(1))
                if v in rec.values() and v not in over.values():
                    team = next(t for t, x in rec.items() if x == v)
                    errs.append(f"{v}% is {L(ctx, team)}'s possession in the last minute only: write 'in the last minute' "
                                f"next to it, or use the match share ({over[team]}%)")
    return errs


SCORER_LIST = {"en": r"((?:<P=[^>]+>(?:'s)?(?:,\s*|\s+and\s+)?)+)\s+(?:each |both |also )?(?:scored|scores|netted|struck)"
                      r"(?:\s+(?:twice|once|two goals|a goal|again|his second|the goals?))?\s+for\s+<T=([^>]+)>",
               "zh": r"((?:<P=[^>]+>\s*(?:、|和|与|，)?\s*)+)(?:分别|各|都)?为\s*<T=([^>]+)>\s*(?:建功|进球|破门|打进|入球|扳回)"}
TWICE = {"en": r"<P=([^>]+)>(?:'s)?(?:\s+\w+){0,2}?\s+(?:scored twice|scores twice|(?:two|2) goals|a brace|his second|scored again|scores again)",
         "zh": r"<P=([^>]+)>\s*(?:梅开二度|连入两球|连进两球|打进两球|两度破门|再进一球|再入一球|第二个进球)"}
GOALS_TOTAL = {"en": r"\b(\d+) goals\b", "zh": r"(\d+|两|三|四|五|六)\s*(?:个)?(?:进球|球)(?!员|队|门|场|权|速|迷)"}


def check_goal_counts(tagged, lang, ctx, names):
    """Recaps and studio: who scored for whom, who scored twice, and how many goals there were."""
    errs = []
    if "goal_counts" not in ctx:
        return errs
    for m in re.finditer(SCORER_LIST[lang], tagged):
        for pid in re.findall(r"<P=([^>]+)>", m.group(1)):
            if pid in names.players and names.team_of(pid) != m.group(2):
                errs.append(f"{L(ctx, pid)} scored for {L(ctx, names.team_of(pid))}, not {L(ctx, m.group(2))}")
    for m in re.finditer(TWICE[lang], tagged):
        if ctx["goal_counts"].get(m.group(1), 0) < 2:
            errs.append(f"{L(ctx, m.group(1))} scored {ctx['goal_counts'].get(m.group(1), 0)} goal(s), not two")
    each = (r"((?:<P=[^>]+>(?:,\s*|\s+and\s+)?){2,})\s+(?:each|both)\s+(?:scored|netted)\s+(?:twice|(?:two|2) goals|a brace)"
            if lang == "en" else r"((?:<P=[^>]+>\s*(?:、|和|与)?\s*){2,})(?:各|都)(?:进|入|打进)(?:了)?两球")
    for m in re.finditer(each, tagged):
        for pid in re.findall(r"<P=([^>]+)>", m.group(1)):
            if ctx["goal_counts"].get(pid, 0) < 2:
                errs.append(f"{L(ctx, pid)} scored {ctx['goal_counts'].get(pid, 0)} goal(s), not two")
    total = sum(ctx["goal_counts"].values())
    allowed = {total} | set(ctx["team_goals"].values()) | {0}
    for m in re.finditer(GOALS_TOTAL[lang], tagged):
        n = ZH_NUM.get(m.group(1)) or int(m.group(1))
        if n not in allowed and n > 1:
            errs.append(f"{n} goals: there were {total} in the match")
    return errs


def check_player_team(tagged, lang, ctx, names):
    errs = []
    same = [r"<T=([^>]+)>'s <P=([^>]+)>", r"<T=([^>]+)>\s*的\s*<P=([^>]+)>"]
    same_pt = [r"<P=([^>]+)>\s*[（(]<T=([^>]+)>[)）]", r"<P=([^>]+)> (?:scored |scores )?for <T=([^>]+)>",
               r"<P=([^>]+)> (?:drove|led|powered|inspired|carried) <T=([^>]+)>", r"<P=([^>]+)>\s*为\s*<T=([^>]+)>"]
    against = [r"<P=([^>]+)>(?:\s+\w+){0,2}\s+(?:harass\w*|harried|tormented|troubled|terrori[sz]ed|punished|hurt|"
               r"frustrated|against|stunned|undid|undoing|broke) <T=([^>]+)>"]
    for pat in same:
        for m in re.finditer(pat, tagged):
            if m.group(2) in names.players and names.team_of(m.group(2)) != m.group(1):
                errs.append(f"{L(ctx, m.group(2))} does not play for {L(ctx, m.group(1))}")
    for pat in same_pt:
        for m in re.finditer(pat, tagged):
            if m.group(1) in names.players and names.team_of(m.group(1)) != m.group(2):
                errs.append(f"{L(ctx, m.group(1))} does not play for {L(ctx, m.group(2))}")
    for pat in against:
        for m in re.finditer(pat, tagged):
            if m.group(1) in names.players and names.team_of(m.group(1)) == m.group(2):
                errs.append(f"{L(ctx, m.group(1))} plays for {L(ctx, m.group(2))}, not against them")
    return errs


# characters that Chinese transliterations of Western names are built from; three in a row is a name
TRANSLIT = "斯尔克德特拉里奇森韦曼恩伊诺格布罗夫维纳卡莱利尼茨兹汉丹伦南贝哈蒂索科沃洛芙娜奥佩基塞瓦亚阿埃欧乌菲赫杰莫什比希鲁迪戈泽霍施舒凯亨艾彼托萨路约翰杰史琼戴麦温蒙泰勒纽兰图巴帕瑞吉姆肯登"


def check_zh_names(text, tagged, ctx):
    errs = []
    if "·" in text:
        errs.append("transliterated name with ·")
    m = re.search(f"[{TRANSLIT}]{{3,}}", text)
    if m:
        errs.append(f"transliterated name '{m.group(0)}'")
    m = re.search(r"([一-鿿]{2,4})(?:队|城队|联队|联)(?![员伍长友])", text)
    if m and m.group(1) not in ("两", "球", "对手", "本", "全", "主", "客", "双方", "球员", "这支", "整"):
        if not re.search(r"(?:两队|球队|全队|对手|本队|主队|客队|全联|联赛|联手|联系|联动)", m.group(0)):
            errs.append(f"translated team name '{m.group(0)}'")
    return errs


def check_text(text, lang, ctx, names, types=TYPES):
    ctx = {**ctx, "label": names.label}
    text_n = _num_words(text) if lang == "en" else text
    tagged = names.tag(text_n)
    found = {
        "scoring_team": check_scoring_team(tagged, lang, ctx),
        "passer_receiver": check_passes(tagged, lang, ctx),
        "shooter": check_shooter(tagged, lang, ctx),
        "invented_event": check_events(text_n, lang, ctx),
        "comparison": check_comparisons(tagged, lang, ctx),
        "number": check_numbers(text_n, lang, ctx),
        "possession_window": check_possession_window(text_n, lang, ctx),
        "player_team": check_player_team(tagged, lang, ctx, names) + check_goal_counts(tagged, lang, ctx, names),
        "colour": check_colour(text_n, lang, ctx),
        "zh_names": check_zh_names(text, tagged, ctx) if lang == "zh" else [],
    }
    return {k: v for k, v in found.items() if v and k in types}


def reasons(errors):
    """Flatten check_text output into rejection reasons for a writer's retry prompt."""
    return [r for v in errors.values() for r in v]


# -- running over bundles ------------------------------------------------------------------------
def _items(bundle, studio, stats):
    names = Names(bundle["players"], bundle["teams"])
    for m in bundle["moments"]:
        ctx = moment_context(bundle, m, names)
        for p, by_lang in m["commentary"].items():
            for l, c in by_lang.items():
                yield "live", f"{m['clock']} {m['kind']} {p}/{l}", c, l, ctx, names, m
    if bundle.get("recap"):
        ctx = recap_context(bundle, names)
        for p, by_lang in bundle["recap"].items():
            for l, c in by_lang.items():
                yield "recap", f"recap {p}/{l}", c, l, ctx, names, None
    if studio and stats:
        players, teams, side = studio_players(stats)
        snames = Names(players, teams)
        for s in studio["sections"]:
            ctx = studio_context(stats, s, side)
            for p, by_lang in s["text"].items():
                for l, c in by_lang.items():
                    yield "studio", f"{s['id']} {p}/{l}", c, l, ctx, snames, None


async def _llm_flags(rows, bundle, model):
    from agent_framework import Agent
    from agent_framework.ollama import OllamaChatClient

    from .agents import AUDITOR_INSTRUCTIONS, audit, expected_claims

    agent = Agent(OllamaChatClient(model=model), AUDITOR_INSTRUCTIONS, name="claim_auditor")
    match = {**bundle, "_events": bundle["events"]}
    for r in rows:
        if r["surface"] == "live" and r["agent"]:
            r["llm_auditor"] = await audit(agent, r["text"], expected_claims(r["_moment"], match))


def evaluate(name, llm=False, model="qwen3.5:9b", folder=BUNDLES):
    bundle = json.loads((folder / f"{name}.json").read_text())
    studio_path, stats_path = folder / f"{name}-studio.json", folder / f"{name}-stats.json"
    studio = json.loads(studio_path.read_text()) if studio_path.exists() else None
    stats = json.loads(stats_path.read_text()) if stats_path.exists() else None
    rows = []
    for surface, label, c, lang, ctx, names, moment in _items(bundle, studio, stats):
        agent = c["source"].startswith("agent")
        errors = check_text(c["text"], lang, ctx, names) if agent else {}
        rows.append({"surface": surface, "label": label, "lang": lang, "text": c["text"], "agent": agent,
                     "attempts": c.get("attempts"), "seconds": c.get("seconds"), "errors": errors, "_moment": moment})
    if llm:
        asyncio.run(_llm_flags(rows, bundle, model))
    for r in rows:
        r.pop("_moment")
    return rows, bundle.get("latency"), bundle.get("pipeline")


def variety(rows):
    """Wording variety of live lines per viewer track: distinct bigrams, repeated openers, most repeated phrase."""
    from collections import Counter

    out = {}
    for key in sorted({r["label"].split()[-1] for r in rows if r["surface"] == "live"}):
        lang = key.split("/")[1]
        texts = [r["text"] for r in rows if r["surface"] == "live" and r["label"].endswith(key)]
        if lang == "en":
            toks = [re.findall(r"[a-z']+", t.lower()) for t in texts]
            openers = [" ".join(t[:2]) for t in toks]
            n = 3
        else:  # characters, with Latin names removed
            toks = [list(re.sub(r"[A-Za-z0-9\s.,，。:：%-]+", "", t)) for t in texts]
            openers = ["".join(t[:3]) for t in toks]
            n = 4
        bigrams = [tuple(t[i:i + 2]) for t in toks for i in range(len(t) - 1)]
        grams = Counter(tuple(t[i:i + n]) for t in toks for i in range(len(t) - n + 1))
        top, count = grams.most_common(1)[0] if grams else ((), 0)
        out[key] = {"distinct_2": round(len(set(bigrams)) / max(1, len(bigrams)), 3),
                    "repeated_openers": round(1 - len(set(openers)) / max(1, len(openers)), 3),
                    "top_phrase": (" " if lang == "en" else "").join(top), "top_phrase_count": count}
    if out:
        out["mean_distinct_2"] = round(statistics.mean(v["distinct_2"] for v in out.values()), 3)
    return out


def summarise(rows):
    out = {}
    for surface in ("live", "recap", "studio", "all"):
        sel = [r for r in rows if surface == "all" or r["surface"] == surface]
        agent = [r for r in sel if r["agent"]]
        if not sel:
            continue
        s = {"texts": len(sel), "agent": len(agent), "fallback": len(sel) - len(agent),
             "with_any_error": sum(bool(r["errors"]) for r in agent)}
        s["error_rate"] = round(s["with_any_error"] / len(agent), 3) if agent else None
        s["by_type"] = {t: sum(t in r["errors"] for r in agent) for t in TYPES}
        if any("llm_auditor" in r for r in agent):
            s["llm_auditor_flags"] = sum(bool(r.get("llm_auditor")) for r in agent)
        out[surface] = s
    live = [r["seconds"] for r in rows if r["surface"] == "live" and r["seconds"] is not None]
    if live:
        out["live_seconds_median"] = round(statistics.median(live), 1)
    out["variety"] = variety(rows)
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("bundles", nargs="*", default=["remix-42", "remix-21", "remix-7"])
    parser.add_argument("--label", default="latest")
    parser.add_argument("--llm", action="store_true", help="Also re-run the LLM claim auditor on live lines")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--show", action="store_true", help="Print every flagged text")
    parser.add_argument("--dir", default=str(BUNDLES), help="Folder with the bundles (default docs/synthetic)")
    args = parser.parse_args()
    report = {"label": args.label, "bundles": {}}
    all_rows = []
    for name in args.bundles:
        rows, latency, pipeline = evaluate(name, args.llm, args.model, Path(args.dir))
        all_rows += rows
        report["bundles"][name] = {"summary": summarise(rows), "latency": latency,
                                   "pipeline": {k: pipeline.get(k) for k in ("lines", "agent_lines", "fallback_lines",
                                                                              "retried_lines")} if pipeline else None,
                                   "flagged": [r for r in rows if r["errors"] or r.get("llm_auditor")]}
        s = report["bundles"][name]["summary"]
        v = s["variety"]
        print(f"\n{name}: live median {s.get('live_seconds_median')} s; variety distinct-2 {v.get('mean_distinct_2')}, "
              + ", ".join(f"{k} opener-repeat {x['repeated_openers']} top '{x['top_phrase']}' x{x['top_phrase_count']}"
                          for k, x in v.items() if isinstance(x, dict)))
        for surface in ("live", "recap", "studio"):
            if surface in s:
                x = s[surface]
                types = ", ".join(f"{k} {v}" for k, v in x["by_type"].items() if v)
                llm = f", llm auditor flags {x['llm_auditor_flags']}" if "llm_auditor_flags" in x else ""
                print(f"  {surface:6} {x['agent']}/{x['texts']} agent, {x['with_any_error']} with errors "
                      f"({x['error_rate']}){llm}: {types or 'none'}")
        if args.show:
            for r in report["bundles"][name]["flagged"]:
                print(f"    [{r['label']}] {r['text']}\n      -> {r['errors']} {r.get('llm_auditor') or ''}")
    report["overall"] = summarise(all_rows)
    o = report["overall"]["all"]
    print(f"\nall: {o['agent']}/{o['texts']} agent texts, {o['with_any_error']} with errors ({o['error_rate']}); "
          + ", ".join(f"{k} {v}" for k, v in o["by_type"].items() if v))
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{args.label}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
