#!/usr/bin/env python3
"""Generate the shot stills for the kitty montage, one per shot plan.

Reads ``shots.json`` (the master plan) and renders every shot at native
portrait resolution.  The plan carries far more than the prompt: framing,
lens, light direction, grading, the motion prompt used later for
image-to-video, and the matching narration line.  Keeping all of it in one
file is what stops the individual stages from drifting apart.

Why portrait-native instead of square-then-crop
-----------------------------------------------
The earlier version rendered 1024x1024 squares and cropped them down to
9:16, which left only 432x768 usable pixels before the result was blown
up to 1080x1920.  Everything looked soft.  Rendering straight to portrait
gives the full width to the subject.

The anonymous free tier allows one image per 45 s per IP, so requests
are spaced out and existing files are skipped on a re-run.

Usage:
    python gen_stills.py --plan shots.json --outdir ./shots
    python gen_stills.py --plan shots.json --outdir ./shots --only s04
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".workbuddy" / "skills" / "video-mix" / "scripts"))

from gen_image import legacy_fetch  # noqa: E402

LEGACY_MODELS = ["flux", "sana", "zimage", "turbo"]
FREE_TIER_INTERVAL = 45.0


def build_prompt(shot: dict, anchor: str) -> str:
    """Assemble the final prompt from the plan's parts.

    The consistency anchors are appended verbatim to every shot so the
    same cat in the same room appears in all of them.
    """
    parts = [shot["subject"], shot.get("action", ""), shot.get("framing", ""),
             shot.get("light", ""), shot.get("grade", ""),
             shot.get("camera", ""), shot.get("negative", "")]
    prompt = ", ".join(p for p in parts if p)
    prompt += ", " + anchor
    return prompt


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the shot stills")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--only", nargs="*", help="render only these shot ids")
    ap.add_argument("--model", default="flux")
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=1820)
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text("utf-8"))
    anchor = plan["anchor"]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    todo = [s for s in plan["shots"]
            if not args.only or s["id"] in args.only]
    for i, shot in enumerate(todo):
        dest = outdir / f"{shot['id']}.jpg"
        if dest.exists() and dest.stat().st_size > 10000:
            print(f"[skip] {dest.name} already there")
            continue

        prompt = build_prompt(shot, anchor)
        # A fixed seed per shot keeps the character consistent between
        # reruns of the same plan.
        seed = shot.get("seed") or random.Random(shot["id"]).randrange(1, 10**8)
        print(f"[{i + 1}/{len(todo)}] {shot['id']} -> {dest.name}")
        print(f"       {prompt[:150]}...", flush=True)

        wait = FREE_TIER_INTERVAL * (0 if i == 0 else 1)
        if wait:
            time.sleep(wait)
        legacy_fetch(prompt, dest, args.model, args.width, args.height,
                     seed, True, False, tries=3)
        print(f"       ok {dest.stat().st_size // 1024}KB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
