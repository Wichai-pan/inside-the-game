# Submission and judging checklist

Status as of 25 Oct 2026, after commit d4a0446 (redesign, accuracy pass, Microsoft integration) was pushed and published. Deadline: **27 Oct 2026, 11:59 pm PT** (28 Oct, 08:59 Helsinki).

Legend: ✅ done · 🟡 partly / in progress · ⬜ to do · ❌ not met (explained)

## Entry requirements (Official Rules §3–4)

| Requirement | Rule | Status | Evidence / action |
| --- | --- | --- | --- |
| Registered on the hackathon website | §4 | ⬜ check | Registration closed 20 Oct, 12:00 pm PT. User to confirm the account on https://aka.ms/insidethegame is registered. |
| Eligible entrant (age of majority, not in an excluded country, no conflict of interest) | §3 | ⬜ user | User to confirm. For a team: up to 4 members and one Representative (§4.10). |
| Submission entered on the "Projects" page during the submission period | §4 | ⬜ user | Fill the fields from `project_page.md`. |
| Responds to the challenge and fits at least one category | §4, §4.1, §4.2 | ✅ | Five-stage pipeline (below). Suggested category: Best Multi-Agent System. |
| New project, created after 29 Sep 2026 | §4.3 | ✅ | First commit `3711f71` in the public repo; user to confirm no earlier work was reused. |
| Third-party data/SDKs used under their licences | §4.3, §4.11 | ✅ | DFL open data CC BY 4.0 (attributed in README and `project_page.md`); kloppy, databallpy, Agent Framework, Ollama are open source. |
| Project pitch: what we built, Microsoft/Azure technologies, what it does, problem it solves | §4.4 | ✅ | `project_page.md` → "Elevator pitch". |
| Demo video under 2 minutes | §4.4 | ✅ | `video/final_candidate.mp4`, 1:50, recorded from the redesigned public site; script in `video_script.md`. Synthetic voice; optionally re-voice with a human on the same timings. |
| Video shows the project running on its platform | §4.4 | ✅ | Recorded from the public site in Chrome; the `ask.py` scene shows a real terminal run. |
| Video at a public URL (YouTube, Vimeo, Facebook Video or Youku) | §4.4 | ⬜ user | Upload as Public with `video/final_candidate.srt`; paste the link into the project page. |
| No third-party trademarks, copyrighted music or other material | §4.4 | ✅ | Fictional clubs/players, own screen recordings, synthetic voice, no music, no logos. |
| Public GitHub repository URL | §4.4 | ✅ | https://github.com/Wichai-pan/inside-the-game — public (checked 25 Oct). Push any later changes before submitting. |
| No inappropriate content | §4.4 | ✅ | — |
| Synthetic, football-realistic data; sources licensed | §4.7 | ✅ | Seeded stitched matches with fictional clubs/players (`insidegame/remix.py`, `DATA_PLAN.md`). The published pages credit the DFL open data (Bassek et al. 2025, CC BY 4.0) in the footer and on `about.html`. |
| Working project accessible for testing, free, no login | §4.8 | ✅ | https://wichai-pan.github.io/inside-the-game/ (static, no login). `testing_instructions.md`. Keep it online until 10 Nov 2026. |
| Materials in English | §4.9 | ✅ | All submission documents and the video in English; Chinese appears only as product output. |
| Original work, solely owned | §4.6, §4.11 | ⬜ user | User to confirm. |
| No financial/preferential support from Microsoft | §4.12 | ⬜ user | User to confirm. |

## Challenge pipeline (§4.1 i)

| Stage | Status | Evidence |
| --- | --- | --- |
| Ingest synthetic events as they happen | ✅ | `FeedIngestor` executor, events in time order (`insidegame/agents.py`, `insidegame/live.py`). |
| Interpret into statistics and patterns | ✅ | `insidegame/analyst.py`, `insidegame/stats.py`. |
| Explain why a moment matters | ✅ | "Why it matters" + "Evidence from the feed" (event IDs) on the live page; moment ranking in `analyst.py`. |
| Render alongside the match | ✅ | `docs/index.html`, `docs/studio.html`; `overlays` cues in each bundle. |
| Personalize (casual vs. analyst, club, player, language) | ✅ | Viewer, language, follow and player focus controls. |

## Suggested features (§4.1 ii–vi)

| Feature | Status | Evidence |
| --- | --- | --- |
| Player identification, speed/distance indicators | ✅ | Player names tagged on the pitch; click a dot for the player focus card (distance, top speed, sprints); player tags in overlay cues. Threshold-triggered indicators: 🟡 (sprint count shown; no pop-up on threshold). |
| Pass quality: distance, accuracy, difficulty | ✅ | `metrics.length_m`, pass completion, difficulty 0–100. |
| Speed: ball speed, shot speed | ✅ | `ball_speed_kmh`, shot speed from tracking. |
| Auto-eventing from video | ❌ | Not in scope; events come from the data. Stated in limitations. |
| Narratives, milestones, recaps | ✅ | Live captions, automated recap, post-match studio. |
| Explainability (control vs. chaos, pressure, rhythm) | ✅ | Moment kinds: in control, chaotic spell, pressure pays off, high regain; momentum in studio. |
| Data innovation (synthetic dataset) | ✅ | Seeded match stitching with provenance. |
| Multi-language | 🟡 | English and Simplified Chinese (two languages). |

## Judging criteria (§6.2, 20% each)

| Criterion | Our case | Gaps |
| --- | --- | --- |
| Technological implementation | Synthetic data generator; Agent Framework workflows; kloppy/databallpy statistics; modular package; README + `docs/ARCHITECTURE.md`; provider switch to Foundry/Azure OpenAI by configuration (`providers.py`); 72 pytest tests, GitHub Actions CI green on d4a0446; `evaluate.py` error measurement. | Foundry/Azure not run live (no subscription). |
| Agentic design & innovation | Two multi-agent workflows; auditor agent extracts claims while code judges; retry loops (38 of 108 live lines rewritten after a rejection); template fallback; measured live latency; MCP server with 7 tools; `ask.py` agent that discovers and calls them. | `ask.py` answers are not fact-checked. |
| Real-world impact | One feed → many audiences and languages; traceable claims; errors measured (flagged agent texts 40/178 → 1/180); latency measured honestly. | Median 11–12 s per line on a laptop; needs a hosted model for live use (stated). Hand read still finds ~3–4% of live lines with a wrong detail. |
| User experience & presentation | Broadcast-style match centre with "Try it in 30 seconds", click-a-dot player focus, studio and How it works page; two-voice captions; evidence per line; 1:50 video. | Synthetic voice-over. |
| Category adherence (Best Multi-Agent System) | Distinct roles, shared state, handoffs, failure recovery, end-to-end outcome. | — |

## Category fit

| Category | Fit | Note |
| --- | --- | --- |
| Best Multi-Agent System | Strong | Primary target. |
| Grand Prize | Eligible | Automatically considered. |
| Best Use of Microsoft Foundry | Weak | Foundry not used live (no Azure subscription). Only claim "ready to switch". |
| Best Enterprise Solution | Weak–medium | Checks and traceability help; no deployment story. |
| Best Azure Cloud Native Integration | Not met | No Azure services used. |

## Open items before the deadline

1. Watch `video/final_candidate.mp4` once; optionally re-voice it with a human voice on the same timings (`video_script.md`).
2. Upload the video to YouTube as Public (with `video/final_candidate.srt`); add the link to `project_page.md` and the project page.
3. On https://aka.ms/insidethegame: confirm registration, team details/Representative, choose the category, paste the pitch, repo URL, demo URL, video URL and testing instructions; submit before 27 Oct 23:59 PT.
4. Keep the GitHub Pages site and repo public and unchanged in substance until judging ends (10 Nov 2026).

## Accuracy issues seen on the published demo (25 Oct, after d4a0446)

The earlier issues (match 42 "Harbor City level", "low xG" for 0.155) are gone after the regeneration. Still visible in match 42 (English, analyst):

- 3:11: "Callum Ashby finishes Callum Ashby's shot from Sami Halloran's pass, blocked by Kian Morrow…" — repeated name, and "finishes" for a blocked shot.
- 6:15: "Luca Brennan completes a 25 m, 98/100 difficulty pass…" — the passer was Ravi Kendrick; Brennan received it (the Chinese line has it right).
- `ask.py "Why did Ridgeway win?"` once answered "Harbor City's superior xG of 0.31 compared to Ridgeway United's 1.04" and claimed a late equaliser that did not happen. The video uses a different, checked answer.
