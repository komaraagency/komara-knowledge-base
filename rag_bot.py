import os
import telebot
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TOKEN)
user_memory = {}

def charger_dialogue(nom_fichier):
    try:
        with open(f"dialogues/{nom_fichier}", "r", encoding="utf-8") as f:
            lines = f.readlines()
            for i, line in enumerate(lines):
                if "Réponse suggérée:" in line or "Message de bienvenue:" in line:
                    return lines[i+1].strip().replace('"', '')
            return f.read()
    except:
        return ""

def generer_reponse(user_id, user_message):
    msg = user_message.lower()
    memory = user_memory.get(user_id, "")

    # 1. DIALOGUES PRIORITAIRES
    if "bonjour" in msg or "salut" in msg or "start" in msg:
        return charger_dialogue("01_accueil.md")
    
    # 2. TARIFS & COMMANDE - Ligne 24 à 30 de ta FAQ
    if "prix" in msg or "tarif" in msg or "coût" in msg:
        return """### **TARIFS & COMMANDE KOMARA AGENCY**
**Visuels:** 300 MAD / 5 images
**Vidéos:** 600 MAD / 3 vidéos  
**Pack Starter:** 1200 MAD

Je t'envoie le catalogue complet sur WhatsApp ? +212701986219"""

    # 3. COMMENT COMMANDER - Ligne 32 à 37
    if "commander" in msg or "commande" in msg:
        return """**COMMENT COMMANDER ?**
1. Écris-nous sur WhatsApp +212701986219
2. On fait ton devis gratuit sous 2h
3. Paiement 50% puis livraison 24-48h

Tu veux qu'on commence par quoi ?"""

    # 4. MOYENS DE PAIEMENT - Ligne 39 à 41
    if "paiement" in msg or "regler" in msg:
        return """**MOYENS DE PAIEMENT**
On accepte : Orange Money, MoMo, Virement, Paypal, Carte.
50% à la commande, 50% à la livraison."""

    # 5. DÉLAI DE LIVRAISON - Ligne 43 à 46
    if "delai" in msg or "livraison" in msg or "temps" in msg:
        return """**DÉLAI DE LIVRAISON**
Très rapide ⚡
**Visuel :** 24h
**Vidéo :** 48h
**Logo :** 48h"""

    # 6. CHATBOT WHATSAPP - Ligne 48 à 50
    if "chatbot" in msg or "agent ia" in msg or "whatsapp" in msg:
        return """**AGENTS IA WHATSAPP KOMARA**
Oui ! On crée des Agents IA WhatsApp à 2000 MAD.
Il répond seul à tes clients, prend les commandes et envoie le catalogue 24h/24.
Tu veux voir un exemple ?"""

    # 7. MÉMOIRE LOGO
    if memory == "logo":
        user_memory.pop(user_id)
        return f"Parfait pour '{user_message}'. L'agence préconise une identité 'Luxury African' Noir & Or. On lance 3 concepts 8K pour toi ?"
    if "logo" in msg:
        user_memory[user_id] = "logo"
        return "L'agence prend note. Pour quel type d'activité souhaitez-vous le logo ?"

    # 8. FAQ PREMIUM - Ligne 56 à 76
    if "signature" in msg or "esthétique" in msg:
        return "L'agence se distingue par une esthétique **Luxury African**. Palette Noir profond #000 et Or prestige #D4AF37, précision technique 8K Sony A7R V 85mm pour un impact visuel maximal."
    
    if "pourquoi" in msg and "choisir" in msg:
        return "Parce que l'agence conçoit des actifs digitaux conçus pour convertir. Fusion entre IA haute technicité et direction artistique rigoureuse pour vous démarquer de la concurrence."

    return "J'ai bien reçu votre demande. En tant qu'assistant officiel de KOMARA AGENCY, ma mission est de vous orienter vers l'excellence. Quel service vous intéresse ?"

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, charger_dialogue("01_accueil.md"))

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    reponse = generer_reponse(message.from_user.id, message.text)
    bot.reply_to(message, reponse)

print("Bot KOMARA AGENCY V5 OFFICIEL lancé...")
bot.polling()
