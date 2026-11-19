#!/usr/bin/env python3
"""Local Stable Video Diffusion image-to-video on CPU.

This is the offline fallback for when no cloud video model is reachable.
It runs ``stabilityai/stable-video-diffusion-img2vid-xt`` on the CPU, so it
needs no key, no quota and no network once the weights are cached.

Reality check, measured on an 8GB Intel Mac (2026-11-19): expect 20-40
minutes per shot at 25 frames. That is slow, and the result is softer than
Veo or Seedance. But it is genuine diffusion motion -- the subject moves --
not a camera move over a still.

Requires a Python 3.12 build with torch 2.2.2 installed (PyTorch stopped
shipping macOS x86_64 wheels for Python 3.13).

Usage:
    python local_svd.py --image shot.jpg --prompt "the cat walks forward" \
        --out clip.mp4 --frames 25 --height 512 --width 288
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HOME", "/tmp/hfcache")


def main() -> int:
    ap = argparse.ArgumentParser(description="Local SVD image-to-video")
    ap.add_argument("--image", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--frames", type=int, default=25)
    ap.add_argument("--height", type=int, default=512)
    ap.add_argument("--width", type=int, default=288)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--model",
                    default="stabilityai/stable-video-diffusion-img2vid-xt")
    args = ap.parse_args()

    import numpy as np
    import torch
    from diffusers import StableVideoDiffusionPipeline
    from diffusers.utils import export_to_video, load_image
    from PIL import Image

    print(f"[svd] torch {torch.__version__} | threads {torch.get_num_threads()}")
    print(f"[svd] loading {args.model} (this takes a while the first time)")

    t0 = time.time()
    pipe = StableVideoDiffusionPipeline.from_pretrained(
        args.model, torch_dtype=torch.float32, variant="fp16")
    pipe = pipe.to("cpu")
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_slicing()
    print(f"[svd] pipeline ready in {time.time() - t0:.0f}s")

    image = load_image(args.image).resize(
        (args.width, args.height), Image.LANCZOS)

    generator = None
    if args.seed is not None:
        generator = torch.manual_seed(args.seed)

    t0 = time.time()
    frames = pipe(image, num_frames=args.frames, num_inference_steps=args.steps,
                  decode_chunk_size=4, generator=generator).frames[0]
    print(f"[svd] generated {len(frames)} frames in {time.time() - t0:.0f}s")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    export_to_video(frames, str(out), fps=8)
    print(f"[svd] wrote {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
