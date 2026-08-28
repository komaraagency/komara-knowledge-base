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
IMG1 = "portfolio_01" # CORRIGÉ
IMG2 = "portfolio_02" # CORRIGÉ

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
                if "Message de bienvenue:" in contenu:
                    return contenu.split("Message de bienvenue:")[1].strip().split("\n")[0]
                return contenu[:3500]
        except: continue
    return None

def generer_reponse(user_id, user_message):
    msg = user_message.lower()

    # 0. CONVERSATION HUMAINE
    if any(x in msg for x in ["comment tu vas", "ca va", "salam"]):
        return "Wa alaykoum salam 🙏 Alhamdoulilah je vais bien. Et toi, prêt à scaler ton business avec l'IA?"
    if "merci" in msg:
        return "Je t'en prie 👑 Dis-moi, on part sur quel service pour commencer?"

    # 1. CERVEAU 1 : DIALOGUES/
    mapping_dialogues = {
        "bonjour": "01_acceuil.md", "salut": "01_acceuil.md", "start": "01_acceuil.md",
        "prix": "02_proposition.md", "tarif": "02_proposition.md", "service": "02_proposition.md", "💎": "02_proposition.md",
        "chatbot": "03_agents_ia.md", "agent": "03_agents_ia.md", "🤖": "03_agents_ia.md",
        "objection": "04_objections.md", "trop cher": "04_objections.md", "cher": "04_objections.md",
        "closing": "05_closing.md", "valider": "05_closing.md",
        "formation": "06_formation.md", "apprendre": "06_formation.md",
        "ugc": "08_ugc.md", "video": "08_ugc.md",
        "visuel": "09_visuels.md", "image": "09_visuels.md",
        "logo": "10_logo.md", "branding": "10_logo.md",
        "bonus": "11_bonus.md", "offre": "11_bonus.md",
        "faq": "12_faq_rapide.md", "question": "12_faq_rapide.md"
    }
    for mot, fichier in mapping_dialogues.items():
        if mot in msg:
            reponse = lire_fichier("dialogues", fichier)
            if reponse: return reponse

    # 2. CERVEAU 2 : DOCS/
    mapping_docs = {
        "portfolio": "Portfolio.md", "📂": "Portfolio.md", "realisation": "Portfolio.md",
        "commander": "process.md", "commande": "process.md", "🚀": "process.md",
        "paiement": "process.md", "regler": "process.md",
        "delai": "process.md", "livraison": "process.md",
        "style": "style_guide.md", "esthétique": "style_guide.md"
    }
    for mot, fichier in mapping_docs.items():
        if mot in msg:
            reponse = lire_fichier("docs", fichier)
            if reponse: return reponse

    # 3. MEMOIRE LOGO
    if user_memory.get(user_id) == "logo":
        user_memory.pop(user_id, None)
        return "Parfait. L'agence préconise *Luxury African* Noir & Or. On lance 3 concepts 8K pour toi maintenant?"
    if "logo" in msg:
        user_memory[user_id] = "logo"
        return lire_fichier("dialogues", "10_logo.md") or "Pour quel type d'activité souhaitez-vous le logo?"

    # 4. PAR DEFAUT VENDEUR
    return "Je t'ai bien compris 👑 Parmi nos 4 pôles : *Visuels*, *Branding*, *Chatbot*, *Formation*... lequel t'intéresse le plus pour scaler?"

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
            time.sleep(1.5)
            reponse = f"Un expert KOMARA vous contacte sur WhatsApp *{WHATSAPP}* sous 5 minutes."
        
        elif txt == "📂 Portfolio":
            time.sleep(2)
            try:
                if os.path.exists(IMG1): bot.send_photo(chat_id, open(IMG1, "rb"))
                if os.path.exists(IMG2): bot.send_photo(chat_id, open(IMG2, "rb"))
            except Exception as e: print(f"[ERREUR IMAGE] {e}")
            reponse = lire_fichier("docs", "Portfolio.md") or "Voici notre esthétique *Luxury African 8K*."
        
        else:
            reponse = generer_reponse(message.from_user.id, txt)
            temps_reflexion = min(4, max(1, len(reponse) / 200))
            time.sleep(temps_reflexion)
        
        bot.send_message(chat_id, reponse, reply_markup=menu_principal())
    
    except Exception as e:
        print(f"[CRASH] {e}")
        bot.send_message(message.chat.id, "Désolé maintenance. Expert KOMARA vous contacte sur WhatsApp 🙏", reply_markup=menu_principal())

def run_bot():
    while True:
        try:
            print("BOT KOMARA V9.3.1 LUXURY CONÇU ET LANCÉ")
            bot.delete_webhook(drop_pending_updates=True)
            time.sleep(2)
            bot.infinity_polling(timeout=20)
        except Exception as e:
            print(f"[REDEMARRAGE] {e}")
            time.sleep(5)

if __name__ == "__main__":
    run_bot()
