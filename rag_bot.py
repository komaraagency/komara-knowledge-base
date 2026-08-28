import os
import time
import telebot
from dotenv import load_dotenv
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TOKEN)
user_memory = {}

MESSAGE_MAINTENANCE = "Désolé nous sommes en maintenance. Un expert KOMARA vous recontacte sur WhatsApp +212701986219 🙏"

def menu_principal():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("💎 Voir les Tarifs"), KeyboardButton("📂 Portfolio"))
    markup.add(KeyboardButton("🚀 Commander"), KeyboardButton("🤖 Chatbot IA"))
    return markup

def lire_dialogue(nom_fichier):
    """LIT DANS dialogues/ en PRIORITE. Ne crash jamais."""
    chemin = f"dialogues/{nom_fichier}"
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            contenu = f.read().strip()
            # Si le fichier a "Réponse suggérée:" on prend la ligne d'après
            if "Réponse suggérée:" in contenu:
                lignes = contenu.split("\n")
                for i, line in enumerate(lignes):
                    if "Réponse suggérée:" in line or "Message de bienvenue:" in line:
                        return lignes[i+1].strip().replace('"', '')
            return contenu
    except Exception as e:
        print(f"[ERREUR LECTURE] {chemin}: {e}")
        return None

def generer_reponse(user_id, user_message):
    msg = user_message.lower()
    memory = user_memory.get(user_id, "")

    # 1. PRIORITE 1 : LIRE DANS LE DOSSIER DIALOGUES
    mapping_fichiers = {
        "bonjour": "01_accueil.md",
        "salut": "01_accueil.md",
        "start": "01_accueil.md",
        "prix": "02_proposition.md",
        "tarif": "02_proposition.md",
        "💎": "02_proposition.md",
        "chatbot": "03_agents_ia.md",
        "agent ia": "03_agents_ia.md",
        "🤖": "03_agents_ia.md",
        "commander": "04_livraison.md",
        "commande": "04_livraison.md",
        "🚀": "04_livraison.md",
        "paiement": "05_paiement.md",
        "regler": "05_paiement.md",
        "delai": "06_delai.md",
        "livraison": "06_delai.md",
        "portfolio": "07_portfolio.md",
        "📂": "07_portfolio.md"
    }
    
    for mot, fichier in mapping_fichiers.items():
        if mot in msg:
            reponse_fichier = lire_dialogue(fichier)
            if reponse_fichier:
                return reponse_fichier

    # 2. PRIORITE 2 : REPONSES DURES SI FICHIER MANQUANT
    if "signature" in msg or "esthétique" in msg:
        return "L'agence se distingue par une esthétique **Luxury African**. Palette Noir #000 et Or #D4AF37, précision 8K Sony A7R V."
    
    if "pourquoi" in msg and "choisir" in msg:
        return "Parce que l'agence conçoit des actifs digitaux conçus pour convertir. Fusion entre IA haute technicité et direction artistique rigoureuse."

    # 3. PRIORITE 3 : MEMOIRE LOGO
    if memory == "logo":
        user_memory.pop(user_id, None)
        return f"Parfait pour '{user_message}'. L'agence préconise une identité 'Luxury African' Noir & Or. On lance 3 concepts 8K pour toi ?"
    if "logo" in msg:
        user_memory[user_id] = "logo"
        return "L'agence prend note. Pour quel type d'activité souhaitez-vous le logo ?"

    # 4. PAR DEFAUT
    return "J'ai bien reçu votre demande. Quel service vous intéresse ? Choisissez ci-dessous 👇"

@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        accueil = lire_dialogue("01_accueil.md") or "Bienvenue chez KOMARA AGENCY"
        bot.send_message(message.chat.id, accueil, reply_markup=menu_principal())
    except Exception as e:
        print(f"[CRASH START] {e}")
        bot.send_message(message.chat.id, MESSAGE_MAINTENANCE, reply_markup=menu_principal())

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    try:
        reponse = generer_reponse(message.from_user.id, message.text)
        bot.send_message(message.chat.id, reponse, reply_markup=menu_principal())
    except Exception as e:
        print(f"[CRASH MESSAGE] {e}")
        bot.send_message(message.chat.id, MESSAGE_MAINTENANCE, reply_markup=menu_principal())

def run_bot():
    while True: # BOUCLE INFINIE ANTI-MORT
        try:
            print("Bot KOMARA AGENCY V7.0 OFFICIEL lancé...")
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            print(f"[CRASH TOTAL] {e}. Redémarrage dans 5 secondes...")
            time.sleep(5)

if __name__ == "__main__":
    run_bot()
