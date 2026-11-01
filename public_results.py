"""Export allowlisted evidence and independently recount public result records."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path

import batch
import verify

CONDITIONS = ("baseline", "meta")
OUTCOMES = {
    "valid",
    "invalid_response",
    "rate_limit",
    "provider_error",
    "timeout",
    "interrupted",
    "fatal_error",
    "protocol_error",
}
SOURCE_FILES = {"batch.py", "verify.py", "pi-audit.ts"}


def read(path):
    return json.loads(path.read_text())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode()).hexdigest() if value else None


def save(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def tally(rows, planned):
    output = {}
    for condition in CONDITIONS:
        selected = [r for r in rows if r["condition"] == condition]
        valid = [r for r in selected if r["outcome"] == "valid"]
        if len({r["trial"] for r in valid}) != len(valid):
            raise ValueError("Duplicate accepted logical trial")
        counts = {str(i): sum(r["number"] == i for r in valid) for i in range(1, 11)}
        n = len(valid)
        invalid = sum(r["outcome"] == "invalid_response" for r in selected)
        output[condition] = {
            "planned_trials": planned[condition],
            "attempted_trials": len({r["trial"] for r in selected}),
            "accepted_trials": n,
            "physical_attempts": len(selected),
            "excluded_attempts": len(selected) - n,
            "outcomes": dict(sorted(Counter(r["outcome"] for r in selected).items())),
            "counts": counts,
            "tv_to_uniform": round(
                sum(abs(v / n - 0.1) for v in counts.values()) / 2, 12
            )
            if n
            else None,
            "model_valid_response_rate": n / (n + invalid) if n + invalid else None,
            "pi_reported_cost": round(sum(r["pi_reported_cost"] for r in selected), 9),
        }
        if any("reported_cost_known" in r for r in selected):
            output[condition]["missing_cost_attempts"] = sum(
                not r.get("reported_cost_known", True) for r in selected
            )
    return output


def normalized_record(raw, condition, trial, folder):
    valid = raw["outcome"] == "valid"
    usage = raw.get("usage") or {}
    return {
        "runner": raw.get("runner", "pi"),
        "evidence_level": raw.get("evidence_level", "pi_http_request"),
        "observed_model": raw.get("observed_model"),
        "condition": condition,
        "trial": trial,
        "attempt": raw["attempt"],
        "outcome": raw["outcome"],
        "number": raw["number"],
        "response_text": raw.get("response") if valid else None,
        "started_at": raw.get("started_at"),
        "finished_at": raw["finished_at"],
        "elapsed_seconds": raw.get("elapsed_seconds"),
        "http_statuses": raw.get("http_statuses", []),
        "retry_after_seconds": raw.get("retry_after_seconds", 0),
        "session_id_sha256": text_hash(raw.get("session_id")),
        "response_id_sha256": text_hash(raw.get("response_id")),
        "raw_result_sha256": digest(folder / "result.json"),
        "raw_evidence_sha256": sorted(raw.get("evidence_sha256", {}).values()),
        "input_tokens": usage.get("input"),
        "output_tokens": usage.get("output"),
        "reasoning_tokens": usage.get("reasoning"),
        "pi_reported_cost": (usage.get("cost") or {}).get("total") or 0,
        "reported_cost_known": (usage.get("cost") or {}).get("total") is not None,
    }


def export(sources, destination, protocol, schedule, design, seed):
    with ExitStack() as stack:
        for root in sorted(set(sources.values())):
            stack.enter_context(batch.locked(root))
        _export(sources, destination, protocol, schedule, design, seed)


def verify_raw(root):
    """Use the verifier frozen with a run, including its original parser rules."""
    manifest = read(root / "manifest.json")
    for name, sha in manifest["source_sha256"].items():
        path = (root / "snapshot" / name).resolve()
        if (
            not path.is_relative_to((root / "snapshot").resolve())
            or digest(path) != sha
        ):
            raise ValueError("Frozen source changed before raw verification")
    done = subprocess.run(
        [sys.executable, str(root / "snapshot/verify.py"), str(root)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(done.stdout)


def _export(sources, destination, protocol, schedule, design, seed):
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(
            "Result directories are immutable; choose a new output path"
        )
    if protocol["prompts"].keys() != set(CONDITIONS):
        raise ValueError("Expected exactly baseline and meta prompts")
    mapping = {row["trial"]: row for row in schedule} if schedule else None
    manifests, checks, rows, provenance = {}, {}, [], {}
    planned = dict.fromkeys(CONDITIONS, 0)
    for label, root in sources.items():
        manifest = read(root / "manifest.json")
        expected_files = SOURCE_FILES | (
            {"cli_runners.py"}
            if manifest["config"].get("runner", "pi") != "pi"
            else set()
        )
        if set(manifest["source_sha256"]) != expected_files:
            raise ValueError("Unexpected source snapshot files")
        checks[label] = verify_raw(root)
        manifests[label] = manifest
        config = manifest["config"]
        if config["system_prompt"] != "You are a helpful assistant.":
            raise ValueError("Unexpected system preamble")
        for entry in manifest["schedule"]:
            condition = mapping[entry["trial"]]["condition"] if mapping else label
            expected = protocol["prompts"][condition]
            if entry.get("prompt", config["prompt"]) != expected:
                raise ValueError("Source prompt differs from the benchmark condition")
            planned[condition] += 1
        for path in sorted((root / "attempts").glob("*/*/result.json")):
            raw = read(path)
            condition = mapping[raw["trial"]]["condition"] if mapping else label
            trial = (
                mapping[raw["trial"]]["condition_trial"] if mapping else raw["trial"]
            )
            rows.append(normalized_record(raw, condition, trial, path.parent))
        status = read(root / "status.json")
        provenance[label] = {
            "runner": config.get("runner", "pi"),
            "runner_version": manifest.get("runner_version", manifest["pi_version"]),
            "evidence_level": "pi_http_request"
            if config.get("runner", "pi") == "pi"
            else "cli_transcript",
            "system_preamble_application": {
                "pi": "system override plus cwd",
                "claude": "system override; native CLI context may remain",
                "codex": "instructions file; native CLI context may remain",
                "grok": "system override; native CLI context may remain",
                "agy": "unsupported by CLI; native agent instructions remain",
            }[config.get("runner", "pi")],
            "created_at": manifest["created_at"],
            "state": status["state"],
            "elapsed_seconds": status["elapsed_seconds"],
            "raw_manifest_sha256": digest(root / "manifest.json"),
            "raw_verification": checks[label],
            "working_directory_sha256": text_hash(config["cwd"]),
            "working_directory": "/" if config["cwd"] == "/" else None,
            "source_sha256": manifest["source_sha256"],
            "runner_schema": manifest["schema_version"],
            "pi_version": manifest["pi_version"],
            "provider": config["provider"],
            "model": config["model"],
            "thinking": config["thinking"],
            "concurrency_ceiling": config["concurrency"],
            "control_settings": {
                k: config[k]
                for k in (
                    "max_attempts",
                    "timeout",
                    "base_backoff",
                    "max_backoff",
                    "recovery_successes",
                    "circuit_errors",
                    "max_seconds",
                    "max_cost",
                )
            },
            "pi_settings_sha256": text_hash(
                json.dumps(manifest["pi_settings"], sort_keys=True)
            ),
            "models_file_sha256": manifest.get("provider_metadata", {}).get(
                "models_file_sha256"
            ),
            "stored_auth_type": manifest.get("provider_metadata", {}).get(
                "stored_auth_type"
            ),
        }
    keys = (
        "runner",
        "runner_version",
        "provider",
        "model",
        "thinking",
        "concurrency_ceiling",
        "working_directory_sha256",
        "pi_version",
        "control_settings",
        "pi_settings_sha256",
        "models_file_sha256",
        "stored_auth_type",
    )
    descriptions = list(provenance.values())
    if any(any(p[k] != descriptions[0][k] for k in keys) for p in descriptions[1:]):
        raise ValueError("Condition configurations differ beyond the user prompt")
    if planned["baseline"] != planned["meta"]:
        raise ValueError("Both conditions must have the same planned trial count")
    rows.sort(key=lambda r: (r["condition"], r["trial"], r["attempt"]))
    summary = tally(rows, planned)
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
        for label, root in sources.items():
            folder = target / "source" / label
            folder.mkdir(parents=True)
            for name in manifests[label]["source_sha256"]:
                shutil.copyfile(root / "snapshot" / name, folder / name)
        metadata = {
            "schema": "random-number-public-result-v1",
            "exported_at": datetime.now(UTC).isoformat(),
            "protocol": protocol,
            "design": design,
            "order_seed": seed,
            "planned_per_condition": planned,
            "sources": provenance,
            "redactions": [
                "Historical private working directories are replaced by hashes. A neutral root directory is disclosed as / when used. Pi includes this directory in its system prompt.",
                "Session and response IDs are hashed. Credentials, endpoint headers, environment and free-form error text are not exported.",
                "Public rows are selected fields from verified native logs. Full raw logs remain local; raw file hashes are commitments, not downloadable transcripts.",
            ],
            "cost_basis": "Runner-reported API-equivalent usage metadata when available, not a verified bill. Legacy field name pi_reported_cost is retained for compatibility.",
            "model_identity": "Pi outgoing payload or native CLI model option checked; native reported model retained where exposed. Immutable backend snapshot is not independently established.",
        }
        metadata["files_sha256"] = {
            str(p.relative_to(target)): digest(p)
            for p in target.rglob("*")
            if p.is_file()
        }
        save(target / "manifest.json", metadata)
        verify_public(target)
        target.rename(destination)
    print(f"Verified public result: {destination}")


def verify_public(root):
    root = root.resolve()
    manifest = read(root / "manifest.json")
    if manifest.get("schema") in (
        "random-number-public-grid-v1",
        "random-number-public-grid-v2",
    ):
        import grid_results

        return grid_results.verify_public(root)
    required = {"attempts.jsonl", "summary.json"}
    for label, source in manifest["sources"].items():
        if label not in {"baseline", "meta", "shared"} or set(
            source["source_sha256"]
        ) != SOURCE_FILES | (
            {"cli_runners.py"} if source.get("runner", "pi") != "pi" else set()
        ):
            raise ValueError("Invalid source metadata")
        for name, sha in source["source_sha256"].items():
            relative = f"source/{label}/{name}"
            required.add(relative)
            if manifest["files_sha256"].get(relative) != sha:
                raise ValueError("Frozen source hash does not match provenance")
    if set(manifest["files_sha256"]) != required:
        raise ValueError("Missing or unexpected public evidence file")
    for name, sha in manifest["files_sha256"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root) or digest(path) != sha:
            raise ValueError("Public evidence hash mismatch")
    rows = [
        json.loads(line) for line in (root / "attempts.jsonl").read_text().splitlines()
    ]
    keys, sessions = set(), set()
    for row in rows:
        if (
            row["outcome"] not in OUTCOMES
            or type(row["attempt"]) is not int
            or row["attempt"] < 1
        ):
            raise ValueError("Invalid attempt metadata")
        key = (row["condition"], row["trial"], row["attempt"])
        if key in keys:
            raise ValueError("Duplicate physical attempt")
        keys.add(key)
        if (
            row["condition"] not in CONDITIONS
            or not 1
            <= row["trial"]
            <= manifest["planned_per_condition"][row["condition"]]
        ):
            raise ValueError("Trial outside frozen schedule")
        if row["outcome"] == "valid":
            if not isinstance(row["response_text"], str) or not re.fullmatch(
                r"(?:[1-9]|10)", row["response_text"].strip()
            ):
                raise ValueError("Invalid response counted")
            if int(row["response_text"].strip()) != row["number"]:
                raise ValueError("Parsed response differs from number")
            source = manifest["sources"].get(
                row["condition"], manifest["sources"].get("shared")
            )
            runner = row.get("runner", "pi")
            if runner != source.get("runner", "pi"):
                raise ValueError("Runner label differs from provenance")
            if runner == "pi":
                if row["http_statuses"] != [200]:
                    raise ValueError("Errored HTTP response counted")
            elif (
                runner not in {"claude", "codex", "grok", "agy"}
                or row.get("evidence_level") != "cli_transcript"
                or row["http_statuses"]
            ):
                raise ValueError("Invalid native CLI evidence profile")
            session = row["session_id_sha256"]
            if (not session and runner == "pi") or (session and session in sessions):
                raise ValueError("Reused native session")
            if session:
                sessions.add(session)
        elif row["number"] is not None:
            raise ValueError("Error attempt contributed a number")
    actual = tally(rows, manifest["planned_per_condition"])
    for condition in CONDITIONS:
        source = manifest["sources"].get(condition, manifest["sources"].get("shared"))
        verify.check_attempt_history(
            [r for r in rows if r["condition"] == condition],
            source["control_settings"]["max_attempts"],
        )
    if actual != read(root / "summary.json"):
        raise ValueError("Public tally does not match attempt records")
    return manifest, actual


def report(results, output, *, probes=False):
    lines = [
        "# Exploratory probes" if probes else "# Recorded results",
        "",
        "<!-- Generated by bench.py report"
        + (" --probes" if probes else "")
        + ". Do not edit manually. -->",
        "",
        *(
            [
                "Small exploratory rounds, kept separate from the 1,000-trial benchmark. See [design and interpretation](PROBE_NOTES.md).",
                "",
            ]
            if probes
            else []
        ),
        "Each row is one condition in one round. Rounds are kept separate.",
        "TV is distance from uniform: 0 is an exact uniform histogram; 0.90 is the maximum for ten categories.",
        "Dollar figures are runner-reported API-equivalent metadata, not verified charges; n/a means cost was not fully reported.",
        "",
        "| Round | Runner | Model | Thinking / effort | Condition | State | Accepted / planned | Excluded attempts | TV | Reported metadata (USD) |",
        "| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    details = []
    grid_folders = []
    histogram = [
        "",
        "## Answer counts",
        "",
        "| Round | Runner | Model | Thinking / effort | Prompt | "
        + " | ".join(map(str, range(1, 11)))
        + " |",
        "| --- | --- | --- | --- | --- | " + " | ".join(["---:"] * 10) + " |",
    ]
    for folder in sorted(results.iterdir() if results.exists() else []):
        if not (folder / "manifest.json").exists():
            continue
        if read(folder / "manifest.json").get("schema") in (
            "random-number-public-grid-v1",
            "random-number-public-grid-v2",
        ):
            grid_folders.append(folder)
            continue
        meta, summary = verify_public(folder)
        relative = Path(os.path.relpath(folder, output.parent)).as_posix()
        model = next(iter(meta["sources"].values()))["model"]
        for condition in CONDITIONS:
            row = summary[condition]
            source = meta["sources"].get(condition, meta["sources"].get("shared"))
            tv = (
                f"{row['tv_to_uniform']:.3f}"
                if row["tv_to_uniform"] is not None
                else "n/a"
            )
            lines.append(
                f"| [{folder.name}]({relative}/summary.json) | {source.get('runner', 'pi')} | {model} | {source['thinking']} | {condition} | {source['state']} | {row['accepted_trials']} / {row['planned_trials']} | {row['excluded_attempts']} | {tv} | "
                + (
                    "n/a"
                    if row.get("missing_cost_attempts", 0)
                    else f"{row['pi_reported_cost']:.4f}"
                )
                + " |"
            )
            runner = source.get("runner", "pi")
            counts = " | ".join(str(row["counts"][str(n)]) for n in range(1, 11))
            histogram.append(
                f"| [{folder.name}]({relative}/manifest.json) | {runner} | {model} | {source['thinking']} | {condition} | {counts} |"
            )
        details += [
            "",
            f"### {folder.name}",
            "",
            f"Design: {meta['design']}. [Protocol and provenance]({relative}/manifest.json).",
        ]
    if grid_folders:
        import grid_results

        sections = [
            "## Model and effort grids",
            "",
            "Each model/effort/prompt cell remains separate. Confidence intervals describe the answer proportions among accepted trials.",
            "",
        ]
        for folder in grid_folders:
            sections.extend(grid_results.report_lines(folder))
        lines = lines[:4] + sections + ["", "## Historical rounds", ""] + lines[4:]
    if not probes and (output.parent / "reports/comparison.png").is_file():
        lines[4:4] = [
            "[![Scannable matrix comparing model families, versions and efforts for both prompts](reports/comparison.png)](reports/comparison.png)",
            "",
            "Completed-grid snapshot: effective answer count from 1 (concentrated) to 10 (evenly spread). "
            "[Full-resolution image](reports/comparison.png) · "
            "[Interactive matrix](reports/comparison.html) (download and open) · "
            "[Scope and interpretation](VISUALIZATION.md). Historical rounds and excluded settings remain in the tables below.",
            "",
        ]
    output.write_text("\n".join(lines + histogram + details) + "\n")
