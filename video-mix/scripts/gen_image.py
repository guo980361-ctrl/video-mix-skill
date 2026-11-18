#!/usr/bin/env python3
"""Text-to-image with two interchangeable backends.

``--backend auto`` (the default) picks the best available option:

* **keyed**  - ``gen.pollinations.ai``, requires a free Pollinations API key.
  No watermark, no 45-second rate limit, and image generation is cheap
  against the account balance. This is the long-term option.
* **anon**   - ``image.pollinations.ai``, no key at all. Works immediately
  but every render carries a ``pollinations.ai`` watermark and calls are
  limited to roughly one image per 45 seconds per IP.

A content-addressed cache sits in front of both. The cache key is derived
from the prompt and the generation parameters, so re-running a shot list
after a crash costs nothing.

Usage:
    python gen_image.py --prompt "a cat astronaut" --out cat.jpg
    python gen_image.py --prompt "..." --out b.jpg --backend keyed --key pk_xxx
    python gen_image.py --prompt "..." --out c.jpg --no-cache
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pollinations_client import (  # noqa: E402
    IMAGE_MODELS,
    PollinationsError,
    find_key,
    gen_image as polli_gen_image,
)
from vmix_common import install_deps, out_path  # noqa: E402

LEGACY_BASE = "https://image.pollinations.ai/prompt/"
LEGACY_MODELS = ["flux", "sana", "zimage", "turbo"]
FREE_TIER_INTERVAL = 45.0
CACHE_DIR = Path.home() / ".workbuddy" / "cache" / "video-mix"


def cache_path(prompt: str, width: int, height: int, model: str | None,
               seed: int | None, backend: str) -> Path:
    """Content-addressed cache location for a generation request."""
    payload = json.dumps(
        {"p": prompt, "w": width, "h": height, "m": model,
         "s": seed, "b": backend},
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / backend / f"{digest}.jpg"


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


def legacy_fetch(prompt: str, out: Path, model: str | None, width: int,
                 height: int, seed: int | None, nologo: bool,
                 enhance: bool, tries: int = 3) -> Path:
    """Anonymous, watermarked, rate-limited endpoint."""
    import requests

    q = {"width": width, "height": height, "nologo": "true" if nologo else "false"}
    if model:
        q["model"] = model
    if seed is not None:
        q["seed"] = seed
    if enhance:
        q["enhance"] = "true"
    url = LEGACY_BASE + urllib.parse.quote(prompt, safe="") + "?" + urllib.parse.urlencode(q)

    out.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, timeout=180, stream=True)
            if r.status_code != 200:
                last = RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code == 402:
                    wait = _sleep_for_402(r.text)
                    print(f"[t2i] free-tier rate limit, waiting {wait:.0f}s")
                    time.sleep(wait)
                    continue
                if r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(4 * attempt)
                    continue
                raise last
            ctype = r.headers.get("content-type", "")
            if "image" not in ctype:
                last = RuntimeError(f"not an image: {r.text[:200]}")
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
    ap = argparse.ArgumentParser(description="Text-to-image (keyed or anonymous)")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True, help="output image path")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "keyed", "anon"],
                    help="auto uses a key when one is configured")
    ap.add_argument("--model", default=None,
                    help=f"keyed: one of {IMAGE_MODELS}; anon: {LEGACY_MODELS}")
    ap.add_argument("--key", help="Pollinations API key (overrides env/file)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--nologo", action="store_true", default=True)
    ap.add_argument("--logo", dest="nologo", action="store_false")
    ap.add_argument("--enhance", action="store_true")
    ap.add_argument("--no-cache", action="store_true",
                    help="skip the content-addressed cache")
    args = ap.parse_args()

    install_deps(quiet=True)
    out = out_path(args.out)

    key = find_key(args.key)
    if args.backend == "auto":
        backend = "keyed" if key else "anon"
    else:
        backend = args.backend

    # The cache key must reflect the backend, otherwise an anonymous render
    # would be served for a keyed request.
    cache_file = cache_path(args.prompt, args.width, args.height,
                            args.model, args.seed, backend)
    if not args.no_cache and cache_file.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(cache_file.read_bytes())
        print(f"[t2i] cache hit -> {out}")
        print(str(out))
        return 0

    t0 = time.time()
    print(f"[t2i] backend={backend} {args.prompt[:70]}")

    if backend == "keyed":
        if not key:
            print("[t2i] no Pollinations key found.\n"
                  "  Free signup: https://enter.pollinations.ai/keys\n"
                  "  Then: export POLLINATIONS_KEY=pk_xxx", file=sys.stderr)
            return 2
        model = args.model or IMAGE_MODELS[0]
        try:
            polli_gen_image(args.prompt, out, model=model,
                            width=args.width, height=args.height,
                            seed=args.seed, key=key, nologo=args.nologo)
        except PollinationsError as exc:
            print(f"[t2i] {exc}", file=sys.stderr)
            return 2
    else:
        model = args.model if args.model in LEGACY_MODELS else None
        try:
            legacy_fetch(args.prompt, out, model, args.width, args.height,
                         args.seed, args.nologo, args.enhance)
        except Exception as exc:
            print(f"[t2i] {exc}", file=sys.stderr)
            return 2

    if not args.no_cache:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(out.read_bytes())

    size = out.stat().st_size / 1024
    print(f"[t2i] OK -> {out}  ({size:.0f} KB, {time.time() - t0:.1f}s)")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
