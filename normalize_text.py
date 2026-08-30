"""Module pour normaliser le texte."""

import re

def normalize_text(text: str) -> str:
    """Normalise le texte en retirant les espaces supplémentaires et en le mettant en minuscules."""
    normalized = re.sub(r'\s+', ' ', text).strip()  # Remplace les espaces multiples par un simple espace
    return normalized.lower()  # Convertit en minuscules

