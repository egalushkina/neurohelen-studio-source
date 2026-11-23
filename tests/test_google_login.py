import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from webstudio.server import Studio

class GoogleLoginTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.app=Studio(Path(self.tmp.name));self.g=self.app.google_login;self.g.client_id='test.apps.googleusercontent.com'
 def tearDown(self):self.app.store.db.close();self.tmp.cleanup()
 def login(self,sub='google-user-1',email='one@gmail.com',password=''):
  browser,nonce=self.g.begin()
  with patch.object(self.g,'verify',return_value={'sub':sub,'email':email,'email_verified':True,'nonce':nonce}):return self.g.finish(browser,'signed-token',password)
 def test_google_returns_same_private_account(self):
  a=self.login();b=self.login();c=self.login('google-user-2','two@gmail.com')
  self.assertEqual(a[2],b[2]);self.assertNotEqual(a[2],c[2])
  p=self.app.store.new_project(a[2],'omni','ru');self.assertIsNone(self.app.store.project(p['id'],c[2]))
 def test_existing_email_requires_password_before_link(self):
  _,_,uid=self.app.auth('one@gmail.com','A-real-password',True)
  with self.assertRaises(ValueError):self.login()
  self.assertEqual(self.login(password='A-real-password')[2],uid)
  self.assertEqual(self.login()[2],uid)
 def test_wrong_nonce_unverified_and_replay_rejected(self):
  for claims in [{'sub':'a','email':'a@gmail.com','email_verified':True,'nonce':'wrong'},{'sub':'a','email':'a@gmail.com','email_verified':False}]:
   browser,nonce=self.g.begin();claims.setdefault('nonce',nonce)
   with patch.object(self.g,'verify',return_value=claims),self.assertRaises(ValueError):self.g.finish(browser,'token')
  browser,nonce=self.g.begin()
  with patch.object(self.g,'verify',return_value={'sub':'a','email':'a@gmail.com','email_verified':True,'nonce':nonce}):
   self.g.finish(browser,'token')
   with self.assertRaises(ValueError):self.g.finish(browser,'token')
 def test_google_no_free_live_balance(self):
  self.app.store.initial_balance=0;self.app.config.mode='live';self.app.config.public_access=True;self.app.config.require_purchase=True
  _,_,uid=self.login()
  from cartoonbot.access import allowed
  self.assertEqual(self.app.store.user(uid)['balance'],0)
  self.assertFalse(allowed(self.app.store,self.app.config,uid))
 def test_google_verifier_failure_does_not_create_user(self):
  browser,nonce=self.g.begin()
  with patch.object(self.g,'verify',side_effect=ValueError('bad signature')),self.assertRaises(ValueError):self.g.finish(browser,'token')
  self.assertEqual(self.app.store.db.execute('SELECT count(*) FROM web_accounts').fetchone()[0],0)
