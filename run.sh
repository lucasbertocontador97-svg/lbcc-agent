#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LBCC Agent — iniciando..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

PYTHON=/home/runner/workspace/.pythonlibs/bin/python3.11

# Encontrar npm
NPM=$(which npm 2>/dev/null || echo "")
if [ -z "$NPM" ]; then
  for p in /usr/local/bin/npm /nix/var/nix/profiles/default/bin/npm \
            /home/runner/.nix-profile/bin/npm; do
    if [ -x "$p" ]; then NPM="$p"; break; fi
  done
fi

# Se ainda não achou, usar o frontend já buildado (dist/ do git)
echo "Python: $PYTHON"
echo "npm: ${NPM:-não encontrado}"

echo "📦 Instalando dependências Python..."
$PYTHON -m pip install -q --break-system-packages -r backend/requirements.txt

echo "🎭 Playwright Chromium..."
$PYTHON -m playwright install chromium 2>/dev/null || true

# Frontend — buildar só se npm disponível, senão usar dist já existente
if [ -n "$NPM" ]; then
  echo "📦 Node..."
  cd frontend
  $NPM install --silent 2>/dev/null || true
  echo "🔨 Buildando frontend..."
  $NPM run build
  cd ..
else
  echo "⚠️  npm não encontrado — usando frontend pré-buildado se existir"
fi

export BROWSER_HEADLESS=true

echo ""
echo "✅ Iniciando servidor..."
echo ""

$PYTHON -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
