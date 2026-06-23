"""
Gerenciador de Procedimentos — Fase 2
Salva/carrega/executa procedimentos em JSON.
Estrutura: data/procedures/nome_do_procedimento.json
"""
import json
import re
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
                "updated_at":  data.get("updated_at", ""),
                "last_execution": data.get("last_execution", ""),
                "last_status": data.get("last_status", "nunca_executado"),
                "filename":    f.name,
                "steps":       data.get("steps", []),
            })
        except Exception:
            pass
    return result


def sanitize_name(name: str) -> str:
    safe_name = (name or "").lower().replace(" ", "_").replace("/", "_")
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in ("_", "-"))
    return safe_name.strip("_-") or f"procedimento_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"


def get_procedure(name: str) -> Optional[dict]:
    """Busca procedimento por nome (sem .json)."""
    _ensure_dir()
    safe_name = sanitize_name(name)
    path = PROCEDURES_DIR / f"{safe_name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    path = PROCEDURES_DIR / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    # Busca parcial
    for f in PROCEDURES_DIR.glob("*.json"):
        if name.lower() in f.stem.lower():
            return json.loads(f.read_text(encoding="utf-8"))
    return None


def save_procedure(name: str, description: str, steps: list,
                   variables: list = None, proc_id: str = None,
                   extra: dict = None) -> dict:
    _ensure_dir()
    safe_name = sanitize_name(name)
    now = datetime.utcnow().isoformat()
    existing = get_procedure(safe_name) or {}

    proc = {
        "id":          proc_id or existing.get("id") or str(uuid.uuid4()),
        "name":        safe_name,
        "description": description,
        "steps":       [normalize_step(step) for step in steps],
        "variables":   variables or _extract_variables(steps),
        "created_at":  existing.get("created_at") or now,
        "updated_at":  now,
        "last_execution": existing.get("last_execution", ""),
        "last_status": existing.get("last_status", "nunca_executado"),
    }
    if extra:
        proc.update(extra)

    path = PROCEDURES_DIR / f"{safe_name}.json"
    path.write_text(json.dumps(proc, ensure_ascii=False, indent=2), encoding="utf-8")
    return proc


def delete_procedure(name: str) -> bool:
    _ensure_dir()
    path = PROCEDURES_DIR / f"{name}.json"
    if path.exists():
        path.unlink()
        return True
    return False


def normalize_step(step: dict) -> dict:
    if not isinstance(step, dict):
        return {"type": "wait", "ms": 500}
    normalized = dict(step)
    step_type = normalized.get("type") or normalized.get("action") or "wait"
    normalized["type"] = step_type
    normalized.setdefault("action", step_type)
    return normalized


def step_to_cmd(step: dict) -> dict:
    normalized = normalize_step(step)
    cmd = dict(normalized)
    cmd["action"] = normalized.get("action") or normalized.get("type")
    return cmd


def update_step(procedure_name: str, index: int, step: dict) -> Optional[dict]:
    proc = get_procedure(procedure_name)
    if not proc:
        return None
    steps = proc.get("steps", [])
    if index < 0 or index >= len(steps):
        return None
    steps[index] = normalize_step(step)
    proc["steps"] = steps
    proc["updated_at"] = datetime.utcnow().isoformat()
    path = PROCEDURES_DIR / f"{sanitize_name(proc.get('name', procedure_name))}.json"
    path.write_text(json.dumps(proc, ensure_ascii=False, indent=2), encoding="utf-8")
    return proc


def record_execution(name: str, status: str) -> Optional[dict]:
    proc = get_procedure(name)
    if not proc:
        return None
    proc["last_execution"] = datetime.utcnow().isoformat()
    proc["last_status"] = status
    proc["updated_at"] = datetime.utcnow().isoformat()
    path = PROCEDURES_DIR / f"{sanitize_name(proc.get('name', name))}.json"
    path.write_text(json.dumps(proc, ensure_ascii=False, indent=2), encoding="utf-8")
    return proc


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


def infer_name_from_text(text: str) -> str:
    value = (text or "").strip()
    quoted = re.search(r'"([^"]+)"|\'([^\']+)\'', value)
    if quoted:
        return sanitize_name(quoted.group(1) or quoted.group(2))
    patterns = [
        r"(?:procedimento|ensinar|executar|testar)\s+([A-Za-z0-9À-ÿ _-]{3,80})",
        r"(?:modo ensinar)\s+([A-Za-z0-9À-ÿ _-]{3,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match:
            return sanitize_name(match.group(1))
    return ""


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
