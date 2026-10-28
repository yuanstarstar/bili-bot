# -*- coding: utf-8 -*-
"""本地 Web 控制台：控制机器人、扫码登录、人设、配置、草稿、日志。"""
from __future__ import annotations

import json
import mimetypes
import re
import shutil
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from app.bili import login as login_mod
from app.brain import persona as persona_mod
from app.config import Config
from app.log import recent as recent_logs
from app.store import Store

STATIC = Path(__file__).resolve().parent / "static"
_DRAFT_RE = re.compile(r"^/api/drafts/(\d+)$")
_MAX_BODY = 2 * 1024 * 1024


class BotHandler(BaseHTTPRequestHandler):
    server_version = "BiliBot/0.1"
    protocol_version = "HTTP/1.1"
    ctx: dict[str, Any] = {}

    # ------------------------------------------------------------------ HTTP
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                self._api_get(path, qs)
                return
            if path in ("/", "/index.html"):
                self._file(STATIC / "index.html")
                return
            if path.startswith("/static/"):
                rel = path[len("/static/"):]
                target = (STATIC / rel).resolve()
                if not str(target).startswith(str(STATIC.resolve())):
                    self._json({"ok": False, "error": "forbidden"}, 403)
                    return
                self._file(target)
                return
            self._json({"ok": False, "error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": str(e)}, 500)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_json()
            self._api_post(path, body)
        except ValueError as e:
            self._json({"ok": False, "error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            self._json({"ok": False, "error": str(e)}, 500)

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        try:
            self.ctx.get("logger").debug("%s %s", self.command, self.path)
        except Exception:
            pass

    # ------------------------------------------------------------------ 基础
    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > _MAX_BODY:
            raise ValueError("请求体过大")
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise ValueError(f"JSON 格式不正确：{e}") from e
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def _json(self, data: Any, status: int = 200) -> None:
        t0 = time.time()
        raw = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)
        try:
            self.ctx.get("logger").info("JSON %s %d bytes %.2fs", self.path, len(raw), time.time() - t0)
        except Exception:
            pass

    def _file(self, path: Path) -> None:
        path = Path(path)
        if not path.is_file():
            self._json({"ok": False, "error": "not found"}, 404)
            return
        data = path.read_bytes()
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    # ------------------------------------------------------------------ GET
    def _api_get(self, path: str, qs: dict) -> None:
        cfg: Config = self.ctx["cfg"]
        store: Store = self.ctx["store"]
        engine = self.ctx["engine"]
        if path == "/api/status":
            data = engine.status()
            data["ok"] = True
            self._json(data)
        elif path == "/api/config":
            self._json({"ok": True, "config": cfg.snapshot()})
        elif path == "/api/persona":
            self._json({"ok": True, "persona": persona_mod.load(cfg.persona_path)})
        elif path == "/api/logs":
            n = int((qs.get("n") or ["200"])[0])
            self._json({"ok": True, "lines": recent_logs(n)})
        elif path == "/api/actions":
            n = int((qs.get("limit") or ["50"])[0])
            self._json({"ok": True, "actions": store.recent_actions(n)})
        elif path == "/api/videos":
            n = int((qs.get("limit") or ["30"])[0])
            self._json({"ok": True, "videos": store.query(
                "SELECT bvid,title,owner_name,duration,tname,fetched_at FROM videos "
                "ORDER BY fetched_at DESC LIMIT ?", (n,))})
        elif path == "/api/drafts":
            status = (qs.get("status") or [""])[0] or None
            self._json({"ok": True, "drafts": store.list_drafts(status, 100)})
        elif path == "/api/login/status":
            self._json({"ok": True, "login": engine._cached_login()})
        elif path == "/api/login/poll":
            key = (qs.get("key") or [""])[0]
            if not key:
                self._json({"ok": False, "error": "缺少 key"}, 400)
                return
            res = login_mod.poll(key)
            if res.get("status") == "ok":
                cookies = res.pop("cookies", {}) or {}
                applied = engine.apply_login(cookies)
                res = {"ok": True, "status": "ok", "message": "登录成功",
                       "user": applied.get("user")}
            else:
                res["ok"] = False
            self._json(res)
        elif path == "/api/model/test":
            ok, msg = engine._cached_llm(force=True)
            self._json({"ok": ok, "message": msg})
        elif path == "/api/feed":
            n = int((qs.get("limit") or ["10"])[0])
            items = engine.preview_candidates(n)
            self._json({"ok": True, "videos": [
                {"bvid": x.get("bvid"), "title": x.get("title"),
                 "owner": (x.get("owner") or {}).get("name"),
                 "duration": x.get("duration"), "source": x.get("_source")}
                for x in items
            ]})
        elif path == "/api/cache":
            self._json({"ok": True, **self._cache_info(cfg)})
        elif path == "/api/voice/status":
            self._json({"ok": True, "enabled": bool(cfg.get("voice.enabled", True)),
                        "asr": dict(zip(("ok", "message"), engine.asr.check()))})
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    # ------------------------------------------------------------------ POST
    def _api_post(self, path: str, body: dict) -> None:
        cfg: Config = self.ctx["cfg"]
        store: Store = self.ctx["store"]
        engine = self.ctx["engine"]

        if path == "/api/config":
            patch = body if isinstance(body, dict) else {}
            cfg.update(patch)
            engine.reload_persona()
            engine._login_ts = 0.0
            engine._llm_ts = 0.0
            self._json({"ok": True, "config": cfg.snapshot()})
        elif path == "/api/persona":
            if isinstance(body.get("persona"), dict) and not body.get("name"):
                body = body["persona"]
            if not isinstance(body, dict) or not str(body.get("name") or "").strip():
                raise ValueError("人设缺少 name（名字不能为空）")
            try:
                persona_mod.save(cfg.persona_path, body)
            except Exception as e:  # noqa: BLE001
                raise ValueError(f"人设保存失败：{e}") from e
            engine.reload_persona()
            self._json({"ok": True, "persona": engine.persona})
        elif path == "/api/login/qr":
            self._json({"ok": True, **login_mod.generate()})
        elif path == "/api/bot/start":
            ok, msg = engine.start()
            self._json({"ok": ok, "message": msg, "error": "" if ok else msg})
        elif path == "/api/bot/pause":
            ok, msg = engine.pause()
            self._json({"ok": ok, "message": msg, "error": "" if ok else msg})
        elif path == "/api/bot/resume":
            ok, msg = engine.resume()
            self._json({"ok": ok, "message": msg, "error": "" if ok else msg})
        elif path == "/api/bot/stop":
            ok, msg = engine.stop()
            self._json({"ok": ok, "message": msg, "error": "" if ok else msg})
        elif path == "/api/bot/once":
            dry = bool(body.get("dry_run", True))
            force = bool(body.get("force", False))
            bvid = str(body.get("bvid") or "").strip()
            deep = bool(body.get("deep", bool(bvid)))
            import faulthandler
            faulthandler.dump_traceback_later(180 if bvid else 25, repeat=False)
            try:
                res = engine.run_once(dry_run=dry, force=force, bvid=bvid, deep=deep)
            finally:
                faulthandler.cancel_dump_traceback_later()
            res.setdefault("ok", False)
            self._json(res)
        elif path == "/api/bot/reset_quota":
            store.set_setting("quota_reset_ts", str(time.time()))
            store.log_action("quota_reset", "", "手动重置今日计数", True)
            self._json({"ok": True, "message": "已重置今日计数"})
        elif path == "/api/model/test":
            ok, msg = engine._cached_llm(force=True)
            self._json({"ok": ok, "message": msg})
        elif path == "/api/cache/clean":
            kind = str(body.get("kind") or "cache")
            self._json({"ok": True, **self._clean_cache(cfg, kind)})
        elif path == "/api/voice/asr":
            bvid = str(body.get("bvid") or "").strip()
            if not bvid:
                self._json({"ok": False, "error": "缺少 bvid"}, 400)
                return
            try:
                from app.bili import video as video_mod
                meta = video_mod.view(engine.client, bvid)
                tr = engine.asr.transcribe_video(
                    engine.client, meta,
                    max_seconds=int(body.get("seconds") or cfg.get("voice.asr.max_seconds", 120) or 120),
                )
                self._json({"ok": True, "bvid": bvid, **tr})
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)}, 500)
        else:
            m = _DRAFT_RE.match(path)
            if m:
                self._draft_action(int(m.group(1)), body)
            else:
                self._json({"ok": False, "error": "not found"}, 404)

    # ------------------------------------------------------------------ 草稿
    def _draft_action(self, draft_id: int, body: dict) -> None:
        store: Store = self.ctx["store"]
        engine = self.ctx["engine"]
        draft = store.get_draft(draft_id)
        if not draft:
            self._json({"ok": False, "error": "草稿不存在"}, 404)
            return
        action = str(body.get("action") or "").strip()
        if action == "reject":
            store.update_draft(draft_id, status="rejected")
            self._json({"ok": True, "message": "已忽略"})
            return
        if action != "approve":
            self._json({"ok": False, "error": "未知操作"}, 400)
            return
        if str(draft.get("status")) != "pending":
            self._json({"ok": False, "error": f"草稿状态是 {draft.get('status')}"}, 400)
            return
        opened, why = engine.circuit.is_open()
        if opened:
            self._json({"ok": False, "error": why}, 400)
            return
        kind = "nested" if int(draft.get("root") or 0) else "comment"
        ok, reason = engine.limiter.can_write(kind, ignore_hours=True, ignore_cap=True)
        if not ok:
            self._json({"ok": False, "error": f"未发布：{reason}"}, 400)
            return
        try:
            score_meta = json.loads(draft.get("score") or "{}")
        except Exception:
            score_meta = {}
        res = engine.actions.post_comment(
            {"bvid": draft.get("bvid"), "aid": int(draft.get("aid") or 0)},
            str(draft.get("message") or ""), root=int(draft.get("root") or 0),
            parent=int(draft.get("parent") or 0),
            at_uid=int(score_meta.get("at_uid") or 0),
            at_name=str(score_meta.get("at_name") or ""),
        )
        if res.get("ok"):
            store.update_draft(draft_id, status="sent", sent_at=time.time())
        else:
            store.update_draft(draft_id, status="failed", error=str(res.get("error") or ""))
        self._json(res)

    # ------------------------------------------------------------------ 缓存
    @staticmethod
    def _dir_size(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())

    def _cache_info(self, cfg: Config) -> dict:
        return {
            "cache_bytes": self._dir_size(cfg.cache_dir),
            "logs_bytes": self._dir_size(cfg.log_dir),
            "db_bytes": cfg.db_path.stat().st_size if cfg.db_path.exists() else 0,
            "data_bytes": self._dir_size(cfg.data_dir),
        }

    def _clean_cache(self, cfg: Config, kind: str) -> dict:
        freed = 0
        errors: list[str] = []
        if kind in ("cache", "all") and cfg.cache_dir.exists():
            for p in list(cfg.cache_dir.iterdir()):
                try:
                    if p.is_file():
                        freed += p.stat().st_size
                        p.unlink()
                    elif p.is_dir():
                        freed += self._dir_size(p)
                        shutil.rmtree(p)
                except OSError as e:
                    errors.append(str(e))
        if kind in ("logs", "all") and cfg.log_dir.exists():
            for p in list(cfg.log_dir.glob("bot.log.*")):
                try:
                    freed += p.stat().st_size
                    p.unlink()
                except OSError as e:
                    errors.append(str(e))
        self.ctx.get("logger").info("清理缓存 %s，释放 %.1f KB", kind, freed / 1024)
        return {"freed": freed, "errors": errors[:5], **self._cache_info(cfg)}


def make_handler(ctx: dict[str, Any]):
    class Handler(BotHandler):
        pass
    Handler.ctx = ctx
    return Handler


class BotHTTPServer(ThreadingHTTPServer):
    # Windows 的 SO_REUSEADDR 会允许两个进程同时监听同一端口，导致旧进程
    # 继续用旧配置响应 /api/status、/api/config。这里禁止复用，端口冲突
    # 时让启动流程换端口，而不是静默双开。
    allow_reuse_address = False
    daemon_threads = True


class WebServer:
    def __init__(self, cfg: Config, store: Store, engine, logger) -> None:
        self.cfg = cfg
        self.store = store
        self.engine = engine
        self.log = logger
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[Thread] = None
        self.port = 0
        self.host = str(cfg.get("app.host") or "127.0.0.1")

    def start(self) -> str:
        wanted = int(self.cfg.get("app.port") or 8765)
        ctx = {"cfg": self.cfg, "store": self.store, "engine": self.engine, "logger": self.log}
        handler = make_handler(ctx)
        last_err: Optional[Exception] = None
        for port in range(max(0, wanted), max(0, wanted) + 10):
            try:
                httpd = BotHTTPServer((self.host, port), handler)
                httpd.daemon_threads = True
                self._httpd = httpd
                self.port = int(httpd.server_address[1])
                break
            except OSError as e:
                last_err = e
        if not self._httpd:
            raise RuntimeError(f"控制台端口绑定失败：{last_err}")
        self._thread = Thread(target=self._httpd.serve_forever, name="web", daemon=True)
        self._thread.start()
        self.log.info("控制台：%s", self.url)
        return self.url

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        return f"http://{host}:{self.port}"
