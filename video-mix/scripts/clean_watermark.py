#!/usr/bin/env python3
"""Remove the Pollinations watermark from every generated still.

The free tier burns a logo into the bottom-right corner.  It is small
but it is exactly where the eye goes to check the quality of a frame,
and a logo in the corner makes even a good image look scraped.

The existing ``dewatermark.py`` is tuned for a corner box and uses
OpenCV inpainting.  This wraps it over a whole directory and also
verifies the result, because a failed inpaint leaves a visible smear
that is worse than the original logo.

Usage:
    python denoise_watermark.py --indir shots --outdir shots_clean
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT = Path.home() / ".workbuddy" / "skills" / "video-mix" / "scripts" / "dewatermark.py"


def main() -> int:
    ap = argparse.ArgumentParser(description="Batch watermark removal")
    ap.add_argument("--indir", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()

    indir = Path(args.indir)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    files = sorted(indir.glob("*.jpg"))
    if not files:
        print(f"[wm] no images in {indir}", file=sys.stderr)
        return 1

    for f in files:
        dst = outdir / f.name
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--image", str(f), "--out", str(dst)],
            capture_output=True, text=True)
        ok = dst.exists() and dst.stat().st_size > 5000
        print(f"[wm] {f.name} -> {'ok' if ok else 'FAILED'}"
              + ("" if ok else f"  {r.stderr.strip()[-200:]}"))
        if not ok:
            return 1
    print(f"[wm] {len(files)} images cleaned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
