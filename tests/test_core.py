# -*- coding: utf-8 -*-
"""核心冒烟测试：配置、存储、限速、熔断、质检、登录 cookie、Web 路由。"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import unittest
import urllib.request
from pathlib import Path

from app.bili import login
from app.bili.client import BiliClient
from app.brain import judge, persona, prompts
from app.brain.llm import extract_json
from app.config import Config
from app.robot.circuit import Circuit
from app.robot.rate import RateLimiter
from app.store import Store
from app.web.server import WebServer


class CoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="bilibot-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = Path(self.tmp)
        self.cfg = Config(self.root / "config.json")
        self.cfg.update({"bot": {"active_hours": ["00:00-23:59"]}})
        self.cfg.ensure_dirs()
        self.store = Store(self.cfg.db_path)
        self.p = persona.load(self.cfg.persona_path)

    # ------------------------------------------------------------------ 配置
    def test_config_defaults_and_paths(self) -> None:
        self.assertEqual(self.cfg.get("llm.model"), "qwen3.5-4b")
        self.assertEqual(self.cfg.get("train.exclude_ranges"), [["2026-10-01", "2026-11-01"]])
        self.assertTrue(self.cfg.data_dir.exists())
        self.assertTrue((self.root / "config.json").exists())

    # ------------------------------------------------------------------ 存储
    def test_store_actions_comments_drafts(self) -> None:
        self.store.log_action("comment", "BV1xx", "hello")
        self.assertEqual(self.store.count_actions("comment", 0), 1)
        self.store.add_our_comment("BV1xx", 1, 1001, 0, 0, "test comment")
        rows = self.store.recent_our_comments(48)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rpid"], 1001)
        self.assertEqual(self.store.count_our_root(0), 1)
        did = self.store.add_draft("comment", "draft text", bvid="BV1xx", aid=1)
        draft = self.store.get_draft(did)
        self.assertEqual(draft["message"], "draft text")
        self.store.update_draft(did, status="sent")
        self.assertEqual(self.store.get_draft(did)["status"], "sent")

    # ------------------------------------------------------------------ 节奏
    def test_rate_limits(self) -> None:
        lim = RateLimiter(self.cfg, self.store)
        ok, _ = lim.can_write("comment")
        self.assertTrue(ok)
        self.store.log_action("comment", "BV1", "x")
        ok, why = lim.can_write("comment")
        self.assertFalse(ok)
        self.assertIn("分钟", why)
        self.assertEqual(lim.remaining("comment"), 7)
        self.assertGreaterEqual(lim.dwell_seconds(600), 20)
        self.assertLessEqual(lim.dwell_seconds(600), 480)

    # ------------------------------------------------------------------ 熔断
    def test_circuit(self) -> None:
        c = Circuit(self.cfg, self.store, logging.getLogger("test"))
        self.assertFalse(c.is_open()[0])
        c.trip("test risk", hours=1)
        ok, why = c.is_open()
        self.assertTrue(ok)
        self.assertIn("熔断", why)
        c.reset()
        self.assertFalse(c.is_open()[0])

    # ------------------------------------------------------------------ 质检
    def test_judge(self) -> None:
        text = "这个转场也太丝滑了"
        self.assertTrue(judge.valid(text, self.p, "comment")[0])
        self.assertFalse(judge.valid("首先，这个视频很不错", self.p, "comment")[0])
        self.assertEqual(judge.clean("评论：  笑死  "), "笑死")
        self.assertFalse(judge.valid("a" * 40, self.p, "comment")[0])
        picked, checked = judge.pick(["首先不错", text], self.p, "comment")
        self.assertEqual(picked, text)
        self.assertTrue(any(x["ok"] for x in checked))
        self.assertTrue(judge.near_duplicate(text, [text]))
        self.assertFalse(judge.near_duplicate(text, ["完全不同的另一句话内容啊"]))

    # ------------------------------------------------------------------ 人设
    def test_persona_prompt(self) -> None:
        self.assertEqual(self.p["name"], "鱼皮汽水")
        sys = persona.system(self.p, "comment")
        self.assertIn("鱼皮汽水", sys)
        self.assertIn("不是 AI", sys)
        self.assertIn("只输出", sys)

    def test_prompt_keeps_video_and_history(self) -> None:
        rp = prompts.reply(
            "标题：测试视频\n简介：这是一个关于测试的简介",
            self.p, "那你觉得呢", thread_text="我：根评论在这里\n对方：之前说过一句",
            parent_uname="小明")
        self.assertIn("根评论在这里", rp)
        self.assertIn("之前说过一句", rp)
        self.assertIn("原视频", rp)
        dp = prompts.dm(self.p, "小明", [
            {"role": "user", "content": "早上好"},
            {"role": "assistant", "content": "早"},
        ], "在吗")
        self.assertIn("早上好", dp)
        self.assertIn("更早的聊天", dp)

    # ------------------------------------------------------------------ JSON
    def test_extract_json(self) -> None:
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('前文 [1, 2, 3] 后文'), [1, 2, 3])

    # ------------------------------------------------------------------ 登录
    def test_cookie_and_login(self) -> None:
        pairs = login._parse_set_cookie([
            "SESSDATA=abc; Path=/; HttpOnly",
            "bili_jct=def; Path=/",
            "DedeUserID=123",
        ])
        self.assertEqual(pairs["SESSDATA"], "abc")
        self.assertEqual(pairs["DedeUserID"], "123")
        self.assertEqual(pairs["bili_jct"], "def")
        client = BiliClient({"timeout_s": 1}, self.root / "data")
        client.add_cookie_pairs({"SESSDATA": "abc", "bili_jct": "def", "DedeUserID": "123"})
        self.assertTrue(client.has_login)
        self.assertEqual(client.csrf, "def")
        saved = login.save_cookie(client, self.root / "data" / "cookie.json")
        self.assertTrue(Path(saved["path"]).exists())
        self.assertIn("SESSDATA", saved["keys"])

    # ------------------------------------------------------------------ Web
    def test_web_config_and_static(self) -> None:
        class FakeEngine:
            def status(self):
                return {"state": "idle", "running": False, "paused": False,
                        "login": {"isLogin": False}, "llm": {"ok": False},
                        "counts": {}, "circuit": {}}

        server = WebServer(self.cfg, self.store, FakeEngine(), logging.getLogger("test"))
        url = server.start()
        try:
            with urllib.request.urlopen(url + "/api/config", timeout=5) as r:
                data = json.loads(r.read().decode("utf-8"))
            self.assertTrue(data["ok"])
            self.assertEqual(data["config"]["llm"]["model"], "qwen3.5-4b")
            with urllib.request.urlopen(url + "/", timeout=5) as r:
                html = r.read().decode("utf-8")
            self.assertIn("BiliBot", html)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
