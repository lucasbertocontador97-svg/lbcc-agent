"""
Browser — Chromium persistente via Playwright.
Fase 1.1: screenshot automático, vídeo, stop, timeout, retry, modo manual.
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


class Browser:
    def __init__(self):
        self._pw: Optional[Playwright] = None
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._lock = asyncio.Lock()
        self._stop_flag = False
        self._manual_mode = False
        self.current_exec_id: Optional[str] = None
        self._exec_screenshot_count = 0

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self, headless: bool = True):
        for d in [PROFILE_DIR, DOWNLOADS_DIR, SCREENSHOTS_DIR, VIDEOS_DIR]:
            d.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()

        self._ctx = await self._pw.chromium.launch_persistent_context(
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

        pages = self._ctx.pages
        self._page = pages[0] if pages else await self._ctx.new_page()
        self._ctx.on("page", lambda p: p.on("download", self._on_download))
        print("[browser] Chromium iniciado.")

    async def stop(self):
        if self._ctx:
            await self._ctx.close()
        if self._pw:
            await self._pw.stop()

    async def _on_download(self, download):
        name = download.suggested_filename
        dest = DOWNLOADS_DIR / name
        await download.save_as(str(dest))
        print(f"[browser] Download: {dest}")

    # ── Controle ───────────────────────────────────────────────────────────────

    def request_stop(self):
        self._stop_flag = True

    def clear_stop(self):
        self._stop_flag = False

    @property
    def should_stop(self) -> bool:
        return self._stop_flag

    def set_manual_mode(self, active: bool):
        self._manual_mode = active

    @property
    def is_manual_mode(self) -> bool:
        return self._manual_mode

    def begin_execution(self, exec_id: str):
        self.current_exec_id = exec_id
        self._exec_screenshot_count = 0
        self.clear_stop()

    def end_execution(self):
        self.current_exec_id = None

    # ── Screenshot ─────────────────────────────────────────────────────────────

    async def screenshot(self, label: str = "") -> dict:
        try:
            exec_id = self.current_exec_id or "manual"
            self._exec_screenshot_count += 1
            safe_label = label.replace(" ", "_").replace("/", "_")[:30]
            fname = f"{exec_id[:8]}_{self._exec_screenshot_count:03d}"
            if safe_label:
                fname += f"_{safe_label}"
            fname += ".jpg"

            path = SCREENSHOTS_DIR / fname
            # Aguardar um momento para a página estabilizar
            await asyncio.sleep(0.5)
            raw = await self._page.screenshot(type="jpeg", quality=70, full_page=False)
            path.write_bytes(raw)
            b64 = base64.b64encode(raw).decode()
            return {"ok": True, "b64": b64, "path": str(path), "filename": fname}
        except Exception as e:
            print(f"[browser] Screenshot falhou: {e}")
            return {"ok": False, "error": str(e)}

    # ── Ações ──────────────────────────────────────────────────────────────────

    async def navigate(self, url: str) -> dict:
        async with self._lock:
            t0 = time.time()
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                # Aguardar página carregar um pouco mais
                await asyncio.sleep(1)
                title = await self._page.title()
                return {"ok": True, "url": self._page.url,
                        "title": title, "ms": int((time.time()-t0)*1000)}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def click(self, selector: str) -> dict:
        async with self._lock:
            try:
                await self._page.wait_for_selector(selector, timeout=10_000)
                await self._page.click(selector)
                return {"ok": True, "selector": selector}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def fill(self, selector: str, value: str) -> dict:
        async with self._lock:
            try:
                await self._page.wait_for_selector(selector, timeout=10_000)
                await self._page.fill(selector, value)
                return {"ok": True, "selector": selector}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def wait(self, ms: int) -> dict:
        await asyncio.sleep(ms / 1000)
        return {"ok": True, "ms": ms}

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
                    'a, button, input, select, textarea, [role=button]'
                ))
                .filter(visible).slice(0, 30)
                .map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    id: el.id || '',
                    name: el.name || '',
                    text: (el.innerText || el.placeholder || el.value || '').substring(0, 60),
                    href: el.href || '',
                }));
            }""")
            body = await self._page.evaluate("""() =>
                document.body?.innerText?.replace(/\\s+/g,' ')?.substring(0, 1500) || ''
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


browser = Browser()
