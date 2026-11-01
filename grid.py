# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Freeze and run a randomized model/effort grid using one service controller."""

import argparse
import json
import random
import secrets
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import batch

ROOT = Path(__file__).resolve().parent
SCHEMA = "random-number-grid-v1"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODELS = {
    "codex": [
        "gpt-6.1-sol",
        "gpt-6.1-terra",
        "gpt-6-astra",
        "gpt-6-sol",
        "gpt-6-luna",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ],
    "claude": [
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "claude-fable-5-1",
        "claude-opus-4-8",
        "claude-sonnet-4-6",
    ],
    "grok": ["grok-4.5", "grok-4.7"],
    "pi": ["openai/gpt-4o"],
}


def cells_for(runner):
    cells = []
    for model in MODELS[runner]:
        efforts = (
            ("off",)
            if runner == "pi"
            else ("low", "medium", "high")
            if runner == "grok"
            else tuple(
                e
                for e in EFFORTS
                if not (model == "claude-sonnet-4-6" and e == "xhigh")
            )
        )
        for effort in efforts:
            cells.append(
                {
                    "id": f"{model.replace('/', '--')}--{effort}",
                    "model": model,
                    "thinking": effort,
                }
            )
    return cells


def plan(args):
    if args.count < 1:
        raise ValueError("Count must be positive")
    cells = (
        args.cells
        if getattr(args, "cells", None) is not None
        else cells_for(args.runner)
    )
    if not cells or len({c["id"] for c in cells}) != len(cells):
        raise ValueError("Grid requires unique cells")
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    protocol = batch.read(ROOT / "protocol.json")
    seed = args.order_seed if args.order_seed is not None else secrets.randbits(64)
    rng = random.Random(seed)
    schedule = []
    # Complete randomized blocks spread every cell across the experiment.
    # Prompt pairs are adjacent, but service completions can be out of order.
    for index in range(1, args.count + 1):
        ordered = cells.copy()
        rng.shuffle(ordered)
        for cell in ordered:
            conditions = list(protocol["prompts"])
            rng.shuffle(conditions)
            for condition in conditions:
                schedule.append(
                    {
                        "trial": len(schedule) + 1,
                        "block": index,
                        "cell": cell["id"],
                        "condition": condition,
                        "condition_trial": index,
                        "model": cell["model"],
                        "thinking": cell["thinking"],
                        "prompt": protocol["prompts"][condition],
                    }
                )
    first = cells[0]
    spec = {
        "schema": SCHEMA,
        "created_at": batch.now(),
        "runner": args.runner,
        "provider": "openrouter"
        if args.runner == "pi"
        else batch.RUNNER_DEFAULTS[args.runner][0],
        "protocol": protocol,
        "cells": cells,
        "planned_per_condition": args.count,
        "order_seed": seed,
        "design": "randomized_complete_cell_blocks_with_adjacent_prompt_pairs",
        "order_note": "Every block randomizes all model/effort cells and each cell's two prompts. Scheduling randomness is never inserted in prompts or model sampling parameters.",
    }
    options = SimpleNamespace(
        out=root / "batch",
        count=len(schedule),
        runner=args.runner,
        provider=spec["provider"],
        model=first["model"],
        thinking=first["thinking"],
        concurrency=args.concurrency,
        max_attempts=3,
        timeout=args.timeout,
        base_backoff=15,
        max_backoff=300,
        recovery_successes=20,
        circuit_errors=8,
        max_seconds=args.max_seconds,
        max_cost=args.max_cost,
        prompt=protocol["prompts"]["baseline"],
        cwd=Path("/"),
        trial_prompts=None,
        trial_schedule=schedule,
        pi_command=getattr(args, "pi_command", None),
        grid_spec=spec.copy(),
    )
    batch.plan(options)
    spec["batch_manifest_sha256"] = batch.digest(root / "batch/manifest.json")
    batch.save(root / "grid.json", spec)
    validate(root)
    print(f"Frozen {args.runner} grid: {len(cells)} cells, {len(schedule)} trials")


def validate(root):
    spec = batch.read(root / "grid.json")
    manifest = batch.read(root / "batch/manifest.json")
    if (
        spec["schema"] != SCHEMA
        or batch.digest(root / "batch/manifest.json") != spec["batch_manifest_sha256"]
    ):
        raise ValueError("Frozen grid manifest changed")
    if manifest.get("grid") != {
        k: v for k, v in spec.items() if k != "batch_manifest_sha256"
    }:
        raise ValueError("Grid specification differs from the frozen batch")
    validate_schedule(spec, manifest["schedule"], manifest["config"])
    return spec, manifest


def validate_schedule(spec, schedule, config):
    cells = {c["id"]: c for c in spec["cells"]}
    if len(cells) != len(spec["cells"]) or not cells:
        raise ValueError("Duplicate or empty cell definitions")
    if (config["runner"], config["provider"]) != (spec["runner"], spec["provider"]):
        raise ValueError("Grid service does not match batch")
    count = spec["planned_per_condition"]
    expected = {
        (cell, condition, i)
        for cell in cells
        for condition in ("baseline", "meta")
        for i in range(1, count + 1)
    }
    seen = set()
    if config["count"] != len(expected) or len(schedule) != len(expected):
        raise ValueError("Grid trial count mismatch")
    for i, row in enumerate(schedule, 1):
        cell = cells.get(row["cell"])
        key = (row["cell"], row["condition"], row["condition_trial"])
        if row["trial"] != i or key in seen or key not in expected:
            raise ValueError("Invalid grid trial mapping")
        if (
            any(row[k] != cell[k] for k in ("model", "thinking"))
            or row["prompt"] != spec["protocol"]["prompts"][row["condition"]]
        ):
            raise ValueError("Grid label differs from effective request")
        if row["block"] != row["condition_trial"]:
            raise ValueError("Invalid complete block assignment")
        seen.add(key)
    if seen != expected:
        raise ValueError("Incomplete grid schedule")
    for i in range(0, len(schedule), 2):
        pair = schedule[i : i + 2]
        if pair[0]["cell"] != pair[1]["cell"] or {r["condition"] for r in pair} != {
            "baseline",
            "meta",
        }:
            raise ValueError("Prompts are not paired within a cell")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("plan")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--runner", choices=list(MODELS), help="Use a built-in grid profile"
    )
    source.add_argument(
        "--spec", type=Path, help="JSON profile with runner and models/efforts"
    )
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--timeout", type=float, default=180)
    p.add_argument("--max-seconds", type=float, default=86400)
    p.add_argument("--max-cost", type=float, default=100)
    p.add_argument("--order-seed", type=int)
    p.add_argument("--pi-command")
    for name in ("run", "export", "status"):
        p = actions.add_parser(name)
        p.add_argument("grid", type=Path)
        if name == "export":
            p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "plan":
        if args.spec:
            profile = batch.read(args.spec)
            args.runner = profile["runner"]
            if args.runner not in MODELS:
                raise ValueError("Unknown profile runner")
            args.cells = []
            for row in profile["models"]:
                for effort in row["efforts"]:
                    args.cells.append(
                        {
                            "id": row["model"].replace("/", "--") + "--" + effort,
                            "model": row["model"],
                            "thinking": effort,
                        }
                    )
        plan(args)
        return 0
    root = args.grid.resolve()
    spec, manifest = validate(root)
    if args.action == "status":
        status = (
            batch.read(root / "batch/status.json")
            if (root / "batch/status.json").exists()
            else {"state": "planned"}
        )
        summary = (
            batch.read(root / "batch/summary.json")
            if (root / "batch/summary.json").exists()
            else {}
        )
        print(
            json.dumps(
                {
                    "runner": spec["runner"],
                    "cells": len(spec["cells"]),
                    "trials_per_prompt_per_cell": spec["planned_per_condition"],
                    "state": status["state"],
                    "elapsed_seconds": status.get("elapsed_seconds", 0),
                    "active": status.get("active", 0),
                    "concurrency_limit": status.get("controller", {}).get(
                        "limit", manifest["config"]["concurrency"]
                    ),
                    "stop_reason": status.get("controller", {}).get("stopped"),
                    **{
                        key: summary.get(key, 0)
                        for key in (
                            "terminal_trials",
                            "accepted_trials",
                            "excluded_attempts",
                            "physical_attempts",
                        )
                    },
                    "planned_trials": manifest["config"]["count"],
                },
                indent=2,
            )
        )
        return 0
    if args.action == "run":
        return subprocess.call(
            [
                sys.executable,
                str(root / "batch/snapshot/batch.py"),
                "run",
                str(root / "batch"),
            ]
        )
    import grid_results

    grid_results.export(root, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
