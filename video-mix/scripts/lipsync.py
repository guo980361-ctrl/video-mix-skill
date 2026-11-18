#!/usr/bin/env python3
"""Lip-sync: drive a still image (or a video) with an audio track.

Verified 2026-11-18, free and no API key:
  pragnakalp/Wav2lip-ZeroGPU  -> /run_infrence(input_image, input_audio)
      image + audio in, 768x768 h264/aac out, 31.1s wall clock.
  fffiloni/LatentSync         -> /generate_lip_sync_video(video, audio)
      video + audio in, zero-a10g, higher fidelity but slower/queued.
  manavisrani07/gradio-lipsync-wav2lip -> /generate(video, audio, checkpoint)
      video + audio in, cpu-basic, works with existing footage.

A still image is turned into a video first (with a slow zoom so it is not a
frozen frame), then handed to the lip-sync backend.

Usage:
    python lipsync.py --image face.jpg --audio voice.mp3 --out talking.mp4
    python lipsync.py --image face.jpg --audio voice.mp3 --out t.mp4 --backend latentsync
    python lipsync.py --video clip.mp4 --audio voice.mp3 --out dubbed.mp4
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vmix_common import (ensure_audio, ensure_video, ffmpeg_bin,  # noqa: E402
                         hf_token, install_deps, out_path, probe_duration,
                         quota_hint, retry)

SPACES = {
    "wav2lip": ("pragnakalp/Wav2lip-ZeroGPU", "/run_infrence"),
    "latentsync": ("fffiloni/LatentSync", "/generate_lip_sync_video"),
    "wav2lip_video": ("manavisrani07/gradio-lipsync-wav2lip", "/generate"),
}


def _client(space: str, timeout: float = 900.0):
    import httpx
    from gradio_client import Client

    kwargs = {}
    token = hf_token()
    if token:
        kwargs["hf_token"] = token
    return Client(space, verbose=False,
                  httpx_kwargs={"timeout": httpx.Timeout(timeout, connect=60.0)},
                  **kwargs)


def _save(result, out: Path) -> Path:
    src = None
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        src = result.get("video") or result.get("output")
        if isinstance(src, dict):
            src = src.get("path") or src.get("url")
    elif isinstance(result, (str, Path)):
        src = result
    if not src:
        raise RuntimeError(f"no video in result: {result!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(src), str(out))
    return out


def still_to_video(image: Path, audio: Path, out: Path, fps: int = 25,
                   size: int = 768) -> Path:
    """Make a gentle zoom/pan clip from a still so lip-sync has motion."""
    vf = (f"scale={size}:{size}:force_original_aspect_ratio=increase,"
          f"crop={size}:{size},"
          f"zoompan=z='min(zoom+0.0009,1.12)':d=1:s={size}x{size}:fps={fps}")
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
           "-loop", "1", "-i", str(image), "-i", str(audio),
           "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)]
    subprocess.run(cmd, check=True)
    return out


def run_wav2lip_image(image: Path, audio: Path, out: Path) -> Path:
    from gradio_client import handle_file

    space, endpoint = SPACES["wav2lip"]
    c = _client(space)

    def call():
        return c.predict(input_image=handle_file(str(image)),
                         input_audio=handle_file(str(audio)),
                         api_name=endpoint)

    res = retry(call, tries=3, base=5.0, label="wav2lip(image)")
    return _save(res, out)


def run_latentsync(video: Path, audio: Path, out: Path) -> Path:
    from gradio_client import handle_file

    space, endpoint = SPACES["latentsync"]
    c = _client(space, timeout=1200.0)

    def call():
        return c.predict(input_video_path=handle_file(str(video)),
                         input_audio_path=handle_file(str(audio)),
                         api_name=endpoint)

    res = retry(call, tries=3, base=8.0, label="latentsync")
    return _save(res, out)


def run_wav2lip_video(video: Path, audio: Path, out: Path) -> Path:
    from gradio_client import handle_file

    space, endpoint = SPACES["wav2lip_video"]
    c = _client(space, timeout=1200.0)

    def call():
        return c.predict(video=handle_file(str(video)),
                         audio=handle_file(str(audio)),
                         checkpoint="wav2lip_gan",
                         no_smooth=0,
                         resize_factor=10,
                         pad_top=0,
                         pad_bottom=0,
                         pad_left=1,
                         api_name=endpoint)

    res = retry(call, tries=3, base=8.0, label="wav2lip(video)")
    return _save(res, out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Free lip-sync (no API key)")
    ap.add_argument("--image", help="still image to animate")
    ap.add_argument("--video", help="existing video to dub")
    ap.add_argument("--audio", required=True, help="driving audio (mp3/wav)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "wav2lip", "latentsync", "wav2lip_video"])
    ap.add_argument("--size", type=int, default=768)
    ap.add_argument("--fps", type=int, default=25)
    args = ap.parse_args()

    install_deps(quiet=True)
    if not args.image and not args.video:
        print("[lipsync] need --image or --video", file=sys.stderr)
        return 1

    out = out_path(args.out)
    audio = Path(args.audio).expanduser().resolve()
    if not audio.exists():
        print(f"[lipsync] audio not found: {audio}", file=sys.stderr)
        return 1

    tmp = out.parent / "_ls_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    wav = ensure_audio(audio, tmp / "audio.wav")

    t0 = time.time()
    try:
        if args.video:
            src = Path(args.video).expanduser().resolve()
            dur = probe_duration(src)
            if args.backend == "auto":
                order = ["latentsync", "wav2lip_video"]
            else:
                order = [args.backend]
            last = None
            for b in order:
                try:
                    if b == "latentsync":
                        path = run_latentsync(src, wav, out)
                    else:
                        path = run_wav2lip_video(src, wav, out)
                    break
                except Exception as exc:
                    last = exc
                    print(f"[lipsync] backend {b} failed: {str(exc)[:200]}")
            else:
                raise RuntimeError(f"all backends failed: {last}")
        else:
            image = Path(args.image).expanduser().resolve()
            if not image.exists():
                print(f"[lipsync] image not found: {image}", file=sys.stderr)
                return 1
            clip = still_to_video(image, wav, tmp / "still.mp4",
                                  fps=args.fps, size=args.size)
            if args.backend in ("auto", "wav2lip"):
                try:
                    path = run_wav2lip_image(image, wav, out)
                except Exception as exc:
                    print(f"[lipsync] wav2lip failed: {str(exc)[:200]}")
                    if args.backend == "auto":
                        path = run_latentsync(clip, wav, out)
                    else:
                        raise
            elif args.backend == "latentsync":
                path = run_latentsync(clip, wav, out)
            else:
                path = run_wav2lip_video(clip, wav, out)
    except Exception as exc:
        print(f"[lipsync] FAILED: {exc}", file=sys.stderr)
        print(quota_hint(exc), file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    kb = path.stat().st_size / 1024
    print(f"[lipsync] OK -> {path}  ({kb:.0f} KB, {time.time() - t0:.1f}s)")
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
