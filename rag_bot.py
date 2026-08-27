import json
import difflib

class KomaraBot:
    def __init__(self, kb_path='kb.json'):
        with open(kb_path, 'r', encoding='utf-8') as f:
            self.kb = json.load(f)
        self.brand = self.kb['brand']
        self.contact = self.kb['contact']['whatsapp']

    def trouver_meilleure_reponse(self, message):
        message = message.lower().strip()
        meilleures_questions = []
        
        for item in self.kb['knowledge']:
            for question in item['questions']:
                ratio = difflib.SequenceMatcher(None, message, question).ratio()
                if ratio > 0.5 or question in message:
                    meilleures_questions.append((ratio, item['answer']))
        
        if meilleures_questions:
            meilleures_questions.sort(reverse=True)
            return meilleures_questions[0][1]
        
        return f"Je n'ai pas bien compris. Tu veux que je t'envoie des exemples sur WhatsApp? {self.contact}"

    def repondre(self, message):
        reponse = self.trouver_meilleure_reponse(message)
        return {
            "brand": self.brand,
            "slogan": self.kb['slogan'],
            "answer": reponse,
            "contact": self.contact
          }
