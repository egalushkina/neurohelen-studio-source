import unittest
from unittest.mock import Mock
from cartoonbot.reels import generate
from tests import test_workflow

class ReelsTests(unittest.TestCase):
    def test_short_copy_uses_final_scenes_and_video_language(self):
        provider=Mock();provider.structured.return_value={'caption':'Небольшая история. Как бы поступили вы?'}
        d={'lang':'uk','audio_mode':'ru','skipped_scenes':[2],'script':{'scenes':[
            {'id':1,'action':'Approved action','dialogue':'Русская реплика'},
            {'id':2,'action':'Skipped content'}]}}
        self.assertTrue(generate(provider,{},d))
        args=provider.structured.call_args.args
        self.assertEqual(args[2]['language'],'ru')
        self.assertEqual(len(args[2]['scenes']),1)
        self.assertEqual(args[3]['properties']['caption']['maxLength'],500)
        provider.structured.return_value={'caption':'x'*501}
        with self.assertRaises(ValueError):generate(provider,{},d)

    def test_failure_keeps_video_and_retry_works_with_zero_balance(self):
        h=test_workflow.Harness();h.setUp()
        try:
            h.event('new:omni');p=h.current();d=p['data']
            d.update(assembled='approved.mp4',script={'scenes':[]});p['state']='video';h.s.save(p)
            h.cfg.require_purchase=True
            h.s.db.execute('UPDATE users SET balance=0 WHERE id=1')
            h.provider.reels_text=Mock(side_effect=RuntimeError('unavailable'))
            h.press('approve');h.finish_job()
            self.assertEqual(h.current()['state'],'finished')
            self.assertTrue(h.current()['data']['reels_text_failed'])
            self.assertEqual(h.current()['data']['final'],'approved.mp4')
            h.provider.reels_text=Mock(return_value='Готовый текст.')
            h.press('reels_text_retry');h.finish_job()
            self.assertEqual(h.current()['data']['reels_text'],'Готовый текст.')
            count=h.s.db.execute('select count(*) from jobs').fetchone()[0]
            h.event('open:'+p['id'])
            self.assertEqual(h.s.db.execute('select count(*) from jobs').fetchone()[0],count)
            self.assertEqual(h.s.user(1)['balance'],0)
        finally:h.tearDown()
