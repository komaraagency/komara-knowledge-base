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
WHATSAPP = "+212701986219"

# IMAGES PORTFOLIO
IMG1 = "wa_image_7917004363912705776"
IMG2 = "wa_image_427715489939713356"

def menu_principal():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("💎 Voir les Tarifs"), KeyboardButton("📂 Portfolio"))
    markup.add(KeyboardButton("🚀 Commander"), KeyboardButton("🤖 Chatbot IA"))
    markup.add(KeyboardButton("👑 Parler à un humain"))
    return markup

def lire_fichier(dossier, nom_fichier):
    """LIT DANS dialogues/ PUIS docs/. Ne crash jamais."""
    chemins = [f"{dossier}/{nom_fichier}", f"{dossier}/{nom_fichier.replace('accueil','acceuil')}"]
    for chemin in chemins:
        try:
            with open(chemin, "r", encoding="utf-8") as f:
                contenu = f.read().strip()
                if "Réponse suggérée:" in contenu:
                    lignes = contenu.split("\n")
                    for i, line in enumerate(lignes):
                        if "Réponse suggérée:" in line or "Message de bienvenue:" in line:
                            if i+1 < len(lignes):
                                return lignes[i+1].strip().replace('"', '')
                return contenu[:3500]
        except:
            continue
    return None

def generer_reponse(user_id, user_message):
    msg = user_message.lower()
    memory = user_memory.get(user_id, "")

    # 1. PRIORITE 1 : DIALOGUES/
    mapping_dialogues = {
        "bonjour": "01_acceuil.md", "salut": "01_acceuil.md", "start": "01_acceuil.md",
        "prix": "02_proposition.md", "tarif": "02_proposition.md", "💎": "02_proposition.md",
        "chatbot": "03_agents_ia.md", "🤖": "03_agents_ia.md",
        "objection": "04_objections.md", "trop cher": "04_objections.md",
        "logo": "10_logo.md", "faq": "12_faq_rapide.md"
    }
    for mot, fichier in mapping_dialogues.items():
        if mot in msg:
            reponse = lire_fichier("dialogues", fichier)
            if reponse: return reponse

    # 2. PRIORITE 2 : DOCS/
    mapping_docs = {
        "portfolio": "Portfolio.md", "📂": "Portfolio.md",
        "commander": "process.md", "🚀": "process.md",
        "paiement": "process.md", "delai": "process.md", "livraison": "process.md",
        "style": "style_guide.md", "esthétique": "style_guide.md",
        "aide": "faq.md"
    }
    for mot, fichier in mapping_docs.items():
        if mot in msg:
            reponse = lire_fichier("docs", fichier)
            if reponse: return reponse

    # 3. MEMOIRE LOGO
    if memory == "logo":
        user_memory.pop(user_id, None)
        return "Parfait. L'agence préconise 'Luxury African' Noir & Or. On lance 3 concepts 8K pour toi ?"
    if "logo" in msg:
        user_memory[user_id] = "logo"
        return lire_fichier("dialogues", "10_logo.md") or "Pour quel type d'activité souhaitez-vous le logo ?"

    return "J'ai bien reçu votre demande. Quel service vous intéresse ? Choisissez ci-dessous 👇"

@bot.message_handler(commands=['start'])
def send_welcome(message):
    try:
        bot.send_chat_action(message.chat.id, 'typing') # EFFET ...
        time.sleep(1) # 1 sec pour l'accueil
        accueil = lire_fichier("dialogues", "01_acceuil.md") or "Bienvenue chez KOMARA AGENCY"
        bot.send_message(message.chat.id, accueil, reply_markup=menu_principal())
    except Exception as e:
        print(f"[CRASH START] {e}")
        bot.send_message(message.chat.id, MESSAGE_MAINTENANCE, reply_markup=menu_principal())

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    try:
        txt = message.text
        chat_id = message.chat.id

        # EFFET REFLEXION 4S MAX
        bot.send_chat_action(chat_id, 'typing')
        
        if txt == "👑 Parler à un humain":
            time.sleep(1.5)
            reponse = f"Un expert KOMARA vous contacte sur WhatsApp {WHATSAPP} sous 5 minutes."
        
        elif txt == "📂 Portfolio":
            time.sleep(2) # 2 sec pour charger les images
            try:
                if os.path.exists(IMG1): bot.send_photo(chat_id, open(IMG1, "rb"))
                if os.path.exists(IMG2): bot.send_photo(chat_id, open(IMG2, "rb"))
            except Exception as e: print(f"[ERREUR IMAGE] {e}")
            reponse = lire_fichier("docs", "Portfolio.md") or "Voici notre esthétique Luxury African 8K."
        
        else:
            # Calcule le temps de réponse en fonction de la longueur
            reponse = generer_reponse(message.from_user.id, txt)
            temps_reflexion = min(4, max(1, len(reponse) / 200)) # max 4 sec
            time.sleep(temps_reflexion)
        
        bot.send_message(chat_id, reponse, reply_markup=menu_principal())
    
    except Exception as e:
        print(f"[CRASH MESSAGE] {e}")
        bot.send_message(message.chat.id, MESSAGE_MAINTENANCE, reply_markup=menu_principal())

def run_bot():
    while True:
        try:
            print("Bot KOMARA AGENCY V8.1 OFFICIEL lancé...")
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            print(f"[CRASH TOTAL] {e}. Redémarrage dans 5 secondes...")
            time.sleep(5)

if __name__ == "__main__":
    run_bot()
