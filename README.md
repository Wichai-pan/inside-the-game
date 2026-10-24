# Inside the Game: one match, every fan

A football match feed goes through Microsoft Agent Framework workflows that interpret the events, pick the moments that matter, explain why, and talk about them the way a broadcast does. Each viewer gets their own version: casual fan or analyst, English or Simplified Chinese, following a team or a single player. The demo replays a match on an animated pitch with two-voice live captions (instant play-by-play plus agent-written analysis), live stats, a player focus mode and an automated recap. A post-match studio page turns full-match statistics into a pundit-style analysis.

## Data: synthetic matches built from real movement

The challenge asks for synthetic, football-realistic data. Our matches are generated, but the movement in them is real:

1. Real matches from the DFL open data (Bassek et al., *Scientific Data* 2025, [figshare](https://springernature.figshare.com/articles/dataset/An_integrated_dataset_of_spatiotemporal_and_event_data_in_elite_soccer/28196177), CC BY 4.0) are cut into live-ball spells at dead balls. Spells where a side has fewer than 11 players are skipped.
2. A seeded generator picks spells from several matches, assigns each side to one of two fictional clubs (Harbor City, Ridgeway United), and maps the real players onto fictional players by playing position (Hungarian matching on average positions).
3. Spells are joined with restarts during which players jog to the next spell's positions. The seed scripts the storyline: how many goals each club scores and roughly when; after a goal, the conceding club restarts from a real kick-off.
4. Events are converted on the new timeline, so passes, shots and tackles stay consistent with the movement. Every bundle records which source spell each part of the match came from (`provenance`).

The matches never happened and all names are fictional. See [DATA_PLAN.md](DATA_PLAN.md). Real matches are only used locally to develop and test the pipeline (`docs/real/`, not in this repository).

## Pipeline

| Stage | Module | What it does |
| --- | --- | --- |
| Generate | `insidegame/remix.py` | Stitches synthetic matches from real spells (above). `insidegame/dfl.py` loads a real DFL window and holds the shared event converter. |
| Interpret | `insidegame/analyst.py` | Causal per-event metrics: pass length, ball speed, progression, receiver space, a 0–100 difficulty rating; shot distance, speed (from tracking) and official xG; running possession, pass completion, shots, xG, pressures and regains. |
| Explain | `insidegame/analyst.py` | Ranks moments (goals, chances, pressure that forces a turnover, high regains, difficult passes, progressive passing spells, chaotic spells) and attaches "why it matters" reasons with the event IDs behind them. |
| Narrate and check | `insidegame/agents.py` | Agent Framework workflow `FeedIngestor → Narrator → FactChecker → ClaimAuditor → Producer → RecapWriter`; rejected lines loop back to the narrator. The fact checker (rules) rejects uninvolved players, numbers or measurements absent from the facts, unsupported football terms and translated names. The claim auditor has a model only *extract* who the line says shot, won the ball, leads, and in which half; code compares that with the feed. After three failed attempts a template line is used. |
| Play-by-play | `insidegame/playbyplay.py` | One speaker's caption track: templated play-by-play called as soon as each event is known, quiet-spell context lines, and the agents' analysis placed at the time it would really be ready. Urgent calls cut in; analysis that play has overtaken is dropped. |
| Live timing | `insidegame/live.py` | Builds a match the way a live system would run: each moment and viewer version is a separate workflow run, timed from the moment being known to the line passing both checks. |
| Statistics | `insidegame/stats.py` | Full-match statistics with [kloppy](https://github.com/PySport/kloppy) (events, oriented per team) and [databallpy](https://github.com/Alek050/databallpy) (25 Hz tracking): distance, high-speed running, sprints, top speed, possession, field tilt, PPDA, pressure on the passer, xT from passes (Karun Singh's open 12×8 grid), 15-minute splits and player tables. |
| Studio | `insidegame/studio.py` | Agent Framework workflow `StoryPlanner → SectionWriter ⇄ SectionChecker → StudioProducer`: six storylines from the statistics (verdict, turning point, momentum, tactics, fairness of the score, standout players), written in a house pundit style and checked like commentary. Comparisons are stated in the facts ("X had more open-play xG than Y") so the writer does not have to infer them. Works on real matches (`stats.build`) and on synthetic bundles (`stats.from_match`, which computes the same table from 5 Hz positions and converted events, excluding restart blends from physical numbers). |
| Render | `docs/index.html`, `docs/studio.html` | Static pages that replay the bundles: pitch, captions, transcript, stats, player focus, recap, timed overlay cues, and the studio analysis with its graphics. No model is called while viewing. |

## Measured results (bundled synthetic matches 42, 21, 7)

- 108 commentary lines: 102 written by the narrator agent and passed both checks, 33 of them after a rewrite; 6 template fallbacks.
- 72 studio texts across the three matches: 64 written by the studio agent, 8 template fallbacks.
- Live delay of an analysis line on an Apple laptop with `qwen3.5:9b`: median 8.6–9.7 s per match (6.6–8.6 s when it passes first time). Play-by-play has no model delay. Serving all four viewer versions from one laptop queues them (37–54 s), so live use needs a hosted model.

## Run

Requires Python 3.10+ and a local [Ollama](https://ollama.com) chat model (developed with `qwen3.5:9b`). Generating synthetic matches needs the DFL files under `data/dfl/<match id>/` (`meta.xml`, `events.xml`, `tracking.xml`) loaded once with databallpy.

```sh
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python -r requirements.txt
.venv/bin/python -m insidegame.live --remix 42 --duration 600
.venv/bin/python -m insidegame.stats --match docs/synthetic/remix-42.json
.venv/bin/python -m insidegame.studio --stats docs/synthetic/remix-42-stats.json --out docs/synthetic/remix-42-studio.json
python3 -m http.server 8000 --directory docs
```

Then open http://localhost:8000.

## Microsoft technology

- **Microsoft Agent Framework** (`agent-framework-core`): two `WorkflowBuilder` graphs with typed executors, type-checked edges, conditional edges for the check-and-retry loops, shared workflow state, and `Agent` instances for the narrator, claim auditor, recap writer and studio pundit.
- **Model provider**: Ollama through `agent-framework-ollama` for now. The chat client is passed into each workflow, so a Microsoft Foundry client can replace it without changing the agents.

## Limitations

- Restarts between spells are blended, not real; team shapes can change across a seam. Statistics describe the generated match, not any real one.
- The checks cover names, numbers, measurements, unsupported terms, the shooter, the ball winner, the leading team and the half where the ball was won. Other wrong claims, such as who passed to whom or which team scored, can still get through, and the 9B model sometimes adds interpretation the data does not support.
- Generation is offline; the page replays results with their measured live timing.
