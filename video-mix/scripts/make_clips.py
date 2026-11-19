#!/usr/bin/env python3
"""Turn the shot stills into clips with real motion.

Three tiers, tried in order per shot:

1. **pollinations** - ``gen.pollinations.ai/video``. Real text-to-video and
   image-to-video models (Veo 3.1, Seedance 2.0, Kling 3.0, Wan 2.x). Needs
   ``POLLINATIONS_KEY``. This is the only tier that gives cinematic motion.
2. **ltx** - the ``Lightricks/ltx-video-distilled`` Space. No key, but it
   spends HF ZeroGPU quota, which is capped per day per account.
3. **move** - local camera move over the still. Zero cost, zero quota, and
   the reason old renders looked like slideshows. Only a last resort.

Every shot gets a motion prompt written for the model, not for a camera.
The rule of thumb: describe what the subject does, then what the camera does,
and never mention a still frame, a photo, or a painting.

Usage:
    python make_clips.py --plan shots.json --stills shots_clean --out clips
    python make_clips.py --plan shots.json --stills shots_clean --out clips --only s04
    python make_clips.py --plan shots.json --stills shots_clean --out clips --backend move
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = Path.home() / ".workbuddy" / "skills" / "video-mix" / "scripts"

sys.path.insert(0, str(SCRIPTS))
from vmix_common import ffmpeg_bin  # noqa: E402

WIDTH, HEIGHT, FPS = 1080, 1920, 30
# The hosted video models accept a start frame at this size; the rest of the
# scaling happens during assembly.
VIDEO_W, VIDEO_H = 576, 1024

# Base style every clip inherits, so the eight shots look like one film.
STYLE = ("warm amber tungsten lighting, cosy living room, soft shadows, "
         "shallow depth of field, handheld documentary camera, natural animal motion")


def run(cmd: list[str], label: str) -> bool:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[clip] {label} FAILED")
        print((r.stderr or r.stdout)[-900:], file=sys.stderr)
        return False
    return True


def motion_prompt(shot: dict) -> str:
    """Build a motion prompt for a video model.

    The ``action`` field describes the pose; the ``camera`` field describes
    framing. For video models we want the movement, so ``motion_prompt`` (a
    new field) wins, then ``ltx_prompt``, then ``action``.
    """
    core = (shot.get("motion_prompt") or shot.get("ltx_prompt")
            or shot.get("action") or "")
    camera = shot.get("camera_motion") or shot.get("camera") or ""
    parts = [core.strip()]
    if camera and camera.lower() not in core.lower():
        parts.append(camera.strip())
    parts.append(STYLE)
    return ". ".join(p for p in parts if p)


def make_pollinations(still: Path, prompt: str, dst: Path, seconds: float,
                      model: str, width: int, height: int) -> bool:
    """Real image-to-video through the keyed Pollinations endpoint."""
    cmd = [sys.executable, str(SCRIPTS / "gen_video.py"),
           "--image", str(still), "--prompt", prompt,
           "--out", str(dst), "--duration", str(max(3, int(round(seconds)))),
           "--width", str(width), "--height", str(height),
           "--backend", "pollinations", "--model", model]
    return run(cmd, f"pollinations:{model} {dst.name}")


def make_ltx(still: Path, prompt: str, dst: Path, seconds: float,
             width: int, height: int) -> bool:
    """A real image-to-video clip from the LTX Space."""
    cmd = [sys.executable, str(SCRIPTS / "gen_video.py"),
           "--image", str(still), "--prompt", prompt,
           "--out", str(dst), "--duration", "2",
           "--width", str(width), "--height", str(height),
           "--backend", "ltx"]
    return run(cmd, f"ltx {dst.name}")


def make_move(still: Path, dst: Path, motion: str, seconds: float) -> bool:
    """A camera move over the still. Not real motion, but never fails."""
    cmd = [sys.executable, str(HERE / "camera_move.py"),
           "--image", str(still), "--out", str(dst),
           "--motion", motion, "--seconds", f"{seconds:.2f}",
           "--width", str(WIDTH), "--height", str(HEIGHT),
           "--fps", str(FPS)]
    return run(cmd, f"{motion} {dst.name}")


def has_pollinations_key() -> bool:
    env = os.environ.get("POLLINATIONS_KEY", "").strip()
    if env:
        return True
    return (Path.home() / ".workbuddy" / ".pollinations_key").exists()


def main() -> int:
    ap = argparse.ArgumentParser(description="Make the clips")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--stills", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "pollinations", "ltx", "move"],
                    help="auto tries pollinations, then ltx, then move")
    ap.add_argument("--model", default="bytedance/seedance-2.0-mini",
                    help="pollinations video model")
    ap.add_argument("--force", action="store_true",
                    help="re-render clips that already exist")
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text("utf-8"))
    stills = Path(args.stills)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    polli = has_pollinations_key()
    if args.backend == "pollinations" and not polli:
        print("[clip] POLLINATIONS_KEY is not set; falling back to the "
              "local camera move.", file=sys.stderr)
        args.backend = "auto"

    for shot in plan["shots"]:
        sid = shot["id"]
        if args.only and sid not in args.only:
            continue
        still = stills / f"{sid}.jpg"
        if not still.exists():
            print(f"[clip] missing still {still}", file=sys.stderr)
            continue
        dst = outdir / f"{sid}.mp4"
        if dst.exists() and dst.stat().st_size > 20000 and not args.force:
            print(f"[clip] skip {dst.name} (exists)")
            continue

        prompt = motion_prompt(shot)
        ok = False

        if args.backend in ("auto", "pollinations") and polli:
            print(f"[clip] {sid}: real motion via {args.model}")
            ok = make_pollinations(still, prompt, dst, shot["duration"],
                                   args.model, VIDEO_W, VIDEO_H)
        if not ok and args.backend in ("auto", "ltx"):
            print(f"[clip] {sid}: real motion via LTX")
            ok = make_ltx(still, prompt, dst, shot["duration"], VIDEO_W, VIDEO_H)
        if not ok and args.backend in ("auto", "move"):
            print(f"[clip] {sid}: camera move ({shot.get('motion', 'push-in')}) "
                  "- NOT real motion")
            ok = make_move(still, dst, shot.get("motion", "push-in"),
                           shot["duration"])

        if ok:
            print(f"[clip]    ok {dst.name} ({dst.stat().st_size // 1024} KB)")
        else:
            print(f"[clip]    {sid} could not be produced", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
