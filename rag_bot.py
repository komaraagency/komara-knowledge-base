import os
import time
import telebot
from dotenv import load_dotenv
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")
user_memory = {}

WHATSAPP = "+212701986219"
IMG1 = "portfolio_01"
IMG2 = "portfolio_02"

def menu_principal():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(KeyboardButton("💎 Voir les Tarifs"), KeyboardButton("📂 Portfolio"))
    markup.add(KeyboardButton("🚀 Commander"), KeyboardButton("🤖 Chatbot IA"))
    markup.add(KeyboardButton("👑 Parler à un humain"))
    return markup

def lire_fichier(dossier, nom_fichier):
    chemins = [f"{dossier}/{nom_fichier}", f"{dossier}/{nom_fichier.replace('accueil','acceuil')}"]
    for chemin in chemins:
        try:
            with open(chemin, "r", encoding="utf-8") as f:
                contenu = f.read().strip()
                if "Réponse suggérée:" in contenu:
                    return contenu.split("Réponse suggérée:")[1].strip().split("\n")[0]
                return contenu[:3500]
        except: continue
    return None

def generer_reponse(user_id, user_message):
    msg = user_message.lower()
    if any(x in msg for x in ["comment tu vas", "ca va", "salam"]):
        return "Wa alaykoum salam 🙏 Alhamdoulilah je vais bien. Et toi, prêt à scaler?"
    if "merci" in msg:
        return "Je t'en prie 👑 Dis-moi, on part sur quel service?"

    mapping_dialogues = {
        "bonjour": "01_acceuil.md", "salut": "01_acceuil.md", "start": "01_acceuil.md",
        "prix": "02_proposition.md", "tarif": "02_proposition.md", "💎": "02_proposition.md",
        "chatbot": "03_agents_ia.md", "🤖": "03_agents_ia.md",
        "logo": "10_logo.md", "branding": "10_logo.md"
    }
    for mot, fichier in mapping_dialogues.items():
        if mot in msg:
            reponse = lire_fichier("dialogues", fichier)
            if reponse: return reponse

    mapping_docs = {
        "commander": "process.md", "🚀": "process.md",
        "style": "style_guide.md"
    }
    for mot, fichier in mapping_docs.items():
        if mot in msg:
            reponse = lire_fichier("docs", fichier)
            if reponse: return reponse

    return "Parmi nos 4 pôles : *Visuels*, *Branding*, *Chatbot*, *Formation*... lequel t'intéresse?"

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.send_chat_action(message.chat.id, 'typing')
    time.sleep(1)
    accueil = lire_fichier("dialogues", "01_acceuil.md") or "Bienvenue chez KOMARA AGENCY"
    bot.send_message(message.chat.id, accueil, reply_markup=menu_principal())

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    try:
        txt = message.text
        chat_id = message.chat.id
        bot.send_chat_action(chat_id, 'typing')
        
        if txt == "👑 Parler à un humain":
            reponse = f"Un expert KOMARA vous contacte sur WhatsApp *{WHATSAPP}* sous 5 minutes."
        
        elif txt == "📂 Portfolio" or txt == "Portfolio":
            time.sleep(2)
            sent = 0
            for img in [IMG1, IMG2]:
                try:
                    if os.path.exists(img): 
                        bot.send_photo(chat_id, open(img, "rb"))
                        sent += 1
                except: pass
            reponse = "Portfolio KOMARA 💎 Visuels IA 8K style Luxury African. Tu veux des exemples pour quel domaine?"
            if sent == 0: reponse = "⚠️ Photos en cours. " + reponse
        
        else:
            reponse = generer_reponse(message.from_user.id, txt)
            time.sleep(min(3, len(reponse) / 200))
        
        bot.send_message(chat_id, reponse, reply_markup=menu_principal())
    
    except Exception as e:
        print(f"[CRASH] {e}")
        bot.send_message(message.chat.id, "Maintenance. Expert KOMARA vous contacte 🙏", reply_markup=menu_principal())

def run_bot():
    bot.remove_webhook()
    time.sleep(2)
    print("BOT KOMARA V9.5 LANCÉ")
    bot.infinity_polling()

if __name__ == "__main__":
    run_bot()
