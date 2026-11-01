# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Archive a stopped analytics run and prepare blind grades without resuming it.

This is a post-hoc export path. It imports the original frozen verifier in a
fresh process; it never modifies the inference snapshot or treats it as complete.
"""

import argparse
from collections import Counter
import hashlib
from pathlib import Path
import runpy
import secrets
import shutil
import sys
import tempfile


def export(root, out):
    if not __debug__:
        raise ValueError("Do not disable assertions when verifying native evidence")
    sys.dont_write_bytecode = True
    frozen = runpy.run_path(str(root / "snapshot/probe.py"))
    batch = frozen["batch"]
    with batch.locked(root):
        status = batch.read(root / "status.json")
        if status["state"] != "stopped":
            raise ValueError("Partial grading export requires a stopped run")
        if out.exists() or any(
            (root / name).exists()
            for name in ("blind-key.json", "blind-responses.json")
        ):
            raise ValueError(
                "Export or blind assignment already exists; do not reshuffle"
            )
        manifest, study, rows = frozen["verified_rows"](root)
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
        accepted = [r for r in public if r["outcome"] == "valid"]
        if not accepted:
            raise ValueError("No valid responses to grade")
        secrets.SystemRandom().shuffle(accepted)
        blind, key = [], {}
        for row in accepted:
            label = secrets.token_hex(12)
            blind.append({"id": label, "case": row["case"], "answer": row["answer"]})
            key[label] = {"trial": row["trial"], "attempt": row["attempt"]}
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".partial-export-", dir=out.parent
        ) as temporary:
            staging = Path(temporary) / "package"
            staging.mkdir()
            batch.save(staging / "attempts.json", public)
            batch.save(staging / "study.json", study)
            batch.save(staging / "schedule.json", manifest["schedule"])
            shutil.copytree(root / "snapshot", staging / "source")
            # The frozen verifier import can create Python caches; those are not evidence.
            for cache in staging.rglob("__pycache__"):
                shutil.rmtree(cache)
            (staging / "posthoc").mkdir()
            shutil.copyfile(__file__, staging / "posthoc/export_partial.py")
            batch.save(
                staging / "manifest.json",
                {
                    "schema": "analytics-coverage-public-v1",
                    "state": "stopped",
                    "partial": True,
                    "stop_reason": status["controller"]["stopped"],
                    "finished_at": status.get("finished_at"),
                    "elapsed_seconds": status["elapsed_seconds"],
                    "analysis_note": "Post-hoc grading of a stopped run requested by the user. Unfinished trials are missing, not failed or unuseful answers. No inference was resumed or replaced.",
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
                    "outcomes": dict(Counter(r["outcome"] for r in public)),
                    "files_sha256": {
                        str(p.relative_to(staging)): batch.digest(p)
                        for p in staging.rglob("*")
                        if p.is_file()
                    },
                },
            )
            staging.rename(out)
        batch.save(root / "blind-key.json", key)
        batch.save(root / "blind-responses.json", blind)
        print(
            f"Archived {len(public)} physical attempts; prepared {len(blind)} blind responses. State remains stopped."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    export(args.raw.resolve(), args.out.resolve())


if __name__ == "__main__":
    main()
