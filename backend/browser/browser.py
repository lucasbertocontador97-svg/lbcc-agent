"""
Browser — Chromium persistente via Playwright.
Fase 3: abas múltiplas, downloads/uploads, login persistente, recuperação automática.
"""
import asyncio
import base64
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext, Page, Playwright

DATA_DIR        = Path(__file__).parent.parent / "data"
PROFILE_DIR     = DATA_DIR / "chrome_profile"
PROFILES_DIR    = DATA_DIR / "profiles"
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
        self._download_callbacks = []
        self._headless = True
        self._profile_name = "default"
        self._teaching = False
        self._teaching_name = ""
        self._teaching_description = ""
        self._teaching_steps: list[dict] = []
        self._teaching_started_at = ""
        self._teaching_last_sig = ""
        self._teaching_last_ts = 0.0
        self._teaching_binding_ready = False

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def start(self, headless: bool = True, profile_name: str = "default"):
        for d in [PROFILE_DIR, PROFILES_DIR, DOWNLOADS_DIR, SCREENSHOTS_DIR,
                  VIDEOS_DIR, ATTACHMENTS_DIR, LOGS_DIR]:
            d.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()
        self._headless = headless
        await self._launch_context(profile_name, headless)
        self._log("start", {"tabs": len(self._pages), "profile": self._profile_name})
        print(f"[browser] Chromium iniciado. Perfil persistente em: {self.profile_dir}")
        return

    @property
    def profile_dir(self) -> Path:
        if self._profile_name == "default":
            return PROFILE_DIR
        return PROFILES_DIR / self._profile_name

    async def _launch_context(self, profile_name: str, headless: bool):
        self._profile_name = profile_name
        profile_dir = PROFILE_DIR if profile_name == "default" else PROFILES_DIR / profile_name
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Chromium do Replit se disponível
        replit_chrome = os.getenv("REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE", "")
        launch_kwargs = dict(
            user_data_dir=str(profile_dir),
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
        self._teaching_binding_ready = False
        await self._ensure_teaching_binding()

        # Restaurar abas existentes ou criar uma nova
        existing = self._ctx.pages
        if existing:
            self._pages = list(existing)
            print(f"[browser] Restauradas {len(self._pages)} aba(s) do perfil.")
        else:
            page = await self._ctx.new_page()
            self._pages = [page]

        self._active_idx = 0
        for page in self._pages:
            self._setup_page_events(page)
            await self._install_teaching_hooks(page)

        # Listener para novas abas criadas por links/popups
        self._ctx.on("page", self._on_new_page)

        self._log("profile_ready", {"tabs": len(self._pages), "profile": self._profile_name})

    async def use_profile(self, profile_name: str) -> dict:
        safe_name = "".join(c for c in profile_name.lower() if c.isalnum() or c in ("-", "_"))[:40]
        if not safe_name:
            safe_name = "default"
        if safe_name == self._profile_name and self._ctx:
            return {"ok": True, "profile": self._profile_name, "path": str(self.profile_dir)}

        async with self._lock:
            try:
                if self._ctx:
                    await self._ctx.close()
                self._ctx = None
                self._pages = []
                self._active_idx = 0
                await self._launch_context(safe_name, self._headless)
                self._log("switch_profile", {"profile": safe_name, "path": str(self.profile_dir)})
                return {"ok": True, "profile": safe_name, "path": str(self.profile_dir)}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def stop(self):
        if self._ctx:
            await self._ctx.close()
        if self._pw:
            await self._pw.stop()

    def _setup_page_events(self, page: Page):
        page.on("download", lambda dl: asyncio.create_task(self._on_download(dl)))
        page.on("framenavigated", lambda frame: asyncio.create_task(self._on_frame_navigated(frame)))

    async def _on_new_page(self, page: Page):
        """Captura novas abas abertas automaticamente."""
        self._pages.append(page)
        self._setup_page_events(page)
        await self._install_teaching_hooks(page)
        self._log("new_tab", {"url": page.url, "index": len(self._pages) - 1})
        print(f"[browser] Nova aba aberta: {page.url}")
        await self.record_teaching_action("new_tab", {"url": page.url}, {"ok": True})

    async def _on_download(self, download):
        name = download.suggested_filename
        dest = DOWNLOADS_DIR / name
        await download.save_as(str(dest))
        size = dest.stat().st_size if dest.exists() else 0
        info = {
            "filename": name,
            "path": str(dest),
            "size": size,
            "url": f"/api/files/{name}",
        }
        self.last_downloads.append(info)
        self._log("download", info)
        await self.record_teaching_action("download", {
            "url": download.url,
            "filename": name,
        }, {"ok": True, **info})
        print(f"[browser] Download: {dest} ({size} bytes)")
        # Notificar callbacks registrados
        for cb in self._download_callbacks:
            try:
                await cb(info)
            except Exception:
                pass

    # ── Logging ────────────────────────────────────────────────────────────────

    def _log(self, event: str, data: dict):
        try:
            log_file = LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}_browser.log"
            entry = {"ts": datetime.now().isoformat(), "event": event, **data}
            with open(log_file, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    async def start_teaching(self, name: str, description: str = "") -> dict:
        from backend.procedures import manager as procs

        safe_name = procs.sanitize_name(name)
        self._teaching = True
        self._teaching_name = safe_name
        self._teaching_description = description or f"Procedimento ensinado: {safe_name}"
        self._teaching_steps = []
        self._teaching_started_at = datetime.now().isoformat()
        self._teaching_last_sig = ""
        self._teaching_last_ts = 0.0
        self._manual_mode = True
        self._stop_flag = False
        self._pause_event.set()
        await self._ensure_active_page()
        await self._ensure_teaching_binding()
        for page in list(self._pages):
            await self._install_teaching_hooks(page)
        await self.record_teaching_action("navigate", {"url": self.url}, {"ok": True})
        self._log("teaching_start", {"name": safe_name, "description": self._teaching_description})
        return {
            "ok": True,
            "name": safe_name,
            "description": self._teaching_description,
            "started_at": self._teaching_started_at,
        }

    async def stop_teaching(self) -> dict:
        from backend.procedures import manager as procs

        if not self._teaching:
            return {"ok": False, "error": "Modo ensinar nao esta ativo."}
        name = self._teaching_name
        description = self._teaching_description
        steps = list(self._teaching_steps)
        self._teaching = False
        self._teaching_name = ""
        self._teaching_description = ""
        self._teaching_steps = []
        self._manual_mode = False
        proc = procs.save_procedure(
            name,
            description,
            steps,
            extra={
                "source": "teach_mode",
                "taught_at": self._teaching_started_at,
                "last_status": "gravado",
            },
        )
        self._log("teaching_stop", {"name": proc.get("name"), "steps": len(steps)})
        return {"ok": True, "procedure": proc, "steps_count": len(steps)}

    def teaching_status(self) -> dict:
        return {
            "active": self._teaching,
            "name": self._teaching_name,
            "description": self._teaching_description,
            "steps_count": len(self._teaching_steps),
            "started_at": self._teaching_started_at,
        }

    async def record_teaching_action(self, action: str, cmd: dict, result: dict = None) -> dict:
        if not self._teaching:
            return {"ok": False, "recorded": False}
        result = result or {}
        if result and result.get("ok") is False:
            return {"ok": False, "recorded": False}
        step = {
            "type": action,
            "action": action,
            "url": cmd.get("url", self.url if action == "navigate" else ""),
            "selector": cmd.get("selector", ""),
            "text": cmd.get("text", ""),
            "value": cmd.get("value", ""),
            "wait_for": cmd.get("wait_for", ""),
        }
        if action == "click_point":
            step["x"] = cmd.get("x", 0)
            step["y"] = cmd.get("y", 0)
        if action == "type_text":
            step["value"] = cmd.get("value", cmd.get("text", ""))
        if action == "click_text" and not step["text"]:
            step["text"] = cmd.get("selector", "")
        if action == "key":
            step["key"] = cmd.get("key", "")
        if action == "wait":
            step["ms"] = cmd.get("ms", 1000)
        if action == "select":
            step["value"] = cmd.get("value", "")
        if action == "upload":
            step["file"] = cmd.get("file", "")
        if action == "download":
            step["filename"] = cmd.get("filename", result.get("filename", ""))
            step["url"] = cmd.get("url", result.get("url", ""))
        await self._record_teaching_step(step)
        return {"ok": True, "recorded": True}

    async def _record_teaching_step(self, payload: dict) -> dict:
        if not self._teaching:
            return {"ok": False, "recorded": False}
        step_type = payload.get("type") or payload.get("action") or "click"
        if step_type in ("mousemove", "mouseover", "focus"):
            return {"ok": False, "recorded": False}
        step = {
            "type": step_type,
            "action": step_type,
            "url": payload.get("url", self.url if step_type == "navigate" else ""),
            "selector": payload.get("selector", ""),
            "text": payload.get("text", ""),
            "value": payload.get("value", ""),
            "wait_for": payload.get("wait_for", ""),
        }
        for key in ("href", "key", "ms", "file", "filename", "x", "y"):
            if payload.get(key) not in (None, ""):
                step[key] = payload.get(key)
        if step_type == "navigate" and not step.get("url"):
            step["url"] = self.url
        if step_type in ("fill", "select") and step.get("value") == "":
            return {"ok": False, "recorded": False}
        sig = json.dumps({
            "type": step.get("type"),
            "url": step.get("url"),
            "selector": step.get("selector"),
            "text": step.get("text"),
            "value": step.get("value"),
        }, ensure_ascii=False, sort_keys=True)
        now = time.time()
        previous_ts = self._teaching_last_ts
        if sig == self._teaching_last_sig and now - self._teaching_last_ts < 1.2:
            return {"ok": True, "recorded": False, "deduped": True}
        self._teaching_last_sig = sig
        self._teaching_last_ts = now
        if previous_ts:
            gap_ms = int((now - previous_ts) * 1000)
            if gap_ms >= 2000:
                self._teaching_steps.append({
                    "type": "wait",
                    "action": "wait",
                    "ms": min(gap_ms, 30000),
                })

        try:
            ss = await self.screenshot(f"teach_{step_type}")
            if ss.get("ok"):
                step["screenshot"] = ss.get("filename", "")
        except Exception:
            pass
        self._teaching_steps.append({k: v for k, v in step.items() if v not in ("", None)})
        self._log("teaching_step", {
            "name": self._teaching_name,
            "index": len(self._teaching_steps),
            "step": self._teaching_steps[-1],
        })
        return {"ok": True, "recorded": True, "step": self._teaching_steps[-1]}

    # ── Aba ativa ──────────────────────────────────────────────────────────────

    @property
    def _page(self) -> Page:
        if not self._pages:
            raise RuntimeError("Nenhuma aba aberta")
        idx = min(self._active_idx, len(self._pages) - 1)
        return self._pages[idx]

    async def _ensure_active_page(self) -> Page:
        if self._ctx:
            try:
                current = list(self._ctx.pages)
                self._pages = [p for p in current if not p.is_closed()]
            except Exception:
                self._ctx = None
                self._pages = []
        else:
            self._pages = []
        if not self._ctx:
            await self._launch_context(self._profile_name, self._headless)
        if not self._pages:
            try:
                page = await self._ctx.new_page()
            except Exception:
                self._ctx = None
                self._pages = []
                await self._launch_context(self._profile_name, self._headless)
                page = self._pages[0] if self._pages else await self._ctx.new_page()
            self._pages = [page]
            self._setup_page_events(page)
            await self._install_teaching_hooks(page)
        self._active_idx = min(self._active_idx, len(self._pages) - 1)
        page = self._pages[self._active_idx]
        if page.is_closed():
            page = await self._ctx.new_page()
            self._pages[self._active_idx] = page
            self._setup_page_events(page)
            await self._install_teaching_hooks(page)
        return page

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
        if self._approval_pending:
            self._approval_result = False
            self._approval_event.set()

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
            page = await self._ensure_active_page()
            raw = await page.screenshot(type="jpeg", quality=70, full_page=False)
            path.write_bytes(raw)
            b64 = base64.b64encode(raw).decode()
            return {"ok": True, "b64": b64, "path": str(path), "filename": fname}
        except Exception as e:
            print(f"[browser] Screenshot falhou: {e}")
            return {"ok": False, "error": str(e)}

    # ── Ações principais ───────────────────────────────────────────────────────

    async def click_point(self, x: float, y: float) -> dict:
        async with self._lock:
            try:
                page = await self._ensure_active_page()
                size = page.viewport_size or await page.evaluate(
                    "() => ({width: window.innerWidth, height: window.innerHeight})"
                )
                width = max(1, int(size.get("width") or 1))
                height = max(1, int(size.get("height") or 1))
                ratio_x = min(max(float(x), 0.0), 1.0)
                ratio_y = min(max(float(y), 0.0), 1.0)
                px = int(ratio_x * width)
                py = int(ratio_y * height)
                await page.mouse.click(px, py)
                await self.wait_for_react(1200)
                result = {"ok": True, "x": ratio_x, "y": ratio_y, "px": px, "py": py}
                await self.record_teaching_action("click_point", {"x": ratio_x, "y": ratio_y}, result)
                self._log("manual_click_point", {"x": ratio_x, "y": ratio_y, "px": px, "py": py})
                return result
            except Exception as e:
                ss = await self.screenshot("manual_click_error")
                self._log("manual_click_point_error", {"error": str(e)[:300], "screenshot": ss.get("filename", "")})
                return {"ok": False, "error": str(e), "screenshot": ss.get("filename", "")}

    async def type_text(self, text: str) -> dict:
        async with self._lock:
            try:
                page = await self._ensure_active_page()
                value = str(text or "")
                await page.keyboard.type(value, delay=15)
                await self.wait_for_react(800)
                result = {"ok": True, "chars": len(value)}
                await self.record_teaching_action("type_text", {"value": value}, result)
                self._log("manual_type_text", {"chars": len(value)})
                return result
            except Exception as e:
                ss = await self.screenshot("manual_type_error")
                self._log("manual_type_text_error", {"error": str(e)[:300], "screenshot": ss.get("filename", "")})
                return {"ok": False, "error": str(e), "screenshot": ss.get("filename", "")}

    async def manual_key(self, key: str) -> dict:
        result = await self.key(key)
        if result.get("ok"):
            await self.record_teaching_action("key", {"key": key}, result)
            self._log("manual_key", {"key": key})
            await self.wait_for_react(1000)
        return result

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
            # Tenta vários formatos de seletor automaticamente
            attempts = [selector]

            # Se parece texto, adicionar variantes
            if not selector.startswith(("#", ".", "[", "input", "button", "a", "select", "textarea")):
                attempts += [
                    f"text={selector}",
                    f"a:has-text('{selector}')",
                    f"button:has-text('{selector}')",
                    f"[title='{selector}']",
                    f"span:has-text('{selector}')",
                ]
            # Variantes adicionais para qualquer seletor
            attempts += [
                f"text={selector}",
                f":text('{selector}')",
            ]

            last_error = ""
            for sel in attempts:
                try:
                    await self._page.locator(sel).first.click(timeout=8_000)
                    self._log("click", {"selector": sel})
                    return {"ok": True, "selector": sel}
                except Exception as e:
                    last_error = str(e)
                    continue

            return {"ok": False, "error": last_error[:200]}

    async def click_text(self, text: str, timeout: int = 4500) -> dict:
        async with self._lock:
            wanted = (text or "").strip()
            if not wanted:
                return {"ok": False, "error": "Texto vazio"}

            attempts = [
                f"role=button[name='{wanted}']",
                f"role=link[name='{wanted}']",
                f"text={wanted}",
                f"a:has-text('{wanted}')",
                f"button:has-text('{wanted}')",
                f"[role=button]:has-text('{wanted}')",
                f"[role=menuitem]:has-text('{wanted}')",
                f"span:has-text('{wanted}')",
            ]
            last_error = ""
            for sel in attempts:
                try:
                    locator = self._page.locator(sel).first
                    await locator.wait_for(state="visible", timeout=timeout)
                    await locator.click(timeout=timeout)
                    try:
                        await self._page.wait_for_load_state("domcontentloaded", timeout=timeout)
                    except Exception:
                        pass
                    self._log("click_text", {"text": wanted, "selector": sel})
                    return {"ok": True, "text": wanted, "selector": sel}
                except Exception as e:
                    last_error = str(e)
            return {"ok": False, "text": wanted, "error": last_error[:300]}

    async def fill(self, selector: str, value: str) -> dict:
        async with self._lock:
            try:
                await self._page.wait_for_selector(selector, timeout=15_000)
                await self._page.fill(selector, value)
                self._log("fill", {"selector": selector})
                return {"ok": True, "selector": selector}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    async def wait_for_react(self, timeout: int = 800) -> dict:
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=timeout)
        except Exception:
            pass
        try:
            await self._page.wait_for_load_state("networkidle", timeout=min(timeout, 600))
        except Exception:
            pass
        try:
            stable = await self._page.evaluate(
                """({stableMs, maxMs}) => new Promise(resolve => {
                    let done = false;
                    let timer = null;
                    const finish = (ok) => {
                        if (done) return;
                        done = true;
                        try { observer.disconnect(); } catch (_) {}
                        clearTimeout(timer);
                        resolve(ok);
                    };
                    const observer = new MutationObserver(() => {
                        clearTimeout(timer);
                        timer = setTimeout(() => finish(true), stableMs);
                    });
                    observer.observe(document.body || document.documentElement, {
                        childList: true,
                        subtree: true,
                        attributes: true
                    });
                    timer = setTimeout(() => finish(true), stableMs);
                    setTimeout(() => finish(false), maxMs);
                })""",
                {"stableMs": 180, "maxMs": timeout},
            )
            self._log("wait_for_react", {"stable": bool(stable), "timeout": timeout})
            return {"ok": True, "stable": bool(stable)}
        except Exception as e:
            self._log("wait_for_react", {"stable": False, "error": str(e)[:200]})
            return {"ok": False, "error": str(e)}

    async def highlight_element(self, locator, label: str = "") -> dict:
        try:
            await locator.evaluate(
                """(el, label) => {
                    const oldOutline = el.style.outline;
                    const oldShadow = el.style.boxShadow;
                    const oldPosition = el.style.position;
                    if (!oldPosition || oldPosition === 'static') el.style.position = 'relative';
                    el.style.outline = '3px solid #f59e0b';
                    el.style.boxShadow = '0 0 0 6px rgba(245, 158, 11, .22)';
                    el.dataset.lbccHighlight = label || 'target';
                    setTimeout(() => {
                        el.style.outline = oldOutline || '';
                        el.style.boxShadow = oldShadow || '';
                        el.style.position = oldPosition || '';
                        delete el.dataset.lbccHighlight;
                    }, 1600);
                    return true;
                }""",
                label,
            )
            self._log("highlight_element", {"label": label})
            return {"ok": True}
        except Exception as e:
            self._log("highlight_element", {"label": label, "error": str(e)[:200]})
            return {"ok": False, "error": str(e)}

    async def _overlay_info(self, locator) -> dict:
        try:
            return await locator.evaluate(
                """el => {
                    const rect = el.getBoundingClientRect();
                    const x = Math.min(Math.max(rect.left + rect.width / 2, 0), window.innerWidth - 1);
                    const y = Math.min(Math.max(rect.top + rect.height / 2, 0), window.innerHeight - 1);
                    const top = document.elementFromPoint(x, y);
                    const describe = node => {
                        if (!node) return '';
                        const id = node.id ? '#' + node.id : '';
                        const cls = typeof node.className === 'string' && node.className
                            ? '.' + node.className.trim().split(/\\s+/).slice(0, 3).join('.')
                            : '';
                        return `${node.tagName.toLowerCase()}${id}${cls}`;
                    };
                    return {
                        blocked: !!top && top !== el && !el.contains(top),
                        top: describe(top),
                        target: describe(el),
                        x,
                        y
                    };
                }"""
            )
        except Exception as e:
            return {"blocked": False, "error": str(e)[:200]}

    async def _locator_value(self, locator) -> str:
        try:
            return await locator.input_value(timeout=2_000)
        except Exception:
            try:
                value = await locator.evaluate(
                    """el => {
                        if ('value' in el) return el.value || '';
                        return el.textContent || '';
                    }"""
                )
                return value or ""
            except Exception:
                return ""

    def _values_match(self, expected: str, actual: str) -> bool:
        if actual == expected:
            return True
        expected_digits = re.sub(r"\D", "", expected or "")
        actual_digits = re.sub(r"\D", "", actual or "")
        if expected_digits and expected_digits == actual_digits:
            return True
        return False

    def _selector_attempts(self, selector: str) -> list[str]:
        if selector.startswith(("role=", "text=", ":")):
            return [selector]
        attempts = [selector]
        if not selector.startswith(("#", ".", "[", "input", "button", "a", "select", "textarea")):
            attempts += [
                f"text={selector}",
                f"a:has-text('{selector}')",
                f"button:has-text('{selector}')",
                f"[title='{selector}']",
                f"span:has-text('{selector}')",
            ]
        attempts += [f"text={selector}", f":text('{selector}')"]
        return list(dict.fromkeys(attempts))

    def _css_value(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _selector_hint_words(self, selector: str) -> list[str]:
        hints = re.findall(r"['\"]([^'\"]{3,})['\"]", selector or "")
        if not hints and selector:
            hints = [selector]
        words: list[str] = []
        for hint in hints:
            for word in re.findall(r"[A-Za-zÀ-ÿ0-9]{4,}", hint):
                lowered = word.lower()
                if lowered not in ("input", "textarea", "placeholder", "button"):
                    words.append(word)
        return list(dict.fromkeys(words))

    def _fill_selector_attempts(self, selector: str) -> list[str]:
        attempts = [selector]
        if selector.startswith(("#", ".", "[")):
            return attempts
        words = self._selector_hint_words(selector)
        for word in words:
            value = self._css_value(word)
            attempts += [
                f"input[placeholder*=\"{value}\" i]",
                f"textarea[placeholder*=\"{value}\" i]",
                f"input[name*=\"{value}\" i]",
                f"input[aria-label*=\"{value}\" i]",
            ]
        return list(dict.fromkeys(attempts))

    async def _fallback_input_selector(self, selector: str) -> str:
        words = self._selector_hint_words(selector)
        if not words:
            return ""
        token = f"lbcc-fallback-{int(time.time() * 1000)}"
        try:
            found = await self._page.evaluate(
                """({words, token}) => {
                    const normalize = value => (value || '')
                        .normalize('NFD')
                        .replace(/[\\u0300-\\u036f]/g, '')
                        .toLowerCase();
                    const wanted = words.map(normalize).filter(Boolean);
                    const nodes = Array.from(document.querySelectorAll(
                        'input, textarea, [contenteditable="true"]'
                    ));
                    const visible = el => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style && style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && rect.width > 4
                            && rect.height > 4
                            && rect.width < window.innerWidth * 0.9
                            && rect.height < window.innerHeight * 0.6;
                    };
                    let best = null;
                    let bestScore = 0;
                    for (const el of nodes) {
                        if (!visible(el) || el.disabled || el.readOnly) continue;
                        const haystack = normalize([
                            el.getAttribute('placeholder'),
                            el.getAttribute('aria-label'),
                            el.getAttribute('type'),
                            el.getAttribute('name'),
                            el.getAttribute('id'),
                            el.getAttribute('class'),
                            el.textContent
                        ].filter(Boolean).join(' '));
                        const score = wanted.reduce((total, word) => (
                            total + (haystack.includes(word) ? 1 : 0)
                        ), 0);
                        if (score > bestScore) {
                            best = el;
                            bestScore = score;
                        }
                    }
                    if (!best || bestScore === 0) return '';
                    best.setAttribute('data-lbcc-fallback-target', token);
                    return `[data-lbcc-fallback-target="${token}"]`;
                }""",
                {"words": words, "token": token},
            )
            if found:
                self._log("safe_fill", {
                    "selector": selector,
                    "step": "fallback_input_encontrado",
                    "fallback_selector": found,
                    "words": words,
                })
            return found or ""
        except Exception as e:
            self._log("safe_fill", {
                "selector": selector,
                "step": "fallback_input_falhou",
                "error": str(e)[:250],
            })
            return ""

    async def _fallback_click_selector(self, selector: str) -> str:
        words = self._selector_hint_words(selector)
        if not words:
            return ""
        token = f"lbcc-click-{int(time.time() * 1000)}"
        try:
            found = await self._page.evaluate(
                """({words, token}) => {
                    const normalize = value => (value || '')
                        .normalize('NFD')
                        .replace(/[\\u0300-\\u036f]/g, '')
                        .toLowerCase();
                    const wanted = words.map(normalize).filter(Boolean);
                    const nodes = Array.from(document.querySelectorAll(
                        'button, a, [role=button], [role=link], [role=menuitem], input, select, textarea, div, span'
                    ));
                    const visible = el => {
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style && style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && rect.width > 4
                            && rect.height > 4;
                    };
                    const textOf = el => [
                        el.innerText,
                        el.textContent,
                        el.value,
                        el.getAttribute('placeholder'),
                        el.getAttribute('aria-label'),
                        el.getAttribute('title'),
                        el.getAttribute('name')
                    ].filter(Boolean).join(' ');
                    let best = null;
                    let bestScore = 0;
                    for (const el of nodes) {
                        if (!visible(el)) continue;
                        const haystack = normalize(textOf(el));
                        const score = wanted.reduce((total, word) => (
                            total + (haystack.includes(word) ? 1 : 0)
                        ), 0);
                        const tag = el.tagName.toLowerCase();
                        const bonus = ['button', 'a', 'input', 'select', 'textarea'].includes(tag)
                            || el.getAttribute('role')
                            ? 0.5
                            : 0;
                        if (score + bonus > bestScore) {
                            best = el;
                            bestScore = score + bonus;
                        }
                    }
                    if (!best || bestScore === 0) return '';
                    best.setAttribute('data-lbcc-click-target', token);
                    return `[data-lbcc-click-target="${token}"]`;
                }""",
                {"words": words, "token": token},
            )
            if found:
                self._log("safe_click", {
                    "selector": selector,
                    "step": "fallback_click_encontrado",
                    "fallback_selector": found,
                    "words": words,
                })
            return found or ""
        except Exception as e:
            self._log("safe_click", {
                "selector": selector,
                "step": "fallback_click_falhou",
                "error": str(e)[:250],
            })
            return ""

    async def click(self, selector: str) -> dict:
        return await self.safe_click(selector)

    async def safe_click(self, selector: str, timeout: int = 4500, retries: int = 1) -> dict:
        async with self._lock:
            last_error = ""
            for attempt_no in range(1, retries + 1):
                await self.wait_for_react()
                candidates = self._selector_attempts(selector)
                fallback = await self._fallback_click_selector(selector)
                if fallback:
                    candidates.append(fallback)
                for sel in candidates:
                    try:
                        locator = self._page.locator(sel).first
                        candidate_timeout = min(timeout, 1800) if sel == selector and len(candidates) > 1 else timeout
                        await locator.wait_for(state="attached", timeout=candidate_timeout)
                        self._log("safe_click", {"selector": sel, "step": "elemento_encontrado", "attempt": attempt_no})
                        await locator.scroll_into_view_if_needed(timeout=candidate_timeout)
                        await locator.wait_for(state="visible", timeout=candidate_timeout)
                        enabled = await locator.is_enabled(timeout=2_000)
                        if not enabled:
                            last_error = "Campo desabilitado"
                            self._log("safe_click", {"selector": sel, "step": "campo_desabilitado", "attempt": attempt_no})
                            continue
                        overlay = await self._overlay_info(locator)
                        if overlay.get("blocked"):
                            self._log("safe_click", {"selector": sel, "step": "overlay_detectado", "overlay": overlay})
                        await self.highlight_element(locator, f"click:{sel[:60]}")
                        if not overlay.get("blocked"):
                            await locator.hover(timeout=min(timeout, 1200))
                        try:
                            await locator.click(timeout=min(timeout, 2500))
                        except Exception as normal_error:
                            self._log("safe_click", {
                                "selector": sel,
                                "step": "click_normal_falhou_force",
                                "error": str(normal_error)[:250],
                            })
                            await locator.click(timeout=min(timeout, 2500), force=True)
                        await self.wait_for_react()
                        self._log("safe_click", {"selector": sel, "step": "click_ok", "attempt": attempt_no})
                        return {"ok": True, "selector": sel, "attempt": attempt_no, "overlay": overlay}
                    except Exception as e:
                        last_error = str(e)
                        step = "elemento_invisivel" if "visible" in last_error.lower() else "falha"
                        self._log("safe_click", {"selector": sel, "step": step, "error": last_error[:250]})
                ss = await self.screenshot(f"safe_click_retry_{attempt_no}")
                self._log("safe_click_retry", {
                    "selector": selector,
                    "attempt": attempt_no,
                    "error": last_error[:300],
                    "screenshot": ss.get("filename"),
                })
                await asyncio.sleep(0.6)
            return {"ok": False, "error": last_error[:300]}

    async def click_text(self, text: str, timeout: int = 10000) -> dict:
        wanted = (text or "").strip()
        if not wanted:
            return {"ok": False, "error": "Texto vazio"}
        attempts = [
            f"role=button[name='{wanted}']",
            f"role=link[name='{wanted}']",
            f"text={wanted}",
            f"a:has-text('{wanted}')",
            f"button:has-text('{wanted}')",
            f"[role=button]:has-text('{wanted}')",
            f"[role=menuitem]:has-text('{wanted}')",
            f"span:has-text('{wanted}')",
        ]
        last_error = ""
        for sel in attempts:
            result = await self.safe_click(sel, timeout=timeout, retries=1)
            if result.get("ok"):
                self._log("click_text", {"text": wanted, "selector": result.get("selector", sel)})
                return {"ok": True, "text": wanted, "selector": result.get("selector", sel)}
            last_error = result.get("error", "")
        return {"ok": False, "text": wanted, "error": last_error[:300]}

    async def fill(self, selector: str, value: str) -> dict:
        return await self.safe_fill(selector, value)

    async def _safe_fill_selector_once(self, selector: str, value: str, timeout: int, attempt_no: int) -> dict:
        locator = self._page.locator(selector).first
        await locator.wait_for(state="attached", timeout=timeout)
        self._log("safe_fill", {"selector": selector, "step": "elemento_encontrado", "attempt": attempt_no})
        await locator.scroll_into_view_if_needed(timeout=timeout)
        await locator.wait_for(state="visible", timeout=timeout)
        self._log("safe_fill", {"selector": selector, "step": "elemento_visivel", "attempt": attempt_no})
        enabled = await locator.is_enabled(timeout=2_000)
        if not enabled:
            self._log("safe_fill", {"selector": selector, "step": "campo_desabilitado", "attempt": attempt_no})
            raise RuntimeError("Campo desabilitado")
        overlay = await self._overlay_info(locator)
        if overlay.get("blocked"):
            self._log("safe_fill", {"selector": selector, "step": "overlay_detectado", "overlay": overlay})
        await self.highlight_element(locator, f"fill:{selector[:60]}")
        await locator.click(timeout=min(timeout, 1800))
        try:
            await locator.fill("", timeout=min(timeout, 1800))
        except Exception:
            await self._page.keyboard.press("Control+A")
            await self._page.keyboard.press("Backspace")
        await locator.fill(value, timeout=min(timeout, 2200))
        filled = await self._locator_value(locator)
        if not self._values_match(value, filled):
            self._log("safe_fill", {
                "selector": selector,
                "step": "valor_divergente",
                "expected_len": len(value),
                "actual_len": len(filled),
            })
            raise RuntimeError(
                f"Valor preenchido divergente: esperado {len(value)} chars, obtido {len(filled)} chars"
            )
        self._log("safe_fill", {
            "selector": selector,
            "step": "valor_preenchido",
            "value_len": len(value),
            "attempt": attempt_no,
        })
        await self.wait_for_react()
        return {"ok": True, "selector": selector, "value_len": len(value), "attempt": attempt_no}

    async def safe_fill(self, selector: str, value: str, timeout: int = 4500, retries: int = 1) -> dict:
        async with self._lock:
            last_error = ""
            for attempt_no in range(1, retries + 1):
                await self.wait_for_react()
                candidates = self._fill_selector_attempts(selector)
                fallback = await self._fallback_input_selector(selector)
                if fallback:
                    candidates.append(fallback)
                for candidate in candidates:
                    try:
                        candidate_timeout = min(timeout, 1500) if candidate == selector and len(candidates) > 1 else min(timeout, 2600)
                        result = await self._safe_fill_selector_once(candidate, value, candidate_timeout, attempt_no)
                        if candidate != selector:
                            result["original_selector"] = selector
                        return result
                    except Exception as e:
                        last_error = str(e)
                        step = "elemento_invisivel" if "visible" in last_error.lower() else "falha"
                        self._log("safe_fill", {
                            "selector": candidate,
                            "original_selector": selector,
                            "step": step,
                            "error": last_error[:250],
                        })
                ss = await self.screenshot(f"safe_fill_retry_{attempt_no}")
                self._log("safe_fill_retry", {
                    "selector": selector,
                    "attempt": attempt_no,
                    "error": last_error[:300],
                    "screenshot": ss.get("filename"),
                })
                await asyncio.sleep(0.6)
            return {"ok": False, "selector": selector, "error": last_error[:300]}

    async def fill_login_credentials(self, email: str, password: str, submit: bool = True) -> dict:
        async with self._lock:
            if not email or not password:
                return {"ok": False, "error": "Email ou senha ausente nas credenciais locais"}

            email_selectors = [
                "input[type='email']",
                "input[name*='email']",
                "input[name*='usuario']",
                "input[name*='user']",
                "input[id*='email']",
                "input[id*='usuario']",
                "input[id*='user']",
                "input[placeholder*='email']",
                "input[placeholder*='E-mail']",
                "input[placeholder*='usuário']",
                "input[placeholder*='Usuario']",
                "input[type='text']",
            ]
            password_selectors = [
                "input[type='password']",
                "input[name*='senha']",
                "input[name*='password']",
                "input[id*='senha']",
                "input[id*='password']",
            ]

            email_selector = await self._fill_first_visible(email_selectors, email)
            password_selector = await self._fill_first_visible(password_selectors, password)

            if not email_selector or not password_selector:
                return {
                    "ok": False,
                    "error": "Nao encontrei campos visiveis de email/senha",
                    "email_filled": bool(email_selector),
                    "password_filled": bool(password_selector),
                }

            clicked_selector = ""
            if submit:
                submit_selectors = [
                    "button[type='submit']",
                    "input[type='submit']",
                    "button:has-text('Entrar')",
                    "button:has-text('Acessar')",
                    "button:has-text('Login')",
                    "text=Entrar",
                    "text=Acessar",
                ]
                for selector in submit_selectors:
                    try:
                        locator = self._page.locator(selector).first
                        await locator.click(timeout=5_000)
                        clicked_selector = selector
                        break
                    except Exception:
                        continue

            try:
                await self._page.wait_for_load_state("domcontentloaded", timeout=10_000)
            except Exception:
                pass

            result = {
                "ok": True,
                "email_selector": email_selector,
                "password_selector": password_selector,
                "submitted": bool(clicked_selector),
                "submit_selector": clicked_selector,
            }
            self._log("fill_login_credentials", {
                "email_selector": email_selector,
                "password_selector": password_selector,
                "submitted": bool(clicked_selector),
            })
            return result

    async def _on_frame_navigated(self, frame):
        try:
            if frame == self._page.main_frame:
                await self.record_teaching_action("navigate", {"url": frame.url}, {"ok": True})
        except Exception:
            pass

    async def _ensure_teaching_binding(self):
        if not self._ctx or self._teaching_binding_ready:
            return
        try:
            await self._ctx.expose_binding("__lbccTeachRecord", self._on_teach_binding)
            self._teaching_binding_ready = True
        except Exception:
            self._teaching_binding_ready = True

    async def _on_teach_binding(self, source, payload):
        try:
            await self._record_teaching_step(payload or {})
        except Exception:
            pass

    async def _install_teaching_hooks(self, page: Page):
        try:
            await page.add_init_script(self._teaching_observer_script())
        except Exception:
            pass
        try:
            await page.evaluate(self._teaching_observer_script())
        except Exception:
            pass

    def _teaching_observer_script(self) -> str:
        return r"""(() => {
            if (window.__lbccTeachInstalled) return;
            window.__lbccTeachInstalled = true;
            const clean = value => (value || '').replace(/\s+/g, ' ').trim();
            const cssEscape = value => {
                try { return CSS.escape(value); } catch (_) { return String(value).replace(/"/g, '\\"'); }
            };
            const textOf = el => clean(
                el.innerText || el.textContent || el.value || el.placeholder ||
                el.getAttribute('aria-label') || el.title || ''
            ).slice(0, 140);
            const selectorFor = el => {
                if (!el || !el.tagName) return '';
                const tag = el.tagName.toLowerCase();
                const testid = el.getAttribute('data-testid');
                if (testid) return `[data-testid="${cssEscape(testid)}"]`;
                if (el.id) return `#${cssEscape(el.id)}`;
                const name = el.getAttribute('name');
                if (name) return `${tag}[name="${cssEscape(name)}"]`;
                const aria = el.getAttribute('aria-label');
                if (aria) return `${tag}[aria-label="${cssEscape(aria)}"]`;
                const placeholder = el.getAttribute('placeholder');
                if (placeholder) return `${tag}[placeholder="${cssEscape(placeholder)}"]`;
                const txt = textOf(el);
                if (txt && ['button', 'a'].includes(tag)) return `${tag}:has-text("${cssEscape(txt.slice(0, 60))}")`;
                if (txt && el.getAttribute('role') === 'button') return `[role=button]:has-text("${cssEscape(txt.slice(0, 60))}")`;
                return tag;
            };
            const send = payload => {
                if (!window.__lbccTeachRecord) return;
                payload.url = location.href;
                payload.title = document.title;
                window.__lbccTeachRecord(payload).catch(() => {});
            };
            const inputTimers = new WeakMap();
            document.addEventListener('click', event => {
                const el = event.target && event.target.closest
                    ? event.target.closest('button,a,[role=button],[role=link],[role=menuitem],input,select,textarea,label')
                    : event.target;
                if (!el) return;
                const tag = (el.tagName || '').toLowerCase();
                const type = tag === 'a' ? 'click_text' : 'click';
                send({type, selector: selectorFor(el), text: textOf(el), href: el.href || ''});
            }, true);
            document.addEventListener('input', event => {
                const el = event.target;
                if (!el || !['INPUT', 'TEXTAREA'].includes(el.tagName)) return;
                clearTimeout(inputTimers.get(el));
                inputTimers.set(el, setTimeout(() => {
                    const inputType = (el.getAttribute('type') || '').toLowerCase();
                    send({
                        type: 'fill',
                        selector: selectorFor(el),
                        text: textOf(el),
                        value: inputType === 'password' ? '{password}' : (el.value || '')
                    });
                }, 600));
            }, true);
            document.addEventListener('change', event => {
                const el = event.target;
                if (!el) return;
                if (el.tagName === 'SELECT') {
                    send({type: 'select', selector: selectorFor(el), value: el.value || '', text: textOf(el)});
                }
                if (el.tagName === 'INPUT' && (el.getAttribute('type') || '').toLowerCase() === 'file') {
                    send({
                        type: 'upload',
                        selector: selectorFor(el),
                        file: Array.from(el.files || []).map(file => file.name).join(', ')
                    });
                }
            }, true);
        })()"""

    async def _fill_first_visible(self, selectors: list[str], value: str) -> str:
        for selector in selectors:
            try:
                locator = self._page.locator(selector).first
                await locator.wait_for(state="visible", timeout=3_000)
                await locator.fill(value, timeout=5_000)
                return selector
            except Exception:
                continue
        return ""

    async def key(self, key: str) -> dict:
        try:
            page = await self._ensure_active_page()
            await page.keyboard.press(key)
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

    async def hub_task_counts_by_responsible(self, person: str) -> dict:
        try:
            data = await self._page.evaluate(
                """async ({person}) => {
                    const norm = s => (s || '')
                        .normalize('NFD')
                        .replace(/[\\u0300-\\u036f]/g, '')
                        .toLowerCase();
                    async function rawJson(url, opts = {}) {
                        const response = await fetch(url, {credentials: 'include', ...opts});
                        const text = await response.text();
                        let data = null;
                        try { data = JSON.parse(text); } catch (_) { data = text; }
                        return {ok: response.ok, status: response.status, data};
                    }
                    const tokenResponse = await rawJson('/api/auth/session-token');
                    const token = tokenResponse.data && tokenResponse.data.token;
                    if (!token) {
                        return {ok: false, error: 'Nao consegui obter token de sessao do Hub.'};
                    }
                    const headers = {Authorization: `Bearer ${token}`};
                    const getJson = url => rawJson(url, {headers});
                    const colaboradoresResponse = await getJson('/api/colaboradores/listar');
                    const colaboradores = Array.isArray(colaboradoresResponse.data)
                        ? colaboradoresResponse.data
                        : [];
                    const target = norm(person);
                    const matchedPeople = colaboradores.filter(c => norm(c.name).includes(target));
                    const targetIds = new Set(matchedPeople.map(c => c.id).filter(Boolean));
                    if (!targetIds.size) {
                        return {
                            ok: false,
                            error: `Responsavel nao encontrado: ${person}`,
                            colaboradores: colaboradores.map(c => c.name).filter(Boolean)
                        };
                    }

                    const all = [];
                    const pages = [];
                    const limit = 50;
                    let apiTotal = null;
                    for (let skip = 0; skip < 5000; skip += limit) {
                        const response = await getJson(`/api/tarefas?limit=${limit}&skip=${skip}&include_meta=true`);
                        let items = [];
                        if (Array.isArray(response.data)) {
                            items = response.data;
                        } else if (response.data && Array.isArray(response.data.items)) {
                            items = response.data.items;
                            apiTotal = response.data.total ?? response.data.count ?? response.data.meta?.total ?? apiTotal;
                        }
                        pages.push({skip, status: response.status, ok: response.ok, items: items.length});
                        if (!response.ok) break;
                        all.push(...items);
                        if (items.length < limit) break;
                    }

                    const responsibleFields = task => Object.entries(task || {}).filter(([key]) => (
                        /respons|colaborador|usuario|tecnico|owner|assigned/i.test(key)
                    ));
                    const valueText = value => {
                        if (value == null) return '';
                        if (typeof value === 'object') return JSON.stringify(value);
                        return String(value);
                    };
                    const isTargetTask = task => responsibleFields(task).some(([_, value]) => (
                        targetIds.has(String(value)) || norm(valueText(value)).includes(target)
                    ));
                    const statusOf = task => String(
                        task.status || task.situacao || task.estado || 'sem_status'
                    ).toLowerCase();
                    const tasks = all.filter(isTargetTask);
                    const byStatus = {};
                    const globalByStatus = {};
                    for (const task of tasks) byStatus[statusOf(task)] = (byStatus[statusOf(task)] || 0) + 1;
                    for (const task of all) globalByStatus[statusOf(task)] = (globalByStatus[statusOf(task)] || 0) + 1;
                    const sample = tasks.slice(0, 20).map(task => ({
                        id: task.id,
                        protocolo: task.protocolo,
                        titulo: task.titulo,
                        status: task.status,
                        responsavel_id: task.responsavel_id || task.colaborador_id || task.usuario_id || task.assigned_to || '',
                        responsavel_nome: task.responsavel_nome || task.colaborador_nome || task.usuario_nome || task.tecnico_nome || '',
                        cliente: task.cliente_nome || task.razao_social || (task.cliente && task.cliente.razao_social) || ''
                    }));
                    return {
                        ok: true,
                        person,
                        matched_responsibles: matchedPeople.map(c => ({
                            id: c.id,
                            name: c.name,
                            role: c.role || ''
                        })),
                        fetched_total: all.length,
                        api_total: apiTotal,
                        matched_total: tasks.length,
                        by_status: byStatus,
                        global_by_status: globalByStatus,
                        open_count: (byStatus.pendente || 0)
                            + (byStatus.em_andamento || 0)
                            + (byStatus.aguardando_aprovacao || 0),
                        pending_count: byStatus.pendente || 0,
                        in_progress_count: byStatus.em_andamento || 0,
                        awaiting_approval_count: byStatus.aguardando_aprovacao || 0,
                        done_count: byStatus.concluido || 0,
                        rejected_count: byStatus.reprovado || 0,
                        canceled_count: byStatus.cancelado || 0,
                        pages,
                        sample
                    };
                }""",
                {"person": person},
            )
            self._log("hub_task_counts_by_responsible", {
                "person": person,
                "ok": data.get("ok", False),
                "matched_total": data.get("matched_total"),
                "by_status": data.get("by_status", {}),
            })
            return data
        except Exception as e:
            self._log("hub_task_counts_by_responsible", {
                "person": person,
                "ok": False,
                "error": str(e)[:300],
            })
            return {"ok": False, "error": str(e)}

    async def task_page_summary(self) -> dict:
        try:
            data = await self._page.evaluate("""() => {
                const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                const visible = el => {
                    const style = window.getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && r.width > 8
                        && r.height > 8;
                };
                const text = clean(document.body?.innerText || '');
                const counters = {};
                for (const match of text.matchAll(/([A-Za-zÀ-ÿ ]{3,})\\s*\\((\\d+)\\)/g)) {
                    counters[clean(match[1])] = Number(match[2]);
                }
                const rowSelectors = [
                    'tbody tr',
                    '[role=row]',
                    '[class*=card]',
                    '[class*=task]',
                    '[class*=tarefa]'
                ];
                const seen = new Set();
                const rows = [];
                for (const selector of rowSelectors) {
                    for (const el of Array.from(document.querySelectorAll(selector)).filter(visible)) {
                        const rowText = clean(el.innerText || el.textContent || '');
                        if (rowText.length < 8 || seen.has(rowText)) continue;
                        seen.add(rowText);
                        rows.push(rowText.slice(0, 240));
                    }
                }
                return {
                    text: text.slice(0, 5000),
                    counters,
                    visible_rows: rows.slice(0, 80),
                    visible_row_count: rows.length
                };
            }""")
            self._log("task_page_summary", {
                "url": self._page.url,
                "counters": data.get("counters", {}),
                "visible_row_count": data.get("visible_row_count", 0),
            })
            return {"ok": True, "url": self._page.url, **data}
        except Exception as e:
            return {"ok": False, "error": str(e), "url": self._page.url if self._page else ""}

    async def get_page_context(self) -> dict:
        try:
            data = await self._page.evaluate("""() => {
                const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                const visible = el => {
                    const style = window.getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return style.visibility !== 'hidden'
                        && style.display !== 'none'
                        && r.width > 0
                        && r.height > 0;
                };
                const textOf = el => clean(
                    el.innerText || el.textContent || el.value ||
                    el.placeholder || el.getAttribute('aria-label') || el.title || ''
                );
                const uniq = values => [...new Set(values.map(clean).filter(Boolean))].slice(0, 80);
                const buttons = uniq(Array.from(document.querySelectorAll(
                    'button, input[type=button], input[type=submit], [role=button]'
                )).filter(visible).map(textOf));
                const links = uniq(Array.from(document.querySelectorAll('a, [role=link]'))
                    .filter(visible)
                    .map(el => textOf(el) || el.href));
                const menus = uniq(Array.from(document.querySelectorAll(
                    'nav, aside, [role=menu], [role=menubar], [role=menuitem], .menu, [class*=menu], [class*=nav]'
                )).filter(visible).flatMap(el => clean(el.innerText).split('\\n')));
                return {buttons, links, menus};
            }""")
            return {
                "url": self._page.url,
                "title": await self._page.title(),
                "buttons": data.get("buttons", []),
                "links": data.get("links", []),
                "menus": data.get("menus", []),
            }
        except Exception as e:
            return {"url": "", "title": "", "buttons": [], "links": [], "menus": [], "error": str(e)}

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
