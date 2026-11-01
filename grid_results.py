"""Export, independently verify and report frozen randomized model/effort grids.

One service grid is one frozen batch: every cell and prompt shares one bounded
concurrency controller. Public exports are allowlisted, hash-covered and carry
the full frozen schedule so every row can be checked against it.
"""

import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import batch
import public_results
import verify
from statistics_helpers import CONFIDENCE, tv_to_uniform, wilson_interval

REPO = Path(__file__).resolve().parent
SCHEMA = "random-number-public-grid-v1"
GRID_SCHEMA = "random-number-grid-v1"
PROFILE_SCHEMA = "random-number-public-grid-v2"
PROFILE_GRID_SCHEMA = "random-number-grid-v2"
DESIGN = "randomized_complete_cell_blocks_with_adjacent_prompt_pairs"
CONDITIONS = public_results.CONDITIONS
OUTCOMES = public_results.OUTCOMES
NUMBERS = tuple(str(i) for i in range(1, 11))
SYSTEM_PROMPT = "You are a helpful assistant."
STATES = {"planned", "running", "paused", "stopped", "complete"}
HEX = re.compile(r"[0-9a-f]{64}")
LABEL = re.compile(r"[A-Za-z0-9 ._:/-]{1,128}")
NUMBER = re.compile(r"(?:[1-9]|10)", re.ASCII)
DATED_SUFFIX = re.compile(r"-\d{8}$")
CONTROL_KEYS = (
    "max_attempts",
    "timeout",
    "base_backoff",
    "max_backoff",
    "recovery_successes",
    "circuit_errors",
    "max_seconds",
    "max_cost",
)
GRID_KEYS = {
    "schema",
    "created_at",
    "runner",
    "provider",
    "protocol",
    "cells",
    "planned_per_condition",
    "order_seed",
    "design",
    "order_note",
    "batch_manifest_sha256",
}
SCHEDULE_KEYS = {
    "trial",
    "block",
    "cell",
    "condition",
    "condition_trial",
    "model",
    "thinking",
    "prompt",
}
MANIFEST_KEYS = {
    "schema",
    "exported_at",
    "grid",
    "schedule",
    "service",
    "interval",
    "redactions",
    "cost_basis",
    "model_identity",
    "files_sha256",
}
ROW_KEYS = {
    "runner",
    "evidence_level",
    "observed_model",
    "condition",
    "trial",
    "attempt",
    "outcome",
    "number",
    "response_text",
    "started_at",
    "finished_at",
    "elapsed_seconds",
    "http_statuses",
    "retry_after_seconds",
    "session_id_sha256",
    "response_id_sha256",
    "raw_result_sha256",
    "raw_evidence_sha256",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "pi_reported_cost",
    "reported_cost_known",
    "global_trial",
    "block",
    "cell",
    "requested_model",
    "requested_effort",
    "upstream_provider",
    "system_fingerprints",
}
TIMESTAMP = re.compile(r"[0-9T:.+-]{10,40}Z?")
PREAMBLE = {
    "pi": "system override plus cwd",
    "claude": "system override; native CLI context may remain",
    "codex": "instructions file; native CLI context may remain",
    "grok": "system override; native CLI context may remain",
    "agy": "unsupported by CLI; native agent instructions remain",
}


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def json_sha256(value):
    """Hash of the exact bytes batch.save writes for this value."""
    text = json.dumps(value, indent=2, allow_nan=False) + "\n"
    return hashlib.sha256(text.encode()).hexdigest()


def source_files(runner, grid_schema=GRID_SCHEMA):
    return (
        {"batch.py", "verify.py", "pi-audit.ts", "grid.py"}
        | ({"cli_runners.py"} if runner != "pi" else set())
        | (
            {"openrouter_profiles.py", "pi-profile.ts"}
            if grid_schema == PROFILE_GRID_SCHEMA
            else set()
        )
    )


def public_schema(grid):
    return PROFILE_SCHEMA if grid["schema"] == PROFILE_GRID_SCHEMA else SCHEMA


def evidence_level(runner):
    return "pi_http_request" if runner == "pi" else "cli_transcript"


def planned_schedule(grid):
    """Regenerate the frozen order exactly as grid.plan draws it from the seed."""
    rng = random.Random(grid["order_seed"])
    prompts = grid["protocol"]["prompts"]
    schedule = []
    for index in range(1, grid["planned_per_condition"] + 1):
        ordered = list(grid["cells"])
        rng.shuffle(ordered)
        for cell in ordered:
            conditions = list(prompts)
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
                        "prompt": prompts[condition],
                        **(
                            {"openrouter_profile": cell["request_profile"]}
                            if grid["schema"] == PROFILE_GRID_SCHEMA
                            else {}
                        ),
                    }
                )
    return schedule


def validate_grid(grid, schedule):
    """Check cell definitions and the complete frozen schedule; return cells by id."""
    if set(grid) != GRID_KEYS or grid["schema"] not in (
        GRID_SCHEMA,
        PROFILE_GRID_SCHEMA,
    ):
        raise ValueError("Unexpected grid specification")
    profiled = grid["schema"] == PROFILE_GRID_SCHEMA
    if grid["design"] != DESIGN:
        raise ValueError("Unknown grid design")
    prompts = grid["protocol"].get("prompts")
    if (
        not isinstance(prompts, dict)
        or set(prompts) != set(CONDITIONS)
        or any(not isinstance(p, str) or not p.strip() for p in prompts.values())
    ):
        raise ValueError("Expected exactly baseline and meta prompts")
    cells = {}
    for cell in grid["cells"]:
        if (
            not isinstance(cell, dict)
            or set(cell)
            != (
                {"id", "model", "thinking", "request_profile"}
                if profiled
                else {"id", "model", "thinking"}
            )
            or not all(
                isinstance(cell[k], str) and cell[k]
                for k in ("id", "model", "thinking")
            )
            or cell["id"] in cells
        ):
            raise ValueError("Invalid or duplicate cell definition")
        expected_id = f"{cell['model'].replace('/', '--')}--{cell['thinking']}"
        if profiled:
            import openrouter_profiles

            openrouter_profiles.validate_profile(
                cell["request_profile"], cell["model"], cell["thinking"]
            )
            expected_id += f"--t{cell['request_profile']['temperature']:g}"
        if cell["id"] != expected_id:
            raise ValueError("Cell identity differs from its model and settings")
        cells[cell["id"]] = cell
    count = grid["planned_per_condition"]
    if not cells or type(count) is not int or count < 1:
        raise ValueError("Empty grid")
    if type(grid["order_seed"]) is not int:
        raise ValueError("Grid order seed must be an integer")
    width = 2 * len(cells)
    if not isinstance(schedule, list) or len(schedule) != width * count:
        raise ValueError("Grid trial count mismatch")
    seen = set()
    for i, row in enumerate(schedule, 1):
        if not isinstance(row, dict) or set(row) != (
            SCHEDULE_KEYS | {"openrouter_profile"} if profiled else SCHEDULE_KEYS
        ):
            raise ValueError("Unexpected grid schedule fields")
        cell = cells.get(row["cell"])
        key = (row["cell"], row["condition"], row["condition_trial"])
        if (
            cell is None
            or any(
                type(row[k]) is not int for k in ("trial", "block", "condition_trial")
            )
            or row["trial"] != i
            or row["condition"] not in CONDITIONS
            or key in seen
            or row["condition_trial"] != (i - 1) // width + 1
            or row["block"] != row["condition_trial"]
            or row["model"] != cell["model"]
            or row["thinking"] != cell["thinking"]
            or row["prompt"] != prompts[row["condition"]]
            or row.get("openrouter_profile") != cell.get("request_profile")
        ):
            raise ValueError("Invalid grid trial mapping")
        seen.add(key)
    for i in range(0, len(schedule), 2):
        first, second = schedule[i : i + 2]
        if first["cell"] != second["cell"] or {
            first["condition"],
            second["condition"],
        } != set(CONDITIONS):
            raise ValueError("Prompts are not paired within a cell")
    if schedule != planned_schedule(grid):
        raise ValueError("Schedule does not reproduce from the frozen order seed")
    return cells


def tally(rows, planned, profiled=False):
    valid = [r for r in rows if r["outcome"] == "valid"]
    if len({r["global_trial"] for r in valid}) != len(valid):
        raise ValueError("Duplicate accepted logical trial")
    counts = {k: sum(r["number"] == int(k) for r in valid) for k in NUMBERS}
    n = len(valid)
    invalid = sum(r["outcome"] == "invalid_response" for r in rows)
    return {
        "planned_trials": planned,
        "attempted_trials": len({r["global_trial"] for r in rows}),
        "accepted_trials": n,
        "physical_attempts": len(rows),
        "excluded_attempts": len(rows) - n,
        "invalid_response_attempts": invalid,
        **(
            {
                "truncated_response_attempts": sum(
                    r["invalid_reason"] == "truncated" for r in rows
                )
            }
            if profiled
            else {}
        ),
        "operational_exclusions": len(rows) - n - invalid,
        "outcomes": dict(sorted(Counter(r["outcome"] for r in rows).items())),
        "counts": counts,
        "wilson_intervals": {k: wilson_interval(v, n) for k, v in counts.items()},
        "tv_to_uniform": tv_to_uniform(list(counts.values())),
        "model_valid_response_rate": round(n / (n + invalid), 12)
        if n + invalid
        else None,
        "reported_cost": round(sum(r["pi_reported_cost"] for r in rows), 9),
        "missing_cost_attempts": sum(not r["reported_cost_known"] for r in rows),
    }


def summarize(rows, grid):
    planned = grid["planned_per_condition"]
    cells = {}
    for cell in grid["cells"]:
        mine = [r for r in rows if r["cell"] == cell["id"]]
        cells[cell["id"]] = {
            "model": cell["model"],
            "thinking": cell["thinking"],
            **(
                {"request_profile": cell["request_profile"]}
                if "request_profile" in cell
                else {}
            ),
            "conditions": {
                c: tally(
                    [r for r in mine if r["condition"] == c],
                    planned,
                    grid["schema"] == PROFILE_GRID_SCHEMA,
                )
                for c in CONDITIONS
            },
        }
    valid = [r for r in rows if r["outcome"] == "valid"]
    return {
        "schema": public_schema(grid),
        "interval_method": "wilson_score_pointwise",
        "confidence": CONFIDENCE,
        "totals": {
            "planned_trials": planned * 2 * len(cells),
            "attempted_trials": len({r["global_trial"] for r in rows}),
            "physical_attempts": len(rows),
            "accepted_trials": len(valid),
            "excluded_attempts": len(rows) - len(valid),
            "outcomes": dict(sorted(Counter(r["outcome"] for r in rows).items())),
            "counts": {k: sum(r["number"] == int(k) for r in valid) for k in NUMBERS},
        },
        "cells": cells,
    }


def export(root, destination):
    """Atomically write an immutable, verified public export of one frozen grid."""
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    if destination.exists():
        raise FileExistsError(
            "Result directories are immutable; choose a new output path"
        )
    if destination.is_relative_to(root):
        raise ValueError("Public export must be outside the raw grid")
    with batch.locked(root / "batch"):
        _export(root, destination)
    print(f"Verified public grid: {destination}")


def run_frozen_verifier(raw, sources):
    """Run the hash-checked snapshot verify.py, isolated from today's code."""
    with tempfile.TemporaryDirectory(prefix="frozen-verify-") as scratch:
        folder = Path(scratch)
        for name, sha in sources.items():
            shutil.copyfile(raw / "snapshot" / name, folder / name)
            if batch.digest(folder / name) != sha:
                raise ValueError("Frozen source snapshot changed")
        done = subprocess.run(
            [sys.executable, "-E", "-s", "-B", str(folder / "verify.py"), str(raw)],
            cwd=folder,
            env={"PATH": os.defpath},
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
    if done.returncode != 0:
        tail = (done.stderr.strip().splitlines() or ["no output"])[-1]
        raise ValueError(f"Frozen raw verification failed: {tail}")
    result = json.loads(done.stdout)
    if not all(
        result.get(k) is True
        for k in (
            "evidence_hashes_match",
            "error_attempts_excluded",
            "configuration_matches",
        )
    ):
        raise ValueError("Frozen raw verification did not pass")
    return result


def recount(raw, runner, schedule):
    """Map every physical attempt, including exclusions, to its frozen trial."""
    rows = []
    for folder in sorted((raw / "attempts").glob("*/*")):
        if not folder.is_dir():
            continue
        if not (folder / "result.json").exists():
            raise ValueError(
                "Unfinished attempt has no result; resume the frozen batch"
            )
        record = read(folder / "result.json")
        trial, attempt = record["trial"], record["attempt"]
        if (
            type(trial) is not int
            or type(attempt) is not int
            or not 1 <= trial <= len(schedule)
            or folder.parent.name != f"{trial:05d}"
            or folder.name != f"{attempt:02d}"
        ):
            raise ValueError("Attempt folder differs from its recorded trial")
        if (
            record.get("runner", "pi") != runner
            or record.get("evidence_level", "pi_http_request") != evidence_level(runner)
            or record["outcome"] not in OUTCOMES
        ):
            raise ValueError("Attempt runner, evidence or outcome label mismatch")
        if record["outcome"] != "valid" and record["number"] is not None:
            raise ValueError("Error attempt contributed a number")
        entry = schedule[trial - 1]
        row = public_results.normalized_record(
            record, entry["condition"], entry["condition_trial"], folder
        )
        row.update(
            global_trial=trial,
            block=entry["block"],
            cell=entry["cell"],
            requested_model=entry["model"],
            requested_effort=entry["thinking"],
            upstream_provider=record.get("upstream_provider")
            if runner == "pi"
            else None,
            system_fingerprints=record.get("system_fingerprints")
            if runner == "pi"
            else None,
        )
        if "openrouter_profile" in entry:
            row["invalid_reason"] = record.get("invalid_reason")
        rows.append(row)
    rows.sort(key=lambda r: (r["global_trial"], r["attempt"]))
    return rows


def _export(root, destination):
    raw = root / "batch"
    grid = read(root / "grid.json")
    if batch.digest(root / "grid.json") != json_sha256(grid):
        raise ValueError("grid.json is not in canonical frozen form")
    if batch.digest(raw / "manifest.json") != grid.get("batch_manifest_sha256"):
        raise ValueError("Frozen grid manifest changed")
    manifest = read(raw / "manifest.json")
    if manifest.get("grid") != {
        k: v for k, v in grid.items() if k != "batch_manifest_sha256"
    }:
        raise ValueError("Grid specification differs from the frozen batch")
    config = manifest["config"]
    runner = config.get("runner", "pi")
    schedule = manifest["schedule"]
    if (
        (runner, config["provider"]) != (grid["runner"], grid["provider"])
        or config["count"] != len(schedule)
        or config["system_prompt"] != SYSTEM_PROMPT
        or runner not in PREAMBLE
    ):
        raise ValueError("Grid service does not match the frozen batch")
    validate_grid(grid, schedule)
    sources = manifest["source_sha256"]
    if set(sources) != source_files(runner, grid["schema"]):
        raise ValueError("Unexpected source snapshot files")
    # Never execute frozen code before every snapshot hash is checked.
    for name, sha in sources.items():
        if batch.digest(raw / "snapshot" / name) != sha:
            raise ValueError("Frozen source snapshot changed")
    checked = run_frozen_verifier(raw, sources)
    rows = recount(raw, runner, schedule)
    valid = [r for r in rows if r["outcome"] == "valid"]
    if (
        checked["physical_attempts"] != len(rows)
        or checked["accepted_unique_trials"] != len(valid)
        or checked["counts_from_native_sessions"]
        != {k: sum(r["number"] == int(k) for r in valid) for k in NUMBERS}
    ):
        raise ValueError("Frozen verifier disagrees with the grid recount")
    status = read(raw / "status.json") if (raw / "status.json").exists() else {}
    state = status.get("state", "planned")
    if state not in STATES:
        raise ValueError("Unknown batch state")
    runner_metadata = manifest.get("runner_metadata") or {}
    provider_metadata = manifest.get("provider_metadata") or {}
    service = {
        "runner": runner,
        "provider": config["provider"],
        "evidence_level": evidence_level(runner),
        "runner_version": manifest.get("runner_version"),
        "runner_binary_sha256": runner_metadata.get("binary_sha256"),
        "runner_format": runner_metadata.get("format"),
        "auth_type": runner_metadata.get("auth_type")
        or provider_metadata.get("stored_auth_type"),
        "pi_version": manifest.get("pi_version"),
        "pi_settings_sha256": public_results.text_hash(
            json.dumps(manifest.get("pi_settings", {}), sort_keys=True)
        ),
        "models_file_sha256": provider_metadata.get("models_file_sha256"),
        "openrouter_routing": config.get("openrouter_routing"),
        "system_prompt": config["system_prompt"],
        "system_preamble_application": PREAMBLE[runner],
        "working_directory": "/" if config["cwd"] == "/" else None,
        "working_directory_sha256": public_results.text_hash(config["cwd"]),
        "controller": "One bounded concurrency controller shared by every cell and prompt in this service grid",
        "concurrency_ceiling": config["concurrency"],
        "control_settings": {k: config[k] for k in CONTROL_KEYS},
        "prompt_policy": manifest.get("prompt_policy"),
        "sampling": manifest.get("sampling"),
        "error_policy": manifest.get("error_policy"),
        "raw_schema_version": manifest["schema_version"],
        "raw_created_at": manifest["created_at"],
        "raw_manifest_sha256": batch.digest(raw / "manifest.json"),
        "grid_json_sha256": batch.digest(root / "grid.json"),
        "state": state,
        "elapsed_seconds": status.get("elapsed_seconds"),
        "source_sha256": sources,
        "raw_verifier": "Frozen snapshot verify.py, run in a subprocess after source hash checks",
        "raw_verification": {
            k: checked[k]
            for k in (
                "physical_attempts",
                "accepted_unique_trials",
                "unique_sessions",
                "counts_from_native_sessions",
                "evidence_hashes_match",
                "error_attempts_excluded",
                "configuration_matches",
            )
        },
    }
    summary = summarize(rows, grid)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".export-", dir=destination.parent
    ) as scratch:
        target = Path(scratch) / "result"
        target.mkdir()
        with (target / "attempts.jsonl").open("w") as stream:
            for row in rows:
                stream.write(json.dumps(row, allow_nan=False) + "\n")
        save(target / "summary.json", summary)
        (target / "source").mkdir()
        for name in sorted(sources):
            shutil.copyfile(raw / "snapshot" / name, target / "source" / name)
        metadata = {
            "schema": public_schema(grid),
            "exported_at": batch.now(),
            "grid": grid,
            "schedule": schedule,
            "service": service,
            "interval": {
                "method": "wilson_score",
                "confidence": CONFIDENCE,
                "scope": "Pointwise per cell, prompt and answer; proportion of accepted answers only",
            },
            "redactions": [
                "Working directories are replaced by hashes; a neutral root directory is disclosed as / when used.",
                "Session and response IDs are hashed. Credentials, environment, CLI argv, binary paths and free-form error text are not exported.",
                "Public rows are selected fields from verified native logs. Raw logs stay local; raw file hashes are commitments, not transcripts.",
            ],
            "cost_basis": (
                "OpenRouter response usage.cost for the pinned route when exposed, including failed attempts with usage; not a verified bill. Field name pi_reported_cost is kept for compatibility."
                if grid["schema"] == PROFILE_GRID_SCHEMA
                else "Runner-reported API-equivalent usage metadata when available, not a verified bill. Field name pi_reported_cost is kept for compatibility."
            ),
            "model_identity": "Requested model and effort come from the frozen schedule. Native reported model is retained and checked where exposed. Immutable backend snapshot is not independently established.",
        }
        metadata["files_sha256"] = {
            p.relative_to(target).as_posix(): batch.digest(p)
            for p in sorted(target.rglob("*"))
            if p.is_file()
        }
        save(target / "manifest.json", metadata)
        private = {str(root), str(REPO)}
        if len(str(Path.home())) > 1:
            private.add(str(Path.home()))
        for path in target.rglob("*"):
            if path.is_file() and any(s in path.read_text() for s in private):
                raise ValueError("Public export would expose a private path")
        verify_public(target)
        target.rename(destination)


def _amount(value, optional=True):
    if value is None:
        return optional
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _sha(value, optional=True):
    return (value is None and optional) or (
        isinstance(value, str) and HEX.fullmatch(value) is not None
    )


def verify_public(root):
    """Independently validate a public grid export; return (manifest, summary)."""
    root = Path(root).resolve()
    manifest = read(root / "manifest.json")
    if set(manifest) != MANIFEST_KEYS or manifest["schema"] not in (
        SCHEMA,
        PROFILE_SCHEMA,
    ):
        raise ValueError("Unexpected public grid manifest")
    grid, schedule, service = (
        manifest["grid"],
        manifest["schedule"],
        manifest["service"],
    )
    validate_grid(grid, schedule)
    if manifest["schema"] != public_schema(grid):
        raise ValueError("Public schema differs from the frozen grid schema")
    runner = service["runner"]
    if (
        runner not in PREAMBLE
        or (runner, service["provider"]) != (grid["runner"], grid["provider"])
        or service["evidence_level"] != evidence_level(runner)
        or service["system_preamble_application"] != PREAMBLE[runner]
        or service["system_prompt"] != SYSTEM_PROMPT
        or service["state"] not in STATES
        or service["raw_manifest_sha256"] != grid["batch_manifest_sha256"]
        or service["grid_json_sha256"] != json_sha256(grid)
        or (runner != "pi" and not _sha(service["runner_binary_sha256"], False))
    ):
        raise ValueError("Service provenance differs from the frozen grid")
    max_attempts = service["control_settings"]["max_attempts"]
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("Invalid retry budget")
    if set(service["source_sha256"]) != source_files(runner, grid["schema"]):
        raise ValueError("Invalid source metadata")
    files = manifest["files_sha256"]
    for name, sha in service["source_sha256"].items():
        if files.get(f"source/{name}") != sha:
            raise ValueError("Frozen source hash does not match provenance")
    required = {"attempts.jsonl", "summary.json"} | {
        f"source/{name}" for name in service["source_sha256"]
    }
    if set(files) != required:
        raise ValueError("Missing or unexpected public evidence file")
    for name, sha in files.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or batch.digest(path) != sha:
            raise ValueError("Public evidence hash mismatch")
    rows = [
        json.loads(line) for line in (root / "attempts.jsonl").read_text().splitlines()
    ]
    keys, accepted, sessions = set(), set(), set()
    for row in rows:
        profiled = grid["schema"] == PROFILE_GRID_SCHEMA
        if set(row) != ROW_KEYS | ({"invalid_reason"} if profiled else set()):
            raise ValueError("Unexpected public row fields")
        if profiled and (
            row["invalid_reason"] not in (None, "truncated", "format")
            or (row["outcome"] == "invalid_response")
            != (row["invalid_reason"] is not None)
        ):
            raise ValueError("Invalid response reason does not match outcome")
        if not (
            (
                row["observed_model"] is None
                or LABEL.fullmatch(str(row["observed_model"]))
            )
            and all(
                row[k] is None or TIMESTAMP.fullmatch(str(row[k]))
                for k in ("started_at", "finished_at")
            )
            and isinstance(row["http_statuses"], list)
            and all(type(s) is int for s in row["http_statuses"])
        ):
            raise ValueError("Unexpected free-form public row value")
        if any(
            type(row[k]) is not int
            for k in ("global_trial", "attempt", "trial", "block")
        ):
            raise ValueError("Invalid attempt metadata")
        if not 1 <= row["global_trial"] <= len(schedule) or not (
            1 <= row["attempt"] <= max_attempts
        ):
            raise ValueError("Attempt outside frozen schedule or retry budget")
        entry = schedule[row["global_trial"] - 1]
        if (
            row["cell"],
            row["condition"],
            row["trial"],
            row["block"],
            row["requested_model"],
            row["requested_effort"],
        ) != (
            entry["cell"],
            entry["condition"],
            entry["condition_trial"],
            entry["block"],
            entry["model"],
            entry["thinking"],
        ):
            raise ValueError("Public row differs from the frozen schedule")
        if (
            row["outcome"] not in OUTCOMES
            or row["runner"] != runner
            or row["evidence_level"] != evidence_level(runner)
            or not isinstance(row["http_statuses"], list)
            or (runner != "pi" and row["http_statuses"])
        ):
            raise ValueError("Invalid runner, evidence or outcome label")
        if not (
            all(
                _amount(row[k])
                for k in ("input_tokens", "output_tokens", "reasoning_tokens")
            )
            and all(_amount(row[k]) for k in ("elapsed_seconds", "retry_after_seconds"))
            and _amount(row["pi_reported_cost"], False)
            and type(row["reported_cost_known"]) is bool
            and (row["reported_cost_known"] or row["pi_reported_cost"] == 0)
        ):
            raise ValueError("Usage and cost must be finite nonnegative numbers")
        if not (
            _sha(row["session_id_sha256"])
            and _sha(row["response_id_sha256"])
            and _sha(row["raw_result_sha256"], False)
            and isinstance(row["raw_evidence_sha256"], list)
            and all(_sha(v, False) for v in row["raw_evidence_sha256"])
        ):
            raise ValueError("Invalid evidence hash")
        key = (row["global_trial"], row["attempt"])
        if key in keys or row["raw_result_sha256"] in keys:
            raise ValueError("Duplicate physical attempt")
        keys.update((key, row["raw_result_sha256"]))
        fingerprints = row["system_fingerprints"]
        if runner != "pi":
            if row["upstream_provider"] is not None or fingerprints is not None:
                raise ValueError("Upstream provider metadata on a native CLI row")
        elif not (
            (
                row["upstream_provider"] is None
                or LABEL.fullmatch(str(row["upstream_provider"]))
            )
            and (
                fingerprints is None
                or (
                    isinstance(fingerprints, list)
                    and all(
                        isinstance(f, str) and LABEL.fullmatch(f) for f in fingerprints
                    )
                )
            )
        ):
            raise ValueError("Invalid upstream provider metadata")
        if row["outcome"] != "valid":
            if row["number"] is not None or row["response_text"] is not None:
                raise ValueError("Error attempt contributed a number")
            continue
        text = row["response_text"]
        if (
            type(row["number"]) is not int
            or not isinstance(text, str)
            or not NUMBER.fullmatch(text.strip())
            or int(text.strip()) != row["number"]
        ):
            raise ValueError("Invalid response counted")
        if runner == "pi" and row["http_statuses"] != [200]:
            raise ValueError("Errored HTTP response counted")
        observed = row["observed_model"]
        profile = entry.get("openrouter_profile")
        if profile:
            if (
                observed not in profile["accepted_models"]
                or row["upstream_provider"] != profile["expected_provider"]
            ):
                raise ValueError("Observed profile model or upstream mismatch")
            if profile["mode"] in ("off", "non_reasoning") and row[
                "reasoning_tokens"
            ] not in (None, 0):
                raise ValueError("Reasoning observed in a thinking-off cell")
        elif observed is not None and entry["model"] not in (
            observed,
            DATED_SUFFIX.sub("", observed),
        ):
            raise ValueError("Observed model differs from the requested model")
        if row["global_trial"] in accepted:
            raise ValueError("Duplicate accepted logical trial")
        accepted.add(row["global_trial"])
        session = row["session_id_sha256"]
        if (runner == "pi" and not session) or (session and session in sessions):
            raise ValueError("Reused or missing native session")
        if session:
            sessions.add(session)
    history = [{**r, "trial": r["global_trial"]} for r in rows]
    verify.check_attempt_history(history, max_attempts)
    if service["state"] == "complete":
        by_trial = {}
        for row in history:
            by_trial.setdefault(row["trial"], []).append(row)
        if len(by_trial) != len(schedule) or any(
            max(v, key=lambda r: r["attempt"])["outcome"] in verify.RETRYABLE
            and len(v) < max_attempts
            for v in by_trial.values()
        ):
            raise ValueError("Complete grid has unfinished trials")
    actual = summarize(rows, grid)
    if actual != read(root / "summary.json"):
        raise ValueError("Public tally does not match attempt records")
    checked = service["raw_verification"]
    if (
        checked["physical_attempts"] != len(rows)
        or checked["accepted_unique_trials"] != len(accepted)
        or checked["counts_from_native_sessions"] != actual["totals"]["counts"]
        or not all(
            checked[k] is True
            for k in (
                "evidence_hashes_match",
                "error_attempts_excluded",
                "configuration_matches",
            )
        )
    ):
        raise ValueError("Raw verification record disagrees with public rows")
    return manifest, actual


def _interval(count, bounds):
    if bounds is None:
        return f"{count} [n/a]"
    return f"{count} [{bounds[0]:.3f}, {bounds[1]:.3f}]"


def report_lines(root, base=REPO):
    """Verified, timestamp-free Markdown section for one public grid."""
    root = Path(root).resolve()
    manifest, summary = verify_public(root)
    base = Path(base).resolve()
    link = (
        root.relative_to(base).as_posix()
        if root.is_relative_to(base)
        else f"results/{root.name}"
    )
    grid, service, totals = manifest["grid"], manifest["service"], summary["totals"]
    cells = summary["cells"]
    profiled = grid["schema"] == PROFILE_GRID_SCHEMA
    dimensions = (
        "Model | Mode | Requested effort | Temperature | Release | Upstream / precision | Stratum"
        if profiled
        else "Model | Requested effort"
    )
    dimension_rules = " | ".join(["---"] * (7 if profiled else 2))
    invalid = sum(
        row["invalid_response_attempts"]
        for cell in cells.values()
        for row in cell["conditions"].values()
    )
    binary = service["runner_binary_sha256"]
    lines = [
        f"## Grid {root.name}",
        "",
        "<!-- Generated by grid_results.report_lines. Do not edit manually. -->",
        "",
        f"Frozen {service['runner']} service grid via provider {service['provider']}: "
        f"{len(cells)} model/effort cells x 2 prompts x {grid['planned_per_condition']} "
        f"randomized complete blocks = {totals['planned_trials']} planned logical trials, "
        "all under one shared bounded concurrency controller. "
        f"State: {service['state']}.",
        f"Recorded {totals['physical_attempts']} physical attempts for "
        f"{totals['attempted_trials']} attempted logical trials: "
        f"{totals['accepted_trials']} accepted, {totals['excluded_attempts']} attempts excluded "
        f"({invalid} invalid responses, {totals['excluded_attempts'] - invalid} operational).",
        f"Runner {service['runner']} version `{service['runner_version']}`"
        + (f", binary sha256 `{binary[:12]}`" if binary else "")
        + f", evidence level {service['evidence_level']}. "
        "Requested model and effort are shown per row; reported model metadata is checked where the runner exposes it.",
        f"Evidence: [manifest, frozen schedule and provenance]({link}/manifest.json), "
        f"[attempts]({link}/attempts.jsonl), [summary]({link}/summary.json).",
        "",
        "### Cell summary",
        "",
        f"| {dimensions} | Prompt | Accepted / planned | Invalid responses | Operational exclusions | TV | Reported metadata (USD) |",
        f"| {dimension_rules} | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    answers = [
        "",
        "### Answer counts with 95% Wilson intervals",
        "",
        "Each entry is count [low, high] for the proportion of accepted answers in that cell and prompt.",
        "",
        f"| {dimensions} | Prompt | n | " + " | ".join(NUMBERS) + " |",
        f"| {dimension_rules} | --- | ---: | "
        + " | ".join(["---:"] * len(NUMBERS))
        + " |",
    ]
    for cell in cells.values():
        for condition in CONDITIONS:
            row = cell["conditions"][condition]
            label = f"| {cell['model']} | {cell['thinking']} | {condition} |"
            if profiled:
                profile = cell["request_profile"]
                label = f"| {cell['model']} | {profile['mode']} | {cell['thinking']} | {profile['temperature']:g} | {profile['release_period']} | {profile['expected_provider']} / {profile['quantization']} | {profile['stratum']} | {condition} |"
            tv = (
                "n/a" if row["tv_to_uniform"] is None else f"{row['tv_to_uniform']:.3f}"
            )
            cost = (
                "n/a" if row["missing_cost_attempts"] else f"{row['reported_cost']:.4f}"
            )
            lines.append(
                f"{label} {row['accepted_trials']} / {row['planned_trials']} | "
                f"{row['invalid_response_attempts']} | {row['operational_exclusions']} | "
                f"{tv} | {cost} |"
            )
            answers.append(
                f"{label} {row['accepted_trials']} | "
                + " | ".join(
                    _interval(row["counts"][k], row["wilson_intervals"][k])
                    for k in NUMBERS
                )
                + " |"
            )
    notes = [
        "",
        "Notes:",
        "",
        "- This grid is reported separately from historical rounds and from other grids. Nothing is pooled across models, efforts, prompts, runners or grids.",
        "- TV is total variation distance from uniform (0 is uniform, 0.90 is the maximum for ten answers). It is a point estimate; no bootstrap or other TV interval is reported.",
        "- Intervals are pointwise 95% Wilson score intervals. They are conditional on valid answers: invalid responses and operational exclusions are not in n.",
        "- They assume accepted trials are independent draws from a stable process. They are not simultaneous across cells or answers and do not prove the backend model snapshot stayed fixed.",
        "- n/a means no accepted answers (n=0), so no proportion, interval or TV is defined.",
        "- Dollar figures are runner-reported API-equivalent metadata, not verified charges; n/a means cost was not reported for every attempt.",
    ]
    return lines + answers + notes
