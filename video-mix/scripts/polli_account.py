#!/usr/bin/env python3
"""Inspect and manage the Pollinations free balance.

The Pollinations key-backed API bills against an account balance. The
balance starts at 0.25 USD for a new account and can be topped up without
paying anything through the quests system, so a long-running workflow is
possible if you know how much is left and how to earn more.

Commands:

    balance          show the current balance and the free quests that can
                     still be claimed
    usage            show recent generations and what each one cost
    quests           list every quest with its reward, marking what is
                     still available
    models           list the image and video models this skill can use
    key              show where the key was found (never prints the key)

Every command works without a key except the ones that read account data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pollinations_client import (  # noqa: E402
    IMAGE_MODELS,
    VIDEO_MODELS,
    PollinationsError,
    available_quests,
    balance,
    find_key,
    quests,
    save_key,
    usage,
)


def cmd_balance(args) -> int:
    key = find_key(args.key)
    if not key:
        print("未找到 Pollinations API key。")
        print("  免费注册：https://enter.pollinations.ai/keys")
        print("  注册后运行：python scripts/polli_account.py key --set <你的key>")
        return 1
    try:
        data = balance(key)
    except PollinationsError as exc:
        print(f"[balance] {exc}", file=sys.stderr)
        return 2

    print("=== 账户余额 ===")
    for k, v in data.items():
        if isinstance(v, (int, float, str)):
            print(f"  {k}: {v}")

    try:
        avail = available_quests(key)
    except PollinationsError:
        avail = []
    if avail:
        total = sum(float(q.get("rewardAmount") or 0) for q in avail)
        print(f"\n=== 可免费领取的 quest（合计 +{total:.2f} USD）===")
        for q in sorted(avail, key=lambda x: -float(x.get("rewardAmount") or 0)):
            print(f"  +{q.get('rewardAmount'):>6} USD  {q.get('title')}")
        print("\n  在 https://pollinations.ai/community 完成对应任务即可到账。")
    return 0


def cmd_usage(args) -> int:
    key = find_key(args.key)
    if not key:
        print("未找到 Pollinations API key，无法查询用量。", file=sys.stderr)
        return 1
    try:
        rows = usage(key, args.limit)
    except PollinationsError as exc:
        print(f"[usage] {exc}", file=sys.stderr)
        return 2
    if not rows:
        print("暂无用量记录。")
        return 0
    print(f"=== 最近 {len(rows)} 条用量 ===")
    for r in rows[: args.limit]:
        ts = str(r.get("timestamp") or r.get("createdAt") or "")[:19]
        model = r.get("model") or r.get("modelName") or "?"
        cost = r.get("cost") or r.get("amount") or r.get("price") or 0
        status = r.get("status") or ""
        print(f"  {ts:20s} {model:40s} ${float(cost):.6f} {status}")
    total = sum(float(r.get("cost") or r.get("amount") or r.get("price") or 0)
                for r in rows[: args.limit])
    print(f"\n  合计: ${total:.6f}")
    return 0


def cmd_quests(args) -> int:
    key = find_key(args.key)
    try:
        rows = quests(key)
    except PollinationsError as exc:
        print(f"[quests] {exc}", file=sys.stderr)
        return 2
    avail = [q for q in rows if q.get("state") == "available"]
    done = [q for q in rows if q.get("state") == "completed"]

    # A quest that requires spending money is not a free source of balance.
    # Only quests whose wording implies no out-of-pocket cost are listed as
    # genuinely free; the rest are shown separately so nobody mistakes
    # "top up $100" for a giveaway.
    paid_markers = ("top up", "top-up", "auto top-up", "paid request",
                     "paid usage")
    free = [q for q in avail
            if not any(m in (q.get("title") or "").lower() for m in paid_markers)]
    paid = [q for q in avail if q not in free]

    free_total = sum(float(q.get("rewardAmount") or 0) for q in free)
    paid_total = sum(float(q.get("rewardAmount") or 0) for q in paid)

    print(f"=== Quest 清单 ===")
    print(f"  零成本可领: {len(free)} 个（合计 +{free_total:.2f} USD）")
    print(f"  需先消费: {len(paid)} 个（合计 +{paid_total:.2f} USD）"
          f"  ← 这些要求先充值或产生付费流量，不算免费额度")

    print("\n--- 零成本可领（推荐）---")
    for q in sorted(free, key=lambda x: -float(x.get("rewardAmount") or 0)):
        print(f"  +{float(q.get('rewardAmount') or 0):>6.2f} USD  "
              f"[{q.get('category')}] {q.get('title')}")

    print("\n--- 需先消费 ---")
    for q in sorted(paid, key=lambda x: -float(x.get("rewardAmount") or 0)):
        print(f"  +{float(q.get('rewardAmount') or 0):>6.2f} USD  "
              f"[{q.get('category')}] {q.get('title')}")

    print(f"\n--- 已完成（{len(done)} 个，仅供参考）---")
    for q in sorted(done, key=lambda x: -float(x.get("rewardAmount") or 0))[:10]:
        print(f"  +{float(q.get('rewardAmount') or 0):>6.2f} USD  {q.get('title')}")
    return 0


def cmd_models(args) -> int:
    print(f"=== 图片模型（{len(IMAGE_MODELS)} 个，均已实测可用）===")
    for m in IMAGE_MODELS:
        print(f"  {m}")
    print(f"\n=== 视频模型（{len(VIDEO_MODELS)} 个，均支持 start_frame 图生视频）===")
    for m in VIDEO_MODELS:
        print(f"  {m}")
    print("\n提示：视频模型按调用计费，用之前先跑 `balance` 看剩余额度。")
    return 0


def cmd_key(args) -> int:
    key = find_key(args.key)
    if args.set:
        path = save_key(args.set)
        print(f"[key] 已保存到 {path}（权限 600）")
        print("  也可改用环境变量：export POLLINATIONS_KEY=...")
        return 0
    if not key:
        print("未找到 key。")
        print("  免费注册：https://enter.pollinations.ai/keys")
        print("  保存：python scripts/polli_account.py key --set <你的key>")
        return 1
    src = "命令行参数"
    import os
    if os.environ.get("POLLINATIONS_KEY"):
        src = "环境变量 POLLINATIONS_KEY"
    else:
        f = Path.home() / ".workbuddy" / ".pollinations_key"
        if f.exists():
            src = f"文件 {f}"
    masked = f"{key[:6]}...{key[-4:]}" if len(key) > 12 else "***"
    print(f"已找到 key（来源：{src}）")
    print(f"  值：{masked}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Pollinations 额度与模型管理")
    ap.add_argument("--key", help="API key（默认从环境变量或文件读取）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("balance", help="查看余额与可领取的 quest").set_defaults(
        func=cmd_balance)
    p = sub.add_parser("usage", help="查看近期用量与花费")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_usage)
    sub.add_parser("quests", help="列出全部 quest").set_defaults(func=cmd_quests)
    sub.add_parser("models", help="列出可用模型").set_defaults(func=cmd_models)
    p = sub.add_parser("key", help="查看或保存 key")
    p.add_argument("--set", help="保存 key 到本地文件")
    p.set_defaults(func=cmd_key)

    args = ap.parse_args()
    try:
        return args.func(args)
    except PollinationsError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
