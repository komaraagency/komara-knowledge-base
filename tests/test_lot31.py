# -*- coding: utf-8 -*-
"""Lot 31 — Fixes du 03/10 (screenshots client) :
  1. Catalogue → chiffre nu ajoute au panier (ne part plus sur le portfolio).
  2. Portfolio → chiffre nu continue de fonctionner (pas de régression).
  3. "Voir les Tarifs" affiche le vrai catalogue (plus le stub vide).
  4. survey_done "oui" démarre la commande (plus le "oui" appris ailleurs).
  5. secret_reply "oui" démarre la commande.
  6. Document client (non-admin) → transféré à l'admin + relance devis,
     au lieu du "réservé à l'admin" qui tuait la conversation.
  7. /image et le trigger paiement/QR n'ont subi aucune régression.
"""
import os, sys, tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TELEGRAM_TOKEN"] = "123:TEST"
os.environ["ADMIN_CHAT_ID"] = "99999"
TD = Path(tempfile.mkdtemp(prefix="lot31_"))
os.environ["ACTIONS_DIR"] = str(TD)
os.environ["MEMORY_DIR"] = str(TD)

OK = KO = 0
def check(name, cond, extra=""):
    global OK, KO
    if cond: OK += 1; print(f"OK  {name}")
    else: KO += 1; print(f"KO  {name} -> {extra}")

import rag_bot, actions, catalogue, list_context, img_gen, qr_module

class FakeBot:
    def __init__(self):
        self.sent = []
        self.photos = []
        self.docs = []
        self.forwards = []
    def send_message(self, cid, text=None, **k):
        self.sent.append((cid, text)); return True
    def send_photo(self, cid, *a, **k):
        self.photos.append(cid); return True
    def send_chat_action(self, *a, **k): return True
    def forward_message(self, to_chat, from_chat, msg_id):
        self.forwards.append((to_chat, from_chat, msg_id)); return True
    def send_document(self, *a, **k):
        self.docs.append((a, k)); return True
    def answer_callback_query(self, cid, text=None, **k): return True
    def get_file(self, fid): return type("F", (), {"file_path": "x"})()
    def download_file(self, path): return b"X"

fb = FakeBot()
rag_bot.bot = fb
catalogue.DB_CONN = None  # force la reconstruction propre si besoin

# ── Setup catalogue : au moins 1 produit actif, panier vide ──────────────
# NB : on réutilise la DB initialisée par actions (ACTIONS_DIR temp) au
# lieu d'une connexion brute — init_catalogue_db a besoin de bot_state,
# créé par le schéma global d'actions.
actions.init_db()
products = catalogue.active_products()
check("catalogue a des produits actifs", len(products) >= 1, len(products))

CHAT_CATA = 1001
CHAT_PORTF = 1002

# ── 1. Catalogue : chiffre nu après show_catalogue ajoute au panier ──────
print("── CATALOGUE vs PORTFOLIO (chiffre nu) ──")
catalogue.clear_cart(CHAT_CATA)
fb.sent.clear()
catalogue.show_catalogue(fb, CHAT_CATA, "fr")
check("show_catalogue déclare le contexte 'catalogue'",
      list_context.get_context(CHAT_CATA) == "catalogue")
rag_bot._process_text(CHAT_CATA, "1", "fr")
check("chiffre nu après catalogue → ajouté au panier (pas de portfolio)",
      catalogue.cart_count(CHAT_CATA) == 1, catalogue.cart_count(CHAT_CATA))

# ── 2. Portfolio : chiffre nu continue de fonctionner ────────────────────
print("── PORTFOLIO (régression) ──")
with patch.object(rag_bot, "portfolio_images", lambda: [("Realisation A", "u1"), ("Realisation B", "u2")]):
    rag_bot.send_portfolio(CHAT_PORTF, "fr")
    check("send_portfolio déclare le contexte 'portfolio'",
          list_context.get_context(CHAT_PORTF) == "portfolio")
    sent_before = len(fb.sent)
    with patch.object(rag_bot, "send_portfolio_image_by_index", lambda cid, idx, lang: fb.sent.append((cid, f"IMG_{idx}")) or True):
        rag_bot._process_text(CHAT_PORTF, "2", "fr")
    check("chiffre nu après portfolio → image envoyée (pas le panier)",
          any(t == "IMG_2" for _, t in fb.sent[sent_before:]), fb.sent[sent_before:])
check("panier du chat portfolio resté vide (pas de collision)",
      catalogue.cart_count(CHAT_PORTF) == 0, catalogue.cart_count(CHAT_PORTF))

# ── 2bis. Catalogue inline : boutons "Voir Panier" + "Parler à un humain" ─
print("── CATALOGUE INLINE (boutons panier/support) ──")
class FakeCall:
    def __init__(self, data):
        self.data = data
        self.id = 1
        self.message = type("M", (), {"chat": type("C", (), {"id": 2002})()})()
fb.sent.clear()
catalogue.show_catalogue_inline(fb, 2002, "fr")
check("catalogue inline envoie la photo bannière", len(fb.photos) >= 1, fb.photos)
kb = fb.photos and None
# callback kmr_cart → affiche le panier
catalogue.handle_callback(fb, FakeCall("kmr_cart"), "fr")
check("callback kmr_cart → panier affiché",
      any("Panier" in (t or "") or "panier" in (t or "") for _, t in fb.sent), fb.sent[-2:])
# callback kmr_human → flow human démarré
fb.sent.clear()
catalogue.handle_callback(fb, FakeCall("kmr_human"), "fr")
check("callback kmr_human → tunnel support démarré",
      (actions._fetch_flow(2002) or (None,))[0] == "human", actions._fetch_flow(2002))
actions._clear_flow(2002)

# ── 3. "Voir les Tarifs" affiche le vrai catalogue ────────────────────────
print("── BOUTON TARIFS ──")
fb.sent.clear()
ok_btn = rag_bot._handle_menu_button(2001, "💎 Voir les Tarifs", "fr")
check("bouton Tarifs consommé", ok_btn is True)
check("le vrai catalogue est envoyé (plus le stub vide)",
      any("Agent IA" in (t or "") or "€" in (t or "") for _, t in fb.sent), fb.sent)
check("le stub 'Voici nos offres :' seul n'est plus envoyé",
      not any((t or "").strip() == "Voici nos offres :" for _, t in fb.sent), fb.sent)

# ── 4. survey_done "oui" → commande, pas le KB ────────────────────────────
print("── POST_CTA : survey → oui ──")
actions._save_flow(3001, "survey", "0", {})
# On épuise les questions du sondage pour atteindre survey_done
# (réponse valide selon le type : note pour rate, oui pour bool, texte sinon)
rag_bot.init_memory_db()
for _ in range(len(actions.SURVEY_QUESTIONS) + 2):
    flow = actions._fetch_flow(3001)
    if not flow:
        break
    _, step, data = flow
    if flow[0] != "survey":  # le sondage est fini (post_cta ouvert) → stop
        break
    kind = actions.SURVEY_QUESTIONS[int(step)]["kind"] if step.isdigit() and int(step) < len(actions.SURVEY_QUESTIONS) else "text"
    ans = "5" if kind == "rate" else ("oui" if kind == "bool" else "très content")
    actions._advance_flow(fb, 3001, "survey", step, data, ans, "fr")
check("flow devient post_cta après le sondage",
      (actions._fetch_flow(3001) or (None,))[0] == "post_cta", actions._fetch_flow(3001))
fb.sent.clear()
with patch.object(catalogue, "start_checkout", lambda *a, **k: False):
    handled = actions.handle(fb, 3001, "Oui", "fr")
# NB : après 'Oui', le flow actif doit ÊTRE la commande ("order") — c'est
# le comportement voulu (le tunnel de commande démarre pour de vrai).
_flow_after = actions._fetch_flow(3001)
check("'Oui' après le sondage DÉMARRE la commande (flow = order)",
      handled is True and _flow_after is not None and _flow_after[0] == "order",
      _flow_after)
check("'Oui' n'a PAS atteint le KB (pas de réponse hors-sujet whatsapp)",
      not any("whatsapp" in (t or "").lower() and "démo" in (t or "").lower() for _, t in fb.sent), fb.sent)

# ── 5. secret_reply "oui" → commande ──────────────────────────────────────
print("── POST_CTA : secret_reply → oui ──")
with patch.object(rag_bot, "menu_for_lang", lambda lang: None):
    rag_bot.secret_reply(fb, 4001, "fr")
check("secret_reply ouvre bien post_cta",
      (actions._fetch_flow(4001) or (None,))[0] == "post_cta", actions._fetch_flow(4001))
with patch.object(catalogue, "start_checkout", lambda *a, **k: False):
    handled2 = actions.handle(fb, 4001, "oui", "fr")
check("'oui' après secret_reply démarre aussi la commande", handled2 is True)

# ── 5bis. "non" après un post_cta → clôture polie, flow libéré ────────────
actions._save_flow(5001, "post_cta", "order", {})
handled3 = actions.handle(fb, 5001, "non", "fr")
check("'non' après post_cta clôture proprement",
      handled3 is True and actions._fetch_flow(5001) is None)

# ── 6. Document client (non-admin) → transféré + relance ─────────────────
print("── DOCUMENT CLIENT ──")
class FakeMsg:
    def __init__(self, cid):
        self.chat = type("C", (), {"id": cid})()
        self.document = type("D", (), {"file_name": "brief.pdf", "file_size": 1000})()
        self.caption = None; self.message_id = 42
        self.photo = None; self.location = None; self.voice = None; self.audio = None; self.text = None
fb.forwards.clear(); fb.sent.clear()
try:
    rag_bot._handle_message(FakeMsg(6001))
except Exception as e:
    check("document client ne lève pas d'exception", False, e)
check("document client transféré à l'admin", len(fb.forwards) == 1, fb.forwards)
check("client reçoit une relance (pas le 'réservé à l'admin')",
      any("transmis" in (t or "").lower() or "équipe" in (t or "").lower() for _, t in fb.sent), fb.sent)
check("pas de message 'réservé à l'admin' envoyé au client",
      not any("réservé" in (t or "").lower() for _, t in fb.sent), fb.sent)

# ── 7. /image et paiement/QR : pas de régression ─────────────────────────
print("── /IMAGE ET QR (non-régression) ──")
import re as _re
check("/image reconnu par le routeur img_gen",
      any(_re.match(p, "/image un lion") for p in img_gen._TRIGGERS),
      img_gen._TRIGGERS)
check("trigger paiement présent (ex: 'payer')",
      "payer" in qr_module.PAY_TRIGGERS or any("pay" in w for w in qr_module.PAY_TRIGGERS),
      qr_module.PAY_TRIGGERS)

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
