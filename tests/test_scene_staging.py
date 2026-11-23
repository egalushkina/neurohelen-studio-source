import unittest
from unittest.mock import Mock,patch
from cartoonbot.visual_prompt import compile_prompt,SECTIONS
from tests import test_workflow as workflow

class SceneStaging(unittest.TestCase):
    def test_compiler_gets_current_staging_and_neighbors_separately(self):
        p=Mock();p.structured.return_value={k:'None' for k in SECTIONS}
        scenes=[{'id':1,'action':'She enters','camera':'wide','image_prompt':'standing at doorway'},
                {'id':2,'action':'She speaks','camera':'medium','image_prompt':'seated at table'},
                {'id':3,'action':'She looks in mirror','camera':'over shoulder','image_prompt':'restroom'}]
        compile_prompt(p,{}, {'route':'omni','ratio':'9:16','script':{'scenes':scenes}},['first.png'],'brief','',2)
        data=p.structured.call_args.args[2]
        self.assertEqual(data['current_scene'],scenes[1])
        self.assertEqual([s['id'] for s in data['adjacent_scene_actions']],[1,3])
        self.assertEqual(data['visual_kind'],'scene_frames')

    def test_remaining_frame_keeps_scene_staging_in_provider_brief(self):
        h=workflow.Harness();h.setUp();self.addCleanup(h.tearDown)
        h.script('omni',seconds=20)
        p=h.current();p['data']['script']['scenes'][1]['image_prompt']='SITTING AT THE TABLE, side view'
        h.s.save(p);h.press('approve');h.press('continue_visuals');h.press('ratio=9:16');h.press('no_refs');h.press('style=3d');h.finish_job()
        h.press('approve')
        with patch.object(h.provider,'image',wraps=h.provider.image) as image:h.finish_job()
        self.assertIn('SITTING AT THE TABLE',image.call_args.args[1])
        self.assertIn('CURRENT SCENE STAGING',image.call_args.args[1])
