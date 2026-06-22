#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LBCC Agent — iniciando..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo "📦 Instalando dependências Python..."
python3 -m pip install --break-system-packages -q -r backend/requirements.txt

echo "🎭 Playwright Chromium..."
python3 -m playwright install chromium 2>/dev/null || true

export BROWSER_HEADLESS=true

echo "✅ Iniciando servidor..."
python3 -m uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
