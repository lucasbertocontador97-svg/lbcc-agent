"""
Banco de dados — SQLite.
Fase 1.1: tabela executions + coluna exec_id em action_logs
"""
import aiosqlite
import json
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "lbcc.db"


async def init_db():
    DB_PATH.parent.mkdir(exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        # Criar tabelas novas
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL DEFAULT 'Nova conversa',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id              TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL,
                meta            TEXT,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS action_logs (
                id          TEXT PRIMARY KEY,
                conv_id     TEXT NOT NULL,
                exec_id     TEXT,
                action      TEXT NOT NULL,
                detail      TEXT,
                result      TEXT,
                ok          INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS procedures (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                steps       TEXT NOT NULL DEFAULT '[]',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS executions (
                id          TEXT PRIMARY KEY,
                conv_id     TEXT NOT NULL,
                command     TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'running',
                started_at  TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER,
                retries     INTEGER DEFAULT 0,
                error       TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conv   ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_logs_conv       ON action_logs(conv_id);
            CREATE INDEX IF NOT EXISTS idx_executions_conv ON executions(conv_id);
        """)

        # Migração segura: adicionar exec_id se não existir
        try:
            await db.execute("ALTER TABLE action_logs ADD COLUMN exec_id TEXT")
        except Exception:
            pass  # Coluna já existe

        await db.commit()


def now() -> str:
    return datetime.utcnow().isoformat()


# ── Conversations ──────────────────────────────────────────────────────────────

async def create_conversation(conv_id: str, title: str = "Nova conversa") -> dict:
    t = now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)",
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
        return [dict(r) for r in await cur.fetchall()]


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
            "UPDATE conversations SET updated_at=? WHERE id=?", (now(), conv_id)
        )
        await db.commit()


# ── Messages ───────────────────────────────────────────────────────────────────

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
        result = []
        for r in await cur.fetchall():
            d = dict(r)
            d["meta"] = json.loads(d["meta"]) if d.get("meta") else {}
            result.append(d)
    return result


# ── Action Logs ────────────────────────────────────────────────────────────────

async def save_action_log(log_id: str, conv_id: str, exec_id: str,
                          action: str, detail: dict, result: dict, ok: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO action_logs(id,conv_id,exec_id,action,detail,result,ok,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (log_id, conv_id, exec_id, action,
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
        result = []
        for r in await cur.fetchall():
            d = dict(r)
            d["detail"] = json.loads(d["detail"]) if d.get("detail") else {}
            d["result"] = json.loads(d["result"]) if d.get("result") else {}
            result.append(d)
    return result


async def get_logs_by_exec(exec_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM action_logs WHERE exec_id=? ORDER BY created_at",
            (exec_id,)
        )
        result = []
        for r in await cur.fetchall():
            d = dict(r)
            d["detail"] = json.loads(d["detail"]) if d.get("detail") else {}
            d["result"] = json.loads(d["result"]) if d.get("result") else {}
            result.append(d)
    return result


# ── Executions ─────────────────────────────────────────────────────────────────

async def create_execution(exec_id: str, conv_id: str, command: str) -> dict:
    t = now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO executions(id,conv_id,command,status,started_at)"
            " VALUES(?,?,?,?,?)",
            (exec_id, conv_id, command, "running", t)
        )
        await db.commit()
    return {"id": exec_id, "conv_id": conv_id, "command": command,
            "status": "running", "started_at": t}


async def finish_execution(exec_id: str, status: str,
                           duration_ms: int, retries: int = 0, error: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE executions SET status=?, finished_at=?, duration_ms=?,"
            " retries=?, error=? WHERE id=?",
            (status, now(), duration_ms, retries, error, exec_id)
        )
        await db.commit()


async def list_executions(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM executions ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_execution(exec_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM executions WHERE id=?", (exec_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


# ── Procedures ─────────────────────────────────────────────────────────────────

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
        cur = await db.execute("SELECT * FROM procedures ORDER BY name")
        result = []
        for r in await cur.fetchall():
            d = dict(r)
            d["steps"] = json.loads(d["steps"])
            result.append(d)
    return result
