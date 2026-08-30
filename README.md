# Komara Agency — Bot commercial local

Base de connaissances et moteur conversationnel local de **Komara Agency**. Le dépôt contient un bot Telegram commercial capable de présenter les services numériques de l'agence, de répondre aux questions fréquentes, de gérer les objections tarifaires et de conserver le contexte récent d'une conversation.

> Le bot fonctionne sans GPT, Gemini, OpenAI ni autre service d'intelligence artificielle externe. Les réponses sont générées à partir des fichiers locaux du dépôt et de la mémoire locale de chaque conversation.

## Fonctionnalités

Le bot couvre les compétences et offres suivantes :

| Pôle | Compétences couvertes |
|---|---|
| **Bots intelligents** | Bots WhatsApp, Telegram et TikTok, chatbots conversationnels, service client automatisé 24h/24, qualification de prospects et automatisation des tâches. |
| **Développement & programmation** | Sites web HTML/CSS/JavaScript, applications web et mobiles, scripts, outils internes, intégrations et solutions numériques sur mesure. |
| **Création digitale** | Logos, affiches, flyers, cartes de visite, identité visuelle, branding et contenus pour les réseaux sociaux. |

Le bot répond également sur le portfolio, les étapes de commande, les tarifs, les moyens de paiement, les délais, les objections commerciales et la mise en relation avec un humain.

## Cerveau local

Le moteur conversationnel s'appuie sur **trois sources locales** chargées au démarrage :

1. **`kb.json`** — Base de connaissances principale contenant **311 fiches de connaissances**, **401 questions et variantes uniques** organisées par intention, ainsi que **100 exemples de conversations naturelles** utilisés comme modèles locaux.
2. **`docs/faq.md`** — FAQ éditoriale avec **28 questions/réponses commerciales** structurées en Markdown.
3. **`docs/dialogues/`** — Dossier contenant l'ensemble des dialogues commerciaux de référence (fichiers `.md` et `.txt`), chacun structuré avec des sections `### Question` suivies de la réponse.

Au démarrage, `rag_bot.py` charge automatiquement ces trois ressources depuis le répertoire du projet. Les chemins sont relatifs et ne dépendent d'aucune machine particulière.

### Moteur de recherche intelligent

Le fichier `local_search.py` implémente un algorithme de recherche par **intersection de mots** (et non par regex stricte). Cette approche offre plusieurs avantages :

- **Compréhension des synonymes** : une question comme « Combien coûte le tarif ? » sera rapprochée de « Quel est le prix ? » grâce aux mots communs.
- **Tolérance aux phrases incomplètes** : l'utilisateur n'a pas besoin de reformuler exactement la question de la FAQ.
- **Fluidité conversationnelle** : le bot ne reste plus bloqué sur un ancien contexte. Dès qu'une nouvelle question obtient un score suffisant, elle est traitée directement sans mélange avec l'historique.
- **Agrégation multi-sources** : les candidats sont collectés dans `kb.json`, `docs/faq.md` et `docs/dialogues/`, puis la réponse avec le **meilleur score absolu** est retenue.

Un seuil minimal de correspondance (0.25) évite les réponses parasites sur des questions trop courtes ou ambiguës.

### Normalisation locale

La recherche applique une normalisation des accents, de la casse, des apostrophes, des traits d'union et des espaces via `normalize_text.py`. Elle rapproche certains synonymes métier contrôlés, par exemple « coût » et « tarif », « site internet » et « site web », ou « automatiser » et « tâche répétitive ». Ces rapprochements ne créent aucune information nouvelle : ils sélectionnent uniquement une réponse déjà validée dans les sources locales.

### Ordre de résolution

```text
Message reçu
    ↓
Mémoire récente du chat (contexte)
    ↓
Recherche directe dans la nouvelle question
    ↓
Recherche par intersection de mots dans :
    • kb.json
    • docs/faq.md
    • docs/dialogues/
    ↓
Si aucun score suffisant → recherche contextuelle (message + historique)
    ↓
Réponse locale de clarification si aucune correspondance
```

Le moteur donne priorité aux expressions les plus précises. Une demande comme « payer en plusieurs fois » ne doit donc pas être capturée par une réponse générale sur le paiement. Une question sans correspondance suffisante reçoit une demande de précision plutôt qu'une réponse inventée.

## Mémoire conversationnelle

La mémoire est séparée par `chat_id` Telegram et conservée dans un fichier JSON. Le bot mémorise les derniers messages utiles afin de comprendre des suivis comme :

```text
Je veux un bot pour mon activité.
Et le prix ?
Comment commencer ?
Je dois en parler à mon associé.
```

Grâce au nouvel algorithme de recherche, le bot traite chaque nouvelle question de manière indépendante et ne reste plus bloqué sur un sujet précédent.

Pour effacer la mémoire d'une conversation, envoyez l'une des commandes suivantes :

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

Le token Telegram ne doit jamais être ajouté à GitHub, à `kb.json`, au README ou à une capture d'écran.

## Déploiement Railway — Worker Telegram

Le `Procfile` définit le processus Telegram :

```text
worker: python rag_bot.py
```

Configurez la variable secrète suivante dans le service Railway :

```text
TELEGRAM_TOKEN=votre_token_BotFather
```

Le Worker doit avoir **une seule réplique** et doit être le seul processus utilisant ce token en long polling. Ne lancez pas simultanément le bot sur un ordinateur local, un second service Railway ou un autre hébergeur avec le même token ; cela provoque l'erreur Telegram `409 Conflict`.

## Déploiement Railway — API Web facultative

`api.py` est un service Flask indépendant. Il ne crée pas de bot Telegram et ne démarre aucun polling. Il peut donc être déployé dans un **second service Railway** avec la commande :

```bash
gunicorn --bind 0.0.0.0:$PORT api:app
```

Routes disponibles :

| Méthode | Route | Fonction |
|---|---|---|
| `GET` | `/` | Vérifie que l'API Komara est disponible. |
| `GET` | `/health` | Retourne un état de santé minimal. |
| `POST` | `/chat` | Reçoit `{ "message": "..." }` et retourne une réponse locale. |

Exemple :

```bash
curl -X POST https://votre-domaine.up.railway.app/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Que fais-tu ?"}'
```

Le service Web n'a pas besoin de `TELEGRAM_TOKEN`. Il ne doit pas être utilisé pour démarrer `rag_bot.py`.

## Statistiques des questions non reconnues

Le Worker et l'API enregistrent localement les questions auxquelles aucune réponse suffisante n'a été trouvée, via le module `local_stats.py`. Elles sont normalisées et agrégées par texte, avec un compteur, les dates de première et dernière occurrence et la source (`telegram` ou `api`). Aucun `chat_id`, token ou historique de conversation n'est enregistré dans ce fichier.

Par défaut, le fichier est `data/unrecognized_questions.json`. Pour le rendre persistant sur Railway, montez un Volume et définissez `UNRECOGNIZED_STATS_FILE=/data/unrecognized_questions.json`. La limite par défaut est de 1000 questions distinctes et peut être ajustée avec `STATS_MAX_ITEMS`.

L'API expose une consultation protégée par `KOMARA_API_KEY` :

```bash
curl -H 'X-API-Key: votre_clé_secrète' \
  'https://votre-domaine.up.railway.app/stats/unrecognized?limit=50'
```

Cette route ne renvoie rien sans clé valide. Les données restent locales au service qui les écrit ; si le Worker et l'API utilisent deux services Railway distincts, utilisez un Volume ou un mécanisme de collecte séparé selon l'architecture souhaitée.

Pour protéger la route `/chat`, définissez une clé uniquement dans le service Web :

```text
KOMARA_API_KEY=une_clé_secrète
```

Les requêtes vers `/chat` devront alors inclure l'en-tête `X-API-Key`. Les routes `/` et `/health` restent disponibles pour le contrôle du service. La limite de taille des requêtes est fixée à 32 Ko.

## Monitoring du Worker et de l'API

Le dépôt inclut un monitoring léger et optionnel. L'API expose `/worker-health` et `/internal/heartbeat`. Le Worker envoie un heartbeat périodique vers l'API, mais uniquement si `MONITOR_API_URL` est configurée. Cette requête HTTP ne contacte pas Telegram et ne lance jamais `getUpdates` ; elle ne crée donc pas de seconde instance de polling.

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

Le endpoint `/worker-health` retourne `200` si le dernier heartbeat date de moins de 180 secondes et `503` si aucun heartbeat n'a été reçu ou si le Worker est silencieux. Un service de surveillance HTTP externe peut vérifier cette route toutes les quelques minutes. La valeur par défaut de l'intervalle heartbeat est de 60 secondes.

## Dépendances

Les dépendances sont figées afin de rendre les déploiements reproductibles :

```text
Flask==3.1.3
gunicorn==23.0.0
pyTelegramBotAPI==4.36.1
python-dotenv==1.2.3
```

Le SDK d'un fournisseur d'IA externe n'est volontairement pas installé.

## Structure du dépôt

```text
.
├── api.py                    # API Flask locale facultative
├── kb.json                   # 311 fiches, 401 questions et 100 conversations naturelles
├── rag_bot.py                # Worker Telegram, mémoire et recherche locale
├── local_search.py           # Moteur de recherche par intersection de mots
├── local_stats.py            # Statistiques des questions non reconnues
├── normalize_text.py         # Normalisation des textes (accents, casse, synonymes)
├── Procfile                  # worker: python rag_bot.py
├── requirements.txt          # Dépendances figées
├── data/
│   ├── memory.json           # Mémoire conversationnelle par chat_id
│   └── unrecognized_questions.json  # Stats des questions non reconnues
├── docs/
│   ├── faq.md                # FAQ commerciale locale (28 questions)
│   ├── dialogues/            # Dialogues commerciaux de référence (.md, .txt)
│   ├── Portfolio.md          # Présentation du portfolio
│   ├── process.md            # Processus de commande
│   └── style_guide.md        # Ton et règles rédactionnelles
├── portfolio_01              # Média portfolio local
├── portfolio_02              # Média portfolio local
└── scripts/
    └── backup_kb.py          # Script de sauvegarde de kb.json
```

## Sauvegarde de kb.json

Le script `scripts/backup_kb.py` crée une copie horodatée de `kb.json`, vérifie que le JSON est valide avant la copie, écrit de manière atomique et supprime les anciennes archives au-delà de la limite configurée.

Exécution manuelle avec conservation des 30 dernières copies :

```bash
python3 scripts/backup_kb.py --keep 30
```

Pour une exécution régulière sur une machine ou un serveur Linux, ajoutez une tâche cron, par exemple chaque jour à 02:00 :

```cron
0 2 * * * cd /chemin/vers/komara-knowledge-base && /usr/bin/python3 scripts/backup_kb.py --keep 30 >> /var/log/komara-backup.log 2>&1
```

Les archives sont placées dans `backups/kb/` par défaut. Sur Railway, le système de fichiers est temporaire sans Volume ; montez donc un Volume sur `/backups` et utilisez :

```bash
python3 scripts/backup_kb.py --backup-dir /backups/kb --keep 30
```

Le script ne remplace jamais `kb.json` et refuse de créer une archive si le fichier source est invalide. Pour une vraie protection contre la perte du projet, copiez aussi les archives vers un stockage externe ou un dépôt privé séparé.

## Tests de validation

Avant chaque publication ou redéploiement, exécutez :

```bash
python3 -m json.tool kb.json >/dev/null
python3 -m py_compile rag_bot.py api.py local_search.py local_stats.py normalize_text.py
python3 -m pip check
git diff --check
```

Les tests fonctionnels doivent confirmer :

- Le chargement de `kb.json`, `docs/faq.md` et `docs/dialogues/` au démarrage.
- Les recherches sur les services et tarifs avec des formulations variées.
- La fluidité conversationnelle (changement de sujet sans blocage).
- La commande `/reset` et l'effacement de la mémoire.
- L'enregistrement des questions non reconnues dans `local_stats.py`.

## Contact

**Komara Agency**
WhatsApp : `+212 701 986 219`
Paiements possibles selon le projet : Orange Money, MoMo, PayPal ou virement.

---

*Documentation mise à jour le 13 janvier 2025.*
