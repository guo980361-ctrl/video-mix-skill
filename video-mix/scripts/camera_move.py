#!/usr/bin/env python3
"""Camera moves that make a still read as filmed footage.

The stock Ken Burns effect in ``still_to_clip.py`` zooms the whole frame
at one speed.  It is recognisable as fake within a second or two: every
pixel moves together, so the image feels like a slide show.

What real camera movement looks like is *parallax* -- when the camera
moves, near and far parts of the scene shift by different amounts.  That
single effect is most of what separates "a photo" from "a shot".

This module approximates it without any depth estimation: the lower band
of the frame (usually the nearer subject) is shifted further than the
upper band (the room behind), and the two are joined with a soft
gradient so there is no visible seam.  It also adds the secondary
movements that make footage feel alive:

  * breathing  -- a very slow sinusoidal scale, the way a handheld
                  operator's breathing shows up in a long take
  * sway       -- a low-frequency horizontal drift
  * focus pull -- a slow resolve from soft to sharp
  * grain      -- moving film grain, so the surface is never static

Motions available:
  push-in, pull-out, pan-left, pan-right, parallax-left, parallax-right,
  crane-up, breath, still

Usage:
    python camera_move.py --image shot.jpg --out clip.mp4 --motion push-in --seconds 4
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".workbuddy" / "skills" / "video-mix" / "scripts"))
from vmix_common import ffmpeg_bin  # noqa: E402

# Per-motion parameters:
#   zoom_end   -- scale at the last frame
#   pan_end    -- horizontal drift as a fraction of frame width
#   rise_end   -- vertical drift as a fraction of frame height
#   parallax   -- 0 disables the layered move, 1 is full strength
#   breathe    -- amplitude of the breathing scale, in percent
#   sway       -- amplitude of the horizontal sway, in percent
MOTIONS = {
    "push-in":        dict(zoom_end=1.14, pan_end=0.0,  rise_end=0.0,  parallax=0.0,  breathe=0.6, sway=0.15),
    "pull-out":       dict(zoom_end=0.90, pan_end=0.0,  rise_end=0.0,  parallax=0.0,  breathe=0.6, sway=0.15),
    "pan-left":       dict(zoom_end=1.08, pan_end=-0.09,rise_end=0.0,  parallax=0.45, breathe=0.5, sway=0.18),
    "pan-right":      dict(zoom_end=1.08, pan_end=0.09, rise_end=0.0,  parallax=0.45, breathe=0.5, sway=0.18),
    "parallax-left":  dict(zoom_end=1.10, pan_end=-0.06,rise_end=0.0,  parallax=0.9,  breathe=0.5, sway=0.18),
    "parallax-right": dict(zoom_end=1.10, pan_end=0.06, rise_end=0.0,  parallax=0.9,  breathe=0.5, sway=0.18),
    "crane-up":       dict(zoom_end=1.12, pan_end=0.0,  rise_end=0.05, parallax=0.55, breathe=0.5, sway=0.12),
    "breath":         dict(zoom_end=1.05, pan_end=0.0,  rise_end=0.0,  parallax=0.0,  breathe=1.4, sway=0.1),
    "still":          dict(zoom_end=1.02, pan_end=0.0,  rise_end=0.0,  parallax=0.0,  breathe=0.3, sway=0.08),
}


def build_graph(motion: str, w: int, h: int, fps: int, seconds: float) -> str:
    """Return the ffmpeg filter graph for one camera move."""
    p = MOTIONS.get(motion, MOTIONS["push-in"])
    frames = max(1, int(round(seconds * fps)))

    # Oversample the source so the move has room to travel without
    # exposing the edges of the frame.
    over = 1.32
    sw, sh = int(w * over), int(h * over)
    pad = int(w * 0.12)

    # zoompan interpolates the scale across the clip: 1 -> zoom_end.
    z_expr = f"(1+({p['zoom_end'] - 1:.4f})*on/{frames})"
    # Breathing: a slow sine across the whole clip.
    breathe = f"(1+{p['breathe'] / 100:.5f}*sin(2*PI*on/{frames}))"
    # Sway: half as many cycles, offset in phase.
    sway = f"({p['sway'] / 100:.5f}*sin(2*PI*on/{max(frames * 2, 2)}+1.2))"
    z_full = f"{z_expr}*{breathe}"

    # The pan is expressed as a fraction of the scaled frame and applied
    # on top of the zoom centring.
    pan_px = p["pan_end"] * sw
    rise_px = p["rise_end"] * sh
    x_expr = (f"(iw-iw/zoom)/2+({pan_px:.1f}+{sway}*iw)*on/{frames}")
    y_expr = f"(ih-ih/zoom)/2+({rise_px:.1f})*on/{frames}"

    g = []
    g.append(f"[0:v]scale={sw}:{sh}:force_original_aspect_ratio=increase,"
             f"crop={sw}:{sh},setsar=1[base]")
    g.append(f"[base]zoompan=z='{z_full}':x='{x_expr}':y='{y_expr}':"
             f"d=1:s={w}x{h}:fps={fps}[mv]")

    if p["parallax"] > 0.01:
        # Shift the foreground band further than the background, then join
        # them with a soft vertical ramp so there is no hard seam.
        dx = int(p["parallax"] * p["pan_end"] * w * 0.9) or int(p["pan_end"] * w * 0.35)
        ramp = 0.24          # height of the blend ramp, as a fraction
        start = 0.42         # where the ramp begins, as a fraction of height
        g.append(
            f"[mv]split=2[bgA][fgA];"
            f"[bgA]crop={w}:{h}:0:0[bg];"
            f"[fgA]crop={w}:{h}:0:0,pad={w + 2 * pad}:{h}:{pad}:0:color=black,"
            f"crop={w}:{h}:{pad + dx}:0[fgshift];"
            f"[bg][fgshift]blend=all_expr="
            f"'A*(1-clip((Y/{h}-{start})/{ramp},0,1))+"
            f"B*clip((Y/{h}-{start})/{ramp},0,1)'[par]"
        )
        cur = "par"
    else:
        cur = "mv"

    # A slow focus pull: slightly soft at the start, sharp by the end.
    g.append(f"[{cur}]gblur=sigma=1.1:enable='lt(t,0.5)',"
             f"gblur=sigma=0.3:enable='gte(t,0.5)'[focus]")
    # Moving grain, so the surface never looks frozen.
    g.append("[focus]noise=alls=5:allf=t+u[grn]")
    g.append("[grn]format=yuv420p[out]")
    return ";".join(g)


def main() -> int:
    ap = argparse.ArgumentParser(description="Camera move over a still")
    ap.add_argument("--image", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--motion", default="push-in", choices=sorted(MOTIONS))
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()

    src = Path(args.image)
    dst = Path(args.out)
    if not src.exists():
        print(f"[move] image not found: {src}", file=sys.stderr)
        return 1

    graph = build_graph(args.motion, args.width, args.height, args.fps,
                        args.seconds)
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
           "-loop", "1", "-framerate", str(args.fps), "-i", str(src),
           "-vf", graph,
           "-t", f"{args.seconds}",
           "-r", str(args.fps),
           "-c:v", "libx264", "-preset", "medium", "-crf", "17",
           "-pix_fmt", "yuv420p", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2500:], file=sys.stderr)
        return r.returncode
    print(f"[move] {args.motion} {src.name} -> {dst.name} "
          f"({dst.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
