# ---------------------------------------------------------------------------
# list_context — mémorise, pour chaque chat, QUEL contenu numéroté a été
# envoyé en dernier (catalogue ou portfolio).
#
# BUG BOSS (03/10, screenshot) : un client qui vient de voir le catalogue
# ("3. Agent IA Premium — 150€ ... tape 'ajouter <numéro>'") et répond
# juste "3" se retrouvait avec une image de PORTFOLIO n°3 au lieu d'ajouter
# le produit au panier — les deux listes numérotées se marchaient dessus,
# cassant le tunnel catalogue → panier → commande (closing).
#
# Fix : chaque envoi de liste numérotée déclare son contexte ; un chiffre
# nu reçu juste après est routé vers CE contexte précis, pas vers une
# règle globale aveugle.
# ---------------------------------------------------------------------------

_LAST_LIST: dict[int, str] = {}


def set_context(chat_id: int, kind: str) -> None:
    """kind : 'catalogue', 'portfolio' ou 'services' (menu 1-4 du pitch)."""
    _LAST_LIST[chat_id] = kind


def get_context(chat_id: int) -> str:
    return _LAST_LIST.get(chat_id, "")


def clear_context(chat_id: int) -> None:
    _LAST_LIST.pop(chat_id, None)
