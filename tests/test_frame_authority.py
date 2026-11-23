import unittest
from unittest.mock import patch
from tests.test_workflow import Harness
from cartoonbot.continuity import approved_frame_authority, DEVICE_GEOMETRY

class FrameAuthorityTests(unittest.TestCase):
    def test_remaining_generation_uses_edited_first_frame_over_legacy_staging_appearance(self):
        h=Harness();h.setUp()
        try:
            h.script('omni',seconds=20);h.press('approve');h.press('continue_visuals');h.press('ratio=9:16');h.press('done');h.press('style=3d');h.finish_job()
            h.press('edit');h.event(text='Длинные кудрявые волосы');h.finish_job()
            revised=h.current()['data']['images']['1']['path']
            p=h.current();p['data']['script']['scenes'][1]['image_prompt']='STALE SHORT BOB'
            p['data']['script']['assets'][0]['description']='OBSOLETE ASSET IDENTITY';h.s.save(p)
            h.press('approve')
            with patch.object(h.provider,'image',wraps=h.provider.image) as generate:h.finish_job()
            self.assertEqual(generate.call_args.args[2][0],revised)
            self.assertNotIn('OBSOLETE ASSET IDENTITY',generate.call_args.args[1])
            self.assertIn('ignore only stale identity/style details',generate.call_args.args[1])
            contract=approved_frame_authority(h.current()['data'],generate.call_args.args[2])
            self.assertIn('image 1 is the LATEST',contract)
        finally:h.tearDown()
    def test_device_rule_covers_front_back_and_viewpoint(self):
        for text in ('INNER','outer lid','phone/tablet','over-the-shoulder'):
            self.assertIn(text,DEVICE_GEOMETRY)

    def test_anatomy_edits_keep_targets_without_other_scene_pose_references(self):
        h=Harness();h.setUp()
        try:
            h.visuals('omni',seconds=30)
            p=h.current();old={k:v['path'] for k,v in p['data']['images'].items()}
            edits=[{'id':i,'instruction':'Исправить анатомию: убрать лишнюю руку.'} for i in (2,3)]
            h.s.enqueue(p,'edit_visuals',{'edits':edits})
            with patch.object(h.provider,'image',wraps=h.provider.image) as generate:h.finish_job()
            self.assertEqual(generate.call_count,2)
            for call,n in zip(generate.call_args_list,(2,3)):
                self.assertEqual(call.args[2],[old[str(n)]])
                self.assertIn('rebuild the affected',call.args[1])
            self.assertEqual(h.current()['data']['images']['1']['path'],old['1'])
        finally:h.tearDown()

    def test_replace_request_survives_planning_and_renders_selected_frames_anew(self):
        h=Harness();h.setUp()
        try:
            h.visuals('omni',seconds=30);h.press('edit')
            h.event(text='Переделай фото 2 и 3, исправь руки')
            changes={'clarification':'','changes':[{'id':i,'instruction':'Исправить анатомию рук.'} for i in (2,3)]}
            with patch.object(h.provider,'edits',return_value=changes):h.finish_job()
            self.assertTrue(all(x['regenerate'] for x in h.current()['data']['edits']))
            h.press('apply')
            with patch.object(h.provider,'image',wraps=h.provider.image) as generate:h.finish_job()
            self.assertEqual(generate.call_count,2)
            for call in generate.call_args_list:
                self.assertIn('Create a NEW replacement',call.args[1])
                self.assertNotIn('Edit ONLY',call.args[1])
        finally:h.tearDown()
