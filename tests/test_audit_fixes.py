import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from tests import test_workflow as workflow
from cartoonbot.seedance_continuity import validate_dialogue,compile_prompt
from cartoonbot.quality import review

class AuditFixes(unittest.TestCase):
    def harness(self):
        h=workflow.Harness();h.setUp();self.addCleanup(h.tearDown);return h

    def test_partial_edit_invalidates_video_and_checkpoints_each_frame(self):
        h=self.harness();h.visuals('omni',seconds=20)
        p=h.current();before=p['data']['images']['1']['path']
        p['data']['videos']={'1':{'path':'old1'},'2':{'path':'old2'}}
        p['data']['assembled']='old_final';h.s.save(p)
        job=h.s.enqueue(p,'edit_visuals',{'edits':[{'id':i,'instruction':'Replace pose'} for i in (1,2)]})
        original=h.provider.image;n=[0]
        def image(*args):
            n[0]+=1
            if n[0]==2:raise RuntimeError('failed')
            return original(*args)
        with patch.object(h.provider,'image',side_effect=image):h.w.run_once()
        d=h.current()['data'];self.assertNotEqual(before,d['images']['1']['path'])
        self.assertNotIn('1',d['videos']);self.assertIn('2',d['videos']);self.assertNotIn('assembled',d)
        self.assertEqual(d['visual_completed'][job],[1])
        self.assertEqual(d['pending_generation']['unit'],2)

    def test_recovery_then_resume_skips_completed_edit(self):
        h=self.harness();h.visuals('omni',seconds=20);p=h.current()
        jid=h.s.enqueue(p,'edit_visuals',{'edits':[{'id':i,'instruction':'Replace pose'} for i in (1,2)]})
        h.s.claim();p=h.current();p['data']['visual_completed']={jid:[1]};h.s.save(p);h.s.recover()
        h.press('recover_check');h.finish_job();self.assertEqual(h.current()['state'],'resume_ready')
        h.press('resume_generation')
        with patch.object(h.provider,'image',wraps=h.provider.image) as generate:h.finish_job()
        self.assertEqual(generate.call_count,1);self.assertEqual(generate.call_args.args[-1],2)

    def test_unknown_recovery_never_submits_generation(self):
        h=self.harness();h.visuals('omni');p=h.current();jid=h.s.enqueue(p,'video');h.s.claim()
        oid=h.s.operation(jid,'google_omni');h.s.record_operation(oid,'unknown');h.s.recover()
        h.press('recover_check')
        with patch.object(h.provider,'video') as generate:h.w.run_once()
        self.assertFalse(generate.called);self.assertEqual(h.current()['data']['error'],'recovery_pending')
        h.press('retry_unknown');self.assertEqual(h.current()['state'],'retry_unknown_confirm')

    def test_completed_empty_image_recovery_allows_resume_without_generation(self):
        h=self.harness();h.visuals('omni');p=h.current()
        jid=h.s.enqueue(p,'visuals');h.s.claim();p=h.current()
        target=h.cfg.root/'media'/'1'/p['id']/jid/'image_3.png'
        target.parent.mkdir(parents=True)
        p['data']['pending_generation']={'job':jid,'unit':3,'target':str(target)};h.s.save(p)
        result=h.cfg.root/'codex'/'empty'/'result.json';result.parent.mkdir(parents=True)
        result.write_text('{"path":""}')
        oid=h.s.operation(jid,'local_codex')
        h.s.record_operation(oid,'succeeded',usage={'native_image':True,'result_path':str(result)})
        h.s.recover();h.press('recover_check')
        body=json.loads(h.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertIn('Проверяю сохранённый результат',body['text'])
        with patch.object(h.provider,'image') as generate:h.finish_job()
        generate.assert_not_called()
        self.assertEqual(h.current()['state'],'resume_ready')
        self.assertEqual(h.s.db.execute('SELECT state FROM operations WHERE id=?',(oid,)).fetchone()[0],'failed')

    def test_partial_billing_matches_quote_and_is_idempotent(self):
        h=self.harness();h.visuals('omni',seconds=20);p=h.current()
        self.assertEqual(h.e.quote(p['data']),(500,20))
        jid=h.s.enqueue(p,'video',{'unit_price':250},reserve=500)
        job=h.s.claim()
        with h.s.transaction():
            h.s.complete_unit(job,1);h.s.complete_unit(job,1);h.s.settle(job,False);h.s.settle(job,False)
        self.assertEqual(h.s.user(1)['balance'],9750);self.assertEqual(h.s.user(1)['reserved'],0)

    def test_dialogue_mismatch_and_silent_mode(self):
        plan={'shots':[{'dialogue':'Мама: «Привет!»'}]}
        d={'script':{'scenes':[{'dialogue':'Мама: «Привет!»'}]}}
        validate_dialogue(plan,d)
        with self.assertRaisesRegex(ValueError,'dialogue_mismatch'):
            validate_dialogue({'shots':[{'dialogue':'Пока!'}]},d)
        with self.assertRaisesRegex(ValueError,'dialogue_mismatch'):
            validate_dialogue(plan,{'audio_mode':'none'})

    def test_archive_keeps_old_results_accessible(self):
        h=self.harness()
        for _ in range(12):h.event('new:omni')
        h.event('archive:0')
        body=json.loads(h.s.db.execute('select body from outbox order by id desc limit 1').fetchone()[0])
        self.assertEqual(len(body['buttons']),2)
        self.assertEqual(h.s.db.execute('select count(*) from projects').fetchone()[0],12)

    def test_identical_edit_flagged_without_another_model_call(self):
        with tempfile.TemporaryDirectory() as temp:
            a=Path(temp)/'a';a.write_bytes(b'same')
            p=Mock();result=review(p,{},str(a),str(a),'change hair','3d')
            self.assertFalse(result['matches_request']);p.structured.assert_not_called()

    def test_retry_delivery_checks_ownership(self):
        h=self.harness();h.s.user(1);h.s.user(2)
        h.s.emit(1,file='saved.mp4',kind='video');oid=h.s.db.execute('select max(id) from outbox').fetchone()[0]
        h.s.db.execute('update outbox set delivered=-1 where id=?',(oid,))
        h.event('resend:'+str(oid),uid=2)
        self.assertEqual(h.s.db.execute('select delivered from outbox where id=?',(oid,)).fetchone()[0],-1)
        h.event('resend:'+str(oid),uid=1)
        self.assertEqual(h.s.db.execute('select delivered from outbox where id=?',(oid,)).fetchone()[0],0)

    def test_history_compression_preserves_stale_button_data(self):
        h=self.harness();h.script();p=h.current();expected=copy.deepcopy(p['data']);revision=p['revision']
        h.s.save(p);row=h.s.db.execute('SELECT body FROM history WHERE project_id=? AND revision=?',(p['id'],revision)).fetchone()
        self.assertIsInstance(row['body'],bytes);self.assertEqual(h.s.history_data(row['body']),expected)

    def test_failed_delivery_has_resend_notice_without_generation(self):
        from cartoonbot.telegram import Telegram
        h=self.harness();h.s.user(1,'ru');h.s.emit(1,file=str(h.cfg.root/'media'/'missing.mp4'),kind='video')
        t=Telegram(h.cfg,h.s,h.e);self.addCleanup(t.client.close)
        self.assertTrue(t.deliver())
        body=json.loads(h.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertIn('resend:',str(body['buttons']))
        self.assertEqual(h.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)

    def test_no_cross_scene_operation_recovery(self):
        h=self.harness();h.visuals('omni',seconds=20);p=h.current();jid=h.s.enqueue(p,'edit_visuals')
        job=h.s.claim();p=h.current()
        target=h.cfg.root/'media'/'1'/p['id']/jid/'image_2.png';target.parent.mkdir(parents=True)
        p['data']['pending_generation']={'job':jid,'unit':1,'target':str(target.parent/'image_1.png')};h.s.save(p)
        oid=h.s.operation(jid,'google_omni');h.s.record_operation(oid,'unknown')
        p=h.current();p['data']['pending_generation']={'job':jid,'unit':2,'target':str(target)};h.s.save(p)
        h.s.recover();h.press('recover_check');h.finish_job()
        self.assertEqual(h.current()['state'],'resume_ready')
        self.assertFalse(target.exists())

    def test_live_token_billing_is_opt_in(self):
        from cartoonbot.config import Config
        self.assertFalse(Config().billing_enabled)

    def test_unknown_charge_stays_reserved(self):
        h=self.harness();h.visuals();p=h.current();jid=h.s.enqueue(p,'video',{'unit_price':250},reserve=250)
        job=h.s.claim();oid=h.s.operation(jid,'google_omni');h.s.record_operation(oid,'unknown')
        self.assertTrue(h.s.unresolved_charge(job))
        self.assertEqual(h.s.user(1)['reserved'],250)
