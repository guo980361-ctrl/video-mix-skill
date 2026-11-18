#!/usr/bin/env python3
"""Assemble clips into a finished montage: cut, transition, subtitle, score.

Everything runs locally through ffmpeg (pip-installed static build, no brew
needed).

Features:
  * concat a list of clips, each normalized to a common size/fps
  * optional crossfade (xfade) transitions between clips
  * burn an SRT subtitle file, or generate one from the audio duration
  * duck the BGM under the voice track with sidechaincompress
  * loudness-normalize the final mix to broadcast-ish LUFS

Usage:
    python mix.py --clips a.mp4 b.mp4 c.mp4 --out final.mp4
    python mix.py --clips a.mp4 b.mp4 --out final.mp4 --transition fade --duration 0.5
    python mix.py --clips a.mp4 b.mp4 --out final.mp4 --subtitles s.srt
    python mix.py --clips a.mp4 b.mp4 --out final.mp4 --bgm music.mp3 --voice voice.mp3
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vmix_common import (ensure_audio, ensure_dir, ffmpeg_bin,  # noqa: E402
                         install_deps, out_path, probe_duration)

TRANSITIONS = ["fade", "fadeblack", "fadewhite", "slideleft", "slideright",
               "wipeleft", "circleopen", "dissolve", "cut"]


def normalize(clip: Path, out: Path, w: int, h: int, fps: int) -> Path:
    """Conform a clip to the timeline: size, fps, codec, silent audio."""
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,fps={fps},"
          f"format=yuv420p")
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(clip),
           "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
           "-vf", vf, "-t", "9999",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-ar", "44100", "-ac", "2",
           "-shortest", "-map", "0:v:0", "-map", "1:a:0",
           str(out)]
    subprocess.run(cmd, check=True)
    return out


def srt_to_ass(srt_path: Path, ass_path: Path, width: int, height: int,
               font_size: int) -> Path:
    """Convert SRT to ASS with an explicit PlayRes.

    libass renders a plain SRT against a default PlayResX of 384, which
    scales the font by width/384 -- a 1080-wide render came out roughly
    2.8x too large. Declaring PlayResX/PlayResY equal to the output size
    makes FontSize a literal pixel value, so sizing becomes predictable.
    """
    text = srt_path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues = []
    for b in blocks:
        lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        start, end = [t.strip() for t in lines[idx].split("-->")][:2]
        body = " ".join(lines[idx + 1:])
        if not body:
            continue

        def conv(ts: str) -> str:
            ts = ts.replace(",", ".")
            h, m, s = ts.split(":")
            return f"{int(h):d}:{int(m):02d}:{float(s):05.2f}"

        cues.append((conv(start), conv(end), body))

    margin = max(30, round(height * 0.045))
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,PingFang SC,{font_size},&H00FFFFFF,&H000000FF,&H80000000,&H00000000,0,0,0,0,100,100,0,0,1,{max(2, round(font_size * 0.08))},0,2,{margin},{margin},{margin},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    body = "".join(
        f"Dialogue: 0,{s},{e},Default,,0,0,0,,{t}\n" for s, e, t in cues
    )
    ass_path.write_text(head + body, encoding="utf-8")
    return ass_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Assemble a montage from clips")
    ap.add_argument("--clips", nargs="+", required=True,
                    help="clip paths, in order")
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920, help="1920 = vertical 9:16")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--transition", default="fade", choices=TRANSITIONS)
    ap.add_argument("--duration", type=float, default=0.5,
                    help="transition length in seconds")
    ap.add_argument("--subtitles", help="SRT file to burn in")
    ap.add_argument("--subtitle-size", type=int, default=0,
                    help="font size; 0 = auto-scale to output width "
                         "(recommended)")
    ap.add_argument("--bgm", help="background music file")
    ap.add_argument("--bgm-volume", default="0.18")
    ap.add_argument("--voice", help="voice track to play over the video")
    ap.add_argument("--voice-volume", default="1.0")
    ap.add_argument("--no-duck", action="store_true",
                    help="disable automatic BGM ducking")
    args = ap.parse_args()

    install_deps(quiet=True)
    out = out_path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    tmp = ensure_dir(out.parent / "_mix_tmp")
    clips = [Path(c).expanduser().resolve() for c in args.clips]
    missing = [str(c) for c in clips if not c.exists()]
    if missing:
        print(f"[mix] missing clips: {missing}", file=sys.stderr)
        return 1

    # ---- normalize every clip
    norm = []
    for i, c in enumerate(clips):
        n = tmp / f"clip_{i:02d}.mp4"
        norm.append(normalize(c, n, args.width, args.height, args.fps))
        print(f"[mix] normalized {i + 1}/{len(clips)}: {c.name}")

    durs = [probe_duration(p) for p in norm]
    print(f"[mix] clip durations: {[round(d, 2) for d in durs]}")

    # The voice track is often longer than the clip stack. Without padding,
    # -shortest would cut the tail of the narration (and its subtitles).
    # Freeze the last frame to extend the video instead.
    voice_len = 0.0
    if args.voice:
        voice_len = probe_duration(Path(args.voice).expanduser().resolve())
    n_ov = max(0, len(norm) - 1) * (args.duration
                                     if args.transition != "cut" else 0)
    video_len = sum(durs) - n_ov
    if voice_len > video_len + 0.05:
        pad = voice_len - video_len
        print(f"[mix] padding last clip by {pad:.2f}s to cover the voice track")
        last = norm[-1]
        padded = tmp / f"clip_{len(norm) - 1:02d}_pad.mp4"
        vf = (f"tpad=stop_mode=clone:stop_duration={pad:.3f}")
        subprocess.run(
            [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(last), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy", str(padded)],
            check=True,
        )
        norm[-1] = padded
        durs[-1] = probe_duration(padded)

    # ---- build the video graph
    if len(norm) == 1:
        vgraph = "[0:v]null[vx0]"
        vlabel = "[vx0]"
    elif args.transition == "cut" or args.duration <= 0:
        vgraph = "".join(f"[{i}:v]" for i in range(len(norm))) + \
                 f"concat=n={len(norm)}:v=1:a=0[vx0]"
        vlabel = "[vx0]"
    else:
        d = args.duration
        # xfade offset chain: each transition starts where the previous
        # segment ends minus the overlap length.
        acc = durs[0]
        offs = []
        for i in range(1, len(norm)):
            offs.append(acc - d)
            acc = acc + durs[i] - d
        # Each filter consumes the previous output label and produces a new
        # one; the final label is what the subtitle stage (if any) consumes.
        parts = []
        prev = "[0:v]"
        for i in range(1, len(norm)):
            label = f"vx{i}"
            parts.append(
                f"{prev}[{i}:v]xfade=transition={args.transition}:"
                f"duration={d}:offset={offs[i - 1]:.3f}[{label}]"
            )
            prev = f"[{label}]"
        vgraph = ";".join(parts)
        vlabel = prev
    vout = "vout"

    # ---- audio graph
    aud = []
    inputs = []
    for i, p in enumerate(norm):
        inputs += ["-i", str(p)]

    voice_idx = None
    bgm_idx = None
    extra = []
    if args.voice:
        voice_idx = len(norm)
        extra += ["-i", str(Path(args.voice).expanduser().resolve())]
    if args.bgm:
        bgm_idx = len(norm) + (1 if args.voice else 0)
        extra += ["-i", str(Path(args.bgm).expanduser().resolve())]

    if voice_idx is not None:
        agraph = f"[{voice_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={args.voice_volume}[aout]"
    elif bgm_idx is not None:
        agraph = f"[{bgm_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={args.bgm_volume}[aout]"
    else:
        agraph = f"[{len(norm) - 1}:a]aformat=sample_rates=44100:channel_layouts=stereo[aout]"

    post = []
    if args.voice and args.bgm and not args.no_duck:
        agraph = (
            f"[{bgm_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            f"volume={args.bgm_volume}[bgm];"
            f"[{voice_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            f"volume={args.voice_volume}[voice];"
            f"[bgm][voice]sidechaincompress=threshold=0.03:ratio=8:"
            f"attack=20:release=300[ducked];"
            f"[ducked][voice]amix=inputs=2:duration=longest:dropout_transition=0[aout]"
        )
    elif args.voice and args.bgm:
        agraph = (
            f"[{bgm_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            f"volume={args.bgm_volume}[bgm];"
            f"[{voice_idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
            f"volume={args.voice_volume}[voice];"
            f"[bgm][voice]amix=inputs=2:duration=longest:dropout_transition=0[aout]"
        )

    # ---- subtitles: render through ASS so the font size is exact pixels
    if args.subtitles:
        srt = Path(args.subtitles).expanduser().resolve()
        if not srt.exists():
            print(f"[mix] subtitle file not found: {srt}", file=sys.stderr)
            return 1
        # ~4.5% of output width reads well on vertical short video
        fsize = args.subtitle_size or max(18, round(args.width * 0.045))
        ass = tmp / "subs.ass"
        srt_to_ass(srt, ass, args.width, args.height, fsize)
        esc = str(ass).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        vgraph = f"{vgraph};{vlabel}ass='{esc}'[{vout}]"
        vlabel = f"[{vout}]"

    # Cap the output at the video length. Without this, a long BGM track
    # would extend the file past the last frame (and past the subtitles).
    target_len = sum(durs) - n_ov
    if voice_len > target_len:
        target_len = voice_len

    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *inputs, *extra,
           "-filter_complex", f"{vgraph};{agraph}",
           "-map", vlabel, "-map", "[aout]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", "-t", f"{target_len:.3f}", str(out)]

    print("[mix] encoding ->", out)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-4000:], file=sys.stderr)
        print("[mix] FAILED", file=sys.stderr)
        return 2

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    total = sum(durs)
    kb = out.stat().st_size / 1024
    print(f"[mix] OK -> {out}  ({kb / 1024:.1f} MB, ~{total:.1f}s of clips)")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
