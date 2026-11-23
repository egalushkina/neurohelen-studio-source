import unittest
import test_workflow
from cartoonbot.seedance_prompts import video_reference_prompt

class VideoReferenceTests(unittest.TestCase):
    def setUp(self):
        self.h=test_workflow.Harness();self.h.setUp()
        self.event=self.h.event;self.current=self.h.current;self.press=self.h.press;self.s=self.h.s

    def tearDown(self):
        self.h.tearDown()

    def test_seedance_video_reference_is_analysis_only(self):
        from cartoonbot.catalog import has_video_reference
        d={'recreate':True,'route':'seedance','seedance_reference_mode':'direct',
           'refs':[{'kind':'video','path':'source.mp4','seconds':15}],
           'script':{'assets':[],'scenes':[]}}
        self.assertFalse(has_video_reference(d))
        prompt=video_reference_prompt(d)
        self.assertIn('NOT attached',prompt)
        self.assertNotIn('@Video 1',prompt)

    def test_video_is_not_accepted_at_photo_reference_step(self):
        self.event('new:seedance')
        p=self.current();p['state']='references';self.s.save(p)
        self.event(media={'kind':'video','path':'long.mp4','seconds':30})
        self.assertEqual(self.current()['data']['refs'],[])
        self.assertEqual(self.current()['state'],'references')
