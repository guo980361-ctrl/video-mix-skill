#!/usr/bin/env python3
"""Batch-generate a shot list of images for a montage.

Reads a JSON or plain-text shot list and produces one image per shot.

Plain text format (one shot per line):
    A young woman walking in neon rain | cyberpunk street, neon reflections

JSON format:
    [{"prompt": "...", "name": "shot01"}, ...]

Usage:
    python gen_shots.py --shots shots.txt --outdir ./shots
    python gen_shots.py --shots shots.json --outdir ./shots --width 1024 --height 576
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vmix_common import install_deps, out_path, slugify  # noqa: E402
from gen_image import fetch, build_url  # noqa: E402


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
    ap.add_argument("--model", default=None)
    ap.add_argument("--delay", type=float, default=46.0,
                    help="seconds between requests; the free tier allows "
                         "only one image per 45s per IP")
    ap.add_argument("--no-delay", action="store_true",
                    help="ignore the free-tier rate limit (expect 402 errors)")
    args = ap.parse_args()

    install_deps(quiet=True)
    shots = parse_shots(Path(args.shots).expanduser().resolve())
    outdir = out_path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print(f"[shots] {len(shots)} shots -> {outdir}")
    ok, fail = [], []
    for i, s in enumerate(shots, 1):
        name = s.get("name") or slugify(s["prompt"])
        out = outdir / f"{name}.jpg"
        if out.exists():
            print(f"[shots] {i}/{len(shots)} skip {name} (exists)")
            ok.append(str(out))
            continue
        url = build_url(s["prompt"], args.width, args.height, args.model,
                        None, True, False)
        try:
            fetch(url, out, tries=3)
            print(f"[shots] {i}/{len(shots)} OK {name}")
            ok.append(str(out))
        except Exception as exc:
            print(f"[shots] {i}/{len(shots)} FAIL {name}: {str(exc)[:160]}")
            fail.append(name)
        if i < len(shots) and args.delay and not args.no_delay:
            time.sleep(args.delay)

    print(f"[shots] done: {len(ok)} ok, {len(fail)} failed")
    if fail:
        print("[shots] failed:", ", ".join(fail))
    print(str(outdir))
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
