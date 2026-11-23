import unittest,time
from tests import test_workflow
from cartoonbot.access import allowed
class PaidAccessTests(unittest.TestCase):
 def test_test_credit_is_not_paid_credit_and_owner_exempt(self):
  h=test_workflow.Harness();h.setUp()
  try:
   h.s.user(1);h.cfg.require_purchase=True;h.cfg.admins={2}
   self.assertFalse(allowed(h.s,h.cfg,1))
   self.assertTrue(allowed(h.s,h.cfg,2,500))
   p=h.s.new_project(1,'omni','ru')
   for kind in ('script','edit_script','visuals','edit_visuals','photo_image','analyze','plan_edits'):
    h.e.queue(p,kind)
   self.assertEqual(h.s.db.execute('select count(*) from jobs').fetchone()[0],0)
   h.s.db.execute('insert into ledger values (?,?,?,?,?)',('purchase',1,1000,'purchase',time.time()))
   self.assertTrue(allowed(h.s,h.cfg,1,1000))
   self.assertFalse(allowed(h.s,h.cfg,1,1001))
   h.s.db.execute('update users set reserved=750 where id=1')
   self.assertFalse(allowed(h.s,h.cfg,1,500))
   self.assertTrue(allowed(h.s,h.cfg,1,750,750))
  finally:h.tearDown()

 def test_seedance_prices_include_discounted_thirty_seconds(self):
  from cartoonbot.catalog import seedance_tokens,price_guide
  self.assertEqual(seedance_tokens({'seconds':15},'2.5',{}),700)
  self.assertEqual(seedance_tokens({'seconds':30},'2.5',{}),1300)
  self.assertEqual(seedance_tokens({'seconds':30},'2.5',{'seedance_2.5_30':1250}),1250)
  guide=price_guide('ru',{})
  self.assertIn('700',guide);self.assertIn('1300',guide)

 def test_worker_blocks_unpaid_script_and_visual_jobs_before_provider(self):
  from unittest.mock import Mock
  from cartoonbot.worker import Worker
  h=test_workflow.Harness();h.setUp()
  try:
   h.cfg.require_purchase=True;h.cfg.admins={2};h.s.user(1)
   provider=Mock()
   worker=Worker(h.s,h.cfg,provider,h.e)
   for kind in ('script','visuals','edit_visuals','photo_image'):
    p=h.s.new_project(1,'omni','ru');h.s.enqueue(p,kind)
    worker.run_once()
   self.assertEqual(provider.mock_calls,[])
  finally:h.tearDown()
