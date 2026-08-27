import os
import telebot
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer, util

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN") # Mets ton token dans.env
bot = telebot.TeleBot(TOKEN)

# 1. Charger le cerveau IA
model = SentenceTransformer('all-MiniLM-L6-v2')

# 2. Charger tous les fichiers.md dans la mémoire
knowledge_base = []
for fichier in os.listdir('dialogues'):
    if fichier.endswith('.md'):
        with open(f'dialogues/{fichier}', 'r', encoding='utf-8') as f:
            contenu = f.read()
            knowledge_base.append({"source": fichier, "texte": contenu})

# 3. Créer les embeddings
embeddings = model.encode([k['texte'] for k in knowledge_base], convert_to_tensor=True)

def trouver_reponse(question_user):
    question_embedding = model.encode(question_user, convert_to_tensor=True)
    resultats = util.semantic_search(question_embedding, embeddings, top_k=1)
    meilleur_match = resultats[0][0]
    return knowledge_base[meilleur_match['corpus_id']]['texte']

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    question = message.text
    reponse = trouver_reponse(question)
    # On envoie que les 4000 premiers caractères pour Telegram
    bot.reply_to(message, reponse[:4000]) 

print("Bot Komara lancé...")
bot.polling()
