"""
LBCC Agent — API principal. Fase 1.1
"""
import json, os, uuid, time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.agent.agent import agent, TIMEOUT_SECONDS
from backend.browser.browser import browser, VIDEOS_DIR, SCREENSHOTS_DIR, DOWNLOADS_DIR
from backend.db import database as db

FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    headless = os.getenv("BROWSER_HEADLESS", "true").lower() == "true"
    await browser.start(headless=headless)
    print(f"[api] LBCC Agent pronto. headless={headless}")
    yield
    await browser.stop()


app = FastAPI(title="LBCC Agent", version="1.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


# ── WebSocket Manager ──────────────────────────────────────────────────────────

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
            t   = msg.get("type")

            if t == "chat":
                await _handle_chat(sid, msg)
            elif t == "stop":
                browser.request_stop()
                await wsm.send(sid, {"type": "system", "text": "Sinal de parada enviado..."})
            elif t == "manual_on":
                browser.set_manual_mode(True)
                await wsm.send(sid, {"type": "system", "text": "Modo manual ativado. Você controla o navegador."})
            elif t == "manual_off":
                browser.set_manual_mode(False)
                await wsm.send(sid, {"type": "system", "text": "Controle devolvido ao agente."})
            elif t == "screenshot":
                ss = await browser.screenshot("manual")
                if ss.get("ok") and ss.get("b64"):
                    await wsm.send(sid, {"type": "screenshot", "b64": ss.get("b64", ""), "label": "Manual"})
            elif t == "ping":
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

    if browser.is_manual_mode:
        await wsm.send(sid, {"type": "system",
                              "text": "Modo manual ativo. Desative antes de enviar comandos."})
        return

    # Criar execução
    exec_id = str(uuid.uuid4())
    t_start = time.time()
    await db.create_execution(exec_id, conv_id, text)
    browser.begin_execution(exec_id)

    await db.save_message(str(uuid.uuid4()), conv_id, "user", text)
    if not history:
        await db.update_conversation_title(conv_id, text[:60])

    # Informar o frontend do exec_id
    await wsm.send(sid, {"type": "exec_start", "exec_id": exec_id})

    full_reply = ""
    final_status = "completed"
    retries = 0

    async for event in agent.run(text, conv_id, exec_id, history):
        await wsm.send(sid, event)

        if event["type"] == "retry":
            retries += 1
        if event["type"] in ("done", "error", "ask", "stopped", "timeout"):
            full_reply = event.get("text", "")
            if event["type"] in ("error", "stopped", "timeout"):
                final_status = event["type"]

    duration_ms = int((time.time() - t_start) * 1000)
    await db.finish_execution(exec_id, final_status, duration_ms, retries,
                              full_reply if final_status != "completed" else None)
    browser.end_execution()

    await wsm.send(sid, {"type": "exec_end", "exec_id": exec_id,
                          "status": final_status, "duration_ms": duration_ms})

    if full_reply:
        await db.save_message(str(uuid.uuid4()), conv_id, "assistant", full_reply)


# ── REST ───────────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def status():
    return {
        "ok": True,
        "browser_url": browser.url,
        "manual_mode": browser.is_manual_mode,
        "timeout_seconds": TIMEOUT_SECONDS,
    }

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

# ── Execuções ──────────────────────────────────────────────────────────────────

@app.get("/api/executions")
async def list_executions():
    return await db.list_executions()

@app.get("/api/executions/{exec_id}")
async def get_execution(exec_id: str):
    ex = await db.get_execution(exec_id)
    if not ex:
        raise HTTPException(404)
    return ex

@app.get("/api/executions/{exec_id}/logs")
async def get_execution_logs(exec_id: str):
    logs = await db.get_logs_by_exec(exec_id)
    return logs

@app.get("/api/executions/{exec_id}/logs/export")
async def export_logs_json(exec_id: str):
    """Exporta logs de uma execução como JSON."""
    import tempfile, aiofiles
    logs = await db.get_logs_by_exec(exec_id)
    ex   = await db.get_execution(exec_id)
    data = {"execution": ex, "logs": logs}
    tmp  = Path(tempfile.mktemp(suffix=".json"))
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return FileResponse(str(tmp), filename=f"exec_{exec_id[:8]}_logs.json",
                        media_type="application/json")

# ── Mídia ──────────────────────────────────────────────────────────────────────

@app.get("/api/screenshots")
async def list_screenshots():
    return browser.list_screenshots()

@app.get("/api/screenshots/{name}")
async def get_screenshot(name: str):
    path = SCREENSHOTS_DIR / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(str(path), media_type="image/jpeg")

@app.get("/api/videos")
async def list_videos():
    return browser.list_videos()

@app.get("/api/videos/{name}")
async def get_video(name: str):
    path = VIDEOS_DIR / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(str(path), media_type="video/webm")

@app.get("/api/files")
async def list_files():
    if not DOWNLOADS_DIR.exists():
        return []
    return [{"name": f.name, "size": f.stat().st_size, "url": f"/api/files/{f.name}"}
            for f in sorted(DOWNLOADS_DIR.iterdir(), key=lambda x: -x.stat().st_mtime)
            if f.is_file()]

@app.get("/api/files/{name}")
async def download_file(name: str):
    path = DOWNLOADS_DIR / name
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(str(path), filename=name)

@app.get("/api/procedures")
async def list_procedures():
    return await db.list_procedures()

# ── Frontend ───────────────────────────────────────────────────────────────────

if FRONTEND_DIR.exists():
    assets = FRONTEND_DIR / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

@app.get("/{full_path:path}")
async def serve_frontend(full_path: str):
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text())
    return HTMLResponse("<h1>Build o frontend: cd frontend && npm run build</h1>", 500)
