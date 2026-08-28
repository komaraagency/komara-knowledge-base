import os, time, json, telebot
from dotenv import load_dotenv
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TOKEN, parse_mode="Markdown")

with open("kb.json", "r", encoding="utf-8") as f:
    BRAIN = json.load(f)

WHATSAPP = BRAIN["contact"]["whatsapp"]

def menu():
    m = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    m.add("💎 Voir les Tarifs", "📂 Portfolio")
    m.add("🚀 Commander", "🤖 Chatbot IA")
    m.add("👑 Parler à un humain")
    return m

def chercher(msg):
    msg = msg.lower()
    for k in BRAIN["knowledge"]:
        if any(q in msg for q in k["questions"]):
            return k["answer"]
    return None

@bot.message_handler(commands=['start'])
def start(m):
    bot.send_chat_action(m.chat.id, 'typing') # FIX 3
    time.sleep(1)
    bot.send_message(m.chat.id, BRAIN["knowledge"][0]["answer"], reply_markup=menu())

@bot.message_handler(func=lambda m: True)
def handle(m):
    bot.send_chat_action(m.chat.id, 'typing') # FIX 3
    txt = m.text
    
    if txt in ["📂 Portfolio", "Portfolio"]:
        sent = 0
        for img in ["portfolio_01", "portfolio_02"]:
            try:
                if os.path.exists(img):
                    bot.send_photo(m.chat.id, open(img, "rb"))
                    sent += 1
                    time.sleep(0.5) # FIX 1
            except: pass
        txt_out = f"Portfolio KOMARA 💎 {BRAIN['slogan']}\nTu veux des exemples pour quel domaine?"
        if sent == 0: txt_out = "⚠️ Photos en upload. " + txt_out
        bot.send_message(m.chat.id, txt_out, reply_markup=menu())
    
    elif txt == "💎 Voir les Tarifs":
        packs = "\n".join([f"*{p['nom']}*: {p['prix']} - {p['contenu']}" for p in BRAIN["packs"]])
        rep = chercher('prix') + "\n\n" + packs
        bot.send_message(m.chat.id, rep, reply_markup=menu())
    
    elif txt == "👑 Parler à un humain":
        bot.send_message(m.chat.id, f"Expert KOMARA vous contacte sur *{WHATSAPP}* sous 5min 🙏", reply_markup=menu())
    
    else:
        rep = chercher(txt)
        if not rep: # FIX 2
            rep = "Parmi nos services : Agent IA, Site Web, Vidéo UGC... lequel t'intéresse?"
        time.sleep(min(2, len(rep) / 200)) # Délai naturel
        bot.send_message(m.chat.id, rep, reply_markup=menu())

def run():
    bot.remove_webhook()
    time.sleep(2)
    print(f"KOMARA V10.3 {BRAIN['brand']} LANCÉ")
    bot.infinity_polling()

if __name__ == "__main__": run()
