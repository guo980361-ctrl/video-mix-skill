"""Shared utilities for the video-mix skill.

All backends here were empirically verified on 2026-11-18. See
references/free-backends.md for the raw findings, including which
"free" services turned out to be dead.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- paths

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_WORKDIR = Path.cwd() / "video-mix-out"


def workdir(path: str | os.PathLike | None = None) -> Path:
    """Default output location when a script needs to invent one.

    Scripts resolve user-supplied relative paths against the current
    working directory (standard CLI behaviour); this is only a fallback.
    """
    d = Path(path) if path else DEFAULT_WORKDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def out_path(path: str | os.PathLike, base: Path | None = None) -> Path:
    """Resolve an output path.

    Relative paths resolve against the current working directory, so
    `--out video-mix-out/demo/a.mp4` never turns into
    `video-mix-out/video-mix-out/demo/a.mp4`.
    """
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    root = Path(base) if base else Path.cwd()
    return root / p


def ensure_dir(p: str | os.PathLike) -> Path:
    p = Path(p)
    p.mkdir(parents=True, exist_ok=True)
    return p


# ---------------------------------------------------------------- python deps


def _py() -> str:
    return sys.executable


def install_deps(quiet: bool = False) -> None:
    """Install the Python packages this skill needs. Idempotent."""
    required = ["requests", "edge-tts", "imageio-ffmpeg", "gradio_client"]
    missing = []
    for mod in required:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if not missing:
        return
    cmd = [_py(), "-m", "pip", "install", "--quiet", *missing]
    if not quiet:
        print("[deps] installing:", " ".join(missing))
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------- ffmpeg


def ffmpeg_bin() -> str:
    """Locate ffmpeg: PATH first, then the pip-installed static build.

    The pip static build (imageio-ffmpeg) is the preferred fallback because
    it needs no brew and ships an x86_64 macOS binary.
    """
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "ffmpeg not found. Install it with: pip install imageio-ffmpeg"
        ) from exc


def ffprobe_bin() -> str | None:
    """ffprobe is optional; only used for duration probing."""
    return shutil.which("ffprobe")


def run_ff(args: list[str], quiet: bool = False) -> None:
    """Run ffmpeg with the given arguments."""
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args]
    if not quiet:
        print("[ffmpeg]", " ".join(args[:6]), "..." if len(args) > 6 else "")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{proc.stderr[-3000:]}")


def probe_duration(path: str | os.PathLike) -> float:
    """Return media duration in seconds. Falls back to ffprobe, then to a
    decode count via ffmpeg."""
    probe = ffprobe_bin()
    if probe:
        out = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
        )
        try:
            return float(json.loads(out.stdout)["format"]["duration"])
        except Exception:
            pass
    # fallback: parse ffmpeg stderr
    proc = subprocess.run(
        [ffmpeg_bin(), "-i", str(path)],
        capture_output=True,
        text=True,
    )
    for line in proc.stderr.splitlines():
        if "Duration:" in line:
            ts = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = ts.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    raise RuntimeError(f"cannot determine duration of {path}")


# ---------------------------------------------------------------- misc


def slugify(text: str, maxlen: int = 40) -> str:
    keep = []
    for ch in text.strip():
        if ch.isalnum() or ch in "-_":
            keep.append(ch)
        elif ch in " /\\":
            keep.append("-")
    out = "".join(keep).strip("-")
    return (out or "item")[:maxlen]


def ensure_audio(src: str | os.PathLike, dst: str | os.PathLike,
                 duration: float | None = None) -> Path:
    """Normalize any audio file to 44.1kHz stereo wav, trimmed to duration."""
    dst = Path(dst)
    args = ["-i", str(src), "-ar", "44100", "-ac", "2"]
    if duration:
        args += ["-t", str(round(duration, 3))]
    args += [str(dst)]
    run_ff(args)
    return dst


def ensure_video(src: str | os.PathLike, dst: str | os.PathLike,
                 width: int = 768, height: int = 768, fps: int = 25,
                 duration: float | None = None) -> Path:
    """Normalize any video to h264/yuv420p at a fixed size, letterboxed to
    keep aspect ratio via scale+pad."""
    dst = Path(dst)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"fps={fps}"
    )
    args = ["-i", str(src), "-vf", vf]
    if duration:
        args += ["-t", str(round(duration, 3))]
    args += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "2",
        str(dst),
    ]
    run_ff(args)
    return dst


def retry(fn, tries: int = 3, base: float = 3.0, label: str = "task"):
    """Retry a callable with exponential backoff.

    HF ZeroGPU Spaces frequently return 502/timeout while a cold container
    boots; retrying is what actually makes them reliable.
    """
    last = None
    for attempt in range(1, tries + 1):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if attempt == tries:
                break
            wait = base * (2 ** (attempt - 1))
            print(f"[retry] {label} failed (attempt {attempt}/{tries}): "
                  f"{type(exc).__name__}: {str(exc)[:200]}")
            print(f"[retry] waiting {wait:.0f}s then retrying")
            time.sleep(wait)
    raise RuntimeError(f"{label} failed after {tries} attempts: {last}")


def hf_token() -> str | None:
    """Return the Hugging Face token, if the user configured one.

    Anonymous ZeroGPU access is capped at 2 GPU-minutes/day per IP; a free
    account with a token raises that to 5 minutes/day. Set HF_TOKEN in the
    environment or write it to ~/.workbuddy/.hf_token.
    """
    tok = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if tok:
        return tok.strip()
    token_file = Path.home() / ".workbuddy" / ".hf_token"
    if token_file.exists():
        val = token_file.read_text(encoding="utf-8").strip()
        if val:
            return val
    return None


def quota_hint(exc: Exception) -> str:
    """Explain a ZeroGPU quota error in actionable terms."""
    text = str(exc)
    if "ZeroGPU quota" in text or "exceeded your" in text:
        return (
            "Hugging Face ZeroGPU 额度已用尽。匿名访问每天仅 2 分钟 GPU "
            "时长，免费账号（绑定 token）为 5 分钟/天。\n"
            "  解决办法：到 https://huggingface.co/settings/tokens 创建免费 "
            "token，然后 export HF_TOKEN=hf_xxx\n"
            "  或改用本机的 --backend wan 等其他后端。"
        )
    return str(exc)[:400]

