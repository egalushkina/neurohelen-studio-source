import copy
import unittest
from unittest.mock import Mock
from cartoonbot.voices import ensure,for_scene,for_video,turn_taking
from cartoonbot.seedance_continuity import compile_prompt

class Voices(unittest.TestCase):
    def test_speech_direction_is_lively_clear_and_keeps_turns_separate(self):
        rule=turn_taking()
        self.assertIn('slightly brisk',rule)
        self.assertIn('clear speech',rule)
        self.assertIn('brief pauses',rule)
        self.assertIn('no overlapping voices',rule)
        self.assertIn('never slow or rushed',rule)

    def data(self):
        return {'seconds':15,'lang':'ru','script':{'assets':[
            {'name':'Кот','description':'Orange cat. VOICE PROFILE: adult male, warm baritone, clear Russian, lively natural pace.'},
            {'name':'Лена','description':'VOICE PROFILE: adult female, bright alto, clear Russian, natural pace.'}],
            'scenes':[{'dialogue':'Кот: «Привет!»'},{'dialogue':'Кот: «Просыпайся!»\nЛена: «Хорошо!»'}]}}
    def test_same_voice_across_scenes_without_more_model_calls(self):
        d=self.data();p=Mock();ensure(p,{},d)
        d['script']['assets'][0]['description']='VOICE PROFILE: female soprano'
        ensure(p,{},d)
        self.assertIn('adult male',for_scene(d,d['script']['scenes'][1]))
        self.assertNotIn('soprano',for_video(d));p.structured.assert_not_called()
        self.assertEqual(for_video(d).count('adult male'),1)
    def test_missing_voice_created_once_and_reused(self):
        d=self.data();d['script']['assets'][0]['description']='Cat narrator'
        p=Mock();p.structured.return_value={'кот':'Male, warm baritone, clear Russian, natural pace.'}
        ensure(p,{},d);ensure(p,{},d)
        self.assertEqual(p.structured.call_count,1)
        self.assertIn('warm baritone',for_scene(d,d['script']['scenes'][0]))
    def test_seedance_receives_canonical_profiles_outside_generated_audio(self):
        d=self.data();ensure(Mock(),{},d)
        for i,scene in enumerate(d['script']['scenes']):
            scene.update(start=i*15/len(d['script']['scenes']),end=(i+1)*15/len(d['script']['scenes']))
        p=Mock();p.structured.return_value={'shots':[{'start':0,'end':15,'location':'room',
            'cast':[{'image':1,'wardrobe':'as image 1'}],'action':'Cat speaks, Lena responds',
            'camera':'medium','dialogue':'Кот: «Привет!»\nКот: «Просыпайся!»\nЛена: «Хорошо!»'}],'audio':'No music.'}
        text=compile_prompt(p,{},d,'',['cat.png'])
        self.assertIn('adult male, warm baritone',text)
        self.assertIn('adult female, bright alto',text)
        self.assertLessEqual(len(text),4000)
        self.assertEqual(p.structured.call_args.args[2]['voice_cast'],d['voice_cast'])
    def test_silent_scene_has_no_voice_cast_in_prompt(self):
        d=self.data();ensure(Mock(),{},d);d['audio_mode']='none'
        self.assertEqual(for_video(d),'');self.assertEqual(for_scene(d,{'dialogue':''}),'')

    def test_explicit_voice_change_is_applied_once(self):
        d=self.data();p=Mock();ensure(p,{},d)
        previous=copy.deepcopy(d['voice_cast']);changed=copy.deepcopy(previous);changed['кот']='Male tenor, clear Russian, natural pace.'
        d['video_edits']=[{'id':1,'instruction':'Change the cat voice to a tenor'}]
        p.structured.return_value=changed
        ensure(p,{},d);ensure(p,{},d)
        self.assertEqual(p.structured.call_count,1)
        self.assertEqual(d['voice_cast']['лена'],previous['лена'])
        self.assertIn('tenor',for_scene(d,d['script']['scenes'][1]))
