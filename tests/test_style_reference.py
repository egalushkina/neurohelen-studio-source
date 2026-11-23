import unittest
from cartoonbot.style_reference import image_inputs
from cartoonbot.continuity import reference_contract, style_contract
from cartoonbot.seedance_prompts import references
class StyleReferenceTests(unittest.TestCase):
 def test_style_attachment_is_separate_from_identity_and_video(self):
  d={'style':'reference','style_reference':{'path':'style.png'},'style_profile':{'instructions':'Clay materials'},'refs':[{'kind':'photo','path':'person.png'}], 'script':{'assets':[{'id':1,'name':'Hero','description':'Identity'}]},'images':{'1':{'path':'approved.png'}},'recreate':True}
  refs,rule=image_inputs(d,['person.png'])
  self.assertEqual(refs,['person.png','style.png']);self.assertIn('Image 2 is STYLE ONLY',rule)
  self.assertIn('STYLE ONLY',reference_contract(d,refs));self.assertIn('Clay materials',style_contract(d))
  self.assertEqual(references(d)[0],['approved.png'])
  d['style']='anime';self.assertEqual(image_inputs(d,['person.png']),(['person.png'],''))
