import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from webstudio.server import Studio
class FirebaseTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.app=Studio(Path(self.tmp.name));self.f=self.app.firebase_login;self.f.enabled=True;self.f.config={'owner_migration_emails':['owner@gmail.com']}
 def tearDown(self):self.app.store.db.close();self.tmp.cleanup()
 def login(self,email='new@gmail.com',sub='firebase-1',verified=True,provider='google.com',password=''):
  claims={'sub':sub,'email':email,'email_verified':verified,'auth_time':time.time(),'firebase':{'sign_in_provider':provider}}
  with patch.object(self.f,'verify',return_value=claims):return self.f.finish('firebase-token',password)
 def test_owner_migration_preserves_balance_projects(self):
  _,_,uid=self.app.auth('owner@gmail.com','Old-password-123',True);self.app.config.admins={uid}
  self.app.store.db.execute('UPDATE users SET balance=1000000 WHERE id=?',(uid,));p=self.app.store.new_project(uid,'omni','ru')
  self.assertEqual(self.login('owner@gmail.com')[2],uid)
  self.assertEqual(self.app.store.user(uid)['balance'],1000000);self.assertIsNotNone(self.app.store.project(p['id'],uid))
 def test_unverified_email_cannot_claim_owner(self):
  _,_,uid=self.app.auth('owner@gmail.com','Old-password-123',True);self.app.config.admins={uid}
  with self.assertRaises(ValueError):self.login('owner@gmail.com',verified=False)
  with self.assertRaises(ValueError):self.login('owner@gmail.com',provider='password')
 def test_user_isolation_zero_balance(self):
  self.app.store.initial_balance=0
  a=self.login()[2];b=self.login('other@gmail.com','firebase-2')[2]
  self.assertNotEqual(a,b);self.assertEqual(self.app.store.user(a)['balance'],0)
  self.assertEqual(self.login()[2],a)
  p=self.app.store.new_project(a,'omni','ru');self.assertIsNone(self.app.store.project(p['id'],b))
 def test_existing_user_requires_old_password(self):
  _,_,uid=self.app.auth('old@gmail.com','Old-password-123',True)
  with self.assertRaises(ValueError):self.login('old@gmail.com')
  self.assertEqual(self.login('old@gmail.com',password='Old-password-123')[2],uid)
 def test_bad_token_rejected(self):
  with patch.object(self.f,'verify',side_effect=ValueError()),self.assertRaises(ValueError):self.f.finish('bad')
