#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LBCC Agent — iniciando..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Python via Nix (disponível no PATH do Replit quando via Run)
PYTHON=$(which python3.11 2>/dev/null || which python3 2>/dev/null || echo "")

if [ -z "$PYTHON" ]; then
  echo "❌ Python não encontrado no PATH"
  echo "PATH=$PATH"
  exit 1
fi

echo "Python: $PYTHON"
echo "pip index: $PIP_INDEX_URL"

# Instalar em diretório local (sem precisar de root)
INSTALL_DIR="$HOME/.local"
mkdir -p "$INSTALL_DIR"

echo "📦 Instalando dependências Python..."
$PYTHON -m pip install \
  --user \
  --quiet \
  --index-url "${PIP_INDEX_URL:-https://pypi.org/simple/}" \
  -r backend/requirements.txt

# Playwright usa o Chromium do Replit (sem precisar instalar)
echo "🎭 Configurando Playwright..."
export PLAYWRIGHT_BROWSERS_PATH="${REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE%/chromium-*/chrome-linux/chrome}"
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1

echo "✅ Frontend pré-buildado"
export BROWSER_HEADLESS=true

echo ""
echo "✅ Iniciando servidor..."
echo ""

$PYTHON -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
