"""Real-match build with live latency measurement.

Each key moment is narrated the way a live system would do it: one workflow run per moment and
viewer variant, started when the moment is known, timed until the line has passed both checks.
The measured time is stored with the line and used to place it on the caption track, so the
replay shows analysis exactly as late as this machine would deliver it.

    .venv/bin/python -m insidegame.live --start 0 --end 600 --model qwen3.5:9b   # real Köln–Bayern clip
    .venv/bin/python -m insidegame.live --remix 7 --duration 600                  # stitched synthetic match
"""
import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from .agents import LANGS, PROFILES, build_workflow
from .analyst import analyse
from .build import overlays
from .dfl import load
from .playbyplay import schedule

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "real"


async def run_once(client, model, match, only):
    workflow = build_workflow(client, model)
    started = time.perf_counter()
    result = await workflow.run({**match, "_only": only})
    return result.get_outputs()[-1], time.perf_counter() - started


async def main_async(args):
    from agent_framework.ollama import OllamaChatClient

    client = OllamaChatClient(model=args.model)
    if args.remix is not None:
        from .remix import generate
        match = generate(args.remix, args.duration)
        out_dir, name = ROOT / "docs" / "synthetic", f"remix-{args.remix}"
        a, b = match["teams"]
        label = f"Match {args.remix}: {a['name']} v {b['name']}"
    else:
        match = load(args.start, args.end)
        out_dir, name, label = OUT, f"dfl-{args.start}-{args.end}", "Köln–Bayern (real data)"
    moments = analyse(match)["moments"]
    print(f"{len(match['events'])} events, {len(moments)} moments")

    # Warm the model so the first measurement is not a cold load.
    await run_once(client, args.model, match, {"moments": [moments[0]["id"]], "variants": [["casual", "en"]],
                                               "recap": False})
    lines = {m["id"]: {p: {} for p in PROFILES} for m in moments}
    for m in moments:
        for p in PROFILES:
            for l in LANGS:
                out, wall = await run_once(client, args.model, match,
                                           {"moments": [m["id"]], "variants": [[p, l]], "recap": False})
                line = next(x for x in out["analysis"]["moments"] if x["id"] == m["id"])["commentary"][p][l]
                lines[m["id"]][p][l] = line
                print(f"{m['clock']} {p}/{l}: {line['seconds']:.1f}s attempts={line['attempts']} {line['source']}")

    # Queueing: all four variants of one moment requested at once (one machine serving every viewer).
    goal = next((m for m in moments if m["kind"] == "goal"), moments[0])
    out, wall = await run_once(client, args.model, match, {"moments": [goal["id"]], "recap": False})
    goal_lines = next(x for x in out["analysis"]["moments"] if x["id"] == goal["id"])["commentary"]
    together = [c["seconds"] for v in goal_lines.values() for c in v.values()]

    # Recaps after the final event (one extra line is narrated to trigger the producer).
    out, _ = await run_once(client, args.model, match, {"moments": [moments[-1]["id"]], "variants": [["casual", "en"]],
                                                       "recap": True})
    analysis = out["analysis"]
    for m in analysis["moments"]:
        m["commentary"] = lines[m["id"]]

    seconds = [c["seconds"] for v in lines.values() for p in v.values() for c in p.values()]
    first_try = [c["seconds"] for v in lines.values() for p in v.values() for c in p.values() if c["attempts"] == 1]
    latency = {
        "per_line_seconds": {"median": round(statistics.median(seconds), 1), "min": round(min(seconds), 1),
                             "max": round(max(seconds), 1)},
        "first_try_median": round(statistics.median(first_try), 1) if first_try else None,
        "four_variants_at_once": sorted(round(x, 1) for x in together),
        "model": f"ollama:{args.model}", "note": "From the moment being known to the line passing both checks.",
    }
    captions = {p: {l: schedule(match, analysis["events"], analysis["moments"], p, l) for l in LANGS}
                for p in PROFILES}
    sources = [c["source"].split(":")[0] for v in lines.values() for p in v.values() for c in p.values()]
    bundle = {
        **{k: match[k] for k in ("seed", "duration", "t0", "hz", "synthetic", "source", "pitch", "teams", "players",
                                 "frames")},
        **({"provenance": match["provenance"], "script": match["script"]} if "provenance" in match else {}),
        "events": analysis["events"], "moments": analysis["moments"], "recap": out["recaps"],
        "final_stats": out["final_stats"], "overlays": overlays(analysis["moments"], match["players"]),
        "captions": captions, "latency": latency,
        "pipeline": {"framework": "Microsoft Agent Framework", "model": f"ollama:{args.model}",
                     "executors": ["feed_ingestor", "narrator", "fact_checker", "claim_auditor", "producer",
                                   "recap_writer"],
                     "lines": len(sources), "agent_lines": sources.count("agent"),
                     "fallback_lines": sources.count("template_fallback"),
                     "retried_lines": sum(c["attempts"] > 1 for v in lines.values() for p in v.values()
                                          for c in p.values()),
                     "seconds": round(sum(seconds), 1)},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{name}.json").write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")))
    index_path = out_dir / "index.json"
    try:
        index = json.loads(index_path.read_text())
    except (OSError, ValueError):
        index = {}
    matches = [m for m in index.get("matches", []) if m["id"] != name] + [{"id": name, "label": label}]
    index_path.write_text(json.dumps({**index, "matches": matches}))
    print(json.dumps(latency, indent=1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--end", type=int, default=600)
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--remix", type=int, default=None, help="Seed of a stitched synthetic match")
    parser.add_argument("--duration", type=int, default=600)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
