# -*- coding: utf-8 -*-
"""Lot 20 — Tests : questions non répondues (fichier+notif admin), promos
globales (/solde /promo /KA /bonnus), /apprends admin-only, flux formation."""
import os, sys, json, re
sys.path.insert(0, "/tmp/kb-repo")
os.chdir("/tmp/kb-repo")
os.environ.setdefault("TELEGRAM_TOKEN", "123:TEST")
os.environ.setdefault("ACTIONS_DIR", "/tmp/kb-repo/data_l20t")
os.environ.setdefault("MEMORY_DIR", "/tmp/kb-repo/data_l20t")
os.environ["ADMIN_CHAT_ID"] = "99999"

OK = KO = 0
def check(name, cond, extra=""):
    global OK, KO
    if cond: OK += 1; print(f"OK  {name}")
    else: KO += 1; print(f"KO  {name} -> {extra}")

class FakeBot:
    """Capture tous les send_message (destinataire, texte)."""
    def __init__(self): self.sent = []
    def send_message(self, cid, text, **kw): self.sent.append((cid, text))
    def typing(self, cid, **kw): pass
    def send_chat_action(self, cid, a, **kw): pass

import rag_bot, actions, catalogue
rag_bot.init_memory_db()
bot = FakeBot()

print("── 1. Question non répondue : fichier + notif admin ──")
rag_bot.forget(800)
r = rag_bot.local_contextual_response(800, "c est quoi la mecanique quantique des particules", "fr")
check("question hors base → aucune réponse inventée", r is None, str(r)[:60])
actions.notify_unanswered(bot, 800, "c est quoi la mecanique quantique des particules", "fr")
f = actions.UNANSWERED_FILE
check("fichier unanswered_questions.json créé", f.exists())
data = json.loads(f.read_text(encoding="utf-8"))
check("question stockée avec count + chat", data["items"] and data["items"][0]["count"] == 1
      and data["items"][0]["chat"] == 800, str(data)[:80])
admin_msgs = [t for cid, t in bot.sent if cid == 99999]
check("admin notifié via son ID telegram", any("QUESTION NON RÉPONDUE" in t for t in admin_msgs),
      str(admin_msgs)[:80])
bot2 = FakeBot()
actions.notify_unanswered(bot2, 800, "C'est quoi la mécanique quantique des particules", "fr")
check("pas de doublon de notif pour la même question",
      not any("QUESTION NON RÉPONDUE" in t for _, t in bot2.sent))

print("── 2. Promos globales : /solde /promo /KA /bonnus + catalogue ──")
actions.init_db()
catalogue.init_catalogue_db(actions.DB_CONN)
price_before = [p[3] for p in catalogue.active_products()]
check("catalogue initial chargé (7 services grille)", len(price_before) == 7, len(price_before))
check("chatbot à 100€ avant promo", 100.0 in price_before, str(price_before))
actions.upsert_client(99998, name="Fatou")
actions.upsert_client(99997, name="Mamadou")
b = FakeBot()
actions.set_global_promo(b, 20.0, "SOLDE")
price_after = [p[3] for p in catalogue.active_products()]
check("promo -20% appliquée au catalogue (100€ → 80€)", 80.0 in price_after, str(price_after))
sent_clients = [cid for cid, t in b.sent if cid in (99998, 99997)]
check("les 2 clients notifiés de la promo", len(sent_clients) == 2, str(sent_clients))
check("admin reçoit la confirmation + comptage",
      any(cid == 99999 and "SOLDE" in t and "20" in t for cid, t in b.sent), str(b.sent)[:90])
actions.clear_global_promo(b)
check("promo désactivée → prix grille restaurés",
      100.0 in [p[3] for p in catalogue.active_products()])
for cmd, pct, lbl in [("/solde", 20, "SOLDE"), ("/promo", 30, "PROMO"), ("/ka", 40, "KA"), ("/bonnus", 35, "BONNUS")]:
    bb = FakeBot()
    args = "" if cmd != "/promo" else ""
    actions._admin_command(bb, 99999, cmd, args, "fr")
    prices = [p[3] for p in catalogue.active_products()]
    expected = round(100.0 * (1 - pct / 100), 2)
    check(f"{cmd} → promo -{pct}% ({lbl}) appliquée (100€ → {expected}€)",
          expected in prices and any("🔥" in t for _, t in bb.sent), str(prices))
    actions.clear_global_promo(bb)
# /promo -25% personnalisé
bb = FakeBot()
actions._admin_command(bb, 99999, "/promo", "-25%", "fr")
check("/promo -25% → promo globale personnalisée (100€ → 75€)",
      75.0 in [p[3] for p in catalogue.active_products()])
actions.clear_global_promo(bb)

print("── 3. /apprends : admin seul, base enrichie ──")
b = FakeBot()
actions._admin_command(b, 99999, "/apprends", "vous livrez a kindia || Oui, partout en Guinée 🇬🇳 livraison offerte !", "fr")
check("admin : connaissance ajoutée (confirmation)", "Connaissance ajoutée" in b.sent[-1][1])
kb_custom = json.loads((actions.ACTIONS_DIR / "kb_custom.json").read_text(encoding="utf-8"))
check("kb_custom.json persiste la fiche", kb_custom and "kindia" in kb_custom[-1]["question"])
r = rag_bot.trouver_meilleure_reponse_multilingue("vous livrez a kindia", "fr")
check("la nouvelle connaissance répond immédiatement",
      r is not None and "partout en Guinée" in r, str(r)[:70])
b2 = FakeBot()
actions.handle(b2, 11111, "/apprends vous livrez ou || n importe", "fr")
kb_len_after = len(rag_bot.LANG_RESOURCES["fr"]["kb"])
b2_admin_msgs = [t for cid, t in b2.sent if cid == 11111]
check("client (non admin) : /apprends refusé par la garde admin",
      not any("Connaissance ajoutée" in t for t in b2_admin_msgs) and "n'importe" not in str(b2_admin_msgs),
      str(b2_admin_msgs)[:90])

print("── 4. Flux formation : F1 → autonome → inscription → notif admin ──")
rag_bot.forget(810)
r = rag_bot.local_contextual_response(810, "plus d infos sur la formation ia", "fr")
check("F1 : réponse formation IA Komara",
      r is not None and "Formation IA Komara" in r, str(r)[:80])
rag_bot.remember(810, "assistant", r if r else "")
r2 = rag_bot.local_contextual_response(810, "autonome", "fr")
check("«autonome» après F1 → inscription (sentinel)", r2 == rag_bot.FORMATION_SENTINEL, str(r2)[:70])
r3 = rag_bot.trouver_meilleure_reponse_multilingue("je veux m inscrire", "fr")
check("«je veux m'inscrire» → inscription (sentinel)", r3 == rag_bot.FORMATION_SENTINEL, str(r3)[:70])
# le vrai flux : _process_text-like : sentinel intercepté → texte + attente
b3 = FakeBot()
import actions as A
flow_before = A._fetch_flow(810)
actions.set_pending_formation(810)
check("état d'attente inscription posé", A._fetch_flow(810) is not None)
b3.sent.clear()
A.handle(b3, 810, "Fatou Diallo, je vends des pagnes, +224 622 55 44 33", "fr")
admin_c = [t for cid, t in b3.sent if cid == 99999]
check("admin notifié avec les infos d'inscription",
      any("INSCRIPTION" in t and "Fatou" in t and "622" in t for t in admin_c), str(admin_c)[:100])
check("client reçoit confirmation",
      any(cid == 810 and "enregistrée" in t for cid, t in b3.sent))
check("état d'attente libéré", A._fetch_flow(810) is None)
# annulation propre
actions.set_pending_formation(811)
b4 = FakeBot()
A.handle(b4, 811, "annuler", "fr")
check("«annuler» sort du flux proprement", A._fetch_flow(811) is None and
      any(cid == 811 for cid, _ in b4.sent))

print("── 5. F6 : prix conforme au catalogue (50€, rien d'inventé) ──")
for lang in ["fr", "en", "es", "ar"]:
    m = actions.FORMATION_MSGS[lang]
    check(f"F6 {lang} : 50€ (tarif catalogue) uniquement",
          "50€" in m and not re.search(r"(?<![\d.])\b(6[1-9]|7\d|8\d|9\d|1[1-9]\d|2\d\d)\s*€", m), m[:60])

print("── 6. «plus d'infos» chatbots / logo / site ne partent pas en coq-à-l'âne ──")
for q, kw in [("plus d infos sur les chatbots", "bot|whatsapp|telegram"),
              ("plus d infos sur le logo", "logo"),
              ("plus d infos sur les sites", "site|web|vitrine")]:
    r = rag_bot.trouver_meilleure_reponse_multilingue(q, "fr")
    check(f"«{q}» → réponse sur le sujet", r is not None and re.search(kw, r.lower()), str(r)[:70])

print(f"TOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
