# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Capture public OpenRouter model metadata without credentials or inference."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

URL = "https://openrouter.ai/api/v1/models"
FIELDS = (
    "id",
    "name",
    "canonical_slug",
    "hugging_face_id",
    "created",
    "architecture",
    "context_length",
    "supported_parameters",
    "reasoning",
    "default_parameters",
    "pricing",
    "expiration_date",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--family",
        action="append",
        required=True,
        help="Exact model-id prefix, e.g. qwen; repeat for more families",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Catalog snapshots are immutable; choose a new output path")
    families = sorted(set(f.strip("/") for f in args.family))
    if any(not f or "/" in f for f in families):
        parser.error("Family must be a nonempty top-level model-id prefix")
    request = Request(URL, headers={"User-Agent": "random-number-bench/catalog"})
    with urlopen(request, timeout=30) as response:
        raw = response.read()
    models = [
        {k: model.get(k) for k in FIELDS}
        for model in json.loads(raw)["data"]
        if model["id"].split("/")[0] in families
    ]
    missing = set(families) - {m["id"].split("/")[0] for m in models}
    if missing:
        parser.error(f"No catalog models for: {', '.join(sorted(missing))}")
    snapshot = {
        "schema": "openrouter-catalog-snapshot-v1",
        "generated": True,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": URL,
        "source_response_sha256": hashlib.sha256(raw).hexdigest(),
        "families": families,
        "date_note": "created is OpenRouter catalog metadata, not a verified original release date",
        "availability_note": "Catalog listing does not establish account access, endpoint availability or immutable backend identity",
        "models": sorted(models, key=lambda m: m["id"]),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream:
        stream.write(json.dumps(snapshot, indent=2, allow_nan=False) + "\n")
    print(f"Captured {len(models)} model entries in {args.out}; no inference calls")


if __name__ == "__main__":
    main()
