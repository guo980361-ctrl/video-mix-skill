#!/usr/bin/env python3
"""Free TTS + subtitles via Microsoft Edge TTS (no API key).

Verified 2026-11-18: `edge-tts` 7.x produces Chinese audio plus a
SentenceBoundary metadata file in one pass, with `offset`/`duration` in
**microseconds**. That timing data lets the montage burn subtitles in sync
without any extra ASR pass.

Usage:
    python tts.py --text "大家好" --out voice.mp3
    python tts.py --script script.txt --out voice.mp3 --srt voice.srt
    python tts.py --text "..." --out v.mp3 --voice yunxi --rate +10%
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vmix_common import install_deps, out_path  # noqa: E402

# Curated Chinese voices with their best-fit use case.
VOICES = {
    "xiaoxiao": ("zh-CN-XiaoxiaoNeural", "女声，温暖，适合旁白/讲解"),
    "xiaoyi": ("zh-CN-XiaoyiNeural", "女声，活泼，适合口播/种草"),
    "yunxi": ("zh-CN-YunxiNeural", "男声，阳光，适合剧情/口播"),
    "yunjian": ("zh-CN-YunjianNeural", "男声，激昂，适合体育/燃向"),
    "yunyang": ("zh-CN-YunyangNeural", "男声，播音腔，适合新闻/知识"),
    "yunxia": ("zh-CN-YunxiaNeural", "男声，卡通，适合儿童/动画"),
}


def _ts(ticks: int) -> str:
    """edge-tts 7.x reports time in 100-nanosecond ticks (10_000_000 = 1s).

    Verified 2026-11-18: a 4.63s clip reported a final boundary ending at
    45750000 ticks = 4.575s. Dividing by 1_000_000 (the old microsecond
    assumption) produced a 45s timeline for a 4.6s clip.
    """
    ms = ticks // 10_000
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def metadata_to_srt(jsonl_path: Path, srt_path: Path) -> Path:
    """Convert edge-tts SentenceBoundary metadata into SRT."""
    entries = []
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") not in ("SentenceBoundary", "WordBoundary"):
            continue
        text = (d.get("text") or "").strip()
        if not text:
            continue
        start = int(d.get("offset", 0))
        dur = int(d.get("duration", 0))
        entries.append((start, start + max(dur, 1), text))

    entries.sort(key=lambda e: e[0])
    blocks = []
    for i, (s, e, t) in enumerate(entries, 1):
        blocks.append(f"{i}\n{_ts(s)} --> {_ts(e)}\n{t}\n")
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text("\n".join(blocks), encoding="utf-8")
    return srt_path


def synth(text: str, voice: str, rate: str, pitch: str,
          media: Path, meta: Path) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)

    async def run():
        await communicate.save(str(media), str(meta))

    asyncio.run(run())


def main() -> int:
    ap = argparse.ArgumentParser(description="Free TTS with subtitles")
    ap.add_argument("--text", help="text to speak")
    ap.add_argument("--script", help="file containing the text")
    ap.add_argument("--out", required=True, help="output mp3 path")
    ap.add_argument("--srt", help="optional output srt path "
                                  "(default: alongside --out)")
    ap.add_argument("--voice", default="xiaoxiao",
                    help="short name or full edge-tts voice id")
    ap.add_argument("--rate", default="+0%")
    ap.add_argument("--pitch", default="+0Hz")
    args = ap.parse_args()

    install_deps(quiet=True)
    if args.text:
        text = args.text
    elif args.script:
        text = Path(args.script).expanduser().read_text(encoding="utf-8")
    else:
        print("[tts] need --text or --script", file=sys.stderr)
        return 1

    voice = VOICES.get(args.voice, (args.voice, ""))[0]

    out = out_path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # --out and --srt resolve against the same base, so
    # "--out a/v.mp3 --srt a/v.srt" lands both in the same folder.
    srt = Path(args.srt) if args.srt else out.with_suffix(".srt")
    if not srt.is_absolute():
        srt = out_path(srt)

    meta = out.with_suffix(".jsonl")
    print(f"[tts] voice={voice} chars={len(text)}")
    synth(text, voice, args.rate, args.pitch, out, meta)
    metadata_to_srt(meta, srt)
    meta.unlink(missing_ok=True)

    kb = out.stat().st_size / 1024
    cues = sum(1 for ln in srt.read_text(encoding="utf-8").splitlines()
               if "-->" in ln)
    print(f"[tts] OK -> {out}  ({kb:.0f} KB)")
    print(f"[tts] subtitles -> {srt}  ({cues} cues)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
