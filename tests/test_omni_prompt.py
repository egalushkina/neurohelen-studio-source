import unittest
from cartoonbot.omni_prompts import delivery_prompt

class OmniPromptTests(unittest.TestCase):
 def test_reference_frame_keeps_action_and_age_without_full_cast_descriptions(self):
  d={'ratio':'9:16','style':'3d','lip':True,'script':{'assets':[
   {'name':'Дочка','description':'Purple backpack, blue eyes. VOICE PROFILE: female, perceived age 9, bright voice.'},
   {'name':'Автор','description':'Green blouse. VOICE PROFILE: adult voice.'},
   {'name':'Location','description':'Unused room with broken furniture.'}]}}
  scene={'video_prompt':'Long description of face and eyes','action':'Дочка нажимает рычаг.', 'camera':'Medium shot', 'dialogue':'Дочка: «Мост готов!»'}
  prompt=delivery_prompt(d,scene,'Russian',True)
  for s in ('reference image 1','Дочка нажимает рычаг.', 'Дочка: «Мост готов!»','perceived age 9'):self.assertIn(s,prompt)
  for s in ('Purple backpack','blue eyes','Green blouse','adult voice','Unused room','Long description'):self.assertNotIn(s,prompt)
 def test_text_only_and_approved_meaning_are_not_filtered(self):
  d={'ratio':'9:16','style':'3d','lip':True}
  scene={'video_prompt':'A child sees a damaged bridge.', 'dialogue':'Child: "I am scared."'}
  prompt=delivery_prompt(d,scene,'English',False)
  self.assertIn(scene['video_prompt'],prompt);self.assertIn(scene['dialogue'],prompt)

 def test_addressed_character_is_not_the_speaker(self):
  from cartoonbot.omni_prompts import scene_voices
  d={'script':{'assets':[{'name':'Лена','description':'VOICE PROFILE: female adult voice'}, {'name':'Персик','description':'VOICE PROFILE: cheerful cat voice'}]}}
  result=scene_voices(d,{'dialogue':'Персик: «Лена, вставай!»'})
  self.assertIn('cheerful cat voice',result)
  self.assertNotIn('female adult voice',result)

 def test_compact_prompt_preserves_long_dialogue_without_truncation(self):
  line='Лена: «'+('Слова ' * 250)+'»'
  scene={'video_prompt':'unused verbose draft '*100,'action':'Лена машет рукой.','camera':'Static shot.','dialogue':line}
  d={'ratio':'9:16','style':'3d','lip':True}
  prompt=delivery_prompt(d,scene,'Russian',True)
  self.assertIn(line,prompt)
  self.assertIn(scene['action'],prompt)
  self.assertNotIn('unused verbose draft',prompt)
  self.assertLess(len(prompt)-len(line),600)

 def test_inline_second_speaker_keeps_voice(self):
  from cartoonbot.omni_prompts import scene_voices
  d={'script':{'assets':[{'name':'Лена','description':'VOICE PROFILE: female'}, {'name':'Марс','description':'VOICE PROFILE: male'}]}}
  result=scene_voices(d,{'dialogue':'Лена: «Привет!» Марс: «Привет, Лена!»'})
  self.assertIn('Лена: female',result)
  self.assertIn('Марс: male',result)
 def test_dialogue_warning_only_pending_omni_scenes(self):
  from cartoonbot.omni_prompts import crowded_dialogue_scenes
  line='Лена: «'+('слово '*30)+'»'
  d={'route':'omni','script':{'scenes':[{'id':i,'dialogue':line} for i in range(1,5)]},'videos':{'1':'ready'},'skipped_scenes':[2]}
  self.assertEqual(crowded_dialogue_scenes(d),[3,4])
  d['route']='seedance'
  self.assertEqual(crowded_dialogue_scenes(d),[])

 def test_product_short_speaker_label_matches_unique_asset(self):
  from cartoonbot.omni_prompts import scene_voices
  d={'script':{'assets':[{'name':'OLLIN крем спрей 15 в 1','description':'VOICE PROFILE: male baritone'}]}}
  self.assertIn('OLLIN: male baritone',scene_voices(d,{'dialogue':'OLLIN: «Привет!»'}))
