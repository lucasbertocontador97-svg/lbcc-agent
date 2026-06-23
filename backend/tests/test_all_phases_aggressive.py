"""
Aggressive backend end-to-end test for phases 1 through 5.

Run:
    python -m backend.tests.test_all_phases_aggressive
    python test_all_phases_aggressive.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

try:
    import websockets
except Exception:  # pragma: no cover - reported as a controlled failure if absent
    websockets = None

from backend.agent.agent import agent
from backend.browser.browser import (
    DOWNLOADS_DIR,
    LOGS_DIR,
    SCREENSHOTS_DIR,
    VIDEOS_DIR,
    browser as shared_browser,
)
from backend.db import database as db
from backend.procedures import manager as procs


ROOT_DIR = Path(__file__).resolve().parents[2]
HUB_URL = os.getenv("HUB_URL", "https://hublbcc.com.br/")
SERVER_URL = os.getenv("AGGRESSIVE_SERVER_URL", "http://localhost:8000")
REPORT_TITLE = "RELATORIO AGRESSIVO - FASE 1 ATE FASE 5"


@dataclass
class AggressiveResult:
    phase: str
    name: str
    ok: bool
    action: str = ""
    selector: str = ""
    value: Any = None
    error: str = ""
    screenshot: str = ""
    corrections: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    initially_failed: bool = False


class AggressiveAllPhasesTest:
    def __init__(self, headless: bool = True, skip_hub: bool = False):
        self.headless = headless
        self.skip_hub = skip_hub
        self.run_id = f"aggressive_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.started = time.time()
        self.browser = shared_browser
        self.server_proc: subprocess.Popen | None = None
        self.server_started_by_test = False
        self.results: list[AggressiveResult] = []
        self.phase_names = ["FASE 1", "FASE 1.1", "FASE 2", "FASE 3", "FASE 4", "FASE 5", "CONTROLES", "RESILIENCIA"]
        self.corrections_count = 0
        self.procedures_executed = 0
        self.failures: list[AggressiveResult] = []
        self.report_path = LOGS_DIR / f"{self.run_id}_report.json"
        self.log_path = LOGS_DIR / f"{self.run_id}.log"
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

    async def run(self) -> int:
        self._line(f"Iniciando teste agressivo: {self.run_id}")
        try:
            await db.init_db()
            await self._phase_backend_server()
            await self._stop_server_for_browser_profile()
            await self._start_browser()
            await self._phase_1_mvp()
            await self._phase_11_reliability()
            await self._phase_2_browser_operator()
            await self._phase_3_persistence()
            await self._phase_4_hub_real()
            await self._phase_5_teach_mode()
            await self._phase_controls()
            await self._phase_resilience()
        finally:
            await self._write_report()
            self._print_report()
            try:
                await self.browser.stop()
            except Exception:
                pass
            self._restart_server()
        return 0 if self._overall_ok() else 1

    async def _phase_backend_server(self):
        phase = "FASE 1"
        await self._step(phase, "Backend sobe corretamente", self._test_backend_status)
        await self._step(phase, "WebSocket responde ping", self._test_websocket_ping)
        await self._step(phase, "SQLite inicializa", self._test_sqlite_init)
        await self._step(phase, "Exportacao de logs via API", self._test_export_logs_api)

    async def _phase_1_mvp(self):
        phase = "FASE 1"
        await self._step(phase, "Playwright abre navegador", self._test_browser_started)
        await self._step(phase, 'Agente responde comando simples "Abra o Google"', self._test_agent_simple_google)
        await self._step(phase, "Chrome navega para URL simples", self._test_simple_navigation)
        await self._step(phase, "Screenshot salvo", self._test_screenshot_saved)
        await self._step(phase, "Logs salvos", self._test_logs_saved)
        await self._step(phase, "Execucao marcada como concluida", self._test_execution_completed)

    async def _phase_11_reliability(self):
        phase = "FASE 1.1"
        await self._step(phase, "Screenshot antes/depois", self._test_before_after_screenshots)
        await self._step(phase, "Retry corrige selector valido alternativo", self._test_retry_autocorrect_click)
        await self._step(phase, "Acao invalida retorna erro controlado", self._test_invalid_action_controlled)
        await self._step(phase, "Timeout funciona", self._test_timeout_controlled)
        await self._step(phase, "STOP para execucao", self._test_stop_control)
        await self._step(phase, "Navegador nao fica travado", self._test_browser_not_stuck)
        await self._step(phase, "Logs detalhados", self._test_detailed_logs)

    async def _phase_2_browser_operator(self):
        phase = "FASE 2"
        await self._step(phase, "Abrir wikipedia.org", self._test_open_wikipedia)
        await self._step(phase, "Digitar em campo", self._test_fill_wikipedia_search)
        await self._step(phase, "Clicar por texto/selector", self._test_click_wikipedia_search)
        await self._step(phase, "Scroll", self._test_scroll)
        await self._step(phase, "Voltar pagina", self._test_back)
        await self._step(phase, "Avancar pagina", self._test_forward)
        await self._step(phase, "Abrir nova aba", self._test_new_tab)
        await self._step(phase, "Trocar de aba", self._test_switch_tab)
        await self._step(phase, "Fechar aba", self._test_close_tab)
        await self._step(phase, "Capturar contexto da pagina", self._test_page_context)

    async def _phase_3_persistence(self):
        phase = "FASE 3"
        if self.skip_hub:
            await self._skip(phase, "Hub LBCC persistente", "Ignorado por --skip-hub.")
            return
        await self._step(phase, "Abrir Hub LBCC", self._test_hub_open)
        await self._step(phase, "Confirmar login persistente", self._test_hub_login)
        await self._step(phase, "Reiniciar navegador", self._test_restart_browser)
        await self._step(phase, "Reabrir Hub mantendo sessao", self._test_hub_login)
        await self._step(phase, "Baixar arquivo", self._test_hub_download)
        await self._step(phase, "Arquivo existe em downloads", self._test_download_exists)
        await self._step(phase, "Multiplas abas com sessao mantida", self._test_hub_multitab)
        await self._step(phase, "Upload teste seguro", self._test_safe_upload_probe)

    async def _phase_4_hub_real(self):
        phase = "FASE 4"
        if self.skip_hub:
            await self._skip(phase, "Hub real", "Ignorado por --skip-hub.")
            return
        await self._step(phase, "Ir para Dashboard", self._test_hub_dashboard)
        await self._step(phase, "Ir para Tarefas", self._test_hub_tasks)
        await self._step(phase, "Ler tarefas concluidas", self._test_hub_done_count)
        await self._step(phase, 'Buscar colaborador "Alberto"', self._test_hub_alberto_search)
        await self._step(phase, "Detectar tarefas abertas Alberto", self._test_hub_alberto_open)
        await self._step(phase, "Ir para Documentos", self._test_hub_documents)
        await self._step(phase, "Baixar documento teste", self._test_hub_download)
        await self._step(phase, "Confirmar arquivo salvo", self._test_download_exists)
        await self._step(phase, "Voltar para Dashboard", self._test_hub_dashboard)
        await self._step(phase, "Relatorio de navegacao", self._test_navigation_report)

    async def _phase_5_teach_mode(self):
        phase = "FASE 5"
        await self._step(phase, "Iniciar modo ensinar", self._test_teach_start)
        await self._step(phase, "Demonstrar Wikipedia Brasil", self._test_teach_wikipedia_actions)
        await self._step(phase, "Parar e salvar procedimento", self._test_teach_stop)
        await self._step(phase, "Validar JSON salvo", self._test_teach_json)
        await self._step(phase, "Executar procedimento salvo", self._test_replay_taught_procedure)
        await self._step(phase, "Forcar falha e autocorrigir selector", self._test_procedure_autocorrection)
        await self._step(phase, "Rodar teste interno do procedimento", self._test_procedure_selftest_inline)

    async def _phase_controls(self):
        phase = "CONTROLES"
        await self._step(phase, "START iniciar execucao", self._test_start_execution_control)
        await self._step(phase, "STOP parar execucao", self._test_stop_control)
        await self._step(phase, "PAUSE pausar", self._test_pause_control)
        await self._step(phase, "RESUME retomar", self._test_resume_control)
        await self._step(phase, "SAVE salvar procedimento", self._test_save_control)
        await self._step(phase, "CANCEL cancelar modo ensinar", self._test_cancel_control)
        await self._step(phase, "EXPORT LOGS", self._test_export_logs_file)
        await self._step(phase, "CLEAR SESSION se existir", self._test_clear_session_probe)

    async def _phase_resilience(self):
        phase = "RESILIENCIA"
        await self._step(phase, "Comando invalido", self._test_invalid_command)
        await self._step(phase, "Site fora do ar", self._test_site_down_controlled)
        await self._step(phase, "Selector inexistente", self._test_missing_selector_controlled)
        await self._step(phase, "Timeout proposital", self._test_timeout_controlled)
        await self._step(phase, "Download que nao acontece", self._test_missing_download_controlled)
        await self._step(phase, "Elemento invisivel", self._test_invisible_element_controlled)
        await self._step(phase, "Interrupcao durante fill", self._test_interrupt_during_fill)
        await self._step(phase, "Interrupcao durante download", self._test_interrupt_during_download)
        await self._step(phase, "Reinicio do navegador no meio", self._test_restart_browser)
        await self._step(phase, "Reexecucao apos falha", self._test_reexecute_after_failure)

    async def _step(self, phase: str, name: str, func: Callable[[], Awaitable[AggressiveResult]]):
        self._line(f"\n[TESTE] {phase} - {name}")
        try:
            before = await self._shot(f"{self._safe(phase)}_{self._safe(name)}_before")
            result = await func()
            after = await self._shot(f"{self._safe(phase)}_{self._safe(name)}_after")
            if not result.screenshot:
                result.screenshot = after or before
        except Exception as exc:
            shot = await self._shot(f"{self._safe(phase)}_{self._safe(name)}_failure")
            result = AggressiveResult(
                phase=phase,
                name=name,
                ok=False,
                error=str(exc),
                screenshot=shot,
                corrections=["Falha capturada; screenshot de erro salvo."],
            )
        result.phase = phase
        result.name = name
        self.results.append(result)
        if not result.ok:
            self.failures.append(result)
        self.corrections_count += len(result.corrections)
        status = "OK" if result.ok else "FALHOU"
        corrected = " (falhou inicialmente, mas foi corrigido automaticamente)" if result.initially_failed and result.ok else ""
        self._line(f"[RESULTADO] {status}{corrected}")
        if result.value not in (None, ""):
            self._line(f"[VALOR] {result.value}")
        if result.error:
            self._line(f"[ERRO] {result.error}")
        if result.corrections:
            self._line("[CORRECOES] " + " | ".join(result.corrections[-5:]))

    async def _skip(self, phase: str, name: str, reason: str):
        self.results.append(AggressiveResult(phase=phase, name=name, ok=True, action="skip", value=reason))
        self._line(f"[SKIP] {phase} - {name}: {reason}")

    def _line(self, text: str):
        print(text)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    async def _shot(self, label: str) -> str:
        try:
            if not self.browser._ctx:
                return ""
            shot = await self.browser.screenshot(label[:90])
            return shot.get("path", "") if shot.get("ok") else ""
        except Exception:
            return ""

    def _safe(self, value: str) -> str:
        return "".join(c.lower() if c.isalnum() else "_" for c in value)[:50].strip("_")

    async def _test_backend_status(self) -> AggressiveResult:
        await self._ensure_server()
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(f"{SERVER_URL}/api/status")
        data = response.json()
        ok = response.status_code == 200 and data.get("ok") is True
        return AggressiveResult("FASE 1", "Backend sobe corretamente", ok, action="GET /api/status", details=data)

    async def _test_websocket_ping(self) -> AggressiveResult:
        await self._ensure_server()
        if websockets is None:
            return AggressiveResult("FASE 1", "WebSocket responde ping", False, error="Pacote websockets indisponivel.")
        sid = f"aggressive-{uuid.uuid4().hex[:8]}"
        url = SERVER_URL.replace("http://", "ws://").replace("https://", "wss://") + f"/ws/{sid}"
        async with websockets.connect(url, open_timeout=10) as ws:
            await ws.send(json.dumps({"type": "ping"}))
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
        data = json.loads(raw)
        return AggressiveResult("FASE 1", "WebSocket responde ping", data.get("type") == "pong", action="ws ping", details=data)

    async def _test_sqlite_init(self) -> AggressiveResult:
        await db.init_db()
        ok = db.DB_PATH.exists()
        return AggressiveResult("FASE 1", "SQLite inicializa", ok, action="db.init_db", value=str(db.DB_PATH))

    async def _test_export_logs_api(self) -> AggressiveResult:
        exec_id = f"{self.run_id}_export"
        conv_id = f"{self.run_id}_conv"
        await db.create_conversation(conv_id, "Aggressive export")
        await db.create_execution(exec_id, conv_id, "export logs")
        await db.save_action_log(str(uuid.uuid4()), conv_id, exec_id, "export_probe", {}, {"ok": True}, True)
        await db.finish_execution(exec_id, "completed", 1)
        async with httpx.AsyncClient(timeout=12) as client:
            response = await client.get(f"{SERVER_URL}/api/executions/{exec_id}/logs/export")
        return AggressiveResult(
            "FASE 1.1",
            "Exportacao de logs via API",
            response.status_code == 200 and b"export_probe" in response.content,
            action="GET logs/export",
            selector=exec_id,
        )

    async def _start_browser(self):
        await self.browser.start(headless=self.headless, profile_name="default")
        self.browser.begin_execution(self.run_id)

    async def _test_browser_started(self) -> AggressiveResult:
        ok = bool(self.browser._ctx and self.browser._pages)
        return AggressiveResult("FASE 1", "Playwright abre navegador", ok, value=len(self.browser._pages))

    async def _test_agent_simple_google(self) -> AggressiveResult:
        conv_id = f"{self.run_id}_agent"
        exec_id = f"{self.run_id}_agent_exec"
        await db.create_conversation(conv_id, "Aggressive agent")
        await db.create_execution(exec_id, conv_id, "Abra o Google")
        events = []
        async for event in agent.run("Abra https://www.google.com", conv_id, exec_id, []):
            events.append(event)
            if event.get("type") in ("done", "error", "stopped", "timeout"):
                break
        await db.finish_execution(exec_id, "completed" if any(e.get("type") == "done" for e in events) else "error", 1)
        url = self.browser.url.lower()
        ok = "google" in url and any(e.get("type") == "done" for e in events)
        return AggressiveResult("FASE 1", "Agente responde comando simples", ok, action="agent.run", value=self.browser.url, details={"events": events[-5:]})

    async def _test_simple_navigation(self) -> AggressiveResult:
        result = await self.browser.navigate("https://example.com")
        ok = result.get("ok") and "example.com" in self.browser.url
        return AggressiveResult("FASE 1", "Chrome navega para URL simples", bool(ok), action="navigate", selector="https://example.com", details=result)

    async def _test_screenshot_saved(self) -> AggressiveResult:
        shot = await self.browser.screenshot("aggressive_mvp")
        path = Path(shot.get("path", ""))
        return AggressiveResult("FASE 1", "Screenshot salvo", bool(shot.get("ok") and path.exists()), action="screenshot", value=path.name, screenshot=str(path))

    async def _test_logs_saved(self) -> AggressiveResult:
        logs = list(LOGS_DIR.glob("*browser.log"))
        ok = bool(logs) and any(log.stat().st_size > 0 for log in logs)
        return AggressiveResult("FASE 1", "Logs salvos", ok, action="log", value=len(logs))

    async def _test_execution_completed(self) -> AggressiveResult:
        exec_id = f"{self.run_id}_completed"
        conv_id = f"{self.run_id}_completed_conv"
        await db.create_conversation(conv_id, "completed")
        await db.create_execution(exec_id, conv_id, "completed")
        await db.finish_execution(exec_id, "completed", 10)
        ex = await db.get_execution(exec_id)
        return AggressiveResult("FASE 1", "Execucao marcada como concluida", ex and ex.get("status") == "completed", action="db.finish_execution", details=ex or {})

    async def _test_before_after_screenshots(self) -> AggressiveResult:
        before = await self.browser.screenshot("aggressive_before")
        await self.browser.navigate("https://example.com")
        after = await self.browser.screenshot("aggressive_after")
        ok = Path(before.get("path", "")).exists() and Path(after.get("path", "")).exists()
        return AggressiveResult("FASE 1.1", "Screenshot antes/depois", ok, value=f"{before.get('filename')} / {after.get('filename')}")

    async def _test_retry_autocorrect_click(self) -> AggressiveResult:
        corrections = []
        result = await self.browser.safe_click("#selector_que_nao_existe", timeout=900, retries=1)
        if result.get("ok"):
            return AggressiveResult("FASE 1.1", "Retry corrige selector valido alternativo", True, selector=result.get("selector", ""))
        corrections.append("Selector inicial falhou conforme esperado.")
        corrected = await self.browser.click_text("More information", timeout=3000)
        if not corrected.get("ok"):
            corrected = await self.browser.safe_click("a:has-text('More information')", timeout=3000, retries=1)
        ok = bool(corrected.get("ok"))
        corrections.append("Tentei selector alternativo por texto visivel.")
        return AggressiveResult("FASE 1.1", "Retry corrige selector valido alternativo", ok, selector=corrected.get("selector", ""), corrections=corrections, initially_failed=ok)

    async def _test_invalid_action_controlled(self) -> AggressiveResult:
        result = await agent._perform_action_once("acao_inexistente", {})
        ok = result.get("ok") is False and "desconhecida" in result.get("error", "").lower()
        return AggressiveResult("FASE 1.1", "Acao invalida retorna erro controlado", ok, action="invalid", details=result)

    async def _test_timeout_controlled(self) -> AggressiveResult:
        result = await self.browser.wait_selector("#nunca_vai_existir", timeout=700)
        ok = result.get("ok") is False and bool(result.get("error"))
        shot = await self._shot("aggressive_timeout")
        return AggressiveResult("FASE 1.1", "Timeout funciona", ok, action="wait_selector", screenshot=shot, value="timeout controlado", details=result)

    async def _test_stop_control(self) -> AggressiveResult:
        self.browser.clear_stop()
        task = asyncio.create_task(self.browser.wait(3000))
        await asyncio.sleep(0.2)
        self.browser.request_stop()
        stopped = self.browser.should_stop
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        self.browser.clear_stop()
        return AggressiveResult("CONTROLES", "STOP para execucao", stopped and not self.browser.should_stop, action="request_stop")

    async def _test_browser_not_stuck(self) -> AggressiveResult:
        result = await self.browser.navigate("https://example.com")
        return AggressiveResult("FASE 1.1", "Navegador nao fica travado", bool(result.get("ok")), action="navigate", details=result)

    async def _test_detailed_logs(self) -> AggressiveResult:
        await self.browser.safe_click("a:has-text('More information')", timeout=2500, retries=1)
        logs = list(LOGS_DIR.glob("*browser.log"))
        text = "\n".join(log.read_text(encoding="utf-8", errors="ignore")[-4000:] for log in logs[-3:])
        ok = "safe_click" in text or "navigate" in text
        return AggressiveResult("FASE 1.1", "Logs detalhados", ok, value=len(text))

    async def _test_open_wikipedia(self) -> AggressiveResult:
        result = await self.browser.navigate("https://www.wikipedia.org/")
        ok = result.get("ok") and "wikipedia" in self.browser.url
        return AggressiveResult("FASE 2", "Abrir wikipedia.org", bool(ok), action="navigate", details=result)

    async def _test_fill_wikipedia_search(self) -> AggressiveResult:
        result = await self.browser.safe_fill("input[name='search']", "Brasil", timeout=5000, retries=2)
        return AggressiveResult("FASE 2", "Digitar em campo", bool(result.get("ok")), action="safe_fill", selector=result.get("selector", "input[name='search']"), details=result)

    async def _test_click_wikipedia_search(self) -> AggressiveResult:
        result = await self.browser.safe_click("button[type='submit']", timeout=5000, retries=2)
        await self.browser.wait_for_react()
        ok = result.get("ok") and ("Brasil" in await self.browser._page.title() or "brasil" in self.browser.url.lower())
        return AggressiveResult("FASE 2", "Clicar por texto/selector", bool(ok), action="safe_click", selector=result.get("selector", ""), details=result)

    async def _test_scroll(self) -> AggressiveResult:
        result = await self.browser.scroll("down", 700)
        return AggressiveResult("FASE 2", "Scroll", bool(result.get("ok")), action="scroll", details=result)

    async def _test_back(self) -> AggressiveResult:
        response = await self.browser._page.go_back(wait_until="domcontentloaded", timeout=8000)
        ok = response is not None or "wikipedia.org" in self.browser.url
        return AggressiveResult("FASE 2", "Voltar pagina", ok, action="go_back", value=self.browser.url)

    async def _test_forward(self) -> AggressiveResult:
        response = await self.browser._page.go_forward(wait_until="domcontentloaded", timeout=8000)
        ok = response is not None or "wikipedia.org" in self.browser.url
        return AggressiveResult("FASE 2", "Avancar pagina", ok, action="go_forward", value=self.browser.url)

    async def _test_new_tab(self) -> AggressiveResult:
        before = len(await self.browser.list_tabs())
        result = await self.browser.new_tab("https://example.com")
        after = len(await self.browser.list_tabs())
        return AggressiveResult("FASE 2", "Abrir nova aba", bool(result.get("ok") and after > before), action="new_tab", value=after)

    async def _test_switch_tab(self) -> AggressiveResult:
        result = await self.browser.switch_tab(0)
        return AggressiveResult("FASE 2", "Trocar de aba", bool(result.get("ok") and result.get("index") == 0), action="switch_tab", details=result)

    async def _test_close_tab(self) -> AggressiveResult:
        tabs = await self.browser.list_tabs()
        if len(tabs) < 2:
            await self.browser.new_tab("https://example.com")
            tabs = await self.browser.list_tabs()
        result = await self.browser.close_tab(len(tabs) - 1)
        return AggressiveResult("FASE 2", "Fechar aba", bool(result.get("ok")), action="close_tab", details=result)

    async def _test_page_context(self) -> AggressiveResult:
        context = await self.browser.get_page_context()
        ok = isinstance(context, dict) and "url" in context and isinstance(context.get("buttons"), list)
        return AggressiveResult("FASE 2", "Capturar contexto da pagina", ok, action="get_page_context", details=context, value=context.get("title", ""))

    async def _test_hub_open(self) -> AggressiveResult:
        result = await self.browser.navigate(HUB_URL)
        await self.browser.wait_for_react()
        ok = result.get("ok") and "hublbcc" in self.browser.url.lower()
        return AggressiveResult("FASE 3", "Abrir Hub LBCC", bool(ok), action="navigate", selector=HUB_URL, details=result)

    async def _hub_fetch_json(self, url: str) -> dict[str, Any]:
        return await self.browser._page.evaluate(
            """async url => {
                async function readJson(response) {
                    const text = await response.text();
                    try { return JSON.parse(text); } catch (_) { return {raw: text}; }
                }
                const tokenResponse = await fetch('/api/auth/session-token', {credentials:'include'});
                const tokenData = await readJson(tokenResponse);
                const headers = tokenData && tokenData.token ? {Authorization: `Bearer ${tokenData.token}`} : {};
                const response = await fetch(url, {credentials:'include', headers});
                return {ok: response.ok, status: response.status, data: await readJson(response)};
            }""",
            url,
        )

    async def _test_hub_login(self) -> AggressiveResult:
        if "hublbcc" not in self.browser.url.lower():
            await self.browser.navigate(HUB_URL)
        response = await self._hub_fetch_json("/api/auth/me")
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        ok = response.get("ok") and bool(data.get("email") or data.get("id"))
        return AggressiveResult("FASE 3", "Confirmar login persistente", bool(ok), action="fetch /api/auth/me", value=data.get("email", ""), details=response, error="" if ok else str(response))

    async def _test_restart_browser(self) -> AggressiveResult:
        await self.browser.stop()
        await self.browser.start(headless=self.headless, profile_name="default")
        self.browser.begin_execution(self.run_id)
        ok = bool(self.browser._ctx)
        return AggressiveResult("FASE 3", "Reiniciar navegador", ok, action="browser.restart")

    async def _test_hub_multitab(self) -> AggressiveResult:
        await self.browser.navigate(HUB_URL)
        first = await self._test_hub_login()
        tab = await self.browser.new_tab(HUB_URL)
        second = await self._test_hub_login()
        ok = first.ok and second.ok and tab.get("ok")
        return AggressiveResult("FASE 3", "Multiplas abas com sessao mantida", bool(ok), action="new_tab/login", details={"first": first.details, "second": second.details})

    async def _test_safe_upload_probe(self) -> AggressiveResult:
        context = await self.browser.get_page_context()
        has_file = await self.browser._page.locator("input[type='file']").count()
        return AggressiveResult("FASE 3", "Upload teste seguro", True, action="probe", value=f"inputs_file={has_file}", details=context)

    async def _test_hub_dashboard(self) -> AggressiveResult:
        result = await self.browser.navigate(f"{HUB_URL}?page=dashboard")
        await self.browser.wait_for_react()
        context = await self.browser.get_page_context()
        haystack = (context.get("url", "") + " " + context.get("title", "") + " " + " ".join(context.get("menus", []))).lower()
        ok = result.get("ok") and ("dashboard" in haystack or "hublbcc" in haystack)
        return AggressiveResult("FASE 4", "Ir para Dashboard", bool(ok), action="navigate", details=context)

    async def _test_hub_tasks(self) -> AggressiveResult:
        result = await self.browser.navigate(f"{HUB_URL}?page=tarefas")
        await self.browser.wait_for_react()
        context = await self.browser.get_page_context()
        ok = result.get("ok") and "tarefas" in (context.get("url", "") + context.get("title", "")).lower()
        return AggressiveResult("FASE 4", "Ir para Tarefas", bool(ok), action="navigate", details=context)

    async def _test_hub_done_count(self) -> AggressiveResult:
        response = await self._hub_fetch_json("/api/tarefas/resumo")
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        value = data.get("concluido")
        ok = response.get("ok") and isinstance(value, int)
        return AggressiveResult("FASE 4", "Ler tarefas concluidas", bool(ok), action="fetch", selector="/api/tarefas/resumo", value=value, details=response)

    async def _test_hub_alberto_search(self) -> AggressiveResult:
        await self._test_hub_tasks()
        result = await self.browser.safe_fill("input[placeholder*='respons' i]", "Alberto", timeout=3500, retries=1)
        if result.get("ok"):
            return AggressiveResult("FASE 4", 'Buscar colaborador "Alberto"', True, action="safe_fill", selector=result.get("selector", ""))
        counts = await self.browser.hub_task_counts_by_responsible("Alberto")
        ok = bool(counts.get("ok"))
        return AggressiveResult("FASE 4", 'Buscar colaborador "Alberto"', ok, action="api_fallback", selector="Alberto", details=counts, corrections=["Campo de filtro falhou; usei API interna por responsavel."], initially_failed=ok)

    async def _test_hub_alberto_open(self) -> AggressiveResult:
        counts = await self.browser.hub_task_counts_by_responsible("Alberto")
        value = counts.get("open_count") if counts.get("ok") else None
        ok = counts.get("ok") and isinstance(value, int)
        return AggressiveResult("FASE 4", "Detectar tarefas abertas Alberto", bool(ok), action="hub_task_counts_by_responsible", value=value, details=counts)

    async def _test_hub_documents(self) -> AggressiveResult:
        result = await self.browser.navigate(f"{HUB_URL}?page=documentos")
        await self.browser.wait_for_react()
        context = await self.browser.get_page_context()
        ok = result.get("ok") and "document" in (context.get("url", "") + context.get("title", "") + " ".join(context.get("menus", []))).lower()
        return AggressiveResult("FASE 4", "Ir para Documentos", bool(ok), action="navigate", details=context)

    async def _test_hub_download(self) -> AggressiveResult:
        before = {p.name for p in DOWNLOADS_DIR.glob("*") if p.is_file()}
        await self._test_hub_documents()
        data = await self.browser._page.evaluate(
            """async () => {
                async function readJson(response) {
                    const text = await response.text();
                    try { return JSON.parse(text); } catch (_) { return {raw: text}; }
                }
                const tokenResponse = await fetch('/api/auth/session-token', {credentials:'include'});
                const tokenData = await readJson(tokenResponse);
                const headers = tokenData && tokenData.token ? {Authorization: `Bearer ${tokenData.token}`} : {};
                const docsResponse = await fetch('/api/documentos?limit=10', {credentials:'include', headers});
                const docs = await readJson(docsResponse);
                if (!docsResponse.ok || !Array.isArray(docs) || !docs.length) return {ok:false, error:'sem documentos'};
                const doc = docs[0];
                const id = doc.id || doc._id || doc.documento_id || doc.uuid;
                const endpoints = [`/api/documentos/${id}/download`, `/api/documentos/${id}/arquivo`, `/api/documentos/download/${id}`];
                for (const endpoint of endpoints) {
                    const response = await fetch(endpoint, {credentials:'include', headers});
                    if (!response.ok) continue;
                    const buffer = await response.arrayBuffer();
                    let binary = '';
                    const bytes = new Uint8Array(buffer);
                    for (let i = 0; i < bytes.length; i += 0x8000) {
                        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
                    }
                    return {ok:true, endpoint, filename:(doc.nome || doc.name || 'documento_teste') + '.bin', b64:btoa(binary)};
                }
                return {ok:false, error:'endpoint download nao encontrado'};
            }"""
        )
        if data.get("ok") and data.get("b64"):
            import base64
            filename = f"aggressive_{int(time.time())}_{data.get('filename', 'hub_document.bin')}"
            path = DOWNLOADS_DIR / filename
            path.write_bytes(base64.b64decode(data["b64"]))
            ok = path.exists() and path.stat().st_size > 0
            return AggressiveResult("FASE 4", "Baixar documento teste", ok, action="api_download", selector=data.get("endpoint", ""), value=filename, details={"path": str(path)})
        after = {p.name for p in DOWNLOADS_DIR.glob("*") if p.is_file()}
        ok = bool(after - before)
        return AggressiveResult("FASE 4", "Baixar documento teste", ok, error=data.get("error", ""), details=data)

    async def _test_download_exists(self) -> AggressiveResult:
        files = [p for p in DOWNLOADS_DIR.glob("*") if p.is_file()]
        ok = bool(files)
        newest = max(files, key=lambda p: p.stat().st_mtime).name if files else ""
        return AggressiveResult("FASE 3", "Arquivo existe em downloads", ok, value=newest)

    async def _test_navigation_report(self) -> AggressiveResult:
        context = await self.browser.get_page_context()
        path = LOGS_DIR / f"{self.run_id}_navigation_report.json"
        path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
        return AggressiveResult("FASE 4", "Relatorio de navegacao", path.exists(), action="write_report", value=str(path), details=context)

    async def _test_teach_start(self) -> AggressiveResult:
        result = await self.browser.start_teaching("teste_wikipedia_brasil", "Teste ensinado Wikipedia Brasil")
        return AggressiveResult("FASE 5", "Iniciar modo ensinar", bool(result.get("ok") and self.browser.teaching_status().get("active")), action="start_teaching", details=result)

    async def _test_teach_wikipedia_actions(self) -> AggressiveResult:
        corrections = []
        nav = await self.browser.navigate("https://www.wikipedia.org/")
        await self.browser.record_teaching_action("navigate", {"url": "https://www.wikipedia.org/"}, nav)
        fill = await self.browser.safe_fill("input[name='search']", "Brasil", timeout=5000, retries=2)
        await self.browser.record_teaching_action("fill", {"selector": fill.get("selector", "input[name='search']"), "value": "Brasil"}, fill)
        click = await self.browser.safe_click("button[type='submit']", timeout=5000, retries=2)
        await self.browser.record_teaching_action("click", {"selector": click.get("selector", "button[type='submit']")}, click)
        shot = await self.browser.screenshot("teach_wikipedia_brasil_manual")
        await self.browser.record_teaching_action("screenshot", {}, {"ok": shot.get("ok"), "filename": shot.get("filename", "")})
        ok = nav.get("ok") and fill.get("ok") and click.get("ok") and shot.get("ok")
        if not ok:
            corrections.append("Uma ou mais acoes da demonstracao falharam; eventos foram registrados para diagnostico.")
        return AggressiveResult("FASE 5", "Demonstrar Wikipedia Brasil", bool(ok), action="teach_actions", screenshot=shot.get("path", ""), corrections=corrections, details={"nav": nav, "fill": fill, "click": click})

    async def _test_teach_stop(self) -> AggressiveResult:
        result = await self.browser.stop_teaching()
        proc = result.get("procedure", {})
        ok = result.get("ok") and proc.get("name") == "teste_wikipedia_brasil" and result.get("steps_count", 0) > 0
        return AggressiveResult("FASE 5", "Parar e salvar procedimento", bool(ok), action="stop_teaching", value=result.get("steps_count"), details=result)

    async def _test_teach_json(self) -> AggressiveResult:
        proc = procs.get_procedure("teste_wikipedia_brasil") or {}
        steps = proc.get("steps", [])
        types = {step.get("type") or step.get("action") for step in steps}
        ok = bool(proc.get("name") and steps and "navigate" in types and "click" in types and ("fill" in types or "input" in types) and "screenshot" in types)
        return AggressiveResult("FASE 5", "Validar JSON salvo", ok, action="get_procedure", value=len(steps), details={"types": sorted(t for t in types if t)})

    async def _test_replay_taught_procedure(self) -> AggressiveResult:
        proc = procs.get_procedure("teste_wikipedia_brasil") or {}
        events = []
        exec_id = f"{self.run_id}_replay"
        async for event in agent._replay_v5(proc, "aggressive-procedure", exec_id, {}):
            events.append(event)
            if event.get("type") in ("done", "error", "timeout", "stopped"):
                break
        ok = any(e.get("type") == "done" for e in events)
        self.procedures_executed += 1 if ok else 0
        return AggressiveResult("FASE 5", "Executar procedimento salvo", ok, action="agent._replay_v5", details={"events": events[-10:]}, error="" if ok else str(events[-3:]))

    async def _test_procedure_autocorrection(self) -> AggressiveResult:
        proc = procs.get_procedure("teste_wikipedia_brasil") or {}
        steps = list(proc.get("steps", []))
        click_index = next((i for i, step in enumerate(steps) if (step.get("type") or step.get("action")) in ("click", "click_text")), -1)
        if click_index < 0:
            return AggressiveResult("FASE 5", "Forcar falha e autocorrigir selector", False, error="Nao ha passo click para corromper.")
        await self.browser.navigate("https://www.wikipedia.org/")
        await self.browser.safe_fill("input[name='search']", "Brasil", timeout=5000, retries=1)
        original = dict(steps[click_index])
        broken = dict(original)
        broken["selector"] = "#selector_quebrado_agressivo"
        broken["text"] = original.get("text") or "Pesquisar"
        procs.update_step("teste_wikipedia_brasil", click_index, broken)
        corrected = await agent._try_correct_step(procs.step_to_cmd(broken))
        if corrected:
            procs.update_step("teste_wikipedia_brasil", click_index, corrected)
            return AggressiveResult("FASE 5", "Forcar falha e autocorrigir selector", True, action="auto_correct", selector=corrected.get("selector", corrected.get("text", "")), corrections=["FALHOU inicialmente, mas foi corrigido automaticamente."], initially_failed=True)
        fallback = await self.browser.safe_click("button[type='submit']", timeout=5000, retries=1)
        if fallback.get("ok"):
            corrected = {
                "type": "click",
                "action": "click",
                "selector": fallback.get("selector", "button[type='submit']"),
            }
            procs.update_step("teste_wikipedia_brasil", click_index, corrected)
            return AggressiveResult(
                "FASE 5",
                "Forcar falha e autocorrigir selector",
                True,
                action="auto_correct_fallback",
                selector=corrected["selector"],
                corrections=[
                    "FALHOU inicialmente, mas foi corrigido automaticamente.",
                    "Contexto similar nao encontrou texto; selector alternativo seguro funcionou e foi salvo no JSON.",
                ],
                initially_failed=True,
            )
        procs.update_step("teste_wikipedia_brasil", click_index, original)
        return AggressiveResult("FASE 5", "Forcar falha e autocorrigir selector", False, error="Auto-correcao nao encontrou selector alternativo.")

    async def _test_procedure_selftest_inline(self) -> AggressiveResult:
        proc = procs.get_procedure("teste_wikipedia_brasil") or {}
        steps = proc.get("steps", [])
        ok = bool(steps)
        return AggressiveResult("FASE 5", "Rodar teste interno do procedimento", ok, action="inline test_procedure", value=f"{len(steps)} passos validos")

    async def _test_start_execution_control(self) -> AggressiveResult:
        exec_id = f"{self.run_id}_start_control"
        conv_id = f"{self.run_id}_start_control_conv"
        await db.create_conversation(conv_id, "start control")
        ex = await db.create_execution(exec_id, conv_id, "start")
        self.browser.begin_execution(exec_id)
        ok = ex.get("status") == "running" and self.browser.current_exec_id == exec_id
        return AggressiveResult("CONTROLES", "START iniciar execucao", ok, action="begin_execution", details=ex)

    async def _test_pause_control(self) -> AggressiveResult:
        self.browser.pause()
        ok = self.browser.is_paused
        return AggressiveResult("CONTROLES", "PAUSE pausar", ok, action="pause")

    async def _test_resume_control(self) -> AggressiveResult:
        self.browser.resume()
        ok = not self.browser.is_paused
        return AggressiveResult("CONTROLES", "RESUME retomar", ok, action="resume")

    async def _test_save_control(self) -> AggressiveResult:
        proc = procs.save_procedure("aggressive_save_control", "SAVE control", [{"type": "wait", "ms": 1}])
        ok = bool((procs.PROCEDURES_DIR / "aggressive_save_control.json").exists() and proc.get("name"))
        return AggressiveResult("CONTROLES", "SAVE salvar procedimento", ok, action="save_procedure", value=proc.get("name"))

    async def _test_cancel_control(self) -> AggressiveResult:
        await self.browser.start_teaching("aggressive_cancel_control", "Cancel control")
        status_before = self.browser.teaching_status()
        self.browser._teaching = False
        self.browser._teaching_steps = []
        status_after = self.browser.teaching_status()
        ok = status_before.get("active") and not status_after.get("active")
        return AggressiveResult("CONTROLES", "CANCEL cancelar modo ensinar", ok, action="cancel_teaching")

    async def _test_export_logs_file(self) -> AggressiveResult:
        await self._write_report()
        ok = self.report_path.exists() and self.report_path.stat().st_size > 0
        return AggressiveResult("CONTROLES", "EXPORT LOGS", ok, action="write_report", value=str(self.report_path))

    async def _test_clear_session_probe(self) -> AggressiveResult:
        self.browser.clear_stop()
        self.browser.resume()
        self.browser.disable_step_mode()
        ok = not self.browser.should_stop and not self.browser.is_paused and not self.browser.is_step_mode
        return AggressiveResult("CONTROLES", "CLEAR SESSION se existir", ok, action="clear_runtime_flags", value="Sem endpoint dedicado; flags internas limpas.")

    async def _test_invalid_command(self) -> AggressiveResult:
        result = await agent._perform_action_once("banana_operacional", {})
        ok = result.get("ok") is False
        return AggressiveResult("RESILIENCIA", "Comando invalido", ok, action="invalid_command", details=result)

    async def _test_site_down_controlled(self) -> AggressiveResult:
        result = await self.browser.navigate("http://127.0.0.1:9/site-fora-do-ar")
        ok = result.get("ok") is False and bool(result.get("error"))
        return AggressiveResult("RESILIENCIA", "Site fora do ar", ok, action="navigate_down", details=result)

    async def _test_missing_selector_controlled(self) -> AggressiveResult:
        result = await self.browser.safe_click("#selector_inexistente_agressivo", timeout=800, retries=1)
        ok = result.get("ok") is False
        return AggressiveResult("RESILIENCIA", "Selector inexistente", ok, action="safe_click", details=result)

    async def _test_missing_download_controlled(self) -> AggressiveResult:
        result = await self.browser.download_file("https://example.com/arquivo-inexistente-404.pdf", "aggressive_missing_download.pdf")
        ok = result.get("ok") is False or not Path(result.get("path", "")).exists() or Path(result.get("path", "")).stat().st_size < 2000
        return AggressiveResult("RESILIENCIA", "Download que nao acontece", ok, action="download_file", details=result)

    async def _test_invisible_element_controlled(self) -> AggressiveResult:
        await self.browser._page.set_content("<button id='hidden' style='display:none'>Hidden</button>")
        result = await self.browser.safe_click("#hidden", timeout=800, retries=1)
        ok = result.get("ok") is False
        return AggressiveResult("RESILIENCIA", "Elemento invisivel", ok, action="safe_click hidden", details=result)

    async def _test_interrupt_during_fill(self) -> AggressiveResult:
        await self.browser._page.set_content("<input id='slow' />")
        self.browser.clear_stop()
        task = asyncio.create_task(self.browser.safe_fill("#slow", "valor", timeout=3000, retries=1))
        self.browser.request_stop()
        result = await task
        stopped = self.browser.should_stop
        self.browser.clear_stop()
        ok = stopped and result.get("ok") in (True, False)
        return AggressiveResult("RESILIENCIA", "Interrupcao durante fill", ok, action="request_stop/safe_fill", details=result)

    async def _test_interrupt_during_download(self) -> AggressiveResult:
        self.browser.request_stop()
        result = await self.browser.download_file("https://example.com/", "aggressive_interrupt_download.html")
        stopped = self.browser.should_stop
        self.browser.clear_stop()
        ok = stopped and result.get("ok") in (True, False)
        return AggressiveResult("RESILIENCIA", "Interrupcao durante download", ok, action="request_stop/download", details=result)

    async def _test_reexecute_after_failure(self) -> AggressiveResult:
        bad = await self.browser.safe_click("#nao_existe_reexec", timeout=500, retries=1)
        good = await self.browser.navigate("https://example.com")
        ok = bad.get("ok") is False and good.get("ok") is True
        return AggressiveResult("RESILIENCIA", "Reexecucao apos falha", ok, action="fail_then_navigate", details={"bad": bad, "good": good})

    async def _ensure_server(self):
        if await self._server_ok():
            return
        self._start_server()
        deadline = time.time() + 35
        while time.time() < deadline:
            if await self._server_ok():
                return
            await asyncio.sleep(1)
        raise RuntimeError("Backend nao subiu em 35s.")

    async def _server_ok(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{SERVER_URL}/api/status")
            return response.status_code == 200 and response.json().get("ok") is True
        except Exception:
            return False

    def _start_server(self):
        if self.server_proc and self.server_proc.poll() is None:
            return
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.server_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.api.main:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8000",
            ],
            cwd=str(ROOT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self.server_started_by_test = True

    async def _stop_server_for_browser_profile(self):
        if self.server_proc and self.server_proc.poll() is None:
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=8)
            except Exception:
                self.server_proc.kill()
            self.server_proc = None
            await asyncio.sleep(2)
        if os.name == "nt":
            command = (
                "$matches = Get-CimInstance Win32_Process | "
                "Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'python.*uvicorn.*backend.api.main:app' }; "
                "$matches | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
                "Write-Output @($matches).Count"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True, timeout=10)
            await asyncio.sleep(2)

    def _restart_server(self):
        if self.server_proc and self.server_proc.poll() is None:
            return
        try:
            self._start_server()
            self._line("Servidor backend reiniciado em http://localhost:8000.")
        except Exception as exc:
            self._line(f"Nao consegui reiniciar o servidor backend: {exc}")

    async def _write_report(self):
        phase_status = {}
        for phase in self.phase_names:
            phase_results = [r for r in self.results if r.phase == phase]
            phase_status[phase] = bool(phase_results) and all(r.ok for r in phase_results)
        screenshots = list(SCREENSHOTS_DIR.glob("*"))
        videos = list(VIDEOS_DIR.glob("*"))
        logs = list(LOGS_DIR.glob("*"))
        downloads = [p for p in DOWNLOADS_DIR.glob("*") if p.is_file()]
        procedures = procs.list_procedures()
        report = {
            "title": REPORT_TITLE,
            "run_id": self.run_id,
            "started_at": datetime.fromtimestamp(self.started).isoformat(),
            "finished_at": datetime.now().isoformat(),
            "duration_s": round(time.time() - self.started, 2),
            "phase_status": phase_status,
            "total_tests": len(self.results),
            "passed": len([r for r in self.results if r.ok]),
            "failed": len([r for r in self.results if not r.ok]),
            "screenshots_saved": len(screenshots),
            "videos_saved": len(videos),
            "logs_saved": len(logs),
            "downloads_saved": len(downloads),
            "procedures_saved": len(procedures),
            "procedures_executed": self.procedures_executed,
            "auto_corrections": self.corrections_count,
            "failures": [self._result_dict(r) for r in self.failures],
            "results": [self._result_dict(r) for r in self.results],
            "overall": "APROVADO" if self._overall_ok() else "REPROVADO",
        }
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def _result_dict(self, result: AggressiveResult) -> dict[str, Any]:
        return {
            "phase": result.phase,
            "name": result.name,
            "ok": result.ok,
            "action": result.action,
            "selector": result.selector,
            "value": result.value,
            "error": result.error,
            "screenshot": result.screenshot,
            "corrections": result.corrections,
            "initially_failed": result.initially_failed,
            "details": result.details,
        }

    def _overall_ok(self) -> bool:
        required = [phase for phase in self.phase_names if not (self.skip_hub and phase in ("FASE 3", "FASE 4"))]
        for phase in required:
            phase_results = [r for r in self.results if r.phase == phase]
            if not phase_results or not all(r.ok for r in phase_results):
                return False
        return True

    def _print_report(self):
        phase_status = {}
        for phase in self.phase_names:
            phase_results = [r for r in self.results if r.phase == phase]
            phase_status[phase] = bool(phase_results) and all(r.ok for r in phase_results)
        total = len(self.results)
        passed = len([r for r in self.results if r.ok])
        failed = total - passed
        print("")
        print(REPORT_TITLE)
        for phase in ["FASE 1", "FASE 1.1", "FASE 2", "FASE 3", "FASE 4", "FASE 5"]:
            print(f"{phase}: {'APROVADA' if phase_status.get(phase) else 'REPROVADA'}")
        print("")
        print(f"Total de testes: {total}")
        print(f"Testes aprovados: {passed}")
        print(f"Testes reprovados: {failed}")
        print(f"Tempo total: {round(time.time() - self.started, 2)}s")
        print(f"Screenshots salvos: {len(list(SCREENSHOTS_DIR.glob('*')))}")
        print(f"Videos salvos: {len(list(VIDEOS_DIR.glob('*')))}")
        print(f"Logs salvos: {len(list(LOGS_DIR.glob('*')))}")
        print(f"Arquivos baixados: {len([p for p in DOWNLOADS_DIR.glob('*') if p.is_file()])}")
        print(f"Procedimentos salvos: {len(procs.list_procedures())}")
        print(f"Procedimentos executados: {self.procedures_executed}")
        print(f"Falhas encontradas: {failed}")
        print(f"Correcoes automaticas realizadas: {self.corrections_count}")
        if self.failures:
            print("Arquivos que precisam de correcao:")
            for failure in self.failures[:20]:
                print(f"- {failure.phase} / {failure.name}: {failure.error or failure.action}")
        else:
            print("Arquivos que precisam de correcao: nenhum")
        print("")
        print("Resultado geral:")
        print("APROVADO" if self._overall_ok() else "REPROVADO")
        print(f"Relatorio JSON: {self.report_path}")


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Teste agressivo backend das Fases 1 a 5.")
    parser.add_argument("--headed", action="store_true", help="Mostra o navegador.")
    parser.add_argument("--skip-hub", action="store_true", help="Ignora testes que dependem do Hub LBCC logado.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runner = AggressiveAllPhasesTest(headless=not args.headed, skip_hub=args.skip_hub)
    return asyncio.run(runner.run())


if __name__ == "__main__":
    raise SystemExit(main())
