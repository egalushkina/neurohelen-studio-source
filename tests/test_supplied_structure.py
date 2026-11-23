import copy
import unittest
from cartoonbot.supplied_script import scene_sections,merge_request,merge_scenes
from tests.test_workflow import Harness

class StructureTests(unittest.TestCase):
    def test_english_final_is_separate_section(self):
        for heading in ('FINAL — NEUROHELEN BOT','Final','FINAL SCENE','Finale'):
            with self.subTest(heading=heading):
                text='SCENE 4 — SHAMPOO\nShampoo speaks.\n'+heading+'\nBot speaks.'
                parts=scene_sections(text)
                self.assertEqual(len(parts),2)
                self.assertNotIn('Bot speaks.',parts[0])
                self.assertIn('Bot speaks.',parts[1])

    def test_final_is_fifth_scene(self):
        text='\n---\n'.join([f'### СЦЕНА {i} — Название\nДействие.' for i in range(1,5)]+['### ФИНАЛ\nОбе реплики.'])
        self.assertEqual(len(scene_sections(text)),5)
        self.assertIn('Обе реплики',scene_sections(text)[-1])
        self.assertEqual(scene_sections('Идея без заголовков'),[])

    def test_merge_preserves_content_speakers_and_updates_duration(self):
        scenes=[dict(id=i,start=(i-1)*10,end=i*10,action=f'Action {i}',camera='wide',dialogue=f'Actor {i}: line {i}',image_prompt='image',video_prompt='video')for i in range(1,7)]
        d={'route':'omni','seconds':60,'script':{'scenes':scenes}}
        original=copy.deepcopy(scenes)
        result=merge_scenes(d,merge_request('Объедини сцены 5 и 6'))
        self.assertEqual(len(result['scenes']),5)
        self.assertEqual(d['seconds'],50)
        self.assertEqual(result['scenes'][4]['dialogue'],'Actor 5: line 5\nActor 6: line 6')
        self.assertEqual(scenes,original)
        self.assertEqual(result['scenes'][-1]['end'],50)
        self.assertEqual(merge_request('Поменять фото 5 и 6'),[])
        with self.assertRaises(ValueError): merge_scenes(d,[1,4])

class MergeFlowTests(Harness):
    def test_video_merge_changes_script_instead_of_video_prompt(self):
        self.script(seconds=60)
        p=self.current();p['state']='edit_input';d=p['data'];d['edit_target']='video'
        base=d['script']['scenes'][0]
        d['script']['scenes']=[dict(base,id=i,start=(i-1)*10,end=i*10,dialogue=f'Actor: line {i}') for i in range(1,7)]
        d['images']={'1':{'path':'keep.png'},'5':{'path':'old.png'}}
        d['videos']={'1':{'path':'keep.mp4'},'6':{'path':'old.mp4'}}
        self.s.save(p)
        self.event(text='Объедини сцены 5 и 6')
        self.finish_job();p=self.current()
        self.assertEqual(p['state'],'script')
        self.assertEqual(len(p['data']['script']['scenes']),5)
        self.assertEqual(list(p['data']['videos']),['1'])
        self.assertEqual(list(p['data']['images']),['1'])
