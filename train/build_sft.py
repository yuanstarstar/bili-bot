# -*- coding: utf-8 -*-
"""把 raw.jsonl 变成 LLaMA-Factory / ShareGPT 格式的 SFT 数据。

任务：
  A 首评：视频信息 + 热评参考 -> 一条顶层评论
  B 楼中楼：视频 + 被回复评论 -> 一条回复
  C 私信：对方私信 -> 一条回复（从楼中楼样本派生，占比小）

用法：
  py -3 train/build_sft.py --raw data/train/raw.jsonl --out-dir data/train/sft
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.brain.judge import simhash  # noqa: E402

BAD = re.compile(r"(https?://|www\.|b23\.tv|抽奖|三连|求关注|加群|私信我|AI|人工智能|机器人|语言模型)")
AI_WORDS = ("首先", "其次", "总之", "不得不说", "综上所述", "希望对你有帮助", "总的来说", "值得一")
URL = re.compile(r"(https?://|www\.|b23\.tv)")


def clean(s: str) -> str:
    s = str(s or "").replace("\u200b", "").replace("\n", " ").replace("\r", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # B 站楼中楼原文常带“回复 @某人 :”前缀，训练目标里去掉，避免模型学会复读
    s = re.sub(r"^回复\s*@[^:：]{1,40}\s*[:：]\s*", "", s)
    return s.strip()


def good_target(s: str, lo: int = 4, hi: int = 120) -> bool:
    s = clean(s)
    if not (lo <= len(s) <= hi):
        return False
    if URL.search(s) or BAD.search(s):
        return False
    if any(w in s for w in AI_WORDS):
        return False
    if not re.search(r"[\u4e00-\u9fff]", s):
        return False
    return True


def video_ctx(v: dict, limit_desc: int = 220) -> str:
    tags = "、".join(v.get("tags") or [])
    desc = clean(v.get("desc"))[:limit_desc]
    lines = [
        f"视频标题：{clean(v.get('title'))}",
        f"UP主：{clean((v.get('owner') or {}).get('name'))}",
    ]
    if v.get("partition"):
        lines.append(f"分区：{v['partition']}")
    if tags:
        lines.append(f"标签：{tags}")
    if desc:
        lines.append(f"简介：{desc}")
    return "\n".join(lines)


def ref_comments(v: dict, exclude_rpid: int = 0, n: int = 5) -> str:
    items = [c for c in (v.get("comments") or []) if int(c.get("rpid") or 0) != exclude_rpid]
    items.sort(key=lambda c: -(int(c.get("like") or 0)))
    out = []
    for c in items[:n]:
        msg = clean(c.get("message"))
        if msg:
            out.append(f"- {clean(c.get('uname'))}（{int(c.get('like') or 0)} 赞）：{msg}")
    return "\n".join(out)


def make_a(v: dict, min_like: int = 0) -> list[dict]:
    out = []
    comments = [c for c in (v.get("comments") or [])
                if good_target(c.get("message")) and int(c.get("like") or 0) >= min_like]
    comments.sort(key=lambda c: -(int(c.get("like") or 0)))
    for c in comments[:24]:
        target = clean(c.get("message"))
        refs = ref_comments(v, int(c.get("rpid") or 0), 5)
        prompt = (
            "你在刷B站。根据下面这个视频，写一条你自己的评论（不是回复别人，是发在评论区的首评）。\n\n"
            + video_ctx(v)
            + ("\n\n评论区已有热评（只参考语气和关注点，不要照抄）：\n" + refs if refs else "")
            + "\n\n要求：只输出评论本身，不要解释、不要引号；8-35个汉字；口语、具体，像真人随手发的；"
              "不要提自己是AI。"
        )
        out.append({
            "conversations": [
                {"from": "human", "value": prompt},
                {"from": "gpt", "value": target},
            ],
            "meta": {"task": "A", "bvid": v.get("bvid"), "rpid": c.get("rpid"), "like": c.get("like")},
        })
    return out


def make_b(v: dict, min_like: int = 0) -> list[dict]:
    out = []
    for pair in (v.get("nested") or []):
        if int(pair.get("child_like") or 0) < min_like:
            continue
        child = clean(pair.get("child"))
        parent = clean(pair.get("parent_message"))
        if not good_target(child, 2, 100) or not parent:
            continue
        prompt = (
            "你在B站一条评论下面回复别人。\n\n"
            + video_ctx(v)
            + f"\n\n你正在回复的评论：@{clean(pair.get('parent_uname'))}：{parent}"
            + "\n\n要求：只输出回复内容，不要@、不要引号、不要解释；2-30个汉字；口语，直接接对方的话；"
              "不要提自己是AI。"
        )
        out.append({
            "conversations": [
                {"from": "human", "value": prompt},
                {"from": "gpt", "value": child},
            ],
            "meta": {"task": "B", "bvid": v.get("bvid"), "root": pair.get("root"), "like": pair.get("child_like")},
        })
    return out


def make_c(v: dict) -> list[dict]:
    out = []
    for pair in (v.get("nested") or []):
        child = clean(pair.get("child"))
        parent = clean(pair.get("parent_message"))
        if not good_target(child, 2, 80) or not parent:
            continue
        if int(hashlib.md5(f"{v.get('bvid')}:{parent}:{child}".encode()).hexdigest()[:8], 16) % 100 >= 18:
            continue
        prompt = (
            "你在看B站私信。对方给你发来一条消息，回一句自然的话。\n\n"
            f"对方（{clean(pair.get('parent_uname'))}）：{parent}\n\n"
            "要求：只输出一句回复，不要解释；2-30个汉字；像真人聊天，不要客服腔，不要提自己是AI。"
        )
        out.append({
            "conversations": [
                {"from": "human", "value": prompt},
                {"from": "gpt", "value": child},
            ],
            "meta": {"task": "C", "bvid": v.get("bvid")},
        })
    return out


class Deduper:
    def __init__(self, threshold: int = 4):
        self.threshold = threshold
        self.buckets: dict[int, list[int]] = defaultdict(list)

    def add(self, text: str) -> bool:
        h = simhash(text)
        key = h >> 48
        for k in (key, key ^ 0xFFFF, key ^ 0xFFFF0000):
            for old in self.buckets.get(k, ()):
                if bin(old ^ h).count("1") <= self.threshold:
                    return False
        self.buckets[key].append(h)
        return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/train/raw.jsonl")
    ap.add_argument("--out-dir", default="data/train/sft")
    ap.add_argument("--val-ratio", type=float, default=0.02)
    ap.add_argument("--min-target-like", type=int, default=20)
    ap.add_argument("--min-nested-like", type=int, default=5)
    args = ap.parse_args()
    raw = Path(args.raw)
    if not raw.is_absolute():
        raw = ROOT / raw
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    dedup = Deduper(4)
    train, val = [], []
    stats = Counter()
    for line in raw.open("r", encoding="utf-8"):
        try:
            v = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if not v.get("title"):
            continue
        samples = make_a(v, args.min_target_like) + make_b(v, args.min_nested_like) + make_c(v)
        for s in samples:
            target = clean(s["conversations"][-1]["value"])
            if not dedup.add(target):
                stats["dup"] += 1
                continue
            task = s["meta"]["task"]
            stats[f"task_{task}"] += 1
            bucket = int(hashlib.md5(str(v.get("bvid")).encode()).hexdigest()[:8], 16) % 100
            (val if bucket < int(args.val_ratio * 100) else train).append(s)

    random.Random(20261027).shuffle(train)
    with (out_dir / "train.jsonl").open("w", encoding="utf-8") as f:
        for s in train:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with (out_dir / "val.jsonl").open("w", encoding="utf-8") as f:
        for s in val:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with (out_dir / "all.jsonl").open("w", encoding="utf-8") as f:
        for s in train + val:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    summary = {
        "raw_videos": sum(1 for _ in raw.open("r", encoding="utf-8")),
        "train": len(train),
        "val": len(val),
        "by_task": {k.replace("task_", ""): v for k, v in stats.items() if k.startswith("task_")},
        "dropped_dup": stats["dup"],
    }
    (out_dir / "stats.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
