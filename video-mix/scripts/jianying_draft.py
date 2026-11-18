#!/usr/bin/env python3
"""Generate a JianYing (剪映专业版) draft from a montage plan.

The draft is a folder holding two files:

    draft_content.json    the timeline (tracks + materials)
    draft_meta_info.json  metadata (name, cover, duration, ...)

which you drop into JianYing's draft directory to get an editable project:
every clip is a separate segment, subtitles are real text tracks, and the
voice/BGM are separate audio tracks with fades and ducking already applied.

Design notes
------------
* Zero third-party dependencies. Media metadata is read with MediaInfo when
  available (pip install pymediainfo) and falls back to ffprobe, then to
  parsing ffmpeg's stderr. Image size is read with the stdlib only.
* The JSON layout matches what JianYing itself writes, verified field by
  field against a draft produced by pyJianYingDraft (Apache-2.0).
* Time is always in **microseconds** -- 1 second == 1_000_000. This is the
  single most common source of "the timeline does not line up" bugs.
* Transitions belong to the segment **before** the cut, and are registered
  in `materials.transitions` *before* the segment is added to a track.

Usage
-----
    python jianying_draft.py --clips a.mp4 b.mp4 c.mp4 \
        --voice voice.mp3 --bgm music.wav --subtitles voice.srt \
        --name "我的混剪" --out ./jianying-out

    # write straight into JianYing's draft folder
    python jianying_draft.py --clips a.mp4 --name "混剪" --install
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vmix_common import (ensure_dir, ffmpeg_bin, install_deps,  # noqa: E402
                         out_path, probe_duration)

SEC = 1_000_000
"""One second in microseconds -- JianYing's native time unit."""

# ---------------------------------------------------------------- transitions

# (name, resource_id, effect_id, default_duration_s, is_overlap)
# Free, non-VIP transitions only. A subset of the 130 free ones JianYing
# ships; enough for montage work without dragging in a large table.
TRANSITIONS = {
    "叠化": ("6724845717472416269", "322577", 0.5, True),
    "淡入淡出": ("6724845717472416269", "322577", 0.5, True),
    "故障": ("6764884502149103611", "3192025", 0.5, True),
    "信号故障": ("6749990741105389063", "3545939", 0.5, True),
    "上下翻页": ("7397337005387944457", "77055399", 1.0, True),
    "上移": ("6724846395116753416", "2917279", 0.5, True),
    "下移": ("6724849276100284942", "2917280", 0.5, True),
    "分割": ("6968372308419285540", "4211683", 0.5, True),
    "云朵": ("6955722927161479694", "2912469", 0.5, True),
    "中心旋转": ("6858191434294497805", "878914", 0.5, False),
    "冰雪结晶": ("6919367228092514824", "1017910", 0.5, False),
    "动漫云朵": ("6777178865119793678", "2911876", 0.5, False),
    "压缩": ("6751618376780485133", "4212466", 0.5, True),
    "叠加": ("6914112332205396488", "1003369", 1.0, True),
    "右移": ("6726711296063967748", "2917287", 1.0, True),
    "向上": ("6724227090872275463", "359459", 1.0, False),
    "向下": ("6724227330190873100", "359449", 1.0, False),
    "向上擦除": ("6724849456891564557", "2917281", 0.5, True),
    "向下擦除": ("6724849752921346573", "2917282", 0.5, True),
}

# ---------------------------------------------------------------- media probe


class MediaInfo:
    """Duration (us), width and height of a media file."""

    def __init__(self, duration_us: int, width: int, height: int,
                 kind: str, has_audio: bool = False):
        self.duration_us = duration_us
        self.width = width
        self.height = height
        self.kind = kind  # "video" | "photo" | "audio"
        self.has_audio = has_audio


def _probe_with_mediainfo(path: Path) -> MediaInfo | None:
    try:
        import pymediainfo
    except ImportError:
        return None
    try:
        if not pymediainfo.MediaInfo.can_parse():
            return None
        info = pymediainfo.MediaInfo.parse(str(path))
    except Exception:
        return None

    if info.video_tracks:
        vt = info.video_tracks[0]
        dur = getattr(vt, "duration", None)
        if dur is None and info.general_tracks:
            dur = getattr(info.general_tracks[0], "duration", None)
        if dur is None:
            return None
        return MediaInfo(int(float(dur) * 1000),
                         int(getattr(vt, "width", 0) or 0),
                         int(getattr(vt, "height", 0) or 0),
                         "video", bool(info.audio_tracks))

    if info.image_tracks:
        it = info.image_tracks[0]
        # JianYing treats a still image as a 3-hour clip.
        return MediaInfo(10800 * SEC,
                         int(getattr(it, "width", 0) or 0),
                         int(getattr(it, "height", 0) or 0),
                         "photo")

    if info.audio_tracks:
        at = info.audio_tracks[0]
        dur = getattr(at, "duration", None)
        if dur is None and info.general_tracks:
            dur = getattr(info.general_tracks[0], "duration", None)
        if dur is None:
            return None
        return MediaInfo(int(float(dur) * 1000), 0, 0, "audio", True)

    return None


def _probe_with_ffprobe(path: Path) -> MediaInfo | None:
    import shutil
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(out.stdout)
    except Exception:
        return None

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    dur = fmt.get("duration")
    if not dur:
        return None
    duration_us = int(float(dur) * SEC)

    for st in streams:
        if st.get("codec_type") == "video":
            return MediaInfo(duration_us,
                             int(st.get("width", 0) or 0),
                             int(st.get("height", 0) or 0),
                             "video",
                             any(s.get("codec_type") == "audio"
                                 for s in streams))
    for st in streams:
        if st.get("codec_type") == "audio":
            return MediaInfo(duration_us, 0, 0, "audio", True)
    return None


def _image_size(path: Path) -> tuple[int, int]:
    """Read an image's dimensions from its header, stdlib only.

    Handles PNG, JPEG, GIF, BMP and WebP -- enough for shot stills.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                fh.seek(16)
                w, h = struct.unpack(">II", fh.read(8))
                return int(w), int(h)
            if head[:6] in (b"GIF87a", b"GIF89a"):
                w, h = struct.unpack("<HH", head[6:10])
                return int(w), int(h)
            if head[:2] == b"BM":
                fh.seek(18)
                w, h = struct.unpack("<ii", fh.read(8))
                return abs(int(w)), abs(int(h))
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                fh.seek(26)
                if head[12:16] == b"VP8X":
                    w = int.from_bytes(fh.read(3), "little") + 1
                    h = int.from_bytes(fh.read(3), "little") + 1
                    return w, h
                if head[12:16] == b"VP8 ":
                    fh.seek(26)
                    data = fh.read(8)
                    w = int.from_bytes(data[0:2], "little") & 0x3FFF
                    h = int.from_bytes(data[2:4], "little") & 0x3FFF
                    return w, h
            if head[:2] == b"\xff\xd8":
                fh.seek(2)
                while True:
                    marker = fh.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        break
                    code = marker[1]
                    if code in (0xD8, 0xD9):
                        continue
                    length = struct.unpack(">H", fh.read(2))[0]
                    if 0xC0 <= code <= 0xCF and code not in (0xC4, 0xC8, 0xCC):
                        fh.read(1)
                        h, w = struct.unpack(">HH", fh.read(4))
                        return int(w), int(h)
                    fh.seek(length - 2, os.SEEK_CUR)
    except Exception:
        pass
    return 0, 0


def probe_media(path: Path) -> MediaInfo:
    """Probe a media file, trying MediaInfo, then ffprobe, then ffmpeg."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"素材不存在: {path}")

    info = _probe_with_mediainfo(path)
    if info is None:
        info = _probe_with_ffprobe(path)
    if info is not None:
        return info

    # Last resort: let ffmpeg tell us (duration from stderr, size via
    # ffprobe-less heuristics).
    try:
        seconds = probe_duration(path)
    except Exception as exc:
        raise RuntimeError(f"无法读取素材信息: {path} ({exc})") from exc

    suffix = path.suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic"):
        w, h = _image_size(path)
        return MediaInfo(10800 * SEC, w, h, "photo")

    proc = subprocess.run([ffmpeg_bin(), "-i", str(path)],
                          capture_output=True, text=True)
    w = h = 0
    for line in proc.stderr.splitlines():
        m = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line)
        if m and "Stream" in line:
            w, h = int(m.group(1)), int(m.group(2))
            break
    has_audio = "Audio:" in proc.stderr
    return MediaInfo(int(seconds * SEC), w, h, "video", has_audio)


# ---------------------------------------------------------------- srt


def parse_srt(path: Path) -> list[tuple[int, int, str]]:
    """Parse an SRT into (start_us, end_us, text) triples.

    Overlapping cues are trimmed rather than rejected: JianYing's own SRT
    importer clamps them, and a hard error here would block the whole
    montage over a 50 ms overlap.
    """
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    cues: list[tuple[int, int, str]] = []
    last_end = 0

    def to_us(stamp: str) -> int:
        stamp = stamp.strip().replace(",", ".")
        h, m, s = stamp.split(":")
        return int(round((int(h) * 3600 + int(m) * 60 + float(s)) * SEC))

    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        parts = lines[idx].split("-->")
        if len(parts) < 2:
            continue
        start, end = to_us(parts[0]), to_us(parts[1])
        body = " ".join(lines[idx + 1:]).strip()
        if not body:
            continue
        if start < last_end:
            start = last_end
        if end <= start:
            end = start + 1_000_000
        last_end = end
        cues.append((start, end, body))
    return cues


# ---------------------------------------------------------------- json blocks


def _new_id() -> str:
    return uuid.uuid4().hex


def _uuid_upper() -> str:
    return str(uuid.uuid4()).upper()


def video_material_json(mat: MediaInfo, path: Path, mid: str) -> dict:
    return {
        "audio_fade": None,
        "category_id": "",
        "category_name": "local",
        "check_flag": 63487,
        "crop": {
            "upper_left_x": 0.0, "upper_left_y": 0.0,
            "upper_right_x": 1.0, "upper_right_y": 0.0,
            "lower_left_x": 0.0, "lower_left_y": 1.0,
            "lower_right_x": 1.0, "lower_right_y": 1.0,
        },
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "duration": mat.duration_us,
        "height": mat.height,
        "id": mid,
        "local_material_id": "",
        "material_id": mid,
        "material_name": path.name,
        "media_path": "",
        "path": str(path),
        "type": mat.kind,
        "width": mat.width,
    }


def audio_material_json(mat: MediaInfo, path: Path, mid: str) -> dict:
    return {
        "app_id": 0,
        "category_id": "",
        "category_name": "local",
        "check_flag": 3,
        "copyright_limit_type": "none",
        "duration": mat.duration_us,
        "effect_id": "",
        "formula_id": "",
        "id": mid,
        "local_material_id": mid,
        "music_id": mid,
        "name": path.name,
        "path": str(path),
        "source_platform": 0,
        "type": "extract_music",
        "wave_points": [],
    }


def text_material_json(text: str, mid: str, size: float,
                       color: tuple[float, float, float],
                       align: int) -> dict:
    """Text material. `content` is a JSON string *inside* the JSON."""
    content = {
        "styles": [{
            "fill": {
                "alpha": 1.0,
                "content": {
                    "render_type": "solid",
                    "solid": {"alpha": 1.0, "color": list(color)},
                },
            },
            "range": [0, len(text)],
            "size": size,
            "bold": False,
            "italic": False,
            "underline": False,
            "strokes": [],
        }],
        "text": text,
    }
    return {
        "id": mid,
        "content": json.dumps(content, ensure_ascii=False),
        "typesetting": 0,
        "alignment": align,
        "letter_spacing": 0.0,
        "line_spacing": 0.02,
        "line_feed": 1,
        "line_max_width": 0.82,
        "force_apply_line_max_width": False,
        "check_flag": 7,
        "type": "subtitle",
        "global_alpha": 1.0,
    }


def speed_json(speed: float) -> dict:
    return {
        "curve_speed": None,
        "id": _new_id(),
        "mode": 0,
        "speed": speed,
        "type": "speed",
    }


def transition_json(name: str, duration_us: int) -> dict:
    resource_id, effect_id, default_s, is_overlap = TRANSITIONS[name]
    return {
        "category_id": "",
        "category_name": "",
        "duration": duration_us,
        "effect_id": effect_id,
        "id": _new_id(),
        "is_overlap": is_overlap,
        "name": name,
        "platform": "all",
        "resource_id": resource_id,
        "type": "transition",
    }


def audio_fade_json(fade_in_us: int, fade_out_us: int) -> dict:
    return {
        "id": _new_id(),
        "fade_in_duration": fade_in_us,
        "fade_out_duration": fade_out_us,
        "fade_type": 0,
        "type": "audio_fade",
    }


def _segment_base(seg_id: str, mat_id: str, start: int, dur: int) -> dict:
    return {
        "enable_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "last_nonzero_volume": 1.0,
        "reverse": False,
        "track_attribute": 0,
        "track_render_index": 0,
        "visible": True,
        "id": seg_id,
        "material_id": mat_id,
        "target_timerange": {"start": start, "duration": dur},
        "common_keyframes": [],
        "keyframe_refs": [],
    }


def media_segment_json(seg_id: str, mat_id: str, start: int, dur: int,
                       src_start: int, src_dur: int, speed: float,
                       volume: float, refs: list[str],
                       is_video: bool) -> dict:
    seg = _segment_base(seg_id, mat_id, start, dur)
    seg.update({
        "source_timerange": {"start": src_start, "duration": src_dur},
        "speed": speed,
        "volume": volume,
        "extra_material_refs": refs,
        "is_tone_modify": False,
    })
    if is_video:
        seg.update({
            "clip": {
                "alpha": 1.0,
                "flip": {"horizontal": False, "vertical": False},
                "rotation": 0.0,
                "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": 0.0, "y": 0.0},
            },
            "uniform_scale": {"on": True, "value": 1.0},
            "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
        })
    else:
        seg.update({"clip": None, "hdr_settings": None})
    return seg


def text_segment_json(seg_id: str, mat_id: str, start: int, dur: int,
                      transform_y: float) -> dict:
    seg = _segment_base(seg_id, mat_id, start, dur)
    seg.update({
        "source_timerange": None,
        "speed": 1.0,
        "volume": 1.0,
        "extra_material_refs": [],
        "is_tone_modify": False,
        "clip": {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": transform_y},
        },
        "uniform_scale": {"on": True, "value": 1.0},
    })
    return seg


def track_json(track_type: str, name: str, segments: list[dict]) -> dict:
    return {
        "attribute": 0,
        "flag": 0,
        "id": _new_id(),
        "is_default_name": name == "",
        "name": name,
        "segments": segments,
        "type": track_type,
    }


def _empty_materials() -> dict:
    return {
        "ai_translates": [], "audio_balances": [], "audio_effects": [],
        "audio_fades": [], "audio_track_indexes": [], "audios": [],
        "beats": [], "canvases": [], "chromas": [], "common_mask": [],
        "color_curves": [], "digital_humans": [], "drafts": [], "effects": [],
        "flowers": [], "green_screens": [], "handwrites": [], "hsl": [],
        "images": [], "log_color_wheels": [], "loudnesses": [],
        "manual_deformations": [], "masks": [], "material_animations": [],
        "material_colors": [], "multi_language_refs": [], "placeholders": [],
        "plugin_effects": [], "primary_color_wheels": [], "realtime_denoises": [],
        "shapes": [], "smart_crops": [], "smart_relights": [],
        "sound_channel_mappings": [], "speeds": [], "stickers": [],
        "tail_leaders": [], "text_templates": [], "texts": [], "time_marks": [],
        "transitions": [], "video_effects": [], "video_trackings": [],
        "videos": [], "vocal_beautifys": [], "vocal_separations": [],
    }


def build_content(width: int, height: int, fps: int,
                  tracks: list[dict], materials: dict,
                  duration: int) -> dict:
    """Assemble the draft_content.json payload."""
    content = {
        "canvas_config": {"height": height, "ratio": "original",
                          "width": width},
        "color_space": 0,
        "config": {
            "adjust_max_index": 1,
            "attachment_info": [],
            "combination_max_index": 1,
            "export_range": None,
            "extract_audio_last_index": 1,
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "lyrics_taskinfo": [],
            "maintrack_adsorb": True,
            "material_save_mode": 0,
            "multi_language_current": "none",
            "multi_language_list": [],
            "multi_language_main": "none",
            "multi_language_mode": "none",
            "original_sound_last_index": 1,
            "record_audio_last_index": 1,
            "sticker_max_index": 1,
            "subtitle_keywords_config": None,
            "subtitle_recognition_id": "",
            "subtitle_sync": True,
            "subtitle_taskinfo": [],
            "system_font_list": [],
            "video_mute": False,
            "zoom_info_params": None,
        },
        "cover": None,
        "create_time": 0,
        "duration": duration,
        "extra_info": None,
        "fps": float(fps),
        "free_render_index_mode_on": False,
        "group_container": None,
        "id": _uuid_upper(),
        "keyframe_graph_list": [],
        "keyframes": {
            "adjusts": [], "audios": [], "effects": [], "filters": [],
            "handwrites": [], "stickers": [], "texts": [], "videos": [],
        },
        "last_modified_platform": {
            "app_id": 3704, "app_source": "lv", "app_version": "5.9.0",
            "os": "mac" if sys.platform == "darwin" else "windows",
        },
        "materials": materials,
        "mutable_config": None,
        "name": "",
        "new_version": "110.0.0",
        "relationships": [],
        "render_index_track_mode_on": False,
        "retouch_cover": None,
        "source": "default",
        "static_cover_image_path": "",
        "time_marks": None,
        "tracks": tracks,
        "update_time": 0,
        "version": 360000,
    }
    content["platform"] = dict(content["last_modified_platform"])
    # render_index must follow the final track order.
    for index, track in enumerate(content["tracks"]):
        for seg in track["segments"]:
            seg["render_index"] = index
            seg["track_render_index"] = 0
    return content


def build_meta_info(name: str, duration: int, root_path: str,
                    cover: str = "") -> dict:
    return {
        "cloud_package_completed_time": "",
        "draft_cloud_capcut_purchase_info": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_materials": [],
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": cover,
        "draft_deeplink_url": "",
        "draft_enterprise_info": {
            "draft_enterprise_extra": "",
            "draft_enterprise_id": "",
            "draft_enterprise_name": "",
            "enterprise_material": [],
        },
        "draft_fold_path": "",
        "draft_id": _uuid_upper(),
        "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False,
        "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False,
        "draft_is_from_deeplink": "false",
        "draft_is_invisible": False,
        "draft_materials": [
            {"type": t, "value": []} for t in (0, 1, 2, 3, 6, 7, 8)
        ],
        "draft_materials_copied_info": [],
        "draft_name": name,
        "draft_new_version": "",
        "draft_removable_storage_device": "",
        "draft_root_path": root_path,
        "draft_segment_extra_info": [],
        "draft_type": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_modified": 0,
        "tm_draft_removed": 0,
        "tm_duration": duration,
    }


# ---------------------------------------------------------------- ducking


def duck_keyframes(cues: list[tuple[int, int, str]],
                   bgm_len: int) -> list[dict]:
    """Build BGM volume keyframes that dip under each narration line.

    JianYing stores audio volume keyframes as a `common_keyframes` list on
    the segment; each keyframe is (time_offset, value). The bed sits at
    `low` while someone talks and returns to `high` in the gaps.
    """
    if not cues:
        return []
    frames: list[tuple[int, float]] = []
    low, high = 0.12, 1.0
    for start, end, _ in cues:
        # Ease down just before the line starts, back up just after it ends.
        pre = max(0, start - 400_000)
        post = min(bgm_len, end + 400_000)
        frames.append((pre, high))
        frames.append((start, low))
        frames.append((end, low))
        frames.append((post, high))
    frames.sort(key=lambda f: f[0])

    # Collapse duplicates at the same offset (later value wins).
    merged: list[tuple[int, float]] = []
    for offset, value in frames:
        if merged and merged[-1][0] == offset:
            merged[-1] = (offset, value)
        else:
            merged.append((offset, value))

    kf_id = _new_id()
    return [{
        "id": kf_id,
        "keyframe_list": [
            {
                "curveType": "Line",
                "graphID": "",
                "left_control": {"x": 0.0, "y": 0.0},
                "right_control": {"x": 0.0, "y": 0.0},
                "id": _new_id(),
                "time_offset": offset,
                "values": [value],
            }
            for offset, value in merged
        ],
        "material_id": "",
        "property_type": "KFTypeVolume",
    }]


# ---------------------------------------------------------------- draft dir


def jianying_draft_root() -> Path | None:
    """Locate JianYing's draft directory on this machine."""
    home = Path.home()
    candidates = [
        home / "Movies/JianyingPro/User Data/Projects/com.lveditor.draft",
        home / "Movies/CapCut/User Data/Projects/com.lveditor.draft",
    ]
    local = os.environ.get("JIANYING_DRAFT_ROOT")
    if local:
        candidates.insert(0, Path(local).expanduser())
    for c in candidates:
        if c.is_dir():
            return c
    return None


def write_draft(folder: Path, name: str, content: dict, meta: dict,
                encrypt: bool = False) -> Path:
    """Write the files that make up a JianYing draft.

    Two naming conventions exist and they matter:

    * 5.9 and older read the timeline from ``draft_content.json``.
    * 6.0 and newer read it from ``draft_info.json``. A 6.x+ app handed a
      ``draft_content.json`` reports "draft content is corrupted".

    So we write **both** names with identical content: whichever the
    installed version looks for, it finds. On 6.0+ the file may be
    encrypted; ``encrypt=True`` produces the AES-256-GCM form that every
    version from 6.0 up understands.
    """
    folder.mkdir(parents=True, exist_ok=True)

    content_bytes = json.dumps(content, ensure_ascii=False, indent=4).encode("utf-8")
    meta_bytes = json.dumps(meta, ensure_ascii=False, indent=4).encode("utf-8")

    if encrypt:
        try:
            from jianying_crypto import encrypt_bytes
        except ImportError as exc:
            raise RuntimeError(
                "加密需要 pycryptodome，请执行: pip install pycryptodome") from exc
        content_bytes = encrypt_bytes(content_bytes)
        meta_bytes = encrypt_bytes(meta_bytes)

    for filename in ("draft_info.json", "draft_content.json"):
        (folder / filename).write_bytes(content_bytes)
    (folder / "draft_meta_info.json").write_bytes(meta_bytes)
    return folder


# ---------------------------------------------------------------- main


def build_draft(clips: list[Path], voice: Path | None, bgm: Path | None,
                subtitles: Path | None, width: int, height: int, fps: int,
                transition: str, transition_dur: float,
                clip_durations: list[float] | None,
                bgm_volume: float, voice_volume: float,
                bgm_fade_in: float, bgm_fade_out: float,
                subtitle_size: float, subtitle_y: float,
                name: str) -> tuple[dict, dict]:
    """Assemble the timeline. Returns (draft_content, draft_meta_info)."""
    materials = _empty_materials()
    tracks: list[dict] = []
    coverage: list[tuple[str, int, int]] = []

    # ---- video track
    video_segments = []
    cursor = 0
    for i, clip in enumerate(clips):
        mat = probe_media(clip)
        mid = _new_id()
        materials["videos"].append(video_material_json(mat, clip, mid))

        speed_id = _new_id()
        materials["speeds"].append({
            "curve_speed": None, "id": speed_id, "mode": 0,
            "speed": 1.0, "type": "speed",
        })
        refs = [speed_id]

        # A transition belongs to the segment *before* the cut.
        if i > 0 and transition in TRANSITIONS:
            dur_us = int(transition_dur * SEC)
            tjson = transition_json(transition, dur_us)
            materials["transitions"].append(tjson)
            refs.append(tjson["id"])

        if mat.kind == "photo":
            # --clip-duration is in seconds; the draft stores microseconds.
            dur = int(clip_durations[i] * SEC) if clip_durations else 3 * SEC
        else:
            dur = mat.duration_us

        seg = media_segment_json(
            _new_id(), mid, cursor, dur, 0, dur, 1.0, 1.0, refs, True)
        video_segments.append(seg)
        cursor += dur

    video_duration = cursor
    tracks.append(track_json("video", "main", video_segments))

    # ---- subtitle track
    if subtitles and subtitles.exists():
        cues = parse_srt(subtitles)
        text_segments = []
        for start, end, body in cues:
            # The timeline ends with the video, so a subtitle that runs
            # past it would dangle outside the project.
            if start >= video_duration:
                break
            end = min(end, video_duration)
            if end <= start:
                continue
            mid = _new_id()
            materials["texts"].append(text_material_json(
                body, mid, subtitle_size, (1.0, 1.0, 1.0), 1))
            text_segments.append(text_segment_json(
                _new_id(), mid, start, end - start, subtitle_y))
        if text_segments:
            tracks.append(track_json("text", "caption", text_segments))

    # ---- voice track
    if voice and voice.exists():
        mat = probe_media(voice)
        mid = _new_id()
        materials["audios"].append(audio_material_json(mat, voice, mid))
        speed_id = _new_id()
        materials["speeds"].append({
            "curve_speed": None, "id": speed_id, "mode": 0,
            "speed": 1.0, "type": "speed",
        })
        dur = min(mat.duration_us, max(video_duration, 1))
        seg = media_segment_json(
            _new_id(), mid, 0, dur, 0, dur, 1.0, voice_volume,
            [speed_id], False)
        tracks.append(track_json("audio", "voice", [seg]))
        coverage.append(("voice", dur, video_duration))

    # ---- bgm track (with ducking under the narration)
    if bgm and bgm.exists():
        mat = probe_media(bgm)
        mid = _new_id()
        materials["audios"].append(audio_material_json(mat, bgm, mid))
        speed_id = _new_id()
        materials["speeds"].append({
            "curve_speed": None, "id": speed_id, "mode": 0,
            "speed": 1.0, "type": "speed",
        })
        refs = [speed_id]
        fade = audio_fade_json(int(bgm_fade_in * SEC),
                               int(bgm_fade_out * SEC))
        materials["audio_fades"].append(fade)
        refs.append(fade["id"])

        # A BGM track shorter than the video would leave silence at the
        # end, so repeat it until it covers the whole timeline. Each repeat
        # is a separate segment reading the same source from 0.
        seg_len = mat.duration_us
        bgm_segments = []
        bgm_cursor = 0
        while bgm_cursor < video_duration:
            take = min(seg_len, video_duration - bgm_cursor)
            bgm_segments.append(media_segment_json(
                _new_id(), mid, bgm_cursor, take, 0, take, 1.0,
                bgm_volume, list(refs), False))
            bgm_cursor += take

        # Ducking is authored on the first segment, which is the one that
        # carries the fade-in.
        if subtitles and subtitles.exists():
            kfs = duck_keyframes(parse_srt(subtitles), bgm_cursor)
            if kfs:
                bgm_segments[0]["common_keyframes"] = kfs
        tracks.append(track_json("audio", "bgm", bgm_segments))
        coverage.append(("bgm", bgm_cursor, video_duration))

    total = max(video_duration, 1)
    content = build_content(width, height, fps, tracks, materials, total)
    meta = build_meta_info(name, total, "")
    return content, meta, coverage


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate a JianYing (剪映) draft from a montage")
    ap.add_argument("--clips", nargs="+", required=True,
                    help="video/image clips, in playback order")
    ap.add_argument("--voice", help="narration audio")
    ap.add_argument("--bgm", help="background music")
    ap.add_argument("--subtitles", help="SRT subtitle file")
    ap.add_argument("--name", default="混剪草稿", help="draft name")
    ap.add_argument("--out", help="输出目录，草稿写进 <目录>/<名称>（默认 ./jianying-out）")
    ap.add_argument("--install", action="store_true",
                    help="write into JianYing's draft directory so it shows "
                         "up on the home screen")
    ap.add_argument("--draft-root", help="override the draft directory")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--transition", default="叠化", choices=sorted(TRANSITIONS))
    ap.add_argument("--transition-duration", type=float, default=0.5)
    ap.add_argument("--clip-duration", type=float,
                    help="force the duration of every clip (seconds)")
    ap.add_argument("--bgm-volume", type=float, default=0.2)
    ap.add_argument("--voice-volume", type=float, default=1.0)
    ap.add_argument("--bgm-fade-in", type=float, default=0.5)
    ap.add_argument("--bgm-fade-out", type=float, default=0.8)
    ap.add_argument("--subtitle-size", type=float, default=8.0)
    ap.add_argument("--subtitle-y", type=float, default=-0.8,
                    help="subtitle vertical position; -0.8 is near the bottom")
    ap.add_argument("--encrypt", action="store_true",
                    help="encrypt the draft files (AES-256-GCM). Needed by "
                         "some newer JianYing builds; requires pycryptodome")
    args = ap.parse_args()

    install_deps(quiet=True)

    clips = [Path(c).expanduser().resolve() for c in args.clips]
    missing = [str(c) for c in clips if not c.exists()]
    if missing:
        print(f"[draft] missing clips: {missing}", file=sys.stderr)
        return 1

    voice = Path(args.voice).expanduser().resolve() if args.voice else None
    bgm = Path(args.bgm).expanduser().resolve() if args.bgm else None
    subs = Path(args.subtitles).expanduser().resolve() if args.subtitles else None
    for label, p in (("voice", voice), ("bgm", bgm), ("subtitles", subs)):
        if p and not p.exists():
            print(f"[draft] {label} not found: {p}", file=sys.stderr)
            return 1

    clip_durations = None
    if args.clip_duration:
        clip_durations = [args.clip_duration] * len(clips)

    content, meta, coverage = build_draft(
        clips, voice, bgm, subs, args.width, args.height, args.fps,
        args.transition, args.transition_duration, clip_durations,
        args.bgm_volume, args.voice_volume, args.bgm_fade_in,
        args.bgm_fade_out, args.subtitle_size, args.subtitle_y, args.name)

    # ---- decide where the draft goes
    root = None
    if args.draft_root:
        root = Path(args.draft_root).expanduser()
    elif args.install:
        root = jianying_draft_root()
        if root is None:
            print("[draft] could not find JianYing's draft folder. "
                  "Install JianYing, or pass --draft-root.", file=sys.stderr)
            return 1

    if root is not None:
        folder = root / args.name
        if folder.exists():
            folder = root / f"{args.name}-vmix"
        meta["draft_root_path"] = str(root)
        meta["draft_fold_path"] = str(folder)
    else:
        # --out is the *parent* directory, matching the default layout
        # (./jianying-out/<name>).  A path that already ends with the
        # draft name is used as-is, so it is never doubled.
        base = out_path(args.out) if args.out else (Path.cwd() / "jianying-out")
        folder = base if base.name == args.name else base / args.name

    write_draft(folder, args.name, content, meta, encrypt=args.encrypt)

    dur = content["duration"] / SEC
    seg_counts = {t["name"]: len(t["segments"]) for t in content["tracks"]}
    print(f"[draft] OK -> {folder}")
    print(f"[draft] name={args.name}  duration={dur:.2f}s  tracks={seg_counts}")
    if args.encrypt:
        print("[draft] files are encrypted (AES-256-GCM)")
    for label, covered, total_len in coverage:
        if covered < total_len:
            print(f"[draft] warning: {label} covers {covered / SEC:.2f}s of "
                  f"{total_len / SEC:.2f}s -- the tail will be silent")
    if args.install:
        print("[draft] open JianYing (剪映专业版) and the draft is on the "
              "home screen.")
    else:
        print("[draft] to use it: copy this folder into JianYing's draft "
              "directory, or re-run with --install")
    print(str(folder))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
