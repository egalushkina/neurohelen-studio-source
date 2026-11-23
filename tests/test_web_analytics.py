import time
import unittest
from tests import test_webstudio

class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.h=test_webstudio.BrowserTests();self.h.setUp()
    def tearDown(self):self.h.tearDown()
    def test_owner_access_and_paid_only_revenue(self):
        h=self.h
        self.assertEqual(h.client.get('/api/analytics').status_code,401)
        h.register()
        self.assertEqual(h.client.get('/api/analytics').status_code,403)
        uid=h.app.store.db.execute('SELECT uid FROM web_accounts').fetchone()[0]
        h.app.config.admins.add(uid)
        self.assertTrue(h.client.get('/api/state').json()['is_owner'])
        db=h.app.store.db
        for oid,user,status,live,amount in [('paid',123,'paid',1,900),('test',123,'paid',0,500),('pending',123,'pending',1,600),('owner',uid,'paid',1,700)]:
            db.execute('INSERT INTO web_orders(id,uid,package,tokens,amount,currency,status,live,created) VALUES(?,?,?,?,?,?,?,?,?)',(oid,user,'trial',1000,amount,'USD',status,live,time.time()))
        result=h.client.get('/api/analytics');self.assertEqual(result.status_code,200)
        d=result.json();self.assertEqual(d['accounts'],0);self.assertEqual(d['buyers'],1)
        self.assertEqual(d['orders'][0]['minor'],900);self.assertEqual(d['orders'][0]['purchases'],1)
        self.assertNotIn('email',result.text)
    def test_public_counts_no_sensitive_referrer_or_bot(self):
        a=self.h.app.analytics
        a.page('/ru/','https://example.com/private?token=secret','Mozilla/5.0')
        a.page('/ru/','','Googlebot')
        d=a.report();self.assertEqual(d['sources'],[{'source':'example.com','hits':1}])
        self.assertEqual(d['views'][0]['hits'],1)
