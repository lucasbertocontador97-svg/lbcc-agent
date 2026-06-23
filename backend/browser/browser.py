"""
Browser — Chromium persistente via Playwright.
Fase 2: scroll, key, wait_selector, download, pause/resume, step-by-step, anexos.
"""
import asyncio
import base64
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

DATA_DIR        = Path(__file__).parent.parent / "data"
PROFILE_DIR     = DATA_DIR / "chrome_profile"
DOWNLOADS_DIR   = DATA_DIR / "downloads"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
VIDEOS_DIR      = DATA_DIR / "videos"
ATTACHMENTS_DIR = DATA_DIR / "attachments"


class Browser:
    def __init__(self):
        self._pw: Optional[Playwright] = None
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._lock = asyncio.Lock()

        # Controles de execução
        self._stop_flag    = False
        self._pause_event  = asyncio.Event()
        self._pause_event.set()   # Começa não-pausado
        self._manual_mode  = False
        self._step_mode    = False  # Passo a passo
        self._step_event   = asyncio.Event()
        self._step_event.set()

        # Aprovação humana
        self._approval_pending = False
        self._approval_message = ""
        self._approval_event   = asyncio.Event()
        self._approval_result  = False

        # Exec atual
        self.current_exec_id: Optional[str] = None
        self._exec_screenshot_count = 0

        # Downloads capturados
        self.last_downloads: list[str] = []

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self, headless: bool = True):
        for d in [PROFILE_DIR, DOWNLOADS_DIR, SCREENSHOTS_DIR,
                  VIDEOS_DIR, ATTACHMENTS_DIR]:
            d.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()

        import os
        # Usar Chromium do Replit se disponível (mais rápido, sem precisar baixar)
        replit_chrome = os.getenv("REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE", "")

        launch_kwargs = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            slow_mo=100,
            viewport={"width": 1366, "height": 768},
            locale="pt-BR",
            timezone_id="America/Sao_Paulo",
            accept_downloads=True,
            downloads_path=str(DOWNLOADS_DIR),
            record_video_dir=str(VIDEOS_DIR),
            record_video_size={"width": 1366, "height": 768},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-software-rasterizer",
            ],
        )
        if replit_chrome and os.path.exists(replit_chrome):
            launch_kwargs["executable_path"] = replit_chrome
            print(f"[browser] Usando Chromium do Replit: {replit_chrome}")

        self._ctx = await self._pw.chromium.launch_persistent_context(**launch_kwargs)

        pages = self._ctx.pages
        self._page = pages[0] if pages else await self._ctx.new_page()
        self._ctx.on("page", self._on_new_page)
        print("[browser] Chromium iniciado com perfil persistente.")

    async def _on_new_page(self, page: Page):
        page.on("download", self._on_download)

    async def _on_download(self, download):
        name = download.suggested_filename
        dest = DOWNLOADS_DIR / name
        await download.save_as(str(dest))
        self.last_downloads.append(str(dest))
        print(f"[browser] Download: {dest}")

    async def stop(self):
        if self._ctx:
            await self._ctx.close()
        if self._pw:
            await self._pw.stop()

    # ── Controles de execução ──────────────────────────────────────────────────

    def request_stop(self):
        self._stop_flag = True
        self._pause_event.set()    # Desbloqueia se estava pausado
        self._step_event.set()

    def clear_stop(self):
        self._stop_flag = False

    @property
    def should_stop(self) -> bool:
        return self._stop_flag

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()

    async def wait_if_paused(self):
        """Aguarda se estiver pausado."""
        await self._pause_event.wait()

    # ── Modo passo a passo ─────────────────────────────────────────────────────

    def enable_step_mode(self):
        self._step_mode = True
        self._step_event.clear()

    def disable_step_mode(self):
        self._step_mode = False
        self._step_event.set()

    def next_step(self):
        """Libera um passo e pausa novamente."""
        self._step_event.set()

    @property
    def is_step_mode(self) -> bool:
        return self._step_mode

    async def wait_for_step(self):
        """Aguarda autorização para próximo passo."""
        if self._step_mode:
            self._step_event.clear()
            await self._step_event.wait()

    # ── Aprovação humana ───────────────────────────────────────────────────────

    async def request_approval(self, message: str) -> bool:
        """Pausa e aguarda aprovação do usuário. Retorna True se aprovado."""
        self._approval_pending = True
        self._approval_message = message
        self._approval_event.clear()
        await self._approval_event.wait()
        self._approval_pending = False
        return self._approval_result

    def approve(self):
        self._approval_result = True
        self._approval_event.set()

    def reject(self):
        self._approval_result = False
        self._approval_event.set()

    @property
    def approval_pending(self) -> bool:
        return self._approval_pending

    @property
    def approval_message(self) -> str:
        return self._approval_message

    # ── Modo manual ────────────────────────────────────────────────────────────

    def set_manual_mode(self, active: bool):
        self._manual_mode = active

    @property
    def is_manual_mode(self) -> bool:
        return self._manual_mode

    # ── Exec tracking ──────────────────────────────────────────────────────────

    def begin_execution(self, exec_id: str):
        self.current_exec_id = exec_id
        self._exec_screenshot_count = 0
        self.last_downloads = []
        self.clear_stop()
        self._pause_event.set()
        self._step_event.set()

    def end_execution(self):
        self.current_exec_id = None

    # ── Screenshot ─────────────────────────────────────────────────────────────

    async def screenshot(self, label: str = "") -> dict:
        try:
            exec_id = self.current_exec_id or "manual"
            self._exec_screenshot_count += 1
            safe = label.replace(" ", "_").replace("/", "_")[:30]
            fname = f"{exec_id[:8]}_{self._exec_screenshot_count:03d}"
            if safe:
                fname += f"_{safe}"
            fname += ".jpg"

            path = SCREENSHOTS_DIR / fname
            await asyncio.sleep(0.5)
            raw = await self._page.screenshot(type="jpeg", quality=70, full_page=False)
            path.write_bytes(raw)
            b64 = base64.b64encode(raw).decode()
            return {"ok": True, "b64": b64, "path": str(path), "filename": fname}
        except Exception as e:
            print(f"[browser] Screenshot falhou: {e}")
            return {"ok": False, "error": str(e)}

    # ── Ações do browser ───────────────────────────────────────────────────────

    async def navigate(self, url: str) -> dict:
        async with self._lock:
            t0 = time.time()
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(1)
                title = await self._page.title()
                return {"ok": True, "url": self._page.url,
                        "title": title, "ms": int((time.time()-t0)*1000)}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def click(self, selector: str) -> dict:
        async with self._lock:
            try:
                await self._page.wait_for_selector(selector, timeout=15_000)
                await self._page.click(selector)
                return {"ok": True, "selector": selector}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def fill(self, selector: str, value: str) -> dict:
        async with self._lock:
            try:
                await self._page.wait_for_selector(selector, timeout=15_000)
                await self._page.fill(selector, value)
                return {"ok": True, "selector": selector, "value": value}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def key(self, key: str) -> dict:
        """Pressiona uma tecla (Enter, Tab, Escape, etc)."""
        try:
            await self._page.keyboard.press(key)
            return {"ok": True, "key": key}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def scroll(self, direction: str = "down", amount: int = 500) -> dict:
        try:
            delta = amount if direction == "down" else -amount
            await self._page.evaluate(f"window.scrollBy(0, {delta})")
            return {"ok": True, "direction": direction, "amount": amount}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def wait(self, ms: int) -> dict:
        await asyncio.sleep(ms / 1000)
        return {"ok": True, "ms": ms}

    async def wait_selector(self, selector: str, timeout: int = 15000) -> dict:
        try:
            await self._page.wait_for_selector(selector, timeout=timeout)
            return {"ok": True, "selector": selector}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def select_option(self, selector: str, value: str) -> dict:
        try:
            await self._page.select_option(selector, value)
            return {"ok": True, "selector": selector, "value": value}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def hover(self, selector: str) -> dict:
        try:
            await self._page.hover(selector)
            return {"ok": True, "selector": selector}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def upload_file(self, selector: str, filepath: str) -> dict:
        """Faz upload de um arquivo para um input[type=file]."""
        try:
            path = Path(filepath)
            if not path.exists():
                # Tentar no diretório de attachments
                path = ATTACHMENTS_DIR / filepath
            if not path.exists():
                return {"ok": False, "error": f"Arquivo não encontrado: {filepath}"}
            await self._page.set_input_files(selector, str(path))
            return {"ok": True, "selector": selector, "file": str(path)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def page_state(self) -> dict:
        try:
            url   = self._page.url
            title = await self._page.title()
            elements = await self._page.evaluate("""() => {
                const visible = el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                };
                return Array.from(document.querySelectorAll(
                    'a, button, input, select, textarea, [role=button], [role=link]'
                ))
                .filter(visible).slice(0, 40)
                .map(el => ({
                    tag:  el.tagName.toLowerCase(),
                    type: el.type || '',
                    id:   el.id || '',
                    name: el.name || '',
                    text: (el.innerText || el.placeholder || el.value || '').substring(0, 80),
                    href: el.href || '',
                    cls:  el.className?.toString()?.substring(0, 50) || '',
                }));
            }""")
            body = await self._page.evaluate("""() =>
                document.body?.innerText?.replace(/\\s+/g,' ')?.substring(0, 2000) || ''
            """)
            return {"ok": True, "url": url, "title": title,
                    "elements": elements, "body": body}
        except Exception as e:
            return {"ok": False, "error": str(e), "url": ""}

    @property
    def url(self) -> str:
        return self._page.url if self._page else ""

    def list_videos(self) -> list[dict]:
        return [{"filename": f.name, "size": f.stat().st_size,
                 "url": f"/api/videos/{f.name}"}
                for f in sorted(VIDEOS_DIR.glob("*.webm"),
                                key=lambda x: -x.stat().st_mtime)]

    def list_screenshots(self) -> list[dict]:
        return [{"filename": f.name, "size": f.stat().st_size,
                 "url": f"/api/screenshots/{f.name}"}
                for f in sorted(SCREENSHOTS_DIR.glob("*.jpg"),
                                key=lambda x: -x.stat().st_mtime)]

    def list_attachments(self) -> list[dict]:
        return [{"filename": f.name, "size": f.stat().st_size}
                for f in sorted(ATTACHMENTS_DIR.iterdir())
                if f.is_file()]


browser = Browser()
