# ---------------------------------------------------------------------------
# tts.py — Synthèse vocale des réponses.
# Moteurs : edge-tts (voix neuronales gratuites, sans clé) par défaut,
# repli automatique gTTS, puis silence (la voix n'est jamais critique).
# Le client écrit en vocal → le bot répond texte + vocal.
# ---------------------------------------------------------------------------

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

TTS_ENABLED = os.getenv("TTS_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
TTS_ENGINE = os.getenv("TTS_ENGINE", "edge_tts").strip().lower()  # edge_tts | gtts
TTS_LANGUAGE = os.getenv("TTS_LANGUAGE", "fr-FR")
TTS_VOICE = os.getenv("TTS_VOICE", "fr-FR-DeniseNeural")
TTS_MAX_CHARS = 400          # au-delà, on reste en texte (un vocal long fatigue)

# Voix neuronales edge-tts par langue (fr par défaut = TTS_VOICE)
EDGE_VOICES = {
    "fr": TTS_VOICE or "fr-FR-DeniseNeural",
    "en": "en-US-JennyNeural",
    "es": "es-ES-ElviraNeural",
    "ar": "ar-EG-SalmaNeural",
}
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


def _edge_voice_for(lang: str) -> str:
    return EDGE_VOICES.get(lang, EDGE_VOICES["fr"])


def _edge_generate(text: str, lang: str, mp3_path: str) -> bool:
    """Génère un mp3 via edge-tts (voix neurale). True si succès."""
    try:
        import edge_tts
    except ImportError:
        return False

    async def _save(path: str) -> None:
        com = edge_tts.Communicate(text, _edge_voice_for(lang))
        with open(path, "wb") as out:
            async for chunk in com.stream():
                if chunk.get("type") == "audio":
                    out.write(chunk.get("data", b""))

    try:
        try:
            asyncio.run(_save(mp3_path))
        except RuntimeError:          # boucle déjà active (rare) → nouvelle boucle
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(_save(mp3_path))
            finally:
                loop.close()
        return Path(mp3_path).is_file() and Path(mp3_path).stat().st_size > 500
    except Exception:
        return False


def _gtts_generate(text: str, lang: str, mp3_path: str) -> bool:
    """Génère un mp3 via gTTS (repli). True si succès."""
    try:
        from gtts import gTTS
    except ImportError:
        return False
    try:
        gTTS(text=text, lang=GTTS_LANGS.get(lang, "fr"), slow=False).save(mp3_path)
        return Path(mp3_path).is_file()
    except Exception:
        return False


def reply_with_voice(bot, chat_id: int, text: str, lang: str = "fr") -> bool:
    """Envoie la réponse en vocal. Ne casse JAMAIS le chat (silence sur erreur)."""
    if not TTS_ENABLED:
        return False
    spoken = clean_for_speech(text)
    if not spoken or len(spoken) > TTS_MAX_CHARS:
        return False
    mp3_path = ogg_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            mp3_path = tmp.name
        # moteur principal puis repli
        ok = False
        if TTS_ENGINE in {"edge_tts", "edge-tts", "edgetts"}:
            ok = _edge_generate(spoken, lang, mp3_path)
        if not ok:
            ok = _gtts_generate(spoken, lang, mp3_path)
        if not ok:
            return False
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
    except Exception:  # pragma: no cover — la voix n'est jamais critique
        return False
    finally:
        for p in (mp3_path, ogg_path):
            if p:
                Path(p).unlink(missing_ok=True)
