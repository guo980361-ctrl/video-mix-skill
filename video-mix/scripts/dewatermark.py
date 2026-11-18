#!/usr/bin/env python3
"""Remove a bottom-right text watermark from an image.

Pollinations stamps "pollinations.ai" into the bottom-right corner of every
free-tier render. The mark is pure white text on the image, so it can be
recovered by cloning the pixels that sit directly above it.

Two passes are used:

1. Detect the watermark band by scanning the bottom-right region for near-
   white pixels that are brighter than the local background.
2. Rebuild that band by inpainting: a mask of the detected text pixels is
   filled by cv2.inpaint (Telea), which propagates the surrounding colour
   inward. A mirrored-clone fallback covers the case where OpenCV is
   unavailable.

The inpaint approach matters: the Pollinations mark includes a small flower
glyph whose strokes are thinner than the text, and a naive clone leaves the
glyph's lower half behind as speckles.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def detect_band(a: np.ndarray, corner_w: int, corner_h: int,
                thresh: int = 235, min_pixels: int = 12) -> tuple[int, int] | None:
    """Return (y_top, y_bottom) of the watermark band, or None.

    min_pixels is deliberately low: the mark's flower glyph is only a few
    pixels wide per row, so a strict row count would clip the band and
    leave the glyph's lower strokes behind.
    """
    h, w, _ = a.shape
    x0 = w - corner_w
    y0 = h - corner_h
    region = a[y0:h, x0:w]
    bright = (region.max(axis=2) >= thresh)
    counts = bright.sum(axis=1)
    rows = np.where(counts >= min_pixels)[0]
    if len(rows) == 0:
        return None
    top = y0 + int(rows.min())
    bottom = y0 + int(rows.max())
    return top, bottom + 1


def build_mask(a: np.ndarray, top: int, bottom: int, x0: int,
               thresh: int = 190, grow: int = 3) -> np.ndarray:
    """Binary mask of watermark pixels, dilated to catch anti-aliased edges.

    The threshold is well below the text brightness on purpose: the glyph's
    thin strokes and their anti-aliased fringes are much dimmer than the
    letterforms, and leaving them behind is what produces visible speckles.
    """
    h, w, _ = a.shape
    y_lo = max(top - 4, 0)
    y_hi = min(bottom + 5, h)
    mask = np.zeros((h, w), dtype=np.uint8)
    region = a[y_lo:y_hi, x0:w]
    if region.size:
        sub = (region.max(axis=2) >= thresh).astype(np.uint8) * 255
        mask[y_lo:y_hi, x0:w] = sub
    try:
        import cv2
        kernel = np.ones((grow * 2 + 1, grow * 2 + 1), np.uint8)
        mask = cv2.dilate(mask, kernel, iterations=1)
    except ImportError:
        pass
    return mask


def inpaint_opencv(a: np.ndarray, mask: np.ndarray) -> np.ndarray | None:
    """Telea inpainting; returns None when OpenCV is unavailable."""
    try:
        import cv2
    except ImportError:
        return None
    rgb = np.clip(a, 0, 255).astype("uint8")
    fixed = cv2.inpaint(rgb, mask, 3, cv2.INPAINT_TELEA)
    return fixed.astype(int)


def inpaint_clone(a: np.ndarray, top: int, bottom: int,
                  x0: int, feather: int = 3) -> np.ndarray:
    """Fallback: clone the rows above the band downward."""
    out = a.copy()
    h = a.shape[0]
    span = bottom - top
    if span <= 0:
        return out
    for i in range(span):
        src = max(top - 1 - i, 0)
        dst = top + i
        if dst >= h:
            break
        out[dst, x0:] = a[src, x0:]
    for k in range(feather):
        y = top - 1 - k
        if y < 0:
            break
        alpha = (k + 1) / (feather + 1)
        out[y, x0:] = (out[y, x0:] * (1 - alpha) +
                       out[max(y - 1, 0), x0:] * alpha)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Remove a bottom-right watermark")
    ap.add_argument("--image", required=True, help="input image")
    ap.add_argument("--out", help="output image (default: in place)")
    ap.add_argument("--x0-frac", type=float, default=0.70,
                    help="left edge of the watermark zone as a width fraction")
    ap.add_argument("--corner-h", type=int, default=60,
                    help="how far up from the bottom to look for the mark")
    ap.add_argument("--thresh", type=int, default=235,
                    help="brightness that counts as watermark text")
    ap.add_argument("--min-pixels", type=int, default=3,
                    help="minimum white pixels in a row to treat it as the mark")
    args = ap.parse_args()

    src = Path(args.image).expanduser()
    if not src.exists():
        print(f"[dewatermark] input not found: {src}", file=sys.stderr)
        return 1
    dst = Path(args.out).expanduser() if args.out else src
    dst.parent.mkdir(parents=True, exist_ok=True)

    im = Image.open(src).convert("RGB")
    a = np.asarray(im).astype(int)
    h, w, _ = a.shape
    x0 = int(w * args.x0_frac)

    band = detect_band(a, w - x0, args.corner_h, args.thresh, args.min_pixels)
    if band is None:
        print(f"[dewatermark] no watermark detected in {src.name}")
        im.save(dst, quality=95)
        return 0

    top, bottom = band
    mask = build_mask(a, top, bottom, x0)
    out = inpaint_opencv(a, mask)
    method = "inpaint-telea"
    if out is None:
        out = inpaint_clone(a, top, bottom, x0)
        method = "clone-fallback"
    Image.fromarray(np.clip(out, 0, 255).astype("uint8")).save(dst, quality=95)
    print(f"[dewatermark] {src.name}: cleaned rows {top}-{bottom} "
          f"(x>={x0}), method={method} -> {dst}")
    print(str(dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
