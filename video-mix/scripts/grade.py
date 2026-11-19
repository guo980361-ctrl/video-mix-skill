#!/usr/bin/env python3
"""Grade a montage so every shot reads as one film.

Why this exists
---------------
Each still comes out of a separate generation, so the eight shots drift
apart in white balance, contrast and saturation.  Watched back to back
that reads as "assembled clips" rather than "one scene", and it is the
main reason an AI montage looks rough even when every frame is decent.

The fix is a single grading pass applied to every clip: one colour
temperature, one contrast curve, one saturation target, plus the small
things that sell photographic footage -- vignette, a touch of grain and
a slight highlight bloom.

It works on the assembled video rather than the source stills so that
the grade lands after the transitions have mixed the shots, and so the
look can be dialled in with one command instead of eight.

Usage:
    python grade.py --in raw.mp4 --out graded.mp4
    python grade.py --in raw.mp4 --out graded.mp4 --preset cool
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".workbuddy" / "skills" / "video-mix" / "scripts"))
from vmix_common import ffmpeg_bin  # noqa: E402

# Each preset is (temperature, tint, contrast, saturation, lift, vignette).
# temperature/tint are in ffmpeg's arbitrary but monotonic scale:
#   temperature  > 0 warms, < 0 cools
#   tint         > 0 shifts magenta, < 0 shifts green
PRESETS = {
    "amber": (0.16, 0.03, 1.08, 1.14, 0.014, 0.30),
    "warm":   (0.11, 0.02, 1.05, 1.08, 0.011, 0.26),
    "neutral": (0.00, 0.00, 1.02, 1.02, 0.006, 0.18),
    "cool":   (-0.05, -0.01, 1.05, 0.98, 0.004, 0.22),
}

# How much warm lift each preset applies on top of the colour balance.
# This is the knob that actually moves the mean R-B of the output.
PRESETS_WARMTH = {
    "amber": 0.55,
    "warm": 0.35,
    "neutral": 0.0,
    "cool": -0.2,
}


def lutrgb_curve(warmth: float) -> str:
    """Build a lutrgb expression that warms the picture.

    The red channel is lifted by a fraction of its own value and the
    blue channel is pulled down by a similar amount, with the green
    channel barely touched.  Working per channel rather than per pixel
    keeps the effect monotonic -- no value can wrap -- and the shift in
    mean R-B is proportional to ``warmth``, so the grade can be tuned by
    measuring the output.
    """
    lift = warmth * 0.22
    drop = warmth * 0.18
    r = f"min(255\\,max(0\\,val*{1 + lift:.4f}+{lift * 24:.1f}))"
    g = f"min(255\\,max(0\\,val*{1 + lift * 0.22:.4f}))"
    b = f"min(255\\,max(0\\,val*{1 - drop:.4f}-{drop * 10:.1f}))"
    return f"lutrgb=r='{r}':g='{g}':b='{b}'"


def build_filter(preset: str, grain: float, bloom: float) -> str:
    temp, tint, contrast, sat, lift, vignette = PRESETS[preset]
    # eq runs first: it is the only filter that can set the contrast
    # curve, the saturation target and the shadow rolloff in one go.
    eq = (f"eq=contrast={contrast}:saturation={sat}:"
          f"gamma=0.98:brightness={lift}")
    # colorbalance pushes the midtones toward the scene's key colour.
    # A positive red and a negative blue is the warm direction; the tint
    # value shifts the same balance slightly toward green or magenta.
    cb = (f"colorbalance=rs=0.04:gs=0.0:bs=-0.07:"
          f"rm=0.05:gm={0.008 * tint / 0.03:.4f}:bm=-0.035:"
          f"rh=0.03:gh=0.012:bh=-0.02")
    # colortemperature shifts the actual pixel temperature. It has no
    # tint option, so the tint is handled by colorbalance above.
    ct = f"colortemperature=temperature={int(temp * 100000)}"

    parts = [eq, cb, ct]

    # A final, measurable warm lift.
    #
    # colortemperature and colorbalance pull in each other's direction:
    # the former works on the correlated colour temperature while the
    # latter works on the balance channels, so stacking both makes the
    # result unpredictable. The "warmth" curve below is a per-channel
    # lookup table that adds red and subtracts blue directly, which is
    # monotonic and can be verified by measuring the mean R-B of the
    # output.
    warmth = PRESETS_WARMTH.get(preset, 0.0)
    if abs(warmth) > 0.01:
        # lutrgb: red channel lifted, blue channel pulled down, green
        # left nearly alone so skin tones do not turn orange.
        lut = lutrgb_curve(warmth)
        parts.append(lut)

    if bloom > 0:
        # A gentle highlight bleed. Without it, bright fur against a dark
        # room looks cut out and pasted on.
        parts.append(
            f"split[a][b];[b]boxblur=6:1[bl];"
            f"[a][bl]blend=all_mode=screen:all_opacity={bloom}[blm];"
            f"[blm]split[c][d];[d]boxblur=2:1[bb];"
            f"[c][bb]blend=all_mode=screen:all_opacity={bloom * 0.5}[out_b]"
        )
        parts.append("[out_b]null")
    else:
        parts.append("null")

    if vignette > 0:
        parts.append(f"vignette=PI/4:eval=frame:mode=forward")

    if grain > 0:
        parts.append(f"noise=alls={int(grain * 12)}:allf=t+u")

    parts.append("format=yuv420p")
    return ",".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Grade a montage")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--preset", default="amber", choices=sorted(PRESETS))
    ap.add_argument("--grain", type=float, default=0.35)
    ap.add_argument("--bloom", type=float, default=0.12)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    args = ap.parse_args()

    src = Path(args.src)
    dst = Path(args.dst)
    if not src.exists():
        print(f"[grade] input not found: {src}", file=sys.stderr)
        return 1

    vf = build_filter(args.preset, args.grain, args.bloom)
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src),
           "-vf", vf,
           "-c:v", "libx264", "-preset", "slow", "-crf", "18",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart",
           str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2000:], file=sys.stderr)
        return r.returncode
    print(f"[grade] {src.name} -> {dst.name}  ({dst.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
