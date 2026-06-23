"""
LBCC Agent — API principal. Fase 3.
"""
import json, os, uuid, time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.agent.agent import agent, TIMEOUT_SECONDS
from backend.browser.browser import (browser, VIDEOS_DIR, SCREENSHOTS_DIR,
                                      DOWNLOADS_DIR, ATTACHMENTS_DIR, LOGS_DIR)
from backend.db import database as db
from backend.procedures import manager as procs

FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend" / "dist"
CREDENTIALS_PATH = Path(os.getenv(
    "CREDENTIALS_FILE",
    str(Path(__file__).parent.parent / "data" / "credentials.json")
))


def _read_credentials_file() -> dict:
    if not CREDENTIALS_PATH.exists():
        return {"sites": {}}
    try:
        data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("sites"), dict):
            return data
    except Exception:
        pass
    return {"sites": {}}


def _write_credentials_file(data: dict):
    CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _safe_credential(alias: str, item: dict) -> dict:
    return {
        "alias": alias,
        "label": item.get("label", alias),
        "url": item.get("url", ""),
        "email": item.get("email", ""),
        "password_set": bool(item.get("password")),
        "aliases": item.get("aliases", []),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    procs.create_examples()
    headless = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"
    await browser.start(headless=headless)
    print(f"[api] LBCC Agent Fase 3 pronto. headless={headless}")
    yield
    await browser.stop()


app = FastAPI(title="LBCC Agent", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


# ── WebSocket ──────────────────────────────────────────────────────────────────

class WsManager:
    def __init__(self):
        self._s: dict[str, WebSocket] = {}

    async def connect(self, sid: str, ws: WebSocket):
        await ws.accept()
        self._s[sid] = ws

    def disconnect(self, sid: str):
        self._s.pop(sid, None)

    async def send(self, sid: str, data: dict):
        ws = self._s.get(sid)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                pass


wsm = WsManager()


@app.websocket("/ws/{sid}")
async def websocket(ws: WebSocket, sid: str):
    await wsm.connect(sid, ws)

    # Notificar downloads em tempo real
    async def on_download(info: dict):
        await wsm.send(sid, {
            "type": "download",
            "filename": info["filename"],
            "path": info["path"],
            "size": info["size"],
            "url": info["url"],
            "text": f"📥 Download salvo: {info['filename']} ({info['size']//1024}KB) → Arquivos > Downloads",
        })
    browser._download_callbacks.append(on_download)

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            t   = msg.get("type")

            if t == "chat":
                await _handle_chat(sid, msg)
            elif t == "stop":
                browser.request_stop()
                await wsm.send(sid, {"type": "system", "text": "⏹ Parada solicitada."})
            elif t == "pause":
                browser.pause()
                await wsm.send(sid, {"type": "paused", "text": "⏸ Pausado."})
            elif t == "resume":
                browser.resume()
                await wsm.send(sid, {"type": "resumed", "text": "▶ Retomado."})
            elif t == "next_step":
                browser.next_step()
                await wsm.send(sid, {"type": "system", "text": "→ Próximo passo."})
            elif t == "step_mode_on":
                browser.enable_step_mode()
                await wsm.send(sid, {"type": "system", "text": "🔢 Modo passo a passo ON."})
            elif t == "step_mode_off":
                browser.disable_step_mode()
                await wsm.send(sid, {"type": "system", "text": "🔢 Modo passo a passo OFF."})
            elif t == "approve":
                browser.approve()
                await wsm.send(sid, {"type": "system", "text": "✅ Aprovado."})
            elif t == "reject":
                browser.reject()
                browser.request_stop()
                await wsm.send(sid, {"type": "system", "text": "❌ Rejeitado."})
            elif t == "manual_on":
                browser.set_manual_mode(True)
                await wsm.send(sid, {"type": "system", "text": "🖐 Modo manual ON."})
            elif t == "manual_off":
                browser.set_manual_mode(False)
                await wsm.send(sid, {"type": "system", "text": "🤖 Agente retomou controle."})
            elif t == "teach_start":
                result = await browser.start_teaching(
                    msg.get("name") or "procedimento_ensinado",
                    msg.get("description", ""),
                )
                await wsm.send(sid, {
                    "type": "teach_status",
                    "teaching": browser.teaching_status(),
                    "text": f"Modo ensinar ativo: {result.get('name')}",
                })
            elif t == "teach_stop":
                result = await browser.stop_teaching()
                await wsm.send(sid, {
                    "type": "teach_status",
                    "teaching": browser.teaching_status(),
                    "text": f"Procedimento salvo: {result.get('procedure', {}).get('name', '')}"
                            if result.get("ok") else result.get("error", "Modo ensinar encerrado."),
                    "result": result,
                })
            elif t == "teach_status":
                await wsm.send(sid, {
                    "type": "teach_status",
                    "teaching": browser.teaching_status(),
                })
            elif t == "screenshot":
                ss = await browser.screenshot("manual")
                if ss.get("ok") and ss.get("b64"):
                    await wsm.send(sid, {"type": "screenshot", "b64": ss["b64"], "label": "Manual"})
            elif t == "list_tabs":
                tabs = await browser.list_tabs()
                await wsm.send(sid, {"type": "tabs", "tabs": tabs})
            elif t == "switch_tab":
                result = await browser.switch_tab(msg.get("index", 0))
                await wsm.send(sid, {"type": "system",
                                      "text": f"Aba {result.get('index')} ativa: {result.get('title','')}"})
            elif t == "new_tab":
                result = await browser.new_tab(msg.get("url", ""))
                await wsm.send(sid, {"type": "system",
                                      "text": f"Nova aba aberta (índice {result.get('index')})"})
            elif t == "close_tab":
                result = await browser.close_tab(msg.get("index"))
                await wsm.send(sid, {"type": "system",
                                      "text": f"Aba fechada. Ativa: {result.get('active_index')}"})
            elif t == "ping":
                await wsm.send(sid, {"type": "pong"})

    except WebSocketDisconnect:
        wsm.disconnect(sid)
    except Exception as e:
        print(f"[ws] erro: {e}")
        wsm.disconnect(sid)
    finally:
        # Remover callback de download
        try:
            browser._download_callbacks.remove(on_download)
        except Exception:
            pass


async def _handle_chat(sid: str, msg: dict):
    text      = msg.get("text", "").strip()
    conv_id   = msg.get("conv_id", "")
    history   = msg.get("history", [])
    variables = msg.get("variables", {})

    if not text or not conv_id:
        return

    if browser.is_manual_mode:
        await wsm.send(sid, {"type": "system",
                              "text": "🖐 Modo manual ativo. Desative para enviar comandos."})
        return

    exec_id = str(uuid.uuid4())
    t_start = time.time()
    await db.create_execution(exec_id, conv_id, text)
    browser.begin_execution(exec_id)

    await db.save_message(str(uuid.uuid4()), conv_id, "user", text)
    if not history:
        await db.update_conversation_title(conv_id, text[:60])

    await wsm.send(sid, {"type": "exec_start", "exec_id": exec_id})

    full_reply   = ""
    final_status = "completed"
    retries      = 0

    async for event in agent.run(text, conv_id, exec_id, history, variables):
        await wsm.send(sid, event)
        if event["type"] == "retry":
            retries += 1
        if event["type"] in ("done", "error", "ask", "stopped", "timeout"):
            full_reply = event.get("text", "")
            if event["type"] != "done":
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
    tabs = await browser.list_tabs()
    return {
        "ok": True,
        "browser_url":      browser.url,
        "browser_profile":  getattr(browser, "_profile_name", "default"),
        "browser_profile_dir": str(browser.profile_dir),
        "manual_mode":      browser.is_manual_mode,
        "paused":           browser.is_paused,
        "step_mode":        browser.is_step_mode,
        "teaching":         browser.teaching_status(),
        "approval_pending": browser.approval_pending,
        "approval_message": browser.approval_message,
        "timeout_seconds":  TIMEOUT_SECONDS,
        "tabs":             tabs,
        "active_tab":       browser._active_idx,
        "downloads":        len(browser.list_downloads()),
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

@app.get("/api/executions")
async def list_executions():
    return await db.list_executions()

@app.get("/api/executions/{exec_id}")
async def get_execution(exec_id: str):
    ex = await db.get_execution(exec_id)
    if not ex: raise HTTPException(404)
    return ex

@app.get("/api/executions/{exec_id}/logs")
async def get_execution_logs(exec_id: str):
    return await db.get_logs_by_exec(exec_id)

@app.get("/api/executions/{exec_id}/logs/export")
async def export_logs(exec_id: str):
    import tempfile
    logs = await db.get_logs_by_exec(exec_id)
    ex   = await db.get_execution(exec_id)
    tmp  = Path(tempfile.mktemp(suffix=".json"))
    tmp.write_text(json.dumps({"execution": ex, "logs": logs}, ensure_ascii=False, indent=2))
    return FileResponse(str(tmp), filename=f"exec_{exec_id[:8]}_logs.json",
                        media_type="application/json")

# ── Abas ──────────────────────────────────────────────────────────────────────

@app.get("/api/tabs")
async def list_tabs():
    return await browser.list_tabs()

@app.post("/api/tabs")
async def new_tab(body: dict):
    return await browser.new_tab(body.get("url", ""))

@app.put("/api/tabs/{index}")
async def switch_tab(index: int):
    return await browser.switch_tab(index)

@app.delete("/api/tabs/{index}")
async def close_tab(index: int):
    return await browser.close_tab(index)

# ── Procedimentos ──────────────────────────────────────────────────────────────

@app.get("/api/procedures")
async def list_procedures():
    return procs.list_procedures()

@app.get("/api/procedures/{name}")
async def get_procedure(name: str):
    p = procs.get_procedure(name)
    if not p: raise HTTPException(404)
    return p

@app.post("/api/procedures")
async def create_procedure(body: dict):
    return procs.save_procedure(body["name"], body.get("description",""),
                                body.get("steps",[]), body.get("variables",[]))

@app.put("/api/procedures/{name}")
async def update_procedure(name: str, body: dict):
    existing = procs.get_procedure(name)
    if not existing: raise HTTPException(404)
    return procs.save_procedure(
        body.get("name", name), body.get("description", existing.get("description","")),
        body.get("steps", existing.get("steps",[])),
        body.get("variables", existing.get("variables",[])), existing.get("id"))

@app.delete("/api/procedures/{name}")
async def delete_procedure(name: str):
    if not procs.delete_procedure(name): raise HTTPException(404)
    return {"deleted": True}

# ── Anexos ─────────────────────────────────────────────────────────────────────

@app.get("/api/credentials")
async def list_credentials():
    data = _read_credentials_file()
    sites = data.get("sites", {})
    return {
        "path": str(CREDENTIALS_PATH),
        "credentials": [
            _safe_credential(alias, item)
            for alias, item in sorted(sites.items())
            if isinstance(item, dict)
        ],
    }

@app.post("/api/credentials")
async def save_credential(body: dict):
    alias = "".join(
        c for c in body.get("alias", "").lower().strip()
        if c.isalnum() or c in ("-", "_", ".")
    )
    if not alias:
        raise HTTPException(400, "alias obrigatorio")

    data = _read_credentials_file()
    sites = data.setdefault("sites", {})
    existing = sites.get(alias, {}) if isinstance(sites.get(alias, {}), dict) else {}

    aliases = body.get("aliases", existing.get("aliases", []))
    if isinstance(aliases, str):
        aliases = [a.strip() for a in aliases.split(",") if a.strip()]
    if not isinstance(aliases, list):
        aliases = []

    password = body.get("password", "")
    sites[alias] = {
        "label": body.get("label", existing.get("label", alias)),
        "url": body.get("url", existing.get("url", "")),
        "email": body.get("email", existing.get("email", "")),
        "password": password if password else existing.get("password", ""),
        "aliases": aliases,
    }
    _write_credentials_file(data)
    return _safe_credential(alias, sites[alias])

@app.delete("/api/credentials/{alias}")
async def delete_credential(alias: str):
    data = _read_credentials_file()
    sites = data.setdefault("sites", {})
    if alias not in sites:
        raise HTTPException(404)
    del sites[alias]
    _write_credentials_file(data)
    return {"deleted": True}

@app.post("/api/teach/start")
async def teach_start(body: dict):
    return await browser.start_teaching(
        body.get("name") or "procedimento_ensinado",
        body.get("description", ""),
    )

@app.post("/api/teach/stop")
async def teach_stop():
    return await browser.stop_teaching()

@app.get("/api/teach/status")
async def teach_status():
    return browser.teaching_status()

@app.post("/api/control/stop")
async def control_stop():
    browser.reject()
    browser.request_stop()
    browser.resume()
    browser.disable_step_mode()
    return {
        "ok": True,
        "stopped": True,
        "approval_pending": browser.approval_pending,
        "manual_mode": browser.is_manual_mode,
        "teaching": browser.teaching_status(),
    }

@app.get("/api/attachments")
async def list_attachments():
    return browser.list_attachments()

@app.post("/api/attachments")
async def upload_attachment(file: UploadFile = File(...)):
    dest = ATTACHMENTS_DIR / file.filename
    content = await file.read()
    dest.write_bytes(content)
    return {"filename": file.filename, "size": len(content)}

@app.delete("/api/attachments/{name}")
async def delete_attachment(name: str):
    path = ATTACHMENTS_DIR / name
    if not path.exists(): raise HTTPException(404)
    path.unlink()
    return {"deleted": True}

# ── Mídia e arquivos ───────────────────────────────────────────────────────────

@app.get("/api/screenshots")
async def list_screenshots():
    return browser.list_screenshots()

@app.get("/api/screenshots/{name}")
async def get_screenshot(name: str):
    path = SCREENSHOTS_DIR / name
    if not path.exists(): raise HTTPException(404)
    return FileResponse(str(path), media_type="image/jpeg")

@app.get("/api/videos")
async def list_videos():
    return browser.list_videos()

@app.get("/api/videos/{name}")
async def get_video(name: str):
    path = VIDEOS_DIR / name
    if not path.exists(): raise HTTPException(404)
    return FileResponse(str(path), media_type="video/webm")

@app.get("/api/files")
async def list_files():
    return browser.list_downloads()

@app.get("/api/files/{name}")
async def download_file(name: str):
    path = DOWNLOADS_DIR / name
    if not path.exists(): raise HTTPException(404)
    return FileResponse(str(path), filename=name)

@app.get("/api/logs")
async def list_logs():
    return browser.list_logs()

@app.get("/api/logs/{name}")
async def get_log(name: str):
    path = LOGS_DIR / name
    if not path.exists(): raise HTTPException(404)
    return FileResponse(str(path), media_type="text/plain")

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
    return HTMLResponse("<h1>Frontend não encontrado.</h1>", 500)
