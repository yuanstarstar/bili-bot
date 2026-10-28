# -*- coding: utf-8 -*-
"""写操作执行器：评论/楼中楼/私信/点赞，统一记录流水与熔断。"""
from __future__ import annotations

import time
from typing import Any

from app.bili import dm as dm_mod
from app.bili import video as video_mod
from app.bili.errors import BiliError
from app.robot.circuit import Circuit


class Actions:
    def __init__(self, cfg, store, logger, client, circuit: Circuit) -> None:
        self.cfg = cfg
        self.store = store
        self.log = logger
        self.client = client
        self.circuit = circuit

    @property
    def self_uid(self) -> int:
        try:
            return int(self.client.cookie_pairs.get("DedeUserID") or 0)
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------ 评论
    def post_comment(self, video: dict, text: str, *, root: int = 0, parent: int = 0,
                     at_uid: int = 0, at_name: str = "") -> dict:
        bvid = str(video.get("bvid") or "")
        aid = int(video.get("aid") or 0)
        kind = "comment" if not root else "nested"
        target = f"{bvid}:{aid}:{root}:{parent}"
        try:
            res = video_mod.post_comment(
                self.client, bvid, text, root=int(root or 0), parent=int(parent or 0),
                at_uid=int(at_uid or 0), at_name=str(at_name or ""),
            )
            rpid = int(res.get("rpid") or 0)
            if rpid:
                self.store.add_our_comment(bvid, aid, rpid, int(root or 0), int(parent or 0),
                                           text, int(at_uid or 0))
            self.store.log_action(kind, target, text, ok=True)
            if at_uid:
                self.store.log_action("at", target, text, ok=True)
            self.circuit.on_success()
            self.log.info("%s 成功 %s rpid=%s：%s", kind, bvid, rpid, text[:40])
            return {"ok": True, "rpid": rpid, "aid": aid, "bvid": bvid, "kind": kind}
        except BiliError as e:
            self.store.log_action(kind, target, text, ok=False, error=str(e))
            tripped = self.circuit.handle(e)
            self.log.warning("%s 失败 %s：%s%s", kind, bvid, e, "（已熔断）" if tripped else "")
            return {"ok": False, "error": str(e), "code": getattr(e, "code", None)}
        except Exception as e:  # noqa: BLE001
            self.store.log_action(kind, target, text, ok=False, error=str(e))
            self.circuit.on_failure(str(e))
            self.log.exception("%s 异常：%s", kind, e)
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------ 私信
    def send_dm(self, uid: int, text: str) -> dict:
        target = str(int(uid))
        try:
            res = dm_mod.send(self.client, int(uid), text)
            self.store.save_dm_message(int(uid), self.self_uid, text, int(time.time()), "out")
            self.store.log_action("dm", target, text, ok=True)
            self.circuit.on_success()
            self.log.info("私信成功 uid=%s：%s", uid, text[:40])
            return {"ok": True, "uid": int(uid), "data": res.get("data")}
        except BiliError as e:
            self.store.log_action("dm", target, text, ok=False, error=str(e))
            tripped = self.circuit.handle(e)
            self.log.warning("私信失败 uid=%s：%s%s", uid, e, "（已熔断）" if tripped else "")
            return {"ok": False, "error": str(e), "code": getattr(e, "code", None)}
        except Exception as e:  # noqa: BLE001
            self.store.log_action("dm", target, text, ok=False, error=str(e))
            self.circuit.on_failure(str(e))
            self.log.exception("私信异常：%s", e)
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------ 点赞
    def like(self, bvid: str) -> dict:
        try:
            video_mod.like_video(self.client, bvid, 1)
            self.store.log_action("like", bvid, "", ok=True)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            self.store.log_action("like", bvid, "", ok=False, error=str(e))
            self.circuit.handle(e) if isinstance(e, BiliError) else self.circuit.on_failure(str(e))
            return {"ok": False, "error": str(e)}
