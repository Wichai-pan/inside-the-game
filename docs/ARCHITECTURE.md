# Inside the Game: architecture

Inside the Game turns a football match feed into commentary that is personalised (casual fan or analyst, English or Chinese) and fact-checked. Every number and name a viewer sees can be traced back to an event in the feed. The submitted matches are synthetic: fictional clubs and players, with movement stitched from real open tracking data.

The system has four layers:

1. **Data**: DFL open data is remixed into synthetic matches.
2. **Deterministic analysis**: the analyst, the stats layer and play-by-play. No model is involved.
3. **Two Microsoft Agent Framework workflows**: live commentary and post-match studio analysis. LLM agents write, a deterministic checker and an LLM claim auditor verify, and rejected text is retried or replaced by a template.
4. **Delivery**: JSON bundles feed static pages and an MCP server, and any MCP client or Agent Framework agent can query the server.

## Data flow

```mermaid
flowchart LR
    DFL[("DFL open data<br/>7 matches, events + 25 Hz tracking<br/>CC BY 4.0, local only")]
    REMIX["remix.py<br/>cut at dead balls, fictional clubs,<br/>seeded storyline, blended seams"]
    MATCH[("synthetic match<br/>events + 5 Hz frames")]
    ANALYST["analyst.py<br/>metrics, running state,<br/>key moments + why it matters"]
    STATS["stats.py from_match<br/>xT, PPDA, field tilt,<br/>segments, physical"]
    PBP["playbyplay.py<br/>instant calls + caption scheduling"]
    LIVEWF["Live commentary workflow<br/>(Agent Framework)"]
    STUDIOWF["Studio analysis workflow<br/>(Agent Framework)"]
    B1[("remix-N.json<br/>moments, commentary, recap,<br/>captions, overlays, latency")]
    B2[("remix-N-stats.json")]
    B3[("remix-N-studio.json")]
    PAGES["docs/index.html (live replay)<br/>docs/studio.html (post-match)"]
    MCP["mcp_server.py<br/>MCP tools over the bundles"]
    CLIENTS["MCP clients:<br/>ask.py agent, Claude, VS Code / Copilot"]

    DFL --> REMIX --> MATCH
    MATCH --> ANALYST --> LIVEWF
    MATCH --> STATS --> STUDIOWF
    LIVEWF --> PBP
    ANALYST --> PBP
    LIVEWF --> B1
    PBP --> B1
    STATS --> B2
    STUDIOWF --> B3
    B1 & B2 & B3 --> PAGES
    B1 & B2 & B3 --> MCP --> CLIENTS
```

| Stage | Module | Notes |
|---|---|---|
| Source adapter | `insidegame/dfl.py` | Reads one DFL match into the pipeline format. Event locations come from the tracked ball, so events and positions share one frame. |
| Synthetic match | `insidegame/remix.py` | A seeded stitcher (see `DATA_PLAN.md`). Real names never reach the output. |
| Analyst | `insidegame/analyst.py` | Causal: a value for an event uses only that event and earlier ones. Each moment keeps the IDs of the events behind every fact. |
| Stats layer | `insidegame/stats.py` | Full-match and per-segment team and player statistics, with definitions. |
| Play-by-play | `insidegame/playbyplay.py` | One speaker. Urgent calls cut in, and analysis lines enter only once they would really be ready. Analysis that play has overtaken is dropped. |
| Bundles | `insidegame/build.py`, `live.py`, `studio.py` | Write `docs/synthetic/*.json`. |
| Model provider | `insidegame/providers.py` | `get_client()` returns an Agent Framework chat client: Ollama, Microsoft Foundry or Azure OpenAI. |
| MCP server | `insidegame/mcp_server.py` | Seven read-only tools over the bundles. |
| Q&A agent | `insidegame/ask.py` | An Agent Framework agent that answers questions by calling the MCP tools. |

## Workflow 1: live commentary (`insidegame/agents.py`)

Each key moment is narrated for every viewer variant (2 profiles × 2 languages). The live runner (`live.py`) starts one workflow run per moment and variant as soon as the moment is known, and times it until the line has passed both checks. The measured time places the line on the caption track.

```mermaid
flowchart LR
    IN([match feed]) --> FI["FeedIngestor<br/><i>deterministic</i><br/>runs analyst, fans out jobs"]
    FI -- NarrationJob --> NA["Narrator<br/><b>LLM agent</b><br/>one grounded line"]
    NA -- Candidate --> FC["FactChecker<br/><i>deterministic</i><br/>names, numbers, units,<br/>unsupported terms, Latin names"]
    FC -- "rejected: reasons" --> NA
    FC -- Audit --> CA["ClaimAuditor<br/><b>LLM agent extracts claims</b>,<br/>code compares with feed"]
    CA -- "wrong shooter / ball winner /<br/>leader / half" --> NA
    FC -- "3rd failure: template" --> PR
    CA -- Approved --> PR["Producer<br/>collects lines, requests recaps"]
    PR -- RecapJob --> RW["RecapWriter<br/><b>LLM agent</b> + same checks"]
    RW --> OUT([analysis + recaps + final stats])
```

- **Two kinds of check.** The fact checker (`verify`) is pure code: it rejects a line that names an uninvolved player, uses a number or measurement that is not in the facts, uses a term the facts do not support (for example "header" or "goalkeeper"), or translates names into Chinese characters. The claim auditor only asks a model to *extract* four claims (shooter, ball winner, leading team, half). Code then compares them with the feed, so the model never judges itself.
- **Every moment always gets commentary.** After three failed attempts a template line is used. The bundle records which path produced each line (`agent:<model>` or `template_fallback`), how many attempts it took and the rejection reasons.

## Workflow 2: studio analysis (`insidegame/studio.py`)

```mermaid
flowchart LR
    S([stats layer]) --> SP["StoryPlanner<br/><i>deterministic</i><br/>6 storylines with facts,<br/>allowed players, graphic spec"]
    SP -- SectionJob --> SW["SectionWriter<br/><b>LLM agent</b><br/>pundit style guide"]
    SW -- Draft --> SC["SectionChecker<br/>verify + claim auditor"]
    SC -- "rejected: reasons" --> SW
    SC -- "Done (agent or template)" --> PRD["StudioProducer"]
    PRD --> O([sections × profiles × languages])
```

The six storylines are verdict, turning point, momentum, tactical battle, "did they deserve it?" and standout performers. The pundit style guide was written by the team, and no broadcast transcripts are copied.

## MCP server and agent Q&A

`insidegame/mcp_server.py` uses the official MCP Python SDK (`MCPServer`, stdio transport). It is read-only and never calls a model. Every answer is either a fact from the feed or a text that has already passed the checks.

| Tool | Returns |
|---|---|
| `list_matches()` | ids, teams, final score, length |
| `get_match_state(match_id, minute)` | score and running team stats at that minute, last event |
| `get_key_moments(match_id, kind?, lang?)` | moments with key fact, why it matters, score after |
| `get_player_stats(match_id, player?, team?, sort_by?, limit?)` | player rows, filtered or top-N by any numeric field |
| `get_team_stats(match_id)` | team table, 6 segments, goals, shots, metric definitions |
| `get_commentary(match_id, profile, lang)` | checked lines per moment + recap, with their source |
| `get_studio_analysis(match_id, profile, lang)` | the six studio sections with the facts they were checked against |

```mermaid
sequenceDiagram
    participant U as User
    participant A as Agent Framework Agent<br/>(ask.py, match_analyst)
    participant M as Model<br/>(Ollama or Foundry)
    participant S as MCP server<br/>(stdio subprocess)
    U->>A: "Why did Ridgeway win?"
    A->>S: list tools (MCPStdioTool)
    A->>M: question + tool schemas
    M-->>A: call get_studio_analysis(remix-42)
    A->>S: tools/call
    S-->>A: verdict, turning point, facts
    M-->>A: call get_team_stats / get_key_moments
    A->>S: tools/call
    S-->>A: numbers
    M-->>A: answer grounded in tool results
    A-->>U: answer + list of tools called
```

```bash
.venv/bin/python -m insidegame.mcp_server                      # stdio server for any MCP client
.venv/bin/python tests/mcp_client.py                           # scripted MCP client: list + call tools
.venv/bin/python -m insidegame.ask "Why did Ridgeway win?"     # agent answers with the MCP tools
```

The same server can be registered in any MCP host, for example a VS Code `mcp.json` entry with `"command": ".venv/bin/python", "args": ["-m", "insidegame.mcp_server"]`. GitHub Copilot agent mode can then query the matches.

## Where Microsoft Foundry plugs in

All agents take an Agent Framework chat client. `insidegame/providers.py` chooses one from environment variables, so prompts, executors and checkers are identical across providers:

```mermaid
flowchart LR
    ENV{{INSIDEGAME_PROVIDER}} -->|ollama, default| OL["OllamaChatClient<br/>local qwen3.5:9b"]
    ENV -->|foundry| FD["FoundryChatClient<br/>FOUNDRY_PROJECT_ENDPOINT, FOUNDRY_MODEL<br/>Entra ID (DefaultAzureCredential)"]
    ENV -->|azure_openai| AO["OpenAIChatCompletionClient (Azure)<br/>AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_MODEL,<br/>API key or Entra ID"]
    OL & FD & AO --> W1[live commentary workflow]
    OL & FD & AO --> W2[studio workflow]
    OL & FD & AO --> QA[ask.py MCP agent]
```

- The agents pass Ollama-style options (`think`, `response_format="json"`). For the cloud clients, a chat middleware (`portable_options`) drops `think` and maps `"json"` to a JSON-object response format.
- **Status:** the Foundry and Azure OpenAI paths are implemented with the official `agent-framework-foundry` and `agent-framework-openai` packages. They are unit-tested for configuration and construction, and they fail with a clear message when variables or credentials are missing. They have **not** been run against a live Foundry project yet, because the team has no Azure subscription during development. Everything shown in the demo ran on local Ollama.
- **Next steps once a subscription exists:** run the live workflow on a Foundry deployment to compare latency and first-try pass rate with local Ollama, using the stored `latency` and `pipeline` blocks. Use Foundry evaluations and tracing (Agent Framework emits OpenTelemetry spans) on the checker rejection reasons.

## Engineering

- `tests/`: pytest suite that needs no model. It covers the fact checker rules, analyst metrics and causality on a seeded synthetic match, caption scheduling invariants, stats-layer consistency with the bundles, the MCP tools in process and over stdio, and provider selection. Known defects are recorded as `xfail` tests, so a fix flips them to passing.
- `.github/workflows/ci.yml`: installs `requirements.txt` and runs the suite on every push and pull request.
