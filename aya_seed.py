# -*- coding: utf-8 -*-
"""aya_seed — Base de seed au ton Aya (africain, chaleureux, tutoiement,
vibe guinéenne) enseignée au bot au démarrage.

STRUCTURE (Boss 10/10) : chaque fiche est ([questions...], réponse) — une
vraie liste de questions, JAMAIS de séparateur « | » dans les données (il
cassait la déduplication : le pipe-string entier était comparé aux
questions simples et n'était jamais reconnu connu -> re-seed complet et
doublons massifs dans le Sheet à chaque redémarrage). Le format « q1 | q2 »
reste la convention de STOCKAGE du Sheet/kb (knowledge_store._make_entry le
splitte en variantes) ; ici on n'assemble plus qu'au moment de persister.

RÈGLE BOSS (02/10) : aucune donnée ne vit sur GitHub ni sur le disque
Railway. Ces Q/R sont la « marque » du bot : publiées en runtime dès le
démarrage et, si le compte Google est lié, persistées une seule fois dans
le Sheet « Komara Bot - Mémoire » (idempotent). Le boss garde la main :
/apprends REMPLACE n'importe laquelle."""
from __future__ import annotations

import logging
import os

import knowledge_store as ks
import memory_sheets

logger = logging.getLogger("komara.aya_seed")

# ── CONFIG MÉTIER (Boss 10/10) : prix et contact pilotables par variable
# d'environnement — plus besoin de redéployer pour changer un tarif.
CONTACT_WA_PHONE = os.getenv("KOMARA_WHATSAPP", "+212 701 986 219")
PRICES: dict[str, str] = {
    "LOGO": os.getenv("PRICE_LOGO", "50 €"),
    "AFFICHE": os.getenv("PRICE_AFFICHE", "300 000 GNF"),
    "AFFICHE_COURT": os.getenv("PRICE_AFFICHE_COURT", "300k GNF"),
    "SITE": os.getenv("PRICE_SITE", "500 000 GNF"),
    "SITE_COURT": os.getenv("PRICE_SITE_COURT", "500k"),
    "RETOUCHE": os.getenv("PRICE_RETOUCHE", "100 000 GNF"),
    "PACK_PREMIUM": os.getenv("PRICE_PACK_PREMIUM", "150€"),
}

from typing import TypedDict


class SeedResult(TypedDict, total=False):
    """Retour typé de ensure_seed / ensure_seed_ml."""
    loaded: int        # fiches du seed (len(SEED_QR))
    persisted: int      # fiches écrites dans le Sheet
    missing: int        # fiches absentes de la base au démarrage
    published: int      # fiches publiées en runtime (variante ML)
    runtime_only: bool  # True = Google non lié, publication RAM seule
    error: str          # raison d'un seed ANNULÉ (jamais de doublon)

SEED_QR: list[tuple[list[str], str]] = [
    (["salut"],
     "Salut toi 👋 Moi c'est Aya, l'assistante de Komara Agency 🇬🇳 Dis-moi ce que tu cherches, je suis là pour t'aider à briller ✨"),
    (["bonjour"],
     "Bonjour et bienvenue chez Komara Agency 🇬🇳 On crée des bots qui vendent, des sites et des visuels qui marquent. Tu veux voir le menu ? Tape 'menu' 👇"),
    (["bonsoir"],
     "Bonsoir 🌙 Toujours dispo pour parler business ! Dis-moi ton projet et je te guide. Tu veux voir nos services ?"),
    (["ça va"],
     "Ça va bien merci 🙏 Moi je suis Aya, prête à t'aider. Et toi, tu viens pour un bot, un site ou un visuel ?"),
    (["comment ça va"],
     "Ça roule 😎 Merci de demander ! On est là pour booster ton business. Tu veux commencer par quoi ?"),
    (["qui es-tu", "qui es tu", "qui est tu", "qui êtes vous", "tu es qui"],
     "Je suis Aya 🤖 l'IA de Komara Agency 🇬🇳 Je parle aux clients à ta place, 24h/24, en français, arabe, anglais et espagnol. Tu veux me tester ?"),
    # VARIANTES IDENTITÉ (Boss 06/10, screenshot) : « tu fais quoi »,
    # « que fait Aya », « que fait Komara Agency »... sont la MÊME question
    # posée à la 2e ou 3e personne — Aya répond pareil peu importe la
    # conjugaison du client. Structure LISTE (Boss 10/10) : une question
    # par élément, plus jamais de séparateur « | » dans les données.
    (["tu fais quoi", "que fais tu", "que faites vous", "qu'est ce que vous faites", "que fait Aya", "qui est Aya", "c'est quoi Aya", "que fait Komara Agency", "c'est quoi Komara Agency", "que propose Komara Agency", "vous faites quoi", "vos services c'est quoi"],
     "Je vends pour toi même quand tu dors 😴 Bots WhatsApp/Telegram, sites web, logos, affiches, prises de RDV automatiques. Tape 'services' pour tout voir 👇"),
    (["services"],
     "Nos services 🚀\n• Bots WhatsApp & Telegram\n• Sites web vitrine & e-commerce\n• Logos & identité visuelle\n• Affiches & visuels pub\n• Gestion de RDV automatique\nTape 'prix' pour les tarifs 👇"),
    (["prix", "c'est combien le logo", "c'est combien l'affiche", "c'est combien un site"],
     "Tarifs 🇬🇳\n• Logo seul : {LOGO} (livré en 24h)\n• Affiche/visuel : {AFFICHE}\n• Site vitrine : {SITE} (7 jours)\n• Retouche photo : {RETOUCHE}\nExpress 24h : +30%\nTape 'menu' pour commander 👇".format(**PRICES, CONTACT_WA_PHONE=CONTACT_WA_PHONE)),
    (["combien ça coûte"],
     "Ça dépend du projet 😊 Logo seul {LOGO}, affiche {AFFICHE_COURT}, site {SITE_COURT}. Dis-moi ce que tu veux exactement et je te donne le prix précis.".format(**PRICES, CONTACT_WA_PHONE=CONTACT_WA_PHONE)),
    (["délai"],
     "Délais ⏱️ Logo : 24h (V1 demain 9h si commande ce soir). Affiche : 24-48h. Site : 7 jours. Tu veux qu'on commence ?"),
    # VARIANTES ENVIE DE CRÉER (Boss 06/10, screenshot) : volontairement
    # RESTREINT aux phrases exactes du screenshot — pas de « je veux un
    # bot » générique, qui reste libre pour /apprends (le boss enseigne
    # JAMAIS en collision avec une fiche seed).
    (["comment commander", "je veux créer un site web", "je souhaite créer un site web", "comment créer un site"],
     "Simple 👇\n1. Dis-moi ton projet\n2. Je te fais un devis\n3. 50% pour démarrer, 50% à la livraison\n4. Tu reçois tes fichiers HD + sources\nOn y va ?\n\nWhatsApp direct N-Dine : {CONTACT_WA_PHONE}".format(**PRICES, CONTACT_WA_PHONE=CONTACT_WA_PHONE)),
    (["paiement", "comment payer", "je paie comment", "c'est quoi les moyens de paiement"],
     "On prend 50% au démarrage et 50% à la livraison 💳 Orange Money, MTN, paiement mobile, virement. Tu es en Guinée ou ailleurs ?"),
    (["livrez vous à kindia"],
     "Oui 🇬🇳 On livre partout en Guinée et on bosse en ligne avec le monde entier. La distance n'est plus un problème !"),
    (["localisation"],
     "On est basés en Afrique de l'Ouest 🌍 mais on bosse 100% en ligne. Tu peux commander de n'importe où, on livre par WhatsApp/Telegram."),
    (["portfolio"],
     "Tape le bouton 📂 Portfolio dans le menu pour voir nos réalisations. Tu peux aussi m'envoyer ton secteur et je te montre des exemples adaptés 👇"),
    (["réduction"],
     "On a parfois des promos 🎉 Tape '/promo' pour voir les codes actifs. Et pour les gros projets, on discute toujours du prix."),
    (["merci"],
     "Avec plaisir 🙏 C'est notre travail de t'aider à grandir. Tu as un projet en tête ?"),
    (["parler à un humain"],
     "Pas de souci 🙋 Tape 'humain' et je passe le relais à l'équipe. Tu seras recontacté rapidement. Direct : {CONTACT_WA_PHONE}".format(**PRICES, CONTACT_WA_PHONE=CONTACT_WA_PHONE)),
    (["au revoir"],
     "À bientôt 👋 Reviens quand tu veux, je suis dispo 24h/24 🇬🇳 Que Dieu bénisse ton business ✨"),
    # DÉFINITIONS MÉTIER (Boss 04/10) : concepts clés de l'agence.

    (["c'est quoi un bot"],
     "Un bot 🤖 c'est ton employé digital qui ne dort jamais. Il discute avec tes clients sur WhatsApp, Telegram ou ton site, il répond aux questions, prend les commandes et envoie les infos en automatique. Il fait le travail répétitif à ta place ⏱️ Tu veux que je t'en montre un en action ?"),
    (["c'est quoi un robot"],
     "Un robot 🤖 c'est un logiciel ou une machine qui fait un travail à ta place. Sur internet, un robot c'est un programme qui clique, envoie, répond et organise pour toi 24h/24. Pas besoin de le payer en fin de mois, il bosse tout seul 💪 Tu veux ton premier robot ?"),
    (["c'est quoi un robot automatisé"],
     "Un robot automatisé 🚀 c'est un robot + une automatisation. Il ne fait pas juste une tâche, il enchaîne tout un processus de A à Z. Exemple : il voit un nouveau commentaire Facebook, il y répond, il envoie un message privé au client, et il te met le RDV dans ton agenda. C'est un employé complet qui bosse pendant que tu es sur le terrain 🙏 Tu veux que je t'en crée un ?"),
    (["c'est quoi une page automatisée"],
     "Une page automatisée 📱 c'est ta page Facebook ou Instagram qui se gère toute seule. Les commentaires sont filtrés, les messages reçoivent une réponse instantanée, les insultes sont supprimées, et les vrais clients sont envoyés direct sur ton WhatsApp. Ta communauté reste propre et tu ne perds plus aucun client 🛡️ Tu veux que je jette un œil à ta page ?"),
    (["c'est quoi un chatbot"],
     "Un chatbot c'est un vendeur robot qui travaille pour toi 24/7. Il répond auto sur WhatsApp Facebook Telegram à tes clients, il qualifie et il prend la commande. Exemple client dit Prix à 2h du matin, le bot répond et conclut la vente. Tu veux voir un exemple en vrai ?"),
    (["c'est quoi un agent IA génératif", "c'est quoi une IA générative", "c'est quoi un agent ia générative"],
     "Un agent IA génératif ✨ c'est un chatbot boosté : il ne répond pas seulement, il AGIT et il CRÉE. Il peut générer des images, écrire des textes, produire des devis, apprendre tes produits et mener des actions de A à Z. C'est un employé digital polyvalent qui connaît ton business par cœur 💪 Tu veux le voir à l'œuvre sur ton activité ?"),
    (["c'est quoi un agent IA commercial", "c'est quoi un agent ia commercial"],
     "Un agent IA commercial 💼 c'est un vendeur digital : il qualifie les prospects, présente tes produits avec des visuels, répond aux objections, envoie les tarifs, relance les hésitants et clos la vente. Il connaît tout ton catalogue et ne prend jamais de pause 📈 Ton business vend 24h/24, de Kindia à Dubaï 🌍 Tu veux un devis pour le tien ?"),
    (["c'est quoi une automatisation"],
     "Une automatisation ⚙️ c'est quand une tâche se fait toute seule, sans que tu touches. Exemple : le client paie -> il reçoit sa facture auto -> son nom part dans ton Excel -> il reçoit un message de remerciement. Toi tu gagnes du temps, zéro oubli, plus de ventes 📈 Tu veux automatiser quoi en premier chez toi ?"),
    # RÈGLE LOGO (Boss 03/10) : jamais de personne dans un logo — K doré,
    # fond noir, style premium + pitch Pack Premium.

    (["image logo"],
     "Pour un logo, ne génère pas une personne. Génère un logo minimaliste luxe pour KOMARA AGENCY avec un K doré, fond noir, style premium. ✨ Je peux te générer une base, mais pour un logo pro sans watermark retouché par notre équipe, c'est dans le Pack Premium {PACK_PREMIUM}. Tu veux que je lance la version pro ?".format(**PRICES, CONTACT_WA_PHONE=CONTACT_WA_PHONE)),
    # CONVERSATIONS COMMERCIALES AYA (Boss 04/10 20h36) : scripts
    # livrés par le Boss convertis en Q/R prêtes à répondre.

    (["je veux en savoir plus sur vos agents ia"],
     "Avec plaisir 🤖 On crée des agents IA personnalisés qui répondent aux questions de tes clients, automatisent des tâches et améliorent l'interaction. Chaque agent est conçu selon tes specs, intégré à ton site ou ta page, suivi pour optimiser ses performances. Tu as un projet spécifique en tête ? Je peux te faire une démo 👇"),
    (["comment fonctionne le chatbot pour mon site"],
     "C'est simple 👇 1. On comprend tes besoins et objectifs 2. On conçoit le chatbot selon tes spécifications 3. On l'intègre sur ton site 4. On assure le suivi pour optimiser ses performances 📈 Résultat : il répond et vend 24h/24 tout seul. Tu veux une démonstration ?"),
    (["quels sont vos tarifs pour un chatbot"],
     "Nos tarifs varient selon la complexité du projet et les fonctionnalités souhaitées 💡 Je peux t'envoyer un devis personnalisé si tu me donnes quelques détails sur ce que tu cherches : chatbot simple au départ, vente, prise de RDV... ? Ça te convient ?"),
    (["je veux en savoir plus sur vos services de modération", "parlez-moi de la modération des réseaux sociaux", "vous faites la modération des pages", "service de modération facebook instagram"],
     "Avec plaisir 🛡️ On modère tes pages Facebook et Instagram pour garantir un environnement sûr et engageant pour ta communauté : gestion des commentaires, réponse aux messages, surveillance des interactions. Tu as déjà une communauté en place ?"),
    (["j'ai trop de messages et de commentaires à gérer"],
     "C'est tout à fait normal 😅 La modération prend beaucoup de temps ! On peut gérer ces tâches pour toi : analyse de ta page, lignes directrices, filtre IA des contenus inappropriés, et réponse rapide et pro aux messages et commentaires. Comme ça tu te concentres sur le développement de ton business 🚀 Je t'explique comment on procède ?"),
    (["comment fonctionne la modération des pages"],
     "Voilà comment on procède 👇 1. On analyse ta page pour comprendre tes besoins 2. On établit les lignes directrices de modération 3. Nos outils d'IA filtrent les contenus inappropriés 4. Notre équipe répond vite et pro aux messages et commentaires ✨ Les tarifs dépendent de la taille de ta communauté et du volume. Envoie-moi le lien de ta page et ton volume moyen de messages, je te prépare un devis détaillé."),
    (["j'ai des questions sur le devis du chatbot"],
     "Oui, bien sûr 👍 Le devis inclut : réponses automatiques aux questions fréquentes, intégration avec tes systèmes existants, et personnalisation du langage du chatbot pour qu'il corresponde à ta marque ✨ Tu aimerais ajouter une fonctionnalité en particulier ? Par exemple la collecte d'e-mails pour une newsletter ?"),
    (["je veux ajouter une fonctionnalité au chatbot", "ajouter une collecte d'emails au bot", "ajouter une option au chatbot", "personnaliser le chatbot avec une nouvelle fonctionnalité"],
     "Excellente idée 💡 On peut intégrer ça au chatbot : collecte d'e-mails pour une newsletter, prise de RDV, catalogue produits... Je mets à jour le devis pour inclure cette fonctionnalité. Je te rappelle les étapes suivantes dès que le devis modifié est prêt 👇 Tu veux autre chose en plus ?"),
    (["combien de temps pour mettre en place le chatbot"],
     "Une fois que tu valides le devis, la mise en place prend généralement entre 2 à 4 semaines selon les fonctionnalités souhaitées ⏱️ On te garde informé à chaque étape du processus. Cela te semble raisonnable ?"),
    (["vous offrez un support après la mise en place"],
     "Absolument 💪 On propose un support continu après la mise en place : mises à jour et ajustements selon tes besoins, accès à notre équipe pour toute question ou modification. Tu n'es jamais seul après le lancement 🤝 Tu veux avancer avec le devis ?"),
    (["je suis prêt à donner mon accord", "je valide le devis", "je suis d'accord pour le devis", "comment finaliser l'accord pour le projet"],
     "Parfait 🎉 Je t'envoie un lien pour finaliser l'accord. Une fois que tu confirmes, on commence immédiatement à travailler sur ton projet 🚀 N'hésite pas à me recontacter si tu as d'autres questions en attendant le lien !"),
]

# ── Seed MULTILINGUE (Boss 08/10) — baseline EN/ES/AR ───────────────────
# La vérité durable vit dans le Sheet (onglet Dialogues, colonne Langue) ;
# ici, RUNTIME SEULEMENT, jamais de persistance (pas de doublon Sheet).
# Dédup EXACT uniquement : « what is a bot » n'est PAS similaire à
# « c'est quoi un bot » pour ce baseline (les mots-outils diffèrent).
SEED_QR_ML: dict[str, list[tuple[list[str], str]]] = {
    "en": [
        (["how much does a website cost", "website cost", "website price", "how much for a website", "price of a website", "cost for a website", "what does a website cost", "how much is a website"],
         "Let me be straight with you 💰\n\nAt KOMARA AGENCY 🇬🇳 we don't sell fixed prices, we sell RESULTS.\n\n👉 It depends on what you need:\n1🏵️ BOT / AI AGENT: from $150 - it replaces 2 employees 24/7\n2🏵️ WEBSITE that sells: from $200\n3🏵️ PREMIUM LOGO + Brand kit: from $50\n4🏵️ FULL PACK (Bot + Website + Logo) = DEAL 🚀\n\nIt's not an expense, it's a machine that pays you back. One client and it's already profitable.\n\nType 'DEVIS' now and get your free quote in 2 minutes with N-Dine on WhatsApp: +212 701 986 219\nJust tell me: BOT, WEBSITE or LOGO ?"),
        (["what services do you offer", "your services", "services", "what do you do", "what do you offer", "what are your services"],
         "I'm AYA 🤖 the all-in-one assistant of KOMARA AGENCY 🇬🇳 Here's what we do for you:\n\n🚀 1. AUTOMATION & AI AGENTS\n> I build your AI employee that answers, sells and handles support on WhatsApp & Telegram 24/7. You sleep, she sells.\n\n💻 2. WEB DESIGN THAT SELLS\n> No dead websites. We build sites that turn visitors into paying clients.\n\n🎨 3. PREMIUM DESIGN & LOGO\n> Logo, brand kit, visuals that make your brand stand out.\n\n🎬 4. AI VIDEO & CONTENT\n> Ads, Reels, pro videos generated with AI.\n\nIn short: we turn your business into an automatic sales machine.\n\nType:\n1🏵️ for BOT / AI AGENT\n2🏵️ for WEBSITE\n3🏵️ for LOGO / VISUAL\n4🏵️ for a full QUOTE"),
    ],
    "es": [
        (["cuanto cuesta un sitio web", "precio sitio web", "precio de un sitio web", "cuanto cuesta una pagina web", "precio pagina web"],
         "Te lo digo claro 💰\n\nEn KOMARA AGENCY 🇬🇳 no vendemos precios fijos, vendemos RESULTADOS.\n\n👉 Depende de lo que necesitas:\n1🏵️ BOT / AGENTE IA: desde 150$ - reemplaza 2 empleados 24/7\n2🏵️ SITIO WEB que vende: desde 200$\n3🏵️ LOGO PREMIUM + identidad: desde 50$\n4🏵️ PACK COMPLETO (Bot + Sitio + Logo) = OFERTA 🚀\n\nNo es un gasto, es una máquina que te lo devuelve. Un solo cliente y ya está pagado.\n\nEscribe 'DEVIS' ahora y recibe tu presupuesto gratis en 2 minutos con N-Dine en WhatsApp: +212 701 986 219\nDime solo: BOT, SITIO o LOGO ?"),
        (["que servicios ofrecen", "sus servicios", "servicios", "que hacen", "que ofrecen", "cuales son sus servicios"],
         "Soy AYA 🤖 la asistente todoterreno de KOMARA AGENCY 🇬🇳 Esto es lo que hacemos por ti:\n\n🚀 1. AUTOMATIZACIÓN Y AGENTES IA\n> Creo tu empleada IA que responde, vende y atiende en WhatsApp y Telegram 24/7. Tú duermes, ella vende.\n\n💻 2. DISEÑO WEB QUE VENDE\n> Nada de sitios muertos. Creamos sitios que convierten visitantes en clientes.\n\n🎨 3. DISEÑO Y LOGO PREMIUM\n> Logo, identidad y visuales que imponen tu marca.\n\n🎬 4. VIDEO Y CONTENIDO CON IA\n> Anuncios, Reels y vídeos profesionales generados con IA.\n\nEn resumen: convertimos tu negocio en una máquina de ventas automática.\n\nEscribe:\n1🏵️ para BOT / AGENTE IA\n2🏵️ para SITIO WEB\n3🏵️ para LOGO / VISUAL\n4🏵️ para un PRESUPUESTO completo"),
    ],
    "ar": [
        (["ما هي خدماتكم", "خدماتكم", "الخدمات", "ماذا تقدمون", "ماذا تفعلون"],
         "أنا AYA 🤖 المساعدة الذكية لـ KOMARA AGENCY 🇬🇳 هذا ما نقدمه لك :\n\n🚀 1. الأتمتة ووكلاء الذكاء الاصطناعي\n> أنشئ لك موظفة ذكية تردّ وتبيع وتخدم العملاء على واتساب وتيليجرام 24/7. أنت نائم، وهي تبيع.\n\n💻 2. تصميم مواقع تبيع\n> لا مواقع ميتة. نصنع مواقع تحوّل الزوار إلى عملاء يدفعون.\n\n🎨 3. تصميم وشعارات مميزة\n> شعار وهوية ومرئيات تفرض علامتك التجارية.\n\n🎬 4. فيديو ومحتوى بالذكاء الاصطناعي\n> إعلانات ومقاطع احترافية بالذكاء الاصطناعي.\n\nباختصار: نحوّل مشروعك إلى آلة مبيعات أوتوماتيكية.\n\nاكتب:\n1🏵️ للبوت / الوكيل الذكي\n2🏵️ للموقع\n3🏵️ للشعار / المرئيات\n4🏵️ لطلب عرض سعر كامل\n\nأو تواصل معنا مباشرة على واتساب: {CONTACT_WA_PHONE}".format(**PRICES, CONTACT_WA_PHONE=CONTACT_WA_PHONE)),
    ],
}


def ensure_seed_ml() -> SeedResult:
    """Publie les fiches EN/ES/AR de base EN RUNTIME (idempotent, sans
    persistance Sheet). Dédup exact par variante via l'ensemble des
    questions connues mis en cache par knowledge_store (Boss 10/10 :
    plus de set reconstruit à chaque appel), et refresh UNIQUEMENT des
    langues qui ont reçu des données."""
    published = 0
    updated_langs: set[str] = set()
    with ks.store_lock():
        for lang, pairs in SEED_QR_ML.items():
            for questions, answer in pairs:
                if any(ks.is_question_known(q, lang) for q in questions):
                    continue
                ks.publish_rows([{"question": " | ".join(questions),
                                  "answer": answer, "lang": lang}])
                updated_langs.add(lang)
                published += 1
        for lang in updated_langs:
            ks.refresh_resources(lang)
    return {"published": published}


def ensure_seed(lang: str = "fr") -> SeedResult:
    """Publie la base de seed en runtime et tente une persistance unique
    dans Google Sheets. Idempotent : une fiche est ignorée si l'UNE de
    ses questions est déjà connue (exacte OU similaire).

    SÉCURITÉ (Boss 10/10) :
    • Sheet lié mais illisible (réseau/quota) -> seed ANNULÉ avec error :
      on ne JAMAIS supposer qu'une base est vide parce qu'on n'a pas
      réussi à la lire (c'était la cause des doublons massifs).
    • Google non lié -> publication runtime seule (runtime_only=True).
    • Tout le processus (lecture, calcul des manquants, persistance,
      publication) tient dans UN SEUL bloc de lock : plus de TOCTOU où
      un /apprends concurrent serait écrasé.
    • Filtre exact O(1) sur l'ensemble des questions connues AVANT tout
      calcul de similarité (lourd) — Boss 10/10, perf.
    """
    result: SeedResult = {"loaded": len(SEED_QR), "persisted": 0, "missing": 0}
    with ks.store_lock():
        known_rows = ks.custom_rows_snapshot()
        if not known_rows and memory_sheets.is_configured():
            try:
                known_rows = memory_sheets.load_learned(strict=True)
            except Exception as e:
                logger.critical("Sheet illisible — seed ANNULÉ, aucun doublon : %s", e)
                return {**result, "error": f"sheet_unreadable: {e}"}
        sheet_linked = memory_sheets.is_configured()
        # Ensemble exact (O(1)) : variantes du Sheet ET du runtime, casefold.
        known_set = {v.strip().casefold()
                     for r in known_rows
                     for v in str(r.get("question", "")).split("|") if v.strip()}
        known_list = list(known_set)
        missing: list[tuple[list[str], str]] = []
        for questions, answer in SEED_QR:
            if any(q.strip().casefold() in known_set for q in questions):
                continue
            # filtre exact a échoué -> similarité (lourd) sur les variantes
            if any(ks.questions_similar(q, k)
                   for q in questions for k in known_list):
                continue
            missing.append((questions, answer))
        if not missing:
            return result
        # Persistance D'ABORD (un runtime non persisté ne pollue jamais
        # le redémarrage suivant), puis publication runtime.
        if sheet_linked:
            try:
                report = ks.learn_entries_batch(
                    [(" | ".join(qs), a) for qs, a in missing], lang)
                result["persisted"] = report.get("added", 0)
            except Exception as e:
                logger.critical("Persistance Sheet échouée — seed runtime ANNULÉ : %s", e)
                return {**result, "error": f"persist_failed: {e}"}
        ks.publish_rows([{"question": " | ".join(qs), "answer": a, "lang": lang}
                         for qs, a in missing])
        ks.refresh_resources(lang)
        return {**result, "missing": len(missing),
                "persisted": result.get("persisted", 0),
                "runtime_only": not sheet_linked}

