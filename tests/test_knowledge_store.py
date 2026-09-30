"""Durable learning and the actual script startup, without Telegram network calls."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import knowledge_store as store


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='komara-store-test-')
        self.addCleanup(self.directory.cleanup)
        self.env = patch.dict(os.environ, {'ACTIONS_DIR': self.directory.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.reset_store()
        self.resources = {'fr': {'kb': [], 'faq': [], 'dialogues': []},
                          'en': {'kb': [], 'faq': [], 'dialogues': []}}
        store.initialize_resources(copy.deepcopy(self.resources),
                                   lambda lang: copy.deepcopy(self.resources[lang]))
        self.question = 'livraison zephyrville mardi'
        self.answer = 'UNIQUE_LEARNED_4829'
        self.path = Path(self.directory.name) / 'kb_custom.json'

    def reset_store(self):
        store.LANG_RESOURCES.clear()
        store._CUSTOM_ROWS = []
        store._INITIALIZED = False
        store._REFRESH_LOADER = None

    def test_learning_is_persisted_and_immediately_visible(self):
        result = store.learn_entry(self.question, self.answer)
        self.assertTrue(result['added'])
        self.assertEqual(store.LANG_RESOURCES['fr']['kb'][0]['answer'], self.answer)
        self.assertEqual(json.loads(self.path.read_text())[0]['answer'], self.answer)

    def test_restart_reloads_durable_learning(self):
        store.learn_entry(self.question, self.answer)
        self.reset_store()
        store.initialize_resources(copy.deepcopy(self.resources),
                                   lambda lang: copy.deepcopy(self.resources[lang]))
        self.assertEqual(store.LANG_RESOURCES['fr']['kb'][0]['answer'], self.answer)

    def test_write_failure_does_not_publish_runtime_entry(self):
        with patch.object(store, '_atomic_write', side_effect=PermissionError('test')):
            with self.assertRaises(PermissionError):
                store.learn_entry(self.question, self.answer)
        self.assertEqual(store.LANG_RESOURCES['fr']['kb'], [])
        self.assertFalse(self.path.exists())

    def test_corrupt_file_is_never_overwritten(self):
        self.path.write_text('{broken JSON')
        with self.assertRaises(json.JSONDecodeError):
            store.learn_entry(self.question, self.answer)
        self.assertEqual(self.path.read_text(), '{broken JSON')
        self.assertEqual(store.LANG_RESOURCES['fr']['kb'], [])

    def test_invalid_file_schema_is_preserved(self):
        self.path.write_text('{"keep": "original"}')
        with self.assertRaises(ValueError):
            store.learn_entry(self.question, self.answer)
        self.assertEqual(self.path.read_text(), '{"keep": "original"}')

    def test_atomic_replace_failure_preserves_existing_file(self):
        store.learn_entry(self.question, self.answer)
        before = self.path.read_bytes()
        with patch.object(store.os, 'replace', side_effect=PermissionError('test')):
            with self.assertRaises(PermissionError):
                store.learn_entry('astronomie quasar nebuleuse', 'OTHER')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(store.LANG_RESOURCES['fr']['kb']), 1)
        self.assertEqual(list(self.path.parent.glob('.kb_custom_*.tmp')), [])

    def test_invalid_language_metadata_does_not_crash_startup(self):
        self.path.write_text('[{"question":"hello","answer":"world","lang":[]}]')
        self.reset_store()
        with self.assertLogs('komara.knowledge', level='ERROR'):
            store.initialize_resources(copy.deepcopy(self.resources), lambda _: {})
        self.assertEqual(store.LANG_RESOURCES['fr']['kb'], [])
        self.assertTrue(self.path.exists())

    def test_duplicate_does_not_change_file_or_runtime(self):
        store.learn_entry(self.question, self.answer)
        before = self.path.read_bytes()
        result = store.learn_entry(self.question, 'OTHER')
        self.assertFalse(result['added'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(store.LANG_RESOURCES['fr']['kb']), 1)

    def test_parallel_duplicate_requests_add_only_once(self):
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda _: store.learn_entry(self.question, self.answer), range(10)))
        self.assertEqual(sum(result['added'] for result in results), 1)
        self.assertEqual(len(json.loads(self.path.read_text())), 1)

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

    def test_actual_script_globals_and_admin_share_learning(self):
        harness = r'''
import ast, json, os, sys
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
source=ast.parse((root/'rag_bot.py').read_text())
source.body=[node for node in source.body if not (isinstance(node,ast.If) and ast.unparse(node.test)=="__name__ == '__main__'")]
active={'__name__':'__main__','__file__':str(root/'rag_bot.py')}
exec(compile(source,str(root/'rag_bot.py'),'exec'),active)
import actions
sent=[]
class FakeBot:
    def send_message(self,chat,text,**kwargs):sent.append(text)
q='livraison zephyrville mardi';a='UNIQUE_LEARNED_4829'
actions._admin_apprends(FakeBot(),99999,q+' || '+a,'fr')
assert any('✅ Connaissance ajoutée' in text for text in sent),sent
assert 'rag_bot' not in sys.modules,'Admin must not reimport the Telegram entrypoint'
assert active['trouver_meilleure_reponse_multilingue'](q,'fr')==a
import rag_bot
assert active['LANG_RESOURCES'] is rag_bot.LANG_RESOURCES
assert active['trouver_meilleure_reponse_multilingue'](q,'fr')==a
assert json.loads((Path(os.environ['ACTIONS_DIR'])/'kb_custom.json').read_text())[0]['answer']==a
# A second independent question fails to save: no false success and no runtime publication.
import knowledge_store
from unittest.mock import patch
sent.clear()
with patch.object(knowledge_store,'_atomic_write',side_effect=PermissionError('test')):
    actions._admin_apprends(FakeBot(),99999,'astronomie quasar nebuleuse || NOT_SAVED','fr')
assert sent and not any('✅' in text for text in sent),sent
assert active['trouver_meilleure_reponse_multilingue']('astronomie quasar nebuleuse','fr') is None
print('SCRIPT_STARTUP_AND_SAVE_FAILURE_OK')
'''
        env = {key: value for key, value in os.environ.items()
               if key in ('PATH', 'HOME', 'LANG', 'LC_ALL')}
        with tempfile.TemporaryDirectory(prefix='komara-startup-test-') as directory:
            env.update(TELEGRAM_TOKEN='123:TEST', ADMIN_CHAT_ID='99999',
                       ACTIONS_DIR=directory, MEMORY_DIR=directory, LOG_LEVEL='CRITICAL')
            result = subprocess.run([sys.executable, '-c', harness, str(ROOT)],
                                    env=env, cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('SCRIPT_STARTUP_AND_SAVE_FAILURE_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
