#!/usr/bin/env python3
"""Validate a JianYing draft folder before handing it to JianYing.

A malformed draft does not produce an error dialog in JianYing -- it
silently shows an empty timeline, which is much harder to debug. This
checks the structural invariants that matter:

  * both files exist and parse as JSON
  * every segment's material_id resolves to a material
  * every extra_material_refs entry resolves to a material
  * segments on a track do not overlap
  * segment timings stay inside their source material's duration
  * the draft duration matches the longest track
  * the platform block claims a version JianYing will accept

Usage:
    python jianying_check.py ./jianying-out/我的混剪
    python jianying_check.py --all          # check every draft found
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SEC = 1_000_000

# Material buckets whose entries carry an "id" we can resolve.
RESOLVABLE = ("videos", "audios", "texts", "stickers", "speeds",
              "transitions", "audio_fades", "material_animations",
              "video_effects", "audio_effects", "effects", "canvases",
              "chromas")


def _read_json(path: Path) -> tuple[dict | None, str | None]:
    """Read a draft JSON file, decrypting it if necessary.

    Returns (data, error). JianYing encrypts draft files from 6.0 on, so
    a "corrupt" draft may simply be ciphertext.
    """
    raw = path.read_bytes()
    if raw.lstrip()[:1] == b"{":
        try:
            return json.loads(raw.decode("utf-8")), None
        except Exception as exc:
            return None, f"不是合法 JSON: {exc}"
    # Looks encrypted -- try to decrypt.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from jianying_crypto import decrypt_bytes
        return json.loads(decrypt_bytes(raw).decode("utf-8")), None
    except ImportError:
        return None, ("是加密格式（需要 pip install pycryptodome 才能校验）")
    except Exception as exc:
        return None, f"是加密格式且无法解密: {exc}"


def check_draft(folder: Path) -> list[str]:
    """Return a list of problems; empty means the draft looks sound."""
    problems: list[str] = []

    # 6.0+ reads draft_info.json; 5.9 reads draft_content.json.
    candidates = [folder / "draft_info.json", folder / "draft_content.json"]
    content_path = next((p for p in candidates if p.exists()), None)
    meta_path = folder / "draft_meta_info.json"

    if content_path is None:
        problems.append("缺少 draft_info.json（6.0+）/ draft_content.json（5.9）")
        return problems
    if not meta_path.exists():
        problems.append(f"缺少 {meta_path.name}（剪映可能不显示这个草稿）")

    content, err = _read_json(content_path)
    if content is None:
        problems.append(f"{content_path.name} 读取失败: {err}")
        return problems

    if meta_path.exists():
        _, meta_err = _read_json(meta_path)
        if meta_err:
            problems.append(f"{meta_path.name} 读取失败: {meta_err}")

    # ---- collect material ids
    materials = content.get("materials", {})
    known: set[str] = set()
    for bucket in RESOLVABLE:
        for entry in materials.get(bucket, []) or []:
            if isinstance(entry, dict) and "id" in entry:
                known.add(entry["id"])
    # Speeds/fades live under their own keys but are referenced by id too.
    for bucket in ("speeds", "transitions", "audio_fades"):
        for entry in materials.get(bucket, []) or []:
            if isinstance(entry, dict) and "id" in entry:
                known.add(entry["id"])

    tracks = content.get("tracks", [])
    if not tracks:
        problems.append("没有任何轨道，时间线会是空的")

    track_ends: dict[str, int] = {}
    video_end = 0
    for track in tracks:
        name = track.get("name", "?")
        segs = track.get("segments", [])
        if not segs:
            problems.append(f"轨道 '{name}' 没有片段")
        ordered = sorted(segs, key=lambda s: s["target_timerange"]["start"])
        track_end = 0
        for seg in segs:
            mid = seg.get("material_id")
            if mid not in known:
                problems.append(
                    f"轨道 '{name}' 的片段引用了不存在的素材 id: {mid}")
            for ref in seg.get("extra_material_refs", []) or []:
                if ref not in known:
                    problems.append(
                        f"轨道 '{name}' 的片段引用了不存在的附加素材: {ref}")
            tr = seg.get("target_timerange") or {}
            start = tr.get("start", 0)
            dur = tr.get("duration", 0)
            if start < 0:
                problems.append(f"轨道 '{name}' 有负的起始时间 {start}")
            if dur <= 0:
                problems.append(f"轨道 '{name}' 有零长片段")
            # A segment far shorter than a frame means the duration was
            # written in the wrong unit (ms instead of us, say).
            if 0 < dur < 10_000 and track.get("type") in ("video", "audio"):
                problems.append(
                    f"轨道 '{name}' 有异常短的片段 ({dur} 微秒)——"
                    f"时长单位可能写错了")
            track_end = max(track_end, start + dur)
            # source range must fit inside the material
            src = seg.get("source_timerange")
            if src:
                mat = None
                for bucket in ("videos", "audios"):
                    for entry in materials.get(bucket, []) or []:
                        if entry.get("id") == mid:
                            mat = entry
                            break
                if mat:
                    mat_len = mat.get("duration", 0)
                    if src["start"] + src["duration"] > mat_len + 1000:
                        problems.append(
                            f"轨道 '{name}': 片段取用范围 "
                            f"{(src['start'] + src['duration']) / SEC:.3f}s "
                            f"超出素材 '{mat.get('material_name')}' 的 "
                            f"{mat_len / SEC:.3f}s")

        for a, b in zip(ordered, ordered[1:]):
            a_end = a["target_timerange"]["start"] + a["target_timerange"]["duration"]
            b_start = b["target_timerange"]["start"]
            if a_end > b_start:
                problems.append(
                    f"轨道 '{name}' 片段重叠: {a_end / SEC:.3f}s > "
                    f"{b_start / SEC:.3f}s")

        track_ends[name] = track_end
        if track.get("type") == "video":
            video_end = max(video_end, track_end)

    duration = content.get("duration", 0)
    # The timeline length is defined by the main video track; the other
    # tracks may legitimately be shorter (a 5 s voice clip under a 30 s
    # video is normal) but must never reach past the end.
    if video_end and abs(duration - video_end) > 10_000:
        problems.append(
            f"草稿时长 {duration / SEC:.3f}s 与主视频轨长度 "
            f"{video_end / SEC:.3f}s 不一致")
    for name, end in track_ends.items():
        if duration and end > duration + 10_000:
            problems.append(
                f"轨道 '{name}' 超出草稿时长 "
                f"({end / SEC:.3f}s > {duration / SEC:.3f}s)")

    # ---- media files must still be where the draft says they are
    for bucket in ("videos", "audios"):
        for entry in materials.get(bucket, []) or []:
            path = entry.get("path")
            if path and not Path(path).exists():
                problems.append(f"素材文件不存在: {path}")

    platform = content.get("platform", {})
    version = platform.get("app_version", "")
    if version and not version.startswith(("5.9", "6.", "7.", "8.", "9.",
                                           "10.", "11.")):
        problems.append(f"app_version 异常: {version}")

    return problems


def find_drafts(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir()
                  if p.is_dir() and (p / "draft_content.json").exists())


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate a JianYing draft")
    ap.add_argument("folder", nargs="?", help="draft folder to check")
    ap.add_argument("--all", action="store_true",
                    help="check every draft under the given folder")
    args = ap.parse_args()

    if not args.folder:
        print(__doc__)
        return 2

    root = Path(args.folder).expanduser().resolve()
    if not root.exists():
        print(f"not found: {root}", file=sys.stderr)
        return 1

    targets = find_drafts(root) if args.all else [root]
    if not targets:
        print(f"no draft found under {root}", file=sys.stderr)
        return 1

    failed = 0
    for folder in targets:
        problems = check_draft(folder)
        if problems:
            failed += 1
            print(f"FAIL {folder}")
            for p in problems:
                print(f"  - {p}")
        else:
            print(f"OK   {folder}")

    print(f"\n{len(targets) - failed}/{len(targets)} draft(s) passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
