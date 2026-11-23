import unittest
from unittest.mock import patch
from tests import test_workflow as workflow
from cartoonbot.providers import LiveProvider

class ReferencesBeforeScript(unittest.TestCase):
    def harness(self):
        h=workflow.Harness();h.setUp();self.addCleanup(h.tearDown);return h

    def test_photos_and_separate_instruction_reach_script_before_visuals(self):
        h=self.harness();h.event('new:seedance');h.press('audio=none');h.press('source=no')
        while h.current()['state']=='intake':h.press('skip')
        self.assertEqual(h.current()['state'],'references')
        self.assertEqual(h.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
        for path in ('face.png','outfit.png','bottle.png'):
            h.event(media={'kind':'photo','path':path})
        h.event(text='Лицо с первого, одежда со второго, флакон с третьего')
        h.press('done');h.press('style_refs_back')
        self.assertEqual(len(h.current()['data']['refs']),3)
        h.press('done');h.press('style=3d')
        with patch.object(h.provider,'script',wraps=h.provider.script) as script:h.finish_job()
        data=script.call_args.args[1]
        self.assertEqual(len(data['refs']),3)
        self.assertIn('флакон',data['photo_reference_instructions'])
        h.press('approve');h.press('continue_visuals');h.press('ratio=9:16')
        self.assertEqual(h.current()['state'],'busy')
        self.assertEqual(h.s.db.execute('SELECT kind FROM jobs ORDER BY created DESC LIMIT 1').fetchone()[0],'visuals')

    def test_script_writer_receives_actual_images_in_caption_order(self):
        h=self.harness();h.script('seedance');d=h.current()['data']
        d['refs']=[{'kind':'photo','path':'face.png','purpose':'identity'},
                   {'kind':'photo','path':'bottle.png','purpose':'product'}]
        d['photo_reference_instructions']='Use both references'
        live=LiveProvider(h.s,h.cfg)
        with patch.object(live,'structured',return_value=d['script']) as call:
            live.script({'id':'test'},d)
        self.assertEqual(call.call_args.kwargs['images'],['face.png','bottle.png'])
        payload=call.call_args.args[2]
        self.assertEqual(payload['photo_reference_instructions'],'Use both references')
        self.assertEqual(payload['photo_references'][1],{'photo_id':2,'purpose':'product'})
