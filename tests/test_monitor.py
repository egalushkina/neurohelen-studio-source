import unittest
from deploy.monitor import transition
class MonitorTests(unittest.TestCase):
 def test_alert_and_recovery_once(self):
  s,e=transition({},['failure']);self.assertIsNone(e)
  s,e=transition(s,['failure']);self.assertEqual(e,'down')
  s,e=transition(s,['failure']);self.assertIsNone(e)
  s,e=transition(s,[]);self.assertEqual(e,'up')
  s,e=transition(s,[]);self.assertIsNone(e)
 def test_transient_failure(self):
  s,e=transition({},['failure']);s,e=transition(s,[])
  self.assertIsNone(e);self.assertFalse(s['active'])
