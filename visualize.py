# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib==3.11.2"]
# ///
"""Build a portable, interactive view from verified completed public grids."""

import argparse
import base64
import hashlib
import json
from pathlib import Path

import matplotlib

import grid_results

ROOT = Path(__file__).resolve().parent
EFFORTS = {
    v: i
    for i, v in enumerate(("off", "minimal", "low", "medium", "high", "xhigh", "max"))
}


def build_rows(paths):
    registry = json.loads((ROOT / "visualization-models.json").read_text())
    rows = []
    identities = set()
    for path in paths:
        path = Path(path).resolve()
        manifest, summary = grid_results.verify_public(path)
        identity = manifest["grid"]["batch_manifest_sha256"]
        if identity in identities:
            raise ValueError("Duplicate immutable run")
        identities.add(identity)
        service = manifest["service"]
        if service["state"] != "complete":
            raise ValueError(f"Only completed grids are shown: {path.name}")
        for cell_info in manifest["grid"]["cells"]:
            cell_id = cell_info["id"]
            cell = summary["cells"][cell_id]
            model = cell["model"]
            meta = registry["models"].get(
                model,
                {"developer": "Unknown", "class": "Unclassified", "version_order": 0},
            )
            profile = cell.get("request_profile", {})
            strata = profile.get("stratum", "default_decoding")
            upstream = (
                profile.get("expected_provider")
                or ", ".join((service.get("openrouter_routing") or {}).get("only", []))
                or "CLI managed"
            )
            mode = profile.get(
                "mode",
                "not applicable" if model == "openai/gpt-4o" else "requested effort",
            )
            rows.append(
                {
                    "id": path.name + "/" + cell_id,
                    "run": path.name,
                    "cell": cell_id,
                    "developer": meta["developer"],
                    "class": meta["class"],
                    "model": model,
                    "version_order": meta["version_order"],
                    "effort": cell["thinking"],
                    "mode": mode,
                    "temperature": str(profile["temperature"])
                    if profile
                    else "default / unknown",
                    "release": profile.get("release_period", "not recorded"),
                    "runner": service["runner"],
                    "runner_version": service["runner_version"],
                    "provider": service["provider"],
                    "upstream": upstream,
                    "precision": profile.get("quantization", "not exposed"),
                    "stratum": strata,
                    "group": path.name
                    + " | "
                    + hashlib.sha256(
                        json.dumps(
                            {
                                "stratum": strata,
                                "provider": profile.get("provider"),
                                "precision": profile.get("quantization"),
                                "decoding": {
                                    k: profile.get(k)
                                    for k in (
                                        "top_p",
                                        "top_k",
                                        "frequency_penalty",
                                        "max_tokens",
                                    )
                                },
                            },
                            sort_keys=True,
                        ).encode()
                    ).hexdigest()[:12],
                    "protocol": manifest["grid"]["protocol"]["version"],
                    "profile": profile or None,
                    "conditions": cell["conditions"],
                    "evidence": f"https://github.com/bryantravissmith/random-number-bench/tree/main/results/{path.name}"
                    if path.parent.name == "results"
                    else None,
                }
            )
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate result/cell identity")
    return sorted(
        rows,
        key=lambda r: (
            r["group"],
            r["developer"],
            r["class"],
            r["version_order"],
            r["model"],
            EFFORTS.get(r["effort"], 99),
            r["temperature"],
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:
        raise ValueError("Do not run with Python -O: verification requires assertions")
    if any(args.out.resolve().is_relative_to(p.resolve()) for p in args.results):
        raise ValueError("Output must be outside immutable input exports")
    rows = build_rows(args.results)
    template = (ROOT / "visualization.html").read_text()
    palette = [
        matplotlib.colors.to_hex(matplotlib.colormaps["cividis"](i / 100))
        for i in range(101)
    ]
    data = json.dumps({"rows": rows, "palette": palette}, allow_nan=False).replace(
        "<", "\\u003c"
    )
    html = template.replace("/* BENCH_DATA */", data)
    # Embed the project's optional fonts when present; the file also works with
    # portable system/serif fallbacks and never fetches external scripts.
    for token, file in (
        ("GEIST_FONT", "Geist-Variable.woff2"),
        ("SERIF_FONT", "GeistSerif-Regular.woff2"),
    ):
        font = ROOT / ".lavish" / "assets" / file
        html = html.replace(
            token,
            "data:font/woff2;base64," + base64.b64encode(font.read_bytes()).decode()
            if font.exists()
            else "",
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html)
    print(f"Wrote {len(rows)} verified settings to {args.out}")


if __name__ == "__main__":
    main()
