"""Pollinations client for the video-mix skill.

Two generations of the API exist and they behave very differently:

* ``image.pollinations.ai/prompt/...``  - the legacy anonymous endpoint.
  No key, no quota accounting, but every render is watermarked and calls
  are rate limited to roughly one image per 45 seconds per IP.
* ``gen.pollinations.ai/...``          - the current endpoint. Requires an
  API key, has no watermark, and exposes image, video, audio and text
  generation. Usage is billed against an account balance, and the balance
  can be topped up for free through the quests system.

This module wraps the second one, because it is the only variant that is
both unwatermarked and usable in a long-running workflow.

Key lookup order:

1. ``POLLINATIONS_KEY`` environment variable
2. ``~/.workbuddy/.pollinations_key`` file
3. the ``--key`` argument, when the caller has one
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

GEN_BASE = "https://gen.pollinations.ai"
LEGACY_IMAGE = "https://image.pollinations.ai/prompt/"

# Models that answered real requests on 2026-11-18. The catalogue endpoint
# lists far more; these are the ones confirmed to render.
IMAGE_MODELS = [
    "tongyi-mai/z-image-turbo",
    "black-forest-labs/flux.1-schnell",
    "black-forest-labs/flux-3-image-klein",
    "qwen/qwen-image-2.1-turbo",
    "bytedance/seedream-5.0-flash",
    "google/gemini-nano-banana-2.1",
]

# Video models confirmed present in the catalogue. Every one of these
# supports ``start_frame``, which is what makes them image-to-video.
VIDEO_MODELS = [
    "google/veo-3.1-fast",
    "bytedance/seedance-2.0-fast",
    "bytedance/seedance-2.0-mini",
    "kwaivgi/kling-v3.0-std",
    "alibaba/wan-2.2-fast",
    "alibaba/wan-3.0",
    "black-forest-labs/flux-3-max",
    "x-ai/grok-imagine-video-1.5-lite",
    "mirelo/sfx-2.0",
]


class PollinationsError(RuntimeError):
    """Raised when the API rejects a request."""


def find_key(explicit: str | None = None) -> str | None:
    """Locate the Pollinations API key.

    Order: explicit argument, then POLLINATIONS_KEY in the environment,
    then ~/.workbuddy/.pollinations_key.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get("POLLINATIONS_KEY")
    if env and env.strip():
        return env.strip()
    key_file = Path.home() / ".workbuddy" / ".pollinations_key"
    if key_file.exists():
        val = key_file.read_text(encoding="utf-8").strip()
        if val:
            return val
    return None


def save_key(key: str) -> Path:
    """Persist a key for future runs with owner-only permissions."""
    path = Path.home() / ".workbuddy" / ".pollinations_key"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(key.strip() + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _get(url: str, key: str | None, timeout: int = 240,
         accept: str = "application/json") -> tuple[int, bytes, str]:
    headers = {"User-Agent": "video-mix-skill/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if accept != "application/json":
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return exc.code, body, exc.headers.get("Content-Type", "")
    except Exception as exc:
        raise PollinationsError(f"request failed: {exc}") from exc


def _explain(code: int, body: bytes) -> str:
    """Turn an API error body into an actionable message."""
    text = body.decode("utf-8", errors="ignore")
    try:
        data = json.loads(text)
        msg = data.get("error", {}).get("message") or data.get("message") or text
    except Exception:
        msg = text
    if code == 401:
        return (
            "Pollinations 需要 API key。免费注册后自动到账 0.25 美元额度，"
            "且无水印。\n"
            "  1. 打开 https://enter.pollinations.ai/keys 注册并创建 key\n"
            "  2. export POLLINATIONS_KEY=你的key\n"
            "  3. 或写入 ~/.workbuddy/.pollinations_key 文件\n"
            f"  服务端原话：{msg[:200]}"
        )
    if code == 402:
        return (
            "Pollinations 账户余额不足。免费额度用完后，可以通过 quests "
            "持续赚取：\n"
            "  python scripts/polli_account.py quests\n"
            f"  服务端原话：{msg[:200]}"
        )
    return f"HTTP {code}: {msg[:300]}"


# ---------------------------------------------------------------- images


def gen_image(prompt: str, out: Path, *, model: str | None = None,
              width: int = 1024, height: int = 1024,
              seed: int | None = None, key: str | None = None,
              nologo: bool = True, referrer: str = "video-mix-skill",
              timeout: int = 300, tries: int = 3) -> Path:
    """Generate an image with the key-backed endpoint (no watermark).

    Falls back to the anonymous legacy endpoint when no key is available,
    which does work but adds a watermark and a 45-second rate limit.
    """
    model = model or IMAGE_MODELS[0]
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if not key:
        return _legacy_image(prompt, out, model, width, height, seed, nologo)

    q = {
        "model": model,
        "width": str(width),
        "height": str(height),
        "referrer": referrer,
    }
    if seed is not None:
        q["seed"] = str(seed)
    if nologo:
        q["nologo"] = "true"
    url = f"{GEN_BASE}/image/{urllib.parse.quote(prompt, safe='')}?{urllib.parse.urlencode(q)}"

    last = ""
    for attempt in range(1, tries + 1):
        code, body, ctype = _get(url, key, timeout=timeout, accept="image/*")
        if code == 200 and ("image" in ctype or body[:3] == b"\xff\xd8\xff"):
            out.write_bytes(body)
            return out
        last = _explain(code, body)
        if code in (429, 500, 502, 503, 504) and attempt < tries:
            wait = 4 * attempt
            print(f"[pollinations] {code}, retry in {wait}s")
            time.sleep(wait)
            continue
        break
    raise PollinationsError(last)


def _legacy_image(prompt: str, out: Path, model: str, width: int, height: int,
                  seed: int | None, nologo: bool) -> Path:
    """Anonymous fallback: watermarked, rate limited to ~1 image / 45 s."""
    legacy_models = {"tongyi-mai/z-image-turbo": "zimage",
                     "black-forest-labs/flux.1-schnell": "flux"}
    q = {"width": str(width), "height": str(height)}
    short = legacy_models.get(model, "flux")
    q["model"] = short
    if seed is not None:
        q["seed"] = str(seed)
    if nologo:
        q["nologo"] = "true"
    url = f"{LEGACY_IMAGE}{urllib.parse.quote(prompt, safe='')}?{urllib.parse.urlencode(q)}"

    last = ""
    for attempt in range(1, 4):
        code, body, ctype = _get(url, None, timeout=180, accept="image/*")
        if code == 200 and ("image" in ctype or body[:3] == b"\xff\xd8\xff"):
            out.write_bytes(body)
            print("[pollinations] anonymous endpoint used - watermark present")
            return out
        last = _explain(code, body)
        if code == 402:
            print("[pollinations] free tier rate limit (1 image / 45s), "
                  "waiting 45s")
            time.sleep(45)
            continue
        if code in (429, 500, 502, 503, 504):
            time.sleep(4 * attempt)
            continue
        break
    raise PollinationsError(last)


# ---------------------------------------------------------------- video


def gen_video(prompt: str, out: Path, *, image: Path | None = None,
              model: str = "bytedance/seedance-2.0-fast",
              width: int = 768, height: int = 768,
              duration: int = 4, key: str | None = None,
              referrer: str = "video-mix-skill",
              timeout: int = 900) -> Path:
    """Image-to-video (or text-to-video) with the key-backed endpoint.

    The API is asynchronous for video: the first response contains a job id,
    and the result is polled from ``/v1/jobs/{id}/result``.
    """
    if not key:
        raise PollinationsError(
            "图生视频需要 Pollinations API key（免费注册即可，无水印）。\n"
            "  打开 https://enter.pollinations.ai/keys 创建 key，"
            "然后 export POLLINATIONS_KEY=你的key\n"
            "  没有 key 时请改用 scripts/still_to_clip.py（本地运镜，零额度）。"
        )

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    q = {
        "model": model,
        "width": str(width),
        "height": str(height),
        "duration": str(duration),
        "referrer": referrer,
    }
    if image:
        data = Path(image).read_bytes()
        import base64
        b64 = base64.b64encode(data).decode()
        q["image"] = f"data:image/jpeg;base64,{b64}"
    url = f"{GEN_BASE}/video/{urllib.parse.quote(prompt, safe='')}?{urllib.parse.urlencode(q)}"

    code, body, ctype = _get(url, key, timeout=timeout, accept="video/*")
    if code == 200 and ("video" in ctype or body[4:8] == b"ftyp"):
        out.write_bytes(body)
        return out

    # Asynchronous path: a job id comes back as JSON.
    try:
        data = json.loads(body.decode("utf-8", errors="ignore"))
    except Exception:
        raise PollinationsError(_explain(code, body))
    job = data.get("id") or data.get("job_id") or data.get("job")
    if not job:
        raise PollinationsError(f"unexpected video response: {str(data)[:300]}")

    print(f"[pollinations] job {job} queued, polling...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(6)
        code, body, ctype = _get(f"{GEN_BASE}/v1/jobs/{job}/result",
                                  key, timeout=120, accept="video/*")
        if code == 200 and ("video" in ctype or body[4:8] == b"ftyp"):
            out.write_bytes(body)
            return out
        if code == 200:
            try:
                d = json.loads(body.decode("utf-8", errors="ignore"))
                st = d.get("status") or d.get("state")
                if st in ("failed", "error"):
                    raise PollinationsError(f"job failed: {str(d)[:300]}")
            except PollinationsError:
                raise
            except Exception:
                pass
        elif code == 402:
            raise PollinationsError(_explain(code, body))
    raise PollinationsError(f"video job {job} did not finish within {timeout}s")


# ---------------------------------------------------------------- account


def balance(key: str | None = None) -> dict:
    """Return the account balance summary."""
    code, body, _ = _get(f"{GEN_BASE}/account/balance", key, timeout=30)
    if code != 200:
        raise PollinationsError(_explain(code, body))
    return json.loads(body.decode("utf-8"))


def usage(key: str | None = None, limit: int = 20) -> list[dict]:
    """Return recent usage records."""
    code, body, _ = _get(f"{GEN_BASE}/account/usage?format=json&limit={limit}",
                         key, timeout=30)
    if code != 200:
        raise PollinationsError(_explain(code, body))
    data = json.loads(body.decode("utf-8"))
    if isinstance(data, dict):
        return data.get("items") or data.get("usage") or []
    return data


def quests(key: str | None = None) -> list[dict]:
    """Return the quest catalogue (free ways to earn balance)."""
    code, body, _ = _get(f"{GEN_BASE}/quests/catalog", key, timeout=30)
    if code != 200:
        raise PollinationsError(_explain(code, body))
    data = json.loads(body.decode("utf-8"))
    return data.get("quests", [])


def available_quests(key: str | None = None) -> list[dict]:
    """Quests that are still claimable."""
    return [q for q in quests(key) if q.get("state") == "available"]
