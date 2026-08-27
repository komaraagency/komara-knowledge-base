from flask import Flask, request, jsonify
from rag_bot import KomaraBot
import os

app = Flask(__name__)
bot = KomaraBot()

@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "status": "Komara Agency Bot OK",
        "version": bot.kb['version'],
        "brand": bot.brand
    })

@app.route('/chat', methods=['POST'])
def chat():
    try:
        data = request.json
        user_message = data.get('message', '')
        
        if not user_message:
            return jsonify({"error": "Le champ 'message' est requis"}), 400
            
        reponse = bot.repondre(user_message)
        return jsonify(reponse)
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
