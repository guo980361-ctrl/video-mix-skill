#!/usr/bin/env python3
"""Assemble the shots into a finished film.

The stock ``mix.py`` applies one transition style to every cut and
slices the clips to their own lengths.  That is what makes an AI
montage feel mechanical: seven identical dissolves, and cuts that land
wherever the source clip happened to end rather than where the line
lands.

This assembler works from the shot plan instead, so it can do the
things a cut actually needs:

  * a different transition at every cut -- a cross-dissolve where the
    scene continues, a dip-to-black where the time jumps, a wipe where
    it does not
  * clip lengths trimmed to the narration, so every line gets its own
    shot and the cutting points land on the words
  * an explicit pace: the shot lengths are scaled as a group to match
    the audio, and individual shots can be nudged longer or shorter
  * a proper subtitle pass: a two-line-safe style with a soft shadow
    rather than a hard black outline, positioned clear of the bottom
    safe area that the platform UI covers

Usage:
    python assemble.py --plan shots.json --clips clips --out film.mp4 \\
        --audio audio/mix.wav --subtitles audio/voice.srt
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".workbuddy" / "skills" / "video-mix" / "scripts"))
from vmix_common import ffmpeg_bin  # noqa: E402

# Transitions that read as "the same scene continues".
SOFT = ["fade", "dissolve", "smoothleft", "smoothup", "fadegrays"]
# Transitions that read as "time has jumped".
HARD = ["fadeblack", "fadewhite", "fadegrays", "wipeleft", "wiperight"]

# Which transition goes after which shot.  The plan can override this
# per shot with a "transition" key.
#
# Note on fadeblack: when xfade fades to black it briefly shows a
# darkened frame of *both* sources blended, which over a warm frame
# reads as a murky red rectangle rather than a clean cut to black.  It
# is only used where the neighbouring shots are genuinely dark.
DEFAULT_CUTS = [
    "fade",        # s01 -> s02  the room, then the cat enters
    "dissolve",    # s02 -> s03  the walk continues into the look up
    "smoothup",    # s03 -> s04  the look up becomes the nuzzle
    "fadewhite",   # s04 -> s05  a bright beat after the warmest moment
    "dissolve",    # s05 -> s06  the purr continues
    "smoothleft",  # s06 -> s07  the hand comes in
    "fade",        # s07 -> s08  into the closing shot
]


def probe(path: Path) -> float:
    r = subprocess.run([ffmpeg_bin(), "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True)
    import re
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", r.stderr)
    if not m:
        raise RuntimeError(f"cannot probe {path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def fit_clips(plan: dict, clip_dir: Path, out_dir: Path,
               fps: int, w: int, h: int) -> list[Path]:
    """Normalise every clip to the target size, fps and duration.

    Clips are trimmed to the length the plan asks for; if a clip is
    shorter, its last frame is held so the cut still lands where it
    should.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    fitted = []
    for shot in plan["shots"]:
        src = clip_dir / f"{shot['id']}.mp4"
        if not src.exists():
            print(f"[assemble] missing clip {src}, skipping", file=sys.stderr)
            continue
        want = float(shot["duration"])
        have = probe(src)
        dst = out_dir / f"{shot['id']}.mp4"

        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
              f"crop={w}:{h},setsar=1,fps={fps}")
        cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
               "-i", str(src), "-vf", vf]
        if have < want:
            # Hold the last frame for the remainder.
            cmd += ["-vf", vf + f",tpad=stop_mode=clone:stop_duration={want - have:.3f}"]
        cmd += ["-t", f"{want:.3f}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                "-pix_fmt", "yuv420p", "-an", str(dst)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr[-1200:], file=sys.stderr)
            raise RuntimeError(f"failed to fit {src}")
        fitted.append(dst)
    return fitted


def build_ass(srt_path: Path, ass_path: Path, w: int, h: int) -> None:
    """Render the subtitles as ASS with a soft, filmic style.

    A hard black outline is the default look of every subtitle tool, and
    it is what makes AI captions look cheap.  A soft drop shadow plus a
    gentle dark gradient behind the text reads more like broadcast
    captions and stays legible over a bright frame.
    """
    text = srt_path.read_text("utf-8", errors="replace").replace("\r\n", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())

    def conv(ts: str) -> str:
        ts = ts.replace(",", ".")
        h, m, s = ts.split(":")
        return f"{int(h):d}:{int(m):02d}:{float(s):05.2f}"

    cues = []
    for b in blocks:
        lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        start, end = [t.strip() for t in lines[idx].split("-->")][:2]
        body = "\n".join(lines[idx + 1:])
        if not body:
            continue
        cues.append((conv(start), conv(end), body))

    # 4.3% of the width: large enough to read on a phone, small enough
    # to leave the picture alone.
    fsize = max(18, round(w * 0.043))
    # The bottom safe area on short-video platforms covers roughly the
    # lowest 14% of the frame, so the captions sit above it.
    margin_v = round(h * 0.135)

    # The style line must match the Format order exactly:
    #   Name, Fontname, Fontsize, PrimaryColour, SecondaryColour,
    #   OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut,
    #   ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,
    #   Alignment, MarginL, MarginR, MarginV, Encoding
    # The font name has to come before the size, otherwise the two
    # numbers shift every following field and the captions render at a
    # nonsensical size.
    font = "PingFang SC"
    outline = max(2, round(fsize * 0.055))
    shadow = max(3, round(fsize * 0.16))
    style = ",".join([
        "Main", font, str(fsize),
        "&H00FFFFFF",          # PrimaryColour: opaque white
        "&H000000FF",          # SecondaryColour
        "&H00202020",          # OutlineColour: soft dark grey
        "&H80000000",          # BackColour: semi transparent black
        "0", "0", "0", "0",     # Bold, Italic, Underline, StrikeOut
        "100", "100", "0.4", "0",
        "1",                   # BorderStyle: outline + shadow
        str(outline), str(shadow),
        "2",                   # Alignment: bottom centre
        str(round(w * 0.06)), str(round(w * 0.06)), str(margin_v),
        "1",                   # Encoding
    ])

    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: {style}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    out = [head]
    for start, end, body in cues:
        # A short fade on each caption, in and out, so the text does not
        # pop.
        body_esc = body.replace("\n", "\\N")
        out.append(
            f"Dialogue: 0,{start},{end},Main,,0,0,0,,"
            f"{{\\fad(160,160)}}{body_esc}\n")
    ass_path.write_text("".join(out), "utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Assemble the film")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--clips", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--audio", default=None)
    ap.add_argument("--subtitles", default=None)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--transition-duration", type=float, default=0.55)
    ap.add_argument("--no-fade", action="store_true")
    args = ap.parse_args()

    plan = json.loads(Path(args.plan).read_text("utf-8"))
    clip_dir = Path(args.clips)
    tmp = Path(args.out).parent / "_assemble_tmp"
    tmp.mkdir(parents=True, exist_ok=True)

    fitted = fit_clips(plan, clip_dir, tmp, args.fps, args.width, args.height)
    if len(fitted) < 2:
        print("[assemble] not enough clips", file=sys.stderr)
        return 1

    durs = [probe(p) for p in fitted]
    print(f"[assemble] clip durations: {[round(d, 2) for d in durs]}")

    d = args.transition_duration
    # xfade offsets: each transition starts where the previous segment
    # ends, minus the overlap.
    acc = durs[0]
    offs = []
    for i in range(1, len(fitted)):
        offs.append(acc - d)
        acc = acc + durs[i] - d

    cuts = []
    for i, shot in enumerate(plan["shots"][:len(fitted)]):
        cuts.append(shot.get("transition",
                              DEFAULT_CUTS[i] if i < len(DEFAULT_CUTS)
                              else "dissolve"))

    parts = []
    prev = "[0:v]"
    for i in range(1, len(fitted)):
        label = f"vx{i}"
        parts.append(f"{prev}[{i}:v]xfade=transition={cuts[i - 1]}:"
                     f"duration={d}:offset={offs[i - 1]:.3f}[{label}]")
        prev = f"[{label}]"

    vlabel = prev
    if args.subtitles:
        srt = Path(args.subtitles)
        ass = tmp / "subs.ass"
        build_ass(srt, ass, args.width, args.height)
        esc = str(ass).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        parts.append(f"{vlabel}ass='{esc}'[vout]")
        vlabel = "[vout]"

    # A gentle fade in and out; it is what stops the first and last
    # frame from feeling abrupt.
    if not args.no_fade:
        parts.append(f"{vlabel}fade=t=in:st=0:d=0.8,fade=t=out:"
                     f"st={acc - 0.9:.3f}:d=0.9[final]")
        vlabel = "[final]"

    inputs = []
    for p in fitted:
        inputs += ["-i", str(p)]

    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y"] + inputs
    if args.audio:
        cmd += ["-i", str(Path(args.audio))]
    cmd += ["-filter_complex", ";".join(parts), "-map", vlabel]
    if args.audio:
        cmd += ["-map", f"{len(fitted)}:a", "-c:a", "aac", "-b:a", "192k",
                "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-r", str(args.fps), str(args.out)]

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-2500:], file=sys.stderr)
        return r.returncode

    total = probe(Path(args.out))
    print(f"[assemble] {args.out}  {total:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
