"""
Agente — loop LLM + browser.
Recebe comando em português, executa ações no Chromium, streama eventos.
"""
import json
import uuid
from typing import AsyncGenerator

from openai import AsyncOpenAI

from backend.browser.browser import browser
from backend.db import database as db

# ── Prompt do sistema ─────────────────────────────────────────────────────────

SYSTEM = """Você é o Agente Operacional LBCC.
Você controla um navegador Chrome real para executar tarefas de escritório contábil.

## Ferramentas disponíveis

Responda SEMPRE com um JSON válido em uma destas formas:

### Executar ação no browser:
{"action": "navigate", "url": "https://..."}
{"action": "click",    "selector": "css-selector"}
{"action": "fill",     "selector": "css-selector", "value": "texto"}
{"action": "wait",     "ms": 1000}
{"action": "screenshot"}

### Finalizar:
{"action": "done", "message": "Tarefa concluída: ..."}

### Pedir aprovação antes de agir:
{"action": "ask", "message": "Encontrei X situação. Deseja que eu ...?"}

### Reportar erro:
{"action": "error", "message": "Não consegui porque ..."}

## Regras
- Sempre verifique o estado atual da página antes de agir.
- Use seletores CSS precisos. Prefira #id, [name=x], button:has-text('texto').
- Nunca invente dados. Se faltar informação, use {"action": "ask", ...}.
- Responda APENAS com JSON. Sem texto fora do JSON.
- Seja objetivo. Máximo 30 iterações por tarefa.
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
        history: list[dict],
    ) -> AsyncGenerator[dict, None]:
        """
        Gera eventos enquanto executa a tarefa.
        Tipos de evento:
          {"type": "thought",   "text": "..."}
          {"type": "action",    "action": "navigate", ...}
          {"type": "result",    "ok": True/False, ...}
          {"type": "screenshot","b64": "..."}
          {"type": "message",   "text": "..."}
          {"type": "done",      "text": "..."}
          {"type": "ask",       "text": "..."}
          {"type": "error",     "text": "..."}
        """
        # Construir histórico para o LLM
        messages = [{"role": "system", "content": SYSTEM}]
        for h in history[-16:]:  # janela de 16 mensagens
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_message})

        for iteration in range(30):
            # Estado atual da página
            state = await browser.page_state()
            state_text = (
                f"\n[Página atual]\n"
                f"URL: {state.get('url','?')}\n"
                f"Título: {state.get('title','?')}\n"
                f"Elementos ({len(state.get('elements',[]))}): "
                f"{json.dumps(state.get('elements',[])[:10], ensure_ascii=False)}\n"
                f"Texto: {state.get('body','')[:600]}"
            )

            # Adicionar estado como contexto desta iteração
            ctx_messages = messages.copy()
            ctx_messages.append({
                "role": "user",
                "content": f"[iteração {iteration+1}]{state_text}"
            })

            # Chamar LLM
            resp = await self._client.chat.completions.create(
                model="gpt-4o",
                messages=ctx_messages,
                temperature=0.1,
                max_tokens=512,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content.strip()

            try:
                cmd = json.loads(raw)
            except Exception:
                yield {"type": "error", "text": f"JSON inválido do LLM: {raw[:200]}"}
                return

            action = cmd.get("action", "")

            # ── done / ask / error ────────────────────────────────────────────
            if action == "done":
                text = cmd.get("message", "Concluído.")
                yield {"type": "done", "text": text}
                await db.save_action_log(
                    str(uuid.uuid4()), conv_id, "done", {}, {"message": text}, True
                )
                return

            if action == "ask":
                yield {"type": "ask", "text": cmd.get("message", "")}
                return

            if action == "error":
                yield {"type": "error", "text": cmd.get("message", "Erro desconhecido.")}
                return

            # ── ações do browser ──────────────────────────────────────────────
            yield {"type": "action", **cmd}

            result = {}
            if action == "navigate":
                result = await browser.navigate(cmd.get("url", ""))
            elif action == "click":
                result = await browser.click(cmd.get("selector", ""))
            elif action == "fill":
                result = await browser.fill(cmd.get("selector", ""), cmd.get("value", ""))
            elif action == "wait":
                result = await browser.wait(cmd.get("ms", 1000))
            elif action == "screenshot":
                ss = await browser.screenshot()
                if ss["ok"]:
                    yield {"type": "screenshot", "b64": ss["b64"]}
                result = {"ok": ss["ok"]}
            else:
                result = {"ok": False, "error": f"Ação desconhecida: {action}"}

            yield {"type": "result", **result}

            # Log no banco
            await db.save_action_log(
                str(uuid.uuid4()), conv_id, action, cmd, result, result.get("ok", False)
            )

            # Informa o LLM do resultado para próxima iteração
            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": f"[resultado] {json.dumps(result, ensure_ascii=False)}"
            })

            # Screenshot automático após navigate
            if action == "navigate" and result.get("ok"):
                ss = await browser.screenshot()
                if ss["ok"]:
                    yield {"type": "screenshot", "b64": ss["b64"]}

        yield {"type": "error", "text": "Limite de 30 iterações atingido."}


# Instância global
agent = Agent()
