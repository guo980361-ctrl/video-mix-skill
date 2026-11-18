#!/usr/bin/env python3
"""Text-to-image via the free, no-key Pollinations endpoint.

Verified 2026-11-18: GET https://image.pollinations.ai/prompt/<prompt>
returns HTTP 200 with a JPEG in ~1.6s at 512x512, no API key required.

Usage:
    python gen_image.py --prompt "a cat astronaut" --out cat.jpg
    python gen_image.py --prompt "..." --out a.png --width 1024 --height 1024
    python gen_image.py --prompt "..." --out b.jpg --model flux
    python gen_image.py --prompt "..." --out seed42.jpg --seed 42
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vmix_common import install_deps, out_path  # noqa: E402

BASE = "https://image.pollinations.ai/prompt/"

# Models observed to work on the free tier.
MODELS = ["flux", "sana", "zimage", "turbo"]

# The anonymous free tier is hard rate-limited to one image per 45s per IP.
# Hitting it returns HTTP 402 with error code FREE_LIMIT.
FREE_TIER_INTERVAL = 45.0


def build_url(prompt: str, width: int, height: int, model: str | None,
              seed: int | None, nologo: bool, enhance: bool) -> str:
    q = {"width": width, "height": height, "nologo": "true" if nologo else "false"}
    if model:
        q["model"] = model
    if seed is not None:
        q["seed"] = seed
    if enhance:
        q["enhance"] = "true"
    return BASE + urllib.parse.quote(prompt, safe="") + "?" + urllib.parse.urlencode(q)


def _sleep_for_402(body: str) -> float:
    """Parse the server's retry hint out of a FREE_LIMIT error body."""
    try:
        data = json.loads(body)
        msg = (data.get("error") or {}).get("message", "")
        m = re.search(r"Retry after (\d+)s", msg)
        if m:
            return max(float(m.group(1)) + 2.0, 5.0)
    except Exception:
        pass
    return FREE_TIER_INTERVAL + 5.0


def fetch(url: str, out: Path, tries: int = 3) -> Path:
    import requests

    out.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, timeout=180, stream=True)
            if r.status_code != 200:
                last = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code == 402:
                    wait = _sleep_for_402(r.text)
                    print(f"[t2i] free-tier rate limit hit, waiting {wait:.0f}s")
                    time.sleep(wait)
                    continue
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(4 * attempt)
                    continue
                raise last
            ctype = r.headers.get("content-type", "")
            if "image" not in ctype:
                last = RuntimeError(f"not an image, content-type={ctype}: {r.text[:200]}")
                time.sleep(4 * attempt)
                continue
            tmp = out.with_suffix(out.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)
            tmp.replace(out)
            return out
        except Exception as exc:
            last = exc
            if attempt < tries:
                time.sleep(4 * attempt)
    raise RuntimeError(f"image generation failed: {last}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Free text-to-image (no API key)")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True, help="output image path")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--model", default=None, choices=MODELS + [None])
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--nologo", action="store_true", default=True)
    ap.add_argument("--logo", dest="nologo", action="store_false")
    ap.add_argument("--enhance", action="store_true")
    args = ap.parse_args()

    install_deps(quiet=True)
    out = out_path(args.out)

    url = build_url(args.prompt, args.width, args.height, args.model,
                    args.seed, args.nologo, args.enhance)
    t0 = time.time()
    print(f"[t2i] {args.prompt[:70]}")
    fetch(url, out)
    size = out.stat().st_size / 1024
    print(f"[t2i] OK -> {out}  ({size:.0f} KB, {time.time() - t0:.1f}s)")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
