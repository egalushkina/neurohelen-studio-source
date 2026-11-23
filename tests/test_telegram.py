import json
import tempfile
import unittest
from pathlib import Path
import httpx
from cartoonbot.config import Config
from cartoonbot.store import Store
from cartoonbot.engine import Engine
from cartoonbot.telegram import Telegram


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.cfg=Config(root=Path(self.tmp.name),token='fake')
        self.cfg.prepare(); self.s=Store(self.cfg.root/'db'); self.e=Engine(self.s,self.cfg)
        self.t=Telegram(self.cfg,self.s,self.e)

    def tearDown(self):
        self.t.client.close(); self.s.db.close(); self.tmp.cleanup()

    def test_public_guest_can_upload_reference(self):
        from unittest.mock import patch
        self.cfg.mode='codex';self.cfg.admins={9};self.cfg.public_access=True
        self.s.user(1,'ru');p=self.s.new_project(1,'omni','ru')
        p['state']='references';self.s.save(p)
        with patch.object(self.t,'download_media',return_value={'kind':'photo','path':'guest.png'}) as download:
            event=self.t.event({'update_id':900,'message':{'from':{'id':1},'chat':{'type':'private'},'photo':[{'file_id':'x'}]}})
        self.assertEqual(event['media']['path'],'guest.png')
        download.assert_called_once()

    def test_blocked_user_does_not_block_other_users(self):
        self.s.emit(1,'first'); self.s.emit(2,'second')
        calls=[]
        def respond(request):
            calls.append(request.content)
            if len(calls)==1: return httpx.Response(403,json={'ok':False,'error_code':403})
            return httpx.Response(200,json={'ok':True,'result':{'message_id':2}})
        self.t.client.close(); self.t.client=httpx.Client(transport=httpx.MockTransport(respond))
        self.assertTrue(self.t.deliver()); self.assertTrue(self.t.deliver())
        states=[r[0] for r in self.s.db.execute('SELECT delivered FROM outbox ORDER BY id')]
        self.assertEqual(states,[-1,1])

    def test_support_is_a_direct_url_button(self):
        from unittest.mock import patch
        self.s.user(1,'ru')
        self.e.home(1)
        row=self.s.db.execute('SELECT * FROM outbox ORDER BY id DESC LIMIT 1').fetchone()
        with patch.object(self.t,'call') as call:
            self.t.send_row(row)
            keyboard=json.loads(call.call_args.args[1]['reply_markup'])['inline_keyboard']
            button=next(b for group in keyboard for b in group if b.get('url'))
            self.assertEqual(button['url'],'https://t.me/neurohelen_pro')
            self.assertNotIn('callback_data',button)

    def test_group_upload_not_downloaded(self):
        ev=self.t.event({'update_id':1,'message':{'from':{'id':1},'chat':{'type':'group'},'video':{'file_id':'x'}}})
        self.assertIsNone(ev)

    def test_seedance_source_upload_downloads_as_limited_reference(self):
        from unittest.mock import patch
        self.s.user(1,'ru')
        p=self.s.new_project(1,'seedance','ru'); p['state']='seedance_source_upload'; self.s.save(p)
        with patch.object(self.t,'download_media',return_value={'kind':'video','path':'clip.mp4','seconds':15}) as download:
            event=self.t.event({'update_id':22,'message':{'from':{'id':1},'chat':{'type':'private'},'video':{'file_id':'x','duration':15}}})
            self.assertTrue(download.call_args.kwargs['reference_video'])
            self.assertEqual(event['media']['path'],'clip.mp4')

    def test_omni_source_video_reaches_analysis(self):
        from unittest.mock import patch
        self.s.user(1,'ru')
        p=self.s.new_project(1,'omni','ru'); p['state']='omni_source_upload'; self.s.save(p)
        for seconds in (15,60):
            with self.subTest(seconds=seconds), patch.object(self.t,'download_media',return_value={'kind':'video','path':'clip.mp4','seconds':seconds}) as download:
                event=self.t.event({'update_id':100+seconds,'message':{'from':{'id':1},'chat':{'type':'private'},'video':{'file_id':'x','duration':seconds}}})
                self.assertFalse(download.call_args.kwargs['reference_video'])
                self.assertEqual(event['media']['seconds'],seconds)
                with patch.object(self.e,'queue') as queue:
                    self.e.handle(event)
                    self.assertEqual(queue.call_args.args[1],'analyze')
                    self.assertEqual(queue.call_args.args[0]['data']['source'],event['media'])

    def test_retry_after_keeps_delivery_pending_without_busy_loop(self):
        self.s.emit(1,'hello')
        self.t.client.close(); self.t.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(429,json={'ok':False,'error_code':429,'parameters':{'retry_after':60}})))
        self.assertTrue(self.t.deliver()); self.assertFalse(self.t.deliver())
        row=self.s.db.execute('SELECT delivered,attempts FROM outbox').fetchone()
        self.assertEqual(tuple(row),(0,1))

    def test_callback_acknowledged_and_language_carried(self):
        self.t.client.close(); self.t.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'ok':True,'result':True})))
        event=self.t.event({'update_id':10,'callback_query':{'id':'cb','from':{'id':1,'language_code':'uk'},'message':{'chat':{'type':'private'}},'data':'new:omni'}})
        self.assertEqual(event['language'],'uk'); self.assertEqual(event['action'],'new:omni')




    def test_text_during_video_upload_does_not_block_following_menu(self):
        from unittest.mock import patch
        for state in ('upload','seedance_source_upload'):
            with self.subTest(state=state):
                self.s.user(1,'ru')
                p=self.s.new_project(1,'seedance','ru'); p['state']=state; self.s.save(p)
                start=100 if state=='upload' else 200
                updates=[{'update_id':start,'message':{'from':{'id':1},'chat':{'type':'private'},'text':'Что дальше?'}},
                         {'update_id':start+1,'message':{'from':{'id':1},'chat':{'type':'private'},'text':'/menu'}}]
                with patch.object(self.t,'call',return_value=updates):
                    self.t.poll_once()
                self.assertEqual(self.s.db.execute('SELECT max(id) FROM updates').fetchone()[0],start+1)
                self.assertGreater(self.s.db.execute('SELECT count(*) FROM outbox').fetchone()[0],0)
                self.e.handle({'id':start+2,'user_id':1,'text':'Что дальше?','media':None})

    def test_long_reference_rejected_before_download(self):
        from unittest.mock import patch
        with patch.object(self.t,'call') as call:
            with self.assertRaisesRegex(ValueError,'video_reference_too_long'):
                self.t.download_media({'video':{'file_id':'x','duration':31}},1,reference_video=True)
            call.assert_not_called()
