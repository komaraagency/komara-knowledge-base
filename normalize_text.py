"""Normalisation de texte pour matching fuzzy + intention.
Le bot doit comprendre l'intention, pas juste les mots-clés exacts.
"""

import re
import unicodedata

def normalize_text(text: str) -> str:
    """Alias de normalize() pour compatibilité avec l'ancien code."""
    return normalize(text)


# Corrections orthographiques courantes (FR)
COMMON_FIXES = {
    "bonjor": "bonjour", "bonjur": "bonjour", "bjr": "bonjour",
    "bonsoir": "bonsoir", "bsr": "bonsoir",
    "komara": "komara", "komra": "komara", "komarra": "komara",
    "agence": "agence", "agenc": "agence",
    "prix": "prix", "prx": "prix", "combien": "combien", "comb1": "combien",
    "bot": "bot", "chatbot": "chatbot", "chat boot": "chatbot",
    "whatsapp": "whatsapp", "whatsap": "whatsapp", "watsapp": "whatsapp",
    "telegram": "telegram", "telegrm": "telegram",
    "site": "site", "siteweb": "site web", "site web": "site web",
    "logo": "logo", "logos": "logo",
    "automatiser": "automatiser", "automatisation": "automatisation",
    "agent": "agent", "agents": "agent",
    "ia": "ia", "ai": "ia", "inteligence": "intelligence",
    "commercial": "commercial", "commerciale": "commercial",
    "vend": "vendre", "vendre": "vendre", "vente": "vente",
    "devis": "devis", "dvis": "devis",
    "rdv": "rdv", "rendez": "rdv", "rendez-vous": "rdv",
    "merci": "merci", "mrci": "merci",
    "salut": "salut", "slt": "salut", "coucou": "salut",
    "comment": "comment", "coment": "comment",
    "facebook": "facebook", "facebok": "facebook", "fb": "facebook",
    "messenger": "messenger", "messengr": "messenger",
    "instagram": "instagram", "insta": "instagram",
    "orange": "orange", "money": "money",
    "client": "client", "clients": "client",
    "service": "service", "servic": "service",
    "formation": "formation", "formtion": "formation",
    "restaurant": "restaurant", "resto": "restaurant",
    "clinique": "clinique", "sante": "sante",
    "immobilier": "immobilier", "immo": "immobilier",
    "ecommerce": "ecommerce", "e-commerce": "ecommerce",
    "abonnement": "abonnement", "abonement": "abonnement",
    "garantie": "garantie", "garanti": "garantie",
    "securite": "securite", "sécurité": "securite",
    "support": "support", "supor": "support",
    "coute": "coute", "couter": "coute", "cout": "coute",
    "marche": "marche", "fonctionne": "marche",
    "pourquoi": "pourquoi", "pourkoi": "pourquoi",
    "langue": "langue", "langues": "langue",
    "vocal": "vocal", "voix": "vocal",
}

# Mapping intention → mots associés (pour la détection d'intention)
INTENT_MAP = {
    "prix_tarif": ["prix", "cout", "combien", "tarif", "coute", "cher", "paye", "euro", "fg", "mad", "argent", "budget", "commission", "abonnement", "mensuel", "facture", "devis"],
    "presentation": ["komara", "agence", "presentation", "qui", "faites", "services", "propose", "quipe", "c'est quoi"],
    "agent_vs_chatbot": ["agent", "chatbot", "difference", "vs", "comparaison", "robot", "bot"],
    "automatisation": ["automatiser", "automatisation", "automatique", "automatiser business", "taches"],
    "rdv": ["rdv", "rendez", "rendez-vous", "appointment", "agenda", "booking", "creneau"],
    "support": ["support", "aide", "aide", "probleme", "urgence", "erreur", "bloque", "panne", "assistance"],
    "technique": ["technique", "securise", "securite", "rgpd", "hebergement", "serveur", "api", "cloud", "ollama", "connexion", "internet", "3g"],
    "langues": ["langue", "langues", "francais", "anglais", "soussou", "malinke", "pular", "multilingue"],
    "whatsapp": ["whatsapp", "whatsap", "watsapp", "messenger", "instagram", "api"],
    "resultats": ["resultat", "ventes", "ca", "chiffre", "roi", "rentabiliser", "augmenter", "fidele"],
    "garantie": ["garantie", "satisfait", "remboursement", "essai", "test", "demo"],
    "secteur": ["secteur", "restaurant", "immobilier", "clinique", "formation", "ecommerce", "garage", "voyage", "coach", "photographe", "avocat", "ecole", "coiffure", "ong", "voiture"],
    "objection": ["trop cher", "pas besoin", "pas sur", "peur", "robot", "remplacer", "complique", "pas tech", "arret", "mentir", "marche afrique"],
    "demarrer": ["commencer", "demarrer", "on commence", "lancer", "je commence", "je lance", "demo", "aujourd", "signer", "etal"],
    "futur": ["futur", "prochaine", "roadmap", "application", "app", "vision", "5 ans", "lever", "r&d", "labo"],
    "marketing": ["pub", "publicite", "facebook ads", "contenu", "tiktok", "promo", "soldes", "cross selling", "upsell", "panier", "avis", "fidelis"],
    "croissance": ["scaler", "grandir", "100 clients", "b2b", "international", "partenariat", "recruter", "fideliser"],
}


def remove_accents(text: str) -> str:
    """Supprime les accents pour un matching plus tolérant."""
    nfkd = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in nfkd if not unicodedata.combining(c))


def normalize(message: str) -> str:
    """Normalise un message: minuscules, sans accents, fautes corrigées."""
    if not message:
        return ""
    # Minuscules
    text = message.lower().strip()
    # Suppression accents
    text = remove_accents(text)
    # Correction des fautes courantes
    words = text.split()
    fixed = []
    for w in words:
        # Nettoyage ponctuation
        w_clean = re.sub(r'[^\w\s-]', '', w)
        # Correction si connue
        if w_clean in COMMON_FIXES:
            fixed.append(COMMON_FIXES[w_clean])
        else:
            fixed.append(w_clean)
    return ' '.join(fixed)


def detect_intent(message: str) -> str | None:
    """Détecte l'intention d'un message (pas juste les mots-clés).
    Retourne la catégorie d'intention ou None.
    """
    norm = normalize(message)
    if not norm:
        return None

    # Score pour chaque intention
    scores = {}
    for intent, keywords in INTENT_MAP.items():
        score = 0
        for kw in keywords:
            kw_norm = remove_accents(kw.lower())
            if kw_norm in norm:
                score += len(kw_norm.split())  # Les mots longs comptent plus
        if score > 0:
            scores[intent] = score

    if scores:
        best = max(scores, key=scores.get)
        return best
    return None


def fuzzy_match(word1: str, word2: str, threshold: float = 0.75) -> bool:
    """Match approximatif entre deux mots (tolérance fautes).
    Utilise la distance de Levenshtein simplifiée.
    """
    if not word1 or not word2:
        return False
    if word1 == word2:
        return True
    if len(word1) < 3 or len(word2) < 3:
        return word1 == word2

    # Distance de Levenshtein
    m, n = len(word1), len(word2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if word1[i - 1] == word2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
    dist = dp[m][n]
    max_len = max(m, n)
    similarity = 1 - (dist / max_len)
    return similarity >= threshold


def score_match(message: str, tags: list, questions: list) -> float:
    """Score de matching entre un message et une entrée de la KB.
    Combine matching exact, fuzzy, et intention.
    """
    norm_msg = normalize(message)
    if not norm_msg:
        return 0.0

    norm_tags = [remove_accents(t.lower()) if False else remove_accents(t.lower()) for t in tags]
    norm_questions = [remove_accents(q.lower()) for q in questions]

    msg_words = set(norm_msg.split())
    score = 0.0

    # 1. Matching direct sur tags
    for tag in norm_tags:
        tag_words = tag.split()
        tag_found = False
        for tw in tag_words:
            if tw in msg_words:
                score += 2.0
                tag_found = True
            else:
                # 2. Fuzzy match sur mots du tag
                for mw in msg_words:
                    if fuzzy_match(tw, mw, 0.75):
                        score += 1.0
                        tag_found = True
                        break
        # Bonus si le tag complet est dans le message
        if tag in norm_msg:
            score += 3.0

    # 3. Matching sur questions
    for q in norm_questions:
        q_words = set(q.split())
        overlap = msg_words & q_words
        if overlap:
            score += len(overlap) * 1.5

    return score
