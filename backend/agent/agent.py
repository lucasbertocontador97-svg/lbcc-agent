"""
Agente — Fase 3: abas, downloads, uploads, login persistente.
"""
import asyncio
import json
import os
import re
import time
import unicodedata
import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional
from urllib.parse import urlparse

from openai import AsyncOpenAI

from backend.browser.browser import browser
from backend.db import database as db
from backend.procedures import manager as procs

TIMEOUT_SECONDS = 75
MAX_RETRIES     = 2
RETRY_DELAY_S   = 1
ACTION_TIMEOUT_SECONDS = 14
IOB_PROFILE = "iob"
IOB_URL = os.getenv("IOB_URL", "https://www.iobonline.com.br/")
CREDENTIALS_FILE = Path(__file__).parent.parent / "data" / "credentials.json"

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
        self._iob_email = os.getenv("IOB_EMAIL", "")
        self._iob_password = os.getenv("IOB_PASSWORD", "")
        self._credentials_file = Path(os.getenv("CREDENTIALS_FILE", str(CREDENTIALS_FILE)))

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
            async for event in self._run_iob_command(iob_command, conv_id, exec_id, user_message):
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
        direct_url = self._extract_direct_url(user_message)
        lower_message = user_message.lower()
        if direct_url and "nova aba" not in lower_message and "abrir nova aba" not in lower_message:
            async for event in self._execute_cmd({"action": "navigate", "url": direct_url}, conv_id, exec_id):
                yield event
            yield {"type": "done", "text": f"Acessei {direct_url}"}
            return

        hub_task_count = self._classify_hub_task_count(user_message, history)
        if hub_task_count:
            async for event in self._run_hub_task_count(
                hub_task_count["person"], hub_task_count["status"], conv_id, exec_id
            ):
                yield event
            return

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
        if "login" in m and "iob" in m:
            return "login_iob"
        if "login" in m:
            return "generic_login"
        if "folha" in m:
            return "go_payroll"
        return None

    def _norm(self, value: str) -> str:
        return "".join(
            c for c in unicodedata.normalize("NFD", value or "")
            if unicodedata.category(c) != "Mn"
        ).lower()

    def _history_text(self, history: list[dict]) -> str:
        parts = []
        for item in (history or [])[-6:]:
            content = item.get("content", "")
            if content:
                parts.append(content)
        return "\n".join(parts)

    def _classify_hub_task_count(self, msg: str, history: list[dict]) -> Optional[dict]:
        combined = msg
        normalized_msg = self._norm(msg)
        if normalized_msg in ("faca isso", "faça isso", "faz isso", "pode fazer", "prossiga"):
            combined = f"{self._history_text(history)}\n{msg}"

        normalized = self._norm(combined)
        if "tarefa" not in normalized:
            return None
        if not any(token in normalized for token in ("pendente", "aberto", "aberta", "em aberto")):
            return None

        person = ""
        patterns = [
            r"(?:para|pro|responsavel|responsável|colaborador|usuario|usuário)\s+(?:o\s+|a\s+)?([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9 ._-]{1,40})",
            r"(?:do|da)\s+([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9 ._-]{1,40})",
        ]
        for pattern in patterns:
            match = re.search(pattern, combined, flags=re.IGNORECASE)
            if match:
                person = match.group(1)
                break
        if not person and "alberto" in normalized:
            person = "Alberto"
        if not person:
            return None
        person = re.split(r"\b(?:pendente|pendentes|aberto|abertas|em aberto|agora|quantas|tem)\b", person, flags=re.IGNORECASE)[0]
        person = person.strip(" .,:;?\"'")
        if not person:
            return None
        return {"person": person.title(), "status": "pendente"}

    async def _run_hub_task_count(
        self, person: str, status: str, conv_id: str, exec_id: str
    ) -> AsyncGenerator[dict, None]:
        context = await browser.get_page_context()
        current = (context.get("url", "") + " " + context.get("title", "")).lower()
        if "hublbcc" not in current:
            async for event in self._execute_cmd({"action": "navigate", "url": "https://hublbcc.com.br/"}, conv_id, exec_id):
                yield event

        context = await browser.get_page_context()
        if "tarefas" not in context.get("url", "").lower():
            async for event in self._execute_cmd({"action": "click_text", "text": "Tarefas"}, conv_id, exec_id):
                yield event

        counts = await browser.hub_task_counts_by_responsible(person)
        ss = await browser.screenshot("hub_task_count")
        if ss.get("ok") and ss.get("b64"):
            yield {"type": "screenshot", "b64": ss["b64"], "label": "Hub Tarefas"}

        if counts.get("ok"):
            responsible_names = ", ".join(
                item.get("name", "") for item in counts.get("matched_responsibles", [])
                if item.get("name")
            ) or person
            pending_count = counts.get("pending_count", 0)
            open_count = counts.get("open_count", 0)
            done_count = counts.get("done_count", 0)
            awaiting_count = counts.get("awaiting_approval_count", 0)
            in_progress_count = counts.get("in_progress_count", 0)
            matched_total = counts.get("matched_total", 0)
            fetched_total = counts.get("fetched_total", 0)

            yield {
                "type": "done",
                "text": (
                    f"Contei internamente pela API do Hub para {responsible_names}: "
                    f"{pending_count} pendente(s), {awaiting_count} aguardando aprovacao, "
                    f"{in_progress_count} em andamento, {done_count} concluida(s). "
                    f"Total do responsavel: {matched_total}. Total geral lido: {fetched_total}."
                ),
                "details": {
                    "person": person,
                    "status_requested": status,
                    "responsibles": counts.get("matched_responsibles", []),
                    "pending_count": pending_count,
                    "open_count": open_count,
                    "awaiting_approval_count": awaiting_count,
                    "in_progress_count": in_progress_count,
                    "done_count": done_count,
                    "matched_total": matched_total,
                    "fetched_total": fetched_total,
                    "by_status": counts.get("by_status", {}),
                    "global_by_status": counts.get("global_by_status", {}),
                    "sample": counts.get("sample", [])[:10],
                },
            }
            return

        await browser.wait(900)
        summary = await browser.task_page_summary()
        counters = summary.get("counters", {}) if summary.get("ok") else {}
        pending_count = None
        for key, value in counters.items():
            if "pendente" in self._norm(key):
                pending_count = value
                break
        if pending_count is None:
            pending_count = summary.get("visible_row_count", 0) if summary.get("ok") else 0

        rows = summary.get("visible_rows", []) if summary.get("ok") else []
        yield {
            "type": "done",
            "text": (
                f"Encontrei {pending_count} tarefa(s) pendente(s) para {person}. "
                f"Filtro aplicado na tela."
            ),
            "details": {
                "person": person,
                "status": status,
                "count": pending_count,
                "sample_rows": rows[:5],
            },
        }

    async def _run_iob_command(
        self, command: str, conv_id: str, exec_id: str, user_message: str = ""
    ) -> AsyncGenerator[dict, None]:
        if command == "stop":
            browser.request_stop()
            yield {"type": "stopped", "text": "Execucao interrompida."}
            return

        if command == "generic_login":
            async for event in self._run_generic_login(user_message, conv_id, exec_id):
                yield event
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

            if self._iob_email and self._iob_password:
                yield {"type": "system", "text": "Credenciais IOB encontradas no .env. Tentando login automatico."}
                ss_before = await browser.screenshot("before_iob_login_credentials")
                if ss_before.get("ok") and ss_before.get("b64"):
                    yield {"type": "screenshot", "b64": ss_before["b64"], "label": "Antes: login IOB"}

                result = await browser.fill_login_credentials(self._iob_email, self._iob_password)
                safe_result = {
                    **result,
                    "email": self._mask_email(self._iob_email),
                    "password_set": bool(self._iob_password),
                }
                yield {"type": "result", **safe_result}
                await db.save_action_log(
                    str(uuid.uuid4()), conv_id, exec_id,
                    "iob_login_credentials",
                    {"email": self._mask_email(self._iob_email), "password_set": True},
                    safe_result,
                    result.get("ok", False),
                )

                ss_after = await browser.screenshot("after_iob_login_credentials")
                if ss_after.get("ok") and ss_after.get("b64"):
                    yield {"type": "screenshot", "b64": ss_after["b64"], "label": "Depois: login IOB"}

                await browser.wait(1500)
                context = await browser.get_page_context()
                yield {"type": "context", "context": context}
                if self._looks_logged_in(context):
                    yield {"type": "done", "text": "Login do IOB confirmado com credenciais locais."}
                    return

                yield {"type": "system", "text": "Login automatico nao foi confirmado; mantendo fallback manual."}
            else:
                yield {"type": "system", "text": "IOB_EMAIL/IOB_PASSWORD ainda nao estao definidos no .env."}

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

    async def _run_generic_login(
        self, user_message: str, conv_id: str, exec_id: str
    ) -> AsyncGenerator[dict, None]:
        target = self._extract_login_target(user_message)
        credential = await self._find_credential(target)
        if not credential:
            context = await browser.get_page_context()
            target = self._host_alias(context.get("url", ""))
            credential = await self._find_credential(target)

        if not credential:
            yield {
                "type": "ask",
                "text": (
                    "Nao encontrei credenciais locais para este site. Cadastre em "
                    "backend/data/credentials.json e tente de novo."
                ),
            }
            approved = await browser.request_approval("Cadastre credenciais locais e aprove para tentar novamente.")
            if not approved:
                yield {"type": "stopped", "text": "Login interrompido."}
                return
            credential = await self._find_credential(target)
            if not credential:
                yield {"type": "error", "text": "Credenciais locais ainda nao encontradas."}
                return

        label = credential.get("label") or credential.get("alias") or "site"
        url = credential.get("url", "")
        if url:
            async for event in self._execute_cmd({"action": "navigate", "url": url}, conv_id, exec_id):
                yield event

        context = await browser.get_page_context()
        yield {"type": "context", "context": context}
        if self._looks_logged_in(context):
            yield {"type": "done", "text": f"Login de {label} ja parece ativo."}
            return

        for text in ("Entrar", "Login", "Acessar", "Minha conta", "Sign in", "Log in"):
            result_seen = False
            async for event in self._execute_cmd({"action": "click_text", "text": text}, conv_id, exec_id):
                yield event
                if event.get("type") == "result" and event.get("ok"):
                    result_seen = True
            if result_seen:
                break

        yield {"type": "system", "text": f"Credenciais locais encontradas para {label}. Tentando login automatico."}
        ss_before = await browser.screenshot("before_generic_login_credentials")
        if ss_before.get("ok") and ss_before.get("b64"):
            yield {"type": "screenshot", "b64": ss_before["b64"], "label": f"Antes: login {label}"}

        result = await browser.fill_login_credentials(credential.get("email", ""), credential.get("password", ""))
        safe_result = {
            **result,
            "email": self._mask_email(credential.get("email", "")),
            "password_set": bool(credential.get("password")),
            "credential": label,
        }
        yield {"type": "result", **safe_result}
        await db.save_action_log(
            str(uuid.uuid4()), conv_id, exec_id,
            "generic_login_credentials",
            {
                "credential": label,
                "email": self._mask_email(credential.get("email", "")),
                "password_set": bool(credential.get("password")),
            },
            safe_result,
            result.get("ok", False),
        )

        ss_after = await browser.screenshot("after_generic_login_credentials")
        if ss_after.get("ok") and ss_after.get("b64"):
            yield {"type": "screenshot", "b64": ss_after["b64"], "label": f"Depois: login {label}"}

        await browser.wait(1500)
        context = await browser.get_page_context()
        yield {"type": "context", "context": context}
        if self._looks_logged_in(context):
            yield {"type": "done", "text": f"Login de {label} confirmado com credenciais locais."}
            return

        yield {
            "type": "ask",
            "text": (
                f"Login automatico de {label} nao foi confirmado. "
                "Conclua manualmente no navegador e aprove quando terminar."
            ),
        }
        approved = await browser.request_approval(f"Conclua manualmente o login de {label}.")
        if not approved:
            yield {"type": "stopped", "text": f"Login de {label} interrompido."}
            return
        yield {"type": "done", "text": f"Login de {label} registrado no perfil persistente."}

    def _looks_logged_in(self, context: dict) -> bool:
        haystack = " ".join(
            [context.get("url", ""), context.get("title", "")]
            + context.get("buttons", [])
            + context.get("links", [])
            + context.get("menus", [])
        ).lower()
        return any(token in haystack for token in ("sair", "logout", "minha conta", "folha", "dashboard"))

    def _extract_login_target(self, message: str) -> str:
        m = message.lower().strip()
        patterns = [
            r"login\s+(?:no|na|em|do|da)\s+([\w\.-]+)",
            r"entrar\s+(?:no|na|em|do|da)\s+([\w\.-]+)",
            r"acessar\s+(?:no|na|em|do|da)\s+([\w\.-]+)",
        ]
        for pattern in patterns:
            found = re.search(pattern, m)
            if found:
                return self._normalize_alias(found.group(1))
        return ""

    async def _find_credential(self, target: str) -> Optional[dict]:
        credentials = self._load_credentials()
        aliases = []
        if target:
            aliases.append(self._normalize_alias(target))

        context = await browser.get_page_context()
        current_host = self._host_alias(context.get("url", ""))
        if current_host:
            aliases.append(current_host)

        for alias in aliases:
            for item in credentials:
                item_aliases = [self._normalize_alias(a) for a in item.get("aliases", [])]
                item_aliases.append(self._normalize_alias(item.get("alias", "")))
                item_aliases.append(self._host_alias(item.get("url", "")))
                if alias and any(
                    alias == item_alias or
                    alias in item_alias or
                    item_alias in alias
                    for item_alias in item_aliases
                    if item_alias
                ):
                    return item

        return None

    def _load_credentials(self) -> list[dict]:
        items = []

        if self._credentials_file.exists():
            try:
                data = json.loads(self._credentials_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    sites = data.get("sites", {})
                    if isinstance(sites, dict):
                        for alias, value in sites.items():
                            if isinstance(value, dict):
                                items.append({"alias": alias, **value})
                    elif isinstance(sites, list):
                        items.extend([item for item in sites if isinstance(item, dict)])
                elif isinstance(data, list):
                    items.extend([item for item in data if isinstance(item, dict)])
            except Exception:
                pass

        if self._iob_email and self._iob_password:
            items.append({
                "alias": "iob",
                "label": "IOB",
                "url": self._iob_url,
                "email": self._iob_email,
                "password": self._iob_password,
                "aliases": ["iob", "iobonline"],
            })

        return [
            item for item in items
            if item.get("email") and item.get("password")
        ]

    def _normalize_alias(self, value: str) -> str:
        value = (value or "").lower().strip()
        value = value.replace("https://", "").replace("http://", "")
        value = value.split("/")[0]
        value = value.removeprefix("www.")
        return re.sub(r"[^a-z0-9.-]+", "", value)

    def _host_alias(self, url: str) -> str:
        try:
            host = urlparse(url).netloc or url
            return self._normalize_alias(host)
        except Exception:
            return ""

    def _mask_email(self, email: str) -> str:
        if "@" not in email:
            return "***"
        left, right = email.split("@", 1)
        return f"{left[:2]}***@{right}"

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

    def _extract_direct_url(self, msg: str) -> str:
        match = re.search(r"https?://[^\s)>\]\"']+", msg or "", flags=re.IGNORECASE)
        if not match:
            return ""
        return match.group(0).rstrip(".,;:")

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
        action_retries = 1 if action in ("click", "click_text", "fill") else MAX_RETRIES
        for attempt in range(action_retries):
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
            if attempt < action_retries - 1:
                yield {"type": "retry", "text": f"Retry {attempt+1}/{action_retries}: {result.get('error','falha')}"}
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
