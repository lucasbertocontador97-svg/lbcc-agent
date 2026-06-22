"""
LBCC Agent — API principal (versão Replit)
FastAPI + WebSocket + serve frontend buildado na mesma porta.
"""
import json
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.agent.agent import agent
from backend.browser.browser import browser
from backend.db import database as db

DOWNLOADS_DIR = Path(__file__).parent.parent / "data" / "downloads"
FRONTEND_DIR  = Path(__file__).parent.parent.parent / "frontend" / "dist"


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    headless = os.getenv("BROWSER_HEADLESS", "true").lower() == "true"
    await browser.start(headless=headless)
    print(f"[api] LBCC Agent pronto. headless={headless}")
    yield
    await browser.stop()


app = FastAPI(title="LBCC Agent", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── WebSocket ─────────────────────────────────────────────────────────────────

class WsManager:
    def __init__(self):
        self._sockets: dict[str, WebSocket] = {}

    async def connect(self, sid: str, ws: WebSocket):
        await ws.accept()
        self._sockets[sid] = ws

    def disconnect(self, sid: str):
        self._sockets.pop(sid, None)

    async def send(self, sid: str, data: dict):
        ws = self._sockets.get(sid)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                pass


wsm = WsManager()


@app.websocket("/ws/{sid}")
async def websocket(ws: WebSocket, sid: str):
    await wsm.connect(sid, ws)
    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)

            if msg.get("type") == "chat":
                await _handle_chat(sid, msg)
            elif msg.get("type") == "screenshot":
                ss = await browser.screenshot()
                if ss["ok"]:
                    await wsm.send(sid, {"type": "screenshot", "b64": ss["b64"]})
            elif msg.get("type") == "ping":
                await wsm.send(sid, {"type": "pong"})

    except WebSocketDisconnect:
        wsm.disconnect(sid)
    except Exception as e:
        print(f"[ws] erro: {e}")
        wsm.disconnect(sid)


async def _handle_chat(sid: str, msg: dict):
    text    = msg.get("text", "").strip()
    conv_id = msg.get("conv_id", "")
    history = msg.get("history", [])

    if not text or not conv_id:
        return

    await db.save_message(str(uuid.uuid4()), conv_id, "user", text)

    if not history:
        await db.update_conversation_title(conv_id, text[:60])

    full_reply = ""
    async for event in agent.run(text, conv_id, history):
        await wsm.send(sid, event)
        if event["type"] in ("done", "error", "ask"):
            full_reply = event.get("text", "")

    if full_reply:
        await db.save_message(str(uuid.uuid4()), conv_id, "assistant", full_reply)


# ── REST ──────────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def status():
    return {"ok": True, "browser_url": browser.url}

@app.get("/api/conversations")
async def list_conversations():
    return await db.list_conversations()

@app.post("/api/conversations")
async def create_conversation(body: dict):
    cid = str(uuid.uuid4())
    return await db.create_conversation(cid, body.get("title", "Nova conversa"))

@app.get("/api/conversations/{cid}/messages")
async def get_messages(cid: str):
    return await db.get_messages(cid)

@app.get("/api/conversations/{cid}/logs")
async def get_logs(cid: str):
    return await db.get_action_logs(cid)

@app.get("/api/procedures")
async def list_procedures():
    return await db.list_procedures()

@app.get("/api/files")
async def list_files():
    if not DOWNLOADS_DIR.exists():
        return []
    files = []
    for f in sorted(DOWNLOADS_DIR.iterdir(), key=lambda x: -x.stat().st_mtime):
        if f.is_file():
            files.append({"name": f.name, "size": f.stat().st_size,
                          "url": f"/api/files/{f.name}"})
    return files

@app.get("/api/files/{name}")
async def download_file(name: str):
    path = DOWNLOADS_DIR / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(str(path), filename=name)


# ── Frontend — serve o build do React na raiz ─────────────────────────────────
if FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="assets")

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    # Rotas de API já foram capturadas acima
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text())
    return HTMLResponse("<h1>Frontend não buildado. Rode: cd frontend && npm run build</h1>", 500)
