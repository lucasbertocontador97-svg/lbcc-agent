#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LBCC Agent — iniciando..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

PYTHON=/home/runner/workspace/.pythonlibs/bin/python3.11
PYTHONLIBS=/home/runner/workspace/.pythonlibs

echo "Python: $PYTHON"

# Usar pip das pythonlibs diretamente
export PATH="$PYTHONLIBS/bin:$PATH"
export PYTHONPATH="$PYTHONLIBS/lib/python3.11/site-packages:$PYTHONPATH"

echo "📦 Instalando dependências Python..."
$PYTHON -m pip install -q \
  --target="$PYTHONLIBS/lib/python3.11/site-packages" \
  --upgrade \
  -r backend/requirements.txt 2>/dev/null || \
$PYTHON -m pip install -q \
  --prefix="$PYTHONLIBS" \
  -r backend/requirements.txt

echo "🎭 Playwright Chromium..."
$PYTHON -m playwright install chromium 2>/dev/null || true

echo "✅ Frontend pré-buildado"
export BROWSER_HEADLESS=true

echo ""
echo "✅ Iniciando servidor..."
echo ""

$PYTHON -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
