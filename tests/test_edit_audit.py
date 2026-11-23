import unittest
from unittest.mock import patch
from tests.test_rejected_video import RejectedVideoTests
from cartoonbot.visual_edits import repair_edits

class EditAuditTests(RejectedVideoTests):
    def test_scene_ranges_and_reference_are_not_confused(self):
        d={'repair_scene':1,'images':{str(i):{} for i in range(1,5)}}
        for text in ('Измени сцены 2-4 как в сцене 1','Поменяй все остальные как в сцене 1'):
            self.assertEqual([e['id'] for e in repair_edits(d,text)],[2,3,4])

    def test_repeated_visual_edits_use_latest_frame_keep_other_videos(self):
        self.visuals('omni',seconds=20)
        p=self.current();p['data']['videos']={'2':{'path':'already-created.mp4'}};self.s.save(p)
        for request in ('1: Сделай кота рыжим','1: Сделай кота белым'):
            before=self.current()['data']['images']['1']['path']
            self.press('edit');self.event(text=request);self.finish_job()
            self.assertEqual(self.current()['state'],'edit_confirm')
            self.press('apply')
            with patch.object(self.provider,'image',wraps=self.provider.image) as generate:
                self.finish_job()
            self.assertEqual(generate.call_args.args[2][0],before)
            self.assertIn('already-created.mp4',str(self.current()['data']['videos']))
            self.assertEqual(self.current()['state'],'visual_sync_choice')
            self.press('visual_sync_only')
            self.assertEqual(self.current()['state'],'visuals')

    def test_correction_in_confirmation_is_accepted(self):
        self.visuals('omni',seconds=20)
        self.press('edit');self.event(text='1: Сделай кота рыжим');self.finish_job()
        self.event(text='1: Нет, сделай кота белым');self.finish_job()
        self.assertEqual(self.current()['state'],'edit_confirm')
        self.assertIn('белым',str(self.current()['data']['edits']))

    def test_edit_planner_receives_actual_images(self):
        from cartoonbot.providers import LiveProvider
        provider=LiveProvider(self.s,self.cfg)
        data={'lang':'ru','edit_target':'visuals','images':{'4':{'path':'latest-frame.png'}},'script':{}}
        with patch.object(provider,'structured',return_value={'changes':[],'clarification':'Which?'}) as call:
            provider.edits({'id':'test'},data,'Исправь экран',[4])
        self.assertEqual(call.call_args.kwargs['images'],['latest-frame.png'])
        self.assertEqual(call.call_args.args[2]['attached_images'],[{'image':1,'target':4}])

    def test_character_sync_updates_other_frames_before_video(self):
        self.visuals('omni',seconds=30)
        p=self.current(); p['state']='repair_visual'; p['data']['repair_scene']=1; self.s.save(p)
        self.event(text='Сделай героиню рыжеволосой женщиной')
        self.finish_job(); self.assertEqual(self.current()['state'],'visual_sync_choice')
        source=self.current()['data']['images']['1']['path']
        self.press('visual_sync_all')
        with patch.object(self.provider,'image',wraps=self.provider.image) as generate:
            self.finish_job()
        self.assertEqual(generate.call_count,2)
        for call in generate.call_args_list:
            self.assertIn(source,call.args[2])
            self.assertIn('authoritative identity',call.args[1])
        self.assertEqual(self.current()['state'],'repair_visual_ready')
        self.assertEqual(self.current()['data']['images']['1']['path'],source)
        self.assertEqual(self.current()['data']['repair_visual_ids'],[2,3])
