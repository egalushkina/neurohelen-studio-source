import unittest
from tests import test_workflow

class RejectedVideoTests(unittest.TestCase):
    setUp=test_workflow.Harness.setUp
    tearDown=test_workflow.Harness.tearDown
    event=test_workflow.Harness.event
    current=test_workflow.Harness.current
    press=test_workflow.Harness.press
    finish_job=test_workflow.Harness.finish_job
    script=test_workflow.Harness.script
    visuals=test_workflow.Harness.visuals
    def test_rejected_can_return_to_quote_without_starting_job(self):
        self.visuals('seedance')
        p=self.current()
        with self.s.transaction():
            jid=self.s.enqueue(p,'video')
            self.s.db.execute("UPDATE jobs SET status='attention' WHERE id=?",(jid,))
            p=self.current(); p['state']='attention'; self.s.save(p)
        oid=self.s.operation(jid,'byteplus_seedance')
        self.s.record_operation(oid,'rejected',usage={'http_status':400})
        self.assertTrue(self.e.rejected_video(p))
        before=self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        self.press('retry_video_quote')
        self.assertEqual(self.current()['state'],'quote')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],before)
        self.s.record_operation(oid,'unknown')
        self.assertFalse(self.e.rejected_video(p))
        self.s.record_operation(oid,'rejected','remote-task')
        self.assertFalse(self.e.rejected_video(p))
    def test_rights_rejection_offers_character_visual_edit(self):
        self.visuals('omni')
        p=self.current();p['state']='attention'
        p['data'].update(error='google_third_party_content',error_scene_id=1)
        self.s.save(p);self.e.show(p)
        import json
        message=json.loads(self.s.db.execute('select body from outbox order by id desc limit 1').fetchone()[0])
        self.assertIn('правами третьих лиц',message['text'])
        self.assertIn('Изменить персонажа',str(message['buttons']))
        self.press('error_edit_visual')
        self.assertEqual(self.current()['state'],'repair_visual')
        self.assertEqual(self.current()['data']['repair_scene'],1)
    def test_vague_character_change_asks_before_generating(self):
        self.visuals('omni')
        p=self.current();p['state']='repair_visual';p['data']['repair_scene']=1;self.s.save(p)
        before=self.s.db.execute('select count(*) from jobs').fetchone()[0]
        for request in ('Измени девушку персонажа','Поменяй девушку на фото .','Пожалуйста поменяй мне героиню на этом изображении'):
            self.event(text=request)
        self.assertEqual(self.current()['state'],'repair_visual')
        self.assertEqual(self.s.db.execute('select count(*) from jobs').fetchone()[0],before)
        self.event(text='Сделай женщину с круглым лицом и рыжими кудрями')
        self.assertEqual(self.current()['state'],'busy')
    def test_remaining_visuals_edit_targets_only_requested_frames(self):
        self.visuals('omni',seconds=40)
        p=self.current();p['state']='repair_visual';p['data']['repair_scene']=1
        original=p['data']['images']['1']['path'];self.s.save(p)
        self.event(text='У сцене 2,3,4 также поменяй цвет кота на этот')
        import json
        payload=json.loads(self.s.db.execute('select payload from jobs order by created desc limit 1').fetchone()[0])
        self.assertEqual([x['id'] for x in payload['edits']],[2,3,4])
        self.finish_job()
        self.assertEqual(self.current()['data']['images']['1']['path'],original)
        self.assertEqual(self.current()['data']['repair_visual_ids'],[2,3,4])
        self.event(text='Поменяй все остальные как в первом кадре')
        payload=json.loads(self.s.db.execute('select payload from jobs order by created desc limit 1').fetchone()[0])
        self.assertEqual([x['id'] for x in payload['edits']],[2,3,4])
