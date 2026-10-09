# ──────────────────────────────────────────────────────────────────────────
# test_devis_secteur — captures Boss 09/10 (« autre catastrophe »)
#
# 1. DEVIS : le Bot Scripté a un délai catalogue de « 48h ». « 48h » est dans
#    DEVIS_URGENT_WORDS -> le devis ajoutait +25 % « express » (62,5 €) sans
#    que le client ait demandé l'urgence. Désormais l'urgence ne se lit que
#    dans les mots du CLIENT.
# 2. SECTEUR : « J'ai une boutique » -> « Tu vends quoi exactement ? » alors
#    que le client vient de le dire. Désormais le secteur est nommé et une
#    action est proposée.
# 3. MENU : le chiffre suit le menu 1-4 (2 puis 3 enchaînables).
# ──────────────────────────────────────────────────────────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TELEGRAM_TOKEN", "123:fake")

import actions
import sector_reply as SR

OK = KO = 0
def check(label, cond, info=""):
    global OK, KO
    if cond: OK += 1
    else:
        KO += 1
        print(f"  KO {label} {info}")

print("── 1. DEVIS : pas de supplément express non demandé ──")
for name, asked, exp in [
    ("Bot Scripté", "", 50),                 # délai catalogue 48h, rien demandé
    ("Bot Scripté", "formation", 50),
    ("Chatbot IA Vendeur", "", 100),
    ("Agent IA Premium", "", 150),
    ("Maintenance mensuelle", "", 50),
    ("Bot Scripté", "urgent", 62.5),         # le CLIENT demande l'urgence
    ("Agent IA Premium", "cette semaine", 187.5),
]:
    data = {"service": name, "activity": "x", "deadline": "48h", "asked_deadline": asked}
    _, total = actions.calc_devis(data)
    check(f"{name} asked={asked!r} -> {exp}€", total == exp, total)

print("── 2. SECTEUR déclaré ──")
for txt, sector in [
    ("J'ai une boutique", "boutique"),
    ("je suis dans l'immobilier", "immobilier"),
    ("mon restaurant à Conakry", "restaurant"),
    ("j'ai un salon de coiffure", "salon"),
    ("je fais de la formation", "formation"),
]:
    check(f"détecte {txt!r}", SR.detect_sector(txt) == sector, SR.detect_sector(txt))
    rep = SR.reply_for(txt) or ""
    check(f"réponse nomme le secteur : {txt!r}", sector.capitalize() in rep and "DEVIS" in rep, rep[:60])
    check(f"pas de re-question : {txt!r}", "vends quoi" not in rep.lower(), "")

check("une QUESTION n'est pas une déclaration",
      SR.reply_for("Quelle boutique me conseilles-tu ?") is None, "")
check("phrase hors secteur -> None", SR.reply_for("Bonjour comment ça va") is None, "")
check("pas d'affirmation inventée (« bons résultats »)",
      "résultats" not in (SR.reply_for("j'ai une boutique") or ""), "")

print("── 3. Accords ta/ton ──")
check("ta boutique", SR.with_possessive("boutique") == "ta boutique", "")
check("ton restaurant", SR.with_possessive("restaurant") == "ton restaurant", "")
check("ton salon", SR.with_possessive("salon") == "ton salon", "")
check("ta formation", SR.with_possessive("formation") == "ta formation", "")

print("── 4. Mot de secteur seul -> menu 1-4 ──")
m = SR.clarify_for("restaurant") or ""
check("menu 1-4 proposé", all(x in m for x in ("1️⃣", "2️⃣", "3️⃣", "4️⃣")), m[:60])
check("phrase longue -> None", SR.clarify_for("je voudrais un restaurant pour mon quartier svp") is None, "")

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
