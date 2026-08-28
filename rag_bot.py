import os
import telebot
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TOKEN)

# On charge la personnalité de KOMARA AGENCY
with open("knowledge.txt", "r", encoding="utf-8") as f:
    KNOWLEDGE = f.read()

def generer_reponse(user_message):
    """L'IA répond en s'appuyant sur le knowledge.txt"""
    user_message = user_message.lower()

    # Logique simple RAG basée sur mots-clés + posture
    if "service" in user_message or "proposer" in user_message:
        return f"""L'agence KOMARA AGENCY élève les standards du marché digital en Afrique.
        
Nous préconisons 3 pôles d'excellence :
1.  **Branding** : Création d'identités visuelles impactantes Noir & Or
2.  **Création Digitale** : Visuels et vidéos publicitaires 8K haute fidélité
3.  **Automatisation** : Déploiement de systèmes RAG pour le support client

Souhaitez-vous que l'agence vous propose une solution premium adaptée à votre projet ?"""

    elif "prix" in user_message or "tarif" in user_message or "coût" in user_message:
        return """La qualité premium de KOMARA AGENCY garantit un résultat 8K et une image de marque qui convertit. 
L'investissement dans l'excellence est le seul chemin vers le succès.

Souhaitez-vous que l'agence vous transmette un devis personnalisé ?"""

    elif "portfolio" in user_message or "réalisation" in user_message:
        return """Les créations de l'agence incarnent le protocole "Luxury African". 
Chaque visuel est produit avec les standards Sony A7R V, palette Noir #000000 et Or #D4AF37, texture de peau réelle.

Souhaitez-vous découvrir le portfolio de l'agence ?"""

    elif "contact" in user_message or "whatsapp" in user_message:
        return """Pour confier votre projet à l'excellence, veuillez contacter l'agence.
L'équipe commerciale de KOMARA AGENCY vous répondra avec précision."""
    
    else:
        return f"""J'ai bien reçu votre demande.

En tant qu'assistant officiel de KOMARA AGENCY, ma mission est de vous orienter vers l'excellence. 
L'agence se spécialise dans les visuels 8K et l'automatisation intelligente qui convertit.

Quel est le projet pour lequel vous souhaitez l'expertise de l'agence ?"""


@bot.message_handler(commands=['start'])
def send_welcome(message):
    welcome_text = """Bonjour.

Je suis l'assistant officiel de KOMARA AGENCY. 
Je suis là pour :
🎨 Vous présenter les services premium de l'agence
💼 Vous présenter le portfolio et les réalisations de l'agence
💬 Répondre à vos questions avec expertise
📩 Vous mettre en contact avec l'équipe

Comment puis-je vous orienter aujourd'hui ?"""
    bot.reply_to(message, welcome_text)

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    reponse = generer_reponse(message.text)
    bot.reply_to(message, reponse)

print("Bot KOMARA AGENCY lancé avec Protocole 8K...")
bot.polling()
