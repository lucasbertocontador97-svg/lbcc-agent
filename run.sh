#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LBCC Agent — iniciando..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Encontrar Python no Replit
PYTHON=""
for p in /home/runner/workspace/.pythonlibs/bin/python3.11 \
          /usr/local/bin/python3.11 \
          /usr/bin/python3.11 \
          /home/runner/workspace/.pythonlibs/bin/python3 \
          $(which python3 2>/dev/null) \
          $(which python 2>/dev/null); do
  if [ -x "$p" ]; then
    PYTHON="$p"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo "❌ Python não encontrado!"
  exit 1
fi

echo "Python: $PYTHON"

# pip via pythonlibs
PIP_DIR=$(dirname "$PYTHON")
PIP=""
for p in "$PIP_DIR/pip3" "$PIP_DIR/pip" "/home/runner/workspace/.pythonlibs/bin/pip3"; do
  if [ -x "$p" ]; then
    PIP="$p"
    break
  fi
done

if [ -z "$PIP" ]; then
  echo "pip não encontrado, usando python -m pip..."
  PIP="$PYTHON -m pip"
fi

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
