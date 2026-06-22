"""
Browser — Chromium persistente via Playwright.
Uma única instância compartilhada por toda a aplicação.
"""
import asyncio
import base64
import time
from pathlib import Path
from typing import Optional

from playwright.async_api import (
    async_playwright, BrowserContext, Page, Playwright
)

PROFILE_DIR = Path(__file__).parent.parent / "data" / "chrome_profile"
DOWNLOADS_DIR = Path(__file__).parent.parent / "data" / "downloads"


class Browser:
    def __init__(self):
        self._pw: Optional[Playwright] = None
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._lock = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self, headless: bool = False):
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

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
            args=["--disable-blink-features=AutomationControlled"],
        )

        pages = self._ctx.pages
        self._page = pages[0] if pages else await self._ctx.new_page()

        # Captura downloads automáticos
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

    # ── Ações ─────────────────────────────────────────────────────────────────

    async def navigate(self, url: str) -> dict:
        async with self._lock:
            t0 = time.time()
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
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

    async def screenshot(self) -> dict:
        """Retorna base64 do screenshot atual."""
        try:
            raw = await self._page.screenshot(type="jpeg", quality=75)
            b64 = base64.b64encode(raw).decode()
            return {"ok": True, "base64": b64}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def page_state(self) -> dict:
        """Snapshot da página para o LLM decidir a próxima ação."""
        try:
            url = self._page.url
            title = await self._page.title()

            # Elementos interativos visíveis
            elements = await self._page.evaluate("""() => {
                const visible = el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 0 && r.height > 0;
                };
                return Array.from(document.querySelectorAll(
                    'a, button, input, select, textarea, [role=button]'
                ))
                .filter(visible)
                .slice(0, 30)
                .map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    id: el.id || '',
                    name: el.name || '',
                    text: (el.innerText || el.placeholder || el.value || '').substring(0, 60),
                    href: el.href || '',
                }));
            }""")

            # Texto visível resumido
            body_text = await self._page.evaluate("""() =>
                document.body?.innerText?.replace(/\\s+/g,' ')?.substring(0, 1500) || ''
            """)

            return {
                "ok": True,
                "url": url,
                "title": title,
                "elements": elements,
                "body": body_text,
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "url": ""}

    @property
    def url(self) -> str:
        return self._page.url if self._page else ""


# Instância global
browser = Browser()
