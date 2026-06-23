"""
Procedure backend self-test for Phase 5.

Run:
    python -m backend.tests.procedure baixar_documento
    python test_procedure.py baixar_documento
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.agent.agent import agent
from backend.browser.browser import LOGS_DIR, browser
from backend.procedures import manager as procs


class ProcedureSelfTest:
    def __init__(self, procedure_name: str, variables: dict[str, str], headless: bool = True):
        self.procedure_name = procedure_name
        self.variables = variables
        self.headless = headless
        self.run_id = f"procedure_{procedure_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.stopped_server = False
        self.total_steps = 0
        self.current_step = 0
        self.step_status: dict[int, dict[str, Any]] = {}
        self.events: list[dict[str, Any]] = []
        self.report_path = LOGS_DIR / f"{self.run_id}_report.json"

    async def run(self) -> int:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        proc = procs.get_procedure(self.procedure_name)
        if not proc:
            print(f'Procedimento "{self.procedure_name}" nao encontrado.')
            print("RESULTADO FINAL:")
            print("REPROVADO")
            return 1

        self.total_steps = len(proc.get("steps", []))
        print(f'Testando procedimento: {proc.get("name", self.procedure_name)}')
        print(f"Passos: {self.total_steps}")
        print("")

        try:
            await self._start_browser()
            async for event in agent._replay_v5(proc, "test-procedure", self.run_id, self.variables):
                self._handle_event(event)
        except Exception as exc:
            self._mark_current(False, str(exc), action="exception")
            print(f"ERRO GERAL: {exc}")
        finally:
            await self._write_report()
            try:
                browser.end_execution()
                await browser.stop()
            except Exception:
                pass
            self._restart_backend_server_if_needed()

        self._print_missing_steps()
        approved = self._approved()
        print("")
        print("RESULTADO FINAL:")
        print("APROVADO" if approved else "REPROVADO")
        print(f"Relatorio: {self.report_path}")
        return 0 if approved else 1

    async def _start_browser(self):
        self._stop_conflicting_backend_server()
        try:
            await browser.start(headless=self.headless, profile_name="default")
            browser.begin_execution(self.run_id)
            browser.disable_step_mode()
            browser.resume()
        except Exception as exc:
            msg = str(exc)
            if "ProcessSingleton" in msg or "user data directory" in msg or "lock" in msg.lower():
                raise RuntimeError(
                    "Nao consegui abrir o Chrome persistente. O backend esta segurando o perfil."
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
            print("Servidor backend pausado temporariamente para liberar o perfil Chrome.")
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
            print("Servidor backend reiniciado em http://localhost:8000.")
        except Exception as exc:
            print(f"Nao consegui reiniciar o servidor automaticamente: {exc}")

    def _handle_event(self, event: dict[str, Any]):
        self.events.append(event)
        event_type = event.get("type")
        if event_type == "system":
            match = re.match(r"\[(\d+)/(\d+)\]\s+(.+)", event.get("text", ""))
            if match:
                previous = self.current_step
                if previous and previous not in self.step_status:
                    self._mark_step(previous, False, "Sem resultado antes do proximo passo.")
                self.current_step = int(match.group(1))
                self.total_steps = int(match.group(2))
                self.step_status.setdefault(self.current_step, {
                    "ok": None,
                    "action": match.group(3),
                    "error": "",
                    "corrections": [],
                })
            elif "Auto-correcao" in event.get("text", "") or "Selector corrigido" in event.get("text", ""):
                if self.current_step:
                    self.step_status.setdefault(self.current_step, {"ok": None, "corrections": []})
                    self.step_status[self.current_step].setdefault("corrections", []).append(event.get("text", ""))
            return

        if event_type == "result":
            ok = bool(event.get("ok"))
            self._mark_current(ok, event.get("error", ""), action=event.get("action", "result"))
            if ok:
                print(f"PASSO {self.current_step}: OK")
            return

        if event_type in ("error", "timeout", "stopped"):
            self._mark_current(False, event.get("text", event_type), action=event_type)
            if self.current_step:
                print(f"PASSO {self.current_step}: ERRO - {event.get('text', event_type)}")

    def _mark_current(self, ok: bool, error: str = "", action: str = ""):
        if not self.current_step:
            self.current_step = 1
        self._mark_step(self.current_step, ok, error, action)

    def _mark_step(self, step: int, ok: bool, error: str = "", action: str = ""):
        current = self.step_status.setdefault(step, {"ok": None, "corrections": []})
        if current.get("ok") is True and not ok:
            return
        current["ok"] = ok
        current["error"] = error
        if action:
            current["action"] = action

    def _print_missing_steps(self):
        for i in range(1, self.total_steps + 1):
            status = self.step_status.get(i)
            if not status:
                self.step_status[i] = {"ok": False, "error": "Passo nao executado.", "corrections": []}
                print(f"PASSO {i}: ERRO - Passo nao executado.")
            elif status.get("ok") is False and not status.get("printed"):
                print(f"PASSO {i}: ERRO - {status.get('error') or 'Falha'}")

    def _approved(self) -> bool:
        if self.total_steps == 0:
            return False
        return all(self.step_status.get(i, {}).get("ok") is True for i in range(1, self.total_steps + 1))

    async def _write_report(self):
        report = {
            "title": "FASE 5 PROCEDURE TEST REPORT",
            "procedure": self.procedure_name,
            "run_id": self.run_id,
            "started_at": datetime.now().isoformat(),
            "total_steps": self.total_steps,
            "steps": self.step_status,
            "approved": self._approved(),
            "events": self.events[-200:],
        }
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        procs.record_execution(self.procedure_name, "aprovado" if self._approved() else "reprovado")


def parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Testa um procedimento salvo pelo backend.")
    parser.add_argument("procedure", help="Nome do procedimento, sem .json")
    parser.add_argument("--headed", action="store_true", help="Abre o navegador visivel.")
    parser.add_argument("--var", action="append", default=[], help="Variavel no formato chave=valor.")
    return parser.parse_args(argv)


def parse_vars(items: list[str]) -> dict[str, str]:
    result = {}
    for item in items:
        if "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runner = ProcedureSelfTest(args.procedure, parse_vars(args.var), headless=not args.headed)
    return asyncio.run(runner.run())


if __name__ == "__main__":
    raise SystemExit(main())
