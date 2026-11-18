#!/usr/bin/env python3
"""Test suite for the JianYing (剪映) integration of the video-mix skill.

Run it with either of:

    python3 tests/test_jianying.py -v
    python3 -m unittest discover -s tests -t . -v

Only the standard library is required.  The encryption tests need
``pycryptodome`` and are skipped when it is missing; the end-to-end tests
need ffmpeg (the skill ships one via ``pip install imageio-ffmpeg``) and
are skipped when no ffmpeg can be located.

The suite covers four layers:

  1. ``jianying_crypto`` -- the offline AES-256-GCM draft format
  2. ``jianying_draft``  -- SRT parsing, JSON blocks, unit handling
  3. ``jianying_check``  -- the validator must catch every fault it claims
  4. ``jianying_pack``   -- FCPXML / asset pack fallback

plus command-line end-to-end runs over synthetic media.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import jianying_check as checker  # noqa: E402
import jianying_crypto as crypto  # noqa: E402
import jianying_draft as draft  # noqa: E402
import jianying_pack as pack  # noqa: E402

SEC = draft.SEC


# ------------------------------------------------------------------ helpers


def ffmpeg_exe() -> str | None:
    """Locate ffmpeg, or None when it is not installed."""
    try:
        from vmix_common import ffmpeg_bin
        return ffmpeg_bin()
    except Exception:
        return shutil.which("ffmpeg")


def make_png(path: Path, width: int = 64, height: int = 32) -> Path:
    """Write a tiny valid PNG with the standard library only."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(
        b"\x00" + bytes(v for x in range(width)
                        for v in ((x * 4) % 256, (y * 8) % 256, 128))
        for y in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )
    return path


def make_video(path: Path, seconds: float = 1.0, color: str = "blue") -> Path:
    subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"color=c={color}:s=320x240:r=25:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(path)],
        check=True, capture_output=True)
    return path


def make_audio(path: Path, seconds: float = 2.0, freq: int = 440) -> Path:
    subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         str(path)],
        check=True, capture_output=True)
    return path


SRT_TEXT = """1
00:00:00,000 --> 00:00:01,500
第一句旁白

2
00:00:01,500 --> 00:00:03,000
第二句旁白
"""


def write_srt(path: Path, text: str = SRT_TEXT) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def run_cli(script: str, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(SCRIPTS_DIR / script), *args]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)


def track(content: dict, name: str) -> dict:
    for t in content["tracks"]:
        if t["name"] == name:
            return t
    raise AssertionError(f"track '{name}' not found in {[t['name'] for t in content['tracks']]}")


# ------------------------------------------------------------------ crypto


try:
    crypto._aes()
    HAS_CRYPTO = True
except Exception:  # pragma: no cover - depends on the environment
    HAS_CRYPTO = False


@unittest.skipUnless(HAS_CRYPTO, "需要 pycryptodome")
class TestCrypto(unittest.TestCase):
    """The offline draft encryption format."""

    def test_json_roundtrip(self):
        obj = {"tracks": [{"name": "main"}], "duration": 12_000_000,
               "中文": "剪映"}
        plain = crypto.encrypt_json(obj)
        self.assertNotEqual(plain[:1], b"{")
        back = json.loads(crypto.decrypt_bytes(plain).decode("utf-8"))
        self.assertEqual(back, obj)

    def test_bytes_roundtrip(self):
        raw = b"hello jianying" * 20
        self.assertEqual(crypto.decrypt_bytes(crypto.encrypt_bytes(raw)), raw)

    def test_unicode_roundtrip(self):
        raw = "混剪草稿：字幕、转场、旁白".encode("utf-8")
        self.assertEqual(crypto.decrypt_bytes(crypto.encrypt_bytes(raw)), raw)

    def test_key_and_iv_are_embedded_at_fixed_offsets(self):
        plain = json.dumps({"a": 1}).encode()
        text = crypto.encrypt_bytes(plain).decode("latin1")
        key, iv = crypto.derive_key_iv(text)
        self.assertEqual(len(key), 32)
        self.assertEqual(len(iv), 16)
        # the key characters sit at the first 8 offsets, the iv at the last 4
        self.assertEqual(key.decode("latin1"),
                         "".join(text[o:o + 4] for o in crypto.OFFSETS[:8]))
        self.assertEqual(iv.decode("latin1"),
                         "".join(text[o:o + 4] for o in crypto.OFFSETS[8:]))

    def test_strip_removes_exactly_48_characters(self):
        text = crypto.encrypt_bytes(b"x" * 500).decode("latin1")
        self.assertEqual(len(text) - len(crypto.strip_muxed(text)), 48)

    def test_output_meets_minimum_length(self):
        for payload in (b"{}", b'{"a":1}', b"x" * 10, b"y" * 500):
            with self.subTest(size=len(payload)):
                text = crypto.encrypt_bytes(payload)
                self.assertGreaterEqual(len(text), crypto.MIN_LEN)

    def test_short_json_is_padded_not_truncated(self):
        obj = {"a": "b"}
        back = json.loads(crypto.decrypt_bytes(crypto.encrypt_json(obj)).decode())
        self.assertEqual(back, obj)

    def test_seed_is_deterministic(self):
        a = crypto.encrypt_bytes(b"deterministic payload here", seed=7)
        b = crypto.encrypt_bytes(b"deterministic payload here", seed=7)
        self.assertEqual(a, b)

    def test_plaintext_is_rejected(self):
        with self.assertRaises(ValueError):
            crypto.decrypt_bytes(b'{"draft": true}')

    def test_too_short_input_is_rejected(self):
        with self.assertRaises(ValueError):
            crypto.decrypt_bytes(b"short")

    def test_tampered_ciphertext_is_detected(self):
        text = crypto.encrypt_bytes(b"important payload" * 20).decode("latin1")
        idx = 120
        mutated = text[:idx] + ("A" if text[idx] != "A" else "B") + text[idx + 1:]
        with self.assertRaises(Exception):
            crypto.decrypt_bytes(mutated.encode("latin1"))

    def test_looks_plaintext(self):
        self.assertTrue(crypto.looks_plaintext(b'  {"a":1}'))
        self.assertFalse(crypto.looks_plaintext(b"Zm9vYmFy"))

    def test_cli_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "draft.json"
            p.write_text(json.dumps({"hello": "世界"}), encoding="utf-8")
            enc = run_cli("jianying_crypto.py", str(p), "-e")
            self.assertEqual(enc.returncode, 0, enc.stderr)
            produced = p.with_suffix(".enc.json")
            self.assertTrue(produced.exists())
            self.assertFalse(crypto.looks_plaintext(produced.read_bytes()))

            dec = run_cli("jianying_crypto.py", str(produced), "-p")
            self.assertEqual(dec.returncode, 0, dec.stderr)
            self.assertEqual(json.loads(dec.stdout)["hello"], "世界")

    def test_cli_refuses_to_double_encrypt(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "draft.json"
            p.write_text(json.dumps({"a": 1}), encoding="utf-8")
            run_cli("jianying_crypto.py", str(p), "-e")
            enc_file = p.with_suffix(".enc.json")
            again = run_cli("jianying_crypto.py", str(enc_file), "-e")
            self.assertNotEqual(again.returncode, 0)


# ------------------------------------------------------------------ srt


class TestSrt(unittest.TestCase):
    """SRT parsing: the timings feed straight into the timeline."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def parse(self, text):
        p = self.tmp / "s.srt"
        p.write_text(text, encoding="utf-8")
        return draft.parse_srt(p)

    def test_basic_cue(self):
        cues = self.parse(SRT_TEXT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0][0], 0)
        self.assertEqual(cues[0][1], 1_500_000)
        self.assertEqual(cues[0][2], "第一句旁白")

    def test_timestamps_are_microseconds(self):
        cues = self.parse("1\n00:00:01,000 --> 00:00:02,000\nhi\n")
        self.assertEqual(cues[0][1] - cues[0][0], 1_000_000)

    def test_crlf_and_bom_are_tolerated(self):
        p = self.tmp / "crlf.srt"
        p.write_bytes("﻿1\r\n00:00:00,000 --> 00:00:01,000\r\n文本\r\n"
                      .encode("utf-8"))
        cues = draft.parse_srt(p)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0][2], "文本")

    def test_multiline_body_is_joined(self):
        cues = self.parse("1\n00:00:00,000 --> 00:00:01,000\nline one\nline two\n")
        self.assertEqual(cues[0][2], "line one line two")

    def test_overlapping_cues_are_trimmed(self):
        cues = self.parse(
            "1\n00:00:00,000 --> 00:00:02,000\na\n\n"
            "2\n00:00:01,000 --> 00:00:03,000\nb\n")
        self.assertEqual(cues[1][0], 2_000_000)

    def test_zero_length_cue_is_extended(self):
        cues = self.parse("1\n00:00:01,000 --> 00:00:01,000\na\n")
        self.assertGreater(cues[0][1], cues[0][0])

    def test_garbage_blocks_are_skipped(self):
        cues = self.parse("not a subtitle\n\n1\n00:00:00,000 --> 00:00:01,000\nok\n")
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0][2], "ok")

    def test_empty_file(self):
        self.assertEqual(self.parse(""), [])


# ------------------------------------------------------------------ draft


class TestDraftBlocks(unittest.TestCase):
    """The JSON blocks that make up a JianYing draft."""

    def test_transition_block(self):
        t = draft.transition_json("叠化", 500_000)
        self.assertEqual(t["type"], "transition")
        self.assertEqual(t["duration"], 500_000)
        self.assertEqual(t["name"], "叠化")
        self.assertTrue(t["id"])
        resource_id, effect_id, _, _ = draft.TRANSITIONS["叠化"]
        self.assertEqual(t["resource_id"], resource_id)
        self.assertEqual(t["effect_id"], effect_id)

    def test_audio_fade_block(self):
        f = draft.audio_fade_json(500_000, 800_000)
        self.assertEqual(f["fade_in_duration"], 500_000)
        self.assertEqual(f["fade_out_duration"], 800_000)
        self.assertEqual(f["type"], "audio_fade")

    def test_speed_block(self):
        s = draft.speed_json(1.5)
        self.assertEqual(s["speed"], 1.5)
        self.assertEqual(s["type"], "speed")

    def test_text_material_keeps_the_words(self):
        m = draft.text_material_json("你好", "mid123", 8.0, (1.0, 1.0, 1.0), 1)
        # `content` is a JSON string *inside* the JSON
        inner = json.loads(m["content"])
        self.assertEqual(inner["text"], "你好")
        self.assertEqual(inner["styles"][0]["range"], [0, 2])
        self.assertEqual(m["id"], "mid123")
        self.assertEqual(m["type"], "subtitle")

    def test_duck_keyframes_dip_under_the_narration(self):
        cues = [(1_000_000, 2_000_000, "hello")]
        kfs = draft.duck_keyframes(cues, 10_000_000)
        self.assertEqual(len(kfs), 1)
        kf = kfs[0]
        self.assertEqual(kf["property_type"], "KFTypeVolume")
        offsets = {e["time_offset"]: e["values"][0] for e in kf["keyframe_list"]}
        self.assertAlmostEqual(offsets[1_000_000], 0.12, places=6)
        self.assertAlmostEqual(offsets[2_000_000], 0.12, places=6)
        self.assertAlmostEqual(offsets[600_000], 1.0, places=6)
        self.assertAlmostEqual(offsets[2_400_000], 1.0, places=6)

    def test_duck_keyframes_without_cues(self):
        self.assertEqual(draft.duck_keyframes([], 10_000_000), [])

    def test_build_content_sets_render_index_per_track(self):
        seg = draft.media_segment_json("s1", "m1", 0, 1_000_000, 0, 1_000_000,
                                       1.0, 1.0, [], True)
        tracks = [draft.track_json("video", "main", [seg])]
        content = draft.build_content(1080, 1920, 30, tracks,
                                      draft._empty_materials(), 1_000_000)
        self.assertEqual(content["duration"], 1_000_000)
        self.assertEqual(content["fps"], 30.0)
        self.assertEqual(content["canvas_config"]["width"], 1080)
        self.assertEqual(content["tracks"][0]["segments"][0]["render_index"], 0)
        self.assertTrue(content["platform"]["app_version"].startswith("5.9"))

    def test_build_meta_info(self):
        meta = draft.build_meta_info("我的混剪", 12_000_000, "/tmp/drafts")
        self.assertEqual(meta["draft_name"], "我的混剪")
        self.assertEqual(meta["tm_duration"], 12_000_000)
        self.assertEqual(meta["draft_root_path"], "/tmp/drafts")

    def test_image_size_is_read_from_the_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_png(Path(tmp) / "a.png", 64, 32)
            self.assertEqual(draft._image_size(p), (64, 32))

    def test_probe_media_rejects_a_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            draft.probe_media(Path("/definitely/not/here.mp4"))

    @unittest.skipUnless(ffmpeg_exe(), "需要 ffmpeg")
    def test_probe_media_reads_duration_and_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            v = make_video(Path(tmp) / "v.mp4", seconds=1.0)
            info = draft.probe_media(v)
            self.assertEqual(info.kind, "video")
            self.assertAlmostEqual(info.duration_us / SEC, 1.0, delta=0.15)

    @unittest.skipUnless(ffmpeg_exe(), "需要 ffmpeg")
    def test_probe_media_recognises_a_photo(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_png(Path(tmp) / "p.png", 48, 24)
            info = draft.probe_media(p)
            self.assertEqual(info.kind, "photo")
            self.assertEqual((info.width, info.height), (48, 24))


# ------------------------------------------------------------------ checker


@unittest.skipUnless(ffmpeg_exe(), "需要 ffmpeg")
class TestChecker(unittest.TestCase):
    """The validator has to actually catch broken drafts."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.clip = make_video(cls.tmp / "clip01.mp4", 1.0)
        cls.clip2 = make_video(cls.tmp / "clip02.mp4", 1.0, "green")
        cls.voice = make_audio(cls.tmp / "voice.mp3", 2.0)
        cls.bgm = make_audio(cls.tmp / "bgm.wav", 1.5)
        cls.srt = write_srt(cls.tmp / "voice.srt")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def build(self):
        content, meta, _ = draft.build_draft(
            [self.clip, self.clip2], self.voice, self.bgm, self.srt,
            1080, 1920, 30, "叠化", 0.5, None,
            0.2, 1.0, 0.5, 0.8, 8.0, -0.8, "测试草稿")
        return content, meta

    def write(self, content, meta, folder: Path, encrypt=False):
        draft.write_draft(folder, "测试草稿", content, meta, encrypt=encrypt)
        return folder

    def test_a_valid_draft_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(*self.build(), Path(tmp) / "d")
            self.assertEqual(checker.check_draft(folder), [])

    def test_both_file_names_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(*self.build(), Path(tmp) / "d")
            self.assertTrue((folder / "draft_info.json").exists())
            self.assertTrue((folder / "draft_content.json").exists())
            self.assertTrue((folder / "draft_meta_info.json").exists())
            self.assertEqual((folder / "draft_info.json").read_bytes(),
                             (folder / "draft_content.json").read_bytes())

    def test_dangling_material_id_is_caught(self):
        content, meta = self.build()
        content["tracks"][0]["segments"][0]["material_id"] = "does-not-exist"
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(content, meta, Path(tmp) / "d")
            problems = checker.check_draft(folder)
            self.assertTrue(any("不存在的素材 id" in p for p in problems), problems)

    def test_dangling_extra_material_ref_is_caught(self):
        content, meta = self.build()
        content["tracks"][0]["segments"][0]["extra_material_refs"] = ["ghost"]
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(content, meta, Path(tmp) / "d")
            problems = checker.check_draft(folder)
            self.assertTrue(any("不存在的附加素材" in p for p in problems), problems)

    def test_overlapping_segments_are_caught(self):
        content, meta = self.build()
        segs = content["tracks"][0]["segments"]
        segs[1]["target_timerange"]["start"] = 0
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(content, meta, Path(tmp) / "d")
            problems = checker.check_draft(folder)
            self.assertTrue(any("重叠" in p for p in problems), problems)

    def test_missing_media_file_is_caught(self):
        content, meta = self.build()
        content["materials"]["videos"][0]["path"] = "/nope/missing.mp4"
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(content, meta, Path(tmp) / "d")
            problems = checker.check_draft(folder)
            self.assertTrue(any("素材文件不存在" in p for p in problems), problems)

    def test_invalid_json_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "d"
            folder.mkdir()
            (folder / "draft_info.json").write_text("{not json", encoding="utf-8")
            (folder / "draft_meta_info.json").write_text("{}", encoding="utf-8")
            problems = checker.check_draft(folder)
            self.assertTrue(any("读取失败" in p for p in problems), problems)

    def test_missing_files_are_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            problems = checker.check_draft(Path(tmp) / "nothing")
            self.assertTrue(any("缺少 draft_info.json" in p for p in problems),
                            problems)

    def test_millisecond_duration_is_caught(self):
        """Regression: a duration written in ms instead of us.

        A 3 second clip written as 3000 (milliseconds) must not slip
        through -- it is only 3 ms long on the timeline.
        """
        content, meta = self.build()
        seg = content["tracks"][0]["segments"][0]
        seg["target_timerange"]["duration"] = 3000
        seg["source_timerange"]["duration"] = 3000
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(content, meta, Path(tmp) / "d")
            problems = checker.check_draft(folder)
            self.assertTrue(any("时长单位可能写错了" in p for p in problems),
                            problems)

    def test_microsecond_duration_is_caught(self):
        """Regression: seconds written as microseconds (3 -> 3 us)."""
        content, meta = self.build()
        seg = content["tracks"][0]["segments"][0]
        seg["target_timerange"]["duration"] = 3
        seg["source_timerange"]["duration"] = 3
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(content, meta, Path(tmp) / "d")
            problems = checker.check_draft(folder)
            self.assertTrue(any("时长单位可能写错了" in p for p in problems),
                            problems)

    def test_a_track_running_past_the_end_is_caught(self):
        content, meta = self.build()
        seg = content["tracks"][0]["segments"][0]
        seg["target_timerange"]["duration"] = 60 * SEC
        seg["source_timerange"]["duration"] = 60 * SEC
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(content, meta, Path(tmp) / "d")
            problems = checker.check_draft(folder)
            self.assertTrue(any("超出草稿时长" in p for p in problems),
                            problems)

    def test_duration_mismatch_is_caught(self):
        content, meta = self.build()
        content["duration"] = 99_000_000
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(content, meta, Path(tmp) / "d")
            problems = checker.check_draft(folder)
            self.assertTrue(any("草稿时长" in p for p in problems), problems)

    def test_source_range_beyond_the_material_is_caught(self):
        content, meta = self.build()
        seg = content["tracks"][0]["segments"][0]
        seg["source_timerange"]["duration"] = 60 * SEC
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(content, meta, Path(tmp) / "d")
            problems = checker.check_draft(folder)
            self.assertTrue(any("超出素材" in p for p in problems), problems)

    @unittest.skipUnless(HAS_CRYPTO, "需要 pycryptodome")
    def test_encrypted_draft_is_validated_too(self):
        content, meta = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(content, meta, Path(tmp) / "d", encrypt=True)
            raw = (folder / "draft_info.json").read_bytes()
            self.assertFalse(crypto.looks_plaintext(raw))
            self.assertEqual(checker.check_draft(folder), [])

    def test_cli_reports_success(self):
        content, meta = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            folder = self.write(content, meta, Path(tmp) / "d")
            proc = run_cli("jianying_check.py", str(folder))
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("OK", proc.stdout)


# ------------------------------------------------------------------ pack


class TestPackHelpers(unittest.TestCase):
    """The version-proof fallback: FCPXML and time formatting."""

    def test_timecode(self):
        self.assertEqual(pack.timecode(0.0, 30), "00:00:00:00")
        self.assertEqual(pack.timecode(1.5, 30), "00:00:01:15")
        self.assertEqual(pack.timecode(61.0, 30), "00:01:01:00")
        self.assertEqual(pack.timecode(3661.0, 30), "01:01:01:00")

    def test_rat_uses_rational_time(self):
        self.assertEqual(pack.rat(4.0), "4000/1000s")
        self.assertTrue(pack.rat(1.5).endswith("s"))
        self.assertNotIn(".", pack.rat(1.5))


@unittest.skipUnless(ffmpeg_exe(), "需要 ffmpeg")
class TestFcpxml(unittest.TestCase):
    """FCPXML must import into Premiere and Resolve."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.clip = make_video(cls.tmp / "clip01.mp4", 1.0)
        cls.clip2 = make_video(cls.tmp / "clip02.mp4", 1.0, "green")
        cls.voice = make_audio(cls.tmp / "voice.mp3", 2.0)
        cls.bgm = make_audio(cls.tmp / "bgm.wav", 1.5)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_assets_use_the_default_namespace(self):
        """Regression: an `ns1:` prefix makes Resolve refuse the file."""
        xml = pack.build_fcpxml([self.clip, self.clip2], self.voice,
                                self.bgm, 1080, 1920, 30)
        self.assertNotIn("ns1:", xml)
        self.assertNotIn("ns0:", xml)
        self.assertIn('<asset ', xml)

    def test_output_is_well_formed_xml(self):
        import xml.etree.ElementTree as ET
        xml = pack.build_fcpxml([self.clip, self.clip2], self.voice,
                                self.bgm, 1080, 1920, 30)
        root = ET.fromstring(xml)
        ns = "{http://www.futuresoft.net/FoundationXML/2019}"
        self.assertTrue(root.tag.startswith(ns))
        assets = root.findall(f".//{ns}asset")
        self.assertEqual(len(assets), 4)  # 2 clips + voice + bgm
        clips = root.findall(f".//{ns}asset-clip")
        self.assertEqual(len(clips), 4)  # 2 video + voice + bgm

    def test_video_clips_are_back_to_back(self):
        import xml.etree.ElementTree as ET
        xml = pack.build_fcpxml([self.clip, self.clip2], None, None,
                                1080, 1920, 30)
        ns = "{http://www.futuresoft.net/FoundationXML/2019}"
        root = ET.fromstring(xml)
        spine = root.find(f".//{ns}spine")
        clips = spine.findall(f"{ns}asset-clip")
        self.assertEqual(clips[0].get("offset"), "0/1000s")
        self.assertEqual(clips[1].get("offset"), "1000/1000s")


# ------------------------------------------------------------------ e2e


@unittest.skipUnless(ffmpeg_exe(), "需要 ffmpeg")
class TestEndToEnd(unittest.TestCase):
    """The whole chain, driven through the command line."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.clips = [make_video(cls.tmp / "clip01.mp4", 1.0),
                     make_video(cls.tmp / "clip02.mp4", 1.0, "green"),
                     make_video(cls.tmp / "clip03.mp4", 1.0, "red")]
        cls.voice = make_audio(cls.tmp / "voice.mp3", 2.0)
        cls.bgm = make_audio(cls.tmp / "bgm.wav", 1.5)
        cls.srt = write_srt(cls.tmp / "voice.srt")
        cls.png = make_png(cls.tmp / "still.png", 64, 32)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_draft_cli_builds_a_four_track_timeline(self):
        out = self.tmp / "out-draft"
        proc = run_cli(
            "jianying_draft.py",
            "--clips", *[str(c) for c in self.clips],
            "--voice", str(self.voice),
            "--bgm", str(self.bgm),
            "--subtitles", str(self.srt),
            "--name", "端到端",
            "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        folder = out / "端到端"
        content = json.loads((folder / "draft_info.json").read_text("utf-8"))
        names = [t["name"] for t in content["tracks"]]
        self.assertEqual(names, ["main", "caption", "voice", "bgm"])

        # the video track is contiguous and ends at the draft duration
        segs = track(content, "main")["segments"]
        self.assertEqual(len(segs), 3)
        cursor = 0
        for seg in segs:
            self.assertEqual(seg["target_timerange"]["start"], cursor)
            cursor += seg["target_timerange"]["duration"]
        self.assertEqual(content["duration"], cursor)

        # one transition per cut, registered before it is referenced
        self.assertEqual(len(content["materials"]["transitions"]), 2)
        transition_ids = {t["id"] for t in content["materials"]["transitions"]}
        for seg in segs[1:]:
            refs = set(seg["extra_material_refs"])
            self.assertTrue(refs & transition_ids)

        # the BGM is looped until it covers the whole timeline
        bgm_segs = track(content, "bgm")["segments"]
        self.assertGreaterEqual(len(bgm_segs), 2)
        self.assertEqual(bgm_segs[-1]["target_timerange"]["start"]
                         + bgm_segs[-1]["target_timerange"]["duration"],
                         content["duration"])

        # subtitles became editable text segments
        texts = track(content, "caption")["segments"]
        self.assertEqual(len(texts), 2)
        self.assertTrue(all(t["source_timerange"] is None for t in texts))
        self.assertTrue(texts[0]["clip"]["transform"]["y"] < 0)

        # and the validator is happy with what we just wrote
        self.assertEqual(checker.check_draft(folder), [])

    def test_draft_cli_encrypts_on_request(self):
        out = self.tmp / "out-enc"
        proc = run_cli(
            "jianying_draft.py",
            "--clips", *[str(c) for c in self.clips],
            "--name", "加密草稿",
            "--out", str(out),
            "--encrypt")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        raw = (out / "加密草稿" / "draft_info.json").read_bytes()
        self.assertFalse(crypto.looks_plaintext(raw))
        self.assertEqual(checker.check_draft(out / "加密草稿"), [])

    @unittest.skipUnless(HAS_CRYPTO, "需要 pycryptodome")
    def test_encrypted_draft_decrypts_back_to_the_same_timeline(self):
        out = self.tmp / "out-enc2"
        run_cli("jianying_draft.py",
                "--clips", *[str(c) for c in self.clips],
                "--name", "往返", "--out", str(out), "--encrypt")
        folder = out / "往返"
        decrypted = crypto.decrypt_json_file(folder / "draft_info.json")
        self.assertEqual(len(decrypted["tracks"][0]["segments"]), 3)
        self.assertGreater(decrypted["duration"], 0)

    def test_photo_clip_duration_is_read_as_seconds(self):
        """Regression: `--clip-duration 3` used to mean 3 microseconds."""
        out = self.tmp / "out-photo"
        proc = run_cli(
            "jianying_draft.py",
            "--clips", str(self.png), str(self.clips[0]),
            "--name", "图片草稿",
            "--out", str(out),
            "--clip-duration", "3")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        content = json.loads((out / "图片草稿" / "draft_info.json")
                             .read_text("utf-8"))
        segs = track(content, "main")["segments"]
        self.assertEqual(segs[0]["target_timerange"]["duration"], 3 * SEC)
        self.assertEqual(content["duration"], 4 * SEC)

    def test_subtitles_are_clamped_to_the_video_length(self):
        """A narration longer than the video must not dangle outside it."""
        long_srt = write_srt(self.tmp / "long.srt",
                             "1\n00:00:00,000 --> 00:00:01,000\n第一句\n\n"
                             "2\n00:00:01,000 --> 00:00:09,000\n第二句\n")
        out = self.tmp / "out-clamp"
        proc = run_cli(
            "jianying_draft.py",
            "--clips", *[str(c) for c in self.clips],
            "--subtitles", str(long_srt),
            "--name", "字幕裁齐",
            "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        content = json.loads((out / "字幕裁齐" / "draft_info.json")
                             .read_text("utf-8"))
        texts = track(content, "caption")["segments"]
        last = texts[-1]["target_timerange"]
        self.assertLessEqual(last["start"] + last["duration"],
                             content["duration"])
        self.assertEqual(checker.check_draft(out / "字幕裁齐"), [])

    def test_pack_cli_writes_the_fallback_assets(self):
        out = self.tmp / "out-pack"
        proc = run_cli(
            "jianying_pack.py",
            "--clips", *[str(c) for c in self.clips],
            "--voice", str(self.voice),
            "--bgm", str(self.bgm),
            "--subtitles", str(self.srt),
            "--name", "素材包",
            "--out", str(out))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        folder = out / "素材包"
        self.assertTrue((folder / "manifest.json").exists())
        self.assertTrue((folder / "premiere.xml").exists())
        self.assertTrue((folder / "subtitles.srt").exists())
        self.assertTrue((folder / "clips").is_dir())
        self.assertEqual(len(list((folder / "clips").iterdir())), 3)

        manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
        self.assertEqual(len(manifest["clips"]), 3)
        self.assertEqual(manifest["voice"]["file"], "voice.mp3")
        self.assertEqual(manifest["bgm"]["file"], "bgm.wav")
        # the clips are laid out back to back on the timeline
        self.assertEqual(manifest["clips"][0]["timeline_start"], 0.0)
        for prev, nxt in zip(manifest["clips"], manifest["clips"][1:]):
            self.assertAlmostEqual(prev["timeline_end"],
                                   nxt["timeline_start"], places=3)

    def test_pack_cli_can_embed_a_draft(self):
        out = self.tmp / "out-pack2"
        proc = run_cli(
            "jianying_pack.py",
            "--clips", *[str(c) for c in self.clips],
            "--name", "带草稿",
            "--out", str(out),
            "--draft")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        folder = out / "带草稿" / "jianying-draft"
        self.assertTrue((folder / "draft_info.json").exists())
        self.assertTrue((folder / "draft_content.json").exists())
        self.assertEqual(checker.check_draft(folder), [])

    def test_draft_cli_reports_a_missing_clip(self):
        proc = run_cli("jianying_draft.py", "--clips", "/nope/missing.mp4",
                       "--name", "坏草稿", "--out", str(self.tmp / "bad"))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("missing clips", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
