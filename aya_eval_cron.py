# ---------------------------------------------------------------------------
# aya_eval_cron.py — Évaluation hebdomadaire automatique de la mémoire Aya
# Chaque lundi 08h00 UTC (avant le briefing 8h), pipeline 6 étapes complet,
# rapport envoyé à l'admin. Même pattern que weekly_report.py (scheduler
# thread démon). /evaluation (admin) reste disponible pour un aperçu immédiat.
# ---------------------------------------------------------------------------

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger("komara.aya_eval_cron")


def build_message(report: dict) -> str:
    ev, tr = report["posttrain"], report["pretrain"]
    lines = [
        "📊 ÉVALUATION HEBDO — MÉMOIRE AYA",
        f"🧠 Base : {tr['docs']} entrées, {tr['vocab_size']} tokens indexés",
        f"🎯 Précision : {ev['accuracy']:.0%} ({ev['hits']}/{ev['total']})",
    ]
    if ev["below_threshold"]:
        lines.append(f"⚠️ Sous le seuil ({ev['threshold']:.0%}) — à enseigner :")
    elif ev["misses"]:
        lines.append(f"⚠️ {len(ev['misses'])} formulation(s) à améliorer :")
    else:
        lines.append("✅ RAS — toutes les formulations clients sont servies.")
    for m in ev["misses"][:5]:
        lines.append(f"• « {m['question'][:70]} »")
    if ev["misses"]:
        lines.append("→ Corrige avec : /apprends <question> || <réponse>")
    return "\n".join(lines)


def send_weekly_evaluation(bot) -> None:
    import aya_pipeline
    from actions import ADMIN_CHAT_ID
    if not ADMIN_CHAT_ID:
        return
    try:
        report = aya_pipeline.run_evaluation(lang="fr")
        bot.send_message(ADMIN_CHAT_ID, build_message(report))
        # Miroir best-effort des écarts vers Questions sans réponse
        misses = report["posttrain"]["misses"]
        if misses:
            try:
                from memory_sheets import log_unanswered
                for m in misses:
                    log_unanswered(m["question"], "fr", "evaluation_hebdo", 1)
            except Exception:
                pass
    except Exception:
        logger.exception("Évaluation hebdo Aya a échoué (anti-crash)")


def _next_monday_8am() -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=8, minute=0, second=0, microsecond=0)
    days_ahead = (7 - now.weekday()) % 7  # lundi = 0
    if days_ahead == 0 and now >= target:
        days_ahead = 7
    target += timedelta(days=days_ahead)
    return (target - now).total_seconds() + 1


def scheduler_loop(bot) -> None:
    """Thread démon : évaluation mémoire Aya chaque lundi 08h00 UTC."""
    while True:
        time.sleep(_next_monday_8am())
        send_weekly_evaluation(bot)
