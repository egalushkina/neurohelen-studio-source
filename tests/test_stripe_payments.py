import json
import unittest
from decimal import Decimal
from tests import test_workflow
from cartoonbot import payments
from cartoonbot.catalog import PACKAGES, package_usd

class StripePaymentTests(unittest.TestCase):
    event=test_workflow.Harness.event
    tearDown=test_workflow.Harness.tearDown
    def setUp(self):
        test_workflow.Harness.setUp(self)
        self.cfg.payment_provider_token='yk:LIVE:key';self.cfg.payment_receipts='provider';self.cfg.payment_vat_code=1
        self.cfg.stripe_provider_token='stripe:LIVE:key'
        self.event(text='/start')
    def orders(self):
        return [dict(r) for r in self.s.db.execute('SELECT * FROM payment_orders ORDER BY created')]
    def test_different_providers_and_exact_display_prices(self):
        for key,_,tokens,rubles in PACKAGES:
            self.event('pay:world:'+key);r=self.orders()[-1]
            data=payments.invoice_data(self.s,self.cfg,1,r['id'])
            self.assertEqual(r['amount'],int(Decimal(package_usd(key,rubles))*100))
            self.assertEqual(r['tokens'],tokens)
            self.assertEqual(data['currency'],'USD')
            self.assertEqual(data['provider_token'],self.cfg.stripe_provider_token)
            self.assertNotIn('provider_data',data)
        self.event('pay:rf:trial');r=self.orders()[-1]
        data=payments.invoice_data(self.s,self.cfg,1,r['id'])
        self.assertEqual(r['amount'],76000);self.assertEqual(data['currency'],'RUB')
        self.assertEqual(data['provider_token'],self.cfg.payment_provider_token)
        self.assertIn('provider_data',data)
    def test_credit_replay_currency_guard_and_disabled_provider(self):
        self.event('pay:world:standard');r=self.orders()[0];before=self.s.user(1)['balance']
        p={'invoice_payload':r['id'],'currency':'RUB','total_amount':2767,'telegram_payment_charge_id':'tg_usd','provider_payment_charge_id':'ch_usd'}
        self.event(payment=p);self.assertEqual(self.s.user(1)['balance'],before)
        p['currency']='USD'
        self.assertIsNone(payments.checkout_error(self.s,self.cfg,1,p))
        self.cfg.stripe_provider_token=''
        self.assertIsNotNone(payments.checkout_error(self.s,self.cfg,1,p))
        # A confirmed in-flight payment must still credit even if the provider was disabled.
        self.event(payment=p);self.event(payment=p)
        self.assertEqual(self.s.user(1)['balance'],before+2700)
    def test_ui_button_currency_and_no_test_sales(self):
        self.event('package:world:trial')
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertIn('pay:world:trial',str(body));self.assertIn('$9.00',body['text'])
        self.cfg.stripe_provider_token='stripe:TEST:key'
        self.event('pay:world:trial');self.assertEqual(self.orders(),[])
        self.event('pay:rf:trial');self.assertEqual(len(self.orders()),1)
    def test_missing_stripe_and_invalid_region_do_not_use_yookassa(self):
        self.cfg.stripe_provider_token=''
        self.event('pay:world:trial');self.event('pay:invalid:trial')
        self.assertEqual(self.orders(),[])
        self.assertTrue(payments.ready(self.cfg));self.assertFalse(payments.ready(self.cfg,'world'))
