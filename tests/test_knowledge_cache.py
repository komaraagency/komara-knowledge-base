"""Regression coverage for independent corpora and content-aware cache keys."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import local_search

class KnowledgeCacheTests(unittest.TestCase):
    def setUp(self):
        local_search._RESOURCE_CACHE.clear()

    def search(self, entries, message='livraison zephyrville mardi', faq=None, dialogues=None):
        return local_search.trouver_meilleure_reponse(message, entries, faq or [], dialogues or [])

    def test_equal_sized_bases_have_independent_answers(self):
        a = [{'questions': ['livraison zephyrville mardi'], 'answer': 'ANSWER_A'}]
        b = [{'questions': ['livraison zephyrville mardi'], 'answer': 'ANSWER_B'}]
        self.assertEqual(self.search(a), 'ANSWER_A')
        self.assertEqual(self.search(b), 'ANSWER_B')

    def test_answer_edit_without_length_change(self):
        entries = [{'questions': ['livraison zephyrville mardi'], 'answer': 'BEFORE'}]
        self.assertEqual(self.search(entries), 'BEFORE')
        entries[0]['answer'] = 'AFTER'
        self.assertEqual(self.search(entries), 'AFTER')

    def test_question_edit_without_length_change(self):
        entries = [{'questions': ['livraison zephyrville mardi'], 'answer': 'MATCH'}]
        self.assertEqual(self.search(entries), 'MATCH')
        entries[0]['questions'] = ['astronomie constellation orion']
        self.assertIsNone(self.search(entries))

    def test_equal_sized_language_faqs_stay_isolated(self):
        first = [{'question': 'livraison zephyrville mardi', 'answer': 'FAQ_A'}]
        second = [{'question': 'livraison zephyrville mardi', 'answer': 'FAQ_B'}]
        self.assertEqual(self.search([], faq=first), 'FAQ_A')
        self.assertEqual(self.search([], faq=second), 'FAQ_B')

    def test_cache_is_bounded(self):
        for index in range(40):
            self.search([{'questions': ['livraison zephyrville mardi'], 'answer': str(index)}])
        self.assertLessEqual(len(local_search._RESOURCE_CACHE), 12)

if __name__ == '__main__':
    unittest.main()
