"""
Browser — Chromium persistente via Playwright.
Fase 3: abas múltiplas, downloads/uploads, login persistente, recuperação automática.
"""
import asyncio
import base64
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

DATA_DIR        = Path(__file__).parent.parent / "data"
PROFILE_DIR     = DATA_DIR / "chrome_profile"
DOWNLOADS_DIR   = DATA_DIR / "downloads"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
VIDEOS_DIR      = DATA_DIR / "videos"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
LOGS_DIR        = DATA_DIR / "logs"


class Browser:
    def __init__(self):
        self._pw:   Optional[Playwright]     = None
        self._ctx:  Optional[BrowserContext] = None
        self._pages: list[Page]              = []   # todas as abas
        self._active_idx: int                = 0    # aba ativa

        self._lock = asyncio.Lock()

        # Controles de execução
        self._stop_flag   = False
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._manual_mode = False
        self._step_mode   = False
        self._step_event  = asyncio.Event()
        self._step_event.set()

        # Aprovação humana
        self._approval_pending = False
        self._approval_message = ""
        self._approval_event   = asyncio.Event()
        self._approval_result  = False

        # Exec atual
        self.current_exec_id: Optional[str] = None
        self._exec_screenshot_count = 0
        self.last_downloads: list[str] = []

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self, headless: bool = True):
        for d in [PROFILE_DIR, DOWNLOADS_DIR, SCREENSHOTS_DIR,
                  VIDEOS_DIR, ATTACHMENTS_DIR, LOGS_DIR]:
            d.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()

        # Chromium do Replit se disponível
        replit_chrome = os.getenv("REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE", "")
        launch_kwargs = dict(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            slow_mo=80,
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
        if replit_chrome and Path(replit_chrome).exists():
            launch_kwargs["executable_path"] = replit_chrome
            print(f"[browser] Usando Chromium do Replit: {replit_chrome}")

        self._ctx = await self._pw.chromium.launch_persistent_context(**launch_kwargs)

        # Restaurar abas existentes ou criar uma nova
        existing = self._ctx.pages
        if existing:
            self._pages = list(existing)
            print(f"[browser] Restauradas {len(self._pages)} aba(s) do perfil.")
        else:
            page = await self._ctx.new_page()
            self._pages = [page]

        self._active_idx = 0
        self._setup_page_events(self._pages[0])

        # Listener para novas abas criadas por links/popups
        self._ctx.on("page", self._on_new_page)

        self._log("start", {"tabs": len(self._pages)})
        print(f"[browser] Chromium iniciado. Perfil persistente em: {PROFILE_DIR}")

    async def stop(self):
        if self._ctx:
            await self._ctx.close()
        if self._pw:
            await self._pw.stop()

    def _setup_page_events(self, page: Page):
        page.on("download", lambda dl: asyncio.create_task(self._on_download(dl)))

    async def _on_new_page(self, page: Page):
        """Captura novas abas abertas automaticamente."""
        self._pages.append(page)
        self._setup_page_events(page)
        self._log("new_tab", {"url": page.url, "index": len(self._pages) - 1})
        print(f"[browser] Nova aba aberta: {page.url}")

    async def _on_download(self, download):
        name = download.suggested_filename
        dest = DOWNLOADS_DIR / name
        await download.save_as(str(dest))
        self.last_downloads.append(str(dest))
        self._log("download", {"filename": name, "path": str(dest)})
        print(f"[browser] Download: {dest}")

    # ── Logging ────────────────────────────────────────────────────────────────

    def _log(self, event: str, data: dict):
        try:
            log_file = LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}_browser.log"
            entry = {"ts": datetime.now().isoformat(), "event": event, **data}
            with open(log_file, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── Aba ativa ──────────────────────────────────────────────────────────────

    @property
    def _page(self) -> Page:
        if not self._pages:
            raise RuntimeError("Nenhuma aba aberta")
        idx = min(self._active_idx, len(self._pages) - 1)
        return self._pages[idx]

    # ── Gerenciador de abas ────────────────────────────────────────────────────

    async def list_tabs(self) -> list[dict]:
        tabs = []
        for i, p in enumerate(self._pages):
            try:
                title = await p.title()
            except Exception:
                title = "?"
            tabs.append({
                "index": i,
                "url": p.url,
                "title": title,
                "active": i == self._active_idx,
            })
        return tabs

    async def new_tab(self, url: str = "") -> dict:
        page = await self._ctx.new_page()
        self._pages.append(page)
        self._setup_page_events(page)
        self._active_idx = len(self._pages) - 1
        if url:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        self._log("open_tab", {"url": url, "index": self._active_idx})
        return {"ok": True, "index": self._active_idx, "url": page.url}

    async def switch_tab(self, index: int) -> dict:
        if index < 0 or index >= len(self._pages):
            return {"ok": False, "error": f"Aba {index} não existe. Total: {len(self._pages)}"}
        self._active_idx = index
        page = self._pages[index]
        try:
            await page.bring_to_front()
            title = await page.title()
        except Exception:
            title = "?"
        self._log("switch_tab", {"index": index, "url": page.url})
        return {"ok": True, "index": index, "url": page.url, "title": title}

    async def close_tab(self, index: int = None) -> dict:
        idx = index if index is not None else self._active_idx
        if len(self._pages) <= 1:
            return {"ok": False, "error": "Não é possível fechar a única aba"}
        if idx < 0 or idx >= len(self._pages):
            return {"ok": False, "error": f"Aba {idx} não existe"}
        page = self._pages.pop(idx)
        try:
            await page.close()
        except Exception:
            pass
        self._active_idx = min(self._active_idx, len(self._pages) - 1)
        self._log("close_tab", {"index": idx})
        return {"ok": True, "closed_index": idx, "active_index": self._active_idx}

    # ── Controles de execução ──────────────────────────────────────────────────

    def request_stop(self):
        self._stop_flag = True
        self._pause_event.set()
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
        await self._pause_event.wait()

    def enable_step_mode(self):
        self._step_mode = True
        self._step_event.clear()

    def disable_step_mode(self):
        self._step_mode = False
        self._step_event.set()

    def next_step(self):
        self._step_event.set()

    @property
    def is_step_mode(self) -> bool:
        return self._step_mode

    async def wait_for_step(self):
        if self._step_mode:
            self._step_event.clear()
            await self._step_event.wait()

    def set_manual_mode(self, active: bool):
        self._manual_mode = active

    @property
    def is_manual_mode(self) -> bool:
        return self._manual_mode

    async def request_approval(self, message: str) -> bool:
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

    # ── Ações principais ───────────────────────────────────────────────────────

    async def navigate(self, url: str) -> dict:
        async with self._lock:
            t0 = time.time()
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(1)
                title = await self._page.title()
                self._log("navigate", {"url": url, "title": title})
                return {"ok": True, "url": self._page.url,
                        "title": title, "ms": int((time.time()-t0)*1000)}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def click(self, selector: str) -> dict:
        async with self._lock:
            try:
                await self._page.wait_for_selector(selector, timeout=15_000)
                await self._page.click(selector)
                self._log("click", {"selector": selector})
                return {"ok": True, "selector": selector}
            except Exception as e:
                try:
                    await self._page.locator(selector).first.click(timeout=5_000)
                    return {"ok": True, "selector": selector}
                except Exception:
                    return {"ok": False, "error": str(e)}

    async def fill(self, selector: str, value: str) -> dict:
        async with self._lock:
            try:
                await self._page.wait_for_selector(selector, timeout=15_000)
                await self._page.fill(selector, value)
                self._log("fill", {"selector": selector})
                return {"ok": True, "selector": selector}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def key(self, key: str) -> dict:
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
        try:
            path = Path(filepath)
            if not path.exists():
                path = ATTACHMENTS_DIR / filepath
            if not path.exists():
                return {"ok": False, "error": f"Arquivo não encontrado: {filepath}"}
            await self._page.set_input_files(selector, str(path))
            self._log("upload", {"selector": selector, "file": str(path)})
            return {"ok": True, "selector": selector, "file": str(path)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def download_file(self, url: str, filename: str = None) -> dict:
        """Força download de uma URL."""
        try:
            async with self._page.expect_download(timeout=30_000) as dl_info:
                await self._page.evaluate(f"""
                    const a = document.createElement('a');
                    a.href = '{url}';
                    a.download = '{filename or ""}';
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                """)
            download = await dl_info.value
            name = filename or download.suggested_filename
            dest = DOWNLOADS_DIR / name
            await download.save_as(str(dest))
            self._log("download_forced", {"url": url, "dest": str(dest)})
            return {"ok": True, "filename": name, "path": str(dest)}
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
            tabs = await self.list_tabs()
            return {"ok": True, "url": url, "title": title,
                    "elements": elements, "body": body,
                    "tabs": tabs, "active_tab": self._active_idx}
        except Exception as e:
            return {"ok": False, "error": str(e), "url": ""}

    @property
    def url(self) -> str:
        try:
            return self._page.url
        except Exception:
            return ""

    def list_downloads(self) -> list[dict]:
        if not DOWNLOADS_DIR.exists():
            return []
        return [
            {"filename": f.name, "size": f.stat().st_size,
             "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
             "url": f"/api/files/{f.name}"}
            for f in sorted(DOWNLOADS_DIR.iterdir(),
                            key=lambda x: -x.stat().st_mtime)
            if f.is_file()
        ]

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

    def list_logs(self) -> list[dict]:
        logs = []
        for f in sorted(LOGS_DIR.glob("*.log"), reverse=True)[:7]:
            logs.append({"date": f.stem, "size": f.stat().st_size,
                         "url": f"/api/logs/{f.name}"})
        return logs


browser = Browser()
