#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LBCC Agent — iniciando..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Encontrar pip/python corretos
PIP=$(which pip3 || which pip || echo "")
PYTHON=$(which python3.11 || which python3 || which python || echo "")

if [ -z "$PIP" ]; then
  echo "pip não encontrado, tentando instalar via python..."
  $PYTHON -m ensurepip --upgrade 2>/dev/null || true
  PIP="$PYTHON -m pip"
fi

echo "Python: $PYTHON"
echo "Pip: $PIP"

# Python deps
echo "📦 Instalando dependências Python..."
$PIP install -q -r backend/requirements.txt

# Playwright
echo "🎭 Instalando Playwright Chromium..."
$PYTHON -m playwright install chromium 2>/dev/null || true

# Node
echo "📦 Node..."
cd frontend
npm install --silent
echo "🔨 Buildando frontend..."
npm run build
cd ..

export BROWSER_HEADLESS=true

echo ""
echo "✅ Iniciando servidor..."
echo ""

$PYTHON -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
