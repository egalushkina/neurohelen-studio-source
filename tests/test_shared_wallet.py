import os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from cartoonbot.store import Store
from cartoonbot.access import paid_available
class SharedWalletTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.web=self.open('web',-1,300);self.tg=self.open('telegram',123,700)
 def open(self,ns,uid,amount):
  with patch.dict(os.environ,{'STUDIO_WALLET_PATH':str(self.root/'wallet.sqlite3'),'STUDIO_WALLET_NAMESPACE':ns}):s=Store(self.root/(ns+'.sqlite3'),initial_balance=0)
  with s.transaction():
   s.user(uid);s.credit(uid,amount,'purchase1')
  return s
 def tearDown(self):
  self.web.db.close();self.tg.db.close();self.tmp.cleanup()
 def link(self):
  token=self.web.wallet.begin(-1)
  with self.tg.transaction():self.tg.wallet.claim(123,token)
  self.web.wallet.confirm(-1,123)
 def test_merge_credit_reserve_settle(self):
  self.link();self.assertEqual(self.web.user(-1)['balance'],1000);self.assertEqual(self.tg.user(123)['balance'],1000)
  with self.tg.transaction():self.tg.wallet.change(123,'r',reserve=800)
  self.assertEqual(paid_available(self.web,-1),200)
  with self.assertRaises(ValueError):
   with self.web.transaction():self.web.wallet.change(-1,'r2',reserve=250)
  with self.tg.transaction():self.tg.wallet.change(123,'s',amount=-800,reserve=-800,paid=-800)
  with self.web.transaction():self.web.credit(-1,100,'newpurchase')
  self.assertEqual(self.tg.user(123)['balance'],300)
 def test_single_use_and_account_authority(self):
  token=self.web.wallet.begin(-1)
  with self.tg.transaction():self.tg.wallet.claim(123,token)
  with self.assertRaises(ValueError):self.web.wallet.confirm(-2,123)
  self.web.wallet.confirm(-1,123)
  with self.assertRaises(ValueError):self.web.wallet.confirm(-1,123)
  with self.assertRaises(ValueError):
   with self.tg.transaction():self.tg.wallet.claim(123,token)
 def test_rollback_is_atomic(self):
  self.link()
  try:
   with self.web.transaction():
    self.web.credit(-1,500,'bad');raise RuntimeError()
  except RuntimeError:pass
  self.assertEqual(self.tg.user(123)['balance'],1000)
 def test_expiry_and_reserved(self):
  token=self.web.wallet.begin(-1)
  with self.tg.transaction():self.tg.wallet.claim(123,token);self.tg.wallet.change(123,'r',reserve=1)
  with self.assertRaises(ValueError):self.web.wallet.confirm(-1,123)
 def test_local_journals_and_paid_import(self):
  self.assertEqual(self.web.db.execute('PRAGMA journal_mode').fetchone()[0],'delete')
  self.assertEqual(paid_available(self.web,-1),300)
 def test_expired_and_replaced_link(self):
  token=self.web.wallet.begin(-1)
  self.web.db.execute('UPDATE wallet.links SET expires=0')
  with self.assertRaises(ValueError):self.tg.wallet.claim(123,token)
  old=self.web.wallet.begin(-1);new=self.web.wallet.begin(-1)
  with self.assertRaises(ValueError):self.tg.wallet.claim(123,old)
  self.tg.wallet.claim(123,new)
  self.web.wallet.confirm(-1,123)
 def test_duplicate_credit(self):
  self.link()
  with self.web.transaction():self.web.credit(-1,100,'once')
  with self.web.transaction():self.web.credit(-1,100,'once')
  self.assertEqual(self.tg.user(123)['balance'],1100)
 def test_simultaneous_reservations(self):
  import threading
  self.link();barrier=threading.Barrier(2);results=[]
  def reserve(store,uid):
   barrier.wait()
   try:
    with store.transaction():store.wallet.change(uid,'parallel',reserve=700)
    results.append('ok')
   except ValueError:results.append('insufficient')
  threads=[threading.Thread(target=reserve,args=(self.web,-1)),threading.Thread(target=reserve,args=(self.tg,123))]
  for t in threads:t.start()
  for t in threads:t.join(10);self.assertFalse(t.is_alive())
  self.assertCountEqual(results,['ok','insufficient'])
  self.assertEqual(self.web.user(-1)['reserved'],700)
 def test_legacy_paid_balance_migration_once(self):
  path=self.root/'legacy.sqlite3';s=Store(path,initial_balance=0)
  with s.transaction():
   s.user(-9);s.db.execute('UPDATE users SET balance=850,reserved=50 WHERE id=-9')
   s.db.execute("INSERT INTO ledger VALUES('legacy',-9,1000,'purchase',0)")
   s.db.execute("INSERT INTO ledger VALUES('spent',-9,-150,'generation',0)")
  s.db.close()
  with patch.dict(os.environ,{'STUDIO_WALLET_PATH':str(self.root/'wallet.sqlite3'),'STUDIO_WALLET_NAMESPACE':'web'}):s=Store(path,initial_balance=0)
  self.assertEqual(s.user(-9)['balance'],850);self.assertEqual(paid_available(s,-9),800)
  self.assertEqual(s.user(-9)['balance'],850);s.db.close()
 def test_bot_requires_explicit_account_confirmation(self):
  from cartoonbot.engine import Engine
  from cartoonbot.config import Config
  token=self.web.wallet.begin(-1,'owner@example.test');engine=Engine(self.tg,Config())
  engine.handle({'id':9001,'user_id':123,'text':'/start link_'+token})
  self.assertIsNone(self.web.wallet.status(-1)['pending_telegram_id'])
  import json
  message=json.loads(self.tg.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
  self.assertIn('owner@example.test',message['text'])
  engine.handle({'id':9002,'user_id':123,'action':'wallet_link:'+token})
  self.assertEqual(self.web.wallet.status(-1)['pending_telegram_id'],123)
  self.web.wallet.confirm(-1,123)
  self.assertEqual(self.tg.user(123)['balance'],1000)
