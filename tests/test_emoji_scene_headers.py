import unittest
from cartoonbot.supplied_script import scene_sections

class EmojiSceneHeadersTests(unittest.TestCase):
    def test_last_scene_with_emoji_is_not_merged(self):
        text='\n\n'.join(f'{"🪳 " if n==5 else "### "}SCENE {n} — TITLE ({(n-1)*10}–{n*10} sec)\nVisual: action {n}\n🪳 Cockroach: line {n}' for n in range(1,6))
        sections=scene_sections(text)
        self.assertEqual(len(sections),5)
        self.assertNotIn('line 5',sections[3])
        self.assertIn('line 5',sections[4])
    def test_emojis_in_dialogue_are_not_headers(self):
        self.assertEqual(len(scene_sections('### SCENE 1\n🪳 Cockroach: Hello\n🧼 Soap: Hi')),1)
