# Inside the Game: one match, every fan

A synthetic football feed goes through a Microsoft Agent Framework workflow. The workflow interprets the events, picks the moments that matter, explains why, and writes fact-checked commentary and recaps. Every output exists for two viewer types (casual fan, analyst) and two languages (English, Simplified Chinese). The demo site replays the result on an animated pitch, with a broadcast-style lower third, live stats and a timed overlay feed.

All teams, players, events and positions are fictional and come from a seeded simulator. No real match data or footage is used.

## Pipeline

| Stage | Where | What it does |
| --- | --- | --- |
| Ingest | `insidegame/simulate.py` | Seeded 3-minute clip: kick-offs, passes, pressure, tackles, interceptions, shots and restarts, with 22 players and the ball at 5 Hz. Each seed changes the names, the sequence of play and the outcomes. |
| Interpret | `insidegame/analyst.py` | Causal per-event metrics: pass length, ball speed, progression, receiver space and a 0–100 difficulty rating; shot distance, speed and xG; running possession share, pass completion, shots, xG, pressures and regains. |
| Explain | `insidegame/analyst.py` | Ranks candidate moments (goals, chances, pressure that forces a turnover, high regains, difficult passes, long passing spells, chaotic spells) and attaches deterministic "why it matters" reasons with the event IDs behind them. |
| Narrate and check | `insidegame/agents.py` | Agent Framework workflow: `FeedIngestor → Narrator → FactChecker → ClaimAuditor → Producer → RecapWriter`, with rejections looping back to the narrator. The narrator, claim auditor and recap writer are LLM agents. The fact checker (rules) rejects lines that name an uninvolved player, use a number or measurement absent from the facts, or claim an action the feed does not contain. The claim auditor has the model only *extract* who the line says shot, who won the ball, which team leads and in which half; code then compares those claims with the feed. Rejected lines go back with the reasons; after three attempts a template line is used. |
| Render | `insidegame/build.py`, `docs/` | Exports each match as JSON, with timed `overlays` cues a broadcast renderer could consume, and a static page that replays them. |
| Personalize | page + agents | Casual and analyst views differ in wording, numbers and layout. The language switch changes the generated commentary as well as the interface. You can follow one team, or pick a player: the player is tagged on the pitch with a 5-second trail, a live card shows touches, passes, average pass difficulty, shots, xG, ball wins, distance and top speed, threshold badges appear for sprints (above 25 km/h for 1.5 s) and every 100 m covered, the moments they took part in are highlighted with their role, and a button jumps to their next involvement. |

The export records how each line was produced (agent first try, agent after a rewrite, or template), so the fact-check rate can be measured.

In the three bundled matches (seeds 5, 78, 87; 108 commentary lines and 12 recaps), 101 lines and 10 recaps were written by the agents and passed both checks; 35 lines needed at least one rewrite. The other 7 lines and 2 recaps fell back to templates. Building took 12–13 minutes per match on an Apple laptop with `qwen3.5:9b`.

## Run

Requires Python 3.10+ and a local [Ollama](https://ollama.com) with a chat model (developed with `qwen3.5:9b`).

```sh
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python -r requirements.txt
.venv/bin/python -m insidegame.build --seeds 5 78 87 --model qwen3.5:9b
python3 -m http.server 8000 --directory docs
```

Then open http://localhost:8000. The page plays the bundled results and does not call a model while you watch.

## Microsoft technology

- **Microsoft Agent Framework** (`agent-framework-core`): `WorkflowBuilder` graph with typed executors, conditional edges for the check-and-retry loop, shared workflow state, and `Agent` instances for the narrator, claim auditor and recap writer.
- **Model provider**: currently Ollama through `agent-framework-ollama`. The chat client is passed into `build_workflow`, so an Azure AI Foundry client can replace it without touching the agents.

## Limitations

- The simulator produces plausible, not physically exact, movement. Player speed is capped at 8.5 m/s; distances covered run about 1.5× real match rates because the clip is unusually dense with turnovers. Events are authoritative for actions and outcomes; positions are never used to label a pass, tackle or shot.
- The checks cover names, numbers, measurements, a list of unsupported football terms, the shooter, the ball winner, the leading team and the half where the ball was won. Other wrong claims (for example who passed to whom) can still get through.
- Generation is offline. The page simulates live delivery by releasing moments in time order, using only events up to that moment.
