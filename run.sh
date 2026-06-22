#!/bin/bash
set -e

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  LBCC Agent — iniciando..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Python deps ───────────────────────────────────────────────────────────────
echo "📦 Instalando dependências Python..."
pip install -q -r backend/requirements.txt

# ── Playwright — usa o Chromium do sistema no Replit ─────────────────────────
echo "🎭 Configurando Playwright..."
playwright install chromium 2>/dev/null || true

# ── Frontend ──────────────────────────────────────────────────────────────────
echo "📦 Instalando dependências Node..."
cd frontend
npm install --silent

echo "🔨 Buildando frontend..."
npm run build
cd ..

# ── Variável de ambiente para headless ────────────────────────────────────────
export BROWSER_HEADLESS=true

# ── Iniciar backend (serve frontend buildado também) ─────────────────────────
echo ""
echo "✅ Pronto! Acesse pela aba Webview do Replit."
echo ""

uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
