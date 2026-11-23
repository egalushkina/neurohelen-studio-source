import json
import unittest
from tests.test_workflow import Harness
from cartoonbot import payments
from cartoonbot.telegram import Telegram
from unittest.mock import Mock

class PaymentTests(unittest.TestCase):
    event=Harness.event
    tearDown=Harness.tearDown
    def setUp(self):
        Harness.setUp(self);self.cfg.payment_provider_token='123:LIVE:fake';self.cfg.payment_receipts='external'
        self.event(text='/start')
    def order(self):
        self.event('pay:trial')
        return self.s.db.execute('SELECT * FROM payment_orders').fetchone()['id']
    def paid(self,oid,**kw):
        return dict(invoice_payload=oid,currency='RUB',total_amount=76000,telegram_payment_charge_id='tg1',provider_payment_charge_id='yk1',**kw)
    def test_credit_once_and_no_precheckout_credit(self):
        oid=self.order();before=self.s.user(1)['balance'];p=self.paid(oid)
        self.assertIsNone(payments.checkout_error(self.s,self.cfg,1,p))
        self.assertEqual(self.s.user(1)['balance'],before)
        self.event(payment=p);self.event(payment=p)
        self.assertEqual(self.s.user(1)['balance'],before+1000)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM ledger WHERE kind='purchase'").fetchone()[0],1)
    def test_mismatch_and_wrong_user(self):
        oid=self.order();p=self.paid(oid);p['total_amount']=1
        self.assertIsNotNone(payments.checkout_error(self.s,self.cfg,1,p))
        self.event(payment=p);p['total_amount']=76000
        self.event(uid=2,payment=p)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM ledger').fetchone()[0],0)
    def test_reuse_and_receipt(self):
        oid=self.order();self.assertEqual(self.order(),oid)
        self.cfg.payment_receipts='provider';self.cfg.payment_vat_code=1
        data=payments.invoice_data(self.s,self.cfg,1,oid)
        self.assertEqual(json.loads(data['prices'])[0]['amount'],76000)
        self.assertEqual(json.loads(data['provider_data'])['receipt']['items'][0]['vat_code'],1)
        self.assertEqual(data['need_email'],'true')
    def test_precheckout_transport(self):
        oid=self.order();tg=Telegram(self.cfg,self.s,self.e);tg.call=Mock()
        try:
            tg.event({'update_id':100,'pre_checkout_query':dict(self.paid(oid),id='q1',**{'from':{'id':1}})})
            self.assertEqual(tg.call.call_args.args[0],'answerPreCheckoutQuery')
            self.assertEqual(tg.call.call_args.args[1]['ok'],'true')
        finally:tg.client.close()
    def test_only_one_checkout_can_start_for_same_order(self):
        oid=self.order();q=dict(self.paid(oid),id='q1')
        with self.s.transaction():
            self.assertIsNone(payments.approve_checkout(self.s,self.cfg,1,q))
            self.assertIsNone(payments.approve_checkout(self.s,self.cfg,1,q))
            self.assertIsNotNone(payments.approve_checkout(self.s,self.cfg,1,dict(q,id='q2')))
        self.event(payment=self.paid(oid))
        self.assertEqual(self.s.db.execute("SELECT status FROM payment_orders WHERE id=?",(oid,)).fetchone()[0],'paid')
