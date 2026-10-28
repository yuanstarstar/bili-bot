# -*- coding: utf-8 -*-
"""采集 B 站视频 + 高互动评论（默认排除 2026-10），产出 data/train/raw.jsonl。

用法：
  py -3 train/collect.py --videos 200 --months 2025-11..2026-09
  py -3 train/collect.py --videos 2000 --pages 3 --max-nested 40

只读接口；使用本地凭证（data/bili_cookie.json，自动从旧项目复制）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.bili import auth, comments, feed, video  # noqa: E402
from app.bili.client import BiliClient  # noqa: E402
from app.config import Config  # noqa: E402

KEYWORDS = [
    "动画 杂谈", "新番 吐槽", "游戏 实况", "游戏 攻略", "科技 数码", "手机 评测",
    "音乐 现场", "搞笑 整活", "美食 探店", "生活 vlog", "鬼畜", "影视 解说",
]
EXCLUDE_MONTH = "2026-10"
TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"(https?://|www\.|b23\.tv|BV[0-9A-Za-z]{10})")
BAD_RE = re.compile(r"(抽奖|三连|关注我|点个赞|求赞|互关|广告|加群|私信我|代练|返利|优惠券)")
EMOJI_ONLY_RE = re.compile(r"^[\W_]+$", re.UNICODE)


def clean_text(s: str) -> str:
    s = TAG_RE.sub("", str(s or ""))
    s = s.replace("\u200b", "").replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", s).strip()


def month_ranges(spec: str) -> list[tuple[int, int, str]]:
    """把 2025-11..2026-09 展开成每月的 [begin,end] 时间戳。"""
    a, b = spec.split("..")
    ay, am = map(int, a.split("-"))
    by, bm = map(int, b.split("-"))
    out = []
    y, m = ay, am
    while (y, m) <= (by, bm):
        begin = int(time.mktime((y, m, 1, 0, 0, 0, 0, 0, -1)))
        if m == 12:
            end = int(time.mktime((y + 1, 1, 1, 0, 0, 0, 0, 0, -1))) - 1
        else:
            end = int(time.mktime((y, m + 1, 1, 0, 0, 0, 0, 0, -1))) - 1
        out.append((begin, end, f"{y:04d}-{m:02d}"))
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def search_seeds(client: BiliClient, months, pages: int, per_page: int = 30,
                 order: str = "pubdate", workers: int = 4):
    """并行搜索种子。每个 worker 用独立 BiliClient，避免 WBI/cookie 竞态。"""
    seen: set[str] = set()
    tasks = [(begin, end, label, kw, page)
             for begin, end, label in months
             for kw in KEYWORDS
             for page in range(1, max(1, pages) + 1)]
    cfg_snapshot = dict(client.cfg or {})
    data_dir = client.data_dir
    cookies = dict(client.cookie_pairs)

    def one(task):
        begin, end, label, kw, page = task
        c = BiliClient(cfg_snapshot, data_dir, timeout=45)
        c.add_cookie_pairs(cookies)
        try:
            data = c.api(
                "/x/web-interface/wbi/search/type",
                {
                    "search_type": "video", "keyword": kw, "page": page,
                    "page_size": per_page, "order": order,
                    "pubtime_begin_s": begin, "pubtime_end_s": end,
                },
                wbi=True, timeout=40,
            )
        except Exception as e:  # noqa: BLE001
            print(f"[search] {label} {kw} p{page} 失败：{e}", flush=True)
            return []
        out = []
        for it in data.get("result") or []:
            bvid = str(it.get("bvid") or "")
            if not bvid:
                continue
            out.append({
                "bvid": bvid,
                "aid": int(it.get("aid") or 0),
                "title": clean_text(it.get("title")),
                "desc": clean_text(it.get("description")),
                "author": clean_text(it.get("author")),
                "pubdate": int(it.get("pubdate") or 0),
                "play": int(it.get("play") or 0),
                "review": int(it.get("video_review") or 0),
                "duration": str(it.get("duration") or ""),
                "pic": str(it.get("pic") or ""),
                "partition": clean_text(it.get("typename")),
                "tag": clean_text(it.get("tag")),
                "_month": label,
            })
        print(f"[search] {label} {kw} p{page} -> {len(out)}", flush=True)
        time.sleep(0.4)
        return out

    if workers <= 1:
        for items in map(one, tasks):
            for seed in items:
                if seed["bvid"] not in seen:
                    seen.add(seed["bvid"])
                    yield seed
        return
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for items in ex.map(one, tasks):
            for seed in items:
                if seed["bvid"] not in seen:
                    seen.add(seed["bvid"])
                    yield seed


def keep_comment(c: dict, exclude_month: str, min_like: int) -> bool:
    msg = clean_text(c.get("message"))
    if not (4 <= len(msg) <= 120):
        return False
    if int(c.get("like") or 0) < min_like:
        return False
    if int(c.get("is_up") or 0):
        return False
    if URL_RE.search(msg) or BAD_RE.search(msg):
        return False
    if EMOJI_ONLY_RE.match(msg):
        return False
    if not re.search(r"[\u4e00-\u9fff]", msg):
        return False
    ct = int(c.get("ctime") or 0)
    if ct and time.strftime("%Y-%m", time.localtime(ct)) == exclude_month:
        return False
    return True


def load_done(path: Path) -> set[str]:
    done: set[str] = set()
    if not path.is_file():
        return done
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
                if obj.get("bvid"):
                    done.add(obj["bvid"])
            except Exception:  # noqa: BLE001
                pass
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", type=int, default=200)
    ap.add_argument("--months", default="2025-11..2026-09")
    ap.add_argument("--pages", type=int, default=2)
    ap.add_argument("--comment-pages", type=int, default=2)
    ap.add_argument("--min-like", type=int, default=10)
    ap.add_argument("--min-video-reply", type=int, default=300)
    ap.add_argument("--min-video-play", type=int, default=200000)
    ap.add_argument("--max-nested", type=int, default=30)
    ap.add_argument("--out", default="data/train/raw.jsonl")
    ap.add_argument("--exclude-month", default=EXCLUDE_MONTH)
    ap.add_argument("--order", default="pubdate",
                    choices=["pubdate", "click", "scores", "stow"])
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    cfg = Config()
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    done = load_done(out)
    print(f"已有 {len(done)} 个视频，目标 {args.videos}", flush=True)

    client = BiliClient(cfg.snapshot(), cfg.data_dir, timeout=30)
    info = auth.load_cookie(client, cfg.snapshot())
    print("登录：", info, flush=True)
    # 搜索接口对当前登录态会返回空（账号级风控），用游客态搜索，评论仍用登录态读。
    search_client = BiliClient(cfg.snapshot(), cfg.data_dir, timeout=30)
    months = month_ranges(args.months)
    print("月份：", [m[2] for m in months], flush=True)

    got = 0
    for seed in search_seeds(search_client, months, args.pages,
                             order=args.order, workers=args.workers):
        if got >= args.videos:
            break
        bvid = seed["bvid"]
        if bvid in done:
            continue
        try:
            meta = video.view(client, bvid)
        except Exception as e:  # noqa: BLE001
            print(f"[view] {bvid} 失败：{e}", flush=True)
            time.sleep(0.6)
            continue
        stat = meta.get("stat") or {}
        if int(stat.get("reply") or 0) < args.min_video_reply:
            continue
        if int(stat.get("view") or 0) < args.min_video_play:
            continue
        aid = int(meta.get("aid") or seed["aid"])
        if aid <= 0:
            continue
        try:
            items = comments.collect_video_comments(
                client, aid, pages=args.comment_pages, mode=3, pause_s=1.2
            )
        except Exception as e:  # noqa: BLE001
            print(f"[comments] {bvid} 失败：{e}", flush=True)
            time.sleep(0.8)
            continue
        kept = [c for c in items if keep_comment(c, args.exclude_month, args.min_like)]
        kept.sort(key=lambda c: (-(int(c.get("like") or 0)), -(int(c.get("ctime") or 0))))
        # 楼中楼
        nested = []
        for c in kept[:12]:
            if len(nested) >= args.max_nested:
                break
            if int(c.get("rcount") or 0) <= 0:
                continue
            try:
                sub = comments.sub_replies(client, aid, int(c["rpid"]), pn=1, ps=20)
            except Exception:  # noqa: BLE001
                continue
            for r in sub.get("items") or []:
                msg = clean_text(r.get("message"))
                if not (2 <= len(msg) <= 100):
                    continue
                if URL_RE.search(msg) or BAD_RE.search(msg):
                    continue
                ct = int(r.get("ctime") or 0)
                if ct and time.strftime("%Y-%m", time.localtime(ct)) == args.exclude_month:
                    continue
                if not re.search(r"[\u4e00-\u9fff]", msg):
                    continue
                nested.append({
                    "root": int(c["rpid"]),
                    "parent": int(r.get("parent") or c["rpid"]),
                    "parent_message": clean_text(c.get("message")),
                    "parent_uname": clean_text(c.get("uname")),
                    "child": msg,
                    "child_uname": clean_text(r.get("uname")),
                    "child_like": int(r.get("like") or 0),
                    "ctime": ct,
                })
                if len(nested) >= args.max_nested:
                    break
            time.sleep(0.4)

        rec = {
            "bvid": bvid,
            "aid": aid,
            "cid": int(meta.get("cid") or 0),
            "title": clean_text(meta.get("title")),
            "desc": clean_text(meta.get("desc"))[:500],
            "pubdate": int(meta.get("pubdate") or seed["pubdate"]),
            "owner": meta.get("owner") or {"mid": 0, "name": seed["author"]},
            "partition": seed.get("partition") or meta.get("tname") or "",
            "tags": [t.strip() for t in str(seed.get("tag") or "").split(",") if t.strip()][:12],
            "stat": meta.get("stat") or {},
            "comments": [
                {k: c.get(k) for k in ("rpid", "root", "parent", "mid", "uname", "message",
                                       "like", "rcount", "ctime", "level", "is_up")}
                for c in kept[:50]
            ],
            "nested": nested,
            "collected_at": int(time.time()),
            "_month": seed.get("_month"),
        }
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        done.add(bvid)
        got += 1
        print(
            f"[{got}/{args.videos}] {bvid} 评论={len(rec['comments'])} 楼中楼={len(rec['nested'])} "
            f"{rec['title'][:30]}", flush=True,
        )
        time.sleep(0.8)
    print(f"完成：新增 {got}，文件 {out}，总 {len(done)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
