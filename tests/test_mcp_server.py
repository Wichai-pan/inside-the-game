"""MCP server tools, in process and over stdio exactly as an external client sees them."""
import asyncio
import json

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError

from insidegame import mcp_server as srv

from . import mcp_client
from .conftest import MATCH_IDS, load_bundle

MID = MATCH_IDS[0]


def test_list_matches_has_every_bundle():
    matches = srv.list_matches()["matches"]
    assert [m["id"] for m in matches] == MATCH_IDS
    for m in matches:
        final = load_bundle(f"{m['id']}.json")["final_stats"]
        assert f"{final['a']['goals']}-{final['b']['goals']}" in m["final_score"]


def test_match_state_follows_the_score():
    bundle = load_bundle(f"{MID}.json")
    goal = next(m for m in bundle["moments"] if m["kind"] == "goal")
    before = srv.get_match_state(MID, goal["t"] / 60 - 0.5)
    after = srv.get_match_state(MID, (goal["t"] + 5) / 60)
    assert before["score"] != after["score"] and after["score"] == srv._score(bundle, goal["state"])
    assert srv.get_match_state(MID, 0)["note"] == "no events recorded yet"


def test_key_moments_carry_why_it_matters():
    moments = srv.get_key_moments(MID)["moments"]
    assert len(moments) == len(load_bundle(f"{MID}.json")["moments"])
    goals = srv.get_key_moments(MID, kind="goal", lang="zh")["moments"]
    assert goals and all(m["kind"] == "goal" and m["why_it_matters"] for m in goals)


def test_player_and_team_stats():
    top = srv.get_player_stats(MID, sort_by="xg", limit=2)["players"]
    assert len(top) == 2 and top[0]["xg"] >= top[1]["xg"]
    name = top[0]["player"]
    assert srv.get_player_stats(MID, player=name.split()[-1].lower())["players"][0]["player"] == name
    team = srv.get_team_stats(MID)
    assert len(team["segments"]) == 6 and team["definitions"]["xt"]


@pytest.mark.parametrize("profile, lang", [("casual", "en"), ("analyst", "zh")])
def test_commentary_and_studio(profile, lang):
    c = srv.get_commentary(MID, profile, lang)
    assert c["lines"] and all(line["source"] for line in c["lines"])
    studio = srv.get_studio_analysis(MID, profile, lang)
    assert [s["id"] for s in studio["sections"]][0] == "verdict"


@pytest.mark.parametrize("call", [lambda: srv.get_commentary("nope"),
                                  lambda: srv.get_commentary(MID, "pundit"),
                                  lambda: srv.get_key_moments(MID, lang="fr"),
                                  lambda: srv.get_player_stats(MID, sort_by="player")])
def test_bad_arguments_raise_tool_errors(call):
    with pytest.raises(ToolError):
        call()


def test_in_process_client_sees_all_tools():
    async def session():
        async with Client(srv.build_server()) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            return names, await client.call_tool("get_key_moments", {"match_id": MID, "kind": "goal"})

    names, result = asyncio.run(session())
    assert names == {fn.__name__ for fn in srv.TOOLS}
    assert not result.is_error and json.loads(mcp_client.text_of(result))["moments"]


def test_stdio_client_round_trip():
    names, results = asyncio.run(mcp_client.run())
    assert "get_commentary" in names
    errors = {name for (name, _), (is_error, _) in results.items() if is_error}
    assert errors == {"get_match_state"}  # only the deliberately unknown match id fails
    bad = next(body for (name, args), (err, body) in results.items() if err)
    assert "unknown match" in bad
