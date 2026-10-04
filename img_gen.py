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
import time
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
# Boss 03/10 (fix fidélité prompt) : enhance=true RETIRÉ — ce paramètre
# fait réécrire le prompt du client par une IA tierce de Pollinations
# avant génération, ce qui diluait/ignorait les instructions explicites
# (« NO plastic », ethnicité précisée...). On envoie désormais le prompt
# du client QUASI TEL QUEL (juste notre protocole collé, voir plus bas),
# sans réécriture externe.
POLLINATIONS = ("https://image.pollinations.ai/prompt/{p}"
                "?width=1280&height=1280&model=flux"
                "&nologo=true&seed={s}")
# Pollinations gate désormais ~50% des requêtes anonymes (402 Payment
# Required, constaté 03/10) — on retente avant d'abandonner.
MAX_GEN_RETRIES = 3

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
# demandé. RACCOURCI (fix fidélité 03/10) et placé juste APRÈS le prompt
# client (position de poids fort) plutôt qu'en toute fin — un prompt trop
# long dilue/tronque les dernières instructions chez beaucoup de modèles.
REALISM_LOCK = (
    ", photorealistic, real skin texture, visible pores, NO cartoon, "
    "NO drawing, NO anime, NO plastic, NO smooth skin, NO fake AI look, "
    "NO blurry, NO deformed, NO distorted face, NO extra fingers"
)
# Détails de style secondaires (poids plus faible, OK si tronqués en cas
# de prompt déjà long) : marque + palette + protocole technique.
STYLE_TAIL = (
    ", no other brand logo, no third-party watermark, shot on Sony A7R V, "
    "85mm f/1.8, deep black and prestige gold #D4AF37 palette, luxury "
    "aesthetic, ultra detailed, 8K"
)

# ---------------------------------------------------------------------------
# ETHNICITÉ (fix 03/10, Boss) : si le CLIENT précise une ethnicité
# (africain, européen, asiatique...), on la respecte strictement — jamais
# écrasée par le protocole. Si le prompt décrit une PERSONNE sans aucune
# ethnicité précisée, on applique le défaut de marque KOMARA (persona
# africaine) ; un prompt qui ne parle pas de personne (objet, logo,
# paysage...) ne reçoit AUCUN ajout d'ethnicité.
# ---------------------------------------------------------------------------
ETHNICITY_KEYWORDS = {
    "africain": ("africain", "africaine", "african", "noir", "noire",
                 "black", "guinéen", "guineen", "guinéenne", "ouest-africain",
                 "west african", "subsaharien"),
    "européen": ("européen", "européenne", "european", "caucasian",
                 "caucasien", "caucasienne", "blanche", "blanc ", "white "),
    "asiatique": ("asiatique", "asian", "chinoise", "chinois", "coréen",
                  "coreen", "coréenne", "japonaise", "japonais", "asia "),
}
PERSON_KEYWORDS = (
    "femme", "homme", "woman", "man", "personne", "person", "portrait",
    "visage", "face", "fille", "girl", "garçon", "boy", "modèle", "model",
    "client", "entrepreneur", "entrepreneure", "développeur", "developpeur",
)


def _detect_ethnicity(prompt_low: str) -> str | None:
    """Ethnicité explicitement nommée par le client dans son prompt, ou
    None si aucune (le défaut de marque s'applique alors, si personne)."""
    for name, words in ETHNICITY_KEYWORDS.items():
        if any(w in prompt_low for w in words):
            return name
    return None


def _mentions_person(prompt_low: str) -> bool:
    return any(w in prompt_low for w in PERSON_KEYWORDS)

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


# ---------------------------------------------------------------------------
# SUIIVI DE CONVERSATION IMAGE (Boss 03/10 — fix routage) :
# les captions (pitch « Tu veux que je lance la version pro ? », done
# « Tu veux une variante ou une version pro… ? ») doivent entrer dans la
# MÉMOIRE du bot, sinon le « Oui » du client tombe sur une fiche KB sans
# rapport (ex: démo multi-canaux — screenshot Boss). rag_bot enregistre
# ici son propre writer d'historique (remember), img_gen reste découplé.
# ---------------------------------------------------------------------------
HISTORY_RECORDER = None   # set_history_recorder(remember) par rag_bot
HISTORY_READER = None     # set_history_reader() par rag_bot : dernière
                          # réponse du bot pour CE chat (fraîcheur contexte)
LAST_PROMPT: dict = {}    # chat_id → dernier prompt généré (pour variante)
LAST_CAPTION: dict = {}    # chat_id → dernière caption envoyée (contexte)


def set_history_recorder(fn) -> None:
    """rag_bot injecte remember() ici (découplage, pas d'import circulaire)."""
    global HISTORY_RECORDER
    HISTORY_RECORDER = fn


def set_history_reader(fn) -> None:
    """rag_bot injecte un lecteur (dernier msg assistant du chat) — sert à
    détecter un contexte image PÉRIMÉ (fix incohérences Boss 04/10 : le
    « Oui » du client répondait à une vieille image alors que le bot
    venait de servir une fiche KB / une démo sans rapport)."""
    global HISTORY_READER
    HISTORY_READER = fn


def _remember_caption(chat_id: int, caption: str) -> None:
    """Mémorise la caption côté historique conversationnel (best-effort)."""
    LAST_CAPTION[chat_id] = caption
    try:
        if HISTORY_RECORDER:
            HISTORY_RECORDER(chat_id, "assistant", caption)
    except Exception:
        logger.exception("Caption non mémorisée (envoi continue)")


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
    # PAR DÉFAUT : négatifs/réalisme COLLÉS juste après le prompt client
    # (poids fort, fix fidélité 03/10), ethnicité respectée si précisée
    # par le client, défaut marque (africain) UNIQUEMENT si portrait sans
    # ethnicité précisée, puis détails de style secondaires en fin.
    low = prompt.lower()
    # Pas de devinette de genre : le nom (femme/homme...) est déjà dans
    # le prompt client, on ajoute juste le descripteur ethnique manquant.
    ethnicity_tag = ", West African" if (
        _detect_ethnicity(low) is None and _mentions_person(low)) else ""
    out = f"{prompt}{ethnicity_tag}{REALISM_LOCK}, {BRAND_TAG}{STYLE_TAIL}"
    if "logo" not in low and "banniere" not in low and "bannière" not in low and "banner" not in low:
        out += ", vertical 9:16 format"
    return out


def _done_caption(prompt: str, lang: str) -> str:
    """Caption finale : pitch Pack Premium 150€ pour un logo."""
    if _is_logo_prompt(prompt):
        return LOGO_DONE_FR
    return _m(lang, "done")


# LOGO OFFICIEL KOMARA AGENCY (Boss 04/10) : le K doré/vert sur fond noir,
# fichier assets/komara_logo_official.jpg. Détouré en RGBA au premier
# appel (fond quasi-noir → transparent), puis cache.
_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "komara_logo_official.jpg"
_LOGO_RGBA = None


def _load_official_logo():
    """Charge le logo officiel, détouré (fond noir → transparence) et
    recadré sur le K. Cache en RAM. None si absent/corrompu."""
    global _LOGO_RGBA
    if _LOGO_RGBA is not None:
        return _LOGO_RGBA
    try:
        from PIL import Image

        src = Image.open(_LOGO_PATH).convert("RGB")
        src = src.resize((256, int(256 * src.height / src.width)))
        rgba = src.convert("RGBA")
        px = rgba.load()
        for y in range(rgba.height):
            for x in range(rgba.width):
                r, g, b, _ = px[x, y]
                # fond quasi-noir (max des canaux < 30) → transparent
                if max(r, g, b) < 30:
                    px[x, y] = (r, g, b, 0)
        bbox = rgba.getbbox()  # recadre sur le K seul
        _LOGO_RGBA = rgba.crop(bbox) if bbox else rgba
        return _LOGO_RGBA
    except Exception:
        logger.exception("Logo officiel K illisible — fallback tampon texte")
        _LOGO_RGBA = False  # sentinelle : ne pas re-essayer à chaque image
        return None


def _stamp_brand(path: Path) -> None:
    """Marque chaque image générée du LOGO OFFICIEL KOMARA AGENCY (K
    doré/vert, fichier fourni par le Boss le 04/10) collé en bas à
    droite sur bandeau noir — recouvre au passage tout clip-art/watermark
    tiers (ex: « @pollinations.ai » collé par le modèle dans un coin).
    Règle d'or (Boss 03/10) : ici on produit 100% Komara Agency, jamais
    une autre marque visible sur un visuel livré.
    Fallback si le logo est illisible : tampon texte doré.
    Best-effort : si Pillow/la police manquent, l'image part sans tampon
    plutôt que de bloquer l'envoi."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.open(path).convert("RGB")
        w, h = img.size
        draw = ImageDraw.Draw(img, "RGBA")

        logo = _load_official_logo()
        if logo:
            # Taille : ~17% de la largeur, marge 2.5% ; bandeau noir discret
            # en bas PLEINE LARGEUR pour la lisibilité + anti clip-art tiers.
            lw = max(90, int(w * 0.17))
            lh = int(lw * logo.height / logo.width)
            if lh > h // 3:          # jamais un logo gigantesque
                lh = h // 3
                lw = int(lh * logo.width / logo.height)
            band_h = lh + int(h * 0.028)
            draw.rectangle([0, h - band_h, w, h], fill=(10, 10, 10, 200))
            resized = logo.resize((lw, lh), Image.LANCZOS)
            img.paste(resized, (w - lw - int(w * 0.025),
                                h - band_h + (band_h - lh) // 2), resized)
            img.save(path, "JPEG", quality=92)
            return


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


def _fetch_image(prompt: str) -> bytes | None:
    """Télécharge l'image chez Pollinations, avec retry (Boss 03/10 :
    le endpoint anonyme gate désormais ~50% des requêtes en 402 Payment
    Required — un simple retry suffit presque toujours)."""
    full_prompt = _with_8k_protocol(prompt)
    for attempt in range(MAX_GEN_RETRIES):
        try:
            url = POLLINATIONS.format(p=quote(full_prompt), s=random.randint(1, 10**6))
            resp = requests.get(url, timeout=TIMEOUT)
            if resp.status_code == 200 and resp.content[:2] == b"\xff\xd8":
                return resp.content
        except Exception:
            logger.exception("Échec génération image (tentative %s/%s)", attempt + 1, MAX_GEN_RETRIES)
        if attempt < MAX_GEN_RETRIES - 1:
            time.sleep(1.5)
    return None


def _generate_and_send(bot, chat_id: int, prompt: str, lang: str) -> None:
    """Thread worker : télécharge l'image, tamponne la marque, puis l'envoie."""
    stop = threading.Event()
    threading.Thread(target=_typing_keeper, args=(bot, chat_id, stop), daemon=True).start()
    try:
        content = _fetch_image(prompt)
        if content:
            IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            name = f"img_{chat_id}_{random.randint(10**9, 10**10)}.jpg"
            path = IMAGES_DIR / name
            path.write_bytes(content)
            _stamp_brand(path)
            caption = _done_caption(prompt, lang)
            LAST_PROMPT[chat_id] = prompt   # « variante » → regénère ce prompt
            with open(path, "rb") as f:
                bot.send_photo(chat_id, f, caption=caption)
            _remember_caption(chat_id, caption)
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


# ---------------------------------------------------------------------------
# ROUTAGE DES SUIVIS IMAGE (Boss 03/10 — fix « Oui » → démo sans rapport
# et « lance la version pro » → question sans réponse) :
#   • « Oui » après le pitch logo  → lance le devis Pack Premium 150€
#   • « lance la version pro »     → idem (n'importe quand après une image)
#   • « variante » / « autre »     → regénère le même prompt (nouveau seed)
#   • « Oui » après le done        → boutons Variante / Version pro
#   • « non »                      → clôture polie, le rendu reste gratuit
# ---------------------------------------------------------------------------
_PRO_TRIGGERS = (
    "version pro", "lance le pro", "lance la pro", "pack premium",
    "va pour le pack", "va pour la version", "je veux le pack",
    "je veux la version", "lance le premium", "upgrade", "lance-le",
    "lance la", "envoie la version", "fais la version",
)
_VARIANTE_TRIGGERS = (
    "variante", "une variante", "autre version", "une autre version",
    "une autre", "regenere", "regénère", "régénère", "refais-la",
    "refais la", "refais une", "encore une",
)
_NEG_WORDS = ("non", "no", "nope", "لا", "nan", "non merci")

def _start_premium_devis(bot, chat_id: int, lang: str, prompt: str) -> None:
    """« Oui » / « version pro » → tunnel devis avec la demande d'origine
    (Pack Premium 150€) — jamais de fiche au hasard, jamais de démo."""
    import actions
    details = f"Version pro Pack Premium 150€ (logo/visuel retouché équipe) — demande d'origine : « {prompt[:200]} »"
    bot.send_message(
        chat_id,
        "🔥 Parfait, on lance ta version pro ! 💎\n"
        "Pack Premium — 150€ (logo/visuel pro retouché par l'équipe, sans "
        "watermark, fichiers HD + sources).\n\n"
        "Quelques infos pour ton devis 👇")
    actions.start_flow(bot, chat_id, "devis", lang, trigger_text=details)
    # NOTE : le contexte image n'est PAS vidé ici — un client qui relance
    # « version pro » ou « variante » après le devis reste bien routé.
    # Il est vidé par « non » ou remplacé par la prochaine image.


def _ask_variant_or_pro(bot, chat_id: int, lang: str) -> None:
    """« Oui » après le done : le client veut quoi exactement ? On DEMANDE
    (routage par boutons, zéro devinette)."""
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("🔄 Une variante (gratuite)", callback_data="kmr_img_variante"),
           InlineKeyboardButton("💎 Version pro 150€", callback_data="kmr_img_pro"))
    bot.send_message(chat_id, "Avec plaisir 😊 Tu veux plutôt :", reply_markup=kb)


def _regenerate_variant(bot, chat_id: int, lang: str) -> None:
    """« variante » → regénère le DERNIER prompt (nouveau seed aléatoire)."""
    prompt = LAST_PROMPT.get(chat_id)
    if not prompt:
        bot.send_message(chat_id, _m(lang, "ask"))
        return
    bot.send_message(chat_id, _m(lang, "working"))
    threading.Thread(
        target=_generate_and_send, args=(bot, chat_id, prompt, lang), daemon=True,
    ).start()


def handle_pro_followup(bot, chat_id: int, text: str, lang: str) -> bool:
    """Routing des réponses au pitch/done d'une image générée.
    True = message consommé. Ne s'active QUE dans un contexte image
    (une caption a été envoyée récemment à CE client)."""
    if chat_id not in LAST_CAPTION:
        return False
    low = (text or "").strip().lower().strip(" .?!…'’")
    if not low:
        return False
    caption = LAST_CAPTION.get(chat_id, "")
    # FIX INCOHÉRENCES (Boss 04/10, screenshot) : le contexte image n'est
    # ACTIF que si la dernière réponse du bot à CE client était bien la
    # caption de l'image. Si le bot a servi autre chose entre-temps
    # (fiche KB, démo texte, portfolio...), le « Oui » du client répond
    # à CETTE dernière réponse — on purge le contexte périmé et on laisse
    # le message suivre son chemin normal. Terminé les relances d'une
    # vieille image jusqu'au devis Premium sans rapport.
    if HISTORY_READER is not None:
        try:
            last_assistant = (HISTORY_READER(chat_id) or "").strip()
            cap_ref = caption.strip()
            if last_assistant and cap_ref and last_assistant != cap_ref:
                LAST_CAPTION.pop(chat_id, None)
                return False
        except Exception:
            logger.exception("Vérification fraîcheur contexte image impossible")
    is_logo_pitch = "Pack Premium" in caption          # pitch logo → devis direct
    is_done = "version pro" in caption                 # done → choix variante/pro
    tokens = low.split()
    is_confirm = low in ("oui", "ouais", "yes", "yeah", "si", "sí", "نعم", "أكيد", "تمام", "ok", "okay", "d'accord", "daccord", "vas-y", "vasy", "go")
    is_short_confirm = (len(tokens) <= 3 and tokens and tokens[0] in (
        "oui", "ouais", "yes", "yeah", "si", "sí", "نعم", "va", "vas-y", "vasy", "ok", "go"))
    is_neg = low in _NEG_WORDS or (tokens and tokens[0] in ("non", "no", "nan") and len(tokens) <= 3)

    last_prompt = LAST_PROMPT.get(chat_id, "")

    # « lance la version pro » / « version pro » → devis Premium, direct
    if any(t in low for t in _PRO_TRIGGERS):
        _start_premium_devis(bot, chat_id, lang, last_prompt)
        return True

    # « variante » / « une autre » → regénération gratuite du même prompt
    if any(t in low for t in _VARIANTE_TRIGGERS):
        _regenerate_variant(bot, chat_id, lang)
        return True

    # « Oui » après le PITCH LOGO (« Tu veux que je lance la version pro ? »)
    if (is_confirm or is_short_confirm) and is_logo_pitch:
        _start_premium_devis(bot, chat_id, lang, last_prompt)
        return True

    # « Oui » après le DONE (« variante ou version pro ? ») → on demande lequel
    if (is_confirm or is_short_confirm) and is_done:
        _ask_variant_or_pro(bot, chat_id, lang)
        return True

    # « non » → clôture polie, le rendu de base reste gratuit
    if is_neg:
        bot.send_message(chat_id, (
            "Pas de souci 😊 Le rendu de base reste gratuit — tape /image "
            "quand tu veux une autre idée, ou « catalogue » pour nos offres 🇬🇳"))
        LAST_CAPTION.pop(chat_id, None)
        LAST_PROMPT.pop(chat_id, None)
        return True

    return False


def handle_callback(bot, call, lang: str) -> bool:
    """Boutons du choix variante/pro. True = clic consommé."""
    data = getattr(call, "data", "") or ""
    chat_id = call.message.chat.id if call.message else None
    if chat_id is None or data not in ("kmr_img_variante", "kmr_img_pro"):
        return False
    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass
    if data == "kmr_img_variante":
        _regenerate_variant(bot, chat_id, lang)
    else:
        _start_premium_devis(bot, chat_id, lang, LAST_PROMPT.get(chat_id, ""))
    return True


# ---------------------------------------------------------------------------
# IMG2IMG (Boss 04/10) : photo du client + texte → image générée.
# La photo de référence est hébergée publiquement (uguu.se, ~3h de
# rétention — largement le temps de générer) car Pollinations ne peut
# lire qu'une URL publique, jamais le fichier local.
# VERROUS : visage/identité préservés, réalisme strict (pas de plastique,
# flou, déformation ni cartoon sauf demande explicite), prompt du client
# respecté (pas de réécriture externe, enhance retiré — même fix que le
# texte→image 03/10).
# ---------------------------------------------------------------------------
POLLINATIONS_I2I = ("https://image.pollinations.ai/prompt/{p}"
                    "?width=1024&height=1024&model=flux"
                    "&nologo=true&seed={s}&image={img}")
# Le gating 402 est plus lourd sur l'img2img (modèle d'édition partagé) :
# plus de tentatives que le texte→image.
MAX_I2I_RETRIES = 5
REF_HOST_UPLOAD = "https://uguu.se/upload"

# Préserve le visage/identité de la photo de référence (règle Boss :
# jamais de visage modifié lors d'une retouche).
IDENTITY_LOCK = (
    ", keep the exact same face as the reference photo, preserve facial "
    "features, same skin tone, same identity, do not alter the face"
)


def _upload_reference(data: bytes) -> str | None:
    """Héberge la photo de référence et renvoie son URL publique directe."""
    try:
        resp = requests.post(
            REF_HOST_UPLOAD,
            files={"files[]": ("ref.jpg", data, "image/jpeg")},
            timeout=30,
        )
        payload = resp.json()
        files = payload.get("files") or []
        if payload.get("success") and files:
            url = str(files[0].get("url", "")).replace("\\", "")
            if url.startswith("http"):
                return url
        logger.error("Hébergement ref refusé : %s", str(payload)[:200])
    except Exception:
        logger.exception("Hébergement photo de référence impossible")
    return None


def _i2i_prompt(caption: str) -> str:
    """Prompt img2img : caption client respectée + verrous identité/rendu.
    Cartoon autorisé UNIQUEMENT si demandé explicitement (même règle que
    le texte→image)."""
    cap = (caption or "").strip()
    lowered = cap.lower()
    if any(k in lowered for k in CARTOON_KEYWORDS):
        return f"{cap}{IDENTITY_LOCK}, {BRAND_TAG}{STYLE_TAIL}"
    return f"{cap}{IDENTITY_LOCK}{REALISM_LOCK}, {BRAND_TAG}{STYLE_TAIL}"


def _fetch_image_i2i(caption: str, ref_url: str) -> bytes | None:
    """Génère depuis la photo de référence + caption (retry sur 402)."""
    full_prompt = _i2i_prompt(caption)
    for attempt in range(MAX_I2I_RETRIES):
        try:
            url = POLLINATIONS_I2I.format(
                p=quote(full_prompt), s=random.randint(1, 10**6), img=ref_url)
            resp = requests.get(url, timeout=TIMEOUT + 30)
            if resp.status_code == 200 and resp.content[:2] == b"\xff\xd8":
                return resp.content
        except Exception:
            logger.exception("Échec img2img (tentative %s/%s)", attempt + 1, MAX_I2I_RETRIES)
        if attempt < MAX_I2I_RETRIES - 1:
            time.sleep(2.5)
    return None


def _edit_and_send(bot, chat_id: int, caption: str, photo_bytes: bytes, lang: str) -> None:
    """Thread worker img2img : héberge la photo, génère, tamponne, envoie."""
    stop = threading.Event()
    threading.Thread(target=_typing_keeper, args=(bot, chat_id, stop), daemon=True).start()
    try:
        ref_url = _upload_reference(photo_bytes)
        if not ref_url:
            bot.send_message(chat_id, _m(lang, "error"))
            return
        content = _fetch_image_i2i(caption, ref_url)
        if content:
            IMAGES_DIR.mkdir(parents=True, exist_ok=True)
            name = f"edit_{chat_id}_{random.randint(10**9, 10**10)}.jpg"
            path = IMAGES_DIR / name
            path.write_bytes(content)
            _stamp_brand(path)
            cap = _done_caption(caption, lang)
            LAST_PROMPT[chat_id] = caption   # « variante » → regénère
            with open(path, "rb") as f:
                bot.send_photo(chat_id, f, caption=cap)
            _remember_caption(chat_id, cap)
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


# Intentions « copie exacte du visage » (fix Boss 04/10, screenshot) : le
# moteur gratuit fait au mieux mais ne garantit PAS un visage 100%
# identique — on le dit AVANT de générer, jamais après coup.
COPY_INTENT = (
    "copie exacte", "copie cette", "copie ce ", "copie la photo",
    "copie moi", "copie-moi", "copie l'image", "exactement la même",
    "exactement la meme", "la même photo", "la meme photo", "same exact",
    "exactly the same", "identique", "same face",
)

COPY_HONESTY_NOTE = (
    "⚠️ Petite précision honnête : le rendu gratuit garde la scène et le "
    "style de ta photo, mais ne garantit pas un visage 100% identique. "
    "Pour une fidélité parfaite du visage, la Version Pro est là 💎\n\n"
)


def handle_photo_request(bot, chat_id: int, caption: str, message, lang: str) -> bool:
    """Photo + texte → retouche IA (img2img). True = message consommé.
    Sans caption → False (la photo suit son chemin habituel : portfolio
    admin ou scan de reçu client)."""
    cap = (caption or "").strip()
    if not cap:
        return False
    if not IMG_ENABLED:
        return False
    try:
        _f = bot.get_file(message.photo[-1].file_id)
        photo_bytes = bot.download_file(_f.file_path)
    except Exception:
        logger.exception("Téléchargement photo client impossible")
        bot.send_message(chat_id, _m(lang, "error"))
        return True
    low_cap = cap.lower()
    prefix = COPY_HONESTY_NOTE if any(k in low_cap for k in COPY_INTENT) else ""
    bot.send_message(chat_id, prefix + _m(lang, "working"))
    threading.Thread(
        target=_edit_and_send, args=(bot, chat_id, cap, photo_bytes, lang),
        daemon=True,
    ).start()
    return True
