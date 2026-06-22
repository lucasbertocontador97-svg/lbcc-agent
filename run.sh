#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LBCC Agent — iniciando..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Python deps
echo "📦 Instalando dependências Python..."
pip install -q -r backend/requirements.txt

# Playwright — instala Chromium próprio se o do sistema não funcionar
echo "🎭 Instalando Playwright Chromium..."
playwright install chromium --with-deps 2>/dev/null || playwright install chromium

# Node deps e build do frontend
echo "📦 Node..."
cd frontend
npm install --silent
echo "🔨 Buildando frontend..."
npm run build
cd ..

# Headless obrigatório no Replit
export BROWSER_HEADLESS=true

echo ""
echo "✅ Iniciando servidor..."
echo ""

uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
