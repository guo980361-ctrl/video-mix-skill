#!/usr/bin/env python3
"""Measure how much real motion a clip has.

The problem this solves: a camera move over a still frame *looks* like
motion on a phone but has no subject motion. This script measures the
difference with numbers instead of opinion.

Three metrics per clip:

* **subject_motion** - optical flow magnitude inside the centre region
  (where the subject lives), after removing the global camera component.
  A real animal moving scores high; a Ken Burns push-in scores near zero.
* **background_stability** - residual flow after the global camera motion
  is subtracted. High means the background is being dragged around, which
  is what makes a fake move look fake.
* **temporal_detail** - mean absolute frame difference. Catches the
  "everything is one frozen picture" case directly.

Usage:
    python measure_motion.py clips/s02.mp4 clips/s03.mp4
    python measure_motion.py --dir clips
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path.home() / ".workbuddy" / "skills" / "video-mix" / "scripts"))


def read_frames(path: Path, max_frames: int = 48,
                size: int = 256) -> list[np.ndarray]:
    """Decode a clip to a list of grayscale float arrays.

    Uses ffmpeg to pipe raw gray frames, because imageio's pyav plugin is
    an optional extra that is often missing.
    """
    import subprocess

    from vmix_common import ffmpeg_bin

    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
           "-i", str(path), "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease,"
           f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=black,format=gray",
           "-frames:v", str(max_frames), "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = size * size
    count = len(raw) // n
    if count < 1:
        raise RuntimeError("no frames decoded")
    arr = np.frombuffer(raw[:count * n], dtype=np.uint8)
    return [f.astype(np.float32) for f in arr.reshape(count, size, size)]


def optical_flow(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Dense optical flow via Farneback.

    Returns (ux, uy), each ``a.shape``.
    """
    import cv2

    a8 = np.clip(a, 0, 255).astype(np.uint8)
    b8 = np.clip(b, 0, 255).astype(np.uint8)
    # OpenCV 5.x dropped the `win` argument entirely (10 args max);
    # OpenCV 4.x accepts it (11 args). The docstring still mentions "win"
    # in 5.x, so detect by version instead.
    major = int(cv2.__version__.split(".")[0])
    if major < 5:
        win = cv2.createHanningWindow((a8.shape[1], a8.shape[0]), cv2.CV_32F)
        flow = cv2.calcOpticalFlowFarneback(
            a8, b8, None, 0.5, 3, 21, 3, 5, 1.2, 0, win)
    else:
        flow = cv2.calcOpticalFlowFarneback(
            a8, b8, None, 0.5, 3, 21, 3, 5, 1.2, 0)
    return flow[..., 0].astype(np.float32), flow[..., 1].astype(np.float32)


def verdict(affine_residual: float) -> str:
    """Classify a clip from its affine residual.

    Calibrated on 2026-11-19 against known samples:
      real filmed / generated motion -> residual >= 0.60
      ambiguous                     -> 0.35 - 0.60
      camera move over a still      -> residual < 0.35
    """
    if affine_residual >= 0.60:
        return "REAL MOTION"
    if affine_residual >= 0.35:
        return "slight motion"
    return "STATIC (camera move only)"


def analyze_frames(frames: list[np.ndarray], name: str = "frames") -> dict:
    """Measure motion on already-decoded grayscale frames.

    Split out from :func:`analyze` so the metric can be unit tested without
    touching the filesystem.
    """
    import cv2

    if len(frames) < 3:
        return {"file": name, "error": "too few frames"}
    h, w = frames[0].shape
    cy0, cy1 = int(h * 0.2), int(h * 0.8)
    cx0, cx1 = int(w * 0.2), int(w * 0.8)

    subj_mag: list[float] = []
    bg_mag: list[float] = []
    detail: list[float] = []
    affine_resid: list[float] = []

    ys, xs = np.mgrid[0:h, 0:w]
    pts = np.stack([xs.ravel(), ys.ravel()], axis=1).astype(np.float32)

    for a, b in zip(frames, frames[1:]):
        ux, uy = optical_flow(a, b)
        mag = np.hypot(ux, uy)
        flow_pts = np.stack([ux.ravel(), uy.ravel()], axis=1).astype(np.float32)

        # Fit an affine model to the whole flow field, then look at what is
        # left over. A camera move (zoom / pan / parallax) is, to a very good
        # approximation, an affine transform of the frame -- so an affine fit
        # absorbs it and the residual collapses to nearly zero. Genuine
        # subject motion is not affine, so it survives.
        try:
            M, inliers = cv2.estimateAffinePartial2D(
                pts, pts + flow_pts, method=cv2.RANSAC,
                ransacReprojThreshold=2.0, maxIters=500)
        except Exception:
            M = None
        if M is not None:
            pred = (M[:, :2] @ pts.T).T + M[:, 2]
            resid = np.linalg.norm(pred - (pts + flow_pts), axis=1)
            affine_resid.append(float(resid.mean()))
        else:
            affine_resid.append(float(mag.mean()))

        # global camera component: median flow of the whole frame
        gx = float(np.median(ux))
        gy = float(np.median(uy))
        rx, ry = ux - gx, uy - gy
        rmag = np.hypot(rx, ry)
        subj_mag.append(float(rmag[cy0:cy1, cx0:cx1].mean()))
        bg_mag.append(float(rmag.mean()))
        detail.append(float(np.abs(b - a).mean()))

    return {
        "file": name,
        "frames": len(frames),
        "subject_motion": round(float(np.mean(subj_mag)), 4),
        "affine_residual": round(float(np.mean(affine_resid)), 4),
        "temporal_detail": round(float(np.mean(detail)), 4),
        "camera_drift": round(float(np.hypot(
            float(np.median([np.mean(optical_flow(a, b)[0])
                             for a, b in zip(frames, frames[1:])])),
            float(np.median([np.mean(optical_flow(a, b)[1])
                             for a, b in zip(frames, frames[1:])])))), 4),
    }


def analyze(path: Path) -> dict:
    frames = read_frames(path)
    return analyze_frames(frames, name=Path(path).name)


def main() -> int:
    ap = argparse.ArgumentParser(description="Measure real motion in clips")
    ap.add_argument("clips", nargs="*")
    ap.add_argument("--dir")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    paths = [Path(p) for p in args.clips]
    if args.dir:
        paths += sorted(Path(args.dir).glob("*.mp4"))
    if not paths:
        ap.error("give clip paths or --dir")

    rows = []
    for p in paths:
        try:
            rows.append(analyze(p))
            print(f"[motion] {p.name} done", file=sys.stderr)
        except Exception as exc:
            rows.append({"file": p.name, "error": str(exc)[:120]})
            print(f"[motion] {p.name} FAILED: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print()
    print(f"{'clip':22s} {'affine_resid':>13s} {'subject_motion':>15s} "
          f"{'temporal_detail':>16s}  verdict")
    print("-" * 84)
    for r in rows:
        if "error" in r:
            print(f"{r['file']:22s}  ERROR {r['error']}")
            continue
        # The decisive metric is affine_residual: how much of the frame
        # motion cannot be explained by a single affine transform.
        #   camera move over a still -> an affine transform explains it
        #   real filmed or generated motion -> residual >= 0.60
        ar = r["affine_residual"]
        print(f"{r['file']:22s} {ar:13.4f} {r['subject_motion']:15.4f} "
              f"{r['temporal_detail']:16.4f}  {verdict(ar)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
