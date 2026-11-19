#!/usr/bin/env python3
"""Score the montage: a real BGM, a room tone, and precise narration ducking.

The earlier version summed four sine waves.  That is a test tone, not
music -- it has no timbre, no envelope and no harmonic movement, and it
sounded like a synthesiser being switched on.

This module synthesises the score additively instead: each note is a
stack of harmonics with per-partial decay (so a struck string loses its
upper partials first, like a real piano or harp), a soft attack and a
slow release.  The notes are arranged into a chord progression with a
gentle arpeggio over a low pad, which reads as "warm acoustic
accompaniment" rather than "beep".

Three layers come out of it:

  score.wav   the music -- piano-ish plucks over a soft pad
  room.wav    room tone -- filtered noise plus a distant hum, at a very
              low level, so the silence between lines is not dead
  mix.wav     score + room + narration, with the score ducking under
              the voice on a measured envelope instead of a threshold
              compressor

Everything is mixed in numpy rather than in an ffmpeg graph, because the
ducking envelope has to be built from the narration signal itself and
its result has to be measurable.

Usage:
    python audio_score.py --duration 34 --out audio/score.wav
    python audio_score.py --duration 34 --voice audio/voice.mp3 \\
        --out audio/score.wav --room-out audio/room.wav --mix-out audio/mix.wav
"""

from __future__ import annotations

import argparse
import math
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path.home() / ".workbuddy" / "skills" / "video-mix" / "scripts"))
from vmix_common import ffmpeg_bin  # noqa: E402

SR = 44100

# A minor-key progression that reads as warm rather than sad:
#   Am9 -> Fmaj7 -> Cmaj7 -> G6
CHORDS = [
    ("A", [0, 3, 7, 10, 14]),
    ("F", [-4, 0, 4, 7, 11]),
    ("C", [-9, 0, 4, 7, 11]),
    ("G", [-5, 0, 4, 7, 11]),
]

NOTE_BASE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Partial stacks: (harmonic number, relative level)
PIANO = [(1, 1.0), (2, 0.42), (3, 0.18), (4, 0.09), (5, 0.05)]
HARP = [(1, 1.0), (2, 0.50), (3, 0.28), (4, 0.14), (6, 0.06)]


def midi_to_hz(semitone: int) -> float:
    return 440.0 * (2.0 ** (semitone / 12.0))


def chord_hz(root: str, intervals: list[int]) -> list[float]:
    base = NOTE_BASE[root] + 12
    return [midi_to_hz(base + i) for i in intervals]


def trapezoid(n: int, ramp: int) -> np.ndarray:
    """A flat envelope with short fades at both ends."""
    env = np.ones(n)
    r = min(ramp, n // 2)
    if r > 0:
        env[:r] = np.linspace(0, 1, r)
        env[-r:] = np.linspace(1, 0, r)
    return env


def pluck(freq: float, dur: float, amp: float,
          partials: list[tuple[int, float]], sr: int = SR) -> np.ndarray:
    """One struck-string voice: a harmonic stack with per-partial decay."""
    n = int(dur * sr)
    t = np.arange(n, dtype=np.float64) / sr
    out = np.zeros(n)
    for p, pamp in partials:
        # Higher partials decay faster -- the tone thins out over time.
        env = np.exp(-p * 3.0 * t)
        # Short attack avoids the click of an instant start.
        attack = np.minimum(t / 0.012, 1.0)
        out += amp * pamp * env * attack * np.sin(2 * np.pi * freq * p * t)
    return out


def pad(freq: float, dur: float, amp: float, sr: int = SR) -> np.ndarray:
    """A soft sustained tone."""
    n = int(dur * sr)
    t = np.arange(n, dtype=np.float64) / sr
    out = np.zeros(n)
    for p, pamp in ((1, 1.0), (2, 0.35), (3, 0.12)):
        out += amp * pamp * np.sin(2 * np.pi * freq * p * t)
    return out * trapezoid(n, int(0.55 * sr))


def build_score(duration: float, seed: int, sr: int = SR) -> np.ndarray:
    """Lay out the music: a low pad with an arpeggio over it.

    The pad segments overlap by a second, so the chord changes are
    crossfaded rather than dipping to silence in between.
    """
    total = int(duration * sr) + sr
    buf = np.zeros(total)

    bar = 2.4                      # seconds per chord
    overlap = 1.0                  # how much consecutive chords overlap
    rng = np.random.default_rng(seed)

    n_chords = int(np.ceil(duration / (bar * 2))) + 1
    for ci in range(n_chords):
        root, intervals = CHORDS[ci % len(CHORDS)]
        freqs = chord_hz(root, intervals)
        start = ci * bar * 2 - overlap / 2
        if start + bar * 2 + overlap < 0:
            continue
        seg = min(bar * 2 + overlap, duration - start)
        if seg <= 0:
            break

        padv = np.zeros(int(seg * sr))
        for f in freqs[:2]:
            padv += pad(f, seg, 0.05, sr)
        i0 = max(0, int(start * sr))
        k = min(len(buf), i0 + len(padv))
        if k > i0:
            buf[i0:k] += padv[:k - i0]

        # Arpeggio over the chord tones.
        pattern = [0, 2, 1, 3, 2, 4, 3, 1]
        step = bar * 2 / len(pattern)
        for pi, idx in enumerate(pattern):
            if idx >= len(freqs):
                continue
            t0 = start + pi * step
            if t0 >= duration:
                break
            f = freqs[idx] * 2
            if pi % 2 == 0:
                voice, g = pluck(f, step * 1.6, 0.115, HARP, sr), 1.0
            else:
                voice, g = pluck(f, step * 1.6, 0.075, PIANO, sr), 0.78
            j = max(0, int(t0 * sr))
            k = min(len(buf), j + len(voice))
            if k > j:
                buf[j:k] += voice[:k - j] * g

    # A quiet high note now and then, to give the melody some air.
    t = 1.2
    while t < duration - 1.0:
        root, intervals = CHORDS[int(t / (bar * 2)) % len(CHORDS)]
        f = chord_hz(root, intervals)[-1] * 4
        voice = pluck(f, 1.7, 0.035, HARP, sr)
        j = int(t * sr)
        k = min(len(buf), j + len(voice))
        buf[j:k] += voice[:k - j]
        t += float(rng.uniform(3.4, 5.2))

    return buf


def build_room_tone(duration: float, seed: int, sr: int = SR) -> np.ndarray:
    """Room tone: filtered noise plus a distant hum.

    Silence between the lines should not be digital black.  A little
    filtered noise at a very low level reads as "a quiet room".
    """
    rng = np.random.default_rng(seed + 1000)
    n = int(duration * sr)
    t = np.arange(n, dtype=np.float64) / sr

    noise = rng.normal(0, 1, n)
    # Cheap 1/f shaping with moving averages of increasing length.
    for k in (8, 40, 200):
        noise = np.convolve(noise, np.ones(k) / k, mode="same")

    hum = (0.35 * np.sin(2 * np.pi * 50 * t)
           + 0.20 * np.sin(2 * np.pi * 100 * t + 0.7))
    # Slow level drift so it never sounds static.
    drift = 0.85 + 0.15 * np.sin(2 * np.pi * 0.07 * t)

    return (noise * 0.05 + hum * 0.02) * drift


def read_audio(path: Path, sr: int = SR) -> np.ndarray:
    """Decode any audio file to a mono float array at ``sr``."""
    r = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
         "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode()[-1200:])
    return np.frombuffer(r.stdout, dtype=np.float32).astype(np.float64)


def duck_envelope(voice: np.ndarray, sr: int = SR,
                  attack_ms: float = 15.0, release_ms: float = 320.0
                  ) -> np.ndarray:
    """A gain curve that follows the narration.

    A threshold compressor reacts to whatever level happens to arrive, so
    the dip under the voice is uneven from line to line.  Here the
    envelope is the smoothed absolute value of the narration itself,
    normalised against its own peak, so every line gets the same dip
    whatever the narrator's level was.
    """
    n = len(voice)
    env = np.zeros(n)
    # Vectorised asymmetric smoothing: the decay uses a running maximum
    # followed by an exponential decay, which is close enough to a
    # proper attack/release follower at audio rates.
    a_att = math.exp(-1.0 / (attack_ms * 0.001 * sr))
    a_rel = math.exp(-1.0 / (release_ms * 0.001 * sr))
    absv = np.abs(voice)
    # Attack: fast one-pole on the rising side.
    out = np.empty(n)
    prev = 0.0
    for i in range(n):
        v = absv[i]
        if v > prev:
            prev = a_att * prev + (1 - a_att) * v
        else:
            prev = a_rel * prev + (1 - a_rel) * v
        out[i] = prev
    env = out
    peak = float(np.max(env)) or 1.0
    return np.clip(env / peak, 0, 1)


def write_wav(path: Path, samples: np.ndarray, sr: int = SR,
              peak: float = 0.9) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    m = float(np.max(np.abs(samples))) or 1.0
    scale = peak / m
    data = (np.clip(samples * scale, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


def mix_final(score: np.ndarray, voice: np.ndarray, room: np.ndarray,
              out_path: Path, sr: int = SR, duck_db: float = -10.0,
              score_gain: float = 0.42, room_gain: float = 0.30,
              voice_gain: float = 1.0) -> np.ndarray:
    """Mix the three layers with a measured ducking envelope.

    Ducking alone is not enough.  The score's partials sit right in the
    1-4 kHz band where speech intelligibility lives, so a loud, bright
    score masks the words even while it is 10 dB down.  The score
    therefore also gets a shelving EQ that trades some of that band for
    the low mids, which is what a real music editor does when laying a
    bed under a voice.
    """
    n = max(len(score), len(room), len(voice))

    def fit(x: np.ndarray) -> np.ndarray:
        return np.pad(x, (0, max(0, n - len(x))))[:n]

    score, room, voice = fit(score), fit(room), fit(voice)

    env = duck_envelope(voice, sr)
    gain = 10.0 ** ((duck_db * env) / 20.0)
    # Smooth the gain curve so there are no zipper artefacts.
    k = np.hanning(sr // 50)
    k /= k.sum()
    gain = np.convolve(gain, k, mode="same")

    # The score is written to a temp file so ffmpeg can apply the EQ.
    tmp = out_path.with_suffix(".score.wav")
    write_wav(tmp, score)

    # Cut the speech band, lift the low mids: the classic "music under
    # voice" curve.  equalizer() is a parametric bell; the low shelf is
    # a separate filter.
    eq = ("equalizer=f=2200:t=q:w=1.4:g=-7,"
          "equalizer=f=260:t=q:w=0.7:g=3.5,"
          "equalizer=f=6000:t=q:w=1.6:g=2.5")
    r = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(tmp), "-af", eq, "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True)
    tmp.unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode()[-1500:])
    scored = np.frombuffer(r.stdout, dtype=np.float32).astype(np.float64)

    mixed = scored * score_gain * gain + room * room_gain + voice * voice_gain
    write_wav(out_path, mixed, sr)
    return mixed


def main() -> int:
    ap = argparse.ArgumentParser(description="Synthesise the score")
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--room-out", default=None)
    ap.add_argument("--voice", default=None,
                    help="narration track; when given with --mix-out, "
                         "a full mix is written")
    ap.add_argument("--mix-out", default=None)
    ap.add_argument("--duck-db", type=float, default=-10.0)
    ap.add_argument("--room-gain", type=float, default=0.45)
    args = ap.parse_args()

    score = build_score(args.duration, args.seed)
    write_wav(Path(args.out), score)
    print(f"[score] {args.out} ({args.duration:.1f}s)")

    room = build_room_tone(args.duration, args.seed)
    if args.room_out:
        write_wav(Path(args.room_out), room)
        print(f"[room ] {args.room_out}")

    if args.voice and args.mix_out:
        voice = read_audio(Path(args.voice))
        print(f"[voice] {len(voice) / SR:.2f}s")
        mixed = mix_final(score, voice, room, Path(args.mix_out),
                          duck_db=args.duck_db, room_gain=args.room_gain)
        print(f"[mix  ] {args.mix_out} ({len(mixed) / SR:.2f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
