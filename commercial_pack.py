# -*- coding: utf-8 -*-
"""
commercial_pack.py — Feature #4 : Système Commercial 4-en-1.

ORDRE D'EXÉCUTION STRICT (spec) — un if/elif sur client_step, jamais
2 fonctions en même temps :

    qualification > devis > downsell (si déclenché) > bump > docs

Tunnels :
  1. handle_qualification : step==new + demande devis → question budget
     A) <100k B) 100-300k C) +300k (GNF de référence, convertis dans la
     monnaie locale du client). Réponse A (ou montant < 50k GNF) →
     step=low_budget : templates à 50k + STOP, pas de devis.
  2. handle_downsell : « trop cher / pas budget / c'est cher » + step
     ==quoted → Starter à 50% du prix (1 page).
  3. handle_bump : step==quoted + « oui / d'accord / on lance » → pack
     Maintenance + Domaine (+30.000 GNF/mois, converti).
  4. generate_docs : step==paid → contrat_{id}.pdf + facture_{id}.pdf
     (fpdf2, 100% local) envoyés au client.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import commercial_db as cdb
import devis_engine

logger = logging.getLogger("komara")

DOCS_DIR = Path(__file__).resolve().parent / "docs_commerciaux"
DOCS_DIR.mkdir(exist_ok=True)

# Seuils de qualification — base GNF (lettre #4), convertis à l'affichage
TIER_LOW_GNF = 100_000.0     # A) < 100k
TIER_HIGH_GNF = 300_000.0    # B) 100-300k, C) > 300k
LOW_BUDGET_LIMIT_GNF = 50_000.0   # sous 50k → templates + stop
TEMPLATES_PRICE_GNF = 50_000.0    # offre templates
BUMP_MONTH_GNF = 30_000.0         # Maintenance + Domaine / mois
WHATSAPP = "+212701986219"

DOWNSELL_WORDS = {"trop cher", "pas budget", "pas de budget", "c'est cher",
                  "cest cher", "trop chere", "c est cher", "no budget",
                  "too expensive", "es caro", "muy caro", "غالي",
                  "مكلف"}
CONFIRM_WORDS = {"oui", "yes", "d'accord", "daccord", "on lance", "ok",
                 "si", "sí", "va", "نعم", "حسناً", "ok je veux", "je confirme"}
BUMP_YES = {"oui", "yes", "sí", "نعم"}
BUMP_NO = {"non", "no", "لا"}

# ---------------------------------------------------------------------------
# Messages multilingues — ton Aya FR, adresse boss/Chef (règle d'or 3)
# ---------------------------------------------------------------------------
M = {
    "fr": {
        "qualify": ("Pour te faire un prix juste, 3 questions rapides, boss :\n"
                    "1️⃣ Ton budget ?\n"
                    "A) Moins de {low}\nB) {low} – {high}\nC) Plus de {high}\n"
                    "Réponds A, B ou C 👇"),
        "low_budget": ("Pour ce budget on a des templates à {tpl} 🎨\n"
                       "Voici le lien : {link}\n\nSi tu veux du 100% sur mesure "
                       "plus tard, je suis là Chef 🙌"),
        "qualified": ("Merci Chef 🙌 Avec ce budget je peux te préparer un "
                      "devis sur mesure. Quel service tu vises ?\n\n{grid}\n\n"
                      "Tape le numéro 👇"),
    },
    "en": {
        "qualify": ("To give you a fair price, 3 quick questions boss:\n"
                    "1️⃣ Your budget?\n"
                    "A) Under {low}\nB) {low} – {high}\nC) Above {high}\n"
                    "Answer A, B or C 👇"),
        "low_budget": ("For that budget we have templates at {tpl} 🎨\n"
                       "Here's the link: {link}\n\nIf you want 100% custom "
                       "later, I'm here chief 🙌"),
        "qualified": ("Thanks boss 🙌 With that budget I can prepare a custom "
                      "quote. Which service are you after?\n\n{grid}\n\n"
                      "Type the number 👇"),
    },
    "es": {
        "qualify": ("Para darle un precio justo, 3 preguntas rápidas jefe:\n"
                    "1️⃣ ¿Su presupuesto?\n"
                    "A) Menos de {low}\nB) {low} – {high}\nC) Más de {high}\n"
                    "Responda A, B o C 👇"),
        "low_budget": ("Para ese presupuesto tenemos plantillas a {tpl} 🎨\n"
                       "Aquí tiene el enlace: {link}\n\nSi quiere algo 100% "
                       "personalizado más tarde, aquí estoy jefe 🙌"),
        "qualified": ("Gracias jefe 🙌 Con ese presupuesto puedo prepararle un "
                      "presupuesto a medida. ¿Qué servicio busca?\n\n{grid}\n\n"
                      "Escriba el número 👇"),
    },
    "ar": {
        "qualify": ("لأعطيك سعراً عادلاً، 3 أسئلة سريعة زعيم:\n"
                    "1️⃣ ميزانيتك؟\n"
                    "أ) أقل من {low}\nب) {low} – {high}\nج) أكثر من {high}\n"
                    "أجب أ أو ب أو ج 👇"),
        "low_budget": ("لهذه الميزانية لدينا قوالب بسعر {tpl} 🎨\n"
                       "ها هو الرابط: {link}\n\nإذا أردت تصميماً حسب الطلب "
                       "لاحقاً أنا هنا زعيم 🙌"),
        "qualified": ("شكراً زعيم 🙌 بهذه الميزانية يمكنني إعداد عرض مخصص. "
                      "أي خدمة تريد؟\n\n{grid}\n\nاكتب الرقم 👇"),
    },
}
TEMPLATES_LINK = "https://komara-agency.onrender.com"


def t(lang: str, key: str, **kw) -> str:
    return (M.get(lang) or M["fr"])[key].format(**kw)


def _fmt_gnf_in(gnf: float, cc: str) -> str:
    """Affiche un montant GNF dans la monnaie du client."""
    conv = devis_engine.convert_devis(cc, gnf / devis_engine.usd_per_unit("GNF"),
                                      "USD") if devis_engine.usd_per_unit("GNF") else None
    if conv:
        return devis_engine.format_price(conv)
    return f"{gnf:,.0f}".replace(",", " ") + " GNF"


# ---------------------------------------------------------------------------
# 1. QUALIFICATION
# ---------------------------------------------------------------------------

def start_qualification(bot, chat_id: int, lang: str, grid: str) -> bool:
    """Étape 1 de l'ordre strict : posée au début du devis si
    client_step == 'new'. Retour True = message envoyé."""
    cdb.set_step(chat_id, "qualifying")
    cc = _country_of(chat_id, lang)
    bot.send_message(chat_id, t(lang, "qualify",
                                low=_fmt_gnf_in(TIER_LOW_GNF, cc),
                                high=_fmt_gnf_in(TIER_HIGH_GNF, cc)))
    _remember_grid(chat_id, grid)
    return True


def handle_qualification_answer(bot, chat_id: int, text: str,
                                 lang: str) -> bool:
    """A/B/C ou montant → routing spec. True = consommé."""
    low = text.strip().lower()
    cc = _country_of(chat_id, lang)
    m = re.search(r"\d[\d\s.,]*", text)

    tier = None
    if re.fullmatch(r"a[\).\s]*", low) or "moins" in low or "less" in low:
        tier = "A"
    elif re.fullmatch(r"b[\).\s]*", low):
        tier = "B"
    elif re.fullmatch(r"c[\).\s]*", low) or "plus" in low or "above" in low:
        tier = "C"
    elif m:
        # montant chiffré → converti vers GNF pour comparer aux seuils
        val = float(re.sub(r"[^\d.]", "", m.group(0)) or 0)
        # le client parle dans sa monnaie locale : reconvertissons
        gnf = _to_gnf(val, cc)
        tier = "A" if gnf < TIER_LOW_GNF else ("B" if gnf < TIER_HIGH_GNF else "C")
    if tier is None:
        return False  # pas une réponse de qualification → laisse passer

    cdb.save_qualification(chat_id, tier, text[:100])
    if tier == "A":
        # spec : A (ou < 50k) → low_budget + STOP, pas de devis
        if not m or _to_gnf(float(re.sub(r"[^\d.]", "", m.group(0)) or 0), cc) < LOW_BUDGET_LIMIT_GNF or not m:
            cdb.set_step(chat_id, "low_budget")
            bot.send_message(chat_id, t(lang, "low_budget",
                                       tpl=_fmt_gnf_in(TEMPLATES_PRICE_GNF, cc),
                                       link=TEMPLATES_LINK))
            return True
    # B / C (ou A au-dessus de 50k) → on lance le devis (la grille
    # de start_flow EST la réponse, pas de doublon de message)
    cdb.set_step(chat_id, "qualified")
    actions_start_devis(bot, chat_id, lang)
    return True


def _to_gnf(amount: float, cc: str) -> float:
    """Montant en monnaie locale du client → équivalent GNF."""
    rate = devis_engine.usd_per_unit("GNF") or 8600.0
    cur = devis_engine._load_currencies().get(cc, {}).get("currency", "USD")
    r2 = devis_engine.usd_per_unit(cur) or 1.0
    return amount / r2 * rate


def _country_of(chat_id: int, lang: str) -> str:
    import actions
    client = actions.get_client(chat_id) or {}
    cc, src = devis_engine.detect_locality(client.get("phone") or "", lang)
    return cc or "GN"


_GRIDS: dict[int, str] = {}


def _remember_grid(chat_id: int, grid: str) -> None:
    _GRIDS[chat_id] = grid


def _recall_grid(chat_id: int) -> str:
    return _GRIDS.get(chat_id, "Tape 'devis' pour voir les services 👇")


def actions_start_devis(bot, chat_id: int, lang: str) -> None:
    """Démarre le flux devis standard (grille) après qualification."""
    import actions
    actions.start_flow(bot, chat_id, "devis", lang)


# ---------------------------------------------------------------------------
# 2. DOWNSELL
# ---------------------------------------------------------------------------

def handle_downsell(bot, chat_id: int, lang: str) -> bool:
    """« trop cher » + step==quoted → Starter à 50%. True = consommé."""
    cdb.set_step(chat_id, "downsell_offered")
    pending = [q for q in cdb.pending_quotes() if q["chat_id"] == str(chat_id)]
    if not pending:
        return False
    q = pending[-1]
    half = round((q["price_local"] or 0) / 2, 2)
    cdb.update_quote(q["id"], downsell_price=half) if _has_col("downsell_price") else None
    msgs = {
        "fr": "Compris Chef. On a une version Starter à 50% du prix "
              f"({half:g} {q['currency']}) avec 1 page.\n"
              "Tu veux démarrer avec ça ?",
        "en": f"Got it boss. We have a Starter version at 50% of the price "
              f"({half:g} {q['currency']}) with 1 page.\nWant to start with that?",
        "es": f"Entendido jefe. Tenemos una versión Starter al 50% del precio "
              f"({half:g} {q['currency']}) con 1 página.\n¿Quiere empezar con eso?",
        "ar": f"فهمت زعيم. لدينا نسخة ستارتر بنصف السعر "
              f"({half:g} {q['currency']}) بصفحة واحدة.\nأتريد البدء بها؟",
    }
    bot.send_message(chat_id, msgs.get(lang, msgs["fr"]))
    return True


def accept_downsell(chat_id: int) -> None:
    """OUI au downsell → price_local divisé par 2 (spec)."""
    pending = [q for q in cdb.pending_quotes() if q["chat_id"] == str(chat_id)]
    if pending:
        q = pending[-1]
        cdb.update_quote(q["id"],
                         price_local=round((q["price_local"] or 0) / 2, 2),
                         price_eur=round((q["price_eur"] or 0) / 2, 2))


def _has_col(col: str) -> bool:
    import actions
    cols = [r[1] for r in actions.DB_CONN.execute(
        "PRAGMA table_info(pending_quotes)")]
    return col in cols


# ---------------------------------------------------------------------------
# 3. BUMP
# ---------------------------------------------------------------------------

def handle_bump(bot, chat_id: int, lang: str) -> bool:
    """« oui / d'accord / on lance » + step==quoted → offre Maintenance
    + Domaine (+30.000 GNF/mois). True = consommé."""
    cdb.set_step(chat_id, "bump_offered")
    cc = _country_of(chat_id, lang)
    bump_txt = _fmt_gnf_in(BUMP_MONTH_GNF, cc)
    msgs = {
        "fr": f"Top 🔥 La plupart ajoutent le pack Maintenance + Domaine pour "
              f"+{bump_txt}/mois pour éviter les bugs.\n"
              f"Je te l'ajoute boss ? Réponds par OUI ou NON",
        "en": f"Top 🔥 Most people add the Maintenance + Domain pack for "
              f"+{bump_txt}/month to avoid bugs.\n"
              f"Shall I add it boss? Answer YES or NO",
        "es": f"¡Top! 🔥 La mayoría añade el pack Mantenimiento + Dominio por "
              f"+{bump_txt}/mes para evitar fallos.\n"
              f"¿Se lo añado jefe? Responda SÍ o NO",
        "ar": f"رائع 🔥 معظم العملاء يضيفون حزمة الصيانة + النطاق "
              f"+{bump_txt}/شهرياً لتجنب الأعطال.\n"
              f"أضيفها لك زعيم؟ أجب بنعم أو لا",
    }
    bot.send_message(chat_id, msgs.get(lang, msgs["fr"]))
    return True


def accept_bump(chat_id: int, cc: str) -> float:
    """OUI au bump → +30.000 GNF/mois converti, ajouté au devis."""
    gnf_rate = devis_engine.usd_per_unit("GNF") or 8600.0
    bump_usd = BUMP_MONTH_GNF / gnf_rate
    conv = devis_engine.convert_devis(cc, bump_usd, "USD")
    pending = [q for q in cdb.pending_quotes() if q["chat_id"] == str(chat_id)]
    if pending:
        q = pending[-1]
        cdb.update_quote(q["id"],
                         price_local=round((q["price_local"] or 0) + conv["price_local"], 2),
                         price_eur=round((q["price_eur"] or 0) + bump_usd * 0.92, 2))
    return conv["price_local"]


# ---------------------------------------------------------------------------
# 4. DOCS (fpdf2)
# ---------------------------------------------------------------------------

_LATIN_MAP = {"—": "-", "–": "-", "’": "'", "‘": "'", "“": '"', "”": '"',
              "…": "...", "•": "-", "×": "x", "→": "->", "€": "EUR"}


def _latin(text) -> str:
    """Helvetica (fpdf2 core) ne supporte que latin-1 : on translitère."""
    s = str(text)
    for k, v in _LATIN_MAP.items():
        s = s.replace(k, v)
    # emojis et autres hors latin-1 : supprimés (les accents restent)
    return s.encode("latin-1", errors="ignore").decode("latin-1")


def generate_docs(bot, chat_id: int, lang: str = "fr") -> list[str]:
    """status==paid → contrat_{id}.pdf + facture_{id}.pdf, envoyés
    au client. Retour : chemins créés."""
    from fpdf import FPDF

    pending_paid = _last_paid_quote(chat_id)
    name = (pending_paid or {}).get("client_name") or "Client"
    service = (pending_paid or {}).get("service") or "Service Komara Agency"
    desc = (pending_paid or {}).get("project_desc") or ""
    price_eur = (pending_paid or {}).get("price_eur") or 0
    price_local = (pending_paid or {}).get("price_local") or 0
    currency = (pending_paid or {}).get("currency") or "USD"
    qid = (pending_paid or {}).get("id") or chat_id

    paths = []
    for kind, title in (("contrat", "CONTRAT DE PRESTATION"),
                        ("facture", "FACTURE")):
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, "Komara Agency", ln=1)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 6, _latin("Agence digitale - komara-agency.onrender.com"), ln=1)
        pdf.ln(6)
        pdf.set_font("Helvetica", "B", 13)
        pdf.cell(0, 8, title, ln=1)
        pdf.ln(4)
        pdf.set_font("Helvetica", "", 11)
        rows = [
            ("Client", _latin(name)),
            ("Service", _latin(service)),
            ("Description", _latin(desc)[:180]),
            ("Prix", f"{price_eur:g} EUR ({price_local:g} {currency})"),
            ("Reference", f"{kind.upper()}-{qid}"),
            ("Contact", WHATSAPP),
        ]
        for k, v in rows:
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(45, 8, k + " :")
            pdf.set_font("Helvetica", "", 11)
            # une ligne par champ (coupe proprement si trop long)
            pdf.cell(0, 8, _latin(v)[:110], ln=1)
        pdf.ln(6)
        pdf.set_font("Helvetica", "I", 9)
        pdf.multi_cell(0, 6,
                       _latin("Komara Agency - Guinee. Paiement 60% au "
                              "lancement, 40% a la livraison. "
                              "Devis valide 7 jours."))
        p = DOCS_DIR / f"{kind}_{qid}.pdf"
        pdf.output(str(p))
        paths.append(str(p))

    if bot is not None:
        try:
            for p in paths:
                with open(p, "rb") as f:
                    bot.send_document(chat_id, f)
        except Exception as e:
            logger.error("Envoi PDF impossible : %s", e)
    return paths


def _last_paid_quote(chat_id: str | int) -> dict | None:
    import actions
    conn = actions.DB_CONN
    row = conn.execute(
        "SELECT * FROM pending_quotes WHERE chat_id=? AND status='paid' "
        "ORDER BY id DESC LIMIT 1", (str(chat_id),)).fetchone()
    if not row:
        return None
    cols = [d[0] for d in conn.execute(
        "SELECT * FROM pending_quotes LIMIT 1").description]
    return dict(zip(cols, row))


# ---------------------------------------------------------------------------
# ROUTEUR PRINCIPAL — ordre strict qualification > devis > downsell > bump
# ---------------------------------------------------------------------------

def handle(bot, chat_id: int, text: str, lang: str = "fr") -> bool:
    """Branché dans _process_text APRÈS actions.handle (flux devis) et
    AVANT la KB. Ordre strict if/elif sur client_step."""
    try:
        low = text.strip().lower()
        step = cdb.get_step(chat_id)

        # F6 : réponse assurance (OUI ASSURANCE / NON) — prioritaire,
        # posée juste après un paiement produit 1/4/5
        if step == "paid":
            import pack_patron
            if pack_patron.handle_assurance_reply(bot, chat_id, text, lang):
                return True

        # qualification en cours : la réponse A/B/C/montant est consommée
        if step == "qualifying":
            if handle_qualification_answer(bot, chat_id, text, lang):
                return True

        # downsell offert : OUI → prix/2, NON → on reste
        if step == "downsell_offered":
            if low in CONFIRM_WORDS:
                accept_downsell(chat_id)
                cdb.set_step(chat_id, "quoted")
                bot.send_message(chat_id, _msg_downsell_ok(lang))
                # après le downsell accepté → propose le bump (ordre strict)
                return handle_bump(bot, chat_id, lang)
            if low in BUMP_NO:
                cdb.set_step(chat_id, "quoted")
                bot.send_message(chat_id, _msg_no_problem(lang))
                return True

        if step == "quoted":
            # downsell AVANT le bump (ordre spec)
            if any(w in low for w in DOWNSELL_WORDS):
                return handle_downsell(bot, chat_id, lang)
            if low in CONFIRM_WORDS:
                return handle_bump(bot, chat_id, lang)

        if step == "bump_offered":
            if low in BUMP_YES:
                cc = _country_of(chat_id, lang)
                added = accept_bump(chat_id, cc)
                bot.send_message(chat_id, _msg_bump_ok(lang, added, cc))
                cdb.set_step(chat_id, "quoted")
                return True
            if low in BUMP_NO:
                bot.send_message(chat_id, _msg_no_problem(lang))
                cdb.set_step(chat_id, "quoted")
                return True

        # status paid (déjà payé) → docs générés une fois au scan ;
        # le client peut aussi les redemander
        if step == "paid" and low in {"docs", "documents", "contrat",
                                      "facture", "pdf", "مستندات"}:
            generate_docs(bot, chat_id, lang)
            return True
        return False
    except Exception as e:
        logger.error("commercial_pack.handle : %s", e)
        return False


def _msg_downsell_ok(lang):
    return {
        "fr": "Parfait Chef 🙌 Starter validé à mi-prix. Je mets ton devis à jour.",
        "en": "Perfect boss 🙌 Starter validated at half price. Updating your quote.",
        "es": "Perfecto jefe 🙌 Starter validado a mitad de precio. Actualizo su presupuesto.",
        "ar": "ممتاز زعيم 🙌 تم اعتماد ستارتر بنصف السعر. أحدث عرضك.",
    }.get(lang, "")


def _msg_no_problem(lang):
    return {
        "fr": "Pas de souci Chef 😊 On reste sur le devis actuel.",
        "en": "No problem boss 😊 We keep the current quote.",
        "es": "Sin problema jefe 😊 Mantenemos el presupuesto actual.",
        "ar": "لا مشكلة زعيم 😊 نبقى على العرض الحالي.",
    }.get(lang, "")


def _msg_bump_ok(lang, added, cc):
    return {
        "fr": f"Ajouté 🔥 Pack Maintenance + Domaine activé (+{added:g} au devis). "
              "Tape 'payer' pour le QR, Chef.",
        "en": f"Added 🔥 Maintenance + Domain pack activated (+{added:g} on the "
              "quote). Type 'pay' for the QR, boss.",
        "es": f"Añadido 🔥 Pack Mantenimiento + Dominio activado (+{added:g} en "
              "el presupuesto). Escriba 'pagar' para el QR, jefe.",
        "ar": f"تمت الإضافة 🔥 حزمة الصيانة + النطاق مفعلة (+{added:g} على العرض). "
              "اكتب 'دفع' لرمز QR يا زعيم.",
    }.get(lang, "")
