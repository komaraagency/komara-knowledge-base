# ---------------------------------------------------------------------------
# invoices.py — Factures proforma PDF 100% locales (reportlab)
# /facture <id> [montant] [client] (admin) : génère et envoie la facture.
# Montant : argument admin, sinon dernier devis du client, sinon "sur devis".
# ---------------------------------------------------------------------------

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

BRAND = "KOMARA AGENCY"
TAGLINE = "Créateur digital — Guinée"
FOOTER = "komara-agency.onrender.com  •  WhatsApp : +212 701 986 219"
PRIMARY = colors.Color(16/255, 122/255, 87/255)  # vert Komara


def _fmt_amount(amount: str) -> str:
    amount = (amount or "").strip()
    if not amount:
        return "Sur devis"
    return amount


def generate_invoice(order: dict, amount: str = "") -> bytes:
    """Génère la facture proforma PDF (bytes), prête à envoyer."""
    now = datetime.now(timezone.utc)
    ref = f"KMA-{now.year}-{int(order.get('id') or 0):04d}"
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, pageCompression=0)
    w, h = A4

    # ── En-tête
    c.setFillColor(PRIMARY)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(20*mm, h - 25*mm, BRAND)
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 10)
    c.drawString(20*mm, h - 31*mm, TAGLINE)

    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(w - 20*mm, h - 25*mm, "FACTURE PROFORMA")
    c.setFont("Helvetica", 9)
    c.drawRightString(w - 20*mm, h - 31*mm, f"Réf. {ref}")
    c.drawRightString(w - 20*mm, h - 36*mm, now.strftime("%d/%m/%Y"))

    c.setStrokeColor(PRIMARY)
    c.setLineWidth(2)
    c.line(20*mm, h - 40*mm, w - 20*mm, h - 40*mm)

    # ── Client
    y = h - 52*mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20*mm, y, "FACTURÉ À")
    c.setFont("Helvetica", 11)
    y -= 6*mm
    c.drawString(20*mm, y, (order.get("name") or "Client")[:60])
    if order.get("phone"):
        y -= 5.5*mm
        c.drawString(20*mm, y, str(order["phone"])[:30])
    if order.get("chat_id"):
        y -= 5.5*mm
        c.drawString(20*mm, y, f"Client #{str(order['chat_id'])[:15]}")

    # ── Détails
    y -= 16*mm
    c.setFillColor(colors.HexColor("#F2F4F7"))
    c.rect(20*mm, y - 6*mm, w - 40*mm, 8*mm, fill=1, stroke=0)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(23*mm, y - 3.5*mm, "PRESTATION")
    c.drawRightString(w - 23*mm, y - 3.5*mm, "DÉTAIL")

    rows = [
        ("Service", order.get("service") or "Prestation digitale"),
        ("Activité", order.get("activity") or "—"),
        ("Échéance", order.get("deadline") or "—"),
        ("Statut", order.get("status") or "en attente"),
    ]
    y -= 14*mm
    c.setFont("Helvetica", 10)
    for label, value in rows:
        c.setFont("Helvetica", 10)
        c.drawString(23*mm, y, label)
        c.drawRightString(w - 23*mm, y, str(value)[:55])
        y -= 7*mm

    # ── Montant
    y -= 10*mm
    c.setStrokeColor(colors.HexColor("#DDDDDD"))
    c.setLineWidth(0.7)
    c.line(20*mm, y + 10*mm, w - 20*mm, y + 10*mm)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(20*mm, y, "MONTANT TOTAL")
    c.setFillColor(PRIMARY)
    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(w - 20*mm, y, _fmt_amount(amount))
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Oblique", 8)
    c.drawRightString(w - 20*mm, y - 6*mm, "TVA non applicable — régime simplifié")

    # ── Paiement + pied
    y -= 22*mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20*mm, y, "MODE DE PAIEMENT")
    c.setFont("Helvetica", 9)
    c.drawString(20*mm, y - 5.5*mm, "Wave / Orange Money — coordonnées communiquées à la validation")
    c.drawString(20*mm, y - 11*mm, "Devis personnalisé confirmé avant tout paiement.")

    c.setStrokeColor(PRIMARY)
    c.setLineWidth(1)
    c.line(20*mm, 22*mm, w - 20*mm, 22*mm)
    c.setFont("Helvetica", 8)
    c.drawString(20*mm, 17*mm, FOOTER)
    c.drawRightString(w - 20*mm, 17*mm, "Merci de votre confiance 🇬🇳".replace("🇬🇳", ""))

    c.showPage()
    c.save()
    return buf.getvalue()


def cmd_facture(bot, chat_id: int, args: str, lang: str = "fr") -> None:
    """/facture (admin) : /facture <id> [montant] [client]"""
    from actions import DB_CONN, DB_LOCK, notify_admin  # noqa: PLC0415
    parts = args.split()
    if not parts or not parts[0].isdigit():
        bot.send_message(
            chat_id,
            "🧾 /facture <id_commande> [montant] [client]\n\n"
            "• id : visible via /commandes\n"
            "• montant : libre (ex. 250 000 GNF, 300€) — sinon dernier devis du client\n"
            "• 'client' : envoie aussi la facture au client",
        )
        return
    order_id = int(parts[0])
    with DB_LOCK:
        row = DB_CONN.execute(
            "SELECT id, chat_id, service, activity, deadline, name, phone, status "
            "FROM orders WHERE id =?", (order_id,),
        ).fetchone()
    if not row:
        bot.send_message(chat_id, f"❌ Commande #{order_id} introuvable (/commandes pour la liste).")
        return
    cols = ("id", "chat_id", "service", "activity", "deadline", "name", "phone", "status")
    order = dict(zip(cols, row))
    amount = " ".join(parts[1:])
    send_client = False
    if "client" in amount.lower():
        send_client = True
        amount = amount.lower().replace("client", "").strip()
    if not amount:
        # Dernier devis du client
        with DB_LOCK:
            q = DB_CONN.execute(
                "SELECT price FROM quotes WHERE chat_id =? ORDER BY id DESC LIMIT 1",
                (str(order["chat_id"]),),
            ).fetchone()
        amount = q[0] if q else ""
    try:
        pdf = generate_invoice(order, amount)
    except Exception as e:  # pragma: no cover
        bot.send_message(chat_id, f"❌ Erreur PDF : {e}")
        return
    ref = f"KMA-{order['id']:04d}"
    bot.send_document(
        chat_id, pdf, visible_filename=f"facture_{ref}.pdf",
        caption=f"🧾 Facture proforma {ref} — {(order.get('name') or '')[:40]}",
    )
    if send_client and order.get("chat_id"):
        try:
            bot.send_document(
                int(order["chat_id"]), pdf, visible_filename=f"facture_{ref}.pdf",
                caption=f"🧾 Ta facture proforma {ref} — Komara Agency 🇬🇳",
            )
            bot.send_message(chat_id, f"✅ Facture envoyée aussi au client ({order['name']}).")
        except Exception:
            bot.send_message(chat_id, "⚠️ Envoi client impossible (chat introuvable).")
