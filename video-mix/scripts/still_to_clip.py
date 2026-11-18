#!/usr/bin/env python3
"""Turn a still image into a moving clip (Ken Burns effect).

This is the fallback when the GPU quota is exhausted: it needs no cloud
service at all, so the montage pipeline can always keep running.

Usage:
    python still_to_clip.py --image shots/a.jpg --out clips/a.mp4 --seconds 4
    python still_to_clip.py --image shots/a.jpg --out clips/a.mp4 --motion zoom-in
    python still_to_clip.py --image shots/a.jpg --out clips/a.mp4 --width 1080 --height 1920
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vmix_common import ffmpeg_bin, install_deps, out_path  # noqa: E402

MOTIONS = {
    # zoompan filter expression per motion type
    "zoom-in": "min(zoom+0.0012,1.18)",
    "zoom-out": "if(lte(zoom,1.0),1.18,max(1.001,zoom-0.0012))",
    "zoom-in-left": "min(zoom+0.0012,1.18)",
    "zoom-in-right": "min(zoom+0.0012,1.18)",
    "still": "1.0",
}


def build_filter(motion: str, w: int, h: int, fps: int) -> str:
    """Ken Burns via zoompan.

    The source is first cropped to the target aspect ratio (so the image
    fills the frame instead of being letterboxed), then zoompan adds the
    movement.
    """
    crop = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h}")
    expr = MOTIONS.get(motion, MOTIONS["zoom-in"])
    if motion == "zoom-in-left":
        x = "x/iw*(iw-iw/zoom)"
        y = "iw/2-(iw/zoom/2)"
    elif motion == "zoom-in-right":
        x = "(iw-iw/zoom)-(x/iw*(iw-iw/zoom))"
        y = "iw/2-(iw/zoom/2)"
    else:
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    zoompan = (f"zoompan=z='{expr}':x='{x}':y='{y}':"
               f"d=1:s={w}x{h}:fps={fps}")
    return f"{crop},{zoompan},format=yuv420p"


def main() -> int:
    ap = argparse.ArgumentParser(description="Still image -> moving clip")
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--fps", type=int, default=25)
    ap.add_argument("--motion", default="zoom-in", choices=list(MOTIONS))
    ap.add_argument("--audio", help="optional audio track to lay under it")
    args = ap.parse_args()

    install_deps(quiet=True)
    image = Path(args.image).expanduser().resolve()
    if not image.exists():
        print(f"[still] image not found: {image}", file=sys.stderr)
        return 1
    out = out_path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    vf = build_filter(args.motion, args.width, args.height, args.fps)
    frames = max(1, int(args.seconds * args.fps))

    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
           "-loop", "1", "-i", str(image)]
    if args.audio:
        cmd += ["-i", str(Path(args.audio).expanduser().resolve())]
    else:
        # silent audio source must be declared as an input, before the
        # output options
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += ["-vf", vf, "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p"]
    if args.audio:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-shortest"]
    else:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-shortest"]
    cmd += [str(out)]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-3000:], file=sys.stderr)
        return 2

    kb = out.stat().st_size / 1024
    print(f"[still] OK -> {out}  ({kb:.0f} KB, {args.seconds:.1f}s, "
          f"{args.motion})")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
