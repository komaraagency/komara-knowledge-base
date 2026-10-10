# ---------------------------------------------------------------------------
# image_safety.py — Blindage anti-NSFW des images générées (Boss 09/10)
#
# BUG CRITIQUE (captures 09/10 23h51-23h53) : « image de banane tricolore »
# et « lion en costume style affiche pro » ont livré des NUS. Le SAFETY_LOCK
# du 06/10 ne suffit pas : Flux ne comprend pas les négations (« no nudity »
# lui SOUFFLE le mot « nudity »), et un prompt bourré de vocabulaire
# portrait (« skin », « 85mm ») fait dériver n'importe quel sujet vers une
# femme. On ne se fie donc JAMAIS au prompt seul. Trois couches serveur :
#
#   1. FILTRE ENTRANT  : demande sexuelle/nue -> refus immédiat, aucun
#                         appel au générateur.
#   2. PROMPT POSITIF  : vocabulaire vêtu/pro, ZÉRO négation sexuelle.
#   3. CONTRÔLE SORTANT: détecteur local NudeNet (ONNX CPU, ~35 ms, aucun
#                         API externe) scanne l'image AVANT l'envoi. Nu
#                         détecté -> image détruite, jamais envoyée.
#
# Dégradation : si NudeNet est absent, on échoue FERMÉ (image refusée)
# tant que IMG_SAFETY_STRICT=1 (défaut) — mieux vaut un « réessaie » qu'un
# nu livré à un client.
# ---------------------------------------------------------------------------
import logging
import os
import re
import threading
import unicodedata

logger = logging.getLogger("komara.img_safety")

STRICT = os.getenv("IMG_SAFETY_STRICT", "1").lower() in {"1", "true", "yes", "on"}
# Seuil de confiance NudeNet (0-1). Bas = plus sévère.
THRESHOLD = float(os.getenv("IMG_SAFETY_THRESHOLD", "0.35"))

# Classes NudeNet qui bloquent l'envoi (parties EXPOSÉES uniquement :
# les variantes *_COVERED, FACE, ARMPITS, FEET ne bloquent pas).
BLOCKED_CLASSES = {
    "FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED", "ANUS_EXPOSED", "BUTTOCKS_EXPOSED",
    "MALE_BREAST_EXPOSED",
}

# ── 1. FILTRE ENTRANT ──────────────────────────────────────────────────────
# Racines sans accents, comparées sur texte normalisé. On bloque la demande
# explicitement sexuelle/nue ; on laisse passer « plage », « maillot de
# bain pub » etc. (ni nu, ni sexuel).
_BLOCK_PATTERNS = [
    r"\bnu(e|es|s|de|des|dite|dites)?\b", r"\bnud(e|es|ity|ite|ites)\b",
    r"\bnaked\b", r"\btopless\b", r"\bnsfw\b", r"\bporn\w*\b", r"\bxxx\b",
    r"\bsexy\b", r"\bsexe\b", r"\bsexuel\w*\b", r"\bsexual\w*\b", r"\berotic\w*\b",
    r"\berotiq\w*\b", r"\bhentai\b", r"\bfetish\w*\b", r"\bbikini\b",
    r"\blingerie\b", r"\bseins?\b", r"\bboobs?\b", r"\bbreasts?\b", r"\btetons?\b",
    r"\bfesses?\b", r"\bbutt\b", r"\bpenis\b", r"\bbite\b", r"\bvagin\w*\b",
    r"\bchatte\b", r"\bpussy\b", r"\bcul\b", r"\bculotte\b", r"\bstrip\w*\b",
    r"\bsans\s+vetements?\b", r"\bsans\s+habits?\b", r"\bdeshabill\w*\b",
    r"\bundress\w*\b", r"\bnue?s?\s+(?:comme|integral)", r"\bseins?\s+nus?\b",
    r"\bexplicit\w*\b", r"\bsensuel\w*\b", r"\bsensual\w*\b", r"\bseduct\w*\b",
    r"\bprovocant\w*\b", r"\bpoitrine\b", r"\bcleavage\b", r"\bmineur\w*\s+nu",
]
_BLOCK_RE = re.compile("|".join(_BLOCK_PATTERNS), re.IGNORECASE)


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.lower()


def is_prompt_unsafe(prompt: str) -> bool:
    """True si la demande est sexuelle/nue (refus immédiat, pas d'appel API)."""
    return bool(_BLOCK_RE.search(_norm(prompt)))


# ── 2. PROMPT POSITIF ──────────────────────────────────────────────────────
# Vocabulaire que Flux comprend : on décrit ce qu'on VEUT voir, jamais ce
# qu'on veut éviter (une négation sexuelle réintroduit le concept).
POSITIVE_SAFE_PERSON = (
    ", wearing elegant full-coverage clothing, professional business "
    "attire, buttoned-up, modest, corporate brand photo"
)
POSITIVE_SAFE_GENERIC = (
    ", professional commercial product photography, clean advertising "
    "poster composition"
)

# Négations/termes à RETIRER du prompt envoyé au générateur (ils soufflent
# le concept). Appliqué sur le prompt FINAL assemblé.
_STRIP_FROM_FINAL = [
    r",?\s*fully clothed", r",?\s*modest attire", r",?\s*safe-for-work",
    r",?\s*no nudity", r",?\s*no explicit or sexual content",
    r",?\s*family-friendly", r",?\s*NO smooth skin",
]


def sanitize_final_prompt(final_prompt: str) -> str:
    out = final_prompt
    for pat in _STRIP_FROM_FINAL:
        out = re.sub(pat, "", out, flags=re.IGNORECASE)
    return out


# ── 3. CONTRÔLE SORTANT ────────────────────────────────────────────────────
_detector = None
_detector_lock = threading.Lock()
_detector_failed = False


def _get_detector():
    global _detector, _detector_failed
    if _detector is not None or _detector_failed:
        return _detector
    with _detector_lock:
        if _detector is None and not _detector_failed:
            try:
                from nudenet import NudeDetector
                _detector = NudeDetector()
                logger.info("[IMG-SAFETY] détecteur NudeNet chargé")
            except Exception:
                _detector_failed = True
                logger.exception("[IMG-SAFETY] NudeNet indisponible")
    return _detector


def image_is_unsafe(path) -> bool:
    """True si l'image contient une partie du corps exposée. ÉCHOUE FERMÉ
    (True) si le détecteur est indisponible et STRICT actif."""
    det = _get_detector()
    if det is None:
        return STRICT
    try:
        for hit in det.detect(str(path)):
            if hit.get("class") in BLOCKED_CLASSES and float(hit.get("score", 0)) >= THRESHOLD:
                logger.warning("[IMG-SAFETY] image BLOQUÉE : %s (%.2f)",
                               hit.get("class"), float(hit.get("score", 0)))
                return True
        return False
    except Exception:
        logger.exception("[IMG-SAFETY] détection impossible")
        return STRICT
