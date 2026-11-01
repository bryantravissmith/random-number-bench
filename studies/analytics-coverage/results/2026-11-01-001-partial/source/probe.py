# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Frozen analytics-coverage pilot using the existing bounded CLI controller.

The adapter changes only response validation and summary, in this process.
Existing number-benchmark sources and running snapshots remain unchanged.
"""

import argparse
from collections import Counter
import contextlib
import hashlib
import io
import json
from pathlib import Path
import random
import secrets
import shutil
import subprocess
import sys
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE if (HERE / "batch.py").exists() else HERE.parents[1]))
import batch
import cli_runners
import verify as history_verifier

NATIVE_PARSE = cli_runners.parse
FORMAT_ERROR = "Final response was not a complete bare integer in 1-10"
FIELDS = {"hypothesis", "test", "expected_if_true"}


def parse(*args, **kwargs):
    result = NATIVE_PARSE(*args, **kwargs)
    result["native_outcome"] = result["outcome"]
    result["native_error"] = result["error"]
    result["number"] = None
    if result["outcome"] == "valid" or (
        result["outcome"] == "invalid_response" and result["error"] == FORMAT_ERROR
    ):
        try:
            answer = json.loads(result["response"])
            if (
                not isinstance(answer, dict)
                or set(answer) != FIELDS
                or any(not isinstance(v, str) or not v.strip() for v in answer.values())
            ):
                raise ValueError("Expected exactly the three nonempty string fields")
            result.update(
                outcome="valid",
                error="",
                answer=answer,
                word_count=len(" ".join(answer.values()).split()),
            )
        except (ValueError, TypeError) as exc:
            result.update(
                outcome="invalid_response", error="Invalid response JSON: " + str(exc)
            )
    return result


def summary(root, manifest, rows=None):
    rows = batch.records(root) if rows is None else rows
    grouped = {}
    for row in rows:
        grouped.setdefault(row["trial"], []).append(row)
    terminals = [
        a[-1]
        for a in grouped.values()
        if a[-1]["outcome"] in batch.TERMINAL
        or len(a) >= manifest["config"]["max_attempts"]
    ]
    accepted = [r for r in rows if r["outcome"] == "valid"]
    if len({r["trial"] for r in accepted}) != len(accepted):
        raise ValueError("Duplicate accepted trial")
    result = {
        "schema": "analytics-coverage-progress-v1",
        "planned_trials": manifest["config"]["count"],
        "terminal_trials": len(terminals),
        "accepted_trials": len(accepted),
        "physical_attempts": len(rows),
        "excluded_attempts": len(rows) - len(accepted),
        "outcomes": dict(Counter(r["outcome"] for r in rows)),
        "pi_reported_cost": sum(
            ((r.get("usage") or {}).get("cost") or {}).get("total") or 0 for r in rows
        ),
    }
    batch.save(root / "summary.json", result)
    return result


def schedule(study, seed):
    rng = random.Random(seed)
    rows = []
    for block in range(1, study["repetitions"] + 1):
        cases = study["cases"].copy()
        rng.shuffle(cases)
        for case in cases:
            efforts = study["efforts"].copy()
            rng.shuffle(efforts)
            for effort in efforts:
                rows.append(
                    {
                        "trial": len(rows) + 1,
                        "block": block,
                        "cell": case["id"],
                        "condition": effort,
                        "condition_trial": block,
                        "model": study["model"],
                        "thinking": effort,
                        "prompt": case["observations"] + "\n\n" + study["instruction"],
                    }
                )
    return rows


def plan(root):
    study = batch.read(HERE / "study.json")
    seed = secrets.randbits(64)
    rows = schedule(study, seed)
    options = SimpleNamespace(
        out=root,
        runner="claude",
        provider="anthropic",
        model=study["model"],
        thinking=study["efforts"][0],
        count=len(rows),
        concurrency=4,
        max_attempts=3,
        timeout=180,
        base_backoff=15,
        max_backoff=300,
        recovery_successes=20,
        circuit_errors=8,
        max_seconds=1800,
        max_cost=20,
        prompt=rows[0]["prompt"],
        cwd=Path("/"),
        trial_prompts=None,
        trial_schedule=rows,
        pi_command=None,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        batch.plan(options)
    manifest = batch.read(root / "manifest.json")
    for name in ("probe.py", "study.json"):
        shutil.copyfile(HERE / name, root / "snapshot" / name)
        manifest["source_sha256"][name] = batch.digest(root / "snapshot" / name)
    manifest["analytics_study"] = {
        "schema": study["schema"],
        "order_seed": seed,
        "study_sha256": batch.digest(root / "snapshot/study.json"),
        "response_contract": "JSON hypothesis/test/expected_if_true, not a number",
        "verifier": "Frozen probe.py verify; generic number verifier does not apply",
    }
    batch.save(root / "manifest.json", manifest)
    batch.save(
        root / "plan.json", {"manifest_sha256": batch.digest(root / "manifest.json")}
    )
    print(
        f"Frozen {len(rows)} trials: {study['model']}, {study['efforts']}; no inference yet"
    )


def check_plan(root):
    manifest = batch.read(root / "manifest.json")
    if batch.read(root / "plan.json")["manifest_sha256"] != batch.digest(
        root / "manifest.json"
    ):
        raise ValueError("Frozen manifest changed")
    for name, sha in manifest["source_sha256"].items():
        if batch.digest(root / "snapshot" / name) != sha:
            raise ValueError("Frozen source changed: " + name)
    study = batch.read(root / "snapshot/study.json")
    if manifest["schedule"] != schedule(
        study, manifest["analytics_study"]["order_seed"]
    ):
        raise ValueError("Schedule does not reproduce")
    return manifest, study


def verified_rows(root):
    manifest, study = check_plan(root)
    rows = batch.records(root)
    history_verifier.check_attempt_history(rows, manifest["config"]["max_attempts"])
    for row in rows:
        folder = root / "attempts" / f"{row['trial']:05d}" / f"{row['attempt']:02d}"
        files = {
            str(p.relative_to(folder))
            for p in folder.rglob("*")
            if p.is_file() and p.name != "result.json"
        }
        if set(row["evidence_sha256"]) != files:
            raise ValueError("Evidence file set changed")
        if any(
            batch.digest(folder / name) != sha
            for name, sha in row["evidence_sha256"].items()
        ):
            raise ValueError("Evidence changed")
        config = batch.trial_config(manifest, row["trial"])
        request = batch.read(folder / cli_runners.REQUEST_FILE)
        if any(request[k] != config[k] for k in cli_runners.REQUEST_KEYS):
            raise ValueError("Request config changed")
        expected = cli_runners._argv(
            config, folder, manifest["runner_metadata"]["binary"]
        )
        if request["command"] != expected or row["command"] != expected:
            raise ValueError("CLI arguments changed")
        again = parse(
            folder,
            config,
            row.get("returncode"),
            timed_out=row["outcome"] == "timeout",
            interrupted=row["outcome"] == "interrupted",
        )
        for key in (
            "outcome",
            "number",
            "response",
            "answer",
            "word_count",
            "native_outcome",
            "session_id",
            "response_id",
            "observed_model",
            "usage",
        ):
            if again.get(key) != row.get(key):
                raise ValueError("Reparse differs: " + key)
    accepted = [r for r in rows if r["outcome"] == "valid"]
    if len({r["session_id"] for r in accepted}) != len(accepted):
        raise ValueError("Session reused")
    return manifest, study, rows


def export(root, out):
    with batch.locked(root):
        manifest, study, rows = verified_rows(root)
        state = batch.read(root / "status.json")["state"]
        if state != "complete":
            raise ValueError("Probe is not complete")
        out.mkdir(parents=True, exist_ok=False)
        public = []
        for row in rows:
            sched = manifest["schedule"][row["trial"] - 1]
            folder = root / "attempts" / f"{row['trial']:05d}" / f"{row['attempt']:02d}"
            public.append(
                {
                    **{
                        k: row.get(k)
                        for k in (
                            "trial",
                            "attempt",
                            "outcome",
                            "response",
                            "answer",
                            "word_count",
                            "native_outcome",
                            "observed_model",
                            "usage",
                            "elapsed_seconds",
                            "started_at",
                            "finished_at",
                        )
                    },
                    "case": sched["cell"],
                    "effort": sched["thinking"],
                    "block": sched["block"],
                    "raw_result_sha256": batch.digest(folder / "result.json"),
                    "raw_evidence_sha256": row["evidence_sha256"],
                    "session_id_sha256": hashlib.sha256(
                        str(row.get("session_id")).encode()
                    ).hexdigest(),
                }
            )
        batch.save(out / "attempts.json", public)
        batch.save(out / "study.json", study)
        batch.save(out / "schedule.json", manifest["schedule"])
        shutil.copytree(root / "snapshot", out / "source")
        batch.save(
            out / "manifest.json",
            {
                "schema": "analytics-coverage-public-v1",
                "state": state,
                "model": manifest["config"]["model"],
                "runner_version": manifest["runner_version"],
                "evidence_level": "cli_transcript",
                "auth_type": manifest["runner_metadata"]["auth_type"],
                "controls": {
                    k: manifest["config"][k]
                    for k in (
                        "concurrency",
                        "timeout",
                        "max_attempts",
                        "max_seconds",
                        "max_cost",
                    )
                },
                "sampling": manifest["sampling"],
                "system_prompt": manifest["config"]["system_prompt"],
                "cwd": "/",
                "raw_manifest_sha256": batch.digest(root / "manifest.json"),
                "source_sha256": manifest["source_sha256"],
                "files_sha256": {
                    str(p.relative_to(out)): batch.digest(p)
                    for p in out.rglob("*")
                    if p.is_file()
                },
            },
        )
        # The blind package is prepared before grading. Key stays private until
        # grading is frozen. It contains no settings, tokens, or execution order.
        accepted = [r for r in public if r["outcome"] == "valid"]
        secrets.SystemRandom().shuffle(accepted)
        blind = []
        key = {}
        for r in accepted:
            label = secrets.token_hex(6)
            blind.append({"id": label, "case": r["case"], "answer": r["answer"]})
            key[label] = {"trial": r["trial"], "attempt": r["attempt"]}
        batch.save(root / "blind-key.json", key)
        batch.save(root / "blind-responses.json", blind)
        print(
            f"Exported {len(public)} attempts; prepared {len(blind)} blinded responses"
        )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["plan", "run", "verify", "export"])
    p.add_argument("root", type=Path)
    p.add_argument("--out", type=Path)
    args = p.parse_args()
    root = args.root.resolve()
    if args.action == "plan":
        plan(root)
        return 0
    check_plan(root)
    frozen = root / "snapshot/probe.py"
    if Path(__file__).resolve() != frozen:
        return subprocess.call(
            [sys.executable, str(frozen), args.action, str(root)]
            + (["--out", str(args.out.resolve())] if args.out else [])
        )
    if args.action == "run":
        cli_runners.parse = parse
        batch.summary = summary
        sys.argv = [str(root / "snapshot/batch.py"), "run", str(root)]
        return batch.main()
    if args.action == "verify":
        _, _, rows = verified_rows(root)
        print(f"Verified {len(rows)} native attempts")
        return 0
    if not args.out:
        raise ValueError("--out required")
    export(root, args.out.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
