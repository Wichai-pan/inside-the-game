"""Build replay bundles for the demo site: simulate, run the agent workflow, export JSON.

    .venv/bin/python -m insidegame.build --seeds 5 21 62 --model qwen3.5:9b
"""
import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

from .agents import build_workflow
from .simulate import generate

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "synthetic"


def overlays(moments, players):
    """Timed, machine-readable graphics cues a broadcast renderer could consume directly."""
    names = {p["id"]: p["name"] for p in players}
    cues = []
    for m in moments:
        cues.append({"id": f"cue-{m['id']}", "type": "lower_third", "start": m["t"], "end": round(m["t"] + 8, 2),
                     "moment": m["id"], "headline": m["headline"], "team": m["team"],
                     "player_tags": [names[p] for p in m["players"][:3]], "metrics": m["metrics"],
                     "evidence": m["event_ids"]})
    return cues


async def run(seed, model, duration):
    from .providers import get_client, model_label

    match = generate(seed, duration)
    workflow = build_workflow(get_client(model), model)
    started = time.time()
    result = await workflow.run(match)
    outputs = result.get_outputs()
    if not outputs:
        raise RuntimeError(f"workflow produced no output for seed {seed}")
    out = outputs[-1]
    analysis = out["analysis"]
    lines = [c for m in analysis["moments"] for prof in m["commentary"].values() for c in prof.values()]
    sources = Counter(c["source"].split(":")[0] for c in lines)
    retried = sum(c["attempts"] > 1 for c in lines)
    bundle = {
        **{k: match[k] for k in ("seed", "duration", "hz", "synthetic", "pitch", "teams", "players", "frames")},
        "events": analysis["events"], "moments": analysis["moments"], "recap": out["recaps"],
        "final_stats": out["final_stats"], "overlays": overlays(analysis["moments"], match["players"]),
        "pipeline": {
            "framework": "Microsoft Agent Framework", "model": model_label(model),
            "executors": ["feed_ingestor", "narrator", "fact_checker", "claim_auditor", "producer", "recap_writer"],
            "lines": len(lines), "agent_lines": sources.get("agent", 0),
            "fallback_lines": sources.get("template_fallback", 0), "retried_lines": retried,
            "seconds": round(time.time() - started, 1),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"seed-{seed}.json"
    path.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")))
    print(f"{path.name}: {len(analysis['events'])} events, {len(analysis['moments'])} moments, "
          f"{bundle['pipeline']}")
    return bundle


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", type=int, nargs="+", default=[5, 21, 62])
    parser.add_argument("--model", default="qwen3.5:9b", help="Installed Ollama model")
    parser.add_argument("--duration", type=int, default=180, help="Clip length in seconds")
    args = parser.parse_args()
    for seed in args.seeds:
        asyncio.run(run(seed, args.model, args.duration))
    index = sorted(int(p.stem.split("-")[1]) for p in OUT.glob("seed-*.json"))
    (OUT / "index.json").write_text(json.dumps({"seeds": [s for s in index if s in args.seeds] or index}))


if __name__ == "__main__":
    main()
