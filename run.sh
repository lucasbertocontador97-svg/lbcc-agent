#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LBCC Agent — iniciando..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Buscar Python em todos os lugares possíveis do Replit
PYTHON=""
for p in \
  /home/runner/workspace/.pythonlibs/bin/python3.11 \
  /home/runner/workspace/.pythonlibs/bin/python3 \
  /nix/var/nix/profiles/default/bin/python3.11 \
  /nix/var/nix/profiles/default/bin/python3 \
  /home/runner/.nix-profile/bin/python3.11 \
  /home/runner/.nix-profile/bin/python3 \
  /usr/bin/python3.11 \
  /usr/bin/python3 \
  /usr/local/bin/python3; do
  if [ -x "$p" ]; then
    PYTHON="$p"
    break
  fi
done

# Fallback: buscar em /nix/store
if [ -z "$PYTHON" ]; then
  PYTHON=$(find /nix/store -name "python3.11" -type f 2>/dev/null | grep "/bin/python3.11" | head -1)
fi

if [ -z "$PYTHON" ]; then
  echo "❌ Python não encontrado. Listando /nix/store python..."
  find /nix/store -name "python3*" -type f 2>/dev/null | grep "/bin/" | head -10
  exit 1
fi

echo "✅ Python: $PYTHON"

# Verificar pip
if ! $PYTHON -m pip --version &>/dev/null 2>&1; then
  echo "pip não encontrado, buscando..."
  PIP=$(find /nix/store -name "pip" -type f 2>/dev/null | head -1)
  if [ -n "$PIP" ]; then
    echo "pip encontrado em: $PIP"
  else
    echo "❌ pip não encontrado"
    exit 1
  fi
fi

echo "📦 Instalando dependências Python..."
$PYTHON -m pip install --user -q -r backend/requirements.txt

echo "🎭 Playwright — usando Chromium do Replit"
export PLAYWRIGHT_BROWSERS_PATH=$(dirname $(dirname $(dirname "$REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE")))

echo "✅ Frontend pré-buildado"
export BROWSER_HEADLESS=true

echo "✅ Iniciando servidor..."
$PYTHON -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
