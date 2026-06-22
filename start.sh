#!/bin/bash
# LBCC Agent — inicialização de desenvolvimento
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ── Verificar Python ──────────────────────────────────────────────────────────
if ! command -v python3 &>/dev/null; then
  echo "❌ Python 3 não encontrado."
  exit 1
fi

# ── .env ─────────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  echo "⚠️  Arquivo .env criado. Adicione sua OPENAI_API_KEY antes de continuar."
  exit 1
fi

source .env
if [ -z "$OPENAI_API_KEY" ] || [ "$OPENAI_API_KEY" = "sk-..." ]; then
  echo "❌ Defina OPENAI_API_KEY no arquivo .env"
  exit 1
fi

# ── Backend: venv + deps ──────────────────────────────────────────────────────
if [ ! -d "backend/venv" ]; then
  echo "📦 Criando ambiente virtual Python..."
  python3 -m venv backend/venv
fi

echo "📦 Instalando dependências Python..."
backend/venv/bin/pip install -q -r backend/requirements.txt

echo "🎭 Verificando Playwright Chromium..."
backend/venv/bin/playwright install chromium 2>/dev/null || true

# ── Frontend: node_modules ────────────────────────────────────────────────────
if [ ! -d "frontend/node_modules" ]; then
  echo "📦 Instalando dependências Node..."
  cd frontend && npm install --silent && cd ..
fi

# ── Iniciar ───────────────────────────────────────────────────────────────────
echo ""
echo "╔═══════════════════════════════╗"
echo "║  LBCC Agent — iniciando...    ║"
echo "╚═══════════════════════════════╝"
echo ""
echo "  Backend  → http://localhost:8000"
echo "  Frontend → http://localhost:5173"
echo ""
echo "  Chrome abrirá automaticamente."
echo "  Ctrl+C para parar tudo."
echo ""

# Backend em background
OPENAI_API_KEY="$OPENAI_API_KEY" \
  backend/venv/bin/uvicorn backend.api.main:app \
    --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Aguardar backend subir
sleep 3

# Frontend
cd frontend && npm run dev &
FRONTEND_PID=$!

cd "$ROOT"

# Matar tudo ao Ctrl+C
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit 0" INT TERM

wait
