"""Durable learning via Google Sheets (memory_sheets) — RÈGLE BOSS 02/10 :
zéro fichier local, zéro GitHub, zéro disque Railway. Un faux backend
memory_sheets simule le tableur dédié « Komara Bot - Mémoire »."""
import copy
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import knowledge_store as store
import memory_sheets


class FakeSheets:
    """Backend en RAM : simule le tableur Google (append-only)."""
    def __init__(self):
        self.dialogues = []
        self.failing = False
        self.calls = 0

    # patch points : save_learned / load_learned appelés par le store
    def save_learned(self, question, answer, lang):
        self.calls += 1
        if self.failing:
            raise RuntimeError("Google non lié (test)")
        self.dialogues.append({"question": question, "answer": answer,
                               "lang": lang, "date": f"t{self.calls}"})

    def load_learned(self):
        # dédoublonne comme le vrai module : la version la plus récente gagne
        learned = {}
        for row in self.dialogues:
            learned[row["question"].strip().casefold()] = row
        return list(learned.values())


class SheetsStoreTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSheets()
        self._save = patch.object(memory_sheets, 'save_learned', self.fake.save_learned)
        self._load = patch.object(memory_sheets, 'load_learned', self.fake.load_learned)
        self._save.start(); self._load.start()
        self.addCleanup(self._save.stop); self.addCleanup(self._load.stop)
        self.reset_store()
        self.resources = {'fr': {'kb': [], 'faq': [], 'dialogues': []},
                          'en': {'kb': [], 'faq': [], 'dialogues': []}}
        store.initialize_resources(copy.deepcopy(self.resources),
                                   lambda lang: copy.deepcopy(self.resources[lang]))
        self.question = 'livraison zephyrville mardi'
        self.answer = 'UNIQUE_LEARNED_4829'

    def reset_store(self):
        store.LANG_RESOURCES.clear()
        store._CUSTOM_ROWS = []
        store._INITIALIZED = False
        store._REFRESH_LOADER = None

    def test_learning_goes_to_sheets_and_runtime(self):
        result = store.learn_entry(self.question, self.answer)
        self.assertTrue(result['added'])
        self.assertEqual(len(self.fake.dialogues), 1)
        self.assertEqual(self.fake.dialogues[0]['answer'], self.answer)
        self.assertEqual(store.LANG_RESOURCES['fr']['kb'][0]['answer'], self.answer)

    def test_restart_reloads_from_sheets(self):
        store.learn_entry(self.question, self.answer)
        self.reset_store()
        store.initialize_resources(copy.deepcopy(self.resources),
                                   lambda lang: copy.deepcopy(self.resources[lang]))
        self.assertEqual(store.LANG_RESOURCES['fr']['kb'][0]['answer'], self.answer)

    def test_sheets_failure_publishes_nothing(self):
        self.fake.failing = True
        with self.assertRaises(RuntimeError):
            store.learn_entry(self.question, self.answer)
        self.assertEqual(store.LANG_RESOURCES['fr']['kb'], [])
        self.assertEqual(store._CUSTOM_ROWS, [])

    def test_duplicate_updates_answer_no_dupe_rows(self):
        store.learn_entry(self.question, self.answer)
        result = store.learn_entry(self.question, 'OTHER')
        self.assertTrue(result['updated'])
        self.assertEqual(len(store._CUSTOM_ROWS), 1)
        self.assertEqual(store._CUSTOM_ROWS[0]['answer'], 'OTHER')
        self.assertEqual(len(store.LANG_RESOURCES['fr']['kb']), 1)
        self.assertEqual(store.LANG_RESOURCES['fr']['kb'][0]['answer'], 'OTHER')

    def test_parallel_duplicates_one_row(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(
                lambda _: store.learn_entry(self.question, self.answer), range(10)))
        # jamais de doublon en RAM, quel que soit l'entrelacement des append
        self.assertEqual(len(store._CUSTOM_ROWS), 1)
        self.assertTrue(all(r['added'] for r in results))

    def test_refresh_preserves_durable_entries(self):
        store.learn_entry(self.question, self.answer)
        self.assertTrue(store.refresh_resources('fr'))
        self.assertEqual(len(store.LANG_RESOURCES['fr']['kb']), 1)
        self.assertEqual(store.LANG_RESOURCES['fr']['kb'][0]['answer'], self.answer)

    def test_reinitializing_does_not_replace_active_state(self):
        shared = store.LANG_RESOURCES
        store.learn_entry(self.question, self.answer)
        returned = store.initialize_resources(copy.deepcopy(self.resources), lambda _: {})
        self.assertIs(returned, shared)
        self.assertEqual(returned['fr']['kb'][0]['answer'], self.answer)

    def test_batch_import_single_append(self):
        entries = [(f'question {i}', f'reponse {i}') for i in range(5)]
        with patch.object(memory_sheets, 'get_memory_sheet_id', return_value='SID'), \
             patch.object(memory_sheets, 'append_rows',
                          side_effect=lambda tab, rows: self.fake.dialogues.extend(
                              {'question': r[2], 'answer': r[3], 'lang': r[1], 'date': r[0]}
                              for r in rows) or True), \
             patch.object(memory_sheets, '_now', return_value='t0'):
            report = store.learn_entries_batch(entries, 'fr')
        self.assertEqual(report['added'], 5)
        self.assertEqual(len(store._CUSTOM_ROWS), 5)
        self.assertEqual(len(store.LANG_RESOURCES['fr']['kb']), 5)

    def test_batch_without_google_raises(self):
        with patch.object(memory_sheets, 'get_memory_sheet_id', return_value=''):
            with self.assertRaises(RuntimeError):
                store.learn_entries_batch([('q', 'a')], 'fr')
        self.assertEqual(store._CUSTOM_ROWS, [])

    def test_no_local_files_created(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            store.learn_entry(self.question, self.answer, directory=d)
            self.assertEqual(list(Path(d).iterdir()), [])  # AUCUN kb_custom.json


class MemorySheetsOfflineTests(unittest.TestCase):
    """Sans compte Google lié : rien n'est écrit, tout échoue proprement."""
    def test_append_rows_false_without_link(self):
        self.assertFalse(memory_sheets.append_rows("Dialogues", [["a", "b"]]))

    def test_read_rows_empty_without_link(self):
        self.assertEqual(memory_sheets.read_rows("Dialogues"), [])

    def test_save_learned_raises_without_link(self):
        with self.assertRaises(RuntimeError):
            memory_sheets.save_learned('q', 'a', 'fr')

    def test_load_learned_empty_without_link(self):
        self.assertEqual(memory_sheets.load_learned(), [])


class EmptyBaseStartupTests(unittest.TestCase):
    """RÈGLE BOSS (02/10) : le corpus a été retiré du repo. Le bot démarre
    avec une base VIDE sans crasher (kb.json, dialogues, faq absents)."""
    def test_rag_bot_boots_with_empty_base(self):
        import subprocess, os
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'LANG')}
        env.update(TELEGRAM_TOKEN='123:TEST', ADMIN_CHAT_ID='99999',
                   MEMORY_DIR=str(Path(tempfile.mkdtemp())))
        code = ("import sys, json; sys.path.insert(0, '');\n"
                "import rag_bot\n"
                "fr = rag_bot.LANG_RESOURCES['fr']\n"
                "assert fr['kb'] == [], fr['kb'][:2]\n"
                "assert fr['faq'] == [], fr['faq'][:2]\n"
                "assert fr['dialogues'] == [], fr['dialogues'][:2]\n"
                "assert not (rag_bot.BASE_DIR / 'kb.json').exists()\n"
                "assert not (rag_bot.BASE_DIR / 'dialogues').exists()\n"
                "print('EMPTY_BASE_OK')")
        result = subprocess.run([sys.executable, '-c', code], env=env,
                                cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertIn('EMPTY_BASE_OK', result.stdout,
                      f"stdout={result.stdout[-500:]} stderr={result.stderr[-500:]}")


import tempfile  # noqa: E402  (utilisé par EmptyBaseStartupTests)

if __name__ == '__main__':
    unittest.main(verbosity=2)
