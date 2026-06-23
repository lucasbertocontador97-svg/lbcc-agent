"""
Agente — Fase 3: abas, downloads, uploads, login persistente.
"""
import asyncio
import json
import os
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
ACTION_TIMEOUT_SECONDS = 35
IOB_PROFILE = "iob"
IOB_URL = os.getenv("IOB_URL", "https://www.iobonline.com.br/")

SYSTEM = """Você é o Agente Operacional LBCC.
Você controla um navegador Chrome real. Cookies e logins são mantidos entre sessões.

## Ferramentas — responda SEMPRE com JSON válido:

{"action": "navigate",      "url": "https://..."}
{"action": "click",         "selector": "texto ou css"}
{"action": "click_text",    "text": "texto visivel"}
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
        self._iob_url = os.getenv("IOB_URL", IOB_URL)

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

        iob_command = self._classify_iob_command(user_message)
        if iob_command:
            async for event in self._run_iob_command(iob_command, conv_id, exec_id):
                yield event
            return

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
            page_context = await browser.get_page_context()
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
                f"Contexto: {json.dumps(page_context, ensure_ascii=False)[:1600]}\n"
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

    def _classify_iob_command(self, msg: str) -> Optional[str]:
        m = msg.lower().strip()
        if m in ("parar", "stop"):
            return "stop"
        if "iob" not in m and "folha" not in m and "login" not in m:
            return None
        if "abrir" in m and "iob" in m:
            return "open_iob"
        if "login" in m:
            return "login_iob"
        if "folha" in m:
            return "go_payroll"
        return None

    async def _run_iob_command(
        self, command: str, conv_id: str, exec_id: str
    ) -> AsyncGenerator[dict, None]:
        if command == "stop":
            browser.request_stop()
            yield {"type": "stopped", "text": "Execucao interrompida."}
            return

        profile = await browser.use_profile(IOB_PROFILE)
        yield {"type": "system", "text": f"Perfil IOB ativo: {profile.get('path', '')}"}
        if not profile.get("ok"):
            yield {"type": "error", "text": profile.get("error", "Falha ao abrir perfil IOB.")}
            return

        if command == "open_iob":
            async for event in self._execute_cmd({"action": "navigate", "url": self._iob_url}, conv_id, exec_id):
                yield event
            yield {"type": "done", "text": "IOB aberto com perfil persistente dedicado."}
            return

        if command == "login_iob":
            context = await browser.get_page_context()
            if "iob" not in (context.get("url", "") + context.get("title", "")).lower():
                async for event in self._execute_cmd({"action": "navigate", "url": self._iob_url}, conv_id, exec_id):
                    yield event

            context = await browser.get_page_context()
            yield {"type": "context", "context": context}
            if self._looks_logged_in(context):
                yield {"type": "done", "text": "Login do IOB ja parece ativo."}
                return

            login_clicked = False
            for text in ("Entrar", "Login", "Acessar", "Acesse", "Minha conta"):
                async for event in self._execute_cmd({"action": "click_text", "text": text}, conv_id, exec_id):
                    yield event
                    if event.get("type") == "result" and event.get("ok"):
                        login_clicked = True
                if login_clicked:
                    break

            context = await browser.get_page_context()
            yield {"type": "context", "context": context}
            if self._looks_logged_in(context):
                yield {"type": "done", "text": "Login do IOB confirmado."}
                return

            yield {
                "type": "ask",
                "text": "Preciso de intervencao humana para concluir o login do IOB. Entre manualmente e aprove para continuar.",
            }
            approved = await browser.request_approval("Conclua o login do IOB no navegador e aprove para continuar.")
            if not approved:
                yield {"type": "stopped", "text": "Login do IOB interrompido pelo usuario."}
                return
            yield {"type": "done", "text": "Login do IOB registrado no perfil persistente."}
            return

        if command == "go_payroll":
            context = await browser.get_page_context()
            if "iob" not in (context.get("url", "") + context.get("title", "")).lower():
                async for event in self._run_iob_command("open_iob", conv_id, exec_id):
                    if event.get("type") != "done":
                        yield event

            for text in ("Folha", "Folha de Pagamento", "Departamento Pessoal", "DP"):
                if browser.should_stop:
                    yield {"type": "stopped", "text": "Execucao interrompida."}
                    return
                async for event in self._execute_cmd({"action": "click_text", "text": text}, conv_id, exec_id):
                    yield event
                    if event.get("type") == "result" and event.get("ok"):
                        final_context = await browser.get_page_context()
                        yield {"type": "context", "context": final_context}
                        yield {"type": "done", "text": "Navegacao para Folha executada sem emitir notas ou transmitir dados."}
                        return

            yield {
                "type": "ask",
                "text": "Nao encontrei o menu Folha com seguranca. Posso aguardar sua navegacao manual ate a tela de Folha?",
            }
            approved = await browser.request_approval("Abra manualmente o menu Folha e aprove para registrar o estado.")
            if not approved:
                yield {"type": "stopped", "text": "Navegacao para Folha interrompida."}
                return
            final_context = await browser.get_page_context()
            yield {"type": "context", "context": final_context}
            yield {"type": "done", "text": "Tela de Folha registrada para observacao."}

    def _looks_logged_in(self, context: dict) -> bool:
        haystack = " ".join(
            [context.get("url", ""), context.get("title", "")]
            + context.get("buttons", [])
            + context.get("links", [])
            + context.get("menus", [])
        ).lower()
        return any(token in haystack for token in ("sair", "logout", "minha conta", "folha", "dashboard"))

    async def _send_context_to_gpt(self, objective: str, action: dict, context: dict) -> Optional[str]:
        try:
            resp = await asyncio.wait_for(
                self._client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "Voce observa navegacao web. Responda JSON curto com risco e sugestao. Nao solicite acoes criticas."},
                        {"role": "user", "content": json.dumps({
                            "objective": objective,
                            "next_action": action,
                            "context": context,
                        }, ensure_ascii=False)},
                    ],
                    temperature=0,
                    max_tokens=180,
                    response_format={"type": "json_object"},
                ),
                timeout=12,
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            return None

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
        async for event in self._execute_cmd_v4(cmd, conv_id, exec_id):
            yield event
        return

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

    async def _execute_cmd_v4(
        self, cmd: dict, conv_id: str, exec_id: str
    ) -> AsyncGenerator[dict, None]:
        action = cmd.get("action", "")

        if action == "done":
            ss = await browser.screenshot("resultado_final")
            if ss.get("ok") and ss.get("b64"):
                yield {"type": "screenshot", "b64": ss["b64"], "label": "Resultado final"}
            yield {"type": "done", "text": cmd.get("message", "Concluido.")}
            await db.save_action_log(str(uuid.uuid4()), conv_id, exec_id,
                                     "done", {}, {"message": cmd.get("message")}, True)
            return

        if action == "ask":
            message = cmd.get("message", "")
            lowered = message.lower()
            if any(token in lowered for token in ("senha", "password", "e-mail", "email", "login")):
                message = (
                    "Intervencao manual necessaria: preencha login/senha diretamente "
                    "no navegador em modo manual e aprove quando terminar. Nao envie "
                    "credenciais pelo chat."
                )
            yield {"type": "ask", "text": message}
            approved = await browser.request_approval(message)
            yield {"type": "system" if approved else "stopped",
                   "text": "Aprovado. Continuando..." if approved else "Rejeitado pelo usuario."}
            return

        if action == "error":
            yield {"type": "error", "text": cmd.get("message", "Erro.")}
            return

        context_before = await browser.get_page_context()
        yield {"type": "context", "context": context_before}
        gpt_hint = await self._send_context_to_gpt("executar acao de navegacao", cmd, context_before)
        if gpt_hint:
            yield {"type": "system", "text": f"Contexto enviado ao GPT: {gpt_hint[:220]}"}

        yield {"type": "action", **cmd}
        ss_before = await browser.screenshot(f"before_{action}")
        if ss_before.get("ok") and ss_before.get("b64"):
            yield {"type": "screenshot", "b64": ss_before["b64"], "label": f"Antes: {action}"}

        label = action
        if action == "navigate":
            label = f"nav_{cmd.get('url','')[:30]}"
        elif action == "click":
            label = f"click_{cmd.get('selector','')[:25]}"
        elif action == "click_text":
            label = f"click_text_{cmd.get('text','')[:25]}"
        elif action == "fill":
            label = f"fill_{cmd.get('selector','')[:20]}"

        result = {"ok": False, "error": "Nao executado"}
        for attempt in range(MAX_RETRIES):
            if browser.should_stop:
                yield {"type": "stopped", "text": "Execucao interrompida."}
                return
            try:
                result = await asyncio.wait_for(
                    self._perform_action_once(action, cmd),
                    timeout=ACTION_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                result = {"ok": False, "error": f"Timeout da acao apos {ACTION_TIMEOUT_SECONDS}s"}
            except Exception as e:
                result = {"ok": False, "error": str(e)}

            if result.get("ok"):
                break
            if attempt < MAX_RETRIES - 1:
                yield {"type": "retry", "text": f"Retry {attempt+1}/{MAX_RETRIES}: {result.get('error','falha')}"}
                await asyncio.sleep(RETRY_DELAY_S)

        if action == "download" and result.get("ok"):
            yield {"type": "system", "text": f"Download salvo: {result.get('filename')}"}
        if action == "list_tabs" and result.get("tabs"):
            yield {"type": "system",
                   "text": "Abas: " + " | ".join(
                       f"[{t['index']}]{'*' if t['active'] else ''} {t['title'][:30]}"
                       for t in result["tabs"]
                   )}

        yield {"type": "result", **result}

        ss_after = await browser.screenshot(f"after_{label}")
        if ss_after.get("ok") and ss_after.get("b64"):
            yield {"type": "screenshot", "b64": ss_after["b64"], "label": f"Depois: {label}"}

        context_after = await browser.get_page_context()
        yield {"type": "context", "context": context_after}

        await db.save_action_log(str(uuid.uuid4()), conv_id, exec_id,
                                 action, {**cmd, "context_before": context_before},
                                 {**result, "context_after": context_after}, result.get("ok", False))

    async def _perform_action_once(self, action: str, cmd: dict) -> dict:
        if action == "navigate":
            return await browser.navigate(cmd.get("url", ""))
        if action == "click":
            return await browser.click(cmd.get("selector", ""))
        if action == "click_text":
            return await browser.click_text(cmd.get("text", ""))
        if action == "fill":
            return await browser.fill(cmd.get("selector", ""), cmd.get("value", ""))
        if action == "key":
            return await browser.key(cmd.get("key", ""))
        if action == "scroll":
            return await browser.scroll(cmd.get("direction", "down"), cmd.get("amount", 500))
        if action == "wait":
            return await browser.wait(cmd.get("ms", 1000))
        if action == "wait_selector":
            return await browser.wait_selector(cmd.get("selector", ""), cmd.get("timeout", 15000))
        if action == "select":
            return await browser.select_option(cmd.get("selector", ""), cmd.get("value", ""))
        if action == "hover":
            return await browser.hover(cmd.get("selector", ""))
        if action == "upload":
            return await browser.upload_file(cmd.get("selector", ""), cmd.get("file", ""))
        if action == "download":
            return await browser.download_file(cmd.get("url", ""), cmd.get("filename", ""))
        if action == "screenshot":
            ss = await browser.screenshot("manual")
            return {"ok": ss.get("ok", False), "filename": ss.get("filename")}
        if action == "new_tab":
            return await browser.new_tab(cmd.get("url", ""))
        if action == "switch_tab":
            return await browser.switch_tab(cmd.get("index", 0))
        if action == "close_tab":
            return await browser.close_tab(cmd.get("index"))
        if action == "list_tabs":
            return {"ok": True, "tabs": await browser.list_tabs()}
        return {"ok": False, "error": f"Acao desconhecida: {action}"}

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
