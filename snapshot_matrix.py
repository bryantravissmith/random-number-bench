# /// script
# requires-python = ">=3.12"
# dependencies = ["playwright==1.64.1"]
# ///
"""Capture the verified HTML matrix as a high-resolution, offline PNG."""

import argparse
import hashlib
import json
from pathlib import Path
import struct


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, default=Path("reports/comparison.html"))
    parser.add_argument("--out", type=Path, default=Path("reports/comparison.png"))
    parser.add_argument("--browser", choices=("chromium", "chrome"), default="chromium")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check source/image hashes without a browser",
    )
    args = parser.parse_args()
    presentation = args.html.with_name(args.html.stem + "-manifest.json")
    if json.loads(presentation.read_text())["html_sha256"] != sha(args.html):
        raise ValueError(
            "HTML differs from its verified presentation manifest; regenerate it first"
        )
    sources = {
        "html_sha256": sha(args.html),
        "presentation_manifest_sha256": sha(presentation),
        "renderer_sha256": sha(Path(__file__)),
    }
    provenance = args.out.with_name(args.out.stem + "-image.json")
    if args.check:
        saved = json.loads(provenance.read_text())
        if saved["sources"] != sources or saved["png_sha256"] != sha(args.out):
            raise ValueError(
                "Matrix image is stale or modified; regenerate with snapshot_matrix.py"
            )
        print(f"Verified image and source hashes: {args.out}")
        return

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, channel="chrome" if args.browser == "chrome" else None
        )
        page = browser.new_page(
            viewport={"width": 1600, "height": 1000},
            device_scale_factor=2,
            color_scheme="light",
            locale="en-US",
        )
        # All fonts, data and scripts are local; do not fetch anything remotely.
        page.route("http://**/*", lambda route: route.abort())
        page.route("https://**/*", lambda route: route.abort())
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.html.resolve().as_uri(), wait_until="load")
        page.wait_for_selector("#matrix .score")
        page.evaluate("document.fonts.ready")
        # Match the approved matrix, omitting controls that cannot work in a PNG.
        page.add_style_tag(
            content="#download, .footer details { display:none!important; }"
        )
        page.evaluate("""() => {
            const note = document.querySelector('#sample-size').parentElement;
            note.textContent = document.querySelector('#sample-size').textContent
                + ' · counts + 95% CIs in the linked interactive matrix';
            for (const paragraph of document.querySelectorAll('.footer p')) {
                paragraph.innerHTML = paragraph.innerHTML.replace(
                    'click for denominators', 'see the interactive matrix for denominators'
                );
            }
        }""")
        if errors:
            raise ValueError(f"Matrix failed to render: {errors}")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(args.out), full_page=True, animations="disabled")
        version = browser.version
        browser.close()

    width, height = struct.unpack(">II", args.out.read_bytes()[16:24])
    provenance.write_text(
        json.dumps(
            {
                "schema": "random-number-matrix-image-v1",
                "sources": sources,
                "png_sha256": sha(args.out),
                "browser_channel": args.browser,
                "browser_version": version,
                "playwright_version": "1.64.1",
                "viewport_width": 1600,
                "device_scale_factor": 2,
                "image_pixels": [width, height],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Wrote {width} × {height} matrix image: {args.out}")


if __name__ == "__main__":
    main()
