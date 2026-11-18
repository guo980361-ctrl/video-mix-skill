#!/usr/bin/env python3
"""Export a JianYing-importable asset pack, and a Premiere-compatible XML.

This is the version-proof sibling of `jianying_draft.py`. A JianYing draft
is a private, version-sensitive format (encrypted since 6.0), so it is not
always the best way to hand work over. This script instead produces plain
files that any editor can ingest:

  pack/
    clips/clip01.mp4 ...   the montage sources, in order
    subtitles.srt          subtitles with the same timings
    voice.mp3              narration
    bgm.wav                music
    manifest.json          timings, so a human (or an agent) can rebuild
                           the cut in seconds
    premiere.xml           an FCPXML/Premiere sequence with the same cut

Use `--draft` on top of this to also emit a JianYing draft next to the pack.

Usage
-----
    python jianying_pack.py --clips a.mp4 b.mp4 c.mp4 \
        --voice voice.mp3 --bgm music.wav --subtitles voice.srt \
        --name "我的混剪" --out ./jianying-pack
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vmix_common import ensure_dir, install_deps, out_path  # noqa: E402
from jianying_draft import (SEC, probe_media, parse_srt,  # noqa: E402
                            build_draft)

# The FCPXML sequence format that Premiere and DaVinci Resolve read.
FCPXML_NS = "http://www.futuresoft.net/FoundationXML/2019"
ASSET_NS = "http://www.futuresoft.net/FoundationXML/2019/Asset"


def timecode(seconds: float, fps: int) -> str:
    total_frames = int(round(seconds * fps))
    frames = total_frames % fps
    total_seconds = total_frames // fps
    secs = total_seconds % 60
    minutes = (total_seconds // 60) % 60
    hours = total_seconds // 3600
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{frames:02d}"


def rat(seconds: float) -> str:
    """FCPXML rational time, e.g. "12/30s". Resolve rejects plain decimals."""
    return f"{int(round(seconds * 1000))}/1000s"


def build_fcpxml(clips: list[Path], voice: Path | None, bgm: Path | None,
                 width: int, height: int, fps: int) -> str:
    """Build an FCPXML sequence that Premiere / Resolve can import.

    Structure (FCPXML 1.9):

        resources
          format        -- the sequence's video format
          asset         -- one per media file
        library
          event
            project
              sequence
                spine
                  asset-clip   -- one per timeline item

    All timings use rational time ("1.5s" style breaks Resolve).
    """
    # FCPXML puts the core elements (fcpxml, resources, asset, format,
    # sequence, spine, asset-clip) in the *default* namespace. Only the
    # Apple "asset" elements use the second namespace, and in practice
    # Resolve and Premiere expect everything unprefixed -- so register the
    # FoundationXML namespace as default and drop the asset prefix entirely.
    ET.register_namespace("", FCPXML_NS)

    resources = ET.Element(f"{{{FCPXML_NS}}}resources")
    fmt_id = "r-format-1"
    fmt = ET.SubElement(resources, f"{{{FCPXML_NS}}}format",
                        id=fmt_id, name=f"FFVideoFormat{height}p{fps}",
                        frameDuration=f"1/{fps}s",
                        width=str(width), height=str(height),
                        colorSpace="1-1-1 (Rec. 709)")
    ET.SubElement(fmt, f"{{{FCPXML_NS}}}samplecharacteristics")

    asset_ids: dict[Path, str] = {}

    def add_asset(path: Path, has_video: bool, has_audio: bool,
                  duration: float) -> str:
        aid = f"r-asset-{uuid_short(path)}"
        asset_ids[path] = aid
        asset = ET.SubElement(resources, f"{{{FCPXML_NS}}}asset",
                              id=aid, name=path.name, start="0s",
                              duration=rat(duration),
                              hasVideo="1" if has_video else "0",
                              hasAudio="1" if has_audio else "0",
                              format=fmt_id, audioSources="1",
                              audioChannels="2", audioRate="48k")
        ET.SubElement(asset, f"{{{FCPXML_NS}}}uid").text = \
            f"urn:uuid:{aid}"
        rep = ET.SubElement(asset, f"{{{FCPXML_NS}}}representation")
        media = ET.SubElement(rep, f"{{{FCPXML_NS}}}media")
        ET.SubElement(media, f"{{{FCPXML_NS}}}src").text = \
            f"file://{path}"
        return aid

    # ---- assets
    clip_info = []
    cursor = 0.0
    for clip in clips:
        mat = probe_media(clip)
        dur = mat.duration_us / SEC
        add_asset(clip, True, mat.has_audio, dur)
        clip_info.append((clip, cursor, dur))
        cursor += dur

    total_duration = cursor
    if voice:
        add_asset(voice, False, True, probe_media(voice).duration_us / SEC)
    if bgm:
        add_asset(bgm, False, True, probe_media(bgm).duration_us / SEC)

    # ---- library / event / project / sequence
    library = ET.Element(f"{{{FCPXML_NS}}}library")
    event = ET.SubElement(library, f"{{{FCPXML_NS}}}event", name="video-mix")
    project = ET.SubElement(event, f"{{{FCPXML_NS}}}project", name="video-mix")
    sequence = ET.SubElement(project, f"{{{FCPXML_NS}}}sequence",
                             duration=rat(total_duration),
                             format=fmt_id, tcStart="0s",
                             tcFormat="NDF", audioLayout="stereo",
                             audioRate="48k")
    media = ET.SubElement(sequence, f"{{{FCPXML_NS}}}media")
    video = ET.SubElement(media, f"{{{FCPXML_NS}}}video", format=fmt_id)
    ET.SubElement(video, f"{{{FCPXML_NS}}}samplecharacteristics")
    audio_el = ET.SubElement(media, f"{{{FCPXML_NS}}}audio", format=fmt_id)
    ET.SubElement(audio_el, f"{{{FCPXML_NS}}}samplecharacteristics")
    ET.SubElement(audio_el, f"{{{FCPXML_NS}}}channelcount").text = "2"

    spine = ET.SubElement(sequence, f"{{{FCPXML_NS}}}spine")

    # Video clips on the base lane, back to back.
    for clip, start, dur in clip_info:
        ET.SubElement(spine, f"{{{FCPXML_NS}}}asset-clip",
                      name=clip.name,
                      ref=asset_ids[clip],
                      offset=rat(start),
                      start="0s",
                      duration=rat(dur),
                      enabled="1",
                      audioRole="dialogue")

    # Voice and BGM on their own lanes above the video.
    lane = 1
    for media_path, role in ((voice, "dialogue"), (bgm, "music")):
        if not media_path:
            continue
        dur = probe_media(media_path).duration_us / SEC
        ET.SubElement(spine, f"{{{FCPXML_NS}}}asset-clip",
                      name=media_path.name,
                      ref=asset_ids[media_path],
                      offset="0s",
                      start="0s",
                      duration=rat(min(dur, total_duration)),
                      lane=str(lane),
                      enabled="1",
                      audioRole=role)
        lane += 1

    root = ET.Element(f"{{{FCPXML_NS}}}fcpxml", version="1.9")
    root.append(resources)
    root.append(library)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def uuid_short(path: Path) -> str:
    import hashlib
    return hashlib.md5(str(path).encode()).hexdigest()[:8]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Export a JianYing-importable asset pack")
    ap.add_argument("--clips", nargs="+", required=True)
    ap.add_argument("--voice")
    ap.add_argument("--bgm")
    ap.add_argument("--subtitles")
    ap.add_argument("--name", default="混剪素材包")
    ap.add_argument("--out", help="输出目录，素材包写进 <目录>/<名称>（默认 ./jianying-pack）")
    ap.add_argument("--draft", action="store_true",
                    help="also emit a JianYing draft alongside the pack")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--fps", type=int, default=30)
    args = ap.parse_args()

    install_deps(quiet=True)

    clips = [Path(c).expanduser().resolve() for c in args.clips]
    missing = [str(c) for c in clips if not c.exists()]
    if missing:
        print(f"[pack] missing clips: {missing}", file=sys.stderr)
        return 1

    voice = Path(args.voice).expanduser().resolve() if args.voice else None
    bgm = Path(args.bgm).expanduser().resolve() if args.bgm else None
    subs = Path(args.subtitles).expanduser().resolve() if args.subtitles else None

    # --out is the *parent* directory, matching the default layout
    # (./jianying-pack/<name>).  A path that already ends with the pack
    # name is used as-is, so it is never doubled.
    base = out_path(args.out) if args.out else (Path.cwd() / "jianying-pack")
    base = base if base.name == args.name else base / args.name
    pack = ensure_dir(base)
    clips_dir = ensure_dir(pack / "clips")

    manifest = {
        "name": args.name,
        "canvas": {"width": args.width, "height": args.height,
                   "fps": args.fps},
        "clips": [],
        "voice": None,
        "bgm": None,
        "subtitles": [],
    }

    cursor = 0.0
    for i, clip in enumerate(clips):
        mat = probe_media(clip)
        dur = mat.duration_us / SEC
        dest = clips_dir / f"clip{i + 1:02d}{clip.suffix}"
        if clip.resolve() != dest.resolve():
            shutil.copy2(clip, dest)
        manifest["clips"].append({
            "index": i + 1,
            "file": f"clips/{dest.name}",
            "duration": round(dur, 3),
            "timeline_start": round(cursor, 3),
            "timeline_end": round(cursor + dur, 3),
        })
        cursor += dur

    if voice and voice.exists():
        dest = pack / f"voice{voice.suffix}"
        if voice.resolve() != dest.resolve():
            shutil.copy2(voice, dest)
        manifest["voice"] = {
            "file": dest.name,
            "duration": round(probe_media(voice).duration_us / SEC, 3),
        }

    if bgm and bgm.exists():
        dest = pack / f"bgm{bgm.suffix}"
        if bgm.resolve() != dest.resolve():
            shutil.copy2(bgm, dest)
        manifest["bgm"] = {
            "file": dest.name,
            "duration": round(probe_media(bgm).duration_us / SEC, 3),
        }

    if subs and subs.exists():
        dest = pack / "subtitles.srt"
        shutil.copy2(subs, dest)
        for start, end, body in parse_srt(subs):
            manifest["subtitles"].append({
                "start": round(start / SEC, 3),
                "end": round(end / SEC, 3),
                "text": body,
            })

    (pack / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # Premiere XML: a sequence other NLEs can open.
    try:
        xml = build_fcpxml(clips, voice, bgm, args.width, args.height, args.fps)
        (pack / "premiere.xml").write_text(xml, encoding="utf-8")
    except Exception as exc:
        print(f"[pack] premiere.xml skipped: {exc}", file=sys.stderr)

    readme = f"""# {args.name} —— 剪映素材包

## 怎么用

**方式一：剪映专业版**
1. 打开剪映 → 新建草稿 → 导入 `clips/` 里的所有视频，按文件名顺序拖到主轨。
2. 字幕：点「文本 → 导入字幕」，选 `subtitles.srt`。
3. 音频：把 `{manifest['bgm']['file'] if manifest['bgm'] else 'bgm'}` 拖到音频轨当背景音乐，
   把 `{manifest['voice']['file'] if manifest['voice'] else 'voice'}` 拖到另一条音频轨当旁白。
4. 时间点见 `manifest.json`，每个片段的 `timeline_start` / `timeline_end` 已标好。

**方式二：Premiere Pro / DaVinci Resolve**
导入 `premiere.xml` 即可得到同样的一条时间线。

**方式三：直接用成片**
如果只要成片，用本 skill 的 `mix.py` 一步合成，不需要剪映。

## 文件说明

| 文件 | 说明 |
|---|---|
| `clips/` | 素材片段，文件名即播放顺序 |
| `subtitles.srt` | 字幕，时间轴已对齐 |
| `voice.*` | 旁白音轨 |
| `bgm.*` | 背景音乐 |
| `manifest.json` | 每个片段的时长与时间轴位置 |
| `premiere.xml` | Premiere / Resolve 用的工程文件 |

## 想直接得到可编辑的剪映草稿？

用 `jianying_draft.py --install`，它会生成真正的剪映草稿目录，
打开剪映就能在首页看到，时间轴、字幕轨、旁白轨、BGM 轨都排好了。

> 注意：剪映 6.0 以后草稿是加密格式，生成的草稿主要适配 5.9 / 10.x。
> 打不开时用上面的方式一手动导入即可，素材和字幕都是现成的。
"""
    (pack / "README.md").write_text(readme, encoding="utf-8")

    if args.draft:
        content, meta, _ = build_draft(
            clips, voice, bgm, subs, args.width, args.height, args.fps,
            "叠化", 0.5, None, 0.2, 1.0, 0.5, 0.8, 8.0, -0.8, args.name)
        draft_dir = ensure_dir(pack / "jianying-draft")
        content_bytes = json.dumps(content, ensure_ascii=False,
                                   indent=4).encode("utf-8")
        meta_bytes = json.dumps(meta, ensure_ascii=False,
                                 indent=4).encode("utf-8")
        # Both names, same content: 6.0+ reads draft_info.json, 5.9 reads
        # draft_content.json.
        for filename in ("draft_info.json", "draft_content.json"):
            (draft_dir / filename).write_bytes(content_bytes)
        (draft_dir / "draft_meta_info.json").write_bytes(meta_bytes)

    print(f"[pack] OK -> {pack}")
    for item in manifest["clips"]:
        print(f"[pack] clip {item['index']}: {item['timeline_start']:.2f}s -> "
              f"{item['timeline_end']:.2f}s  {item['file']}")
    print(str(pack))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
