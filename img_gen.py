# ---------------------------------------------------------------------------
# img_gen.py — Génération d'images simple (Pollinations, gratuit, sans clé).
# /image <description> ou « génère une image de ... »
# Tourne dans un thread séparé pour ne jamais bloquer le bot.
# ---------------------------------------------------------------------------

import os
import random
import re
import threading
from pathlib import Path
from urllib.parse import quote

import requests

BASE_DIR = Path(__file__).resolve().parent
IMG_ENABLED = os.getenv("IMG_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
IMAGES_DIR = Path(os.getenv("IMAGES_DIR", BASE_DIR / "data" / "images"))
KEEP_IMAGES = 30          # derniers fichiers conservés
TIMEOUT = 60              # génération + téléchargement
POLLINATIONS = "https://image.pollinations.ai/prompt/{p}?width=1024&height=1024&nologo=true&seed={s}"

# Déclencheurs (FR/EN/ES/AR) — routing simple et déterministe
_TRIGGERS = [
    r"^/imagine?\b", r"^/image\b", r"^/photo\b", r"^/dessin\b",
    r"\bgen[ée]re (?:moi )?une image\b", r"\bgen[ée]re (?:moi )?un logo\b",
    r"\bcr[ée][ée] (?:moi )?une image\b", r"\bcr[ée][ée]r (?:moi )?une image\b",
    r"\bfais (?:moi )?une image\b", r"\bdessine\b", r"\bfais (?:moi )?un dessin\b",
    r"\bimage de\b",
    r"\bgenerate (?:me )?an image\b", r"\bcreate (?:me )?an image\b", r"\bdraw me\b", r"\bmake me an image\b",
    r"\bcrea (?:me )?una imagen\b", r"\bgenera (?:me )?una imagen\b", r"\bdibuja\b",
    r"\bأنشئ صورة\b", r"\bاصنع صورة\b", r"\bارسم\b",
]
_TRIGGERS_RE = [re.compile(p, re.IGNORECASE) for p in _TRIGGERS]

MESSAGES = {
    "fr": {"working": "🎨 Je crée ton image, quelques secondes…",
           "ask": "🎨 Décris-moi l'image que tu veux : sujet, style, couleurs. Exemple : « image de un lion en costume, style affiche pro »",
           "error": "😕 La génération a échoué cette fois. Renvoie ta demande, ou écris « catalogue » : l'équipe Komara te construit un agent IA sur devis.",
           "done": "✨ Ton image est prête ! Tu veux une variante ou une version pro retouchée par l'équipe Komara ?"},
    "en": {"working": "🎨 Creating your image, a few seconds…",
           "ask": "🎨 Describe the image you want: subject, style, colors. Example: « image of a lion in a suit, pro poster style »",
           "error": "😕 Generation failed this time. Try again, or type « visuals »: the Komara team makes pro custom visuals on quote.",
           "done": "✨ Your image is ready! Want a variant or a pro version polished by the Komara team?"},
    "es": {"working": "🎨 Creando tu imagen, unos segundos…",
           "ask": "🎨 Describe la imagen que quieres: tema, estilo, colores. Ejemplo: « imagen de un león con traje, estilo póster pro »",
           "error": "😕 La generación falló esta vez. Inténtalo de nuevo o escribe « visuales »: el equipo Komara hace visuales pro a presupuesto.",
           "done": "✨ ¡Tu imagen está lista! ¿Quieres una variante o una versión pro retocada por el equipo Komara?"},
    "ar": {"working": "🎨 أنشئ صورتك، بضع ثوان…",
           "ask": "🎨 صف لي الصورة التي تريدها: الموضوع، الأسلوب، الألوان. مثال: « صورة أسد ببدلة، أسلوب ملصق احترافي »",
           "error": "😕 فشل الإنشاء هذه المرة. أعد المحاولة أو اكتب « تصاميم »: فريق كومارا يصنع تصاميم احترافية بسعر على الطلب.",
           "done": "✨ صورتك جاهزة! تريد نسخة أخرى أو نسخة احترافية بلمسة فريق كومارا؟"},
}


def _m(lang: str, key: str) -> str:
    return MESSAGES.get(lang, MESSAGES["fr"])[key]


def extract_prompt(text: str):
    """Retourne la description si le message demande une image, sinon None."""
    if not IMG_ENABLED or not text:
        return None
    low = text.lower()
    # « image de vos réalisations / portfolio » → c'est le vrai portfolio,
    # pas une génération : on laisse la KB répondre.
    if any(w in low for w in ("vos exemples", "vos réalisations", "votre portfolio",
                              "vos projets", "portfolio", "your work", "tus trabajos")):
        return None
    for rex in _TRIGGERS_RE:
        m = rex.search(text)
        if m:
            prompt = text[m.end():].strip() if m.end() > 0 else ""
            # /image <prompt> : le prompt suit la commande
            if m.group(0).startswith("/") and not prompt:
                return ""     # commande sans description
            return prompt
    return None


def _prune_images() -> None:
    try:
        files = sorted(IMAGES_DIR.glob("img_*.jpg"), key=lambda p: p.name, reverse=True)
        for old in files[KEEP_IMAGES:]:
            old.unlink(missing_ok=True)
    except Exception:
        pass


def _typing_keeper(bot, chat_id: int, stop: "threading.Event") -> None:
    """Garde l'indicateur « tape… » vivant pendant une génération longue
    (l'indicateur Telegram expire au bout de ~5 secondes)."""
    while not stop.wait(4.0):
        try:
            bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass


# Protocole 8K — knowledge.txt (identité KOMARA IA, Luxury African)
PROTOCOL_8K = (
    ", shot on Sony A7R V, 85mm lens, f/1.8, shallow depth of field, "
    "deep black and prestige gold #D4AF37 color palette, luxury african "
    "aesthetic, real skin texture, visible pores, natural reflections, "
    "no artificial smoothing, ultra detailed, 8K quality"
)


def _with_8k_protocol(prompt: str) -> str:
    """Colle le protocole 8K de knowledge.txt au prompt client.
    Le 9:16 vertical s'applique aux visuels réseaux (pas aux logos)."""
    if not prompt:
        return prompt
    out = prompt + PROTOCOL_8K
    low = prompt.lower()
    if "logo" not in low and "banniere" not in low and "bannière" not in low and "banner" not in low:
        out += ", vertical 9:16 format"
    return out


def _generate_and_send(bot, chat_id: int, prompt: str, lang: str) -> None:
    """Thread worker : télécharge l'image puis l'envoie."""
    stop = threading.Event()
    threading.Thread(target=_typing_keeper, args=(bot, chat_id, stop), daemon=True).start()
    try:
        url = POLLINATIONS.format(p=quote(_with_8k_protocol(prompt)), s=random.randint(1, 10**6))
        resp = requests.get(url, timeout=TIMEOUT)
        if resp.status_code == 200 and resp.content[:2] == b"\xff\xd8":
            IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            name = f"img_{chat_id}_{random.randint(10**9, 10**10)}.jpg"
            path = IMAGES_DIR / name
            path.write_bytes(resp.content)
            with open(path, "rb") as f:
                bot.send_photo(chat_id, f, caption=_m(lang, "done"))
            _prune_images()
            return
        bot.send_message(chat_id, _m(lang, "error"))
    except Exception:
        try:
            bot.send_message(chat_id, _m(lang, "error"))
        except Exception:
            pass
    finally:
        stop.set()


def handle_image_request(bot, chat_id: int, text: str, lang: str) -> bool:
    """Routing : True = message consommé (image demandée)."""
    prompt = extract_prompt(text)
    if prompt is None:
        return False
    if not prompt:
        bot.send_message(chat_id, _m(lang, "ask"))
        return True
    bot.send_message(chat_id, _m(lang, "working"))
    threading.Thread(
        target=_generate_and_send, args=(bot, chat_id, prompt, lang), daemon=True,
    ).start()
    return True
