# ---------------------------------------------------------------------------
# weekly_report.py — Rapport hebdomadaire automatique envoyé à l'admin
# Chaque lundi 09h00 (heure de Guinée, GMT), calculé 100% depuis SQLite.
# /hebdo (admin) : aperçu immédiat.
# ---------------------------------------------------------------------------

import threading
import time
from datetime import datetime, timedelta, timezone


def compute_weekly(days: int = 7) -> dict:
    """Statistiques des N derniers jours depuis la base locale."""
    import actions
    from actions import DB_CONN, DB_LOCK
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    out = {"orders": 0, "leads": 0, "appointments": 0, "quotes": 0,
           "clients": 0, "top_services": [], "days": days}
    with DB_LOCK:
        for table, key in (("orders", "orders"), ("leads", "leads"),
                          ("appointments", "appointments"), ("quotes", "quotes")):
            row = DB_CONN.execute(
                f"SELECT COUNT(*) FROM {table} WHERE created_at >=?", (since,)
            ).fetchone()
            out[key] = int(row[0]) if row else 0
        row = DB_CONN.execute(
            "SELECT COUNT(DISTINCT chat_id) FROM orders WHERE created_at >=?", (since,)
        ).fetchone()
        out["clients"] = int(row[0]) if row else 0
        rows = DB_CONN.execute(
            "SELECT service, COUNT(*) n FROM quotes WHERE created_at >=? "
            "GROUP BY service ORDER BY n DESC LIMIT 3", (since,)
        ).fetchall()
        out["top_services"] = [(r[0], int(r[1])) for r in rows]
    return out


def build_message(stats: dict) -> str:
    lines = [
        "📊 RAPPORT HEBDO — Komara Agency 🇬🇳",
        f"({stats['days']} derniers jours)",
        "",
        f"🚀 Commandes : {stats['orders']}",
        f"📄 Devis : {stats['quotes']}",
        f"📞 Leads : {stats['leads']}",
        f"📅 RDV : {stats['appointments']}",
        f"👥 Clients distincts : {stats['clients']}",
    ]
    if stats["top_services"]:
        lines.append("")
        lines.append("🏆 Top services :")
        for svc, n in stats["top_services"]:
            lines.append(f"   • {svc or '—'} : {n}")
    total = stats["orders"] + stats["quotes"] + stats["leads"]
    if total == 0:
        lines.append("")
        lines.append("Semaine calme — pense à publier du contenu pour faire venir des prospects 😉")
    return "\n".join(lines)


def send_weekly(bot) -> None:
    import actions
    from actions import ADMIN_CHAT_ID
    if not ADMIN_CHAT_ID:
        return
    try:
        stats = compute_weekly(7)
        bot.send_message(ADMIN_CHAT_ID, build_message(stats))
    except Exception:
        pass


def cmd_hebdo(bot, chat_id: int, lang: str = "fr") -> None:
    stats = compute_weekly(7)
    bot.send_message(chat_id, build_message(stats))


def _next_monday_9am() -> float:
    now = datetime.now(timezone.utc)
    target = now.replace(hour=9, minute=0, second=0, microsecond=0)
    days_ahead = (7 - now.weekday()) % 7  # lundi = 0
    if days_ahead == 0 and now >= target:
        days_ahead = 7
    target += timedelta(days=days_ahead)
    return (target - now).total_seconds() + 1


def scheduler_loop(bot) -> None:
    """Thread démon : rapport chaque lundi 09h00 UTC."""
    while True:
        time.sleep(_next_monday_9am())
        send_weekly(bot)
