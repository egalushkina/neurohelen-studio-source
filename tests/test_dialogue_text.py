import unittest
from cartoonbot.dialogue_text import clean, normalize_scene
class DialogueTextTests(unittest.TestCase):
 def test_dash_punctuation_removed_without_changing_spelling(self):
  self.assertEqual(clean('Кот: «Это — кто-то!»\n— Да – я. - Привет.'),'Кот: «Это кто-то!»\nДа я. Привет.')
 def test_embedded_dialogue_updated_but_timeline_unchanged(self):
  scene={'dialogue':'Кот: «Я — тут»','video_prompt':'0–10s. Кот: «Я — тут»'}
  normalize_scene(scene)
  self.assertEqual(scene['video_prompt'],'0–10s. Кот: «Я тут»')
 def test_idempotent(self):
  text='Лена: «Кто-то пришёл?»'
  self.assertEqual(clean(clean(text)),text)
