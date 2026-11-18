#!/usr/bin/env python3
"""Image-to-video with a tiered set of backends.

Backend order for ``--backend auto``:

1. **pollinations** - ``gen.pollinations.ai``. Needs a free API key. Gives
   access to Veo 3.1, Seedance 2.x, Kling 3.0 and Wan 3.0, none of which
   touch the Hugging Face ZeroGPU quota. This is the long-term option.
2. **ltx** - the ``Lightricks/ltx-video-distilled`` Space. No key, but it
   spends ZeroGPU quota, which is capped at 2 minutes/day anonymously.
3. **wan** - ``Upsampler/wan-2-2-14b-image-to-video``. Same quota pool.
4. **sdxt** - a CPU-resident Space. Consumes no quota but is far too slow
   for production (measured: still running after 18 minutes).

``--backend local`` is the offline escape hatch: it animates the still image
with a Ken Burns move via ffmpeg, costs nothing and takes about a second.

Usage:
    python gen_video.py --image face.jpg --prompt "she turns and smiles" --out clip.mp4
    python gen_video.py --image face.jpg --prompt "..." --out c.mp4 --backend ltx
    python gen_video.py --image face.jpg --prompt "..." --out d.mp4 --backend pollinations
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pollinations_client import (  # noqa: E402
    VIDEO_MODELS,
    PollinationsError,
    find_key,
    gen_video as polli_gen_video,
)
from vmix_common import (hf_token, install_deps, out_path, quota_hint,  # noqa: E402
                         retry)

LTX_SPACE = "Lightricks/ltx-video-distilled"
WAN_SPACE = "Upsampler/wan-2-2-14b-image-to-video"
# CPU-resident fallback: consumes no ZeroGPU quota, but quality is lower.
SDXT_SPACE = "waloneai/SDXT-Image-To-Video"

NEGATIVE = "worst quality, low quality, blurry, distorted, flickering, watermark, text"


def _client(space: str, timeout: float = 900.0):
    import httpx
    from gradio_client import Client

    kwargs = {}
    token = hf_token()
    if token:
        kwargs["hf_token"] = token
    return Client(space, verbose=False,
                  httpx_kwargs={"timeout": httpx.Timeout(timeout, connect=60.0)},
                  **kwargs)


def _save(result, out: Path) -> Path:
    """Pull the video path out of whatever shape gradio returns."""
    src = None
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, dict):
        src = result.get("video") or result.get("output")
        if isinstance(src, dict):
            src = src.get("path") or src.get("url")
    elif isinstance(result, (str, Path)):
        src = result
    if not src:
        raise RuntimeError(f"no video in result: {result!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(str(src), str(out))
    return out


def gen_ltx(image: Path, prompt: str, out: Path, width: int, height: int,
            duration: int, seed: int | None) -> Path:
    from gradio_client import handle_file

    c = _client(LTX_SPACE)

    def call():
        kw = dict(
            prompt=prompt,
            negative_prompt=NEGATIVE,
            input_image_filepath=handle_file(str(image)),
            mode="image-to-video",
            duration_ui=duration,
            ui_frames_to_use=9,
            randomize_seed=seed is None,
            ui_guidance_scale=1.0,
            improve_texture_flag=True,
            height_ui=height,
            width_ui=width,
            api_name="/image_to_video",
        )
        if seed is not None:
            kw["seed_ui"] = seed
        return c.predict(**kw)

    res = retry(call, tries=3, base=5.0, label="ltx image_to_video")
    return _save(res, out)


def gen_wan(image: Path, prompt: str, out: Path, width: int, height: int,
            duration: int, seed: int | None) -> Path:
    """Wan 2.2 image-to-video space.

    Endpoint signature differs between space revisions, so probe the API
    and map parameters by name.
    """
    from gradio_client import handle_file

    c = _client(WAN_SPACE, timeout=1200.0)
    api = c.view_api(return_format="dict")
    named = api.get("named_endpoints") or {}

    def call():
        for name in ("/generate_video", "/image_to_video", "/infer", "/predict"):
            if name not in named:
                continue
            params = named[name].get("parameters") or []
            kw = {}
            for p in params:
                if p.get("parameter_has_default"):
                    continue
                pn = p["parameter_name"]
                if "image" in pn and pn != "end_image":
                    kw[pn] = handle_file(str(image))
                elif pn == "end_image":
                    continue  # optional last frame
                elif "prompt" in pn:
                    kw[pn] = prompt
                elif "negative" in pn:
                    kw[pn] = NEGATIVE
                elif "seed" in pn:
                    kw[pn] = seed if seed is not None else 42
                elif "duration" in pn:
                    kw[pn] = max(1.0, float(duration))
                elif "steps" in pn:
                    kw[pn] = 6
                else:
                    kw[pn] = ""
            return c.predict(**kw, api_name=name)
        raise RuntimeError(f"no usable endpoint in {WAN_SPACE}: {list(named)}")

    res = retry(call, tries=2, base=8.0, label="wan image_to_video")
    return _save(res, out)


def gen_sdxt(image: Path, prompt: str, out: Path, width: int, height: int,
             duration: int, seed: int | None) -> Path:
    """CPU-resident SDXT image-to-video.

    Does NOT consume ZeroGPU quota, but it is impractically slow: measured
    18+ minutes for a 2s clip that LTX produces in 18 seconds. Treat it as
    a last resort; for a zero-quota fallback use still_to_clip.py instead
    (local Ken Burns, 1-2 seconds).
    """
    from gradio_client import handle_file

    c = _client(SDXT_SPACE, timeout=1200.0)

    def call():
        return c.predict(image_path=handle_file(str(image)),
                         seed=seed if seed is not None else 666666,
                         fps=25,
                         duration=max(1, duration),
                         use_duration=True,
                         api_name="/generate_video")

    res = retry(call, tries=1, base=8.0, label="sdxt image_to_video")
    return _save(res, out)


def gen_pollinations(image: Path, prompt: str, out: Path, width: int,
                     height: int, duration: int, seed: int | None,
                     model: str | None = None) -> Path:
    """Key-backed video generation. No ZeroGPU quota involved."""
    key = find_key()
    if not key:
        raise PollinationsError(
            "Pollinations 视频后端需要 API key（免费注册即得 0.25 USD，"
            "且不消耗 HF 额度）。\n"
            "  打开 https://enter.pollinations.ai/keys 创建 key，"
            "然后 export POLLINATIONS_KEY=pk_xxx"
        )
    mdl = model or VIDEO_MODELS[1]
    return polli_gen_video(prompt, out, image=image, model=mdl,
                           width=width, height=height, duration=duration,
                           key=key)


def gen_local(image: Path, prompt: str, out: Path, width: int,
              height: int, duration: int, seed: int | None) -> Path:
    """Offline fallback: animate the still with a Ken Burns move."""
    from still_to_clip import main as still_main
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp_out = Path(td) / "clip.mp4"
        argv = sys.argv
        sys.argv = ["still_to_clip.py", "--image", str(image),
                    "--out", str(tmp_out), "--seconds", str(max(1, duration)),
                    "--width", str(width), "--height", str(height),
                    "--motion", "zoom-in"]
        try:
            rc = still_main()
        finally:
            sys.argv = argv
        if rc != 0 or not tmp_out.exists():
            raise RuntimeError("local Ken Burns fallback failed")
        # shutil.copy raises FileNotFoundError when the destination
        # directory does not exist, so create it first.
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(tmp_out, out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Image-to-video with tiered backends")
    ap.add_argument("--image", required=True)
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--backend", default="auto",
                    choices=["auto", "pollinations", "ltx", "wan", "sdxt", "local"])
    ap.add_argument("--model", default=None,
                    help="pollinations backend model, e.g. "
                         f"{VIDEO_MODELS[1]}")
    ap.add_argument("--key", help="Pollinations API key (overrides env/file)")
    ap.add_argument("--width", type=int, default=704)
    ap.add_argument("--height", type=int, default=512)
    ap.add_argument("--duration", type=int, default=2)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    install_deps(quiet=True)
    image = Path(args.image).expanduser().resolve()
    if not image.exists():
        print(f"[i2v] image not found: {image}", file=sys.stderr)
        return 1
    out = out_path(args.out)

    # The keyed backend is tried first because it does not compete for the
    # ZeroGPU daily allowance, which is the scarcest resource in this
    # pipeline. The HF Spaces remain as no-key fallbacks.
    if args.backend == "auto":
        order = ["pollinations", "ltx", "wan"]
        if not find_key(args.key):
            order = ["ltx", "wan"]
    else:
        order = [args.backend]

    fns = {
        "pollinations": lambda i, p, o, w, h, d, s: gen_pollinations(
            i, p, o, w, h, d, s, args.model),
        "ltx": gen_ltx,
        "wan": gen_wan,
        "sdxt": gen_sdxt,
        "local": gen_local,
    }

    t0 = time.time()
    last = None
    path = None
    for idx, backend in enumerate(order):
        print(f"[i2v] backend={backend} prompt={args.prompt[:60]}")
        try:
            path = fns[backend](image, args.prompt, out, args.width,
                                args.height, args.duration, args.seed)
            break
        except Exception as exc:
            last = exc
            print(f"[i2v] backend {backend} failed:", file=sys.stderr)
            if isinstance(exc, PollinationsError):
                print(str(exc), file=sys.stderr)
            else:
                print(quota_hint(exc), file=sys.stderr)
            if idx + 1 < len(order):
                print(f"[i2v] falling back to {order[idx + 1]}", file=sys.stderr)

    if path is None:
        print(f"[i2v] all backends failed: {last}", file=sys.stderr)
        print("  最后兜底：--backend local（本地运镜，零额度、零网络）",
              file=sys.stderr)
        return 2

    kb = path.stat().st_size / 1024
    print(f"[i2v] OK -> {path}  ({kb:.0f} KB, {time.time() - t0:.1f}s)")
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
