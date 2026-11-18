#!/usr/bin/env python3
"""Batch-generate a shot list of images for a montage.

Reads a JSON or plain-text shot list and produces one image per shot.

Plain text format (one shot per line):
    A young woman walking in neon rain | cyberpunk street, neon reflections

JSON format:
    [{"prompt": "...", "name": "shot01"}, ...]

Cost control:
  * Existing files are skipped, so re-running never pays twice.
  * A content-addressed cache in ~/.workbuddy/cache/video-mix serves
    repeated prompts without touching the API.
  * The anonymous free tier is limited to one image per 45 seconds per IP,
    so requests are spaced out. The keyed backend has no such limit.

Usage:
    python gen_shots.py --shots shots.txt --outdir ./shots
    python gen_shots.py --shots shots.json --outdir ./shots --backend keyed
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gen_image import cache_path, legacy_fetch  # noqa: E402
from pollinations_client import (  # noqa: E402
    IMAGE_MODELS,
    PollinationsError,
    find_key,
    gen_image as polli_gen_image,
)
from vmix_common import install_deps, out_path, slugify  # noqa: E402

LEGACY_MODELS = ["flux", "sana", "zimage", "turbo"]


def parse_shots(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8").strip()
    if raw.startswith("["):
        data = json.loads(raw)
        return [{"prompt": d["prompt"], "name": d.get("name")} for d in data]
    shots = []
    for i, line in enumerate(raw.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|", 1)]
        shots.append({
            "prompt": parts[0],
            "name": slugify(parts[1] if len(parts) > 1 else f"shot{i:02d}"),
        })
    return shots


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch image generation from a shot list")
    ap.add_argument("--shots", required=True)
    ap.add_argument("--outdir", default="shots")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--backend", default="auto", choices=["auto", "keyed", "anon"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--key", help="Pollinations API key (overrides env/file)")
    ap.add_argument("--delay", type=float, default=46.0,
                    help="seconds between requests; only needed for the "
                         "anonymous tier (1 image / 45s / IP)")
    ap.add_argument("--no-delay", action="store_true",
                    help="ignore the anonymous rate limit (expect 402 errors)")
    args = ap.parse_args()

    install_deps(quiet=True)
    shots = parse_shots(Path(args.shots).expanduser().resolve())
    outdir = out_path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    key = find_key(args.key)
    if args.backend == "auto":
        backend = "keyed" if key else "anon"
    else:
        backend = args.backend
    if backend == "keyed" and not key:
        print("[shots] --backend keyed requires a Pollinations key.\n"
              "  Free signup: https://enter.pollinations.ai/keys", file=sys.stderr)
        return 2

    print(f"[shots] {len(shots)} shots -> {outdir}  (backend={backend})")
    ok, fail = [], []
    for i, s in enumerate(shots, 1):
        name = s.get("name") or slugify(s["prompt"])
        out = outdir / f"{name}.jpg"
        if out.exists():
            print(f"[shots] {i}/{len(shots)} skip {name} (exists)")
            ok.append(str(out))
            continue

        cache_file = cache_path(s["prompt"], args.width, args.height,
                                args.model, None, backend)
        if cache_file.exists():
            out.write_bytes(cache_file.read_bytes())
            print(f"[shots] {i}/{len(shots)} OK {name} (cache hit)")
            ok.append(str(out))
            continue

        try:
            if backend == "keyed":
                model = args.model or IMAGE_MODELS[0]
                polli_gen_image(s["prompt"], out, model=model,
                                width=args.width, height=args.height, key=key)
            else:
                model = args.model if args.model in LEGACY_MODELS else None
                legacy_fetch(s["prompt"], out, model, args.width, args.height,
                             None, True, False)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_bytes(out.read_bytes())
            print(f"[shots] {i}/{len(shots)} OK {name}")
            ok.append(str(out))
        except (PollinationsError, Exception) as exc:
            print(f"[shots] {i}/{len(shots)} FAIL {name}: {str(exc)[:160]}")
            fail.append(name)

        # The keyed backend has no per-IP rate limit, so the delay is only
        # applied for the anonymous tier.
        if (backend == "anon" and i < len(shots) and args.delay
                and not args.no_delay):
            time.sleep(args.delay)

    print(f"[shots] done: {len(ok)} ok, {len(fail)} failed")
    if fail:
        print("[shots] failed:", ", ".join(fail))
    print(str(outdir))
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
