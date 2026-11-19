"""Tests for the motion measurement helper.

The metric is the residual left after a global affine transform explains the
frame-to-frame change. A camera move over a still (zoom / pan) is affine, so
its residual collapses to ~0. Real subject motion is not affine and survives.
"""

import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import measure_motion  # noqa: E402


def _write_clip(path: Path, frames: list[np.ndarray], fps: int = 10) -> Path:
    """Write an RGB clip with the ffmpeg binary bundled with imageio."""
    import imageio.v3 as iio

    iio.imwrite(str(path), np.stack(frames), fps=fps, codec="libx264",
                quality=9, macro_block_size=1)
    return path


def _cam_move_frames(h: int, w: int, n: int, zoom: float = 1.25) -> list[np.ndarray]:
    """Frames produced by a pure zoom: fully explainable by an affine map.

    The output frame size stays constant -- only the crop window into the
    source grows -- otherwise consecutive frames would have different
    dimensions and the flow could not be computed at all.
    """
    rng = np.random.default_rng(7)
    big = rng.integers(0, 255, (int(h * zoom) + 4, int(w * zoom) + 4, 3),
                       dtype=np.uint8).astype(np.float32)
    frames = []
    for i in range(n):
        scale = 1.0 + (zoom - 1.0) * i / max(1, n - 1)
        ch, cw = int(h * scale), int(w * scale)
        y0 = (big.shape[0] - ch) // 2
        x0 = (big.shape[1] - cw) // 2
        crop = big[y0:y0 + h, x0:x0 + w]
        frames.append(np.clip(crop, 0, 255).astype(np.uint8))
    return frames


def _moving_subject_frames(h: int, w: int, n: int) -> list[np.ndarray]:
    """A bright square that translates across a static background."""
    frames = []
    for i in range(n):
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:, :, 1] = 40
        x = 10 + i * 8
        y = h // 2
        frame[y - 12:y + 12, x:x + 24] = 240
        frames.append(frame)
    return frames


class TestAffineResidual(unittest.TestCase):
    def test_camera_move_scores_near_zero(self):
        """A pure zoom must not be mistaken for subject motion."""
        frames = _cam_move_frames(160, 160, 10)
        gray = [f.astype(np.float32).mean(axis=2) for f in frames]
        r = measure_motion.analyze_frames(gray)
        self.assertLess(r["affine_residual"], 0.35,
                        "a camera move must not look like real motion")
        self.assertEqual(measure_motion.verdict(r["affine_residual"]),
                         "STATIC (camera move only)")

    def test_translating_subject_scores_above_camera_move(self):
        gray_mv = [f.astype(np.float32).mean(axis=2)
                   for f in _moving_subject_frames(160, 160, 10)]
        gray_cam = [f.astype(np.float32).mean(axis=2)
                    for f in _cam_move_frames(160, 160, 10)]
        mv = measure_motion.analyze_frames(gray_mv)
        cam = measure_motion.analyze_frames(gray_cam)
        self.assertGreater(mv["affine_residual"], cam["affine_residual"] * 2,
                           "subject motion must score clearly above a camera move")

    def test_too_few_frames_is_reported(self):
        r = measure_motion.analyze_frames([np.zeros((32, 32), np.float32)])
        self.assertIn("error", r)


class TestVerdict(unittest.TestCase):
    def test_thresholds_split_camera_move_from_real_motion(self):
        self.assertEqual(measure_motion.verdict(0.05), "STATIC (camera move only)")
        self.assertEqual(measure_motion.verdict(0.34), "STATIC (camera move only)")
        self.assertEqual(measure_motion.verdict(0.35), "slight motion")
        self.assertEqual(measure_motion.verdict(0.59), "slight motion")
        self.assertEqual(measure_motion.verdict(0.60), "REAL MOTION")
        self.assertEqual(measure_motion.verdict(1.03), "REAL MOTION")


class TestCli(unittest.TestCase):
    def test_cli_runs_on_a_generated_clip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            clip = _write_clip(Path(tmp) / "clip.mp4",
                               _moving_subject_frames(160, 160, 12))
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "measure_motion.py"), str(clip)],
                capture_output=True, text=True, timeout=300)
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            self.assertIn("affine_resid", proc.stdout)


if __name__ == "__main__":
    unittest.main()
