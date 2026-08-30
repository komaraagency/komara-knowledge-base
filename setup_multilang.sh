#!/bin/bash

# Script de création de la structure multilingue pour Komara Agency
# Usage: bash setup_multilang.sh

set -e

echo "🌍 Création de la structure multilingue Komara Agency..."

# Création des dossiers
mkdir -p lang/fr/dialogues
mkdir -p lang/en/dialogues
mkdir -p lang/ar/dialogues
mkdir -p lang/es/dialogues

echo "✅ Dossiers créés"

# Copie des fichiers français de secours (racine -> lang/fr/)
if [ -f "kb.json" ]; then
    cp kb.json lang/fr/kb.json
    echo "✅ kb.json copié vers lang/fr/"
fi

if [ -f "docs/faq.md" ]; then
    cp docs/faq.md lang/fr/faq.md
    echo "✅ faq.md copié vers lang/fr/"
fi

if [ -d "docs/dialogues" ]; then
    cp -r docs/dialogues/* lang/fr/dialogues/ 2>/dev/null || true
    echo "✅ Dialogues copiés vers lang/fr/"
fi

echo ""
echo "📋 Structure créée :"
tree lang/ 2>/dev/null || find lang/ -type f -o -type d | head -30

echo ""
echo "🎯 Prochaines étapes :"
echo "1. Les fichiers anglais, arabe et espagnol sont déjà dans lang/en/, lang/ar/, lang/es/"
echo "2. Testez le bot avec des messages dans différentes langues"
echo "3. Ajoutez vos propres dialogues dans lang/*/dialogues/"
echo ""
echo "✨ Configuration multilingue terminée !"

