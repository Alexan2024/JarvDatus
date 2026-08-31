"""Хранилище: SQLite на диске Railway."""
import json
import os
import sqlite3
import threading
from datetime import date, datetime

from app import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        folder = os.path.dirname(config.DB_PATH)
        if folder:
            os.makedirs(folder, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def init() -> None:
    c = _connect()
    with _lock:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plan_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                kind TEXT NOT NULL,          -- task | event | carry
                title TEXT NOT NULL,
                note TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',  -- open|done|partial|dropped|moved
                ticktick_id TEXT DEFAULT '',
                ticktick_project TEXT DEFAULT '',
                position INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pinged (
                uid TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            """
        )
        c.commit()


# ---------- key-value ----------

def kv_get(key: str, default=None):
    c = _connect()
    with _lock:
        row = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return row["value"]


def kv_set(key: str, value) -> None:
    c = _connect()
    with _lock:
        c.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        c.commit()


def kv_del(key: str) -> None:
    c = _connect()
    with _lock:
        c.execute("DELETE FROM kv WHERE key=?", (key,))
        c.commit()


# ---------- история диалога ----------

def add_message(role: str, content: str) -> None:
    c = _connect()
    with _lock:
        c.execute(
            "INSERT INTO messages(role,content,created_at) VALUES(?,?,?)",
            (role, content, datetime.utcnow().isoformat()),
        )
        c.execute(
            "DELETE FROM messages WHERE id NOT IN "
            "(SELECT id FROM messages ORDER BY id DESC LIMIT 400)"
        )
        c.commit()


def history(limit: int = 20) -> list[dict]:
    c = _connect()
    with _lock:
        rows = c.execute(
            "SELECT role,content FROM messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def clear_history() -> None:
    c = _connect()
    with _lock:
        c.execute("DELETE FROM messages")
        c.commit()


# ---------- факты о владельце ----------

def add_fact(text: str) -> None:
    c = _connect()
    with _lock:
        c.execute(
            "INSERT INTO facts(text,created_at) VALUES(?,?)",
            (text, datetime.utcnow().isoformat()),
        )
        c.commit()


def facts() -> list[str]:
    c = _connect()
    with _lock:
        rows = c.execute("SELECT text FROM facts ORDER BY id").fetchall()
    return [r["text"] for r in rows]


def clear_facts() -> None:
    c = _connect()
    with _lock:
        c.execute("DELETE FROM facts")
        c.commit()


# ---------- план дня ----------

def save_plan(day: str, items: list[dict]) -> None:
    c = _connect()
    with _lock:
        c.execute("DELETE FROM plan_items WHERE day=?", (day,))
        for i, it in enumerate(items):
            c.execute(
                "INSERT INTO plan_items(day,kind,title,note,status,ticktick_id,"
                "ticktick_project,position) VALUES(?,?,?,?,?,?,?,?)",
                (
                    day,
                    it.get("kind", "task"),
                    it.get("title", ""),
                    it.get("note", ""),
                    it.get("status", "open"),
                    it.get("ticktick_id", ""),
                    it.get("ticktick_project", ""),
                    i,
                ),
            )
        c.commit()


def get_plan(day: str) -> list[dict]:
    c = _connect()
    with _lock:
        rows = c.execute(
            "SELECT * FROM plan_items WHERE day=? ORDER BY position, id", (day,)
        ).fetchall()
    return [dict(r) for r in rows]


def set_item_status(item_id: int, status: str, note: str | None = None) -> None:
    c = _connect()
    with _lock:
        if note is None:
            c.execute("UPDATE plan_items SET status=? WHERE id=?", (status, item_id))
        else:
            c.execute(
                "UPDATE plan_items SET status=?, note=? WHERE id=?",
                (status, note, item_id),
            )
        c.commit()


def carryover(day: str) -> list[dict]:
    """Незакрытые задачи прошлых дней."""
    c = _connect()
    with _lock:
        rows = c.execute(
            "SELECT * FROM plan_items WHERE day<? AND kind!='event' "
            "AND status IN ('open','partial','moved') ORDER BY day DESC, position LIMIT 10",
            (day,),
        ).fetchall()
    seen, out = set(), []
    for r in rows:
        t = r["title"].strip().lower()
        if t in seen:
            continue
        seen.add(t)
        out.append(dict(r))
    return out


def close_old_open(day: str) -> None:
    """После переноса помечаем старые записи как перенесённые."""
    c = _connect()
    with _lock:
        c.execute(
            "UPDATE plan_items SET status='moved' WHERE day<? AND status='open'", (day,)
        )
        c.commit()


# ---------- статистика ----------

def day_score(day: str) -> tuple[int, int]:
    c = _connect()
    with _lock:
        rows = c.execute(
            "SELECT status FROM plan_items WHERE day=? AND kind!='event'", (day,)
        ).fetchall()
    total = len(rows)
    done = sum(1 for r in rows if r["status"] == "done")
    return done, total


def streak(today: date) -> int:
    """Сколько дней подряд закрыто хотя бы половина задач."""
    from datetime import timedelta

    n, d = 0, today
    while True:
        done, total = day_score(d.isoformat())
        if total == 0 or done * 2 < total:
            break
        n += 1
        d = d - timedelta(days=1)
    return n


# ---------- защита от повторных пингов ----------

def was_pinged(uid: str) -> bool:
    c = _connect()
    with _lock:
        row = c.execute("SELECT 1 FROM pinged WHERE uid=?", (uid,)).fetchone()
    return row is not None


def mark_pinged(uid: str) -> None:
    c = _connect()
    with _lock:
        c.execute(
            "INSERT OR IGNORE INTO pinged(uid,created_at) VALUES(?,?)",
            (uid, datetime.utcnow().isoformat()),
        )
        c.execute(
            "DELETE FROM pinged WHERE uid NOT IN "
            "(SELECT uid FROM pinged ORDER BY created_at DESC LIMIT 300)"
        )
        c.commit()
