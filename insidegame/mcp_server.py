"""MCP server: match intelligence from the published bundles, as tools any MCP client or agent can call.

Reads the bundles in docs/synthetic/ (index.json, <id>.json, <id>-stats.json, <id>-studio.json); it
never calls a model, so every answer is a fact from the feed or a line that already passed the checks.

    .venv/bin/python -m insidegame.mcp_server                 # stdio transport (Claude, VS Code, Agent Framework)
    INSIDEGAME_BUNDLES=docs/real .venv/bin/python -m insidegame.mcp_server

Tools: list_matches, get_match_state, get_key_moments, get_player_stats, get_team_stats,
get_commentary, get_studio_analysis. Each takes a match id from list_matches.
"""
import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

ROOT = Path(__file__).resolve().parent.parent
LANGS, PROFILES = ("en", "zh"), ("casual", "analyst")
_cache = {}


def bundle_dir():
    return Path(os.environ.get("INSIDEGAME_BUNDLES", ROOT / "docs" / "synthetic"))


def _load(name):
    """Read a bundle file, re-reading it when the file changes (the build scripts rewrite bundles)."""
    path = bundle_dir() / name
    if not path.exists():
        raise ToolError(f"{name} not found in {bundle_dir()}")
    stamp = path.stat().st_mtime_ns
    if _cache.get(path, (None,))[0] != stamp:
        _cache[path] = (stamp, json.loads(path.read_text(encoding="utf-8")))
    return _cache[path][1]


def _ids():
    return [m["id"] for m in _load("index.json")["matches"]]


def _match(match_id, part=""):
    """The match bundle, or with part="stats"/"studio" the file that goes with it."""
    if match_id not in _ids():
        raise ToolError(f"unknown match {match_id!r}; call list_matches (known: {', '.join(_ids())})")
    return _load(f"{match_id}{'-' + part if part else ''}.json")


def _check(lang=None, profile=None):
    if lang is not None and lang not in LANGS:
        raise ToolError(f"lang must be one of {LANGS}")
    if profile is not None and profile not in PROFILES:
        raise ToolError(f"profile must be one of {PROFILES}")


def _names(match):
    return {t["id"]: t["name"] for t in match["teams"]}


def _score(match, state):
    teams = _names(match)
    return f"{teams['a']} {state['a']['goals']}-{state['b']['goals']} {teams['b']}"


def list_matches() -> dict:
    """List the available matches with their id, teams, final score and length in minutes."""
    out = []
    for entry in _load("index.json")["matches"]:
        m = _load(f"{entry['id']}.json")
        final = m["final_stats"]
        out.append({"id": entry["id"], "label": entry["label"], "teams": [t["name"] for t in m["teams"]],
                    "final_score": _score(m, final), "minutes": round(m["duration"] / 60, 1),
                    "synthetic": m.get("synthetic", False), "moments": len(m["moments"])})
    return {"matches": out}


def get_match_state(match_id: str, minute: float) -> dict:
    """Score and running team statistics at a given match minute (e.g. 6.5), plus the last recorded event."""
    m = _match(match_id)
    t = float(minute) * 60
    before = [e for e in m["events"] if e["t"] <= t and e.get("state")]
    if not before:
        return {"minute": minute, "score": _score(m, {"a": {"goals": 0}, "b": {"goals": 0}}),
                "note": "no events recorded yet"}
    last = before[-1]
    teams, st = _names(m), last["state"]
    keys = ("goals", "shots", "on_target", "xg", "passes", "pass_pct", "possession_pct")
    return {"minute": minute, "clock": f"{int(t // 60)}:{int(t % 60):02d}", "score": _score(m, st),
            "teams": {teams[k]: {x: st[k][x] for x in keys} for k in ("a", "b")},
            "last_event": last["fact"]["en"]}


def get_key_moments(match_id: str, kind: str | None = None, lang: str = "en") -> dict:
    """Key moments in order, each with the facts behind it and why it matters.

    kind filters by moment type (goal, shot, pressure_win, long_spell, ...); lang is en or zh."""
    _check(lang)
    m = _match(match_id)
    teams = _names(m)
    out = []
    for mo in m["moments"]:
        if kind and mo["kind"] != kind:
            continue
        key = next((f for f in mo["facts"] if f["event_id"] == mo["key_event"]), mo["facts"][-1])
        out.append({"id": mo["id"], "clock": mo["clock"], "kind": mo["kind"], "team": teams.get(mo["team"]),
                    "headline": mo["headline"][lang], "key_fact": key[lang],
                    "why_it_matters": [w[lang] for w in mo["why"]], "score_after": _score(m, mo["state"])})
    return {"match": match_id, "moments": out}


def get_player_stats(match_id: str, player: str | None = None, team: str | None = None,
                     sort_by: str = "xt", limit: int = 5) -> dict:
    """Player statistics (distance, sprints, passes, xT, shots, xG, goals, assists, tackles ...).

    player matches part of a name; team matches part of a team name; otherwise the top `limit`
    players by `sort_by` (any numeric field, e.g. xg, xt, distance_km, sprints, progressive_passes)."""
    rows = _match(match_id, "stats")["players"]
    if player:
        rows = [r for r in rows if player.lower() in r["player"].lower()]
    if team:
        rows = [r for r in rows if team.lower() in r["team"].lower()]
    if not player:
        if rows and not isinstance(rows[0].get(sort_by), (int, float)):
            raise ToolError(f"sort_by must be a numeric field: {', '.join(k for k, v in rows[0].items() if isinstance(v, (int, float)))}")
        rows = sorted(rows, key=lambda r: r[sort_by], reverse=True)[:max(1, int(limit))]
    return {"match": match_id, "players": rows}


def get_team_stats(match_id: str) -> dict:
    """Full-match team statistics, the score by two-minute segments, goals and shots, with metric definitions."""
    s = _match(match_id, "stats")
    home, away = (t["team"] for t in s["teams"])
    segments = [{"minutes": seg["minutes"], home: seg["home"], away: seg["away"]} for seg in s["segments"]]
    return {"match": s["match"], "teams": s["teams"], "segments": segments, "goals": s["goals"],
            "shots": s["shots"], "definitions": s["definitions"]}


def get_commentary(match_id: str, profile: str = "casual", lang: str = "en") -> dict:
    """Checked commentary for a viewer profile (casual or analyst) and language (en or zh):
    one line per key moment plus the automated recap. Each line says whether an agent or the
    template fallback wrote it."""
    _check(lang, profile)
    m = _match(match_id)
    lines = []
    for mo in m["moments"]:
        c = mo.get("commentary", {}).get(profile, {}).get(lang)
        if c:
            lines.append({"clock": mo["clock"], "headline": mo["headline"][lang], "text": c["text"],
                          "source": c["source"]})
    recap = (m.get("recap") or {}).get(profile, {}).get(lang)
    return {"profile": profile, "lang": lang, "lines": lines, "recap": recap and recap["text"],
            "final": m["final_stats"][lang]}


def get_studio_analysis(match_id: str, profile: str = "casual", lang: str = "en") -> dict:
    """Post-match studio analysis: storylines (verdict, turning point, momentum, tactics, deserved?,
    standout players), each with the pundit text and the facts it was checked against."""
    _check(lang, profile)
    s = _match(match_id, "studio")
    sections = []
    for sec in s["sections"]:
        text = sec.get("text", {}).get(profile, {}).get(lang) or {}
        sections.append({"id": sec["id"], "title": sec["title"][lang], "text": text.get("text"),
                         "facts": sec["facts"][lang]})
    return {"match": s["match"], "profile": profile, "lang": lang, "sections": sections}


TOOLS = (list_matches, get_match_state, get_key_moments, get_player_stats, get_team_stats,
         get_commentary, get_studio_analysis)


def build_server():
    server = MCPServer(
        "inside-the-game",
        instructions=("Match intelligence for synthetic football matches. Start with list_matches to get a "
                      "match id. All numbers come from the event feed; commentary and studio texts already "
                      "passed a fact checker."))
    for fn in TOOLS:
        server.tool()(fn)
    return server


def main():
    build_server().run("stdio")


if __name__ == "__main__":
    main()
