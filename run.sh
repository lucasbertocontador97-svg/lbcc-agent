#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LBCC Agent — iniciando..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

PYTHON=/home/runner/workspace/.pythonlibs/bin/python3.11

echo "Python: $PYTHON"

echo "📦 Instalando dependências Python..."
$PYTHON -m pip install -q --break-system-packages -r backend/requirements.txt

echo "🎭 Playwright Chromium..."
$PYTHON -m playwright install chromium 2>/dev/null || true

echo "✅ Frontend já buildado — usando dist/ do repositório"

export BROWSER_HEADLESS=true

echo ""
echo "✅ Iniciando servidor..."
echo ""

$PYTHON -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
