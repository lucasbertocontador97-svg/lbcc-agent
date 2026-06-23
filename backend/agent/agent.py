"""
Agente — Fase 3: abas, downloads, uploads, login persistente.
"""
import asyncio
import json
import time
import uuid
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI

from backend.browser.browser import browser
from backend.db import database as db
from backend.procedures import manager as procs

TIMEOUT_SECONDS = 180
MAX_RETRIES     = 3
RETRY_DELAY_S   = 2

SYSTEM = """Você é o Agente Operacional LBCC.
Você controla um navegador Chrome real. Cookies e logins são mantidos entre sessões.

## Ferramentas — responda SEMPRE com JSON válido:

{"action": "navigate",      "url": "https://..."}
{"action": "click",         "selector": "texto ou css"}
{"action": "fill",          "selector": "css", "value": "texto"}
{"action": "key",           "key": "Enter"}
{"action": "scroll",        "direction": "down", "amount": 500}
{"action": "wait",          "ms": 1000}
{"action": "wait_selector", "selector": "css", "timeout": 15000}
{"action": "select",        "selector": "css", "value": "opcao"}
{"action": "upload",        "selector": "css", "file": "nome.pdf"}
{"action": "download",      "url": "https://...", "filename": "arquivo.pdf"}
{"action": "screenshot"}
{"action": "new_tab",       "url": "https://..."}
{"action": "switch_tab",    "index": 0}
{"action": "close_tab",     "index": 0}
{"action": "list_tabs"}
{"action": "done",          "message": "Tarefa concluída."}
{"action": "ask",           "message": "Preciso de informação X para continuar."}
{"action": "error",         "message": "Não consegui porque..."}

## Como encontrar elementos na página

Você recebe a lista de elementos visíveis com text, id, href, cls.
Use essas informações para construir o seletor correto:

- Se o elemento tem id → use #id
- Se é um link com texto → use o próprio texto: "Documentos", "Financeiro", "Clientes"
- Se é botão com texto → use o texto: "Salvar", "Confirmar", "Baixar"
- O click inteligente testa automaticamente múltiplos seletores
- NUNCA invente seletores — use o que está listado nos elementos da página

## Estratégia de navegação

1. Receba o comando do usuário
2. Veja a lista de elementos visíveis no estado atual
3. Identifique qual elemento corresponde ao pedido (pelo texto ou href)
4. Use click com o texto exato do elemento
5. Aguarde a página carregar e tire screenshot
6. Se chegou onde queria → done. Se não → continue.

## Regras
- Não repita a mesma ação que falhou. Tente texto diferente.
- Não tire screenshot sem antes executar uma ação.
- Se já concluiu a tarefa, use done imediatamente.
- Nunca invente dados. Use ask se faltar informação.
- Responda APENAS com JSON. Máximo 40 iterações.
"""


class Agent:
    def __init__(self):
        import os
        from dotenv import load_dotenv
        load_dotenv()
        self._client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    async def run(
        self,
        user_message: str,
        conv_id: str,
        exec_id: str,
        history: list[dict],
        variables: dict = None,
    ) -> AsyncGenerator[dict, None]:

        deadline  = time.time() + TIMEOUT_SECONDS
        variables = variables or {}

        # ── Tentar replay de procedimento ──────────────────────────────────────
        proc = self._find_procedure(user_message)
        if proc:
            yield {"type": "system",
                   "text": f"📋 Procedimento '{proc['name']}' encontrado. Executando..."}
            async for event in self._replay(proc, conv_id, exec_id, variables):
                yield event
            return

        # ── Comandos diretos de abas ───────────────────────────────────────────
        tab_result = await self._handle_tab_command(user_message)
        if tab_result:
            yield tab_result
            return

        # ── Loop LLM ───────────────────────────────────────────────────────────
        messages = [{"role": "system", "content": SYSTEM}]
        for h in history[-16:]:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_message})

        for iteration in range(40):
            if browser.should_stop:
                yield {"type": "stopped", "text": "Execução interrompida."}
                return

            if time.time() > deadline:
                yield {"type": "timeout",
                       "text": f"Timeout após {TIMEOUT_SECONDS}s."}
                return

            if browser.is_paused:
                yield {"type": "paused", "text": "Pausado. Aguardando..."}
                await browser.wait_if_paused()
                if browser.should_stop:
                    yield {"type": "stopped", "text": "Interrompido."}
                    return
                yield {"type": "resumed", "text": "Retomado."}

            state = await browser.page_state()
            tabs_info = ""
            if state.get("tabs"):
                tabs_info = f"\nAbas abertas: {json.dumps(state['tabs'], ensure_ascii=False)}"

            state_text = (
                f"\n[Estado atual]\n"
                f"URL: {state.get('url','?')}\n"
                f"Título: {state.get('title','?')}\n"
                f"Aba ativa: {state.get('active_tab', 0)}{tabs_info}\n"
                f"Elementos ({len(state.get('elements',[]))}): "
                f"{json.dumps(state.get('elements',[])[:12], ensure_ascii=False)}\n"
                f"Texto: {state.get('body','')[:800]}"
            )

            ctx_msgs = messages.copy()
            ctx_msgs.append({"role": "user",
                             "content": f"[iteração {iteration+1}]{state_text}"})

            # LLM com retry
            raw = None
            for attempt in range(MAX_RETRIES):
                try:
                    resp = await self._client.chat.completions.create(
                        model="gpt-4o",
                        messages=ctx_msgs,
                        temperature=0.1,
                        max_tokens=512,
                        response_format={"type": "json_object"},
                    )
                    raw = resp.choices[0].message.content.strip()
                    break
                except Exception as e:
                    if attempt < MAX_RETRIES - 1:
                        yield {"type": "retry",
                               "text": f"Tentativa {attempt+1}: {e}"}
                        await asyncio.sleep(RETRY_DELAY_S)
                    else:
                        yield {"type": "error", "text": f"LLM falhou: {e}"}
                        return

            try:
                cmd = json.loads(raw)
            except Exception:
                yield {"type": "error", "text": f"JSON inválido: {raw[:200]}"}
                return

            async for event in self._execute_cmd(cmd, conv_id, exec_id):
                yield event

            action = cmd.get("action", "")
            if action in ("done", "ask", "error"):
                return

            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user",
                             "content": f"[resultado] ação={action} concluída"})

        yield {"type": "error", "text": "Limite de 40 iterações atingido."}

    async def _handle_tab_command(self, msg: str) -> Optional[dict]:
        """Interpreta comandos de abas em linguagem natural."""
        m = msg.lower().strip()

        if "listar abas" in m or "list tabs" in m:
            tabs = await browser.list_tabs()
            text = "Abas abertas:\n" + "\n".join(
                f"  {'→' if t['active'] else ' '} [{t['index']}] {t['title']} — {t['url']}"
                for t in tabs
            )
            return {"type": "done", "text": text}

        if "fechar aba" in m:
            import re
            nums = re.findall(r'\d+', m)
            idx = int(nums[0]) if nums else None
            result = await browser.close_tab(idx)
            if result["ok"]:
                return {"type": "done",
                        "text": f"Aba {result['closed_index']} fechada. Ativa: {result['active_index']}"}
            return {"type": "error", "text": result["error"]}

        if "trocar para aba" in m or "mudar para aba" in m or "switch tab" in m:
            import re
            nums = re.findall(r'\d+', m)
            if nums:
                result = await browser.switch_tab(int(nums[0]))
                if result["ok"]:
                    return {"type": "done",
                            "text": f"Trocado para aba {result['index']}: {result['title']}"}
                return {"type": "error", "text": result["error"]}

        if "nova aba" in m or "abrir nova aba" in m:
            import re
            urls = re.findall(r'https?://\S+', msg)
            result = await browser.new_tab(urls[0] if urls else "")
            return {"type": "done", "text": f"Nova aba aberta (índice {result['index']})"}

        return None

    async def _execute_cmd(
        self, cmd: dict, conv_id: str, exec_id: str
    ) -> AsyncGenerator[dict, None]:
        action = cmd.get("action", "")

        if action == "done":
            ss = await browser.screenshot("resultado_final")
            if ss.get("ok") and ss.get("b64"):
                yield {"type": "screenshot", "b64": ss["b64"], "label": "Resultado final"}
            yield {"type": "done", "text": cmd.get("message", "Concluído.")}
            await db.save_action_log(str(uuid.uuid4()), conv_id, exec_id,
                                     "done", {}, {"message": cmd.get("message")}, True)
            return

        if action == "ask":
            yield {"type": "ask", "text": cmd.get("message", "")}
            approved = await browser.request_approval(cmd.get("message", ""))
            if not approved:
                yield {"type": "stopped", "text": "Rejeitado pelo usuário."}
            else:
                yield {"type": "system", "text": "Aprovado. Continuando..."}
            return

        if action == "error":
            yield {"type": "error", "text": cmd.get("message", "Erro.")}
            return

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
        elif action == "download":
            result = await browser.download_file(cmd.get("url",""), cmd.get("filename",""))
            if result.get("ok"):
                yield {"type": "system",
                       "text": f"📥 Download salvo: {result.get('filename')}"}
        elif action == "screenshot":
            ss = await browser.screenshot("manual")
            if ss.get("ok") and ss.get("b64"):
                yield {"type": "screenshot", "b64": ss["b64"], "label": "Manual"}
            result = {"ok": ss.get("ok", False)}
        elif action == "new_tab":
            result = await browser.new_tab(cmd.get("url", ""))
        elif action == "switch_tab":
            result = await browser.switch_tab(cmd.get("index", 0))
        elif action == "close_tab":
            result = await browser.close_tab(cmd.get("index"))
        elif action == "list_tabs":
            tabs = await browser.list_tabs()
            yield {"type": "system",
                   "text": "Abas: " + " | ".join(
                       f"[{t['index']}]{'*' if t['active'] else ''} {t['title'][:30]}"
                       for t in tabs
                   )}
            result = {"ok": True, "tabs": tabs}
        else:
            result = {"ok": False, "error": f"Ação desconhecida: {action}"}

        yield {"type": "result", **result}

        # Screenshot automático
        if action not in ("screenshot", "wait", "scroll", "hover", "list_tabs"):
            ss = await browser.screenshot(label)
            if ss.get("ok") and ss.get("b64"):
                yield {"type": "screenshot", "b64": ss["b64"], "label": label}

        await db.save_action_log(str(uuid.uuid4()), conv_id, exec_id,
                                 action, cmd, result, result.get("ok", False))

    async def _replay(self, proc: dict, conv_id: str,
                      exec_id: str, variables: dict) -> AsyncGenerator[dict, None]:
        steps = procs.apply_variables(proc.get("steps", []), variables)
        total = len(steps)
        for i, step in enumerate(steps):
            if browser.should_stop:
                yield {"type": "stopped", "text": "Replay interrompido."}
                return
            if browser.is_step_mode:
                yield {"type": "step_waiting",
                       "text": f"Passo {i+1}/{total}: {step.get('action')}"}
                await browser.wait_for_step()
                if browser.should_stop:
                    return
            yield {"type": "system",
                   "text": f"[{i+1}/{total}] {step.get('action')} {step.get('url') or step.get('selector','') or ''}"}
            async for event in self._execute_cmd(step, conv_id, exec_id):
                yield event
            if step.get("action") in ("ask", "error"):
                return
        ss = await browser.screenshot("replay_final")
        if ss.get("ok") and ss.get("b64"):
            yield {"type": "screenshot", "b64": ss["b64"], "label": "Concluído"}
        yield {"type": "done",
               "text": f"Procedimento '{proc['name']}' concluído ({total} passos)."}

    def _find_procedure(self, message: str) -> Optional[dict]:
        all_procs = procs.list_procedures()
        msg_lower = message.lower()
        for p in all_procs:
            name = p["name"].replace("_", " ").lower()
            desc = p.get("description", "").lower()
            if name in msg_lower or (desc and desc in msg_lower):
                return procs.get_procedure(p["name"])
        return None


agent = Agent()
