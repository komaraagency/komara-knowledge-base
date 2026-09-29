# -*- coding: utf-8 -*-
"""
qr_module.py — Feature #3 : Module QR Double Sens (Génération + Scan).

FONCTION 1 : generate_payment_qr(amount, client_id, method)
    QR contenant https://pay.komara.agency/{client_id}/{amount}
    → image qr_paiement_{client_id}.png envoyée au client.

FONCTION 2 : scan_receipt_qr(image_path)
    Décode le QR d'un reçu (pyzbar si dispo, sinon OpenCV pur — aucune
    dépendance système). Si un ID de transaction est détecté :
    log dans `payments` + devis marqué `paid`.

RÈGLE ANTI-CONFLIT (lettre #3) — respectée dans rag_bot.handle_message :
    photo/document reçu  → scan_receipt_qr()
    texte 'payer/qr/paiement/pay' → generate_payment_qr()
Le TYPE du message décide de la fonction, jamais les 2 en même temps.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("komara")

BASE_DIR = Path(__file__).resolve().parent
QR_DIR = BASE_DIR / "qr_cache"
QR_DIR.mkdir(exist_ok=True)

PAY_BASE_URL = "https://pay.komara.agency"

# ID de transaction : TX-123456, txn:AB12CD, Ref OM 1234, Wave Ref…, ou
# l'URL de paiement komara (le client peut renvoyer le QR reçu).
TX_RE = re.compile(
    r"(?:tx|txn|trans(?:action)?|ref(?:erence)?|id)[\s:#/-]*([A-Za-z0-9\-]{4,24})"
    r"|pay\.komara\.agency/(\d+)/([\d.]+)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# FONCTION 1 — génération
# ---------------------------------------------------------------------------

def generate_payment_qr(bot, chat_id: int, amount: float, currency: str,
                        method: str = "Orange Money", lang: str = "fr") -> str | None:
    """Génère le QR de paiement et l'envoie au client. Retour : chemin PNG
    (None si erreur). Légende : « Scanne pour payer {amount} {currency} »."""
    import qrcode

    payload = f"{PAY_BASE_URL}/{chat_id}/{amount:g}"
    img = qrcode.make(payload)
    path = QR_DIR / f"qr_paiement_{chat_id}.png"
    img.save(path)

    caption = {
        "fr": f"Scanne pour payer {amount:g} {currency} 📲\nMéthode : {method}\n"
              f"Dès réception du paiement, envoie-moi une photo du reçu Chef 🙌",
        "en": f"Scan to pay {amount:g} {currency} 📲\nMethod: {method}\n"
              f"Once paid, send me a photo of the receipt boss 🙌",
        "es": f"Escanee para pagar {amount:g} {currency} 📲\nMétodo: {method}\n"
              f"Cuando pague, envíeme una foto del recibo jefe 🙌",
        "ar": f"امسح للدفع {amount:g} {currency} 📲\nالطريقة: {method}\n"
              f"بعد الدفع أرسل لي صورة الإيصال زعيم 🙌",
    }.get(lang, "fr")

    if bot is None:
        logger.info("[SIMU] QR généré : %s (%s %s)", payload, amount, currency)
        return str(path)
    try:
        with open(path, "rb") as f:
            bot.send_photo(chat_id, f, caption=caption)
    except Exception as e:
        logger.error("Envoi QR impossible : %s", e)
        return None
    return str(path)


# ---------------------------------------------------------------------------
# FONCTION 2 — scan
# ---------------------------------------------------------------------------

def _decode_pyzbar(image_path: str) -> str | None:
    try:
        from pyzbar.pyzbar import decode  # optionnel (libzbar requise)
        from PIL import Image
        results = decode(Image.open(image_path))
        for r in results:
            if r.data:
                return r.data.decode("utf-8", errors="replace")
    except Exception:
        return None
    return None


def _decode_opencv(image_path: str) -> str | None:
    """Repli 100 % pip : opencv-python-headless, aucune lib système."""
    try:
        import cv2
        img = cv2.imread(image_path)
        if img is None:
            return None
        detector = cv2.QRCodeDetector()
        ok, decoded, _, _ = detector.detectAndDecodeMulti(img)
        if ok and decoded is not None:
            for d in decoded:
                if d:
                    return d
        data, _, _ = detector.detectAndDecode(img)
        return data or None
    except Exception as e:
        logger.error("Scan OpenCV impossible : %s", e)
        return None


def scan_receipt_qr(image_path: str) -> str | None:
    """Retourne le texte du QR, ou None. Essaie pyzbar puis OpenCV."""
    text = _decode_pyzbar(image_path) or _decode_opencv(image_path)
    if text:
        logger.info("QR scanné : %s", text[:120])
    return text


def extract_transaction_id(qr_text: str) -> tuple[str | None, float | None]:
    """(transaction_id, montant) si le QR contient une référence de
    paiement. Ex : « OM TX-881723 payé 234239 GNF » → ('TX-881723', None)
    ou pay.komara.agency/123/25.5 → (None-mais-URL, 25.5)."""
    if not qr_text:
        return None, None
    m = TX_RE.search(qr_text)
    if not m:
        return None, None
    if m.group(1):  # TX/ref trouvé
        return m.group(1), None
    return f"QR-{m.group(2)}", float(m.group(3) or 0) or None


def handle_receipt_scan(bot, chat_id: int, image_path: str,
                        lang: str = "fr") -> bool:
    """Pipeline complet : scan → vérif transaction → log payments →
    devis paid. Retour True si le message est consommé."""
    import commercial_db as cdb

    text = scan_receipt_qr(image_path)
    if not text:
        # Lot 23 : pas de QR → capture d'écran PayPal ? Si le client a
        # un paiement en attente, on transfère vers l'admin.
        try:
            import payment_links
            if payment_links.maybe_forward_capture(bot, chat_id, image_path, lang):
                return True
        except Exception as e:
            logger.error("Transfert capture : %s", e)
        msg = {
            "fr": "Merci pour la photo Chef 📸 Mais je n'arrive pas à lire de QR "
                  "dessus. Envoie le reçu avec le QR visible, ou tape 'payer' "
                  "pour recevoir notre QR de paiement 🙏",
            "en": "Thanks for the photo boss 📸 But I can't read a QR on it. "
                  "Send the receipt with the QR visible, or type 'pay' to get "
                  "our payment QR 🙏",
            "es": "Gracias por la foto jefe 📸 Pero no puedo leer un QR en ella. "
                  "Envíe el recibo con el QR visible, o escriba 'pagar' para "
                  "recibir nuestro QR de pago 🙏",
            "ar": "شكراً على الصورة زعيم 📸 لكن لا أستطيع قراءة رمز QR فيها. "
                  "أرسل الإيصال مع رمز QR ظاهراً، أو اكتب 'دفع' لاستلام رمز الدفع 🙏",
        }.get(lang, "fr")
        bot.send_message(chat_id, msg)
        return True

    tx_id, amount = extract_transaction_id(text)
    if tx_id:
        cdb.insert_payment(chat_id, tx_id, amount or 0, "", method="qr_scan")
        cdb.mark_quote_paid(chat_id)
        cdb.set_step(chat_id, "paid")
        # F4 : status paid → contrat + facture PDF automatiques
        try:
            import commercial_pack
            commercial_pack.generate_docs(bot, chat_id, lang)
        except Exception as e:
            logger.error("Génération PDF après paiement : %s", e)
        # F6 : produits 1/4/5 → offre assurance MRR
        try:
            import pack_patron
            pack_patron.maybe_offer_assurance(bot, chat_id, lang)
        except Exception as e:
            logger.debug("pack_patron pas encore branché : %s", e)
        ok = {
            "fr": f"Paiement reçu ✅ Merci Chef ! Transaction {tx_id} validée.\n"
                  f"Tes documents arrivent tout de suite 📄🔥",
            "en": f"Payment received ✅ Thanks boss! Transaction {tx_id} validated.\n"
                  f"Preparing your documents right away 📄🔥",
            "es": f"Pago recibido ✅ ¡Gracias jefe! Transacción {tx_id} validada.\n"
                  f"Preparando sus documentos ahora mismo 📄🔥",
            "ar": f"تم استلام الدفع ✅ شكراً زعيم! العملية {tx_id} مؤكدة.\n"
                  f"أجهز مستنداتك الآن 📄🔥",
        }.get(lang, "fr")
        bot.send_message(chat_id, ok)
        logger.info("✅ Paiement validé via QR (chat %s, tx %s)", chat_id, tx_id)
    else:
        info = {
            "fr": "QR lu ✅ Mais je ne trouve pas d'ID de transaction dessus.\n"
                  "Ton opérateur t'a envoyé une confirmation par SMS avec un "
                  "code TX ? Envoie-le ici Chef 🙏 (ou la photo du QR de "
                  "confirmation)",
            "en": "QR read ✅ But I can't find a transaction ID on it.\n"
                  "Your operator sent you an SMS confirmation with a TX code? "
                  "Send it here boss 🙏",
            "es": "QR leído ✅ Pero no encuentro un ID de transacción.\n"
                  "¿Su operador le envió un SMS con código TX? Envíelo aquí jefe 🙏",
            "ar": "تمت قراءة الرمز ✅ لكن لا أجد معرف عملية عليه.\n"
                  "أرسل لك المشغل رسالة SMS بكود TX؟ أرسله هنا زعيم 🙏",
        }.get(lang, "fr")
        bot.send_message(chat_id, info)
    return True


# ---------------------------------------------------------------------------
# déclencheurs texte (règle anti-conflit : UNIQUEMENT pour le texte)
# ---------------------------------------------------------------------------

PAY_TRIGGERS = {"payer", "pay", "qr", "paiement", "pagar", "دفع", "رمز", "/payer", "/pay"}


def handle_pay_request(bot, chat_id: int, lang: str = "fr") -> bool:
    """« payer »/« qr »/« paiement » → QR du dernier devis en attente
    (ou demande du montant). Retour True si consommé."""
    import commercial_db as cdb

    pending = [q for q in cdb.pending_quotes() if q["chat_id"] == str(chat_id)]
    if pending:
        q = pending[-1]
        amount, currency = q["price_local"], q["currency"] or "USD"
    else:
        # pas de devis : on invite au devis, pas de QR au hasard
        ask = {
            "fr": "Tu veux payer quoi exactement Chef ? Tape 'devis' d'abord et "
                  "je te fais le QR du bon montant 👌",
            "en": "What do you want to pay exactly boss? Type 'quote' first and "
                  "I'll make the QR with the right amount 👌",
            "es": "¿Qué quiere pagar exactamente jefe? Escriba 'presupuesto' "
                  "primero y le hago el QR con el monto correcto 👌",
            "ar": "ماذا تريد أن تدفع بالضبط زعيم؟ اكتب 'devis' أولاً وسأصنع لك "
                  "رمز QR بالمبلغ الصحيح 👌",
        }.get(lang, "fr")
        bot.send_message(chat_id, ask)
        return True

    method = "Orange Money / Wave" if q["country_code"] in {"GN", "SN", "CI", "ML", "BF"} else "Carte / Mobile Money"
    generate_payment_qr(bot, chat_id, amount, currency, method, lang)
    return True
