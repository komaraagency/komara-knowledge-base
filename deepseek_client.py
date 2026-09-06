"""Client DeepSeek — couche de compréhension externe pour Komara Agency.

Rôle dans l'architecture :
1. Le moteur local (local_search.py + kb.json) répond en priorité — gratuit,
   instantané, 100% maîtrisé.
2. Si le message n'est PAS reconnu localement, on interroge DeepSeek
   (deepseek-chat) pour COMPRENDRE le message et produire une réponse
   naturelle dans le style Komara Agency (Pack 6).
3. Si DeepSeek échoue (clé absente, réseau, timeout), on retombe
   silencieusement sur le fallback local existant.

Le module n'a aucune dépendance externe (urllib uniquement) et ne lève
jamais d'exception vers l'appelant : il renvoie str | None.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("komara.deepseek")

API_URL = "https://api.deepseek.com/chat/completions"

# ---------------------------------------------------------------------------
# Configuration (lue à chaque appel pour capter .env / Railway / changements)
# ---------------------------------------------------------------------------


def _config() -> dict[str, Any]:
    try:  # pragma: no cover - environnement Railway/local avec .env
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except Exception:
        pass
    return {
        "api_key": (os.getenv("DEEPSEEK_API_KEY") or "").strip(),
        "enabled": (os.getenv("DEEPSEEK_ENABLED") or "true").strip().lower()
        in {"1", "true", "yes", "on"},
        "model": (os.getenv("DEEPSEEK_MODEL") or "deepseek-chat").strip(),
        "timeout": max(5, int(os.getenv("DEEPSEEK_TIMEOUT") or "12")),
        "max_tokens": max(80, int(os.getenv("DEEPSEEK_MAX_TOKENS") or "300")),
    }


def deepseek_available() -> bool:
    """True si DeepSeek est utilisable (activé + clé présente)."""
    cfg = _config()
    return bool(cfg["enabled"] and cfg["api_key"])


# ---------------------------------------------------------------------------
# Persona Komara Agency (Pack 6) injecté comme system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """Tu es Ndine, le commercial de KOMARA AGENCY 🇬🇳 (branding digital, IA et automatisation pour business africains).

TON STYLE (obligatoire) :
- Tutoiement amical, chaleureux, humain — nuances africaines ( clients peuvent écrire en argot ou avec des fautes, reste tolérant et naturel ).
- Réponse COURTE : 2 à 4 phrases maximum. 1 ou 2 emojis max, jamais plus.
- Termine TOUJOURS par une question légère qui qualifie le besoin ou ramène vers la vente.
- Réponds dans la LANGUE du client (français, anglais, arabe ou espagnol). S'il mélange, suis sa langue dominante.
- INTERDIT : dire que tu es une IA / un bot / un robot / un modèle de langage. Tu es l'équipe Komara Agency, un humain qui répond vite.
- INTERDIT : jargon technique (API, LLM, prompt, fine-tuning...), réponses rigides type script, pavés de texte, listes à puces.

NOS SERVICES : bots WhatsApp / Telegram / Facebook, sites web, applications, logos et création digitale, Agent IA commercial 24H/24.

NOS PRIX (réponds naturellement si on te les demande, sans inventer d'autres chiffres) :
- Bots : packs à partir de 99$ ; le plus vendu = Bot prise de commande + FAQ à 150$.
- Marché Maroc : packs à partir de 1200 MAD, Pack Business 3500 MAD, sur-mesure sur devis.
- Marché Guinée : site vitrine 1.500.000 FG, e-commerce 3.500.000 FG ; visuels à partir de 50.000 FG.
- Agent IA : à partir de 300€ installation + 90€/mois ; formule FCFA à partir de 50.000 FCFA/mois.
- Devis toujours gratuit. Délai type : 7 jours (express 72H). Installation bot : 48H.

RÈGLES :
- Si la question sort de nos services, réponds brièvement puis ramène vers ce qu'on peut faire pour son business.
- Si le client est agressif ou hors-sujet, reste calme, bref, professionnel.
- Ne promets JAMAIS une fonctionnalité ou un prix qui n'est pas dans cette fiche."""


# ---------------------------------------------------------------------------
# Nettoyage de la réponse (cohérence Telegram / éviter artefacts LLM)
# ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    # **gras** markdown → *gras* (style Telegram utilisé dans la KB)
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    # retire les préambules d'assistant ("Bien sûr !", "Voici...")
    text = re.sub(
        r"^(?:bien sûr|voici|d'accord|okay|ok)\s*[!,:]?\s*", "", text.strip(),
        flags=re.IGNORECASE,
    )
    # coupe à ~600 caractères sur une frontière de phrase
    if len(text) > 600:
        cut = text[:600]
        for sep in (". ", "!\n", "? ", "\n"):
            idx = cut.rfind(sep)
            if idx > 150:
                cut = cut[: idx + 1]
                break
        text = cut.rstrip()
    text = text.strip()
    if text and text[-1] not in ".!?… 🙂🔥😄😊👍🇬🇳❓":
        text += " 🙂"
    return text or None


# ---------------------------------------------------------------------------
# Appel API
# ---------------------------------------------------------------------------


def _history_messages(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """Garde les 6 derniers tours de conversation pour la cohérence."""
    messages: list[dict[str, str]] = []
    if history:
        for item in history[-6:]:
            role = item.get("role")
            content = (item.get("content") or "").strip()
            if role in {"user", "assistant"} and content:
                messages.append({"role": role, "content": content[:400]})
    return messages


def ask_deepseek(
    user_text: str,
    lang: str = "fr",
    history: list[dict[str, str]] | None = None,
) -> str | None:
    """Interroge DeepSeek. Renvoie une réponse courte, ou None si indisponible."""
    cfg = _config()
    if not (cfg["enabled"] and cfg["api_key"]):
        return None

    text = (user_text or "").strip()
    if not text or len(text) > 1500:
        return None

    lang_hint = {
        "fr": "Réponds en français.",
        "en": "Answer in English.",
        "ar": "أجب بالعربية.",
        "es": "Responde en español.",
        None: "Réponds dans la langue utilisée par le client.",
        "": "Réponds dans la langue utilisée par le client.",
    }.get(lang, "Réponds dans la langue utilisée par le client.")

    messages = [{"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{lang_hint}"}]
    messages.extend(_history_messages(history))
    messages.append({"role": "user", "content": text})

    payload = json.dumps(
        {
            "model": cfg["model"],
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": cfg["max_tokens"],
            "stream": False,
        }
    ).encode("utf-8")

    for attempt in (1, 2):
        try:
            req = urllib.request.Request(
                API_URL,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {cfg['api_key']}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = (
                data.get("choices", [{}])[0].get("message", {}).get("content") or ""
            ).strip()
            if content:
                return _clean(content)
            logger.warning("DeepSeek a renvoyé une réponse vide (tentative %s)", attempt)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            # 401/403 (clé invalide) ou 402 (crédits épuisés) : inutile de retry
            if exc.code in (401, 402, 403):
                logger.error("DeepSeek refusé (HTTP %s) : %s", exc.code, body)
                return None
            logger.warning("DeepSeek HTTP %s (tentative %s) : %s", exc.code, attempt, body)
        except Exception:
            logger.warning("DeepSeek indisponible (tentative %s)", attempt, exc_info=True)
    return None
