"""Microsoft Agent Framework workflow: synthetic feed -> analyst -> narrator <-> fact check -> recap.

Graph (each box is an Agent Framework executor; the two model-backed ones wrap an `Agent`):

    FeedIngestor --jobs--> Narrator --> FactChecker --> ClaimAuditor --approved--> Producer --> RecapWriter
                              ^              |               |
                              +--- retry ----+---------------+

The narrator, claim auditor and recap writer are LLM agents (local Ollama via `OllamaChatClient`;
the client is swappable). The ingestor and fact checker are deterministic: the checker rejects a
line that names an uninvolved player, uses a number or measurement absent from the facts, or
mentions an action the facts do not support. The claim auditor asks a model only to *extract* who
the line says shot, who won the ball, which team leads and in which half, then compares those
claims with the feed in code. A rejected line goes back to the narrator with the reasons; after
the last attempt a template line is used, so every moment always gets commentary, and the export
records which path produced each line.
"""
import json
import re
import time
from dataclasses import dataclass, field

from agent_framework import Agent, Executor, WorkflowBuilder, WorkflowContext, handler

from .analyst import analyse

PROFILES = ("casual", "analyst")
LANGS = ("en", "zh")
MAX_ATTEMPTS = 3

STYLE = {
    "casual": ("Audience: a casual fan. One sentence, at most 20 words: what happened and why it matters, "
               "in plain words, at most one number. Call xG chance quality."),
    "analyst": ("Audience: an analyst. One or two sentences, at most 28 words: the decisive metric and the "
                "tactical reason it matters."),
}
LANGUAGE = {
    "en": "Write in English.",
    "zh": ("Write in Simplified Chinese (at most 45 characters for a casual fan, 65 for an analyst). Keep player and team names exactly as they "
           "appear in the facts, in Latin letters; never translate or transliterate them into Chinese characters. "
           "Write xG as 预期进球."),
}
NARRATOR_INSTRUCTIONS = (
    "You are a live football commentator writing one on-screen line for a single match moment. "
    "Use only the facts and reasons you are given. Never invent players, actions, body parts, set pieces, "
    "match minutes or scores. Use only numbers that appear in the facts or reasons. "
    'Reply with JSON only: {"line": "<commentary>"}.'
)
AUDITOR_INSTRUCTIONS = (
    "You extract claims from one football commentary text (English or Chinese). Do not judge, only extract what "
    "the text itself says, and use null for anything it does not say. Reply with JSON only: "
    '{"shooter": surname of the player who took a shot, "ball_winner": surname of the player who won or '
    'intercepted the ball, "ahead_in_score": team the text says is winning the match, '
    '"half": "own" or "opponent" if the text says in which half the ball was won}'
)
RECAP_INSTRUCTIONS = (
    "You write short automated match recaps for a studio graphic. Use only the facts provided: final "
    "statistics and the key moments in order. Never invent players, actions, minutes or scores, and use only "
    'numbers that appear in the facts. Reply with JSON only: {"recap": "<text>"}.'
)

# Terms that describe something the feed never records. A term is allowed only if the facts use it.
CLAIMS = {
    "en": ["goalkeeper", "keeper", "save", "saved", "saves", "scores", "scored", "scoring", "the net",
           "equaliser", "equalizer", "equalises", "equalizes", "leads", "lead", "level", "header",
           "headed", "volley", "cross", "crosses", "corner", "free kick", "free-kick", "penalty",
           "offside", "foul", "card", "assist", "through ball", "dribble", "dribbles", "dribbling",
           "nutmeg", "counter", "post", "crossbar", "woodwork", "counter-attack", "counterattack", "one-two",
           "curl", "curls", "chip", "celebrates", "crowd", "fans", "minute", "half-time", "winner",
           "brace", "hat-trick", "rebound", "blocked", "off target", "wide", "we", "our", "us", "enemy",
           "home", "away", "first half", "second half", "nil"],
    "zh": ["门将", "扑", "进球", "破门", "领先", "扳平", "反超", "头球", "凌空", "传中", "角球", "任意球",
           "点球", "越位", "犯规", "黄牌", "红牌", "助攻", "直塞", "过人", "盘带", "门柱", "横梁", "反击",
           "二过一", "死角", "吊射", "弧线", "观众", "球迷", "庆祝", "补射", "远射", "绝杀", "封堵", "偏出",
           "分钟", "上半场", "下半场", "我们", "我队", "主队", "客队"],
}


@dataclass
class NarrationJob:
    moment: dict
    profile: str
    lang: str
    attempt: int = 1
    rejections: list = field(default_factory=list)
    released: float = field(default_factory=time.perf_counter)


@dataclass
class Candidate:
    job: NarrationJob
    text: str


@dataclass
class Audit:
    job: NarrationJob
    text: str


@dataclass
class Approved:
    job: NarrationJob
    text: str
    source: str


@dataclass
class RecapJob:
    profile: str
    lang: str
    facts: list
    allowed: str
    players: list
    expected: dict


def _json_field(raw, key):
    raw = raw.strip()
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        try:
            return str(json.loads(match.group(0))[key]).strip()
        except (ValueError, KeyError, TypeError):
            pass
    return raw.strip().strip('"')


NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split()) if i >= 2}


def _numbers(text):
    """Digits, plus English number words from two upwards ("four passes" must match a 4 in the facts)."""
    words = [NUMBER_WORDS[w] for w in re.findall(r"[a-z]+", text.lower()) if w in NUMBER_WORDS]
    return re.findall(r"\d+(?:\.\d+)?", text) + words


UNITS = [(r"\s*(?:km/h|kmh|公里/小时|公里每小时)", "kmh"), (r"\s*(?:m\b|metres?\b|meters?\b|米)", "m"),
         (r"\s*%", "%"), (r"\s*/\s*100", "/100"), (r"\s*(?:seconds?\b|secs?\b|秒)", "s")]


def _measures(text):
    """(number, unit) pairs, so a number cannot be reused with a different meaning."""
    pairs = set()
    for m in re.finditer(r"(\d+(?:\.\d+)?)", text):
        rest = text[m.end():m.end() + 12]
        for pattern, unit in UNITS:
            if re.match(pattern, rest, re.I):
                pairs.add((f"{float(m.group(1)):g}", unit))
                break
    return pairs


def verify(text, lang, allowed_text, allowed_players, roster, word_limit, teams=()):
    """Return a list of reasons the text is not supported by the facts (empty when it passes).

    `teams` lists team names as written in the facts; Chinese text must keep at least one of them in
    Latin letters when the facts name a team (models otherwise translate "Bayern" to 拜仁)."""
    reasons = []
    if not text:
        return ["empty output"]
    size = len(text.split()) if lang == "en" else len(text)
    if size > word_limit:
        reasons.append(f"too long ({size} > {word_limit})")
    if lang == "zh" and not re.search(r"[一-鿿]", text):
        reasons.append("not written in Chinese")
    allowed_numbers = set(_numbers(allowed_text))
    allowed_numbers |= {n.lstrip("0") or "0" for n in allowed_numbers} | {f"{float(n):g}" for n in allowed_numbers}
    for n in _numbers(text):
        if n not in allowed_numbers and f"{float(n):g}" not in allowed_numbers:
            reasons.append(f"number {n} is not in the facts")
    for n, unit in _measures(text) - _measures(allowed_text):
        reasons.append(f"{n} {unit} does not match any measurement in the facts")
    if lang == "zh" and any(t in allowed_text for t in teams) and not any(t in text for t in teams) \
            and re.search(r"队|拜仁|科隆|慕尼黑", text):
        reasons.append("team names must stay in Latin letters")
    named_in_facts = any(p["name"].split()[-1] in allowed_text for p in roster)
    if lang == "zh" and ("·" in text or (named_in_facts and not any(p["name"].split()[-1] in text for p in roster))):
        reasons.append("player names must stay in Latin letters")
    lowered, allowed_low = text.lower(), allowed_text.lower()
    found = ((lambda term, hay: re.search(rf"\b{re.escape(term)}\b", hay)) if lang == "en"
             else (lambda term, hay: term in hay))
    for p in roster:
        if p["id"] in allowed_players:
            continue
        surname = p["name"].split()[-1].lower()
        if re.search(rf"\b{re.escape(surname)}\b", lowered):
            reasons.append(f"mentions {p['name']}, who is not in this moment")
    for term in CLAIMS[lang]:
        if found(term, lowered) and not found(term, allowed_low):
            reasons.append(f"claims '{term}' which the facts do not support")
    return reasons


def fallback_line(moment, profile, lang):
    key = moment["facts"][-1][lang]
    why = [w[lang] for w in moment["why"]]
    head = moment["headline"][lang]
    end = "." if lang == "en" else "。"
    parts = [x.rstrip(".。") for x in ([why[0] if why else key] if profile == "casual" else [key] + why[:2])]
    text = (" " if lang == "en" else "").join(x + end for x in parts)
    return f"{head}{': ' if lang == 'en' else '：'}{text}" if profile == "casual" else text


def prompt_facts(moment, profile):
    """The narrator sees only what it needs: the start of the move and its key action (more for analysts).
    Shorter prompts are read faster; the checker still allows anything in the full fact list."""
    facts = moment["facts"]
    key = next(i for i, f in enumerate(facts) if f["event_id"] == moment["key_event"])
    keep = {0, key} | (set(range(max(1, key - 3), key)) if profile == "analyst" else set())
    return [facts[i] for i in sorted(keep)]


def moment_prompt(job):
    m = job.moment
    facts = "\n".join(f"- {f[job.lang]}" for f in prompt_facts(m, job.profile))
    why = "\n".join(f"- {w[job.lang]}" for w in m["why"])
    prompt = (f"{STYLE[job.profile]}\n{LANGUAGE[job.lang]}\n\nMoment: {m['headline']['en']}\n"
              f"Facts, in order:\n{facts}\nWhy it matters:\n{why}\n")
    if job.rejections:
        prompt += ("\nYour previous line was rejected by the fact checker for: "
                   + "; ".join(job.rejections[-1]) + ". Rewrite it using only the facts above.\n")
    return prompt


# Words that restate a recorded outcome, so they are allowed when that outcome is in the facts.
SYNONYMS = {
    ("goal", "en"): "scores scored scoring the net", ("goal", "zh"): "进球 破门",
    ("saved", "en"): "goalkeeper keeper save saves", ("saved", "zh"): "门将 扑救",
    ("wide", "en"): "wide off target", ("wide", "zh"): "偏出 打偏",
    ("blocked", "en"): "blocked block", ("blocked", "zh"): "封堵 被挡",
    ("score_lead", "en"): "lead leads", ("score_lead", "zh"): "领先",
    ("score_level", "en"): "level equaliser equalizer equalises equalizes", ("score_level", "zh"): "扳平 战平",
    ("from_regain", "en"): "counter counter-attack counterattack", ("from_regain", "zh"): "反击 快速反击",
}


def synonyms(facts_and_why, lang):
    words = []
    for item in facts_and_why:
        for key in ("outcome", "code"):
            words.append(SYNONYMS.get((item.get(key), lang), ""))
    return " ".join(words)


def allowed_for(moment, lang):
    outcomes = [{"outcome": moment["outcome"]}] if moment["kind"] in ("goal", "shot") else []
    return " ".join([f[lang] for f in moment["facts"]] + [w[lang] for w in moment["why"]]
                    + [moment["headline"][lang], synonyms(outcomes + moment["why"], lang)])


def _surname(name):
    if not name or str(name).strip().lower() in ("null", "none", "-"):
        return None
    return str(name).split()[-1].lower()


def expected_claims(moment, match):
    """What the feed says about the claim types the auditor extracts."""
    names = {p["id"]: p["name"] for p in match["players"]}
    teams = {t["id"]: t for t in match["teams"]}
    key = next(f for f in moment["facts"] if f["event_id"] == moment["key_event"])
    events = {e["id"]: e for e in match.get("_events", [])}
    shooter = _surname(names[events[key["event_id"]]["actor"]]) if moment["kind"] in ("goal", "shot") else None
    chain = [events[f["event_id"]] for f in moment["facts"] if f["event_id"] in events]
    winners = {_surname(names[e["actor"]]) for e in chain if e["kind"] in ("interception", "tackle")}
    winners |= {_surname(names[e[k]]) for e in chain for k in ("keeper", "blocker") if e.get(k)}
    regains = [e for e in chain if e["kind"] in ("interception", "tackle")]
    area = None
    if regains:
        area = "opponent half" if regains[0]["metrics"]["in_attacking_half"] else "own half"
    st = moment["state"]
    score = events[moment["key_event"]].get("score_after") or {"a": st["a"]["goals"], "b": st["b"]["goals"]}
    leader = None if score["a"] == score["b"] else ("a" if score["a"] > score["b"] else "b")
    return {"shooter": shooter, "winners": winners, "area": area,
            "leader": teams[leader] if leader else None, "teams": list(teams.values())}


def _team_of(text, teams):
    low = (text or "").lower()
    for t in teams:
        if t["name"].lower() in low or low in t["name"].lower() or t["short"].lower() == low:
            return t["id"]
    return None


# Words that signal one of the four audited claim types; a line with none of them skips the model call.
CLAIM_HINTS = re.compile(
    r"shot|shoot|strike|struck|fire|head|score|goal|save|block|win|won|wins|steal|intercept|turnover|tackl|regain|"
    r"lead|ahead|level|equali|half|territory|射|门|进球|扑|封堵|断|抢|夺回|领先|落后|扳平|半场|攻门", re.I)


async def audit(agent, text, exp):
    """Ask the model to extract claims; compare them with the feed in code. Returns rejection reasons."""
    if not CLAIM_HINTS.search(text):
        return []
    response = await agent.run(text, options={"temperature": 0, "max_tokens": 70, "think": False,
                                              "response_format": "json"})
    try:
        raw = json.loads(re.search(r"\{.*\}", response.text, re.S).group(0))
    except (AttributeError, ValueError):
        return []  # unreadable extraction: rely on the rule checker
    half = str(raw.get("half") or "").lower()
    area = "own half" if half.startswith("own") else "opponent half" if half.startswith("opp") else None
    claims = {"shooter": raw.get("shooter"), "winner_of_ball": raw.get("ball_winner"),
              "leading_team": raw.get("ahead_in_score"), "area": area}
    reasons = []
    shooter = _surname(claims.get("shooter"))
    if shooter and exp.get("shooter") is not None and shooter != exp["shooter"]:
        reasons.append(f"the shot was taken by {exp['shooter'].title()}, not {shooter.title()}")
    winner = _surname(claims.get("winner_of_ball"))
    if winner and exp.get("winners") is not None and winner not in exp["winners"]:
        reasons.append(f"{winner.title()} did not win the ball in this moment")
    claimed = claims.get("leading_team")
    if claimed and claimed not in ("null", "None"):
        tid = _team_of(claimed, exp["teams"])
        if exp["leader"] is None:
            if re.search(r"\blead|\bahead|领先", text, re.I):  # "had more xG" is not a claim about the score
                reasons.append("the score is level, no team leads")
        elif tid and tid != exp["leader"]["id"]:
            reasons.append(f"{exp['leader']['name']} lead, not the other team")
    area = claims.get("area")
    if area in ("own half", "opponent half") and exp.get("area") and area != exp["area"]:
        reasons.append(f"the ball was won in the {exp['area']}, not the {area}")
    return reasons


class FeedIngestor(Executor):
    """Runs the feed through the analyst and fans out narration jobs.

    A match dict may carry `_only`: {"moments": [ids], "variants": [[profile, lang]], "recap": bool}
    to narrate a subset, which is how live latency is measured one moment and one viewer at a time.
    """

    def __init__(self):
        super().__init__(id="feed_ingestor")

    @handler
    async def ingest(self, match: dict, ctx: WorkflowContext[NarrationJob]) -> None:
        only = match.pop("_only", {})
        analysis = analyse(match)
        ctx.set_state("match", {**match, "_events": analysis["events"]})
        ctx.set_state("recap", only.get("recap", True))
        ctx.set_state("analysis", analysis)
        variants = only.get("variants") or [(p, l) for p in PROFILES for l in LANGS]
        chosen = [m for m in analysis["moments"] if only.get("moments") is None or m["id"] in only["moments"]]
        jobs = [NarrationJob(m, p, l) for m in chosen for p, l in variants]
        ctx.set_state("expected", len(jobs))
        ctx.set_state("approved", [])
        for job in jobs:
            await ctx.send_message(job)


class Narrator(Executor):
    """LLM agent: one grounded commentary line per moment, profile and language."""

    def __init__(self, client, model_label):
        super().__init__(id="narrator")
        self.agent = Agent(client, NARRATOR_INSTRUCTIONS, name="narrator")
        self.model_label = model_label

    @handler
    async def narrate(self, job: NarrationJob, ctx: WorkflowContext[Candidate]) -> None:
        try:
            response = await self.agent.run(moment_prompt(job), options={
                "temperature": 0.5, "max_tokens": 110, "think": False, "response_format": "json"})
            text = _json_field(response.text, "line")
        except Exception as exc:  # model unavailable: let the checker route to the fallback
            text = ""
            job.rejections.append([f"model error: {exc}"])
            job.attempt = MAX_ATTEMPTS
        await ctx.send_message(Candidate(job, text))


class FactChecker(Executor):
    """Deterministic verifier: approve, send back with reasons, or substitute a template line."""

    def __init__(self, model_label):
        super().__init__(id="fact_checker")
        self.model_label = model_label

    @handler
    async def check(self, cand: Candidate, ctx: WorkflowContext[NarrationJob | Audit | Approved]) -> None:
        job, match = cand.job, ctx.get_state("match")
        limit = (26 if job.profile == "casual" else 36) if job.lang == "en" else (60 if job.profile == "casual" else 85)
        teams = [n for t in match["teams"] for n in (t["name"], t.get("call", t["name"]), t["short"])]
        reasons = verify(cand.text, job.lang, allowed_for(job.moment, job.lang),
                         set(job.moment["players"]), match["players"], limit, teams)
        if not reasons:
            await ctx.send_message(Audit(job, cand.text))
            return
        if cand.text:
            job.rejections.append(reasons)
        await retry_or_fallback(job, ctx)


async def retry_or_fallback(job, ctx):
    if job.attempt < MAX_ATTEMPTS:
        job.attempt += 1
        await ctx.send_message(job)
    else:
        await ctx.send_message(Approved(job, fallback_line(job.moment, job.profile, job.lang), "template_fallback"))


class ClaimAuditor(Executor):
    """LLM agent extracts the line's claims; code compares them with the feed."""

    def __init__(self, client, model_label):
        super().__init__(id="claim_auditor")
        self.agent = Agent(client, AUDITOR_INSTRUCTIONS, name="claim_auditor")
        self.model_label = model_label

    @handler
    async def audit(self, item: Audit, ctx: WorkflowContext[NarrationJob | Approved]) -> None:
        job = item.job
        try:
            reasons = await audit(self.agent, item.text, expected_claims(job.moment, ctx.get_state("match")))
        except Exception:
            reasons = []  # auditor unavailable: the rule check already passed
        if not reasons:
            await ctx.send_message(Approved(job, item.text, f"agent:{self.model_label}"))
            return
        job.rejections.append(reasons)
        await retry_or_fallback(job, ctx)


class Producer(Executor):
    """Collects approved lines into the overlay timeline, then requests recaps."""

    def __init__(self):
        super().__init__(id="producer")

    @handler
    async def collect(self, item: Approved, ctx: WorkflowContext[RecapJob, dict]) -> None:
        item.job.done = time.perf_counter()
        approved = ctx.get_state("approved") + [item]
        ctx.set_state("approved", approved)
        if len(approved) < ctx.get_state("expected"):
            return
        match, analysis = ctx.get_state("match"), ctx.get_state("analysis")
        for m in analysis["moments"]:
            m["commentary"] = {p: {} for p in PROFILES}
        by_id = {m["id"]: m for m in analysis["moments"]}
        for a in approved:
            by_id[a.job.moment["id"]]["commentary"][a.job.profile][a.job.lang] = {
                "text": a.text, "source": a.source, "attempts": a.job.attempt,
                "rejections": a.job.rejections, "seconds": round(a.job.done - a.job.released, 2)}
        ctx.set_state("analysis", analysis)  # state values are snapshots; store the annotated copy
        if not ctx.get_state("recap"):
            await ctx.yield_output({"analysis": analysis, "recaps": None, "final_stats": final_stats(match, analysis)})
            return
        ctx.set_state("recaps", {p: {} for p in PROFILES})
        stats = final_stats(match, analysis)
        players = sorted({pid for m in analysis["moments"] for pid in m["players"]})
        fa, fb = stats["a"]["goals"], stats["b"]["goals"]
        recap_expected = {"leader": None if fa == fb else match["teams"][0 if fa > fb else 1],
                          "teams": match["teams"]}
        for p in PROFILES:
            for l in LANGS:
                facts = [stats[l]] + [f"{m['clock']} {m['headline'][l]}: {m['facts'][-1][l]}"
                                      for m in analysis["moments"]]
                allowed = " ".join(facts + [w[l] for m in analysis["moments"] for w in m["why"]]
                                   + [synonyms([{"outcome": m.get("outcome")}] + m["why"], l)
                                      for m in analysis["moments"]])
                await ctx.send_message(RecapJob(p, l, facts, allowed, players, recap_expected))


class RecapWriter(Executor):
    """LLM agent: automated recap per profile and language, checked like commentary lines."""

    def __init__(self, client, model_label):
        super().__init__(id="recap_writer")
        self.agent = Agent(client, RECAP_INSTRUCTIONS, name="recap_writer")
        self.auditor = Agent(client, AUDITOR_INSTRUCTIONS, name="recap_auditor")
        self.model_label = model_label

    @handler
    async def write(self, job: RecapJob, ctx: WorkflowContext[None, dict]) -> None:
        match = ctx.get_state("match")
        style = ("For a casual fan: 3 short sentences, plain words, at most 70 words."
                 if job.profile == "casual" else
                 "For an analyst: 3-4 sentences, at most 90 words, lead with the statistics that explain the result.")
        limit = (80 if job.profile == "casual" else 100) if job.lang == "en" else 200
        prompt = f"{style}\n{LANGUAGE[job.lang]}\n\nFacts:\n" + "\n".join(f"- {f}" for f in job.facts)
        result, rejections = None, []
        for _ in range(MAX_ATTEMPTS):
            try:
                response = await self.agent.run(prompt, options={
                    "temperature": 0.4, "max_tokens": 400, "think": False, "response_format": "json"})
                text = _json_field(response.text, "recap")
            except Exception as exc:
                rejections.append([f"model error: {exc}"])
                break
            reasons = verify(text, job.lang, job.allowed, set(job.players), match["players"], limit)
            if not reasons:
                try:
                    reasons = await audit(self.auditor, text, job.expected)
                except Exception:
                    reasons = []
            if not reasons:
                result = {"text": text, "source": f"agent:{self.model_label}"}
                break
            rejections.append(reasons)
            prompt += "\nYour previous recap was rejected for: " + "; ".join(reasons) + ". Rewrite it.\n"
        if result is None:
            heads = [f.split(": ", 1)[0] for f in job.facts[1:4]]
            joiner = ", " if job.lang == "en" else "，"
            tail = (" Key moments: " if job.lang == "en" else "关键时刻：") + joiner.join(heads)
            result = {"text": job.facts[0] + tail, "source": "template_fallback"}
        result["rejections"] = rejections
        recaps = ctx.get_state("recaps")
        recaps[job.profile][job.lang] = result
        ctx.set_state("recaps", recaps)
        if sum(len(v) for v in recaps.values()) == len(PROFILES) * len(LANGS):
            await ctx.yield_output({"analysis": ctx.get_state("analysis"), "recaps": recaps,
                                    "final_stats": final_stats(match, ctx.get_state("analysis"))})


def final_stats(match, analysis):
    f = analysis["final"]
    a, b = f["a"], f["b"]
    na, nb = match["teams"][0]["name"], match["teams"][1]["name"]
    en = (f"Final: {na} {a['goals']}-{b['goals']} {nb}. Possession {a['possession_pct']}%-{b['possession_pct']}%. "
          f"Shots {a['shots']}-{b['shots']} (on target {a['on_target']}-{b['on_target']}). "
          f"xG {a['xg']:.2f}-{b['xg']:.2f}. Pass completion {a['pass_pct']}%-{b['pass_pct']}%.")
    zh = (f"终场：{na} {a['goals']}-{b['goals']} {nb}。控球率{a['possession_pct']}%-{b['possession_pct']}%。"
          f"射门{a['shots']}-{b['shots']}（射正{a['on_target']}-{b['on_target']}）。"
          f"预期进球{a['xg']:.2f}-{b['xg']:.2f}。传球成功率{a['pass_pct']}%-{b['pass_pct']}%。")
    return {"en": en, "zh": zh, "a": a, "b": b}


def build_workflow(client, model_label):
    ingest, narrator = FeedIngestor(), Narrator(client, model_label)
    checker, producer, recap = FactChecker(model_label), Producer(), RecapWriter(client, model_label)
    auditor = ClaimAuditor(client, model_label)
    return (WorkflowBuilder(name="inside-the-game", start_executor=ingest, max_iterations=200)
            .add_edge(ingest, narrator)
            .add_edge(narrator, checker)
            .add_edge(checker, narrator, condition=lambda m: isinstance(m, NarrationJob))
            .add_edge(checker, auditor, condition=lambda m: isinstance(m, Audit))
            .add_edge(checker, producer, condition=lambda m: isinstance(m, Approved))
            .add_edge(auditor, narrator, condition=lambda m: isinstance(m, NarrationJob))
            .add_edge(auditor, producer, condition=lambda m: isinstance(m, Approved))
            .add_edge(producer, recap)
            .build())
