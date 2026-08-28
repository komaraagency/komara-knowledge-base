import os
import telebot
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TOKEN)

# 1. On charge la base de connaissance
with open("knowledge.txt", "r", encoding="utf-8") as f:
    KNOWLEDGE = f.read()

# 2. FAQ - Questions fréquentes avec réponses premium
FAQ = {
    "prix": "La qualité premium de KOMARA AGENCY garantit un résultat 8K et une image de marque qui convertit. L'investissement dans l'excellence est le seul chemin vers le succès. Souhaitez-vous un devis personnalisé ?",
    "tarif": "La qualité premium de KOMARA AGENCY garantit un résultat 8K et une image de marque qui convertit. L'investissement dans l'excellence est le seul chemin vers le succès. Souhaitez-vous un devis personnalisé ?",
    "devis": "La qualité premium de KOMARA AGENCY garantit un résultat 8K et une image de marque qui convertit. L'investissement dans l'excellence est le seul chemin vers le succès. Souhaitez-vous un devis personnalisé ?",
    "delai": "L'agence KOMARA AGENCY préconise un délai de 7 à 14 jours ouvrés pour garantir la qualité 8K et le respect du Protocole Luxury African. La précipitation est l'ennemie de l'excellence.",
    "service": "L'agence KOMARA AGENCY élève les standards du marché digital en Afrique. Nous préconisons : 1. Branding Noir & Or  2. Création Digitale 8K  3. Automatisation RAG 4. Retouche Professionnelle",
    "portfolio": "Les créations de l'agence incarnent le protocole 'Luxury African'. Chaque visuel est produit avec les standards Sony A7R V, palette Noir #000 et Or #D4AF37. Souhaitez-vous recevoir le portfolio ?",
    "contact": "Pour confier votre projet à l'excellence, veuillez contacter l'équipe commerciale de KOMARA AGENCY. L'agence vous répondra avec précision.",
    "qui": "Je suis l'assistant officiel de KOMARA AGENCY. Je suis l'extension digitale de l'agence, conçue pour orienter la clientèle vers l'excellence visuelle 'Luxury African'."
}

def chercher_dans_knowledge(question):
    """Si pas dans FAQ, on cherche des mots clés dans knowledge.txt"""
    question = question.lower()
    if "8k" in question or "qualité" in question:
        return "L'agence applique rigoureusement le Protocole 8K : Sony A7R V, palette Noir #000 et Or #D4AF37, texture de peau réelle. Zéro rendu médiocre n'est toléré."
    if "noir" in question or "or" in question or "esthétique" in question:
        return "L'esthétique de l'agence est 'Luxury African'. Palette officielle : Noir profond #000000 et Or prestige #D4AF37. Chaque création renforce le prestige de la marque."
    if "automatisation" in question or "rag" in question or "bot" in question:
        return "L'agence déploie des systèmes d'automatisation intelligente RAG pour le support client. L'objectif : convertir et sublimer l'expérience client."
    if "branding" in question or "identité" in question:
        return "Le pôle Branding de l'agence crée des identités visuelles impactantes. La mission : élever les standards du marché digital en Afrique."
    
    # Réponse par défaut si rien trouvé
    return "J'ai bien reçu votre demande. En tant qu'assistant officiel de KOMARA AGENCY, ma mission est de vous orienter vers l'excellence. Quel est le projet pour lequel vous souhaitez l'expertise de l'agence ?"

def generer_reponse(user_message):
    user_message_lower = user_message.lower()

    # ÉTAPE 1 : Vérifier FAQ
    for mot_cle, reponse in FAQ.items():
        if mot_cle in user_message_lower:
            return reponse
    
    # ÉTAPE 2 : Si pas dans FAQ, chercher dans knowledge
    return chercher_dans_knowledge(user_message)


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

print("Bot KOMARA AGENCY V2 lancé : FAQ + Recherche...")
bot.polling()
