"""Module pour normaliser le texte."""

import re
import unicodedata

def normalize_text(text: str) -> str:
    """Normalise le texte:
    1. Retire les accents (NFD + suppression des diacritiques)
    2. Remplace les espaces multiples par un espace simple
    3. Convertit en minuscules
    """
    if not text:
        return ""
    # Décomposer les caractères accentués et retirer les diacritiques
    normalized = unicodedata.normalize('NFD', text)
    normalized = ''.join(char for char in normalized if unicodedata.category(char) != 'Mn')
    # Nettoyer les espaces
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized.lower()
