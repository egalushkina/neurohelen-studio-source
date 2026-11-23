import json
import unittest
from tests import test_workflow
from cartoonbot.payments import PAYMENT_CHECK_AMOUNTS,invoice_data
from cartoonbot.stripe_checkout import checkout_url
from unittest.mock import Mock,patch

class PaymentChecks(unittest.TestCase):
    event=test_workflow.Harness.event
    tearDown=test_workflow.Harness.tearDown
    def setUp(self):
        test_workflow.Harness.setUp(self)
        enabled=patch.dict(PAYMENT_CHECK_AMOUNTS,{'world':100,'rf':10000})
        enabled.start();self.addCleanup(enabled.stop)
        self.cfg.admins={1};self.cfg.stripe_secret_key='rk_live_fake';self.cfg.stripe_webhook_secret='whsec_fake'
        self.cfg.payment_provider_token='yk:LIVE:fake';self.cfg.payment_receipts='external'
    def test_owner_amount_and_credit_only_after_payment(self):
        self.event('pay:world:payment_check');r=self.s.db.execute('SELECT * FROM payment_orders').fetchone()
        self.assertEqual((r['amount'],r['currency'],r['tokens']),(100,'USD',1))
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM ledger').fetchone()[0],0)
        with patch('cartoonbot.stripe_checkout.httpx.post',return_value=Mock(status_code=200,json=lambda:{'id':'cs_test','url':'https://checkout.stripe.com/c/pay/test','livemode':True})) as post:
            checkout_url(self.s,self.cfg,1,r['id'])
            self.assertEqual(post.call_args.kwargs['data']['line_items[0][price_data][unit_amount]'],'100')
        if 'rf' in PAYMENT_CHECK_AMOUNTS:
            self.event('pay:rf:payment_check');r=self.s.db.execute("SELECT * FROM payment_orders WHERE currency='RUB'").fetchone()
            self.assertEqual(r['amount'],PAYMENT_CHECK_AMOUNTS['rf'])
            data=invoice_data(self.s,self.cfg,1,r['id'])
            self.assertEqual(json.loads(data['prices'])[0]['amount'],r['amount'])
            p={'invoice_payload':r['id'],'currency':'RUB','total_amount':r['amount'],'telegram_payment_charge_id':'check_tg','provider_payment_charge_id':'check_yk'}
            before=self.s.user(1)['balance'];self.event(payment=p);self.event(payment=p)
            self.assertEqual(self.s.user(1)['balance'],before+1)
    def test_other_users_can_buy_but_cannot_pay_someone_elses_order(self):
        self.event('token_shop',uid=2)
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertNotIn('payment_check',str(body))
        self.event('pay:world:payment_check',uid=2);self.event('pay:rf:payment_check',uid=2)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM payment_orders').fetchone()[0],2)
        r=self.s.db.execute("SELECT * FROM payment_orders WHERE user_id=2 AND currency='RUB'").fetchone()
        from cartoonbot.payments import checkout_error
        q={'invoice_payload':r['id'],'currency':'RUB','total_amount':10000}
        self.assertIsNone(checkout_error(self.s,self.cfg,2,q))
        self.assertIsNotNone(checkout_error(self.s,self.cfg,3,q))
        q.update(telegram_payment_charge_id='public_tg',provider_payment_charge_id='public_yk')
        before=self.s.user(2)['balance']
        self.event(uid=2,payment=q);self.event(uid=2,payment=q)
        self.assertEqual(self.s.user(2)['balance'],before+1)
        self.event('token_shop',uid=1)
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertNotIn('payment_check',str(body))

    def test_closed_checks_reject_old_callbacks(self):
        with patch.dict(PAYMENT_CHECK_AMOUNTS,{},clear=True):
            for uid in (1,2):
                self.event('token_shop',uid=uid)
                body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
                self.assertNotIn('payment_check',str(body))
                self.event('pay:world:payment_check',uid=uid)
                self.event('pay:rf:payment_check',uid=uid)
            self.assertEqual(self.s.db.execute('SELECT count(*) FROM payment_orders').fetchone()[0],0)
