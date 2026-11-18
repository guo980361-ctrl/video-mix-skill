#!/usr/bin/env python3
"""Offline encryption for JianYing (剪映专业版) drafts.

JianYing encrypts its draft files from 6.0 onward. The scheme was
reverse-engineered from VideoFusion-macOS's `libvideoeditor.dylib`
(`lvve::EncryptUtils::decrypt`) and is fully offline -- no hook, no
Frida, no runtime keys:

  1. An encrypted `draft_info.json` / `draft_meta_info.json` is a single
     line of base64-ish text.
  2. That text smuggles 48 characters: 4 characters at each of 12 fixed
     offsets. The first 8 blocks (32 chars) are the AES-256 key, the last
     4 blocks (16 chars) are the GCM nonce. They are ASCII, not random
     binary.
  3. Removing those 48 characters leaves the real ciphertext, base64
     encoded. Decoding it gives `ciphertext || GCM-tag(16 bytes)`.
  4. AES-256-GCM(key, nonce) decrypts it.

Because the key travels inside the ciphertext, this works on any machine
without JianYing installed -- no native library, no app dependency.

Attribute the reverse engineering to si-wang-wen-xuan/JY-Draft-Decrypt;
the algorithm itself is public knowledge, and this is an interop detail
rather than a secret.

Requires `pycryptodome` for AES.
"""

from __future__ import annotations

import base64
import json
import os
import random
import string
import sys
from pathlib import Path

OFFSETS = (0x00, 0x07, 0x14, 0x21, 0x28, 0x2F, 0x3B, 0x42, 0x4C, 0x59,
           0x63, 0x7F)
"""12 offsets, 4 characters each."""

MIN_LEN = 131
"""The encryptor refuses text shorter than this."""

TAG_LEN = 16
"""GCM authentication tag length."""


def _aes():
    try:
        from Crypto.Cipher import AES
    except ImportError as exc:
        raise RuntimeError(
            "缺少 pycryptodome，请先执行: pip install pycryptodome") from exc
    return AES


def derive_key_iv(text: str) -> tuple[bytes, bytes]:
    """Recover the AES key and GCM nonce from the encrypted text."""
    key = "".join(text[o:o + 4] for o in OFFSETS[:8]).encode("latin1")
    iv = "".join(text[o:o + 4] for o in OFFSETS[8:]).encode("latin1")
    return key, iv


def strip_muxed(text: str) -> str:
    """Remove the 48 smuggled key/iv characters."""
    return "".join(c for i, c in enumerate(text)
                   if not any(o <= i < o + 4 for o in OFFSETS))


def looks_plaintext(data: bytes) -> bool:
    return data.lstrip()[:1] == b"{"


def decrypt_bytes(data: bytes) -> bytes:
    """Decrypt an encrypted draft file; returns the plaintext bytes."""
    if looks_plaintext(data):
        raise ValueError("文件已是明文 JSON，无需解密")

    text = data.decode("latin1").strip()
    if len(text) < MIN_LEN:
        raise ValueError(f"文件过短 ({len(text)} < {MIN_LEN})，格式不符")

    key, iv = derive_key_iv(text)
    stripped = strip_muxed(text)
    if len(stripped) % 4 != 0:
        raise ValueError("剔除密钥后 base64 长度非 4 的倍数，格式不符")

    raw = base64.b64decode(stripped, validate=True)
    if len(raw) <= TAG_LEN:
        raise ValueError(f"密文长度异常 ({len(raw)})")

    ct, tag = raw[:-TAG_LEN], raw[-TAG_LEN:]
    AES = _aes()
    return AES.new(key, AES.MODE_GCM, nonce=iv).decrypt_and_verify(ct, tag)


def _mux(b64: str, key: bytes, iv: bytes) -> str:
    """Place the key/iv characters at the fixed offsets, filling the rest
    with the base64 payload.

    The 48 smuggled characters occupy 12 fixed 4-character slots spread
    through the text; every other position holds one base64 character.
    So the final text is exactly `len(b64) + 48` characters long, and it
    must be at least MIN_LEN -- which means the base64 payload needs at
    least 83 characters.
    """
    total = len(b64) + 48
    slot = {o + j: blk for blk, o in enumerate(OFFSETS) for j in range(4)}
    kchars = key.decode("latin1")
    ichars = iv.decode("latin1")
    out: list[str] = []
    ki = ii = bi = 0
    for idx in range(total):
        if idx in slot:
            blk = slot[idx]
            if blk < 8:
                out.append(kchars[ki])
                ki += 1
            else:
                out.append(ichars[ii])
                ii += 1
        else:
            out.append(b64[bi])
            bi += 1
    return "".join(out)


def encrypt_bytes(plain: bytes, *, seed: int | None = None) -> bytes:
    """Encrypt plaintext into JianYing's encrypted draft format.

    The key and nonce are random ASCII, because the scheme derives them
    from the ciphertext text itself. A payload too short to fill the
    offset layout is padded, since the encryptor requires at least
    MIN_LEN characters.
    """
    AES = _aes()
    rng = random.Random(seed) if seed is not None else random.SystemRandom()
    pool = string.ascii_letters + string.digits
    key = bytes(ord(rng.choice(pool)) for _ in range(32))
    iv = bytes(ord(rng.choice(pool)) for _ in range(16))

    # The base64 payload is interleaved through the whole text, so it must
    # be long enough that the final text reaches MIN_LEN characters.
    min_b64 = MIN_LEN - 48
    while True:
        ct, tag = AES.new(key, AES.MODE_GCM, nonce=iv).encrypt_and_digest(plain)
        b64 = base64.b64encode(ct + tag).decode()
        if len(b64) >= min_b64:
            break
        # Not enough payload. Grow it without changing the decoded value:
        # for JSON, pad with whitespace *inside* the object, which every
        # JSON parser ignores.
        if plain.lstrip()[:1] == b"{" and plain.rstrip().endswith(b"}"):
            plain = plain[:-1] + b" " * 64 + b"}"
        else:
            plain = plain + b" " * 64

    text = _mux(b64, key, iv)
    return text.encode("latin1")


def encrypt_json(obj: dict) -> bytes:
    """Encrypt a JSON-serialisable object."""
    plain = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    return encrypt_bytes(plain)


def decrypt_json_file(path: str | os.PathLike) -> dict:
    """Read and decrypt a draft JSON file."""
    data = Path(path).read_bytes()
    return json.loads(decrypt_bytes(data).decode("utf-8"))


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="剪映草稿离线加解密（无需安装剪映）")
    ap.add_argument("files", nargs="+", help="草稿 JSON 文件")
    ap.add_argument("-p", "--print", dest="to_stdout", action="store_true",
                    help="解密结果打印到 stdout")
    ap.add_argument("-i", "--inplace", action="store_true",
                    help="原地覆盖（用明文替换密文）")
    ap.add_argument("-e", "--encrypt", action="store_true",
                    help="把明文文件加密（与解密互逆）")
    ap.add_argument("--pretty", action="store_true", help="JSON 缩进美化")
    args = ap.parse_args()

    rc = 0
    for path in args.files:
        p = Path(path)
        try:
            raw = p.read_bytes()
            if args.encrypt:
                if not looks_plaintext(raw):
                    raise ValueError("已经是密文，无需加密")
                out = encrypt_bytes(raw)
                target = p if args.inplace else p.with_suffix(".enc.json")
            else:
                out = decrypt_bytes(raw)
                target = p if args.inplace else p.with_suffix(".decrypted.json")
            if args.pretty:
                out = json.dumps(json.loads(out.decode("utf-8")),
                                 ensure_ascii=False, indent=2).encode("utf-8")
            if args.to_stdout:
                sys.stdout.write(out.decode("utf-8", "replace") + "\n")
            else:
                target.write_bytes(out)
                print(f"[ok] {path} -> {target} ({len(out)} bytes)")
        except Exception as exc:
            print(f"[fail] {path}: {exc}", file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
