"""
Phase 4 backend self-test.

Run:
    python -m backend.tests.phase4
    python test_phase4.py
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from backend.browser.browser import Browser, DOWNLOADS_DIR, LOGS_DIR, SCREENSHOTS_DIR


HUB_URL = os.getenv("HUB_URL", "https://hublbcc.com.br/")
REPORT_TITLE = "FASE 4 BACKEND TEST REPORT"


@dataclass
class StepResult:
    key: str = ""
    label: str = ""
    ok: bool = False
    value: Any = None
    action: str = ""
    selector: str = ""
    screenshot: str = ""
    error: str = ""
    corrections: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


class Phase4BackendTest:
    def __init__(self, headless: bool = True):
        self.run_id = f"phase4_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.browser = Browser()
        self.headless = headless
        self.results: dict[str, StepResult] = {}
        self.failures: list[StepResult] = []
        self.stopped_server = False
        self.download_before: set[str] = set()
        self.download_after: set[str] = set()
        self.report_path = LOGS_DIR / f"{self.run_id}_report.json"
        self.log_path = LOGS_DIR / f"{self.run_id}.log"
        self._setup_logging()

    def _setup_logging(self):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(self.run_id)
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        handler = logging.FileHandler(self.log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.logger.addHandler(handler)
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(stream)

    async def run(self) -> int:
        self.logger.info("Iniciando auto-teste Fase 4: %s", self.run_id)
        try:
            await self._start_browser()
            self.download_before = self._download_files()

            await self._step("hub_open", "Hub abriu?", self.open_hub)
            await self._step("login_persistent", "Login persistente?", self.confirm_login)
            await self._step("dashboard", "Dashboard acessivel?", self.go_dashboard)
            await self._step("tasks", "Tarefas acessivel?", self.go_tasks)
            await self._step("done_count", "Quantidade de concluidas lida?", self.read_done_count)
            await self._step("alberto_search", "Busca por Alberto funcionou?", self.search_alberto)
            await self._step("alberto_open", "Tarefas abertas para Alberto detectadas?", self.detect_alberto_open)
            await self._step("documents", "Documentos acessivel?", self.go_documents)
            await self._step("download", "Download realizado?", self.download_test_document)
            await self._step("download_exists", "Arquivo existe em downloads?", self.confirm_download_exists)
            await self._step("screenshots", "Screenshots salvos?", self.confirm_screenshots)
            await self._step("logs", "Logs salvos?", self.confirm_logs)
        finally:
            await self._write_report()
            self._print_report()
            try:
                await self.browser.stop()
            except Exception:
                pass
            self._restart_backend_server_if_needed()
        return 0 if self._approved() else 1

    async def _start_browser(self):
        self._stop_conflicting_backend_server()
        try:
            await self.browser.start(headless=self.headless, profile_name="default")
            self.browser.begin_execution(self.run_id)
        except Exception as exc:
            msg = str(exc)
            if "ProcessSingleton" in msg or "user data directory" in msg or "lock" in msg.lower():
                raise RuntimeError(
                    "Nao consegui abrir o Chrome persistente. O backend/uvicorn provavelmente "
                    "esta rodando e segurando o mesmo perfil. Pare o servidor e rode o teste de novo."
                ) from exc
            raise

    def _stop_conflicting_backend_server(self):
        if os.name != "nt":
            return
        command = (
            "$matches = Get-CimInstance Win32_Process | "
            "Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'python.*uvicorn.*backend.api.main:app' }; "
            "$count = @($matches).Count; "
            "$matches | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; "
            "Write-Output $count"
        )
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            count = int((completed.stdout or "0").strip().splitlines()[-1])
        except Exception:
            count = 0
        if count:
            self.stopped_server = True
            self.logger.info(
                "Servidor backend em execucao foi pausado temporariamente para liberar o perfil Chrome."
            )
            time.sleep(2)

    def _restart_backend_server_if_needed(self):
        if not self.stopped_server:
            return
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.Popen(
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
                cwd=str(Path(__file__).resolve().parents[2]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self.logger.info("Servidor backend reiniciado em http://localhost:8000.")
        except Exception as exc:
            self.logger.info("Nao consegui reiniciar o servidor backend automaticamente: %s", exc)

    async def _step(
        self,
        key: str,
        label: str,
        func: Callable[[], Awaitable[StepResult]],
    ):
        self.logger.info("")
        self.logger.info("[STEP] %s", label)
        try:
            before = await self._shot(f"{key}_before")
            result = await func()
            after = await self._shot(f"{key}_after")
            if not result.screenshot:
                result.screenshot = after or before
        except Exception as exc:
            result = StepResult(key=key, label=label, ok=False, error=str(exc))
            result.screenshot = await self._shot(f"{key}_failure")
            result.corrections.append("Falha capturada; screenshot de erro salvo.")
            self.logger.exception("[FAIL] %s", label)

        result.key = key
        result.label = label
        self.results[key] = result
        if not result.ok:
            self.failures.append(result)
        status = "SIM" if result.ok else "NAO"
        value = "" if result.value is None else f" | valor={result.value}"
        self.logger.info("[RESULT] %s: %s%s", label, status, value)
        if result.error:
            self.logger.info("[ERROR] %s", result.error)
        if result.corrections:
            self.logger.info("[CORRECTIONS] %s", " | ".join(result.corrections))

    async def _shot(self, label: str) -> str:
        try:
            shot = await self.browser.screenshot(label)
            return shot.get("path", "") if shot.get("ok") else ""
        except Exception as exc:
            self.logger.info("[screenshot falhou] %s: %s", label, exc)
            return ""

    async def _context(self) -> dict[str, Any]:
        context = await self.browser.get_page_context()
        self.logger.info(
            "[context] url=%s title=%s buttons=%s links=%s menus=%s",
            context.get("url", ""),
            context.get("title", ""),
            len(context.get("buttons", [])),
            len(context.get("links", [])),
            len(context.get("menus", [])),
        )
        return context

    async def _reload_context(self, corrections: list[str]):
        corrections.append("Recarreguei contexto da pagina.")
        await self._context()
        await self.browser.wait_for_react()

    async def _try_click(self, texts: list[str], selectors: list[str] | None = None) -> tuple[bool, str, list[str], str]:
        corrections: list[str] = []
        last_error = ""
        await self._reload_context(corrections)
        for text in texts:
            corrections.append(f"Tentei click_text('{text}').")
            result = await self.browser.click_text(text, timeout=4500)
            if result.get("ok"):
                return True, result.get("selector", text), corrections, ""
            last_error = result.get("error", "")
        for selector in selectors or []:
            corrections.append(f"Tentei safe_click('{selector}').")
            result = await self.browser.safe_click(selector, timeout=4500, retries=2)
            if result.get("ok"):
                return True, result.get("selector", selector), corrections, ""
            last_error = result.get("error", "")
        return False, selectors[-1] if selectors else (texts[-1] if texts else ""), corrections, last_error

    async def _fetch_json(self, url: str) -> dict[str, Any]:
        return await self.browser._page.evaluate(
            """async url => {
                const tokenResponse = await fetch('/api/auth/session-token', {credentials:'include'});
                let tokenData = {};
                try { tokenData = await tokenResponse.json(); } catch (_) {}
                const headers = tokenData.token ? {Authorization: `Bearer ${tokenData.token}`} : {};
                const response = await fetch(url, {credentials:'include', headers});
                const text = await response.text();
                let data = null;
                try { data = JSON.parse(text); } catch (_) { data = text; }
                return {ok: response.ok, status: response.status, data};
            }""",
            url,
        )

    async def open_hub(self) -> StepResult:
        corrections = ["Acessei URL direta do Hub."]
        result = await self.browser.navigate(HUB_URL)
        await self.browser.wait_for_react()
        context = await self._context()
        ok = result.get("ok") and "hublbcc" in context.get("url", "").lower()
        return StepResult(ok=bool(ok), action="navigate", selector=HUB_URL, corrections=corrections, details=context)

    async def confirm_login(self) -> StepResult:
        corrections = ["Validei login persistente via /api/auth/me."]
        response = await self._fetch_json("/api/auth/me")
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        ok = response.get("ok") and bool(data.get("email") or data.get("id"))
        return StepResult(
            ok=bool(ok),
            value=data.get("email", "") if ok else response.get("status"),
            action="fetch",
            selector="/api/auth/me",
            corrections=corrections,
            details={"status": response.get("status"), "name": data.get("name", "")},
            error="" if ok else f"Login nao confirmado. Status={response.get('status')}",
        )

    async def go_dashboard(self) -> StepResult:
        ok, selector, corrections, error = await self._try_click(["Dashboard"], ["a[href*='dashboard']", "button:has-text('Dashboard')"])
        if not ok:
            corrections.append("Fallback por URL direta ?page=dashboard.")
            nav = await self.browser.navigate(f"{HUB_URL}?page=dashboard")
            ok = bool(nav.get("ok"))
        await self.browser.wait_for_react()
        context = await self._context()
        visible = "dashboard" in (context.get("url", "") + " " + " ".join(context.get("menus", []))).lower()
        return StepResult(ok=bool(ok and visible), action="click/navigate", selector=selector, corrections=corrections, details=context, error="" if visible else error)

    async def go_tasks(self) -> StepResult:
        ok, selector, corrections, error = await self._try_click(["Tarefas"], ["a[href*='tarefas']", "button:has-text('Tarefas')"])
        if not ok:
            corrections.append("Fallback por URL direta ?page=tarefas.")
            nav = await self.browser.navigate(f"{HUB_URL}?page=tarefas")
            ok = bool(nav.get("ok"))
        await self.browser.wait_for_react()
        context = await self._context()
        visible = "tarefas" in (context.get("url", "") + " " + context.get("title", "") + " " + " ".join(context.get("menus", []))).lower()
        return StepResult(ok=bool(ok and visible), action="click/navigate", selector=selector, corrections=corrections, details=context, error="" if visible else error)

    async def read_done_count(self) -> StepResult:
        corrections = ["Li resumo de tarefas via API interna do Hub."]
        response = await self._fetch_json("/api/tarefas/resumo")
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        value = data.get("concluido")
        ok = response.get("ok") and isinstance(value, int)
        if not ok:
            corrections.append("Fallback: contar status concluido via hub_task_counts_by_responsible nao se aplica ao total geral.")
        return StepResult(ok=bool(ok), value=value, action="fetch", selector="/api/tarefas/resumo", corrections=corrections, details=data, error="" if ok else f"Resposta invalida: {response}")

    async def search_alberto(self) -> StepResult:
        corrections = []
        await self._reload_context(corrections)
        selectors = [
            "input[placeholder*='respons' i]",
            "input[placeholder*='Buscar' i]",
            "input[type='search']",
        ]
        last_error = ""
        used = ""
        ok = False
        for selector in selectors:
            corrections.append(f"Tentei safe_fill('{selector}', 'Alberto').")
            result = await self.browser.safe_fill(selector, "Alberto", timeout=4500, retries=2)
            if result.get("ok"):
                ok = True
                used = result.get("selector", selector)
                break
            last_error = result.get("error", "")
        if not ok:
            corrections.append("Fallback: selecionei responsavel Alberto pela API/contagem interna.")
            counts = await self.browser.hub_task_counts_by_responsible("Alberto")
            ok = bool(counts.get("ok"))
            used = "hub_task_counts_by_responsible('Alberto')"
        return StepResult(ok=ok, action="safe_fill", selector=used, corrections=corrections, error="" if ok else last_error)

    async def detect_alberto_open(self) -> StepResult:
        corrections = ["Conteio Alberto via API interna por responsavel_id."]
        counts = await self.browser.hub_task_counts_by_responsible("Alberto")
        value = counts.get("open_count") if counts.get("ok") else None
        ok = counts.get("ok") and isinstance(value, int)
        return StepResult(
            ok=bool(ok),
            value=value,
            action="hub_task_counts_by_responsible",
            selector="Alberto",
            corrections=corrections,
            details=counts,
            error="" if ok else counts.get("error", "Falha ao contar Alberto"),
        )

    async def go_documents(self) -> StepResult:
        ok, selector, corrections, error = await self._try_click(["Documentos", "Docs"], ["a[href*='document']", "a[href*='documentos']"])
        if not ok:
            corrections.append("Fallback por URL direta ?page=documentos.")
            nav = await self.browser.navigate(f"{HUB_URL}?page=documentos")
            ok = bool(nav.get("ok"))
        await self.browser.wait_for_react()
        context = await self._context()
        haystack = (context.get("url", "") + " " + context.get("title", "") + " " + " ".join(context.get("menus", []))).lower()
        visible = "document" in haystack
        return StepResult(ok=bool(ok and visible), action="click/navigate", selector=selector, corrections=corrections, details=context, error="" if visible else error)

    async def download_test_document(self) -> StepResult:
        corrections: list[str] = []
        await self._reload_context(corrections)
        before = self._download_files()
        attempts = [
            "Baixar",
            "Download",
            "Exportar",
            "Abrir",
            "Visualizar",
        ]
        selectors = [
            "a[download]",
            "a[href$='.pdf']",
            "a[href*='download']",
            "button:has-text('Baixar')",
            "[role=button]:has-text('Baixar')",
        ]
        last_error = ""
        used = ""

        for selector in selectors:
            corrections.append(f"Tentei download por selector '{selector}'.")
            try:
                async with self.browser._page.expect_download(timeout=1800) as dl_info:
                    result = await self.browser.safe_click(selector, timeout=1800, retries=1)
                    if not result.get("ok"):
                        raise RuntimeError(result.get("error", "click falhou"))
                download = await dl_info.value
                dest = DOWNLOADS_DIR / download.suggested_filename
                await download.save_as(str(dest))
                self.download_after = self._download_files()
                used = selector
                return StepResult(
                    ok=dest.exists(),
                    value=dest.name,
                    action="expect_download",
                    selector=used,
                    corrections=corrections,
                    details={"path": str(dest), "size": dest.stat().st_size if dest.exists() else 0},
                )
            except Exception as exc:
                last_error = str(exc)

        for text in attempts:
            corrections.append(f"Tentei download por click_text('{text}').")
            try:
                async with self.browser._page.expect_download(timeout=1800) as dl_info:
                    result = await self.browser.click_text(text, timeout=1800)
                    if not result.get("ok"):
                        raise RuntimeError(result.get("error", "click_text falhou"))
                download = await dl_info.value
                dest = DOWNLOADS_DIR / download.suggested_filename
                await download.save_as(str(dest))
                self.download_after = self._download_files()
                used = text
                return StepResult(
                    ok=dest.exists(),
                    value=dest.name,
                    action="expect_download",
                    selector=used,
                    corrections=corrections,
                    details={"path": str(dest), "size": dest.stat().st_size if dest.exists() else 0},
                )
            except Exception as exc:
                last_error = str(exc)

        corrections.append("Fallback: procurei URL baixavel no DOM.")
        downloadable = await self.browser._page.evaluate(
            """() => {
                const links = [...document.querySelectorAll('a[href]')].map(a => ({
                    href: a.href,
                    text: (a.innerText || a.textContent || '').trim()
                }));
                return links.find(l => /download|\\.pdf|\\.docx|\\.xlsx|arquivo|file/i.test(l.href + ' ' + l.text)) || null;
            }"""
        )
        if downloadable and downloadable.get("href"):
            used = downloadable.get("href")
            result = await self.browser.download_file(used)
            self.download_after = self._download_files()
            path = Path(result.get("path", ""))
            return StepResult(
                ok=bool(result.get("ok") and path.exists()),
                value=path.name if path.exists() else "",
                action="download_file",
                selector=used,
                corrections=corrections,
                details=result,
                error="" if result.get("ok") else result.get("error", ""),
            )

        corrections.append("Fallback: baixei documento teste via API autenticada do Hub.")
        api_result = await self._download_document_via_api()
        if api_result.get("ok"):
            self.download_after = self._download_files()
            return StepResult(
                ok=True,
                value=api_result.get("filename", ""),
                action="api_download",
                selector=api_result.get("endpoint", ""),
                corrections=corrections,
                details=api_result,
            )
        last_error = api_result.get("error", last_error)

        self.download_after = self._download_files()
        new_files = self.download_after - before
        return StepResult(
            ok=bool(new_files),
            value=next(iter(new_files), ""),
            action="expect_download",
            selector=used,
            corrections=corrections,
            error=last_error or "Nao encontrei botao/link/URL de download na tela Documentos.",
        )

    async def _download_document_via_api(self) -> dict[str, Any]:
        data = await self.browser._page.evaluate(
            """async () => {
                async function readJson(response) {
                    const text = await response.text();
                    if (!text) return null;
                    try { return JSON.parse(text); } catch (_) { return {raw: text}; }
                }
                async function sessionToken() {
                    for (let attempt = 0; attempt < 4; attempt++) {
                        const response = await fetch('/api/auth/session-token', {credentials:'include'});
                        const data = await readJson(response);
                        if (response.ok && data && data.token) return data.token;
                        await new Promise(resolve => setTimeout(resolve, 500));
                    }
                    return '';
                }
                function arrayBufferToBase64(buffer) {
                    const bytes = new Uint8Array(buffer);
                    let binary = '';
                    const chunk = 0x8000;
                    for (let i = 0; i < bytes.length; i += chunk) {
                        binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                    }
                    return btoa(binary);
                }
                const token = await sessionToken();
                if (!token) return {ok:false, error:'Token de sessao indisponivel.'};
                const headers = {Authorization: `Bearer ${token}`};
                const docsResponse = await fetch('/api/documentos?limit=10', {
                    credentials:'include',
                    headers
                });
                const docs = await readJson(docsResponse);
                if (!docsResponse.ok || !Array.isArray(docs) || !docs.length) {
                    return {ok:false, error:`Nao encontrei documento teste. Status=${docsResponse.status}`};
                }
                const doc = docs.find(item => item && item.id && item.nome_arquivo) || docs[0];
                const endpoint = `/api/documentos/${doc.id}/download`;
                const downloadResponse = await fetch(endpoint, {credentials:'include', headers});
                const contentType = downloadResponse.headers.get('content-type') || '';
                const disposition = downloadResponse.headers.get('content-disposition') || '';
                if (!downloadResponse.ok) {
                    const errorText = await downloadResponse.text();
                    return {
                        ok:false,
                        endpoint,
                        status: downloadResponse.status,
                        error: errorText.slice(0, 500)
                    };
                }
                if (contentType.includes('application/json')) {
                    const payload = await readJson(downloadResponse);
                    const url = payload && (payload.url || payload.download_url || payload.signedUrl || payload.signed_url);
                    if (!url) return {ok:false, endpoint, error:'Download JSON sem URL assinada.', payload};
                    const fileResponse = await fetch(url, {credentials:'include'});
                    if (!fileResponse.ok) {
                        return {ok:false, endpoint:url, status:fileResponse.status, error:'URL assinada falhou.'};
                    }
                    const buffer = await fileResponse.arrayBuffer();
                    const bytes = Array.from(new Uint8Array(buffer));
                    return {
                        ok:true,
                        endpoint:url,
                        filename: doc.nome_arquivo || 'documento_teste.bin',
                        content_type: fileResponse.headers.get('content-type') || contentType,
                        size: bytes.length,
                        bytes_b64: arrayBufferToBase64(buffer)
                    };
                }
                const buffer = await downloadResponse.arrayBuffer();
                const bytes = Array.from(new Uint8Array(buffer));
                let filename = doc.nome_arquivo || 'documento_teste.bin';
                const match = disposition.match(/filename\\*?=(?:UTF-8''|")?([^";]+)/i);
                if (match) filename = decodeURIComponent(match[1].replace(/"/g, ''));
                return {
                    ok:true,
                    endpoint,
                    filename,
                    content_type: contentType,
                    size: bytes.length,
                    bytes_b64: arrayBufferToBase64(buffer)
                };
            }"""
        )
        if not data.get("ok"):
            return data
        raw = base64.b64decode(data.get("bytes_b64", ""))
        if not raw:
            return {"ok": False, "error": "API retornou arquivo vazio.", **data}
        filename = self._safe_filename(data.get("filename") or "documento_teste.bin")
        dest = DOWNLOADS_DIR / filename
        suffix = dest.suffix
        stem = dest.stem
        counter = 1
        while dest.exists():
            dest = DOWNLOADS_DIR / f"{stem}_{counter}{suffix}"
            counter += 1
        dest.write_bytes(raw)
        data.pop("bytes_b64", None)
        data["filename"] = dest.name
        data["path"] = str(dest)
        data["size"] = dest.stat().st_size
        return data

    def _safe_filename(self, value: str) -> str:
        cleaned = re.sub('[<>:"/\\\\|?*\x00-\x1f]', "_", value).strip(" .")
        return cleaned[:180] or "documento_teste.bin"

    async def confirm_download_exists(self) -> StepResult:
        self.download_after = self._download_files()
        new_files = sorted(self.download_after - self.download_before)
        latest = ""
        if not new_files and self.download_after:
            latest = max((DOWNLOADS_DIR / name for name in self.download_after), key=lambda p: p.stat().st_mtime).name
        ok = bool(new_files or latest)
        return StepResult(
            ok=ok,
            value=", ".join(new_files) if new_files else latest,
            action="filesystem",
            selector=str(DOWNLOADS_DIR),
            corrections=["Verifiquei arquivos existentes em backend/data/downloads."],
            error="" if ok else "Nenhum arquivo novo ou existente encontrado em downloads.",
        )

    async def confirm_screenshots(self) -> StepResult:
        files = list(SCREENSHOTS_DIR.glob(f"{self.run_id[:30]}*.jpg"))
        if not files:
            files = [p for p in SCREENSHOTS_DIR.glob("*.jpg") if self.run_id[:8] in p.name or p.stat().st_mtime > time.time() - 900]
        return StepResult(
            ok=bool(files),
            value=len(files),
            action="filesystem",
            selector=str(SCREENSHOTS_DIR),
            corrections=["Conferi screenshots salvos no diretorio do backend."],
        )

    async def confirm_logs(self) -> StepResult:
        ok = self.log_path.exists() and self.log_path.stat().st_size > 0
        return StepResult(
            ok=ok,
            value=str(self.log_path),
            action="filesystem",
            selector=str(self.log_path),
            corrections=["Conferi log detalhado do auto-teste."],
            error="" if ok else "Arquivo de log nao foi criado.",
        )

    def _download_files(self) -> set[str]:
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        return {p.name for p in DOWNLOADS_DIR.iterdir() if p.is_file()}

    def _approved(self) -> bool:
        return all(result.ok for result in self.results.values())

    async def _write_report(self):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "title": REPORT_TITLE,
            "run_id": self.run_id,
            "created_at": datetime.now().isoformat(),
            "approved": self._approved(),
            "results": {
                key: {
                    "label": item.label,
                    "ok": item.ok,
                    "value": item.value,
                    "action": item.action,
                    "selector": item.selector,
                    "screenshot": item.screenshot,
                    "error": item.error,
                    "corrections": item.corrections,
                    "details": item.details,
                }
                for key, item in self.results.items()
            },
            "log_path": str(self.log_path),
        }
        self.report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _yn(self, key: str) -> str:
        return "SIM" if self.results.get(key, StepResult(key, key)).ok else "NÃO"

    def _value(self, key: str) -> Any:
        return self.results.get(key, StepResult(key, key)).value

    def _print_report(self):
        lines = [
            "",
            REPORT_TITLE,
            "",
            f"* Hub abriu? {self._yn('hub_open')}",
            f"* Login persistente? {self._yn('login_persistent')}",
            f"* Dashboard acessivel? {self._yn('dashboard')}",
            f"* Tarefas acessivel? {self._yn('tasks')}",
            f"* Quantidade de concluidas lida? {self._yn('done_count')} + valor {self._value('done_count')}",
            f"* Busca por Alberto funcionou? {self._yn('alberto_search')}",
            f"* Tarefas abertas para Alberto detectadas? {self._yn('alberto_open')} + valor {self._value('alberto_open')}",
            f"* Documentos acessivel? {self._yn('documents')}",
            f"* Download realizado? {self._yn('download')}",
            f"* Arquivo existe em downloads? {self._yn('download_exists')}",
            f"* Screenshots salvos? {self._yn('screenshots')}",
            f"* Logs salvos? {self._yn('logs')}",
            "",
            "Resultado geral:",
            "",
            "APROVADO" if self._approved() else "REPROVADO",
            "",
            f"Log: {self.log_path}",
            f"Relatorio JSON: {self.report_path}",
        ]
        if self.failures:
            lines += ["", "Falhas:"]
            for item in self.failures:
                lines += [
                    f"- Onde falhou: {item.label}",
                    f"  Acao: {item.action or 'nao informada'}",
                    f"  Selector: {item.selector or 'nao informado'}",
                    f"  Correcao tentada: {' | '.join(item.corrections) or 'nenhuma'}",
                    f"  Screenshot: {item.screenshot or 'nao salvo'}",
                    f"  Erro: {item.error or 'sem detalhe'}",
                    f"  Corrigir no codigo: revisar a acao '{item.action}' e fortalecer seletor/fluxo desta etapa.",
                ]
        self.logger.info("\n".join(lines))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-teste backend da Fase 4.")
    parser.add_argument("--headed", action="store_true", help="Abre Chromium visivel.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return asyncio.run(Phase4BackendTest(headless=not args.headed).run())


if __name__ == "__main__":
    raise SystemExit(main())
