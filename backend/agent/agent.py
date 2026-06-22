"""
Agente — loop LLM + browser.
Fase 2: replay de procedimentos, pausa, passo-a-passo, aprovação humana, novos comandos.
"""
import asyncio
import json
import time
import uuid
from typing import AsyncGenerator

from openai import AsyncOpenAI

from backend.browser.browser import browser
from backend.db import database as db
from backend.procedures import manager as procs

TIMEOUT_SECONDS = 180
MAX_RETRIES     = 3
RETRY_DELAY_S   = 2

SYSTEM = """Você é o Agente Operacional LBCC.
Você controla um navegador Chrome real para executar tarefas de escritório contábil.
O Chrome mantém sessões, cookies e logins entre execuções.

## Ferramentas disponíveis — responda SEMPRE com JSON válido:

{"action": "navigate",     "url": "https://..."}
{"action": "click",        "selector": "css"}
{"action": "fill",         "selector": "css", "value": "texto"}
{"action": "key",          "key": "Enter"}
{"action": "scroll",       "direction": "down", "amount": 500}
{"action": "wait",         "ms": 1000}
{"action": "wait_selector","selector": "css", "timeout": 15000}
{"action": "select",       "selector": "css", "value": "opcao"}
{"action": "hover",        "selector": "css"}
{"action": "upload",       "selector": "css", "file": "nome_arquivo.pdf"}
{"action": "screenshot"}
{"action": "done",         "message": "Tarefa concluída: ..."}
{"action": "ask",          "message": "Situação: X. Deseja continuar?"}
{"action": "error",        "message": "Não consegui porque ..."}

## Regras
- O Chrome mantém logins — se já fez login antes, a sessão pode estar ativa.
- Verifique o estado atual antes de agir.
- Use seletores CSS precisos: #id, [name=x], button, input[type=submit].
- Para texto de botões use: text="Emitir" ou :has-text('Emitir').
- Nunca invente dados. Se faltar informação, use ask.
- Responda APENAS com JSON. Sem texto fora do JSON.
- Máximo 40 iterações por tarefa.
"""


class Agent:
    def __init__(self):
        import os
        from dotenv import load_dotenv
        load_dotenv()
        self._client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        # Aguarda aprovação: guarda o canal de comunicação
        self._pending_approval_resolve = None

    async def run(
        self,
        user_message: str,
        conv_id: str,
        exec_id: str,
        history: list[dict],
        variables: dict = None,
    ) -> AsyncGenerator[dict, None]:

        deadline = time.time() + TIMEOUT_SECONDS
        variables = variables or {}

        # ── Tentar replay de procedimento ──────────────────────────────────────
        proc = self._find_procedure(user_message)
        if proc:
            yield {"type": "system",
                   "text": f"📋 Procedimento encontrado: '{proc['name']}'. Executando replay..."}
            async for event in self._replay_procedure(proc, conv_id, exec_id, variables):
                yield event
            return

        # ── Loop LLM normal ────────────────────────────────────────────────────
        messages = [{"role": "system", "content": SYSTEM}]
        for h in history[-16:]:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_message})

        for iteration in range(40):

            # Verificar STOP
            if browser.should_stop:
                yield {"type": "stopped", "text": "Execução interrompida pelo usuário."}
                return

            # Verificar TIMEOUT
            if time.time() > deadline:
                yield {"type": "timeout",
                       "text": f"Execução interrompida por timeout ({TIMEOUT_SECONDS}s)."}
                return

            # Aguardar se pausado
            if browser.is_paused:
                yield {"type": "paused", "text": "Execução pausada. Aguardando retomada..."}
                await browser.wait_if_paused()
                if browser.should_stop:
                    yield {"type": "stopped", "text": "Interrompido durante pausa."}
                    return
                yield {"type": "resumed", "text": "Execução retomada."}

            # Estado da página
            state = await browser.page_state()
            state_text = (
                f"\n[Página atual]\n"
                f"URL: {state.get('url','?')}\n"
                f"Título: {state.get('title','?')}\n"
                f"Elementos ({len(state.get('elements',[]))}): "
                f"{json.dumps(state.get('elements',[])[:12], ensure_ascii=False)}\n"
                f"Texto: {state.get('body','')[:800]}"
            )

            ctx_messages = messages.copy()
            ctx_messages.append({"role": "user",
                                  "content": f"[iteração {iteration+1}]{state_text}"})

            # Chamar LLM com retry
            raw = None
            for attempt in range(MAX_RETRIES):
                try:
                    resp = await self._client.chat.completions.create(
                        model="gpt-4o",
                        messages=ctx_messages,
                        temperature=0.1,
                        max_tokens=512,
                        response_format={"type": "json_object"},
                    )
                    raw = resp.choices[0].message.content.strip()
                    break
                except Exception as e:
                    if attempt < MAX_RETRIES - 1:
                        yield {"type": "retry",
                               "text": f"Tentativa {attempt+1} falhou: {e}. Tentando..."}
                        await asyncio.sleep(RETRY_DELAY_S)
                    else:
                        yield {"type": "error",
                               "text": f"LLM falhou após {MAX_RETRIES} tentativas: {e}"}
                        return

            try:
                cmd = json.loads(raw)
            except Exception:
                yield {"type": "error", "text": f"JSON inválido: {raw[:200]}"}
                return

            # Executar comando
            async for event in self._execute_cmd(cmd, conv_id, exec_id):
                yield event

            action = cmd.get("action", "")
            if action in ("done", "ask", "error"):
                return

            messages.append({"role": "assistant", "content": raw})
            result_summary = f"[resultado] ação={action} ok={True}"
            messages.append({"role": "user", "content": result_summary})

        yield {"type": "error", "text": "Limite de 40 iterações atingido."}

    async def _replay_procedure(
        self, proc: dict, conv_id: str, exec_id: str, variables: dict
    ) -> AsyncGenerator[dict, None]:
        """Executa um procedimento salvo passo a passo."""
        steps = procs.apply_variables(proc.get("steps", []), variables)
        total = len(steps)

        for i, step in enumerate(steps):
            if browser.should_stop:
                yield {"type": "stopped", "text": "Replay interrompido."}
                return

            # Modo passo a passo
            if browser.is_step_mode:
                yield {"type": "step_waiting",
                       "text": f"Passo {i+1}/{total}: {step.get('action')} — aguardando aprovação"}
                await browser.wait_for_step()
                if browser.should_stop:
                    yield {"type": "stopped", "text": "Interrompido no passo a passo."}
                    return

            yield {"type": "system",
                   "text": f"[{i+1}/{total}] {step.get('action')} {step.get('url') or step.get('selector','') or step.get('value','') or ''}"}

            async for event in self._execute_cmd(step, conv_id, exec_id):
                yield event

            action = step.get("action", "")
            if action in ("ask", "error"):
                return

        ss = await browser.screenshot("replay_final")
        if ss.get("ok") and ss.get("b64"):
            yield {"type": "screenshot", "b64": ss["b64"], "label": "Replay concluído"}

        yield {"type": "done", "text": f"Procedimento '{proc['name']}' concluído com {total} passos."}

    async def _execute_cmd(
        self, cmd: dict, conv_id: str, exec_id: str
    ) -> AsyncGenerator[dict, None]:
        """Executa um único comando e gera eventos."""
        action = cmd.get("action", "")

        # ── Terminais ─────────────────────────────────────────────────────────
        if action == "done":
            ss = await browser.screenshot("resultado_final")
            if ss.get("ok") and ss.get("b64"):
                yield {"type": "screenshot", "b64": ss["b64"], "label": "Resultado final"}
            yield {"type": "done", "text": cmd.get("message", "Concluído.")}
            await db.save_action_log(str(uuid.uuid4()), conv_id, exec_id,
                                     "done", {}, {"message": cmd.get("message")}, True)
            return

        if action == "ask":
            msg = cmd.get("message", "")
            # Pede aprovação real ao usuário
            yield {"type": "ask", "text": msg}
            approved = await browser.request_approval(msg)
            if not approved:
                yield {"type": "stopped", "text": "Ação rejeitada pelo usuário."}
            else:
                yield {"type": "system", "text": "Aprovado. Continuando..."}
            return

        if action == "error":
            yield {"type": "error", "text": cmd.get("message", "Erro.")}
            return

        # ── Ações do browser ──────────────────────────────────────────────────
        yield {"type": "action", **cmd}

        result = {}
        label  = action

        if action == "navigate":
            label  = f"nav_{cmd.get('url','')[:30]}"
            result = await browser.navigate(cmd.get("url", ""))
        elif action == "click":
            label  = f"click_{cmd.get('selector','')[:25]}"
            result = await browser.click(cmd.get("selector", ""))
        elif action == "fill":
            label  = f"fill_{cmd.get('selector','')[:20]}"
            result = await browser.fill(cmd.get("selector",""), cmd.get("value",""))
        elif action == "key":
            result = await browser.key(cmd.get("key", ""))
        elif action == "scroll":
            result = await browser.scroll(cmd.get("direction","down"), cmd.get("amount",500))
        elif action == "wait":
            result = await browser.wait(cmd.get("ms", 1000))
        elif action == "wait_selector":
            result = await browser.wait_selector(cmd.get("selector",""), cmd.get("timeout",15000))
        elif action == "select":
            result = await browser.select_option(cmd.get("selector",""), cmd.get("value",""))
        elif action == "hover":
            result = await browser.hover(cmd.get("selector",""))
        elif action == "upload":
            result = await browser.upload_file(cmd.get("selector",""), cmd.get("file",""))
        elif action == "screenshot":
            ss = await browser.screenshot("manual")
            if ss.get("ok") and ss.get("b64"):
                yield {"type": "screenshot", "b64": ss["b64"], "label": "Manual"}
            result = {"ok": ss.get("ok", False)}
        else:
            result = {"ok": False, "error": f"Ação desconhecida: {action}"}

        yield {"type": "result", **result}

        # Screenshot automático (exceto wait e scroll)
        if action not in ("screenshot", "wait", "scroll", "hover"):
            ss = await browser.screenshot(label)
            if ss.get("ok") and ss.get("b64"):
                yield {"type": "screenshot", "b64": ss["b64"], "label": label}

        # Log
        await db.save_action_log(str(uuid.uuid4()), conv_id, exec_id,
                                 action, cmd, result, result.get("ok", False))

    def _find_procedure(self, message: str) -> Optional[dict]:
        """Verifica se a mensagem corresponde a um procedimento salvo."""
        all_procs = procs.list_procedures()
        msg_lower = message.lower()
        for p in all_procs:
            name = p["name"].replace("_", " ").lower()
            desc = p.get("description", "").lower()
            if name in msg_lower or (desc and desc in msg_lower):
                return procs.get_procedure(p["name"])
        return None


from typing import Optional
agent = Agent()
