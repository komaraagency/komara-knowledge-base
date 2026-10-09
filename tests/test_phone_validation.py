# ──────────────────────────────────────────────────────────────────────────
# test_phone_validation — validateur de numéro strict (Boss 09/10)
#
# Origine : audit des conversations réelles. Un client a tapé
# « Okmjhcfj » puis « +21287654213 » (faux numéro marocain, capté par
# l'ancienne règle « >= 7 chiffres ») et le bot l'a enregistré sur une
# commande. Le validateur commun refuse désormais : textes sans
# chiffres, longueurs impossibles, indicatifs incohérents (+212 à 8
# chiffres locaux), faux évidents (123456789, 000000000).
# ──────────────────────────────────────────────────────────────────────────
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import phone_validation as PV

OK = KO = 0
def check(label, cond, info=""):
    global OK, KO
    if cond: OK += 1
    else:
        KO += 1
        print(f"  KO {label} {info}")

# 1. Numéros VALIDES
for raw in [
    "+212669416020",     # vrai mobile marocain (conversation réelle L587)
    "0612345678",        # local marocain
    "06 12 34 56 78",    # espaces OK
    "+224 622 00 00 00", # Guinée
    "622000000",         # local guinéen
    "+221771234567",     # Sénégal
    "+33612345678",      # France
    "+15125550123",      # USA
    "00447123456789",    # UK via 00
]:
    check(f"valide : {raw!r}", PV.check(raw)[0] is True, PV.check(raw)[1])

# 2. Numéros INVALIDES
bad = [
    ("Okmjhcfj", "not_digits"),       # tapé réellement par un client
    ("D'accord merci", "not_digits"), # capture Boss 08/10
    ("Uyfdt", "not_digits"),          # capture 1
    ("1234", "bad_length"),
    ("+21287654213", "bad_country"),  # FAUX marocain enregistré en prod (L479)
    ("+22462200000", "bad_country"),  # 8 chiffres locaux pour la Guinée
    ("123456789", "fake_pattern"),
    ("000000000", "fake_pattern"),
    ("666666666", "fake_pattern"),
    ("0666666666", "fake_pattern"),   # 6 répété 9 fois
    ("+212 6 12 34 56 7", "bad_country"),  # trop court pour +212
]
for raw, reason in bad:
    ok, got = PV.check(raw)
    check(f"invalide : {raw!r} -> {reason}", ok is False and got == reason, got)

# 3. normalize : forme canonique
check("normalize espaces", PV.normalize("06 12 34 56 78") == "0612345678", "")
check("normalize indicatif", PV.normalize("+224 622 00 00 00") == "+224622000000", "")
check("normalize invalide -> vide", PV.normalize("hhh") == "", "")

# 4. Messages de re-demande adaptés à la raison, 4 langues
for lang in ("fr", "en", "es", "ar"):
    check(f"message {lang} non vide",
          len(PV.reason_msg("not_digits", lang)) > 10, "")
check("indicatif affiché dans le message pays",
      "+212" in PV.reject_msg("+21287654213", "fr"), "")

print(f"\nTOTAL: {OK} OK / {KO} KO")
sys.exit(1 if KO else 0)
