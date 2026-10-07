"""Lot 46 (Boss 07/10, screenshots 17h50) : le bot 'se comporte comme une vache'.

1. Aucune image ne partait : .open() appele sur le tuple ("local", Path).
2. Le '1' du menu services (pitch KB) tombait sur le Portfolio.
3. Un flux de qualification oublie avalait 'Comment tu vas', 'Portfolio'...
4. 'Portfolio' en texte / le titre seul ne donnaient pas la liste / l'image.
"""
import os, sys, json, logging, types, tempfile
os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import actions, rag_bot, list_context
from datetime import datetime, timedelta

KO = 0
def check(name, cond):
    global KO
    print(("OK  " if cond else "KO  ") + name)
    KO += (not cond)

# --- 1. envoi d'image locale ---
sent = []
class FakeBot:
    def send_message(self, cid, text, **kw): sent.append(("TEXT", text))
    def send_photo(self, cid, photo, **kw): sent.append(("PHOTO", kw.get("caption", "")))
    def __getattr__(self, n): return lambda *a, **k: None
rag_bot.bot = FakeBot()
ok = rag_bot.send_portfolio_image_by_index(1, 1, "fr")
check("image locale envoyee (plus d'AttributeError)", ok and sent and sent[0][0] == "PHOTO")

# --- 2. menu services ---
menu = "Tape :\n1\ufe0f\u20e3 pour BOT / AGENT IA\n2\ufe0f\u20e3 pour SITE WEB\n3\ufe0f\u20e3 pour LOGO / VISUEL\n4\ufe0f\u20e3 pour DEVIS complet"
check("menu 1-4 detecte en fin de reponse", bool(rag_bot._SERVICES_MENU_RE.search(menu)))
check("une liste portfolio n'est PAS un menu services",
      not rag_bot._SERVICES_MENU_RE.search("1\ufe0f\u20e3 a\n2\ufe0f\u20e3 b\n3\ufe0f\u20e3 c\n4\ufe0f\u20e3 d\n5\ufe0f\u20e3 e\n6\ufe0f\u20e3 f\n\n\U0001F449 Reponds"))
sent.clear()
check("'1' apres menu services -> route bot (pas portfolio)",
      rag_bot._route_services_choice(5, 1, "fr") and not any(k == "PHOTO" for k, _ in sent))

# --- 3. sortie de flux de qualification ---
f = actions._is_free_question_in_numeric_step
for t in ["Comment tu vas", "Portfolio", "c'est quoi un chatbot", "Salut", "tu fais des bot ?", "prix"]:
    check(f"chatbot_qualify/channel + {t!r} -> sortie", f("chatbot_qualify", "channel", t))
for t in ["WhatsApp", "Telegram", "les deux", "instagram"]:
    check(f"chatbot_qualify/channel + {t!r} -> reponse valide, reste", not f("chatbot_qualify", "channel", t))
check("chatbot_qualify/business + 'boutique de vetements' -> reste",
      not f("chatbot_qualify", "business", "boutique de vetements"))

# --- 4. expiration d'un flux oublie ---
old = (datetime.now() - timedelta(minutes=45)).isoformat(timespec="seconds")
new = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
check("flux de 45 min -> expire", actions._flow_is_stale(old))
check("flux de 5 min -> actif", not actions._flow_is_stale(new))
check("date illisible -> jamais casse", not actions._flow_is_stale("n'importe quoi"))

# --- 5. mots portfolio ---
check("'Portfolio' reconnu", "portfolio" in rag_bot._PORTFOLIO_WORDS)

sys.exit(1 if KO else 0)
