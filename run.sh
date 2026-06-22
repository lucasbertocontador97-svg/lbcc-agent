#!/bin/bash

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LBCC Agent — iniciando..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Mostrar PATH para debug
echo "PATH=$PATH"
echo "Python disponível:"
which python3 2>/dev/null || echo "python3 não encontrado"
which python3.11 2>/dev/null || echo "python3.11 não encontrado"

# Tentar qualquer python disponível
PYTHON=$(which python3.11 2>/dev/null || which python3 2>/dev/null || which python 2>/dev/null)

if [ -z "$PYTHON" ]; then
  echo "❌ Nenhum Python encontrado. Abortando."
  exit 1
fi

echo "✅ Usando: $PYTHON ($($PYTHON --version))"

echo "📦 Instalando dependências..."
$PYTHON -m pip install --user -q -r backend/requirements.txt

export BROWSER_HEADLESS=true
export PLAYWRIGHT_BROWSERS_PATH=$(dirname $(dirname $(dirname "${REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE:-/nix}")))

echo "✅ Iniciando servidor na porta 8000..."
$PYTHON -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
