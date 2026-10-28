# -*- coding: utf-8 -*-
"""OpenAI 兼容 LLM 客户端（llama.cpp / Ollama / vLLM 都能用）。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from app.config import Config


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    # ------------------------------------------------------------------ 配置
    @property
    def base_url(self) -> str:
        return str(self.cfg.get("llm.base_url") or "http://127.0.0.1:8080/v1").rstrip("/")

    @property
    def model(self) -> str:
        return str(self.cfg.get("llm.model") or "qwen3.5-4b")

    @property
    def api_key(self) -> str:
        return str(self.cfg.get("llm.api_key") or "")

    def available(self, timeout: float = 3.0) -> tuple[bool, str]:
        url = self.base_url + "/models"
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
            data = json.loads(raw)
            ids = [str(x.get("id") or "") for x in (data.get("data") or []) if isinstance(x, dict)]
            return True, f"模型服务在线（{', '.join(ids[:3]) or self.model}）"
        except Exception as e:
            return False, f"模型服务不可用：{e}"

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    # ------------------------------------------------------------------ 调用
    def chat(self, messages: list[dict], *, temperature: Optional[float] = None,
             max_tokens: Optional[int] = None, json_mode: Optional[bool] = None,
             timeout: Optional[float] = None) -> str:
        cfg = self.cfg
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": float(cfg.get("llm.temperature", 0.9) if temperature is None else temperature),
            "max_tokens": int(cfg.get("llm.max_tokens", 320) if max_tokens is None else max_tokens),
            "stream": False,
        }
        use_json = bool(cfg.get("llm.json_mode", False) if json_mode is None else json_mode)
        if use_json:
            payload["response_format"] = {"type": "json_object"}
        # Qwen3.x 默认会先输出一段思考；聊天/评论场景直接关掉，省 token 也省时间。
        if bool(cfg.get("llm.disable_thinking", True)):
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        url = self.base_url + "/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_err = ""
        for attempt in range(2):
            req = urllib.request.Request(url, data=body, headers=self._headers(), method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout or float(cfg.get("llm.timeout_s", 180))) as resp:
                    raw = resp.read().decode("utf-8", "replace")
                data = json.loads(raw)
                choices = data.get("choices") or []
                if not choices:
                    raise LLMError(f"模型返回没有 choices：{raw[:200]}")
                msg = choices[0].get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    content = "".join(str(x.get("text") or "") for x in content if isinstance(x, dict))
                text = str(content or "").strip()
                if not text:
                    raise LLMError("模型返回空内容")
                return text
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:300]
                last_err = f"HTTP {e.code}：{detail}"
                if e.code == 400 and use_json and attempt == 0:
                    payload.pop("response_format", None)
                    payload.pop("chat_template_kwargs", None)
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    use_json = False
                    continue
                if e.code == 400 and attempt == 0 and "chat_template_kwargs" in payload:
                    payload.pop("chat_template_kwargs", None)
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    continue
                break
            except (urllib.error.URLError, TimeoutError, ValueError, LLMError) as e:
                last_err = str(e)
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                break
        raise LLMError(last_err or "模型调用失败")

    def chat_json(self, messages: list[dict], **kw: Any) -> Any:
        kw.setdefault("json_mode", True)
        text = self.chat(messages, **kw)
        return extract_json(text)


def extract_json(text: str) -> Any:
    s = (text or "").strip()
    if s.startswith("```"):
        lines = [ln for ln in s.splitlines() if not ln.strip().startswith("```")]
        s = "\n".join(lines).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = s.find(opener)
        end = s.rfind(closer)
        if start >= 0 and end > start:
            chunk = s[start:end + 1]
            try:
                return json.loads(chunk)
            except ValueError:
                try:
                    return json.loads(chunk.replace("，", ",").replace("：", ":"))
                except ValueError:
                    pass
    try:
        return json.loads(s)
    except ValueError as e:
        raise LLMError(f"模型输出不是合法 JSON：{s[:200]}") from e
