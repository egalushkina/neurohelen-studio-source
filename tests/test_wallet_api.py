import os,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
import httpx
from webstudio.server import Studio,Handler
from cartoonbot.store import Store
class LinkAPITests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
  self.env=patch.dict(os.environ,{'STUDIO_WALLET_PATH':str(root/'wallet.db'),'STUDIO_WALLET_NAMESPACE':'web','STUDIO_TELEGRAM_USERNAME':'test_bot'});self.env.start()
  self.app=Studio(root/'web')
  with patch.dict(os.environ,{'STUDIO_WALLET_NAMESPACE':'telegram'}):self.tg=Store(root/'telegram.db',initial_balance=0)
  self.tg.user(123)
  self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.app=self.app
  self.base=f'http://127.0.0.1:{self.server.server_port}';self.server.origin=self.base
  self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.client=httpx.Client(base_url=self.base,headers={'Origin':self.base})
 def tearDown(self):
  self.client.close();self.server.shutdown();self.server.server_close();self.thread.join();self.app.store.db.close();self.tg.db.close();self.env.stop();self.tmp.cleanup()
 def register(self):
  r=self.client.post('/api/register',json={'email':'test@example.test','password':'Password-test-2026'})
  self.assertEqual(r.status_code,200);self.client.headers['X-CSRF-Token']=r.json()['csrf']
 def test_auth_csrf_and_full_link_flow(self):
  self.assertEqual(self.client.post('/api/telegram-link/begin',json={}).status_code,401)
  self.register()
  csrf=self.client.headers.pop('X-CSRF-Token')
  self.assertEqual(self.client.post('/api/telegram-link/begin',json={}).status_code,403)
  self.client.headers['X-CSRF-Token']=csrf
  r=self.client.post('/api/telegram-link/begin',json={});self.assertEqual(r.status_code,200,r.text)
  token=r.json()['url'].split('start=link_')[1];self.tg.wallet.claim(123,token)
  status=self.client.get('/api/telegram-link').json();self.assertEqual(status['pending_telegram_id'],123)
  self.assertEqual(self.client.post('/api/telegram-link/confirm',json={'telegram_id':456}).status_code,400)
  r=self.client.post('/api/telegram-link/confirm',json={'telegram_id':123});self.assertEqual(r.status_code,200,r.text)
  self.assertTrue(self.client.get('/api/telegram-link').json()['linked'])
  self.assertEqual(self.tg.user(123)['balance'],3000)
 def test_foreign_origin_cannot_begin(self):
  self.register();self.client.headers['Origin']='https://evil.example'
  self.assertEqual(self.client.post('/api/telegram-link/begin',json={}).status_code,403)
