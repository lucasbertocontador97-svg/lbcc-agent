"""
Agente — loop LLM + browser.
Fase 1.1: timeout, retry, stop, screenshot automático em toda ação.
"""
import asyncio
import json
import time
import uuid
from typing import AsyncGenerator

from openai import AsyncOpenAI

from backend.browser.browser import browser
from backend.db import database as db

TIMEOUT_SECONDS = 180
MAX_RETRIES     = 3
RETRY_DELAY_S   = 2

SYSTEM = """Você é o Agente Operacional LBCC.
Você controla um navegador Chrome real para executar tarefas de escritório contábil.

## Ferramentas disponíveis — responda SEMPRE com JSON válido:

{"action": "navigate",   "url": "https://..."}
{"action": "click",      "selector": "css-selector"}
{"action": "fill",       "selector": "css-selector", "value": "texto"}
{"action": "wait",       "ms": 1000}
{"action": "screenshot"}
{"action": "done",       "message": "Tarefa concluída: ..."}
{"action": "ask",        "message": "Situação encontrada. Deseja que eu ...?"}
{"action": "error",      "message": "Não consegui porque ..."}

## Regras
- Verifique o estado atual da página antes de agir.
- Use seletores CSS precisos: #id, [name=x], button:has-text('texto').
- Nunca invente dados. Se faltar informação, use ask.
- Responda APENAS com JSON. Sem texto fora do JSON.
- Máximo 30 iterações por tarefa.
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
    ) -> AsyncGenerator[dict, None]:
        """
        Executa com: timeout global, retry por ação, stop flag, screenshot automático.
        """
        deadline = time.time() + TIMEOUT_SECONDS
        messages = [{"role": "system", "content": SYSTEM}]
        for h in history[-16:]:
            messages.append({"role": h["role"], "content": h["content"]})
        messages.append({"role": "user", "content": user_message})

        for iteration in range(30):

            # ── Verificar STOP ─────────────────────────────────────────────
            if browser.should_stop:
                yield {"type": "stopped", "text": "Execução interrompida pelo usuário."}
                return

            # ── Verificar TIMEOUT ──────────────────────────────────────────
            if time.time() > deadline:
                yield {"type": "timeout", "text": f"Execução interrompida por timeout ({TIMEOUT_SECONDS}s)."}
                return

            # ── Estado da página ───────────────────────────────────────────
            state = await browser.page_state()
            state_text = (
                f"\n[Página atual]\n"
                f"URL: {state.get('url','?')}\n"
                f"Título: {state.get('title','?')}\n"
                f"Elementos ({len(state.get('elements',[]))}): "
                f"{json.dumps(state.get('elements',[])[:10], ensure_ascii=False)}\n"
                f"Texto: {state.get('body','')[:600]}"
            )

            ctx_messages = messages.copy()
            ctx_messages.append({"role": "user",
                                  "content": f"[iteração {iteration+1}]{state_text}"})

            # ── Chamar LLM com retry ───────────────────────────────────────
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
                        yield {"type": "retry", "text": f"Tentativa {attempt+1} falhou: {e}. Tentando novamente..."}
                        await asyncio.sleep(RETRY_DELAY_S)
                    else:
                        yield {"type": "error", "text": f"LLM falhou após {MAX_RETRIES} tentativas: {e}"}
                        return

            try:
                cmd = json.loads(raw)
            except Exception:
                yield {"type": "error", "text": f"JSON inválido: {raw[:200]}"}
                return

            action = cmd.get("action", "")

            # ── done / ask / error ─────────────────────────────────────────
            if action == "done":
                text = cmd.get("message", "Concluído.")
                # Screenshot final
                ss = await browser.screenshot("resultado_final")
                if ss["ok"]:
                    yield {"type": "screenshot", "b64": ss["b64"],
                           "label": "Resultado final"}
                yield {"type": "done", "text": text}
                await db.save_action_log(
                    str(uuid.uuid4()), conv_id, exec_id,
                    "done", {}, {"message": text}, True
                )
                return

            if action == "ask":
                yield {"type": "ask", "text": cmd.get("message", "")}
                return

            if action == "error":
                yield {"type": "error", "text": cmd.get("message", "Erro.")}
                return

            # ── Executar ação no browser ───────────────────────────────────
            yield {"type": "action", **cmd}

            result = {}
            label  = ""

            if action == "navigate":
                label  = f"navegar_{cmd.get('url','')[:40]}"
                result = await browser.navigate(cmd.get("url", ""))
            elif action == "click":
                label  = f"click_{cmd.get('selector','')[:30]}"
                result = await browser.click(cmd.get("selector", ""))
            elif action == "fill":
                label  = f"fill_{cmd.get('selector','')[:20]}"
                result = await browser.fill(cmd.get("selector",""), cmd.get("value",""))
            elif action == "wait":
                result = await browser.wait(cmd.get("ms", 1000))
                label  = "wait"
            elif action == "screenshot":
                ss = await browser.screenshot("manual")
                if ss["ok"]:
                    yield {"type": "screenshot", "b64": ss["b64"], "label": "Manual"}
                result = {"ok": ss["ok"]}
            else:
                result = {"ok": False, "error": f"Ação desconhecida: {action}"}

            yield {"type": "result", **result}

            # ── Screenshot automático após toda ação ───────────────────────
            if action != "screenshot" and action != "wait":
                ss = await browser.screenshot(label)
                if ss["ok"]:
                    yield {"type": "screenshot", "b64": ss["b64"], "label": label}

            # ── Log no banco ───────────────────────────────────────────────
            await db.save_action_log(
                str(uuid.uuid4()), conv_id, exec_id,
                action, cmd, result, result.get("ok", False)
            )

            messages.append({"role": "assistant", "content": raw})
            messages.append({
                "role": "user",
                "content": f"[resultado] {json.dumps(result, ensure_ascii=False)}"
            })

        yield {"type": "error", "text": "Limite de 30 iterações atingido."}


agent = Agent()
