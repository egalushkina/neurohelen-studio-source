import unittest
from unittest.mock import Mock
from tests import test_workflow
from cartoonbot.dialogue_review import pending_scene,validate_proposal

class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.h=test_workflow.Harness(); self.h.setUp()
    def tearDown(self): self.h.tearDown()
    def prepare(self):
        h=self.h; h.script()
        p=h.current(); original='Кот: '+' '.join(['слово']*30)
        p['data']['script']['scenes'][0]['dialogue']=original; h.s.save(p)
        h.provider.propose_dialogue=Mock(return_value='Кот: Пора вставать!')
        h.press('approve');h.finish_job()
        self.assertEqual(h.current()['state'],'dialogue_proposal')
        self.assertEqual(h.current()['data']['script']['scenes'][0]['dialogue'],original)
        return original
    def test_failed_shortening_keeps_script_and_allows_retry_or_keep(self):
        original=self.prepare();h=self.h
        h.provider.propose_dialogue=Mock(side_effect=ValueError('seedance_dialogue_mismatch'))
        h.press('dialogue_retry');h.finish_job()
        self.assertEqual(h.current()['state'],'dialogue_proposal')
        self.assertTrue(h.current()['data']['dialogue_proposal']['failed'])
        h.press('dialogue_apply')
        self.assertEqual(h.current()['data']['script']['scenes'][0]['dialogue'],original)
        h.press('dialogue_keep')
        self.assertEqual(h.current()['state'],'script')
        self.assertIsNone(pending_scene(h.current()['data']))

    def test_keep_is_exact_and_does_not_repeat_proposal(self):
        original=self.prepare();h=self.h;h.press('dialogue_keep')
        self.assertEqual(h.current()['state'],'script')
        self.assertEqual(h.current()['data']['script']['scenes'][0]['dialogue'],original)
        self.assertIsNone(pending_scene(h.current()['data']))
        h.press('approve');self.assertEqual(h.current()['state'],'script_summary')
    def test_apply_only_after_confirmation_and_review_full_script(self):
        self.prepare();h=self.h;h.press('dialogue_apply')
        self.assertEqual(h.current()['state'],'script')
        self.assertEqual(h.current()['data']['script']['scenes'][0]['dialogue'],'Кот: Пора вставать!')
        self.assertFalse(h.current()['data']['videos'])
    def test_speaker_swap_is_rejected(self):
        with self.assertRaises(ValueError): validate_proposal('Кот: Привет!\nМама: Да.','Мама: Привет!\nКот: Да.')

    def test_video_edits_are_previewed_before_submission(self):
        h=self.h;h.visuals();h.press('approve')
        if h.current()['state']=='visuals': h.press('approve')
        p=h.current();original=p['data']['script']['scenes'][0]['dialogue']
        p['data']['video_edits']=[{'id':1,'instruction':'Change the line'}];h.s.save(p)
        h.cfg.mode='live'; h.cfg.admins={1}
        h.provider.structured=Mock(return_value={'action':'Cat waves','dialogue':'Кот: Привет!','camera':'wide'})
        h.provider.video=Mock(side_effect=AssertionError('No video before approval'))
        h.press('generate');h.finish_job()
        self.assertEqual(h.current()['state'],'video_revision_review')
        self.assertEqual(h.current()['data']['script']['scenes'][0]['dialogue'],original)
        h.press('accept_video_revision')
        self.assertEqual(h.current()['state'],'quote')
        self.assertEqual(h.current()['data']['script']['scenes'][0]['dialogue'],'Кот: Привет!')
        self.assertNotIn('video_edits',h.current()['data'])
        h.provider.video.assert_not_called()

    def test_shortening_during_video_resumes_remaining_scenes(self):
        import copy
        h=self.h; h.script(seconds=50)
        p=h.current();p['state']='quote';d=p['data']
        d['script']['scenes'][2]['dialogue']='Кот: '+' '.join(['слово']*30)
        d['images']={str(i):{'path':f'{i}.png'} for i in range(1,6)}
        d['videos']={str(i):{'path':f'{i}.mp4'} for i in (1,2)}
        d['resume_scene_ids']=[3,4,5]
        images=copy.deepcopy(d['images']); videos=copy.deepcopy(d['videos']); h.s.save(p)
        h.provider.propose_dialogue=Mock(return_value='Кот: Пора вставать!')
        h.press('generate');h.finish_job();h.press('dialogue_apply')
        d=h.current()['data']
        self.assertEqual(h.current()['state'],'quote')
        self.assertEqual(d['images'],images);self.assertEqual(d['videos'],videos)
        self.assertEqual(d['resume_scene_ids'],[3,4,5])
        self.assertEqual(h.e.quote(d)[0],750)
        self.assertEqual(d['script']['scenes'][2]['dialogue'],'Кот: Пора вставать!')

    def test_preflight_dialogue_failure_continues_without_result_recovery(self):
        h=self.h;h.script(route='seedance')
        p=h.current();p['state']='attention';p['data']['error']='seedance_dialogue_mismatch';h.s.save(p)
        count=h.s.db.execute('select count(*) from jobs').fetchone()[0]
        h.press('recover_check')
        self.assertEqual(h.current()['state'],'quote')
        self.assertEqual(h.s.db.execute('select count(*) from jobs').fetchone()[0],count)

    def test_prompt_length_failure_does_not_restart_storyboard(self):
        h=self.h;h.script(route='seedance')
        for action in ('recover_check','dialogue_retry_quote','error_edit_script'):
            p=h.current();p['state']='attention';d=p['data'];d['error']='seedance_prompt_too_long'
            d['images']={'1':{'path':'saved.png'}};d['videos']={'1':{'path':'saved.mp4'}};h.s.save(p)
            h.press(action)
            self.assertEqual(h.current()['state'],'quote')
            self.assertEqual(h.current()['data']['images'],d['images'])
            self.assertEqual(h.current()['data']['videos'],d['videos'])

    def test_failed_replacement_keeps_other_pending_scene_with_old_video(self):
        import copy,json
        h=self.h; h.script(); p=h.current(); d=p['data']
        base=copy.deepcopy(d['script']['scenes'][0])
        d['script']['scenes']=[dict(base,id=i,start=(i-1)*10,end=i*10) for i in range(1,6)]
        d['videos']={str(i):{'path':f'old-{i}.mp4'} for i in range(1,6)}
        d.update(resume_scene_ids=[3,4],error_scene_id=3,repair_scene=3,edit_target='video',error='RuntimeError')
        p['state']='attention';h.s.save(p);h.e.show(p)
        text=json.loads(h.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])['text']
        self.assertIn('Ожидают: 4',text)
        self.assertIn('Готовые сцены: 1, 2, 5',text)
        p=h.current();p['state']='edit_input';h.s.save(p)
        h.event(text='Изменить действие сцены 3');h.press('apply')
        self.assertEqual(h.current()['data']['resume_scene_ids'],[3,4])
        p=h.current();p['state']='attention';h.s.save(p)
        h.press('skip_scene');h.press('skip_scene_apply')
        self.assertEqual(h.current()['data']['resume_scene_ids'],[4])
        self.assertEqual(h.current()['state'],'quote')

    def test_video_revision_preview_only_contains_requested_scenes(self):
        import copy,json
        h=self.h;h.script();p=h.current();d=p['data']
        base=copy.deepcopy(d['script']['scenes'][0])
        d['pending_video_revision']={'scenes':[dict(base,id=i,action=f'UNIQUE_SCENE_{i}') for i in range(1,6)]}
        d['video_edits']=[{'id':3,'instruction':'change'},{'id':4,'instruction':'change'}]
        p['state']='video_revision_review';h.s.save(p);h.e.show(p)
        text=json.loads(h.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])['text']
        for i in (3,4):self.assertIn(f'UNIQUE_SCENE_{i}',text)
        for i in (1,2,5):self.assertNotIn(f'UNIQUE_SCENE_{i}',text)

    def test_successful_replacement_leaves_only_failed_target_pending(self):
        import copy
        from unittest.mock import patch
        h=self.h;h.script();p=h.current();d=p['data']
        base=copy.deepcopy(d['script']['scenes'][0])
        d['script']['scenes']=[dict(base,id=i,start=(i-1)*10,end=i*10,dialogue='') for i in range(1,6)]
        d['videos']={str(i):{'path':f'old-{i}.mp4'} for i in range(1,6)}
        d['images']={str(i):{'path':f'frame-{i}.png'} for i in range(1,6)}
        d['resume_scene_ids']=[3,4];p['state']='quote';h.s.save(p)
        calls=[]
        def video(job,data,scene,*args):
            calls.append(scene['id'])
            if scene['id']==4:raise RuntimeError('failed')
            return {'path':'new-3.mp4'}
        h.provider.video=Mock(side_effect=video)
        h.e.queue(h.current(),'video')
        with patch('cartoonbot.worker.media.info',return_value={'seconds':10}):h.w.run_once()
        self.assertEqual(calls,[3,4])
        d=h.current()['data'];self.assertEqual(d['resume_scene_ids'],[4])
        self.assertEqual(d['videos']['3']['path'],'new-3.mp4')
        self.assertEqual(d['videos']['4']['path'],'old-4.mp4')
