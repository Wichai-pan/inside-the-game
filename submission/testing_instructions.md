# Testing instructions

No login, install or API key is needed. The public demo is a static site; it replays matches that the agent workflows generated beforehand, with the timing measured when they were generated.

## Test it in 2 minutes

Open **https://wichai-pan.github.io/inside-the-game/** in a desktop browser (Chrome, Edge, Safari or Firefox).

1. **Watch a match (40 s).** Leave *Match 42* selected and press the big green play button on the pitch (or Space), then pick **4×** under the pitch. The scoreboard, pitch and possession bar update live; the *Live commentary* panel shows the two voices: play-by-play appears on the pitch immediately, and the analysis agent's line follows with the delay we measured ("The analyst agent is writing this line… ready in 8.2 s"). Each line says whether an agent wrote it and how many attempts it needed to pass the checks.
2. **Casual vs. analyst (20 s).** Switch **Viewer** to *Analyst*. The same moment now comes with metric chips (shot distance, shot speed, xG), *Why it matters*, *Evidence from the feed* with event IDs, live team stats, an event feed with derived metrics, and the *Agent workflow* panel with the measured delay per line. The *Commentary transcript* shows lines that were dropped because play had moved on.
3. **Another language (10 s).** Click **中文**. Commentary, reasons, stats and recap switch to Simplified Chinese, written natively by the same workflow (not machine-translated). Player names stay in Latin letters, which the fact checker enforces.
4. **Follow a player (15 s).** Click any numbered dot on the pitch (or use **Player focus**). A player card shows touches, passes, distance, top speed, ball wins and every moment the player is involved in; *Next involvement ›* jumps to it. **Follow** picks a club and dims the other club's players and moments.
5. **Key moments and recap (10 s).** Click a moment in *Key moments* to jump to it, or drag the timeline to the end to read the *Automated recap*, written after the final event.
6. **Post-match studio (30 s).** Click **Studio** in the top bar. Read the six numbered storylines (verdict, turning point, momentum, tactics, fairness of the score, standout players) with their graphics. Each section says whether the studio agent's text passed first time, after a rewrite, or was replaced by a template. Try *Analyst* and *中文* here too.
7. **How it works (optional).** The **How it works** page explains the workflow, the synthetic data and its attribution, and what the checks do and do not cover.

Matches 21 (2–2) and 7 (3–2) in the **Match** menu are other seeds with different storylines; match 42 ends 1–2.

## What to look for

- Every agent line is checked against the data. A rule-based fact checker rejects names, numbers, measurements and terms not in the facts; an auditor agent extracts who the line says shot, won the ball and leads, and code compares that with the feed.
- The delay before analysis appears is the delay measured on our laptop (median 11–12 s with a local 9B model), not an animation.
- All clubs and players are fictional. See the project description for how the synthetic matches are built and attributed.

## Run it locally

### Just the site (no model needed)

```sh
git clone https://github.com/Wichai-pan/inside-the-game.git
cd inside-the-game
python3 -m http.server 8000 --directory docs
```

Open http://localhost:8000.

### Regenerate commentary and studio analysis

Requirements: Python 3.10+, [uv](https://docs.astral.sh/uv/) (or pip), and [Ollama](https://ollama.com) with a chat model (developed with `qwen3.5:9b`: `ollama pull qwen3.5:9b`).

```sh
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python -r requirements.txt
.venv/bin/python -m insidegame.live --remix 42 --duration 600
.venv/bin/python -m insidegame.stats --match docs/synthetic/remix-42.json
.venv/bin/python -m insidegame.studio --stats docs/synthetic/remix-42-stats.json --out docs/synthetic/remix-42-studio.json
python3 -m http.server 8000 --directory docs
```

Generating a new synthetic match from scratch also needs the DFL open data files under `data/dfl/<match id>/` (`meta.xml`, `events.xml`, `tracking.xml`), downloaded from [figshare](https://springernature.figshare.com/articles/dataset/An_integrated_dataset_of_spatiotemporal_and_event_data_in_elite_soccer/28196177) (CC BY 4.0). The three bundled matches are already in the repository, so this step is optional.

Regenerating one 10-minute match with all four viewer versions took about 10 minutes of model time on an Apple laptop (match 42: 36 lines, 580 s).

### Tests, MCP server and question answering

```sh
.venv/bin/python -m pytest                                   # 72 tests, no model needed
.venv/bin/python -m insidegame.mcp_server                    # MCP server over stdio (7 tools)
.venv/bin/python -m insidegame.ask "Who scored the goals, and how good were the chances?"
```

`ask.py` starts the MCP server itself, lets an Agent Framework agent choose tools, and prints which tools it called. To use Microsoft Foundry instead of Ollama, set `INSIDEGAME_PROVIDER=foundry`, `FOUNDRY_PROJECT_ENDPOINT` and `FOUNDRY_MODEL` and sign in with `az login` (see `insidegame/providers.py`; not run live by us).

## Known issues

- The demo is a replay: no model runs in the browser.
- The site is designed for desktop width; it works on a phone but the pitch is small.
- `ask.py` answers are not fact-checked; open questions can get wrong numbers from the 9B model.
