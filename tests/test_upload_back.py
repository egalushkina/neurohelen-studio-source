from tests.test_workflow import Harness

class UploadBackTests(Harness):
    def test_back_preserves_script_and_can_choose_generation(self):
        self.script()
        with self.s.transaction():
            p=self.current();p['state']='visual_source';self.s.save(p)
        original=self.current()['data']['script']
        self.press('visual_source=upload')
        self.assertEqual(self.current()['state'],'scene_upload')
        self.press('scene_upload_back')
        self.assertEqual(self.current()['state'],'visual_source')
        self.assertEqual(self.current()['data']['script'],original)
        self.press('visual_source=create')
        self.assertEqual(self.current()['state'],'references')
        self.assertNotIn('style',self.current()['data'])
