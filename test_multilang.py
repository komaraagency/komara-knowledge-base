#!/usr/bin/env python3
"""Test rapide de la détection de langue et du chargement des ressources."""

import sys
from pathlib import Path

# Ajout du répertoire courant au path
sys.path.insert(0, str(Path(__file__).parent))

from rag_bot import (
    detect_language,
    get_supported_languages,
    LANG_RESOURCES,
    trouver_meilleure_reponse_multilingue,
)


def test_detection_langue():
    """Teste la détection de langue sur des exemples."""
    tests = {
        "Bonjour, combien coûte un site web ?": "fr",
        "Hello, how much does a website cost?": "en",
        "مرحبا، كم تكلفة موقع ويب؟": "ar",
        "Hola, ¿cuánto cuesta un sitio web?": "es",
        "Je veux un bot pour mon business": "fr",
        "I want a bot for my business": "en",
        "أريد بوت لنشاطي التجاري": "ar",
        "Quiero un bot para mi negocio": "es",
    }

    print("🔍 Test de détection de langue :")
    print("-" * 60)
    
    success = 0
    for text, expected in tests.items():
        detected = detect_language(text)
        status = "✅" if detected == expected else "❌"
        if detected == expected:
            success += 1
        print(f"{status} '{text[:40]}...' -> {detected} (attendu: {expected})")
    
    print(f"\nRésultat : {success}/{len(tests)} détections correctes")
    return success == len(tests)


def test_chargement_ressources():
    """Vérifie que les ressources sont chargées pour chaque langue."""
    print("\n📚 Test de chargement des ressources :")
    print("-" * 60)
    
    all_loaded = True
    for lang in get_supported_languages():
        resources = LANG_RESOURCES.get(lang, {"kb": [], "faq": [], "dialogues": []})
        kb_count = len(resources.get("kb", []))
        faq_count = len(resources.get("faq", []))
        dialogues_count = len(resources.get("dialogues", []))
        
        has_content = kb_count > 0 or faq_count > 0
        status = "✅" if has_content else "⚠️ "
        
        print(f"{status} [{lang}] kb: {kb_count} | faq: {faq_count} | dialogues: {dialogues_count}")
        
        if not has_content:
            all_loaded = False
    
    return all_loaded


def test_recherche_multilingue():
    """Teste la recherche de réponses dans différentes langues."""
    print("\n🔎 Test de recherche multilingue :")
    print("-" * 60)
    
    tests = [
        ("Quels sont vos services ?", "fr"),
        ("What services do you offer?", "en"),
        ("ما هي خدماتكم؟", "ar"),
        ("¿Qué servicios ofrecen?", "es"),
        ("Combien coûte un site web ?", "fr"),
        ("How much does a website cost?", "en"),
    ]
    
    success = 0
    for text, lang in tests:
        response = trouver_meilleure_reponse_multilingue(text, lang)
        if response:
            success += 1
            print(f"✅ [{lang}] '{text[:35]}...' -> {len(response)} caractères")
        else:
            print(f"❌ [{lang}] '{text[:35]}...' -> Pas de réponse trouvée")
    
    print(f"\nRésultat : {success}/{len(tests)} réponses trouvées")
    return success == len(tests)


if __name__ == "__main__":
    print("=" * 60)
    print("🌍 TEST MULTILINGUE KOMARA AGENCY")
    print("=" * 60)
    
    test1 = test_detection_langue()
    test2 = test_chargement_ressources()
    test3 = test_recherche_multilingue()
    
    print("\n" + "=" * 60)
    if test1 and test2 and test3:
        print("✅ TOUS LES TESTS RÉUSSIS !")
        print("=" * 60)
        sys.exit(0)
    else:
        print("⚠️  CERTAINS TESTS ONT ÉCHOUÉ")
        print("=" * 60)
        sys.exit(1)

