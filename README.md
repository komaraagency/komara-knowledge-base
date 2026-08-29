# Komara Agency — Bot commercial local

Base de connaissances et moteur conversationnel local de **Komara Agency**. Le dépôt contient un bot Telegram commercial capable de présenter les services numériques de l’agence, de répondre aux questions fréquentes, de gérer les objections tarifaires et de conserver le contexte récent d’une conversation.

> Le bot fonctionne sans GPT, Gemini, OpenAI ni autre service d’intelligence artificielle externe. Les réponses sont générées à partir des fichiers locaux du dépôt et de la mémoire locale de chaque conversation.

## Fonctionnalités

Le bot couvre les compétences et offres suivantes :

| Pôle | Compétences couvertes |
|---|---|
| **Bots intelligents** | Bots WhatsApp, Telegram et TikTok, chatbots conversationnels, service client automatisé 24h/24, qualification de prospects et automatisation des tâches. |
| **Développement & programmation** | Sites web HTML/CSS/JavaScript, applications web et mobiles, scripts, outils internes, intégrations et solutions numériques sur mesure. |
| **Création digitale** | Logos, affiches, flyers, cartes de visite, identité visuelle, branding et contenus pour les réseaux sociaux. |

Le bot répond également sur le portfolio, les étapes de commande, les tarifs, les moyens de paiement, les délais, les objections commerciales et la mise en relation avec un humain.

## Cerveau local

Le fichier `kb.json` contient exactement **200 questions/réponses uniques** organisées par intention. Il contient également **100 exemples de conversations naturelles** utilisés comme modèles locaux lorsque la formulation d’un prospect ne correspond pas exactement à une question enregistrée.

La FAQ éditoriale `docs/faq.md` complète le catalogue avec **28 questions/réponses commerciales**. Au démarrage, `rag_bot.py` charge ces ressources depuis le répertoire du projet, et non depuis un chemin absolu propre à une machine particulière.

L’ordre de résolution est le suivant :

```text
Message reçu
    ↓
Mémoire récente du chat
    ↓
Recherche de la correspondance la plus spécifique dans kb.json
    ↓
Recherche complémentaire dans docs/faq.md
    ↓
Modèles parmi les 100 conversations naturelles
    ↓
Réponse locale de clarification
```

Le moteur donne priorité aux expressions les plus précises. Une demande comme « payer en plusieurs fois » ne doit donc pas être capturée par une réponse générale sur le paiement.

## Mémoire conversationnelle

La mémoire est séparée par `chat_id` Telegram et conservée dans un fichier JSON. Le bot mémorise les derniers messages utiles afin de comprendre des suivis comme :

```text
Je veux un bot pour mon activité.
Et le prix ?
Comment commencer ?
Je dois en parler à mon associé.
```

Pour effacer la mémoire d’une conversation, envoyez l’une des commandes suivantes :

```text
/reset
/forget
oublie
oublie-moi
```

Par défaut, le fichier est `data/memory.json`. Pour conserver la mémoire lors des redéploiements Railway, montez un Volume sur `/data` et définissez :

```text
MEMORY_FILE=/data/komara_memory.json
MEMORY_LIMIT=12
```

## Installation locale

Le projet nécessite Python 3.11 ou une version compatible, ainsi que les dépendances déclarées dans `requirements.txt`.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Pour démarrer le bot Telegram :

```bash
export TELEGRAM_TOKEN="votre_token_BotFather"
python rag_bot.py
```

Le token Telegram ne doit jamais être ajouté à GitHub, à `kb.json`, au README ou à une capture d’écran.

## Déploiement Railway — Worker Telegram

Le `Procfile` définit le processus Telegram :

```text
worker: python rag_bot.py
```

Configurez la variable secrète suivante dans le service Railway :

```text
TELEGRAM_TOKEN=votre_token_BotFather
```

Le Worker doit avoir **une seule réplique** et doit être le seul processus utilisant ce token en long polling. Ne lancez pas simultanément le bot sur un ordinateur local, un second service Railway ou un autre hébergeur avec le même token; cela provoque l’erreur Telegram `409 Conflict`.

## Déploiement Railway — API Web facultative

`api.py` est un service Flask indépendant. Il ne crée pas de bot Telegram et ne démarre aucun polling. Il peut donc être déployé dans un **second service Railway** avec la commande :

```bash
gunicorn --bind 0.0.0.0:$PORT api:app
```

Routes disponibles :

| Méthode | Route | Fonction |
|---|---|---|
| `GET` | `/` | Vérifie que l’API Komara est disponible. |
| `GET` | `/health` | Retourne un état de santé minimal. |
| `POST` | `/chat` | Reçoit `{ "message": "..." }` et retourne une réponse locale. |

Exemple :

```bash
curl -X POST https://votre-domaine.up.railway.app/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Que fais-tu ?"}'
```

Le service Web n’a pas besoin de `TELEGRAM_TOKEN`. Il ne doit pas être utilisé pour démarrer `rag_bot.py`.

Pour protéger la route `/chat`, définissez une clé uniquement dans le service Web :

```text
KOMARA_API_KEY=une_clé_secrète
```

Les requêtes vers `/chat` devront alors inclure l’en-tête `X-API-Key`. Les routes `/` et `/health` restent disponibles pour le contrôle du service. La limite de taille des requêtes est fixée à 32 Ko.

## Monitoring du Worker et de l’API

Le dépôt inclut un monitoring léger et optionnel. L’API expose `/worker-health` et `/internal/heartbeat`. Le Worker envoie un heartbeat périodique vers l’API, mais uniquement si `MONITOR_API_URL` est configurée. Cette requête HTTP ne contacte pas Telegram et ne lance jamais `getUpdates`; elle ne crée donc pas de seconde instance de polling.

Dans le service Web, définissez une clé partagée :

```text
KOMARA_API_KEY=une_clé_secrète
WORKER_HEARTBEAT_TIMEOUT=180
```

Dans le service Worker, définissez :

```text
MONITOR_API_URL=https://votre-api.up.railway.app
MONITOR_API_KEY=la_même_clé_secrète
WORKER_HEARTBEAT_INTERVAL=60
```

Le endpoint `/worker-health` retourne `200` si le dernier heartbeat date de moins de 180 secondes et `503` si aucun heartbeat n’a été reçu ou si le Worker est silencieux. Un service de surveillance HTTP externe peut vérifier cette route toutes les quelques minutes. La valeur par défaut de l’intervalle heartbeat est de 60 secondes.

## Dépendances

Les dépendances sont figées afin de rendre les déploiements reproductibles :

```text
Flask==3.1.3
gunicorn==23.0.0
pyTelegramBotAPI==4.36.1
python-dotenv==1.2.3
```

Le SDK d’un fournisseur d’IA externe n’est volontairement pas installé.

## Structure du dépôt

```text
.
├── api.py                 # API Flask locale facultative
├── kb.json                # 200 Q/R et 100 conversations naturelles
├── rag_bot.py             # Worker Telegram, mémoire et recherche locale
├── Procfile               # worker: python rag_bot.py
├── requirements.txt       # Dépendances figées
├── docs/
│   ├── faq.md             # FAQ commerciale locale
│   ├── Portfolio.md       # Présentation du portfolio
│   ├── process.md         # Processus de commande
│   └── style_guide.md     # Ton et règles rédactionnelles
├── dialogues/             # Dialogues commerciaux de référence
├── portfolio_01           # Média portfolio local
└── portfolio_02           # Média portfolio local
```

## Tests de validation

Avant chaque publication ou redéploiement, exécutez :

```bash
python3 -m json.tool kb.json >/dev/null
python3 -m py_compile rag_bot.py api.py
python3 -m pip check
git diff --check
```

Les tests fonctionnels doivent confirmer le chargement de `kb.json`, le chargement de `docs/faq.md`, les recherches sur les services et tarifs, le suivi contextuel et la commande `/reset`.

## Contact

**Komara Agency**
WhatsApp : `+212 701 986 219`
Paiements possibles selon le projet : Orange Money, MoMo, PayPal ou virement.

---

*Documentation mise à jour le 29 août 2026.*
