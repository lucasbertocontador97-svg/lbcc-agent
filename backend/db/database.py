"""
Banco de dados — SQLite via aiosqlite.
Tabelas: conversations, messages, action_logs, procedures
"""
import aiosqlite
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "lbcc.db"


async def get_db() -> aiosqlite.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    return db


async def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL DEFAULT 'Nova conversa',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id              TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id),
                role            TEXT NOT NULL,   -- user | assistant | system
                content         TEXT NOT NULL,
                meta            TEXT,            -- JSON: screenshots, files, etc
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS action_logs (
                id          TEXT PRIMARY KEY,
                conv_id     TEXT NOT NULL,
                action      TEXT NOT NULL,   -- navigate | click | fill | ...
                detail      TEXT,            -- JSON com params
                result      TEXT,            -- JSON com resultado
                ok          INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS procedures (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                steps       TEXT NOT NULL DEFAULT '[]',  -- JSON
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conv
                ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_logs_conv
                ON action_logs(conv_id);
        """)
        await db.commit()


def now() -> str:
    return datetime.utcnow().isoformat()


# ── Conversations ─────────────────────────────────────────────────────────────

async def create_conversation(conv_id: str, title: str = "Nova conversa") -> dict:
    t = now()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            "INSERT INTO conversations(id, title, created_at, updated_at) VALUES(?,?,?,?)",
            (conv_id, title, t, t)
        )
        await db.commit()
    return {"id": conv_id, "title": title, "created_at": t, "updated_at": t}


async def list_conversations() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 60"
        )
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def update_conversation_title(conv_id: str, title: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
            (title[:80], now(), conv_id)
        )
        await db.commit()


async def touch_conversation(conv_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?",
            (now(), conv_id)
        )
        await db.commit()


# ── Messages ──────────────────────────────────────────────────────────────────

async def save_message(msg_id: str, conv_id: str, role: str,
                       content: str, meta: dict = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO messages(id,conversation_id,role,content,meta,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (msg_id, conv_id, role, content,
             json.dumps(meta) if meta else None, now())
        )
        await db.commit()
    await touch_conversation(conv_id)


async def get_messages(conv_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at",
            (conv_id,)
        )
        rows = await cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d["meta"]) if d.get("meta") else {}
        result.append(d)
    return result


# ── Action Logs ───────────────────────────────────────────────────────────────

async def save_action_log(log_id: str, conv_id: str, action: str,
                          detail: dict, result: dict, ok: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO action_logs(id,conv_id,action,detail,result,ok,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (log_id, conv_id, action,
             json.dumps(detail), json.dumps(result),
             1 if ok else 0, now())
        )
        await db.commit()


async def get_action_logs(conv_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM action_logs WHERE conv_id=? ORDER BY created_at",
            (conv_id,)
        )
        rows = await cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d.get("detail") else {}
        d["result"] = json.loads(d["result"]) if d.get("result") else {}
        result.append(d)
    return result


# ── Procedures ────────────────────────────────────────────────────────────────

async def save_procedure(proc_id: str, name: str,
                         description: str, steps: list) -> dict:
    t = now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO procedures(id,name,description,steps,created_at,updated_at)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(name) DO UPDATE SET
                description=excluded.description,
                steps=excluded.steps,
                updated_at=excluded.updated_at
        """, (proc_id, name, description, json.dumps(steps), t, t))
        await db.commit()
    return {"id": proc_id, "name": name, "description": description,
            "steps": steps, "created_at": t, "updated_at": t}


async def list_procedures() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM procedures ORDER BY name"
        )
        rows = await cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["steps"] = json.loads(d["steps"])
        result.append(d)
    return result
