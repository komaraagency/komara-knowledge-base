"""Lot 45 (Boss 07/10, screenshots) : reponses melangees + boucle 'Tape le numero'.

Bug 1 : 'c'est quoi un chatbot' tirait au hasard entre la definition et la fiche
        'Parfait Chef' (variante 'chatbot' seul, ex aequo a 1.0).
Bug 2 : pendant le choix du service (devis/commande), une vraie question libre
        etait avalee -> 'Tape le numero du service (1 a 5)' en boucle.
"""
import os, sys, logging
os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")
logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import local_search as ls
import actions

KO = 0
def check(name, cond):
    global KO
    print(("OK  " if cond else "KO  ") + name)
    KO += (not cond)

# --- Bug 1 : le depart des ex aequo ne melange plus deux sujets ---
KB = [
    {"questions": ["c'est quoi un chatbot", "chatbot c'est quoi"], "answer": "DEFINITION"},
    {"questions": ["je veux un chatbot", "chatbot", "creer un bot"], "answer": "PARFAIT_CHEF"},
]
answers = {ls.trouver_meilleure_reponse("c'est quoi un chatbot", KB, [], []) for _ in range(60)}
check("c'est quoi un chatbot -> toujours la definition", answers == {"DEFINITION"})
answers = {ls.trouver_meilleure_reponse("chatbot", KB, [], []) for _ in range(60)}
check("'chatbot' seul -> fiche Parfait Chef", answers == {"PARFAIT_CHEF"})

# vraies paraphrases : le tirage anti-repetition reste actif
KB2 = [{"questions": ["bonjour"], "answer": ["SALUT_A", "SALUT_B", "SALUT_C"]}]
seen = {ls.trouver_meilleure_reponse("bonjour", KB2, [], []) for _ in range(80)}
check("paraphrases d'une meme fiche : variete conservee", len(seen) >= 2)

# --- Bug 2 : question libre a l'etape numerique ---
f = actions._is_free_question_in_numeric_step
check("devis/service + 'c'est quoi un chatbot' -> sortie", f("devis", "service", "c'est quoi un chatbot"))
check("order/service + 'combien coute un site ?' -> sortie", f("order", "service", "combien coute un site ?"))
check("rdv/slot + 'comment ca marche' -> sortie", f("rdv", "slot", "comment ca marche"))
check("devis/service + '3' -> reste dans le flux", not f("devis", "service", "3"))
check("devis/service + '2️⃣' -> reste dans le flux", not f("devis", "service", "2️⃣"))
check("devis/service + 'logo' (1 mot) -> reste dans le flux", not f("devis", "service", "logo"))
check("autre etape (activity) -> jamais touche", not f("devis", "activity", "c'est quoi un chatbot"))

sys.exit(1 if KO else 0)
