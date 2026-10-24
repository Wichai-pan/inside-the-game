"""Post-match studio analysis: storylines from full-match stats, written by an agent in a pundit voice.

    StoryPlanner --section jobs--> SectionWriter --draft--> SectionChecker --approved--> StudioProducer
                                        ^                        |
                                        +------- retry ----------+

The planner is deterministic: it turns the stats layer (insidegame/stats.py) into six storylines,
each with its own facts, the players it may name and a graphic spec. The writer is an LLM agent that
follows a style guide written by the team (no broadcast transcripts are copied). The checker reuses
the commentary rules (names, numbers, measurements, unsupported terms) and the claim auditor for who
is ahead, then retries or falls back to a template.

    .venv/bin/python -m insidegame.studio --model qwen3.5:9b
"""
import argparse
import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent_framework import Agent, Executor, WorkflowBuilder, WorkflowContext, handler

from .agents import AUDITOR_INSTRUCTIONS, LANGUAGE, audit, verify

ROOT = Path(__file__).resolve().parent.parent
PROFILES, LANGS, MAX_ATTEMPTS = ("casual", "analyst"), ("en", "zh"), 3

# House style for studio analysis. The team refines this after studying post-match shows; it describes
# patterns in our own words rather than quoting any programme.
STYLE_GUIDE = """You are the studio pundit on a post-match show. House style:
- Open with a verdict: one clear opinion about what decided the match, not a list of events.
- Back every opinion with one or two numbers from the facts; never use a number that is not there.
- Contrast is the engine: scoreline versus performance, one half versus the other, one team's plan versus the other's.
- Name the moment or player that swung it, and say why in football terms (territory, pressing, chance quality, crosses).
- Talk to the viewer, short spoken sentences, no bullet points, no hedging filler.
- Every opinion must follow from the facts. Do not add anything the facts do not state: no fatigue, collapse,
  morale, titles or league position, home or away status, weather, crowd, or where on the pitch something happened.
- Keep team and player names exactly as written in the facts.
Reply with JSON only: {"text": "<what you say on air>"}"""

SECTION_BRIEF = {
    "verdict": "Give the overall verdict on the match.",
    "turning_point": "Explain the moment or moments that swung the result.",
    "momentum": "Describe how control of the game shifted between the halves.",
    "tactics": "Explain the main tactical contrast between the two teams.",
    "numbers": ("Explain whether the scoreline was a fair reflection of the chances. Compare the xG with and "
                "without penalties and say which side created more from open play."),
    "player": "Pick out the standout individual performances and justify them.",
}
LENGTH = {"casual": ("2 short sentences, at most 40 words", "at most 80 characters"),
          "analyst": ("2-3 sentences, at most 60 words", "at most 120 characters")}


def _min(m):
    return int(m) + 1  # football convention: 7.8 minutes played is the 8th minute


def _nth(n):
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def storylines(stats):
    teams = {t["team"]: t for t in stats["teams"]}
    (hn, h), (an, a) = list(teams.items())
    short = {hn: "Köln" if "Köln" in hn else hn, an: "Bayern" if "Bayern" in an else an}
    H, A = short[hn], short[an]
    segs = stats["segments"]
    goals = stats["goals"]
    winner = H if h["goals"] > a["goals"] else A if a["goals"] > h["goals"] else None
    players = {p["player"]: p for p in stats["players"]}

    def section(key, title_en, title_zh, en, zh, names, graphic):
        return {"id": key, "title": {"en": title_en, "zh": title_zh}, "facts": {"en": en, "zh": zh},
                "players": sorted(set(names)), "graphic": graphic}

    out = []
    # 1. Verdict
    en = [f"Final score: {H} {h['goals']}-{a['goals']} {A}."]
    zh = [f"终场比分：{H} {h['goals']}-{a['goals']} {A}。"]
    if winner:
        en.append(f"{winner} won the match.")
        zh.append(f"{winner} 赢下了比赛。")
    en.append(f"xG: {H} {h['xg']:.2f}, {A} {a['xg']:.2f}. Possession: {H} {h['possession_pct']}%, {A} {a['possession_pct']}%.")
    zh.append(f"预期进球：{H} {h['xg']:.2f}，{A} {a['xg']:.2f}。控球率：{H} {h['possession_pct']}%，{A} {a['possession_pct']}%。")
    low = [g for g in goals if g["xg"] < 0.1]
    if low:
        en.append(f"{len(low)} of the {len(goals)} goals came from chances under 0.10 xG.")
        zh.append(f"{len(goals)} 个进球中有 {len(low)} 个来自预期进球低于 0.10 的机会。")
    out.append(section("verdict", "The verdict", "比赛结论", en, zh, [], {
        "type": "compare", "rows": [["Goals", h["goals"], a["goals"]], ["xG", h["xg"], a["xg"]],
                                    ["Shots", h["shots"], a["shots"]], ["Possession %", h["possession_pct"], a["possession_pct"]]]}))

    # 2. Turning points: the goals, in order, with the running score
    en, zh, names, score = [], [], [], {H: 0, A: 0}
    for g in goals:
        side = short[g["team"]]
        score[side] += 1
        how_en = "from the penalty spot" if g["penalty"] else f"from a {g['xg']:.2f} xG chance"
        how_zh = "罚进点球" if g["penalty"] else f"把握住预期进球 {g['xg']:.2f} 的机会"
        ast_en = f", set up by {g['assist']}" if g.get("assist") else ""
        ast_zh = f"，助攻者 {g['assist']}" if g.get("assist") else ""
        en.append(f"In the {_nth(_min(g['minute']))} minute {g['player']} scored for {side} {how_en}{ast_en}: {H} {score[H]}-{score[A]} {A}.")
        zh.append(f"第 {_min(g['minute'])} 分钟，{g['player']} 为 {side} {how_zh}{ast_zh}，比分 {H} {score[H]}-{score[A]} {A}。")
        names += [g["player"]] + ([g["assist"]] if g.get("assist") else [])
    if len(goals) >= 2:
        gap = _min(goals[-1]["minute"]) - _min(goals[-2]["minute"])
        en.append(f"The last goal came {gap} minutes after the previous one and was the winner.")
        zh.append(f"最后一个进球距上一个进球只有 {gap} 分钟，成为制胜球。")
        last = players.get(goals[-1]["player"])
        if last and last["minutes"] <= 20:
            en.append(f"{last['player']} had been on the pitch for {last['minutes']} minutes.")
            zh.append(f"{last['player']} 当时才上场 {last['minutes']} 分钟。")
    out.append(section("turning_point", "The turning point", "转折点", en, zh, names, {
        "type": "timeline", "goals": [{"minute": _min(g["minute"]), "team": short[g["team"]], "player": g["player"],
                                       "xg": g["xg"], "penalty": g["penalty"]} for g in goals]}))

    # 3. Momentum: possession and field tilt by 15 minutes
    first = [s for s in segs[:3]]
    second = [s for s in segs[3:]]
    best = max(segs, key=lambda s: s["home"]["field_tilt_pct"])
    worst = min(segs, key=lambda s: s["home"]["possession_pct"])
    en = [f"Between minutes {worst['minutes']}, {A} had {worst['away']['possession_pct']}% possession.",
          f"Between minutes {best['minutes']}, {H} had a field tilt of {best['home']['field_tilt_pct']}%, meaning they had most of the ball in the final third.",
          f"First-half shots: {H} {sum(s['home']['shots'] for s in first)}, {A} {sum(s['away']['shots'] for s in first)}. "
          f"Second-half shots: {H} {sum(s['home']['shots'] for s in second)}, {A} {sum(s['away']['shots'] for s in second)}."]
    zh = [f"第 {worst['minutes']} 分钟，{A} 控球率 {worst['away']['possession_pct']}%。",
          f"第 {best['minutes']} 分钟，{H} 的场地倾斜度达到 {best['home']['field_tilt_pct']}%，即大部分在前场三分之一的控球属于他们。",
          f"上半场射门：{H} {sum(s['home']['shots'] for s in first)}，{A} {sum(s['away']['shots'] for s in first)}。"
          f"下半场射门：{H} {sum(s['home']['shots'] for s in second)}，{A} {sum(s['away']['shots'] for s in second)}。"]
    out.append(section("momentum", "Momentum", "比赛走势", en, zh, [], {
        "type": "segments", "metric": "field_tilt_pct",
        "rows": [{"minutes": s["minutes"], "home": s["home"]["field_tilt_pct"], "away": s["away"]["field_tilt_pct"],
                  "home_possession": s["home"]["possession_pct"], "away_possession": s["away"]["possession_pct"]} for s in segs]}))

    # 4. Tactics: crosses, pressing, progression
    en = [f"Crosses: {H} {h['crosses']}, {A} {a['crosses']}.",
          f"PPDA (opponent passes allowed per defensive action, lower means more pressing): {A} {a['ppda']}, {H} {h['ppda']}.",
          f"Progressive passes: {H} {h['progressive_passes']}, {A} {a['progressive_passes']}. Final-third entries: {H} {h['final_third_entries']}, {A} {a['final_third_entries']}."]
    zh = [f"传中：{H} {h['crosses']} 次，{A} {a['crosses']} 次。",
          f"PPDA（每次防守动作前允许对手传球数，越低代表逼抢越积极）：{A} {a['ppda']}，{H} {h['ppda']}。",
          f"向前推进的传球：{H} {h['progressive_passes']}，{A} {a['progressive_passes']}。进入前场三分之一：{H} {h['final_third_entries']}，{A} {a['final_third_entries']}。"]
    out.append(section("tactics", "The tactical battle", "战术对决", en, zh, [], {
        "type": "compare", "rows": [["Crosses", h["crosses"], a["crosses"]], ["PPDA", h["ppda"], a["ppda"]],
                                    ["Progressive passes", h["progressive_passes"], a["progressive_passes"]],
                                    ["Final-third entries", h["final_third_entries"], a["final_third_entries"]]]}))

    # 5. Numbers versus the scoreline
    pens = {short[s["team"]]: s["xg"] for s in stats["shots"] if s.get("penalty")}
    np_h, np_a = h["xg"] - pens.get(H, 0), a["xg"] - pens.get(A, 0)
    en = [f"xG: {H} {h['xg']:.2f}, {A} {a['xg']:.2f}. Goals: {H} {h['goals']}, {A} {a['goals']}."]
    zh = [f"预期进球：{H} {h['xg']:.2f}，{A} {a['xg']:.2f}。进球：{H} {h['goals']}，{A} {a['goals']}。"]
    if pens:
        en.append(f"Without penalties, xG was {H} {np_h:.2f}, {A} {np_a:.2f}.")
        zh.append(f"去掉点球后，预期进球为 {H} {np_h:.2f}，{A} {np_a:.2f}。")
    for side, t in ((H, h), (A, a)):
        en.append(f"{side} scored {t['goals']} from {t['shots']} shots, {t['on_target']} on target.")
        zh.append(f"{side} {t['shots']} 次射门、{t['on_target']} 次射正，打进 {t['goals']} 球。")
    out.append(section("numbers", "Did they deserve it?", "比分公平吗", en, zh, [], {
        "type": "compare", "rows": [["xG", h["xg"], a["xg"]], ["Non-penalty xG", round(np_h, 2), round(np_a, 2)],
                                    ["Goals", h["goals"], a["goals"]], ["Shots on target", h["on_target"], a["on_target"]]]}))

    # 6. Standout players
    regulars = [p for p in stats["players"] if p["minutes"] >= 45]
    picks = {
        "distance": max(regulars, key=lambda p: p["distance_km"]),
        "sprints": max(regulars, key=lambda p: p["sprints"]),
        "shots": max(stats["players"], key=lambda p: (p["shots"], p["xg"])),
        "tackles": max(stats["players"], key=lambda p: p["tackles_won"]),
        "xt": max(regulars, key=lambda p: p["xt"]),
    }
    sh = lambda p: "Köln" if "Köln" in p["team"] else "Bayern"
    d, sp, s_, tk, xt = picks["distance"], picks["sprints"], picks["shots"], picks["tackles"], picks["xt"]
    en = [f"{d['player']} ({sh(d)}) covered the most ground: {d['distance_km']} km.",
          f"{sp['player']} ({sh(sp)}) made the most sprints, {sp['sprints']}, with a top speed of {sp['top_speed_kmh']} km/h.",
          f"{s_['player']} ({sh(s_)}) took the most shots: {s_['shots']}, worth {s_['xg']:.2f} xG.",
          f"{tk['player']} ({sh(tk)}) won the most tackles: {tk['tackles_won']}.",
          f"{xt['player']} ({sh(xt)}) added the most threat with his passing: {xt['xt']:.2f} xT."]
    zh = [f"{d['player']}（{sh(d)}）跑动最多：{d['distance_km']} 公里。",
          f"{sp['player']}（{sh(sp)}）冲刺次数最多，{sp['sprints']} 次，最高时速 {sp['top_speed_kmh']} 公里。",
          f"{s_['player']}（{sh(s_)}）射门最多：{s_['shots']} 次，预期进球 {s_['xg']:.2f}。",
          f"{tk['player']}（{sh(tk)}）抢断成功最多：{tk['tackles_won']} 次。",
          f"{xt['player']}（{sh(xt)}）通过传球制造的威胁最多：xT {xt['xt']:.2f}。"]
    out.append(section("player", "Standout performers", "表现突出的球员", en, zh, [p["player"] for p in picks.values()], {
        "type": "players", "rows": [{"player": p["player"], "team": sh(p), "stat": k,
                                     "value": {"distance": p["distance_km"], "sprints": p["sprints"], "shots": p["shots"],
                                               "tackles": p["tackles_won"], "xt": p["xt"]}[k]} for k, p in picks.items()]}))
    leader = None if not winner else {"id": "w", "name": hn if winner == H else an, "short": winner}
    teams_list = [{"id": "w" if winner == H else "h", "name": hn, "short": H}, {"id": "w" if winner == A else "a", "name": an, "short": A}]
    return out, {"leader": leader, "teams": teams_list}


@dataclass
class SectionJob:
    section: dict
    profile: str
    lang: str
    attempt: int = 1
    rejections: list = field(default_factory=list)
    released: float = field(default_factory=time.perf_counter)


@dataclass
class Draft:
    job: SectionJob
    text: str


@dataclass
class Done:
    job: SectionJob
    text: str
    source: str


class StoryPlanner(Executor):
    def __init__(self):
        super().__init__(id="story_planner")

    @handler
    async def plan(self, stats: dict, ctx: WorkflowContext[SectionJob]) -> None:
        sections, expected = storylines(stats)
        ctx.set_state("sections", sections)
        ctx.set_state("expected_claims", expected)
        ctx.set_state("roster", [{"id": p["player"], "name": p["player"]} for p in stats["players"]])
        jobs = [SectionJob(s, p, l) for s in sections for p in PROFILES for l in LANGS]
        ctx.set_state("count", len(jobs))
        ctx.set_state("done", [])
        for job in jobs:
            await ctx.send_message(job)


class SectionWriter(Executor):
    def __init__(self, client):
        super().__init__(id="section_writer")
        self.agent = Agent(client, STYLE_GUIDE, name="studio_pundit")

    @handler
    async def write(self, job: SectionJob, ctx: WorkflowContext[Draft]) -> None:
        en_len, zh_len = LENGTH[job.profile]
        audience = ("Audience: casual fans; plain words, at most two numbers."
                    if job.profile == "casual" else "Audience: analysts; lead with the decisive numbers.")
        prompt = (f"{SECTION_BRIEF[job.section['id']]} {audience} Length: {en_len if job.lang == 'en' else zh_len}.\n"
                  f"{LANGUAGE[job.lang]}\n\nFacts:\n" + "\n".join(f"- {f}" for f in job.section["facts"][job.lang]))
        if job.rejections:
            prompt += "\n\nYour previous answer was rejected for: " + "; ".join(job.rejections[-1]) + ". Rewrite it."
        try:
            res = await self.agent.run(prompt, options={"temperature": 0.4, "max_tokens": 220, "think": False,
                                                        "response_format": "json"})
            text = json.loads(res.text).get("text", "").strip()
        except Exception as exc:
            text = ""
            job.rejections.append([f"model error: {exc}"])
        await ctx.send_message(Draft(job, text))


class SectionChecker(Executor):
    def __init__(self, client, model_label):
        super().__init__(id="section_checker")
        self.auditor = Agent(client, AUDITOR_INSTRUCTIONS, name="claim_auditor")
        self.model_label = model_label

    @handler
    async def check(self, draft: Draft, ctx: WorkflowContext[SectionJob | Done]) -> None:
        job = draft.job
        facts = " ".join(job.section["facts"][job.lang] + [job.section["title"][job.lang]])
        limit = {"casual": 48, "analyst": 70}[job.profile] if job.lang == "en" else {"casual": 100, "analyst": 150}[job.profile]
        teams = [n for t in ctx.get_state("expected_claims")["teams"] for n in (t["name"], t["short"])]
        reasons = verify(draft.text, job.lang, facts, set(job.section["players"]), ctx.get_state("roster"), limit, teams)
        if not reasons and draft.text:
            try:
                reasons = await audit(self.auditor, draft.text, ctx.get_state("expected_claims"))
            except Exception:
                reasons = []
        if not reasons:
            await ctx.send_message(Done(job, draft.text, f"agent:{self.model_label}"))
            return
        if draft.text:
            job.rejections.append(reasons)
        if job.attempt < MAX_ATTEMPTS:
            job.attempt += 1
            await ctx.send_message(job)
        else:
            sep = " " if job.lang == "en" else ""
            await ctx.send_message(Done(job, sep.join(job.section["facts"][job.lang][:2]), "template_fallback"))


class StudioProducer(Executor):
    def __init__(self):
        super().__init__(id="studio_producer")

    @handler
    async def collect(self, item: Done, ctx: WorkflowContext[None, dict]) -> None:
        done = ctx.get_state("done") + [item]
        ctx.set_state("done", done)
        if len(done) < ctx.get_state("count"):
            return
        sections = ctx.get_state("sections")
        for s in sections:
            s["text"] = {p: {} for p in PROFILES}
        by_id = {s["id"]: s for s in sections}
        for d in done:
            by_id[d.job.section["id"]]["text"][d.job.profile][d.job.lang] = {
                "text": d.text, "source": d.source, "attempts": d.job.attempt, "rejections": d.job.rejections,
                "seconds": round(time.perf_counter() - d.job.released, 1)}
        await ctx.yield_output({"sections": sections})


def build_workflow(client, model_label):
    planner, writer = StoryPlanner(), SectionWriter(client)
    checker, producer = SectionChecker(client, model_label), StudioProducer()
    return (WorkflowBuilder(name="studio-analysis", start_executor=planner, max_iterations=100)
            .add_edge(planner, writer)
            .add_edge(writer, checker)
            .add_edge(checker, writer, condition=lambda m: isinstance(m, SectionJob))
            .add_edge(checker, producer, condition=lambda m: isinstance(m, Done))
            .build())


async def main_async(args):
    from agent_framework.ollama import OllamaChatClient

    stats = json.loads(Path(args.stats).read_text())
    started = time.perf_counter()
    result = await build_workflow(OllamaChatClient(model=args.model), args.model).run(stats)
    sections = result.get_outputs()[-1]["sections"]
    texts = [t for s in sections for p in s["text"].values() for t in p.values()]
    out = {"match": stats["match"], "source": stats["source"], "style_guide": STYLE_GUIDE, "sections": sections,
           "pipeline": {"framework": "Microsoft Agent Framework", "model": f"ollama:{args.model}",
                        "executors": ["story_planner", "section_writer", "section_checker", "studio_producer"],
                        "texts": len(texts), "agent_texts": sum(t["source"].startswith("agent") for t in texts),
                        "retried": sum(t["attempts"] > 1 for t in texts), "seconds": round(time.perf_counter() - started, 1)}}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out["pipeline"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", default=str(ROOT / "docs" / "real" / "dfl-full-stats.json"))
    parser.add_argument("--out", default=str(ROOT / "docs" / "real" / "dfl-studio.json"))
    parser.add_argument("--model", default="qwen3.5:9b")
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
