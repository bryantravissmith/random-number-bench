# /// script
# requires-python = ">=3.12"
# dependencies = ["matplotlib==3.11.2"]
# ///
"""Build a portable, interactive view from verified completed public grids."""

import argparse
import base64
import hashlib
import json
import math
from pathlib import Path
import re

import matplotlib

import grid_results

ROOT = Path(__file__).resolve().parent
EFFORTS = {
    v: i
    for i, v in enumerate(("off", "minimal", "low", "medium", "high", "xhigh", "max"))
}


def diversity(condition):
    """Observed entropy and its effective count; no population correction/CI."""
    n = condition["accepted_trials"]
    if n == 0:
        return {"entropy_bits": None, "effective_answers": None}
    probabilities = [v / n for v in condition["counts"].values() if v]
    entropy = -sum(p * math.log2(p) for p in probabilities)
    return {"entropy_bits": entropy, "effective_answers": min(10.0, 2**entropy)}


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
                    "label": meta.get("label", model),
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
                    "metrics": {k: diversity(v) for k, v in cell["conditions"].items()},
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
            r["developer"],
            r["class"],
            r["group"],
            r["version_order"],
            r["model"],
            EFFORTS.get(r["effort"], 99),
            r["temperature"],
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", nargs="*", type=Path)
    parser.add_argument("--spec", type=Path, help="Checked-in presentation scope")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--check", action="store_true", help="Verify generated files match"
    )
    parser.add_argument(
        "--embed-fonts", action="store_true", help="Embed optional local fonts"
    )
    args = parser.parse_args()
    scope = {}
    if args.spec:
        if args.results:
            parser.error("Use either --spec or explicit results")
        scope = json.loads(args.spec.read_text())
        args.results = [ROOT / p for p in scope["exports"]]
    if not args.results:
        parser.error("Provide completed exports or --spec")
    if not __debug__:
        raise ValueError("Do not run with Python -O: verification requires assertions")
    if any(args.out.resolve().is_relative_to(p.resolve()) for p in args.results):
        raise ValueError("Output must be outside immutable input exports")
    rows = build_rows(args.results)
    excluded = scope.get("excluded_models", {})
    for row in rows:
        row["exclusion_reason"] = excluded.get(row["model"])
    template = (ROOT / "visualization.html").read_text()
    palette = [
        matplotlib.colors.to_hex(matplotlib.colormaps["cividis"](i / 100))
        for i in range(101)
    ]
    data = json.dumps(
        {"rows": rows, "palette": palette, "scope": scope}, allow_nan=False
    ).replace("<", "\\u003c")
    html = template.replace("/* BENCH_DATA */", data)
    html = html.replace("/* BENCH_SCRIPT */", (ROOT / "visualization.js").read_text())
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
            if args.embed_fonts and font.exists()
            else "",
        )
    html = re.sub(
        r"(?m)^[ \t]*@font-face\s*\{[^}]*src:\s*url\([\"']{2}\)[^}]*\}\n?",
        "",
        html,
    )
    sources = [
        ROOT / name
        for name in (
            "visualize.py",
            "visualization.html",
            "visualization.js",
            "visualization-models.json",
        )
    ]
    if args.spec:
        sources.append(args.spec.resolve())
    provenance = {
        "schema": "random-number-presentation-v1",
        "sources_sha256": {
            (
                str(p.relative_to(ROOT))
                if p.is_relative_to(ROOT)
                else "external-scope.json"
            ): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sources
        },
        "exports": [
            {
                "run": p.name,
                "manifest_sha256": hashlib.sha256(
                    (p / "manifest.json").read_bytes()
                ).hexdigest(),
            }
            for p in args.results
        ],
        "html_sha256": hashlib.sha256(html.encode()).hexdigest(),
        "embed_local_fonts": args.embed_fonts,
        "settings": len(rows),
        "default_visible_settings": sum(not r["exclusion_reason"] for r in rows),
    }
    manifest_path = args.out.with_name(args.out.stem + "-manifest.json")
    manifest_text = json.dumps(provenance, indent=2) + "\n"
    if args.check:
        if args.out.read_text() != html or manifest_path.read_text() != manifest_text:
            raise ValueError(
                "Presentation is stale; regenerate with the same command without --check"
            )
        print(f"Verified presentation and {len(rows)} settings: {args.out}")
        return
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html)
    manifest_path.write_text(manifest_text)
    print(f"Wrote {len(rows)} verified settings to {args.out}")


if __name__ == "__main__":
    main()
