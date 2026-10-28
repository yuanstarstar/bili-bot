# -*- coding: utf-8 -*-
"""生成器：钩子 → 候选 → 自评 → 质检；楼中楼、私信、选视频评分。"""
from __future__ import annotations

import threading
from typing import Any, Optional

from app.brain import judge, persona as persona_mod, prompts
from app.brain.llm import LLM, LLMError


class Writer:
    def __init__(self, llm: LLM, persona: dict) -> None:
        self.llm = llm
        self.persona = persona_mod.normalize(persona)
        self._llm_lock = threading.RLock()

    def _messages(self, channel: str, user_prompt: str) -> list[dict]:
        return [
            {"role": "system", "content": persona_mod.system(self.persona, channel)},
            {"role": "user", "content": user_prompt},
        ]

    def _json(self, channel: str, user_prompt: str, max_tokens: int = 360) -> Any:
        with self._llm_lock:
            return self.llm.chat_json(self._messages(channel, user_prompt), max_tokens=max_tokens)

    # ------------------------------------------------------------------ 钩子
    def hooks(self, ctx_text: str) -> list[str]:
        try:
            obj = self._json("comment", prompts.hooks(ctx_text), max_tokens=160)
        except LLMError:
            return []
        raw = obj.get("hooks") if isinstance(obj, dict) else obj
        if not isinstance(raw, list):
            return []
        out = []
        for x in raw:
            s = str(x).strip()
            if 2 <= len(s) <= 40:
                out.append(s)
        return out[:3]

    # ------------------------------------------------------------------ 首评
    def comment(self, ctx_text: str, examples: list[str]) -> Optional[dict]:
        hook_list = self.hooks(ctx_text)
        try:
            obj = self._json("comment", prompts.comment(ctx_text, self.persona, hook_list, examples),
                             max_tokens=360)
        except LLMError:
            return None
        raw = obj.get("comments") if isinstance(obj, dict) else obj
        if not isinstance(raw, list):
            return None
        candidates = [judge.clean(str(x)) for x in raw if str(x).strip()]
        candidates = [x for x in candidates if x]
        if not candidates:
            return None
        preferred = -1
        critique: dict = {}
        try:
            cj = self._json("comment", prompts.critique(ctx_text, candidates), max_tokens=120)
            if isinstance(cj, dict):
                critique = cj
                preferred = int(cj.get("best", -1))
        except (LLMError, ValueError, TypeError):
            pass
        recent = self._recent()
        text, checked = judge.pick(candidates, self.persona, "comment", recent=recent,
                                   preferred=preferred)
        if not text:
            return None
        return {"text": text, "hooks": hook_list, "candidates": candidates,
                "critique": critique, "checked": checked}

    # ------------------------------------------------------------------ 楼中楼
    def reply(self, ctx_text: str, parent_text: str, thread_text: str = "",
              parent_uname: str = "") -> Optional[dict]:
        try:
            obj = self._json("nested", prompts.reply(ctx_text, self.persona, parent_text,
                                                     thread_text=thread_text,
                                                     parent_uname=parent_uname),
                             max_tokens=240)
        except LLMError:
            return None
        raw = obj.get("replies") if isinstance(obj, dict) else obj
        if not isinstance(raw, list):
            return None
        candidates = [judge.clean(str(x)) for x in raw if str(x).strip()]
        text, checked = judge.pick(candidates, self.persona, "nested", recent=self._recent())
        if not text:
            return None
        return {"text": text, "candidates": candidates, "checked": checked}

    # ------------------------------------------------------------------ 私信
    def dm(self, uname: str, history: list[dict], latest: str) -> Optional[str]:
        try:
            obj = self._json("dm", prompts.dm(self.persona, uname, history, latest), max_tokens=200)
        except LLMError:
            return None
        raw = obj.get("reply") if isinstance(obj, dict) else obj
        s = judge.clean(str(raw or ""))
        ok, _ = judge.valid(s, self.persona, "dm")
        if not ok:
            return None
        # 不要原样复读最近说过/对方刚说过的话。
        recent = [judge.clean(str(m.get("content") or "")) for m in history[-6:]]
        if s and s in recent:
            return None
        return s

    # ------------------------------------------------------------------ 选视频
    def score_video(self, video: dict) -> tuple[float, str]:
        try:
            obj = self._json("comment", prompts.score_video(self.persona, video), max_tokens=80)
            if not isinstance(obj, dict):
                return 5.0, "no-json"
            score = float(obj.get("score") or 5)
            return max(0.0, min(10.0, score)), str(obj.get("why") or "")[:120]
        except (LLMError, ValueError, TypeError):
            return 5.0, "fallback"

    # ------------------------------------------------------------------ 内部
    def _recent(self) -> list[str]:
        recent = getattr(self, "_recent_cache", None)
        if recent is None:
            recent = []
            try:
                store = getattr(self, "store", None)
                if store is not None:
                    rows = store.query("SELECT message FROM our_comments ORDER BY id DESC LIMIT 50")
                    recent = [r["message"] for r in rows]
            except Exception:
                recent = []
            self._recent_cache = recent
        return recent
