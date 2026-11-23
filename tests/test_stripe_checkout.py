import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import Mock,patch
from tests import test_workflow
from cartoonbot import stripe_checkout as stripe

class StripeCheckoutTests(unittest.TestCase):
    event=test_workflow.Harness.event
    tearDown=test_workflow.Harness.tearDown
    def setUp(self):
        test_workflow.Harness.setUp(self)
        self.cfg.stripe_secret_key='sk_live_fake';self.cfg.stripe_webhook_secret='whsec_fake'
        self.event(text='/start');self.event('pay:world:trial')
        self.oid=self.s.db.execute('SELECT id FROM payment_orders').fetchone()[0]
    def session(self):
        return dict(id='cs_live_fake',url='https://checkout.stripe.com/c/pay/fake',livemode=True)
    def make_url(self):
        with patch('cartoonbot.stripe_checkout.httpx.post',return_value=Mock(status_code=200,json=lambda:self.session())) as call:
            url=stripe.checkout_url(self.s,self.cfg,1,self.oid)
            self.assertEqual(stripe.checkout_url(self.s,self.cfg,1,self.oid),url)
            self.assertEqual(call.call_count,1)
            self.assertEqual(call.call_args.kwargs['data']['line_items[0][price_data][unit_amount]'],'900')
            self.assertEqual(call.call_args.kwargs['headers']['Idempotency-Key'],'cartoon-order-'+self.oid)
    def event_body(self):
        return {'type':'checkout.session.completed','livemode':True,'data':{'object':{
            'id':'cs_live_fake','mode':'payment','status':'complete','payment_status':'paid',
            'client_reference_id':self.oid,'metadata':{'order_id':self.oid},'currency':'usd',
            'amount_total':900,'payment_intent':'pi_fake','livemode':True}}}
    def send(self,body,secret='whsec_fake',timestamp=None):
        raw=json.dumps(body).encode();t=str(int(time.time()) if timestamp is None else timestamp)
        sig=hmac.new(secret.encode(),t.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        return stripe.receive(self.s,self.cfg,raw,'t='+t+',v1='+sig)
    def test_signed_paid_once(self):
        self.make_url();before=self.s.user(1)['balance'];body=self.event_body()
        self.assertEqual(self.send(body),200);self.assertEqual(self.send(body),200)
        body['type']='checkout.session.async_payment_succeeded'
        self.assertEqual(self.send(body),200)
        self.assertEqual(self.s.user(1)['balance'],before+1000)
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM ledger WHERE kind='purchase'").fetchone()[0],1)
    def test_unpaid_bad_signature_wrong_amount_mode_do_not_credit(self):
        self.make_url();before=self.s.user(1)['balance'];body=self.event_body()
        self.assertEqual(self.send(body,secret='wrong'),400)
        self.assertEqual(self.send(body,timestamp=int(time.time())-600),400)
        for key,value in [('payment_status','unpaid'),('amount_total',1),('currency','rub'),('livemode',False),('id','cs_other')]:
            b=self.event_body();b['data']['object'][key]=value
            self.assertIn(self.send(b),(200,400))
        self.assertEqual(self.s.user(1)['balance'],before)
    def test_session_race_retry_then_fulfill(self):
        self.assertEqual(self.send(self.event_body()),503)
        self.make_url();self.assertEqual(self.send(self.event_body()),200)
    def test_wrong_owner_and_no_secret(self):
        self.assertIsNone(stripe.checkout_url(self.s,self.cfg,999,self.oid))
        self.cfg.stripe_webhook_secret=''
        self.assertEqual(self.send(self.event_body()),503)
