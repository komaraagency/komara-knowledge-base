# ---------------------------------------------------------------------------
# tts.py — Synthèse vocale des réponses (gTTS : gratuit, sans clé API)
# Le client écrit en vocal → le bot répond texte + vocal.
# Si ffmpeg est dispo : note vocale ogg/opus (bulle vocale Telegram),
# sinon fichier audio mp3 lisible.
# ---------------------------------------------------------------------------

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

TTS_ENABLED = os.getenv("TTS_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
TTS_MAX_CHARS = 400          # au-delà, on reste en texte (un vocal long fatigue)
GTTS_LANGS = {"fr": "fr", "en": "en", "es": "es", "ar": "ar"}

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2B00-\u2BFF\uFE0F]+"
)


def clean_for_speech(text: str) -> str:
    """Texte parlable : sans markdown, sans emojis, sans doubles espaces."""
    text = text or ""
    for ch in "*_`#>~|":
        text = text.replace(ch, " ")
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)   # liens markdown → libellé
    text = _EMOJI_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def reply_with_voice(bot, chat_id: int, text: str, lang: str = "fr") -> bool:
    """Envoie la réponse en vocal. Ne casse JAMAIS le chat (silence sur erreur)."""
    if not TTS_ENABLED:
        return False
    spoken = clean_for_speech(text)
    if not spoken or len(spoken) > TTS_MAX_CHARS:
        return False
    try:
        from gtts import gTTS
    except ImportError:
        return False
    tts_lang = GTTS_LANGS.get(lang, "fr")
    mp3_path = ogg_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            gTTS(text=spoken, lang=tts_lang, slow=False).write_to_fp(tmp)
            mp3_path = tmp.name
        # ffmpeg dispo → vraie note vocale ogg/opus
        if shutil.which("ffmpeg"):
            ogg_path = mp3_path.replace(".mp3", ".ogg")
            conv = subprocess.run(
                ["ffmpeg", "-y", "-i", mp3_path, "-c:a", "libopus",
                 "-b:a", "48k", ogg_path],
                capture_output=True, timeout=30,
            )
            if conv.returncode == 0 and Path(ogg_path).is_file():
                with open(ogg_path, "rb") as f:
                    bot.send_voice(chat_id, f)
                return True
        # pas de ffmpeg (ou échec) → fichier audio mp3 lisible
        with open(mp3_path, "rb") as f:
            bot.send_audio(chat_id, f)
        return True
    except Exception as e:  # pragma: no cover — la voix n'est jamais critique
        import logging
        logging.getLogger("komara.tts").warning("TTS échoué : %s", e)
        return False
    finally:
        for p in (mp3_path, ogg_path):
            if p:
                Path(p).unlink(missing_ok=True)
