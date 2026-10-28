# -*- coding: utf-8 -*-
"""SQLite 存储：设置、动作流水、视频/评论、草稿、私信、去重。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  target TEXT,
  detail TEXT,
  ok INTEGER NOT NULL DEFAULT 1,
  error TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actions_kind_time ON actions(kind, created_at);
CREATE TABLE IF NOT EXISTS videos(
  bvid TEXT PRIMARY KEY,
  aid INTEGER,
  cid INTEGER,
  title TEXT,
  desc TEXT,
  tags TEXT,
  duration INTEGER,
  owner_mid INTEGER,
  owner_name TEXT,
  pubdate INTEGER,
  fetched_at REAL,
  meta TEXT
);
CREATE TABLE IF NOT EXISTS seen_videos(
  bvid TEXT PRIMARY KEY,
  first_seen REAL,
  last_seen REAL,
  action TEXT
);
CREATE TABLE IF NOT EXISTS comments(
  rpid INTEGER PRIMARY KEY,
  bvid TEXT,
  aid INTEGER,
  root INTEGER,
  parent INTEGER,
  mid INTEGER,
  uname TEXT,
  message TEXT,
  like_count INTEGER,
  ctime INTEGER,
  fetched_at REAL
);
CREATE INDEX IF NOT EXISTS idx_comments_like ON comments(like_count DESC);
CREATE TABLE IF NOT EXISTS our_comments(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bvid TEXT,
  aid INTEGER,
  rpid INTEGER UNIQUE,
  root INTEGER,
  parent INTEGER,
  message TEXT,
  at_uid INTEGER DEFAULT 0,
  posted_at REAL,
  checked_at REAL,
  status TEXT DEFAULT 'posted',
  visible INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_our_posted ON our_comments(posted_at);
CREATE TABLE IF NOT EXISTS drafts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  bvid TEXT,
  aid INTEGER,
  root INTEGER DEFAULT 0,
  parent INTEGER DEFAULT 0,
  message TEXT NOT NULL,
  alt TEXT,
  score TEXT,
  status TEXT DEFAULT 'pending',
  created_at REAL,
  sent_at REAL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS dm_threads(
  talker_id INTEGER PRIMARY KEY,
  uname TEXT,
  last_text TEXT,
  last_ts INTEGER,
  unread INTEGER DEFAULT 0,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS dm_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  talker_id INTEGER,
  sender_uid INTEGER,
  content TEXT,
  ts INTEGER,
  direction TEXT,
  msg_key TEXT UNIQUE,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS handled_replies(
  rpid INTEGER PRIMARY KEY,
  parent_rpid INTEGER,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS cache_index(
  path TEXT PRIMARY KEY,
  kind TEXT,
  size INTEGER,
  created_at REAL,
  last_used REAL
);
CREATE TABLE IF NOT EXISTS jobs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT,
  status TEXT,
  progress REAL,
  detail TEXT,
  created_at REAL,
  updated_at REAL
);
"""


def _now() -> float:
    return time.time()


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self.init()

    # ------------------------------------------------------------------ 连接
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=8000")
            self._local.conn = conn
        return conn

    def init(self) -> None:
        with self._init_lock:
            self._conn().executescript(SCHEMA)
            self._conn().commit()

    # ------------------------------------------------------------------ 基础
    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        conn = self._conn()
        with conn:
            cur = conn.execute(sql, tuple(params))
            return int(cur.lastrowid or 0)

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        rows = self._conn().execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[dict]:
        row = self._conn().execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ 设置
    def get_setting(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    # ------------------------------------------------------------------ 动作流水
    def log_action(self, kind: str, target: str = "", detail: Any = None,
                   ok: bool = True, error: str = "") -> int:
        return self.execute(
            "INSERT INTO actions(kind,target,detail,ok,error,created_at) VALUES(?,?,?,?,?,?)",
            (kind, str(target or ""), json.dumps(detail, ensure_ascii=False) if detail is not None else "",
             1 if ok else 0, str(error or ""), _now()),
        )

    def count_actions(self, kind: str, since: float) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS n FROM actions WHERE kind=? AND ok=1 AND created_at>=?",
            (kind, since),
        )
        return int(row["n"]) if row else 0

    def count_kinds(self, kinds: Iterable[str], since: float) -> int:
        kinds = list(kinds)
        if not kinds:
            return 0
        marks = ",".join("?" for _ in kinds)
        row = self.query_one(
            f"SELECT COUNT(*) AS n FROM actions WHERE ok=1 AND kind IN ({marks}) AND created_at>=?",
            (*kinds, since),
        )
        return int(row["n"]) if row else 0

    def last_action_ts(self, kinds: Iterable[str]) -> float:
        kinds = list(kinds)
        if not kinds:
            return 0.0
        marks = ",".join("?" for _ in kinds)
        row = self.query_one(
            f"SELECT MAX(created_at) AS t FROM actions WHERE ok=1 AND kind IN ({marks})",
            tuple(kinds),
        )
        return float(row["t"] or 0) if row else 0.0

    def recent_actions(self, limit: int = 20) -> list[dict]:
        return self.query("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (int(limit),))

    # ------------------------------------------------------------------ 视频
    def upsert_video(self, meta: dict) -> None:
        self.execute(
            """INSERT INTO videos(bvid,aid,cid,title,desc,tags,duration,owner_mid,owner_name,
                                  pubdate,fetched_at,meta)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(bvid) DO UPDATE SET
                 aid=excluded.aid, cid=excluded.cid, title=excluded.title, desc=excluded.desc,
                 tags=excluded.tags, duration=excluded.duration, owner_mid=excluded.owner_mid,
                 owner_name=excluded.owner_name, pubdate=excluded.pubdate,
                 fetched_at=excluded.fetched_at, meta=excluded.meta""",
            (meta.get("bvid", ""), int(meta.get("aid") or 0), int(meta.get("cid") or 0),
             meta.get("title", ""), meta.get("desc", ""),
             json.dumps(meta.get("tags") or [], ensure_ascii=False),
             int(meta.get("duration") or 0),
             int((meta.get("owner") or {}).get("mid") or 0),
             str((meta.get("owner") or {}).get("name") or ""),
             int(meta.get("pubdate") or 0), _now(),
             json.dumps(meta, ensure_ascii=False)),
        )

    def mark_seen(self, bvid: str, action: str = "view") -> None:
        now = _now()
        self.execute(
            """INSERT INTO seen_videos(bvid,first_seen,last_seen,action) VALUES(?,?,?,?)
               ON CONFLICT(bvid) DO UPDATE SET last_seen=excluded.last_seen, action=excluded.action""",
            (bvid, now, now, action),
        )

    def seen_bvids(self) -> set[str]:
        return {r["bvid"] for r in self.query("SELECT bvid FROM seen_videos")}

    def save_comments(self, bvid: str, aid: int, items: list[dict]) -> None:
        now = _now()
        for it in items:
            if not it.get("rpid"):
                continue
            self.execute(
                """INSERT INTO comments(rpid,bvid,aid,root,parent,mid,uname,message,like_count,ctime,fetched_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(rpid) DO UPDATE SET like_count=excluded.like_count, message=excluded.message""",
                (int(it["rpid"]), bvid, int(aid), int(it.get("root") or 0),
                 int(it.get("parent") or 0), int(it.get("mid") or 0), it.get("uname", ""),
                 it.get("message", ""), int(it.get("like") or 0), int(it.get("ctime") or 0), now),
            )

    # ------------------------------------------------------------------ 我们发的评论
    def add_our_comment(self, bvid: str, aid: int, rpid: int, root: int, parent: int,
                        message: str, at_uid: int = 0) -> None:
        self.execute(
            """INSERT INTO our_comments(bvid,aid,rpid,root,parent,message,at_uid,posted_at,status,visible)
               VALUES(?,?,?,?,?,?,?,?,?,1)
               ON CONFLICT(rpid) DO UPDATE SET message=excluded.message, at_uid=excluded.at_uid""",
            (bvid, int(aid), int(rpid), int(root), int(parent), message, int(at_uid), _now(), "posted"),
        )

    def recent_our_comments(self, hours: float = 48) -> list[dict]:
        return self.query(
            "SELECT * FROM our_comments WHERE posted_at>=? ORDER BY posted_at DESC",
            (_now() - hours * 3600,),
        )

    def update_our_comment(self, rpid: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE our_comments SET {cols} WHERE rpid=?", (*fields.values(), int(rpid)))

    def count_our_root(self, root: int) -> int:
        row = self.query_one("SELECT COUNT(*) AS n FROM our_comments WHERE root=?", (int(root),))
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------ 草稿
    def add_draft(self, kind: str, message: str, **kw: Any) -> int:
        alt = kw.get("alt", "")
        if not isinstance(alt, str):
            alt = json.dumps(alt, ensure_ascii=False)
        return self.execute(
            """INSERT INTO drafts(kind,bvid,aid,root,parent,message,alt,score,status,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (kind, kw.get("bvid", ""), int(kw.get("aid") or 0), int(kw.get("root") or 0),
             int(kw.get("parent") or 0), message, alt,
             json.dumps(kw.get("score"), ensure_ascii=False) if kw.get("score") is not None else "",
             kw.get("status", "pending"), _now()),
        )

    def list_drafts(self, status: Optional[str] = None, limit: int = 50) -> list[dict]:
        if status:
            return self.query("SELECT * FROM drafts WHERE status=? ORDER BY id DESC LIMIT ?",
                              (status, int(limit)))
        return self.query("SELECT * FROM drafts ORDER BY id DESC LIMIT ?", (int(limit),))

    def get_draft(self, draft_id: int) -> Optional[dict]:
        return self.query_one("SELECT * FROM drafts WHERE id=?", (int(draft_id),))

    def update_draft(self, draft_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE drafts SET {cols} WHERE id=?", (*fields.values(), int(draft_id)))

    # ------------------------------------------------------------------ 私信
    def upsert_thread(self, talker_id: int, uname: str, last_text: str, last_ts: int,
                      unread: int = 0) -> None:
        self.execute(
            """INSERT INTO dm_threads(talker_id,uname,last_text,last_ts,unread,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(talker_id) DO UPDATE SET uname=excluded.uname, last_text=excluded.last_text,
                 last_ts=excluded.last_ts, unread=excluded.unread, updated_at=excluded.updated_at""",
            (int(talker_id), uname, last_text, int(last_ts), int(unread), _now()),
        )

    def list_threads(self, limit: int = 30) -> list[dict]:
        return self.query("SELECT * FROM dm_threads ORDER BY last_ts DESC LIMIT ?", (int(limit),))

    def save_dm_message(self, talker_id: int, sender_uid: int, content: str, ts: int,
                        direction: str) -> None:
        key = f"{talker_id}:{sender_uid}:{ts}:{content[:80]}"
        self.execute(
            """INSERT OR IGNORE INTO dm_messages(talker_id,sender_uid,content,ts,direction,msg_key,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (int(talker_id), int(sender_uid), content, int(ts), direction, key, _now()),
        )

    def last_dm_ts(self, talker_id: int) -> int:
        row = self.query_one("SELECT MAX(ts) AS t FROM dm_messages WHERE talker_id=?", (int(talker_id),))
        return int(row["t"] or 0) if row else 0

    # ------------------------------------------------------------------ 回复去重
    def handled_reply(self, rpid: int) -> bool:
        return self.query_one("SELECT 1 FROM handled_replies WHERE rpid=?", (int(rpid),)) is not None

    def mark_handled_reply(self, rpid: int, parent: int) -> None:
        self.execute("INSERT OR IGNORE INTO handled_replies(rpid,parent_rpid,created_at) VALUES(?,?,?)",
                     (int(rpid), int(parent), _now()))

    # ------------------------------------------------------------------ 统计
    def today_start(self) -> float:
        """当天计数起点；支持控制台“重置今日计数”。"""
        midnight = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
        try:
            reset = float(self.get_setting("quota_reset_ts", "0") or 0)
        except (TypeError, ValueError):
            reset = 0.0
        if reset > midnight and reset <= time.time():
            return reset
        return midnight

    def stats(self) -> dict:
        start = self.today_start()
        return {
            "today_comment": self.count_actions("comment", start),
            "today_nested": self.count_actions("nested", start),
            "today_dm": self.count_actions("dm", start),
            "today_at": self.count_actions("at", start),
            "seen_videos": len(self.query("SELECT bvid FROM seen_videos")),
            "our_comments": int((self.query_one("SELECT COUNT(*) AS n FROM our_comments") or {}).get("n") or 0),
        }
