# -*- coding: utf-8 -*-
"""
relances.py — Feature #2 : Relance Auto J+1 / J+3 / J+7.

Exécuté par cron_reminder.py toutes les 6 h. Logique spec :

  J+1 : pending, created_at < NOW-24h, last_reminder_at IS NULL
        → Message J+1, last_reminder_at = now, reminder_stage = 1
  J+3 : pending, stage >= 1, last_reminder_at < NOW-72h
        → Message J+3, stage = 2
  J+7 : pending, stage >= 2, last_reminder_at < NOW-7j
        → status = expired + Message J+7

Si le client a payé/répondu (status = paid), aucune relance ne part.

⚠️ Les 3 messages ci-dessous sont EXACTS — « ne pas modifier sans
validation » (lettre #2). Leur ton est déjà Aya.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import commercial_db as cdb

logger = logging.getLogger("komara")

HOURS = {
    1: timedelta(hours=24),      # J+1 : 24h après création
    2: timedelta(hours=72),      # J+3 : 72h après le 1er rappel
    3: timedelta(days=7),       # J+7 : 7 jours après le 2e rappel
}

# Messages de la lettre #2, mis au format lettre finale : PRIX FIXE €
# en premier, monnaie locale en info indicative entre parenthèses.
MSG_J1 = ("Salam {name} ! Ton devis pour {project} à *{price_eur}€* "
          "{local_part}est toujours valable jusqu'à ce soir. On te lance ?")
MSG_J3 = ("Dernière chance {name}. Ton devis expire demain et repasse à prix "
          "normal. Si tu confirmes aujourd'hui, domaine offert.")
MSG_J7 = "On archive ton dossier {name}. Tape 'devis' quand tu es prêt à reprendre."


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def _fmt(v: float) -> str:
    return f"{int(round(v)):,}".replace(",", " ") if v >= 1000 else f"{v:g}"


def process_due_reminders(bot, now: datetime | None = None) -> dict:
    """Balaie les pending_quotes et envoie les relances dues. Retour stats."""
    now = now or datetime.now(timezone.utc)
    sent = {"j1": 0, "j3": 0, "j7": 0, "expired": 0}
    for q in cdb.pending_quotes(status="pending"):
        try:
            created = _parse(q["created_at"])
            last = _parse(q["last_reminder_at"] or "")
            stage = q["reminder_stage"] or 0
            cid = q["chat_id"]
            name = q["client_name"] or "cher client"
            project = q["project_desc"] or q["service"] or "ton projet"
            price_eur = q["price_eur"] or 0
            # info locale recalculée à la base EUR (jamais prix de vente)
            import devis_engine
            conv = devis_engine.convert_devis(q["country_code"] or "GN",
                                               price_eur or 0)
            local_part = ""
            if conv["currency"] != "EUR" and price_eur:
                local_part = f"(~{devis_engine.format_price(conv)} chez toi) "

            if stage == 0 and created and now - created >= HOURS[1]:
                _send(bot, cid, MSG_J1.format(
                    name=name, project=project,
                    price_eur=f"{price_eur:g}",
                    local_part=local_part), parse_mode="Markdown")
                cdb.update_quote(q["id"], last_reminder_at=now.isoformat(timespec="seconds"),
                                 reminder_stage=1)
                sent["j1"] += 1
            elif stage == 1 and last and now - last >= HOURS[2]:
                _send(bot, cid, MSG_J3.format(name=name))
                cdb.update_quote(q["id"], last_reminder_at=now.isoformat(timespec="seconds"),
                                 reminder_stage=2)
                sent["j3"] += 1
            elif stage == 2 and last and now - last >= HOURS[3]:
                _send(bot, cid, MSG_J7.format(name=name))
                cdb.update_quote(q["id"], status="expired",
                                 last_reminder_at=now.isoformat(timespec="seconds"))
                sent["j7"] += 1
                sent["expired"] += 1
        except Exception as e:  # jamais un client ne bloque les autres
            logger.error("Relance impossible (quote %s) : %s", q.get("id"), e)
    if any(sent.values()):
        logger.info("Relances envoyées : %s", sent)
    return sent


def _send(bot, chat_id: str, text: str, parse_mode: str | None = None) -> None:
    if bot is None:
        logger.info("[SIMU] relance → %s : %s", chat_id, text[:60])
        return
    try:
        if parse_mode:
            bot.send_message(int(chat_id), text, parse_mode=parse_mode)
        else:
            bot.send_message(int(chat_id), text)
    except Exception as e:
        logger.error("Envoi relance impossible (%s) : %s", chat_id, e)
