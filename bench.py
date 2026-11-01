# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Freeze, run, verify and export repeated two-condition random-number rounds."""

import argparse
import hashlib
import json
import random
import secrets
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import public_results
from batch import RUNNER_DEFAULTS

ROOT = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def plan(args):
    if args.count < 1 or args.concurrency < 1:
        raise ValueError("Count and concurrency must be positive")
    root = args.out.resolve()
    root.mkdir(parents=True, exist_ok=False)
    protocol = read(ROOT / "protocol.json")
    default_provider, default_model = RUNNER_DEFAULTS[args.runner]
    args.model = args.model or default_model
    provider = args.provider or default_provider
    if args.runner == "pi" and provider not in ("openai", "openrouter"):
        raise ValueError(
            "The Pi adapter audits OpenAI Responses and OpenRouter Chat Completions"
        )
    seed = args.order_seed if args.order_seed is not None else secrets.randbits(64)
    rng = random.Random(seed)
    schedule = []
    for index in range(1, args.count + 1):
        pair = list(protocol["prompts"])
        rng.shuffle(pair)
        for condition in pair:
            schedule.append(
                {
                    "trial": len(schedule) + 1,
                    "condition": condition,
                    "condition_trial": index,
                }
            )
    prompts = [protocol["prompts"][row["condition"]] for row in schedule]
    save(root / "prompts.json", prompts)
    command = [
        sys.executable,
        str(ROOT / "batch.py"),
        "plan",
        "--out",
        str(root / "batch"),
        "--count",
        str(len(schedule)),
        "--concurrency",
        str(args.concurrency),
        "--provider",
        provider,
        "--runner",
        args.runner,
        "--model",
        args.model,
        "--thinking",
        args.thinking,
        "--trial-prompts",
        str(root / "prompts.json"),
        "--cwd",
        "/",
        "--max-seconds",
        str(args.max_seconds),
        "--max-cost",
        str(args.max_cost),
        "--max-attempts",
        str(args.max_attempts),
        "--timeout",
        str(args.timeout),
    ]
    if args.pi_command:
        command += ["--pi-command", args.pi_command]
    subprocess.run(command, check=True)
    manifest = root / "batch/manifest.json"
    save(
        root / "round.json",
        {
            "schema": "random-number-round-v1",
            "created_at": datetime.now(UTC).isoformat(),
            "protocol": protocol,
            "planned_per_condition": args.count,
            "order_seed": seed,
            "order_note": "Random order within adjacent baseline/meta pairs. This seed is not a model sampling seed.",
            "schedule": schedule,
            "batch_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        },
    )
    print(f"Frozen round: {root}; {args.count} trials per condition")


def validate_round(root):
    spec = read(root / "round.json")
    if (
        hashlib.sha256((root / "batch/manifest.json").read_bytes()).hexdigest()
        != spec["batch_manifest_sha256"]
    ):
        raise ValueError("Frozen batch manifest changed")
    manifest = read(root / "batch/manifest.json")
    schedule = spec["schedule"]
    expected = list(range(1, 2 * spec["planned_per_condition"] + 1))
    if [row["trial"] for row in schedule] != expected:
        raise ValueError("Invalid round trial schedule")
    for condition in public_results.CONDITIONS:
        trials = sorted(
            row["condition_trial"] for row in schedule if row["condition"] == condition
        )
        if trials != list(range(1, spec["planned_per_condition"] + 1)):
            raise ValueError("Invalid per-condition logical trials")
    for row, entry in zip(schedule, manifest["schedule"], strict=True):
        if entry.get("prompt") != spec["protocol"]["prompts"][row["condition"]]:
            raise ValueError("Condition label does not match the frozen prompt")
    return spec


def main():
    if sys.flags.optimize:
        raise RuntimeError("Verification requires Python assertions; do not use -O")
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    p = actions.add_parser("plan")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument(
        "--count", type=int, default=1000, help="Logical trials per condition"
    )
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument(
        "--runner", choices=["pi", "claude", "codex", "grok", "agy"], default="pi"
    )
    p.add_argument("--provider", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--thinking", default="medium")
    p.add_argument("--order-seed", type=int)
    p.add_argument("--max-seconds", type=float, default=7200)
    p.add_argument("--max-cost", type=float, default=10)
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--timeout", type=float, default=120)
    p.add_argument("--pi-command", help="JSON argv for offline process tests")
    for name in ("run", "export"):
        p = actions.add_parser(name)
        p.add_argument("round", type=Path)
        if name == "export":
            p.add_argument("--out", type=Path, required=True)
    p = actions.add_parser(
        "export-pair", help="Import separately run historical baseline/meta batches"
    )
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--meta", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = actions.add_parser("verify-public")
    p.add_argument("result", type=Path)
    p = actions.add_parser("report")
    p.add_argument(
        "--probes", action="store_true", help="Report local exploratory runs (ignored by Git)"
    )
    p = actions.add_parser(
        "check", help="Check native CLI subscription login without inference"
    )
    p.add_argument(
        "--runner", choices=["claude", "codex", "grok", "agy"], required=True
    )
    p.add_argument("--model")
    args = parser.parse_args()
    if args.action == "plan":
        plan(args)
    elif args.action == "check":
        import cli_runners

        print(json.dumps(cli_runners.preflight(args.runner, args.model), indent=2))
    elif args.action in ("run", "export"):
        root = args.round.resolve()
        spec = validate_round(root)
        if args.action == "run":
            return subprocess.call(
                [
                    sys.executable,
                    str(root / "batch/snapshot/batch.py"),
                    "run",
                    str(root / "batch"),
                ]
            )
        public_results.export(
            {"shared": root / "batch"},
            args.out,
            spec["protocol"],
            spec["schedule"],
            "interleaved_pairs",
            spec["order_seed"],
        )
    elif args.action == "export-pair":
        public_results.export(
            {"baseline": args.baseline.resolve(), "meta": args.meta.resolve()},
            args.out,
            read(ROOT / "protocol.json"),
            None,
            "separate_historical_batches",
            None,
        )
    elif args.action == "verify-public":
        public_results.verify_public(args.result)
        print("Public records, hashes and tallies verified")
    else:
        if args.probes:
            public_results.report(ROOT / "probes", ROOT / "PROBES.md", probes=True)
        else:
            public_results.report(ROOT / "results", ROOT / "RESULTS.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
