# Lot 5 : audit ton Aya + fiches fondateur Ndine Komara
import json, re
from pathlib import Path

BASE = Path("docs/aya2")

FR_FOUNDER_ANSW = {
    "fondateur": (
        "Le fondateur de Komara Agency 🇬🇳, c'est Ndine Komara 😊 Créateur digital basé en Guinée, "
        "il a bâti l'agence avec une conviction simple : les entrepreneurs africains méritent les "
        "mêmes armes digitales que les grands. Ndine crée des bots intelligents (WhatsApp, "
        "Telegram, TikTok), fait du design graphique pro et accompagne les business qui veulent "
        "grimper dans le digital. Aujourd'hui, lui et son équipe servent des clients en Guinée "
        "comme dans la diaspora. C'est quoi, toi, ton projet avec nous ?"),
    "qui a cree": (
        "Komara Agency 🇬🇳 a été fondée par Ndine Komara, créateur digital basé en Guinée 😊 "
        "Tout est parti de sa mission : aider les autres à grimper dans le digital, avec des "
        "outils qui vendent vraiment — bots intelligents, design pro, sites et formation IA. "
        "Son équipe grandit, et chaque projet lancé porte cette même ambition. "
        "Tu veux rejoindre les clients qui ont déjà sauté le pas ?"),
    "ndine": (
        "Ndine Komara 😊 Le fondateur et le moteur de Komara Agency 🇬🇳. Créateur digital "
        "guinéen : bots intelligents, design graphique pro, accompagnement digital. Sa "
        "signature, c'est des solutions concrètes qui font vendre — pas du digital pour "
        "décorer. Et toute l'agence travaille dans cet esprit. Tu veux en savoir plus sur "
        "ses réalisations ou lancer ton projet ?"),
    "derriere": (
        "Derrière Komara Agency 🇬🇳, il y a une équipe bien réelle 😊 À sa tête, Ndine "
        "Komara, le fondateur : créateur digital basé en Guinée, spécialisé dans les bots "
        "intelligents, le design pro et l'accompagnement digital. Autour de lui, une équipe "
        "qui conçoit, construit et suit chaque projet. Et moi, Aya, je suis la face digitale "
        "qui ne dort jamais 😄 Ton projet, on le lance quand ?"),
    "dirige": (
        "Komara Agency 🇬🇳 est dirigée par son fondateur, Ndine Komara 😊 Créateur digital "
        "basé en Guinée, il pilote l'agence avec une règle simple : chaque projet doit faire "
        "vendre son client. Bots, sites, logos, visuels, formation — tout passe par son "
        "exigence. Et tu peux lui parler de ton projet via l'équipe, ici même. "
        "C'est quoi ton business ?"),
}

FR = [
    ("c est qui le fondateur de komara agency", FR_FOUNDER_ANSW["fondateur"]),
    ("qui a fonde komara agency", FR_FOUNDER_ANSW["fondateur"]),
    ("qui a cree komara agency", FR_FOUNDER_ANSW["qui a cree"]),
    ("c est qui ndine komara", FR_FOUNDER_ANSW["ndine"]),
    ("qui est ndine komara", FR_FOUNDER_ANSW["ndine"]),
    ("qui est derriere komara agency", FR_FOUNDER_ANSW["derriere"]),
    ("qui dirige komara agency", FR_FOUNDER_ANSW["dirige"]),
    ("c est qui le patron de l agence", FR_FOUNDER_ANSW["dirige"]),
    ("le fondateur il fait quoi dans la vie", FR_FOUNDER_ANSW["ndine"]),
]

EN = [
    ("who is the founder of komara agency",
     "The founder of Komara Agency 🇬🇳 is Ndine Komara 😊 A digital creator based in Guinea, "
     "he built the agency on one conviction: African entrepreneurs deserve the same digital "
     "weapons as the big players. Ndine creates smart bots (WhatsApp, Telegram, TikTok), does "
     "pro graphic design, and supports businesses that want to climb in the digital world. "
     "Today, he and his team serve clients in Guinea and the diaspora. So, what's your project with us?"),
    ("who created komara agency",
     "Komara Agency 🇬🇳 was founded by Ndine Komara, a digital creator based in Guinea 😊 "
     "It all started from his mission: helping others climb in the digital world with tools "
     "that actually sell — smart bots, pro design, sites and AI training. His team keeps "
     "growing, and every launched project carries that ambition. "
     "Want to join the clients who already took the leap?"),
    ("who is ndine komara",
     "Ndine Komara 😊 The founder and engine of Komara Agency 🇬🇳. A Guinean digital creator: "
     "smart bots, pro graphic design, digital coaching. His signature is concrete solutions "
     "that sell — not digital for decoration. The whole agency works in that spirit. "
     "Want to know more about his work or launch your project?"),
    ("who is behind komara agency",
     "Behind Komara Agency 🇬🇳 is a very real team 😊 Led by its founder Ndine Komara: a "
     "digital creator based in Guinea, specialized in smart bots, pro design and digital "
     "coaching. Around him, a team that designs, builds and follows every project. And me, "
     "Aya, I'm the digital face that never sleeps 😄 Your project — when do we launch it?"),
    ("who runs komara agency",
     "Komara Agency 🇬🇳 is led by its founder, Ndine Komara 😊 A digital creator based in "
     "Guinea, he runs the agency with one simple rule: every project must make its client "
     "sell. Bots, sites, logos, visuals, training — everything goes through his high "
     "standards. And you can discuss your project with the team, right here. What's your business?"),
]

ES = [
    ("quién es el fundador de komara agency",
     "El fundador de Komara Agency 🇬🇳 es Ndine Komara 😊 Creador digital con base en "
     "Guinea, construyó la agencia con una convicción simple: los emprendedores africanos "
     "merecen las mismas armas digitales que los grandes. Ndine crea bots inteligentes "
     "(WhatsApp, Telegram, TikTok), hace diseño gráfico profesional y acompaña a los "
     "negocios que quieren crecer en lo digital. Hoy, él y su equipo atienden clientes en "
     "Guinea y en la diáspora. ¿Y tú, cuál es tu proyecto con nosotros?"),
    ("quién creó komara agency",
     "Komara Agency 🇬🇳 fue fundada por Ndine Komara, creador digital con base en Guinea 😊 "
     "Todo empezó con su misión: ayudar a otros a crecer en lo digital con herramientas que "
     "de verdad venden — bots inteligentes, diseño pro, sitios y formación IA. Su equipo "
     "crece, y cada proyecto lanzado lleva esa misma ambición. "
     "¿Quieres unirte a los clientes que ya dieron el salto?"),
    ("quién es ndine komara",
     "Ndine Komara 😊 El fundador y motor de Komara Agency 🇬🇳. Creador digital guineano: "
     "bots inteligentes, diseño gráfico pro, acompañamiento digital. Su firma son soluciones "
     "concretas que venden — no lo digital para decorar. Toda la agencia trabaja en ese "
     "espíritu. ¿Quieres saber más de sus realizaciones o lanzar tu proyecto?"),
    ("quién está detrás de komara agency",
     "Detrás de Komara Agency 🇬🇳 hay un equipo muy real 😊 Encabezado por su fundador "
     "Ndine Komara: creador digital con base en Guinea, especializado en bots inteligentes, "
     "diseño pro y acompañamiento digital. Alrededor, un equipo que diseña, construye y "
     "sigue cada proyecto. Y yo, Aya, soy la cara digital que nunca duerme 😄 "
     "¿Tu proyecto, cuándo lo lanzamos?"),
    ("quién dirige komara agency",
     "Komara Agency 🇬🇳 está dirigida por su fundador, Ndine Komara 😊 Creador digital con "
     "base en Guinea, lidera la agencia con una regla simple: cada proyecto debe hacer "
     "vender a su cliente. Bots, sitios, logos, visuales, formación — todo pasa por su "
     "exigencia. Y puedes hablar de tu proyecto con el equipo, aquí mismo. ¿Cuál es tu negocio?"),
]

AR = [
    ("من هو مؤسس وكالة كومارا",
     "مؤسس وكالة كومارا 🇬🇳 هو ندين كومارا 😊 صانع رقمي مقيم في غينيا، بنى الوكالة "
     "بقناعة بسيطة: رواد الأعمال الإفريقيين يستحقون نفس الأسلحة الرقمية التي يملكها "
     "الكبار. ندين يصنع بوتات ذكية (واتساب، تيليجرام، تيك توك)، ويعمل في التصميم "
     "الجرافيكي الاحترافي، ويرافق المشاريع التي تريد الصعود في الرقمي. اليوم، هو "
     "وفريقه يخدمون زبائن في غينيا وفي الجالية. وأنت، ما مشروعك معنا؟"),
    ("من انشأ وكالة كومارا",
     "أنشأ وكالة كومارا 🇬🇳 ندين كومارا، صانع رقمي مقيم في غينيا 😊 كل شيء بدأ من "
     "رسالته: مساعدة الآخرين على الصعود في الرقمي بأدوات تبيع فعلا — بوتات ذكية، "
     "تصميم احترافي، مواقع وتكوين في الذكاء الاصطناعي. فريقه يكبر، وكل مشروع يطلق "
     "يحمل الطموح نفسه. هل تريد الانضمام إلى الزبائن الذين قاموا بالخطوة؟"),
    ("من هو ندين كومارا",
     "ندين كومارا 😊 المؤسس ومحرك وكالة كومارا 🇬🇳. صانع رقمي غيني: بوتات ذكية، "
     "تصميم جرافيكي احترافي، مرافقة رقمية. بصمته هي حلول ملموسة تبيع — لا رقمي "
     "للزينة. والوكالة كلها تعمل بهذا الروح. تريد معرفة المزيد عن أعماله أو إطلاق مشروعك؟"),
    ("من خلف وكالة كومارا",
     "خلف وكالة كومارا 🇬🇳 فريق حقيقي تماما 😊 يقوده مؤسسها ندين كومارا: صانع رقمي "
     "مقيم في غينيا، متخصص في البوتات الذكية والتصميم الاحترافي والمرافقة الرقمية. "
     "وحوله فريق يصمم ويبني ويتابع كل مشروع. وأنا آيا، الوجه الرقمي الذي لا ينام "
     "أبدا 😄 مشروعك، متى نطلقه؟"),
    ("من يدير وكالة كومارا",
     "تدير وكالة كومارا 🇬🇳 مؤسسها ندين كومارا 😊 صانع رقمي مقيم في غينيا، يقود "
     "الوكالة بقاعدة بسيطة: كل مشروع يجب أن يجعل زبونه يبيع. البوتات، المواقع، "
     "الشعارات، التصاميم، التكوين — كل شيء يمر عبر اشتراطه. ويمكنك مناقشة مشروعك مع "
     "الفريق، هنا نفسه. ما هو مشروعك؟"),
]

def merge(path, pack, dedup=True):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    exist = {d["question"] for d in data["dialogues"]}
    added = 0
    for q, a in pack:
        if q not in exist:
            data["dialogues"].append({"question": q, "answer": a})
            exist.add(q)
            added += 1
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return added, len(data["dialogues"])

a, t = merge(f"{BASE}/dialogues.json", FR)
print(f"FR fondateur: +{a} → {t}")
for code, pack in [("en", EN), ("es", ES), ("ar", AR)]:
    a, t = merge(f"{BASE}/dialogues_{code}.json", pack)
    print(f"{code} fondateur: +{a} → {t}")

# ── AUDIT TON AYA : tous les dialogues FR du pack aya2 ──
data = json.loads((BASE / "dialogues.json").read_text(encoding="utf-8"))["dialogues"]
issues = []
for i, d in enumerate(data):
    q, ans = d["question"], d["answer"]
    if not ans.rstrip().endswith(("?", "؟", "!", "…", "👇", ")", "😊")):
        issues.append((i, q, "PAS DE FIN NATURELLE", ans[-50:]))
    # vouvoiement glacé (Aya tutoie) — sauf réponses « vous » de politesse collective
    if re.search(r"\b[Vv]ous (êtes|voulez|avez|pouvez|savez|désirez)\b", ans) and "dites" not in ans:
        issues.append((i, q, "VOUS FROID", ""))
    if re.search(r"\d+\s?€|\d{3,}\s?GNF", ans):
        issues.append((i, q, "PRIX FIXE", ans[:60]))
    if re.search(r"\b(api|github|railway|sqlite|whisper|python|gemini|openai|chatgpt)\b", ans, re.I):
        issues.append((i, q, "FUITE TECH", ans[:60]))
    if "ndine" in ans.lower() and "fondateur" not in ans.lower():
        issues.append((i, q, "NDINE HORS CONTEXTE FONDATEUR", ""))
print("\n── AUDIT AYA ({} dialogues FR) : {} écarts".format(len(data), len(issues)))
for i, q, why, extra in issues:
    print(f"[{i}] {why} | {q[:55]} | {extra}")
