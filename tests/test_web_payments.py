import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from webstudio.server import Studio,Problem
from cartoonbot.access import allowed

class PaymentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.app=Studio(Path(self.tmp.name));self.s=self.app.store
  self.app.config.require_purchase=True
  self.s.user(1);self.s.user(2)
  self.pay=self.app.payments;self.pay.config={'stripe_key':'sk_live_placeholder','yookassa_shop_id':'123','yookassa_key':'placeholder','receipts':'external'}
 def tearDown(self):self.s.db.close();self.tmp.cleanup()
 def stripe(self,*args,**kw):
  return {'id':'cs_example','url':'https://checkout.stripe.com/example','livemode':True}
 def paid(self,oid,**kw):
  row=self.s.db.execute('SELECT * FROM web_orders WHERE id=?',(oid,)).fetchone()
  return dict(id=row['remote'],status='complete',payment_status='paid',amount_total=row['amount'],currency='usd',livemode=True,mode='payment',client_reference_id=oid,metadata={'studio_order':oid},**kw)
 def test_verified_payment_credits_once_and_unlocks(self):
  with patch.object(self.pay,'request',side_effect=self.stripe):a=self.pay.create(1,'trial','international','http://127.0.0.1:8090')
  before=self.s.user(1)['balance'];self.assertFalse(allowed(self.s,self.app.config,1))
  with patch.object(self.pay,'request',return_value=self.paid(a['order'])):
   self.assertEqual(self.pay.check(1,a['order'])['status'],'paid');self.pay.check(1,a['order'])
  self.assertEqual(self.s.user(1)['balance'],before+1000);self.assertTrue(allowed(self.s,self.app.config,1))
 def test_tampered_amount_and_other_user_do_not_credit(self):
  with patch.object(self.pay,'request',side_effect=self.stripe):a=self.pay.create(1,'trial','international','http://127.0.0.1:8090')
  with self.assertRaises(ValueError):self.pay.check(2,a['order'])
  obj=self.paid(a['order']);obj['amount_total']=1
  with patch.object(self.pay,'request',return_value=obj),self.assertRaises(ValueError):self.pay.check(1,a['order'])
  self.assertFalse(allowed(self.s,self.app.config,1))
 def test_pending_not_paid_and_repeat_create_reuses(self):
  with patch.object(self.pay,'request',side_effect=self.stripe) as req:
   a=self.pay.create(1,'trial','international','http://127.0.0.1:8090');b=self.pay.create(1,'trial','international','http://127.0.0.1:8090')
   self.assertEqual(a,b);self.assertEqual(req.call_count,1)
  with patch.object(self.pay,'request',return_value={'id':'cs_example','status':'open','payment_status':'unpaid'}):self.assertEqual(self.pay.check(1,a['order'])['status'],'pending')
  self.assertFalse(allowed(self.s,self.app.config,1))
 def test_gate_preserves_questionnaire_and_resume(self):
  p=self.s.new_project(1,'omni','ru');p['data']['brief']['idea']='Кот готовит завтрак';p['state']='intake'
  self.app.engine.queue(p,'script')
  saved=self.s.project(p['id']);self.assertEqual(saved['data']['brief']['idea'],'Кот готовит завтрак')
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
  with self.assertRaises(Problem):self.app.dispatch(1,action='web:resume')
  with self.assertRaises(Problem):self.s.enqueue(saved,'visuals')
  self.s.db.execute("INSERT INTO ledger VALUES ('paid',1,1000,'purchase',?)",(time.time(),))
  self.app.dispatch(1,action='web:resume')
  self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)
 def test_owner_exempt_other_accounts_not(self):
  self.app.config.admins={1};self.assertTrue(allowed(self.s,self.app.config,1));self.assertFalse(allowed(self.s,self.app.config,2))
 def test_yookassa_success(self):
  with patch.object(self.pay,'request',return_value={'id':'yk_example','test':False,'confirmation':{'confirmation_url':'https://yookassa.ru/pay/example'}}):a=self.pay.create(1,'trial','ru','http://127.0.0.1:8090')
  obj={'id':'yk_example','test':False,'paid':True,'status':'succeeded','metadata':{'studio_order':a['order']},'amount':{'value':'760.00','currency':'RUB'}}
  with patch.object(self.pay,'request',return_value=obj):self.pay.check(1,a['order']);self.pay.check(1,a['order'])
  self.assertEqual(self.s.db.execute("SELECT count(*) FROM ledger WHERE kind='purchase'").fetchone()[0],1)

 def test_new_origin_does_not_reuse_old_return_url(self):
  def response(provider,method,suffix,**kwargs):
   oid=kwargs['data']['client_reference_id']
   return {'id':'cs_'+oid,'url':'https://checkout.stripe.com/'+oid,'livemode':True}
  with patch.object(self.pay,'request',side_effect=response):
   old=self.pay.create(1,'trial','international','http://127.0.0.1:8090')
   new=self.pay.create(1,'trial','international','https://studio.neurohelen.com')
  self.assertNotEqual(old['order'],new['order'])
  row=self.s.db.execute('SELECT return_url FROM web_orders WHERE id=?',(new['order'],)).fetchone()
  self.assertTrue(row[0].startswith('https://studio.neurohelen.com/'))
