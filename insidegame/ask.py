"""Ask a question about a match: an Agent Framework agent answers it with the MCP server's tools.

The agent starts insidegame.mcp_server as a stdio subprocess (MCPStdioTool), discovers its tools and
decides which to call; the model comes from insidegame.providers (local Ollama by default, Microsoft
Foundry when INSIDEGAME_PROVIDER=foundry).

    .venv/bin/python -m insidegame.ask "Why did Ridgeway win?"
    .venv/bin/python -m insidegame.ask --match remix-21 "Who was the most dangerous player?" --lang zh
"""
import argparse
import asyncio
import sys
from pathlib import Path

from agent_framework import Agent, MCPStdioTool

from .providers import get_client, model_label

ROOT = Path(__file__).resolve().parent.parent

INSTRUCTIONS = (
    "You are a football analyst answering questions about one match. Always call the match tools before "
    "answering; never guess. Good starting points: get_studio_analysis for why a result happened, "
    "get_key_moments for what happened when, get_team_stats and get_player_stats for numbers. Answer in "
    "3-5 sentences, cite the numbers you used, and use only facts returned by the tools. Keep player and "
    "team names exactly as the tools write them."
)


def match_tools():
    return MCPStdioTool(name="inside_the_game", command=sys.executable, args=["-m", "insidegame.mcp_server"],
                        cwd=str(ROOT), description="Match intelligence over the published match bundles")


async def ask(question, match_id="remix-42", lang="en", model=None):
    """Return (answer text, names of the tools the agent called)."""
    language = "English" if lang == "en" else "Simplified Chinese (keep names in Latin letters)"
    async with match_tools() as tools:
        agent = Agent(get_client(model), f"{INSTRUCTIONS} Always write the answer in {language}.",
                      name="match_analyst", tools=tools)
        response = await agent.run(f"Match id: {match_id}. Pass lang=\"{lang}\" to tools that take it.\n"
                                   f"Question: {question}\nWrite the whole answer in {language}.",
                                   options={"temperature": 0.2, "think": False})
    called = [c.name for msg in response.messages for c in msg.contents if c.type == "function_call"]
    return response.text, called


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?", default="Why did Ridgeway win?")
    parser.add_argument("--match", default="remix-42")
    parser.add_argument("--lang", default="en", choices=("en", "zh"))
    parser.add_argument("--model")
    args = parser.parse_args()
    answer, called = asyncio.run(ask(args.question, args.match, args.lang, args.model))
    print(f"[{model_label(args.model)}] tools called: {', '.join(called) or 'none'}\n")
    print(answer)


if __name__ == "__main__":
    main()
