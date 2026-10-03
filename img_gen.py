# ---------------------------------------------------------------------------
# img_gen.py — Génération d'images simple (Pollinations, gratuit, sans clé).
# /image <description> ou « génère une image de ... »
# Tourne dans un thread séparé pour ne jamais bloquer le bot.
# ---------------------------------------------------------------------------

import logging
import os
import random
import re
import threading
from pathlib import Path
from urllib.parse import quote

import requests

logger = logging.getLogger("komara.img_gen")

BASE_DIR = Path(__file__).resolve().parent
IMG_ENABLED = os.getenv("IMG_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
IMAGES_DIR = Path(os.getenv("IMAGES_DIR", BASE_DIR / "data" / "images"))
STAMP_FONT_PATH = BASE_DIR / "assets" / "fonts" / "KomaraStamp-Bold.ttf"
KEEP_IMAGES = 30          # derniers fichiers conservés
TIMEOUT = 60              # génération + téléchargement
# URL HD PRO (Boss 03/10) : model=flux (meilleure cohérence), enhance=true
# (rendu plus pro), nologo=true (sans watermark Pollinations), 1280x1280.
POLLINATIONS = ("https://image.pollinations.ai/prompt/{p}"
                "?width=1280&height=1280&model=flux&enhance=true"
                "&nologo=true&seed={s}")

# ---------------------------------------------------------------------------
# RÈGLE 100% KOMARA (Boss 03/10, suite au clip-art « @pollinations.ai »
# visible dans le coin d'une photo générée) : ICI ON PRODUIT 100% KOMARA
# AGENCY 🇬🇳, JAMAIS une autre marque. Deux verrous complémentaires :
#   1. PROMPT : marque KOMARA AGENCY injectée + verrou photoréaliste par
#      défaut (interdit cartoon/dessin/plastique/rendu IA factice), sauf
#      si le CLIENT demande explicitement un style cartoon.
#   2. POST-TRAITEMENT (fiable, indépendant du modèle) : tout clip-art/
#      watermark qu'un modèle externe pourrait coller dans le coin est
#      recouvert par notre propre tampon « KOMARA AGENCY 🇬🇳 » doré —
#      aucune marque tierce ne doit jamais être visible sur un visuel livré.
# ---------------------------------------------------------------------------
BRAND_TAG = "KOMARA AGENCY 🇬🇳"

# Mots qui autorisent le cartoon — UNIQUEMENT si le client le demande
CARTOON_KEYWORDS = (
    "cartoon", "dessin", "anime", "animé", "animée", "manga", "pixar",
    "3d cartoon", "disney", "chibi", "comics", "bd ", "bande dessinée",
)

# Verrou photoréaliste par défaut (gen_komara_100, Boss 03/10) : aucune
# photo « plastique »/IA factice ne doit sortir sans que le client l'ait
# demandé.
REALISM_LOCK = (
    ", photorealistic, ultra realistic, premium luxury photo, 8k, sharp "
    "focus, real texture, professional photography, "
    "NO cartoon, NO drawing, NO anime, NO plastic, NO fake AI look, "
    "no other brand logo, no third-party watermark"
)

# LOGO RULE (Boss 03/10) : pour un logo, JAMAIS de personne — un logo
# minimaliste luxe, fond noir + or premium. La variante KOMARA (K doré)
# s'applique aux demandes génériques/KOMARA ; un client qui nomme SON
# activité (« logo pour mon resto ») garde SA marque, sans le K de Komara.
LOGO_TRIGGER = ("logo", "logos", "logotype", "emblème", "embleme", "sigle")
LOGO_PROTOCOL_KOMARA = (
    ", minimalist luxury logo for KOMARA AGENCY, elegant golden letter "
    "K emblem, deep black background, prestige gold #D4AF37 accents, "
    "premium branding style, clean vector emblem, no person, no faces, "
    "no characters, high contrast, ultra detailed, no other brand logo"
)
LOGO_PROTOCOL_CLIENT = (
    ", minimalist luxury logo emblem, deep black background, prestige "
    "gold #D4AF37 accents, premium branding style, clean vector emblem, "
    "no person, no faces, no characters, high contrast, ultra detailed, "
    "no other brand logo"
)
# Pitch Pack Premium quand le visuel généré est un logo.
LOGO_DONE_FR = ("✨ Je peux te générer une base, mais pour un logo pro sans "
                "watermark retouché par notre équipe, c'est dans le Pack "
                "Premium 150€. Tu veux que je lance la version pro ?")

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


def _is_logo_prompt(prompt: str) -> bool:
    """Le client demande-t-il un LOGO (et non un visuel photo) ?"""
    low = (prompt or "").lower()
    return any(t in low for t in LOGO_TRIGGER)


def _is_cartoon_allowed(prompt: str) -> bool:
    """Le CLIENT a-t-il explicitement demandé un style cartoon/dessin ?
    Par défaut (gen_komara_100, Boss 03/10) le cartoon/plastique est
    INTERDIT — seule une demande explicite du client l'autorise."""
    low = (prompt or "").lower()
    return any(w in low for w in CARTOON_KEYWORDS)


def _with_8k_protocol(prompt: str) -> str:
    """Colle le bon protocole au prompt client, sans changer son sens.

    RÈGLE 100% KOMARA (Boss 03/10) : ici on produit 100% KOMARA AGENCY,
    jamais une autre marque — la marque est injectée dans le prompt, et
    le rendu plastique/cartoon/IA factice est verrouillé par défaut.
    LOGO : logo minimaliste luxe KOMARA (K doré, fond noir, premium,
    AUCUNE personne) — jamais le protocole photo peau/9:16.
    CARTOON explicitement demandé par le client : on respecte, mais
    toujours sous la marque KOMARA AGENCY.
    Sinon (défaut) : marque + verrou photoréaliste + protocole 8K + 9:16
    vertical (sauf bannières)."""
    if not prompt:
        return prompt
    if _is_logo_prompt(prompt):
        low = prompt.lower()
        # Le client nomme SA marque/activité → SA version (pas le K Komara)
        has_own_brand = any(w in low for w in (
            "mon resto", "ma boutique", "mon business", "mon entreprise",
            "ma marque", "mon shop", "mon salon", "mon hôtel", "mon hotel",
            "ma société", "for my", "pour mon", "pour ma"))
        protocol = LOGO_PROTOCOL_CLIENT if has_own_brand else LOGO_PROTOCOL_KOMARA
        return prompt + protocol
    if _is_cartoon_allowed(prompt):
        # Le client a demandé cartoon → on respecte, sous la marque Komara
        return f"{prompt}, {BRAND_TAG} style"
    # PAR DÉFAUT : marque KOMARA + interdiction cartoon/plastique/IA factice
    out = f"{prompt}, {BRAND_TAG}" + PROTOCOL_8K + REALISM_LOCK
    low = prompt.lower()
    if "logo" not in low and "banniere" not in low and "bannière" not in low and "banner" not in low:
        out += ", vertical 9:16 format"
    return out


def _done_caption(prompt: str, lang: str) -> str:
    """Caption finale : pitch Pack Premium 150€ pour un logo."""
    if _is_logo_prompt(prompt):
        return LOGO_DONE_FR
    return _m(lang, "done")


def _stamp_brand(path: Path) -> None:
    """Recouvre tout clip-art/watermark tiers (ex: « @pollinations.ai »
    collé par le modèle dans un coin) par notre propre tampon doré
    « KOMARA AGENCY 🇬🇳 » — fiable car indépendant du prompt/modèle.
    Règle d'or (Boss 03/10) : ici on produit 100% Komara Agency,
    jamais une autre marque visible sur un visuel livré.
    Best-effort : si Pillow/la police manquent, l'image part sans tampon
    plutôt que de bloquer l'envoi."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.open(path).convert("RGB")
        w, h = img.size
        draw = ImageDraw.Draw(img, "RGBA")

        text = "KOMARA AGENCY"
        font_size = max(18, w // 28)
        try:
            font = ImageFont.truetype(str(STAMP_FONT_PATH), font_size)
        except Exception:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), text, font=font)
        text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad_x, pad_y = int(font_size * 0.6), int(font_size * 0.4)
        band_h = text_h + pad_y * 2
        # Bandeau noir semi-opaque PLEINE LARGEUR en bas de l'image : masque
        # fiablement n'importe quel clip-art tiers déjà présent dans ce coin.
        draw.rectangle([0, h - band_h, w, h], fill=(10, 10, 10, 215))
        tx = w - text_w - pad_x - bbox[0]
        ty = h - band_h + pad_y - bbox[1]
        draw.text((tx, ty), text, font=font, fill=(212, 175, 55, 255))  # #D4AF37

        img.save(path, "JPEG", quality=92)
    except Exception:
        logger.exception("Tampon KOMARA AGENCY impossible (image envoyée sans tampon)")


def _generate_and_send(bot, chat_id: int, prompt: str, lang: str) -> None:
    """Thread worker : télécharge l'image, tamponne la marque, puis l'envoie."""
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
            _stamp_brand(path)
            with open(path, "rb") as f:
                bot.send_photo(chat_id, f, caption=_done_caption(prompt, lang))
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
