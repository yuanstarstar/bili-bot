# -*- coding: utf-8 -*-
"""引擎：自主刷视频 → 理解 → 生成 → 自评 → 发布；后台回复/私信/巡检。"""
from __future__ import annotations

import json
import random
import threading
import time
import traceback
from typing import Any, Optional

from app.bili import auth
from app.bili import comments as comments_mod
from app.bili import danmaku as danmaku_mod
from app.bili import dm as dm_mod
from app.bili import feed as feed_mod
from app.bili import login as login_mod
from app.bili import subtitle as subtitle_mod
from app.bili import video as video_mod
from app.bili.client import BiliClient
from app.bili.errors import BiliError
from app.brain import judge, persona as persona_mod, style
from app.brain.generate import Writer
from app.brain.llm import LLM
from app.robot.actions import Actions
from app.robot.circuit import Circuit
from app.robot.rate import RateLimiter
from app.voice.asr import ASR


class Engine:
    def __init__(self, cfg, store, logger) -> None:
        self.cfg = cfg
        self.store = store
        self.log = logger
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._cycle_thread: Optional[threading.Thread] = None
        self._tracker_thread: Optional[threading.Thread] = None
        self.state = "idle"
        self.current = ""
        self.last_cycle = 0.0
        self.last_error = ""
        self.last_skip = ""
        self.last_skip_ts = 0.0
        self._status_lock = threading.RLock()
        self._login_ts = 0.0
        self._login_data: dict = {"isLogin": False, "uname": "", "mid": 0}
        self._login_refreshing = False
        self._llm_ts = 0.0
        self._llm_data: tuple[bool, str] = (False, "检测中")
        self._llm_refreshing = False
        self._tick = 0

        bili_cfg = dict(cfg.get("bili") or {})
        bili_cfg["timeout_s"] = float(cfg.get("bili.timeout_s", 15) or 15)
        self._bili_cfg = bili_cfg
        self.client = self._new_client()
        self.tracker_client = self._new_client()
        self.web_client = self._new_client()
        self.persona = persona_mod.load(cfg.persona_path)
        self.llm = LLM(cfg)
        self.writer = Writer(LLM(cfg), self.persona)
        self.writer.store = store
        self.limiter = RateLimiter(cfg, store)
        self.circuit = Circuit(cfg, store, logger)
        self.actions = Actions(cfg, store, logger, self.client, self.circuit)
        self.tracker_actions = Actions(cfg, store, logger, self.tracker_client, self.circuit)
        self.asr = ASR(cfg)
        if self.client.has_login:
            self._login_data = {"isLogin": True, "uname": "登录中…", "mid": self.self_uid, "vip": 0}
        self.log.info("引擎初始化：登录=%s 模型=%s ASR=%s",
                      self.client.has_login, self.llm.base_url,
                      self.asr.check()[0])

    # ------------------------------------------------------------------ 客户端
    def _new_client(self) -> BiliClient:
        c = BiliClient(dict(self._bili_cfg), self.cfg.data_dir,
                       timeout=float(self._bili_cfg.get("timeout_s") or 15))
        try:
            info = auth.load_cookie(c, self._bili_cfg)
            if info.get("imported"):
                self.log.info("检测到其它项目的登录凭证，已复制到本实例：%s（原文件只读，不再共用）",
                              info["imported"])
        except Exception as e:  # noqa: BLE001
            self.log.warning("读取 cookie 失败：%s", e)
        return c

    @property
    def self_uid(self) -> int:
        return self.actions.self_uid

    def apply_login(self, cookies: dict) -> dict:
        """扫码成功后把 cookie 灌进所有客户端并落盘。"""
        for c in (self.client, self.tracker_client, self.web_client):
            c.add_cookie_pairs({k: v for k, v in (cookies or {}).items() if v})
            try:
                c.ensure_buvid()
            except Exception:
                pass
        try:
            saved = login_mod.save_cookie(self.client, self.cfg.cookie_path)
        except Exception as e:  # noqa: BLE001
            saved = {"error": str(e)}
        self._login_ts = 0.0
        self.log.info("登录 cookie 已更新：%s", saved)
        return {"user": self.client.nav(), "cookie": saved}

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> tuple[bool, str]:
        if self._cycle_thread and self._cycle_thread.is_alive():
            return False, "引擎已在运行"
        ok, msg = self.check_ready()
        if not ok:
            self.last_error = msg
            return False, msg
        self._stop.clear()
        self._pause.clear()
        self.state = "running"
        self.last_error = ""
        self._cycle_thread = threading.Thread(target=self._loop, name="bot-cycle", daemon=True)
        self._tracker_thread = threading.Thread(target=self._track_loop, name="bot-tracker", daemon=True)
        self._cycle_thread.start()
        self._tracker_thread.start()
        self.log.info("引擎启动")
        return True, "已启动"

    def pause(self) -> tuple[bool, str]:
        if not (self._cycle_thread and self._cycle_thread.is_alive()):
            return False, "引擎没在运行"
        self._pause.set()
        self.state = "paused"
        self.log.info("引擎暂停")
        return True, "已暂停"

    def resume(self) -> tuple[bool, str]:
        if not (self._cycle_thread and self._cycle_thread.is_alive()):
            return False, "引擎没在运行"
        self._pause.clear()
        self.state = "running"
        self.log.info("引擎继续")
        return True, "已继续"

    def stop(self) -> tuple[bool, str]:
        self._stop.set()
        self._pause.clear()
        for t in (self._cycle_thread, self._tracker_thread):
            if t and t.is_alive():
                t.join(timeout=6)
        self._cycle_thread = None
        self._tracker_thread = None
        self.state = "idle"
        self.current = ""
        self.log.info("引擎停止")
        return True, "已停止"

    def check_ready(self) -> tuple[bool, str]:
        if not self.client.has_login:
            return False, "未登录：请先在「登录」页扫码"
        # 不在这里同步请求 B 站 nav：启动按钮必须立刻有反馈。
        # cookie 的在线/失效交给后台巡检与主循环处理。
        st = self._cached_login()
        if st.get("isLogin") is False:
            return False, "登录状态未确认：请刷新登录页或重新扫码"
        ok, msg = self._cached_llm(force=True)
        if not ok:
            return False, msg
        return True, "ok"

    def run_once(self, dry_run: bool = True, force: bool = False,
                 bvid: str = "", deep: bool = False) -> dict:
        if self._cycle_thread and self._cycle_thread.is_alive():
            return {"ok": False, "error": "引擎正在后台运行，先停止再单跑"}
        ok, msg = self.check_ready()
        if not ok:
            return {"ok": False, "error": msg}
        forced = None
        bvid = str(bvid or "").strip()
        if bvid:
            # 指定 BV 测试：只解析+生成草稿，绝不发布。
            try:
                forced = video_mod.view(self.client, bvid)
                forced["bvid"] = bvid
                forced["_source"] = "test"
            except BiliError as e:
                return {"ok": False, "error": f"读取视频失败：{e}", "bvid": bvid}
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"读取视频失败：{e}", "bvid": bvid}
            dry_run, force = True, True
        try:
            return self._cycle(dry_run=dry_run, force=force, forced_video=forced,
                               deep=bool(deep or bvid))
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)
            self.log.error("单跑失败：%s\n%s", e, traceback.format_exc())
            return {"ok": False, "error": str(e)}

    def reload_persona(self) -> dict:
        self.persona = persona_mod.load(self.cfg.persona_path)
        self.writer.persona = self.persona
        self.writer._recent_cache = None
        return self.persona

    # ------------------------------------------------------------------ 主循环
    def _loop(self) -> None:
        while not self._stop.is_set():
            if self._pause.is_set():
                self._stop.wait(1.0)
                continue
            try:
                opened, why = self.circuit.is_open()
                if opened:
                    self.current = "熔断中"
                    self._stop.wait(30)
                    continue
                active, why = self.limiter.active_now()
                if not active:
                    self.current = why
                    self._stop.wait(30)
                    continue
                if not self.client.has_login:
                    self.state = "idle"
                    self.last_error = "登录失效，请重新扫码"
                    self._stop.wait(30)
                    continue
                res = self._cycle(dry_run=False)
                self.last_cycle = time.time()
                if res.get("posted"):
                    wait = self.limiter.interval_seconds()
                else:
                    wait = self.limiter.cycle_seconds()
                self.current = f"休息 {int(wait / 60) or 1} 分钟"
                self._stop.wait(max(10.0, wait))
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)
                self.log.error("主循环异常：%s\n%s", e, traceback.format_exc())
                self._stop.wait(60)

    def _track_loop(self) -> None:
        while not self._stop.is_set():
            if self._pause.is_set():
                self._stop.wait(1.0)
                continue
            try:
                if self.tracker_client.has_login:
                    self._track_replies()
                    self._track_dms()
                    self._tick += 1
                    if self._tick % 3 == 0:
                        self._track_visibility()
            except Exception as e:  # noqa: BLE001
                self.log.error("巡检异常：%s\n%s", e, traceback.format_exc())
            self._stop.wait(90)

    # ------------------------------------------------------------------ 单轮
    def _mark_skip(self, reason: str) -> None:
        self.last_skip = str(reason)
        self.last_skip_ts = time.time()
        self.current = self.last_skip
        self.log.info("本轮跳过：%s", self.last_skip)

    def _cycle(self, dry_run: bool = False, force: bool = False, attempt: int = 0,
               forced_video: dict | None = None, deep: bool = False) -> dict:
        if not self.client.has_login:
            return {"ok": False, "error": "未登录"}
        is_test = forced_video is not None
        self.state = "dry-run" if dry_run else "running"
        if forced_video is not None:
            video = dict(forced_video)
            score, why = 10.0, "指定 BV 测试"
            self.current = f"测试：{str(video.get('title') or '')[:24]}"
        else:
            self.current = "刷推荐/热门"
            candidates = self._gather_candidates()
            if not candidates:
                self.current = "没刷到可看的视频"
                return {"ok": False, "error": "没有候选视频（网络/风控/已看全）"}

            # 先看今天还能不能写评论，避免“选了半天视频、看完才说配额不足”。
            if not dry_run:
                ok, reason = self.limiter.can_write("comment", ignore_hours=bool(force), ignore_cap=bool(force))
                if not ok:
                    self._mark_skip(reason)
                    return {"ok": True, "skipped": reason}

            video, score, why = self._choose(candidates)
        bvid = str(video.get("bvid") or "")
        aid = int(video.get("aid") or 0)
        if not bvid or not aid:
            return {"ok": False, "error": "候选视频缺 bvid/aid"}
        if not is_test:
            self.store.mark_seen(bvid, "chosen")
        self.current = f"看：{str(video.get('title') or '')[:24]}"
        self.log.info("选中 %s（%.1f 分，%s）：%s", bvid, score, why, video.get("title"))

        ctx = self._understand(video, fast=(bool(dry_run or force) and not deep))
        if not ctx.get("text"):
            self.current = "视频信息抓取失败"
            return {"ok": False, "error": ctx.get("error") or "视频信息抓取失败", "video": video}

        if not dry_run and not force and bool(self.cfg.get("bot.simulate_dwell", True)):
            dwell = self.limiter.dwell_seconds(int(ctx["video"].get("duration") or 0))
            self.current = f"看视频 {int(dwell)} 秒"
            if self._sleep_interruptible(dwell):
                return {"ok": False, "error": "已停止"}
            read = self.limiter.read_seconds()
            self.current = f"读评论 {int(read)} 秒"
            if self._sleep_interruptible(read):
                return {"ok": False, "error": "已停止"}

        if not dry_run:
            ok, reason = self.limiter.can_write("comment", ignore_hours=bool(force), ignore_cap=bool(force))
            if not ok:
                self._mark_skip(reason)
                return {"ok": True, "skipped": reason, "video": ctx["video"]}
            prob = float(self.cfg.get("bot.comment_probability", 0.55) or 0.55)
            if not force and random.random() > prob:
                reason = f"本轮只看不评（评论概率 {prob:.0%}）"
                self._mark_skip(reason)
                if bool(self.cfg.get("bot.like_video", False)):
                    self.actions.like(bvid)
                return {"ok": True, "skipped": reason, "video": ctx["video"]}

        examples = style.examples(self.store, ctx.get("tags") or [])
        res = self.writer.comment(ctx["text"], examples)
        if not res:
            self.current = "生成失败"
            if attempt < 2 and (dry_run or force):
                self.log.warning("模型没生成出合规评论，换下一个候选重试（%d/2）", attempt + 1)
                return self._cycle(dry_run, force, attempt + 1, forced_video=forced_video, deep=deep)
            return {"ok": False, "error": "模型没生成出合规评论", "video": ctx["video"]}

        text = res["text"]
        at_uid, at_name = self._at_master(ctx)
        if at_uid:
            # 评论本身已通过 6-30 字校验；@ 是额外后缀，不能再按 30 字上限
            # 把整条判失败，否则长评论会静默丢失 @。
            want = f"{text} @{at_name}"
            if len(want) <= 80 and "\n" not in want:
                text = want
            else:
                self.log.info("@主人未追加：评论+@超过 80 字")
                at_uid, at_name = 0, ""

        score_detail = {"choose": score, "why": why, "critique": res.get("critique"),
                        "at_uid": int(at_uid or 0),
                        "at_name": ("@" + at_name) if at_uid else ""}
        review = str(self.cfg.get("bot.approval_mode", "auto")).lower() in ("review", "manual", "approve")
        self.last_skip = ""
        if dry_run or review:
            self.log.info("%s：准备写草稿 %s", "审核模式" if review else "dry-run", bvid)
            draft_id = self.store.add_draft(
                "comment", text, bvid=bvid, aid=aid, score=score_detail, alt=res.get("candidates"),
                status="pending",
            )
            self.store.log_action("draft_review" if review else "dry_run", f"{bvid}:{aid}", text)
            self.current = "待审核" if review else "dry-run 完成"
            self.log.info("%s：草稿 %s 已写", self.current, draft_id)
            out = {"ok": True, "dry_run": dry_run, "review": review, "draft_id": draft_id,
                    "video": ctx["video"], "text": text, "hooks": res.get("hooks"),
                    "candidates": res.get("candidates")}
            if is_test:
                out["test"] = True
                out["context"] = str(ctx.get("text") or "")[:3000]
                out["tags"] = ctx.get("tags") or []
                out["subtitle_chars"] = len(str(ctx.get("subtitle") or ""))
                out["danmaku_count"] = len(ctx.get("danmaku") or [])
            return out

        action = self.actions.post_comment(
            {"bvid": bvid, "aid": aid}, text,
            at_uid=at_uid, at_name=("@" + at_name) if at_uid else "",
        )
        if not action.get("ok"):
            self.current = "发布失败"
            return {"ok": False, "error": action.get("error"), "video": ctx["video"], "text": text}

        self.writer._recent_cache = None
        if bool(self.cfg.get("bot.like_video", False)):
            self.actions.like(bvid)
        self._maybe_proactive_dm(ctx["video"])
        self.current = "发布成功"
        return {"ok": True, "posted": True, "video": ctx["video"], "text": text,
                "rpid": action.get("rpid"), "hooks": res.get("hooks")}

    # ------------------------------------------------------------------ 选视频
    def _gather_candidates(self) -> list[dict]:
        out: list[dict] = []
        errors: list[str] = []
        source_count: dict[str, int] = {}

        def pull(name: str, fn) -> None:
            try:
                items = fn() or []
                source_count[name] = source_count.get(name, 0) + len(items)
                for v in items:
                    if isinstance(v, dict):
                        v["_source"] = name
                        out.append(v)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e}")
                if isinstance(e, BiliError):
                    self.circuit.handle(e)

        # 推荐流是唯一主源：B 站首页推荐，带当前账号的观看画像。
        # 热门/排行榜只在推荐流拿不到足够候选时兜底，避免“个性化”退化成热门。
        if bool(self.cfg.get("bot.use_recommend", True)):
            for idx in (1, 2):
                pull(f"recommend{idx}", lambda i=idx: feed_mod.recommend(
                    self.client, ps=20, fresh_idx=i, fresh_idx_1h=1))
        if source_count.get("recommend1", 0) + source_count.get("recommend2", 0) < 8:
            pull("popular", lambda: feed_mod.popular(self.client, ps=20))
        if bool(self.cfg.get("bot.use_search", False)):
            interests = self.cfg.get("bot.search_interests") or (self.persona.get("interests") or [])[:2]
            for kw in list(interests)[:2]:
                if str(kw).strip():
                    pull(f"search:{kw}", lambda k=str(kw): feed_mod.search(self.client, k, page_size=8))

        seen = self.store.seen_bvids()
        uniq: dict[str, dict] = {}
        for v in out:
            bvid = str(v.get("bvid") or "")
            aid = int(v.get("aid") or 0)
            if not bvid or bvid in seen or aid <= 0:
                continue
            if not uniq.get(bvid):
                uniq[bvid] = v
        if errors:
            self.log.info("候选源告警：%s", "；".join(errors[:4]))
        # 推荐流优先保持 B 站给的顺序；只有推荐不足时才看兜底源。
        ordered = [v for v in uniq.values() if str(v.get("_source", "")).startswith("recommend")]
        if len(ordered) < 8:
            ordered += [v for v in uniq.values() if not str(v.get("_source", "")).startswith("recommend")]
        return ordered[:30]

    def preview_candidates(self, limit: int = 10) -> list[dict]:
        """给控制台预览用：优先 B 站个性化推荐，失败再退热门/排行。"""
        items, source = [], "recommend"
        if bool(self.cfg.get("bot.use_recommend", True)):
            try:
                for idx in (1, 2):
                    batch = feed_mod.recommend(self.client, ps=20, fresh_idx=idx, fresh_idx_1h=1) or []
                    seen = {x.get("bvid") for x in items}
                    items += [x for x in batch if x.get("bvid") not in seen]
                source = "recommend"
            except Exception as e:  # noqa: BLE001
                self.log.info("推荐预览失败：%s", e)
        if not items:
            for name, fn in (("popular", lambda: feed_mod.popular(self.client, ps=20)),
                             ("ranking", lambda: feed_mod.ranking(self.client))):
                try:
                    items = fn() or []
                    source = name
                    break
                except Exception as e:  # noqa: BLE001
                    self.log.info("%s预览失败：%s", name, e)
        out = []
        for v in items[:max(1, int(limit))]:
            if isinstance(v, dict) and v.get("bvid"):
                v["_source"] = source
                out.append(v)
        return out

    def _choose(self, candidates: list[dict]) -> tuple[dict, float, str]:
        pool = list(candidates)
        rec = [v for v in pool if str(v.get("_source", "")).startswith("recommend")]
        if rec:
            subset = rec[:8]
        else:
            random.shuffle(pool)
            subset = pool[:6]
        llm_ok = self._cached_llm()[0]
        scored: list[tuple[float, dict, str]] = []
        for v in subset:
            if llm_ok:
                s, why = self.writer.score_video(v)
            else:
                s, why = self._heuristic_score(v), "heuristic"
            scored.append((s, v, why))
        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0]
        if len(scored) > 1 and random.random() < 0.25:
            best = random.choice(scored[:3])
        self.store.log_action("choose", str(best[1].get("bvid") or ""),
                              {"score": round(best[0], 2), "why": best[2]})
        return best[1], float(best[0]), str(best[2])

    def _heuristic_score(self, video: dict) -> float:
        text = f"{video.get('title') or ''} {video.get('tname') or ''}".lower()
        score = 3.0 + random.random()
        for kw in self.persona.get("interests") or []:
            if str(kw).lower() and str(kw).lower() in text:
                score += 2.0
        dur = int(video.get("duration") or 0)
        if 60 <= dur <= 1800:
            score += 1.0
        views = int((video.get("stat") or {}).get("view") or 0)
        if views >= 100_000:
            score += 0.5
        return min(10.0, score)

    # ------------------------------------------------------------------ 理解
    def _understand(self, video: dict, fast: bool = False) -> dict:
        bvid = str(video.get("bvid") or "")
        aid = int(video.get("aid") or 0)
        t0 = time.time()
        try:
            full = video_mod.view(self.client, bvid)
        except BiliError as e:
            self.circuit.handle(e)
            return {"text": "", "error": str(e)}
        self.log.info("理解 %s：view %.1fs", bvid, time.time() - t0)
        meta: dict[str, Any] = {**video, **full, "bvid": bvid, "aid": int(full.get("aid") or aid)}
        cid = int(full.get("cid") or video.get("cid") or 0)
        aid = int(meta.get("aid") or aid)
        meta["cid"] = cid
        meta["aid"] = aid
        tags: list[str] = []
        if not fast and bool(self.cfg.get("bot.use_tags", True)):
            try:
                tags = video_mod.tags(self.client, bvid)
            except BiliError as e:
                self.log.info("标签抓取失败 %s：%s", bvid, e)
        meta["tags"] = tags
        self.log.info("理解 %s：tags %.1fs", bvid, time.time() - t0)
        sub_text = ""
        if not fast and bool(self.cfg.get("bot.use_subtitle", True)):
            try:
                subs = subtitle_mod.get_subtitles(self.client, aid, cid, bvid)
                sub_text = subtitle_mod.best_text(subs, max_chars=2200)
            except Exception as e:  # noqa: BLE001
                self.log.info("字幕抓取失败 %s：%s", bvid, e)
        self.log.info("理解 %s：subtitle %.1fs（%d 字）", bvid, time.time() - t0, len(sub_text))
        voice_text = ""
        if (not sub_text and bool(self.cfg.get("voice.enabled", True))
                and bool(self.cfg.get("voice.asr.enabled", True))
                and bool(self.cfg.get("voice.asr.for_video", True))
                and (not fast or bool(self.cfg.get("voice.asr.in_dry_run", False)))):
            try:
                tr = self.asr.transcribe_video(
                    self.client, meta,
                    max_seconds=int(self.cfg.get("voice.asr.max_seconds", 120) or 120),
                )
                voice_text = str(tr.get("text") or "").strip()
                self.log.info("理解 %s：语音转录 %.1fs（%d 字）", bvid,
                              time.time() - t0, len(voice_text))
            except Exception as e:  # noqa: BLE001
                self.log.info("语音转录失败 %s：%s", bvid, e)
        dmk: list[str] = []
        if not fast and bool(self.cfg.get("bot.use_danmaku", True)):
            try:
                dmk = danmaku_mod.top(self.client, cid, limit=30)
            except Exception as e:  # noqa: BLE001
                self.log.info("弹幕抓取失败 %s：%s", bvid, e)
        self.log.info("理解 %s：danmaku %.1fs（%d 条）", bvid, time.time() - t0, len(dmk))
        items: list[dict] = []
        try:
            page = comments_mod.video_replies(self.client, aid, ps=20)
            items = page.get("items") or []
        except BiliError as e:
            self.circuit.handle(e)
            self.log.info("评论抓取失败 %s：%s", bvid, e)
        self.log.info("理解 %s：comments %.1fs（%d 条）", bvid, time.time() - t0, len(items))
        if items:
            self.store.save_comments(bvid, aid, items)
        self.store.upsert_video(meta)

        lines = [
            f"标题：{meta.get('title') or ''}",
            f"UP主：{(meta.get('owner') or {}).get('name') or ''} ｜ 分区：{meta.get('tname') or ''}"
            f" ｜ 时长：{int(meta.get('duration') or 0)} 秒 ｜ 播放：{(meta.get('stat') or {}).get('view', 0)}",
            f"简介：{str(meta.get('desc') or '')[:300]}",
            f"标签：{'、'.join(tags[:20])}",
        ]
        if sub_text:
            lines.append(f"字幕节选：{sub_text}")
        if voice_text:
            lines.append(f"视频语音转录：{voice_text[:1200]}")
        if dmk:
            lines.append(f"热弹幕：{' / '.join(dmk[:25])}")
        top = sorted([x for x in items if int(x.get("mid") or 0) != self.self_uid],
                     key=lambda x: int(x.get("like") or 0), reverse=True)[:8]
        if top:
            lines.append("高赞评论：")
            for x in top:
                msg = str(x.get("message") or "").replace("\n", " ")[:90]
                lines.append(f"- 赞{x.get('like', 0)}：{msg}")
        text = "\n".join(lines).strip()
        # 把完整视频上下文缓存进 videos.meta，之后楼中楼/私信回复时还能“看到原视频”。
        meta["last_ctx"] = text[:6000]
        try:
            self.store.upsert_video(meta)
        except Exception as e:  # noqa: BLE001
            self.log.info("视频上下文缓存失败 %s：%s", bvid, e)
        return {"text": text[:3600], "video": meta, "tags": tags,
                "subtitle": sub_text, "voice": voice_text, "danmaku": dmk, "items": items}

    def _ctx_for_video(self, bvid: str, aid: int) -> str:
        row = self.store.query_one("SELECT * FROM videos WHERE bvid=?", (bvid,))
        if row:
            try:
                meta = json.loads(row.get("meta") or "{}")
            except (TypeError, ValueError):
                meta = {}
            # 优先用理解阶段缓存的完整上下文（标题/简介/字幕/弹幕/热评）。
            cached = str(meta.get("last_ctx") or "").strip()
            if cached:
                return cached[:2400]
            try:
                tags = json.loads(row.get("tags") or "[]")
            except (TypeError, ValueError):
                tags = []
            tname = str(meta.get("tname") or "")
            lines = [
                f"标题：{row.get('title') or ''}",
                f"UP主：{row.get('owner_name') or ''} ｜ 分区：{tname}"
                f" ｜ 时长：{int(row.get('duration') or 0)} 秒",
                f"简介：{str(row.get('desc') or '')[:400]}",
                f"标签：{'、'.join(str(x) for x in tags[:15])}",
            ]
            try:
                hot = self.store.query(
                    "SELECT uname,message,like_count FROM comments WHERE bvid=? "
                    "ORDER BY like_count DESC LIMIT 8", (bvid,))
            except Exception:  # noqa: BLE001
                hot = []
            if hot:
                lines.append("高赞评论：")
                for x in hot:
                    msg = str(x.get("message") or "").replace("\n", " ")[:80]
                    lines.append(f"- 赞{x.get('like_count', 0)} {x.get('uname') or ''}：{msg}")
            return "\n".join(lines)[:2400]
        try:
            meta = video_mod.view(self.tracker_client, bvid)
            return (f"标题：{meta.get('title') or ''}\n"
                    f"UP主：{(meta.get('owner') or {}).get('name') or ''}\n"
                    f"简介：{str(meta.get('desc') or '')[:400]}")
        except Exception:  # noqa: BLE001
            return bvid

    # ------------------------------------------------------------------ @主人
    def _at_master(self, ctx: dict) -> tuple[int, str]:
        at = self.cfg.get("bot.at_master") or {}
        mode = str(at.get("mode") or "taste")
        if mode in ("off", "none", "false"):
            return 0, ""
        try:
            uid = int(at.get("uid") or 0)
        except (TypeError, ValueError):
            uid = 0
        name = str(at.get("name") or "").strip()
        if not uid or not name or not self.limiter.at_master_allowed():
            return 0, ""
        if mode == "always":
            return uid, name
        taste = at.get("taste") or self.persona.get("master_taste") or []
        video = ctx.get("video") or {}
        hay = " ".join([
            str(video.get("title") or ""), str(video.get("desc") or ""),
            str(video.get("tname") or ""), " ".join(str(x) for x in (ctx.get("tags") or [])),
        ]).lower()
        if any(str(t).lower() and str(t).lower() in hay for t in taste):
            return uid, name
        return 0, ""

    # ------------------------------------------------------------------ 私信
    def _maybe_proactive_dm(self, video: dict) -> None:
        cfg = self.cfg
        if str(cfg.get("bot.dm.proactive") or "") != "whitelist":
            return
        if random.random() > 0.25:
            return
        cap = int(cfg.get("bot.dm_per_day", 5) or 5)
        if self.store.count_actions("dm", self.limiter.today_start()) >= cap:
            return
        daily = int(cfg.get("bot.dm.proactive_daily", 1) or 1)
        if self.store.count_actions("dm_proactive", self.limiter.today_start()) >= daily:
            return
        wl = cfg.get("bot.dm.whitelist") or []
        uid = 0
        for x in wl:
            if isinstance(x, int):
                uid = x
                break
            if isinstance(x, dict) and x.get("uid"):
                try:
                    uid = int(x["uid"])
                    break
                except (TypeError, ValueError):
                    pass
            if isinstance(x, str) and x.strip().isdigit():
                uid = int(x.strip())
                break
        if not uid or uid == self.self_uid:
            return
        title = str(video.get("title") or "").strip()
        url = str(video.get("url") or "") or f"https://www.bilibili.com/video/{video.get('bvid')}"
        if not title:
            return
        text = f"刷到这个，感觉你会喜欢：{title} {url}"
        res = self.actions.send_dm(uid, text[:500])
        if res.get("ok"):
            self.store.log_action("dm_proactive", str(uid), text, ok=True)

    # ------------------------------------------------------------------ 巡检
    def _thread_context(self, page_items: list[dict], group: list[dict],
                        target: dict, current_rpid: int, current_ctime: float = 0) -> str:
        """把这层楼里、当前回复之前的对话按时间拼出来（含我们的根评论）。"""
        rows: dict[int, dict] = {}
        for o in group:
            rp = int(o.get("rpid") or 0)
            if rp:
                rows[rp] = {
                    "rpid": rp, "mid": self.self_uid, "uname": "我",
                    "message": str(o.get("message") or ""),
                    "ctime": float(o.get("posted_at") or 0),
                }
        for x in page_items:
            rp = int(x.get("rpid") or 0)
            if rp:
                rows[rp] = {
                    "rpid": rp,
                    "mid": int(x.get("mid") or 0),
                    "uname": str(x.get("uname") or "对方"),
                    "message": str(x.get("message") or ""),
                    "ctime": float(x.get("ctime") or 0),
                }
        target_rpid = int(target.get("rpid") or 0)
        target_ctime = float(target.get("posted_at") or 0)
        cutoff = float(current_ctime or target_ctime)
        cur = rows.get(target_rpid)
        if cur and target_ctime:
            cur["ctime"] = target_ctime
        ordered = sorted(rows.values(), key=lambda x: (x["ctime"], x["rpid"]))
        lines = []
        for x in ordered:
            if x["rpid"] == current_rpid:
                continue
            if cutoff and x["ctime"] > cutoff:
                continue
            msg = x["message"].replace("\n", " ").strip()[:120]
            if not msg:
                continue
            who = "我" if int(x["mid"] or 0) == self.self_uid else (x["uname"] or "对方")
            lines.append(f"{who}：{msg}")
        return "\n".join(lines[-10:])

    def _track_replies(self) -> None:
        ours = self.store.recent_our_comments(hours=72)
        groups: dict[tuple[str, int, int], list[dict]] = {}
        for o in ours:
            if str(o.get("status") or "") in ("deleted", "unverified"):
                continue
            root = int(o.get("root") or o.get("rpid") or 0)
            if not root:
                continue
            key = (str(o.get("bvid") or ""), int(o.get("aid") or 0), root)
            groups.setdefault(key, []).append(o)
        max_nested = int(self.cfg.get("bot.max_nested_per_thread", 3) or 3)
        for (bvid, aid, root), group in groups.items():
            if self._stop.is_set():
                return
            try:
                page = comments_mod.sub_replies(self.tracker_client, aid, root, ps=20)
            except BiliError as e:
                code = int(getattr(e, "code", 0) or 0)
                if code in (12006, 12002):
                    # 目录里保留的测试评论可能已被删除，标记后不再每轮重试。
                    for o in group:
                        self.store.update_our_comment(int(o.get("rpid") or 0), status="gone")
                    continue
                self.circuit.handle(e)
                continue
            by_rpid = {int(x.get("rpid") or 0): x for x in group}
            for it in page.get("items") or []:
                rpid = int(it.get("rpid") or 0)
                if not rpid or int(it.get("mid") or 0) == self.self_uid:
                    continue
                if self.store.handled_reply(rpid):
                    continue
                parent = int(it.get("parent") or 0)
                target = by_rpid.get(parent)
                if not target and parent == 0 and root in by_rpid:
                    target = by_rpid[root]
                if not target:
                    continue
                if self.store.count_our_root(root) >= max_nested:
                    self.store.mark_handled_reply(rpid, int(target.get("rpid") or 0))
                    continue
                ok, _why = self.limiter.can_write("nested")
                if not ok:
                    continue
                ctx = self._ctx_for_video(bvid, aid)
                thread = self._thread_context(page.get("items") or [], group, target, rpid,
                                              float(it.get("ctime") or 0))
                res = self.writer.reply(ctx, str(it.get("message") or ""), thread,
                                        str(it.get("uname") or ""))
                if not res:
                    continue
                review = str(self.cfg.get("bot.approval_mode", "auto")).lower() in ("review", "manual", "approve")
                if review:
                    draft_id = self.store.add_draft(
                        "nested", res["text"], bvid=bvid, aid=aid, root=root, parent=rpid,
                        score={"reply_to": int(target.get("rpid") or 0),
                               "uname": it.get("uname"), "parent_text": it.get("message")},
                        alt=res.get("candidates"), status="pending",
                    )
                    self.store.mark_handled_reply(rpid, int(target.get("rpid") or 0))
                    self.store.log_action("draft_review", f"{bvid}:{root}:{rpid}", res["text"])
                    self.log.info("楼中楼草稿 %s：%s", draft_id, res["text"][:40])
                    if self._stop.wait(5):
                        return
                    continue
                action = self.tracker_actions.post_comment(
                    {"bvid": bvid, "aid": aid}, res["text"],
                    root=root, parent=rpid,
                )
                if action.get("ok"):
                    self.store.mark_handled_reply(rpid, int(target.get("rpid") or 0))
                    self.log.info("楼中楼回复 %s：%s", it.get("uname"), res["text"][:40])
                if self._stop.wait(5):
                    return

    def _dm_history(self, talker: int, msgs: list[dict], latest_text: str) -> list[dict]:
        """合并接口最近消息与本地记录，给模型更长的上下文。"""
        rows: dict[tuple[int, str], dict] = {}
        for m in msgs:
            ts = int(m.get("timestamp") or 0)
            content = str(m.get("content") or "").strip()
            if not content:
                continue
            role = "assistant" if int(m.get("sender_uid") or 0) == self.self_uid else "user"
            rows[(ts, content)] = {"role": role, "content": content, "_ts": ts}
        try:
            db = self.store.query(
                "SELECT sender_uid,content,ts,direction FROM dm_messages "
                "WHERE talker_id=? ORDER BY ts ASC LIMIT 200", (talker,))
        except Exception:  # noqa: BLE001
            db = []
        for r in db:
            content = str(r.get("content") or "").strip()
            if not content:
                continue
            ts = int(r.get("ts") or 0)
            role = "assistant" if str(r.get("direction") or "") == "out" or \
                int(r.get("sender_uid") or 0) == self.self_uid else "user"
            rows[(ts, content)] = {"role": role, "content": content, "_ts": ts}
        ordered = sorted(rows.values(), key=lambda x: x["_ts"])
        hist = [{"role": x["role"], "content": x["content"]} for x in ordered]
        if hist and hist[-1]["role"] == "user" and hist[-1]["content"] == latest_text:
            hist = hist[:-1]
        return hist[-24:]

    def _track_dms(self) -> None:
        if not bool(self.cfg.get("bot.dm.reply_incoming", True)):
            return
        try:
            sessions = dm_mod.sessions(self.tracker_client)
        except Exception as e:  # noqa: BLE001
            self.log.info("私信会话读取失败：%s", e)
            return
        for s in sessions[:8]:
            if self._stop.is_set():
                return
            talker = int(s.get("talker_id") or 0)
            if not talker:
                continue
            try:
                msgs = dm_mod.messages(self.tracker_client, talker, size=40)
            except Exception as e:  # noqa: BLE001
                self.log.info("私信记录读取失败 %s：%s", talker, e)
                continue
            for m in msgs:
                if int(m.get("sender_uid") or 0) != self.self_uid and str(m.get("content") or "").strip():
                    self.store.save_dm_message(talker, int(m["sender_uid"]), str(m["content"]),
                                               int(m.get("timestamp") or 0), "in")
            incoming = [m for m in msgs if int(m.get("sender_uid") or 0) != self.self_uid
                        and str(m.get("content") or "").strip()]
            if not incoming:
                continue
            last = incoming[-1]
            last_text = str(last.get("content") or "").strip()
            if not last_text or "登录操作通知" in last_text or "安全中心" in last_text:
                continue
            # 不追着几小时前的旧私信回复，刚收到的才回。
            if time.time() - int(last.get("timestamp") or 0) > 1800:
                self.log.info("私信过旧，跳过 %s（%s）", talker, last_text[:40])
                continue
            row = self.store.query_one(
                "SELECT MAX(ts) AS t FROM dm_messages WHERE talker_id=? AND direction='out'",
                (talker,),
            )
            if int((row or {}).get("t") or 0) >= int(last.get("timestamp") or 0):
                continue
            ok, why = self.limiter.can_write("dm", ignore_hours=True, ignore_gap=True)
            if not ok:
                self.log.info("私信暂缓 %s：%s", talker, why)
                continue
            hist = self._dm_history(talker, msgs, last_text)
            text = self.writer.dm(str(s.get("uname") or talker), hist, str(last.get("content") or ""))
            if not text:
                self.log.info("私信生成未通过质检，跳过 %s（最新：%s）", talker,
                              str(last.get("content") or "")[:40])
                continue
            res = self.tracker_actions.send_dm(talker, text)
            if res.get("ok"):
                self.store.upsert_thread(talker, str(s.get("uname") or ""), text,
                                         int(time.time()), 0)
                self.store.log_action("dm_reply", str(talker), text, ok=True)
            else:
                self.log.warning("私信回复失败 %s：%s", talker, res.get("error"))
            if self._stop.wait(5):
                return

    def _track_visibility(self) -> None:
        ours = self.store.recent_our_comments(hours=8)
        for o in ours:
            if self._stop.is_set():
                return
            status = str(o.get("status") or "posted")
            if status in ("visible", "deleted", "unverified", "shadow", "hidden"):
                continue
            now = time.time()
            age = now - float(o.get("posted_at") or 0)
            if age < 60:
                continue
            if float(o.get("checked_at") or 0) and now - float(o["checked_at"]) < 180:
                continue
            bvid = str(o.get("bvid") or "")
            aid = int(o.get("aid") or 0)
            rpid = int(o.get("rpid") or 0)
            root = int(o.get("root") or 0)
            found = None
            try:
                if root:
                    page = comments_mod.sub_replies(self.tracker_client, aid, root, ps=20)
                    found = next((x for x in page.get("items") or []
                                  if int(x.get("rpid") or 0) == rpid), None)
                else:
                    offset = ""
                    for _ in range(2):
                        page = comments_mod.video_replies(self.tracker_client, aid, ps=20, offset=offset)
                        found = next((x for x in page.get("items") or []
                                      if int(x.get("rpid") or 0) == rpid), None)
                        if found:
                            break
                        offset = str(page.get("cursor") or "")
                        if not offset:
                            break
            except BiliError as e:
                self.circuit.handle(e)
                continue
            if found is not None:
                state = int(found.get("state") or 0)
                invisible = bool(found.get("invisible"))
                if state != 0 or invisible:
                    self.store.update_our_comment(rpid, visible=0, status="shadow", checked_at=now)
                    self.store.log_action("shadowban", bvid,
                                          {"rpid": rpid, "state": state, "invisible": invisible},
                                          ok=False)
                    self.log.warning("评论仅自己可见（B站风控/审核）%s rpid=%s state=%s invisible=%s",
                                     bvid, rpid, state, invisible)
                else:
                    self.store.update_our_comment(rpid, visible=1, status="visible", checked_at=now)
            elif age > 900:
                self.store.update_our_comment(rpid, visible=0, status="unverified", checked_at=now)
                self.store.log_action("swallowed", bvid, {"rpid": rpid}, ok=False)
                self.log.warning("评论 15 分钟不可见（可能被吞）%s rpid=%s", bvid, rpid)
            else:
                self.store.update_our_comment(rpid, checked_at=now)

    # ------------------------------------------------------------------ 状态
    def _cached_login(self, force: bool = False) -> dict:
        """状态接口不能等 B 站网络；过期时先返回上次结果，后台刷新。"""
        if force:
            with self._status_lock:
                data = dict(login_mod.status(self.web_client))
                self._login_data = data
                self._login_ts = time.time()
                return dict(data)
        with self._status_lock:
            if time.time() - self._login_ts < 45:
                return dict(self._login_data)
            data = dict(self._login_data)
            need = not self._login_refreshing
            if need:
                self._login_refreshing = True
        if need:
            threading.Thread(target=self._refresh_login, name="login-refresh", daemon=True).start()
        return data

    def _refresh_login(self) -> None:
        try:
            data = dict(login_mod.status(self.web_client))
            with self._status_lock:
                self._login_data = data
                self._login_ts = time.time()
        except Exception as e:  # noqa: BLE001
            self.log.warning("登录状态刷新失败：%s", e)
        finally:
            with self._status_lock:
                self._login_refreshing = False

    def _cached_llm(self, force: bool = False) -> tuple[bool, str]:
        if force:
            with self._status_lock:
                self._llm_data = self.llm.available(timeout=2.5)
                self._llm_ts = time.time()
                return self._llm_data
        with self._status_lock:
            if time.time() - self._llm_ts < 30:
                return self._llm_data
            data = self._llm_data
            need = not self._llm_refreshing
            if need:
                self._llm_refreshing = True
        if need:
            threading.Thread(target=self._refresh_llm, name="llm-refresh", daemon=True).start()
        return data

    def _refresh_llm(self) -> None:
        try:
            data = self.llm.available(timeout=2.5)
            with self._status_lock:
                self._llm_data = data
                self._llm_ts = time.time()
        except Exception as e:  # noqa: BLE001
            self.log.warning("模型状态刷新失败：%s", e)
        finally:
            with self._status_lock:
                self._llm_refreshing = False

    def status(self) -> dict:
        login_info = self._cached_login()
        llm_ok, llm_msg = self._cached_llm()
        counts = self.store.stats()
        drafts = self.store.query_one("SELECT COUNT(*) AS n FROM drafts WHERE status='pending'") or {}
        limits = {}
        caps = {"comment": "bot.comment_per_day", "nested": "bot.nested_per_day",
                "dm": "bot.dm_per_day"}
        for kind, key in caps.items():
            cap = int(self.cfg.get(key, 0) or 0)
            used = int(counts.get("today_" + kind, 0) or 0)
            ok, why = self.limiter.can_write(kind, ignore_hours=True)
            limits[kind] = {"used": used, "cap": cap,
                            "remaining": max(0, cap - used) if cap > 0 else 0,
                            "can": ok, "why": why}
        return {
            "state": self.state,
            "running": bool(self._cycle_thread and self._cycle_thread.is_alive()),
            "paused": self._pause.is_set(),
            "current": self.current,
            "last_skip": self.last_skip,
            "last_skip_ts": self.last_skip_ts,
            "last_cycle": self.last_cycle,
            "last_error": self.last_error,
            "login": login_info,
            "llm": {"ok": llm_ok, "message": llm_msg, "base_url": self.llm.base_url,
                    "model": self.llm.model},
            "counts": counts,
            "limits": limits,
            "drafts_pending": int(drafts.get("n") or 0),
            "circuit": self.circuit.info(),
            "persona": self.persona.get("name"),
            "voice": {
                "asr": dict(zip(("ok", "message"), self.asr.check())),
            },
            "recent_actions": self.store.recent_actions(8),
        }

    # ------------------------------------------------------------------ 工具
    def _sleep_interruptible(self, seconds: float) -> bool:
        end = time.time() + max(0.0, seconds)
        while time.time() < end and not self._stop.is_set():
            if self._pause.is_set():
                return False
            self._stop.wait(min(1.0, max(0.05, end - time.time())))
        return self._stop.is_set()
