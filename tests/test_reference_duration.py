import unittest
from unittest.mock import Mock,patch
from cartoonbot.providers import LiveProvider
class ReferenceDurationTests(unittest.TestCase):
 def test_reference_keeps_full_duration(self):
  for seconds,count in [(9,1),(39.35,4),(60,6)]:
   p=Mock();p.structured.return_value={'scenes':[{}]*count}
   d={'route':'omni','lang':'ru','seconds':10,'auto_duration':True,'omni_source_flow':True,'source':{'seconds':seconds},'refs':[]}
   with patch('cartoonbot.cast_variation.direction',return_value={}),patch('cartoonbot.providers.validate_script',side_effect=lambda r,d:r):
    LiveProvider.script(p,{},d)
   args=p.structured.call_args.args
   self.assertEqual(args[2]['seconds'],count*10)
   self.assertFalse(args[2]['auto_duration'])
   self.assertEqual(args[3]['properties']['scenes']['minItems'],count)
