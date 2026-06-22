"""
Gerenciador de Procedimentos — Fase 2
Salva/carrega/executa procedimentos em JSON.
Estrutura: data/procedures/nome_do_procedimento.json
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

PROCEDURES_DIR = Path(__file__).parent.parent / "data" / "procedures"


def _ensure_dir():
    PROCEDURES_DIR.mkdir(parents=True, exist_ok=True)


# ── Estrutura de um procedimento ──────────────────────────────────────────────
#
# {
#   "id": "uuid",
#   "name": "google_search",
#   "description": "Pesquisa no Google",
#   "created_at": "2024-...",
#   "steps": [
#     {"action": "navigate", "url": "https://google.com"},
#     {"action": "fill",     "selector": "input[name=q]", "value": "{query}"},
#     {"action": "click",    "selector": "input[name=btnK]"},
#     {"action": "wait",     "ms": 1000}
#   ],
#   "variables": ["query"]   <- variáveis que podem ser substituídas
# }


def list_procedures() -> list[dict]:
    _ensure_dir()
    result = []
    for f in sorted(PROCEDURES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            result.append({
                "id":          data.get("id", f.stem),
                "name":        data.get("name", f.stem),
                "description": data.get("description", ""),
                "steps_count": len(data.get("steps", [])),
                "variables":   data.get("variables", []),
                "created_at":  data.get("created_at", ""),
                "filename":    f.name,
            })
        except Exception:
            pass
    return result


def get_procedure(name: str) -> Optional[dict]:
    """Busca procedimento por nome (sem .json)."""
    _ensure_dir()
    path = PROCEDURES_DIR / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text())
    # Busca parcial
    for f in PROCEDURES_DIR.glob("*.json"):
        if name.lower() in f.stem.lower():
            return json.loads(f.read_text())
    return None


def save_procedure(name: str, description: str, steps: list,
                   variables: list = None, proc_id: str = None) -> dict:
    _ensure_dir()
    # Sanitizar nome para filename
    safe_name = name.lower().replace(" ", "_").replace("/", "_")
    safe_name = "".join(c for c in safe_name if c.isalnum() or c == "_")

    proc = {
        "id":          proc_id or str(uuid.uuid4()),
        "name":        safe_name,
        "description": description,
        "steps":       steps,
        "variables":   variables or _extract_variables(steps),
        "created_at":  datetime.utcnow().isoformat(),
    }

    path = PROCEDURES_DIR / f"{safe_name}.json"
    path.write_text(json.dumps(proc, ensure_ascii=False, indent=2))
    return proc


def delete_procedure(name: str) -> bool:
    _ensure_dir()
    path = PROCEDURES_DIR / f"{name}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def apply_variables(steps: list, variables: dict) -> list:
    """Substitui {variavel} nos steps com os valores fornecidos."""
    result = []
    for step in steps:
        new_step = {}
        for k, v in step.items():
            if isinstance(v, str):
                for var_name, var_value in variables.items():
                    v = v.replace(f"{{{var_name}}}", str(var_value))
            new_step[k] = v
        result.append(new_step)
    return result


def _extract_variables(steps: list) -> list:
    """Detecta {variavel} nos steps automaticamente."""
    import re
    vars_found = set()
    for step in steps:
        for v in step.values():
            if isinstance(v, str):
                for match in re.findall(r'\{(\w+)\}', v):
                    vars_found.add(match)
    return sorted(vars_found)


# Procedimentos de exemplo para iniciar
EXAMPLE_PROCEDURES = [
    {
        "name": "google_search",
        "description": "Pesquisa no Google",
        "steps": [
            {"action": "navigate", "url": "https://www.google.com"},
            {"action": "wait_selector", "selector": "textarea[name=q]"},
            {"action": "fill",    "selector": "textarea[name=q]", "value": "{query}"},
            {"action": "key",     "key": "Enter"},
            {"action": "wait",    "ms": 2000},
            {"action": "screenshot"},
        ],
        "variables": ["query"],
    },
    {
        "name": "receita_federal",
        "description": "Abre o portal da Receita Federal",
        "steps": [
            {"action": "navigate", "url": "https://www.gov.br/receitafederal/pt-br"},
            {"action": "wait",     "ms": 2000},
            {"action": "screenshot"},
        ],
        "variables": [],
    },
    {
        "name": "ecac_login",
        "description": "Acessa o e-CAC (requer login gov.br)",
        "steps": [
            {"action": "navigate", "url": "https://cav.receita.fazenda.gov.br/autenticacao/login"},
            {"action": "wait",     "ms": 2000},
            {"action": "screenshot"},
            {"action": "ask",      "message": "Tela de login do e-CAC aberta. Deseja que eu prossiga com o login?"},
        ],
        "variables": [],
    },
]


def create_examples():
    """Cria procedimentos de exemplo se não existirem."""
    _ensure_dir()
    for p in EXAMPLE_PROCEDURES:
        path = PROCEDURES_DIR / f"{p['name']}.json"
        if not path.exists():
            save_procedure(p["name"], p["description"], p["steps"], p.get("variables", []))
