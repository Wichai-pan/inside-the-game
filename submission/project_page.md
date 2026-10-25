# Inside the Game

**One match. Every fan.**

Fact-checked live commentary and post-match analysis, written by Microsoft Agent Framework workflows from a synthetic match feed, and personalised for each viewer.

- Live demo: https://wichai-pan.github.io/inside-the-game/
- Post-match studio: https://wichai-pan.github.io/inside-the-game/studio.html
- Code: https://github.com/Wichai-pan/inside-the-game
- Demo video: _(YouTube link to be added at upload)_

Suggested category: **Best Multi-Agent System** (also entered for the Grand Prize).

---

## Elevator pitch

**What we built.** Inside the Game turns a stream of synthetic football events into live, explained, personalised commentary and a post-match studio analysis. A match plays on an animated pitch with two caption voices: instant play-by-play, and agent-written analysis that explains why a moment matters. After the final whistle, a pundit-style studio page tells the story of the match from full-match statistics.

**Microsoft technology.** Two **Microsoft Agent Framework** workflows (`agent-framework-core`): a live-commentary graph (`FeedIngestor → Narrator → FactChecker → ClaimAuditor → Producer → RecapWriter`) and a studio graph (`StoryPlanner → SectionWriter ⇄ SectionChecker → StudioProducer`). Both use typed executors, conditional edges for check-and-retry loops, shared workflow state and Agent Framework `Agent` instances. The model runs locally through `agent-framework-ollama` (qwen3.5:9b). The chat client is injected into each workflow, and one setting (`INSIDEGAME_PROVIDER=foundry` or `azure_openai`) switches it to a **Microsoft Foundry** project or **Azure OpenAI** deployment with Entra ID sign-in, without changing the agents. We did not have an Azure subscription, so Foundry is ready to switch to but not used in the published results. An **MCP server** exposes the match intelligence (key moments, player and team stats, commentary, studio analysis) as tools for any MCP client or agent. The site is hosted on **GitHub Pages**, and the code is on **GitHub**.

**What it does.** The same match reaches each fan differently: casual fan or analyst, English or Simplified Chinese, following one club (the other is dimmed) or both, or following a single player throughout the match. Every agent-written line is checked against the data before it is shown: a rule-based fact checker rejects names, numbers, measurements and football terms that are not in the facts, and an auditor agent extracts who the line says shot, won the ball, leads, and in which half, which code then compares with the feed. Failed lines go back to the narrator; after three failures a template line is used.

**What problem it solves.** Broadcasters can afford one commentary track per match, in one language, aimed at the average viewer. Analysts want numbers, casual fans want the story, and fans abroad want their own language. Language models can write all of these versions, but they invent facts. Inside the Game writes many versions and checks each one against the match data, and it measures how long a checked line takes, so you can see whether it is fast enough for live play.

---

## Features

**Live replay (`index.html`)**
- Animated pitch with all 22 fictional players and the ball, at 1×, 2× or 4× speed.
- Two-voice live captions: play-by-play called the instant each event is known, plus agent analysis placed at the time it would really be ready (measured, around 9 s later). Urgent calls cut in; analysis that play has overtaken is dropped.
- "Why it matters" reasons and the event IDs behind each line ("data basis"), so every claim can be traced.
- Key-moment timeline (goals, chances, pressure that forces a turnover, high regains, difficult passes, spells of control or chaos).
- Live statistics: possession, shots, xG, pass completion.
- Personalisation: casual fan or analyst, English or 中文, follow a club, player focus mode with per-player stats (distance, top speed, sprints, ball wins) and a highlight on the pitch.
- Automated recap written after the final event.

**Post-match studio (`studio.html`)**
- Six storylines written by an agent in a house pundit style: verdict, turning point, momentum, tactics, fairness of the score, standout players.
- Graphics for each storyline (goals/xG/shots/possession bars, goal timeline and more).
- Same checks as live commentary; each section shows whether it was rewritten or replaced by a template.
- Casual/analyst and English/中文 versions.

**Engineering**
- Seeded synthetic match generator (`insidegame/remix.py`).
- Pass difficulty (0–100), pass length, ball speed, shot distance and speed, xG, pressures and regains computed causally, event by event (`insidegame/analyst.py`).
- Full-match statistics with kloppy and databallpy: distance, high-speed running, sprints, top speed, field tilt, PPDA, pressure on the passer, expected threat (xT) from passes, 15-minute splits (`insidegame/stats.py`).
- Static pages replay pre-generated bundles, so the public demo needs no model or server.
- Model provider chosen by configuration: local Ollama, Microsoft Foundry or Azure OpenAI (`insidegame/providers.py`).
- MCP server with tools `list_matches`, `get_match_state`, `get_key_moments`, `get_player_stats`, `get_team_stats`, `get_commentary`, `get_studio_analysis` (`insidegame/mcp_server.py`).
- Unit tests for the analyst, play-by-play, statistics, checkers, providers and MCP server, run by GitHub Actions CI.

<!-- TODO before submitting: the provider switch, MCP server, tests and CI were uncommitted work by other sessions on 25 Oct. Remove these three bullets and the Foundry/MCP sentences in the pitch if they are not merged and pushed. -->


---

## How it maps to the five-stage pipeline

| Stage | What we do | Where |
| --- | --- | --- |
| **Ingest** | Synthetic events (passes, shots, tackles, possession changes, pressure, match metadata) and 5 Hz positions arrive in time order; the `FeedIngestor` executor reads them one at a time. | `insidegame/remix.py`, `insidegame/agents.py` |
| **Interpret** | Causal per-event metrics (pass difficulty, ball and shot speed, xG, running possession, pressures, regains) and full-match statistics (distance, sprints, PPDA, field tilt, xT). | `insidegame/analyst.py`, `insidegame/stats.py` |
| **Explain** | Moments are ranked and given "why it matters" reasons with their evidence (event IDs); control vs. chaos, pressure that forces turnovers, progressive spells. The narrator agent writes the explanation; the fact checker and claim auditor verify it. | `insidegame/analyst.py`, `insidegame/agents.py` |
| **Render** | Pitch, timed two-voice captions, live stats, key-moment timeline, recap, and the studio page with graphics. Each match bundle also carries machine-readable overlay cues (`overlays`: type such as lower third, start and end time, headline in both languages, player tags, metrics) and timed caption tracks, ready to drive graphics on a live feed. | `docs/index.html`, `docs/studio.html`, `insidegame/playbyplay.py` |
| **Personalize** | Casual fan vs. analyst, English vs. Simplified Chinese, favourite club, single-player focus. Each version is a separate workflow run, not a translation of one text. | `insidegame/live.py` |

## How it maps to the judging criteria

- **Technological implementation.** A seeded generator for synthetic, football-realistic matches; Microsoft Agent Framework workflows with typed executors and conditional edges; open football analytics libraries (kloppy, databallpy); a documented, modular Python package.
- **Agentic design and innovation.** Specialised agents with distinct roles (narrator, auditor, recap writer, story planner, pundit) and code-based checkers, coordinated through shared workflow state, with retry loops and template fallback when an agent fails. The auditor agent only *extracts* claims; code makes the judgement, which keeps a small local model honest.
- **Real-world impact.** One feed, many audiences: languages and audience levels a single broadcast cannot serve. Measured live latency shows what it would take to run live.
- **User experience and presentation.** A single page with the match, captions and stats side by side; a studio page for after the match; every line traceable to its data.
- **Category adherence (Best Multi-Agent System).** Distinct roles, shared state, handoffs, failure recovery (rewrite loops, fallback), and an end-to-end result (checked, personalised commentary plus a studio analysis) that a single agent prompt did not deliver reliably.

## Measured results (bundled matches 42, 21, 7)

- 108 commentary lines: 102 written by the narrator agent and passed both checks (33 after a rewrite); 6 template fallbacks.
- 72 studio texts: 64 written by the studio agent; 8 template fallbacks.
- Analysis line delay on an Apple laptop with qwen3.5:9b: median 8.6–9.7 s per match (6.6–8.6 s when it passes first time). Play-by-play has no model delay.

---

## Synthetic data and attribution

The challenge asks for synthetic, football-realistic data. Our matches are generated, and the movement in them is real:

1. Real matches from the DFL open data are cut into live-ball spells at dead balls.
2. A seeded generator picks spells from several matches, assigns each side to one of two fictional clubs (Harbor City, Ridgeway United) and maps the real players onto fictional players by playing position.
3. Spells are joined by restarts during which players jog to the next spell's positions. The seed scripts the storyline (who scores, and roughly when).
4. Events are converted onto the new timeline, so passes, shots and tackles stay consistent with the movement. Each bundle records which source spell every part of the match came from.

The matches never happened, and all clubs and players are fictional. No video footage, broadcaster material or real names are used.

**Attribution.** Movement and events are derived from: Bassek, M., et al. *An integrated dataset of spatiotemporal and event data in elite soccer.* Scientific Data (2025). Deutsche Fußball Liga (DFL) open data, [figshare](https://springernature.figshare.com/articles/dataset/An_integrated_dataset_of_spatiotemporal_and_event_data_in_elite_soccer/28196177), licensed under CC BY 4.0. Expected threat uses Karun Singh's open 12×8 xT grid. Statistics use [kloppy](https://github.com/PySport/kloppy) and [databallpy](https://github.com/Alek050/databallpy).

---

## Honest limitations

- **No live Azure.** We had no Azure subscription, so Microsoft Foundry is not used in the published results. The chat client is injected, so switching is a client change, not a rewrite.
- **Replay, not live.** The public demo replays pre-generated bundles with their measured timing; no model runs while you watch. Generation runs offline on a laptop.
- **Latency.** About 9 s per analysis line on a laptop; serving all four viewer versions from one laptop queues them (37–54 s). Live use needs a hosted model.
- **Checks are not complete.** They cover names, numbers, measurements, unsupported terms, the shooter, the ball winner, the leading team and the half. Other wrong claims (for example who passed to whom) can still get through, and the 9B model sometimes adds interpretation the data does not support.
- **Stitching seams.** Restarts between spells are blended, not real; team shapes can change across a seam. Statistics describe the generated match only.
- **Two languages.** English and Simplified Chinese only so far.
- **No auto-eventing.** Events come from the data; we do not detect them from video.
