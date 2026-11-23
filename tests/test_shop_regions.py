import json
import unittest
from tests import test_workflow

class ShopRegionTests(unittest.TestCase):
    def test_regions_prices_counts_and_back(self):
        h=test_workflow.Harness();h.setUp()
        try:
            def latest():
                return json.loads(h.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
            h.event(text='/start',language='ru')
            h.event('token_shop');self.assertIn('token_shop:world',str(latest()))
            for region,price in [('rf','760 ₽'),('world','$9.00')]:
                h.event('token_shop:'+region)
                body=latest();self.assertNotIn('Omni Flash',body['text']);self.assertNotIn('Seedance',body['text']);self.assertIn(price,str(body));self.assertIn('1 000 токенов',str(body))
                self.assertNotIn('42 сцен',str(body));self.assertIn('10 500',body['text'])
                h.event('package:'+region+':trial')
                body=latest();self.assertIn(price,body['text']);self.assertIn('token_shop:'+region,str(body))
            self.assertEqual(h.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
            self.assertEqual(h.s.db.execute('SELECT count(*) FROM ledger').fetchone()[0],0)
        finally:h.tearDown()
