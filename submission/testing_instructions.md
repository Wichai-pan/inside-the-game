# Testing instructions

No login, install or API key is needed. The public demo is a static site; it replays matches that the agent workflows generated beforehand, with the timing measured when they were generated.

## Test it in 2 minutes

Open **https://wichai-pan.github.io/inside-the-game/** in a desktop browser (Chrome, Edge, Safari or Firefox).

1. **Watch a match (40 s).** Leave *Match 42* selected. Click **4×** under the pitch, then **▶**. The left panel shows the pitch and live stats; the right panel shows the live commentary. Notice the two voices: play-by-play appears immediately, and an agent-written analysis line appears a few seconds later, labelled with how many attempts it needed to pass the checks. Below it, *Why it matters* lists the reasons and *Data basis* lists the events behind the line.
2. **Casual vs. analyst (15 s).** Switch **Viewer** between *Casual fan* and *Analyst*. The analyst version uses numbers (xG, pass difficulty, ball speed); the casual version tells the story in plain language.
3. **Another language (10 s).** Click **中文**. Commentary, reasons, stats and recap switch to Simplified Chinese, generated (not machine-translated) by the same workflow. Player names stay in their original form, which the fact checker enforces.
4. **Follow a player (15 s).** In **Player focus**, choose e.g. *#10 Callum Ashby · Striker*. The player is highlighted on the pitch and a player card shows distance, top speed, sprints and ball wins. **Follow** picks a club and dims the other club's players and moments.
5. **Recap (10 s).** Drag the timeline to the end. The *Automated recap* is written after the final event.
6. **Post-match studio (30 s).** Click **Post-match studio →** (or open https://wichai-pan.github.io/inside-the-game/studio.html). Read the six storylines (verdict, turning point, momentum, tactics, fairness of the score, standout players) with their graphics. Each section says whether the studio agent's text passed first time, after a rewrite, or was replaced by a template. Try *Analyst* and *中文* here too.

Matches 21 and 7 in the **Match** menu are other seeds with different storylines.

## What to look for

- Every agent line is checked against the data. A rule-based fact checker rejects names, numbers, measurements and terms not in the facts; an auditor agent extracts who the line says shot, won the ball and leads, and code compares that with the feed.
- The delay before analysis appears is the delay measured on our laptop (median about 9 s with a local 9B model), not an animation.
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

Regenerating one 10-minute match with all four viewer versions took about 7 minutes of model time on an Apple laptop (match 42: 36 lines, 394 s).

## Known issues

- The demo is a replay: no model runs in the browser.
- The site is designed for desktop width; it works on a phone but the pitch is small.
