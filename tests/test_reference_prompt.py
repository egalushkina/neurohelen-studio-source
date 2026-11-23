import unittest
from cartoonbot.seedance_prompts import finalize_reference_prompt

class ReferencePromptTests(unittest.TestCase):
    def test_photo_numbers_map_to_actual_asset_numbers(self):
        d={'recreate':True,'script':{'assets':[{'id':2,'reference_photo_ids':[1]}]}}
        p=finalize_reference_prompt('wife reference photo 1; image 3 is stranger; женщина с фото 1',d)
        self.assertIn('wife image 2',p)
        self.assertIn('с image 2',p)
        self.assertNotIn('photo 1',p)
        self.assertIn('image 3 is stranger',p)
        self.assertIn('Preserve the established haircut',p)
    def test_missing_mapping_does_not_invent_image_number(self):
        p=finalize_reference_prompt('reference photo 5',{'recreate':True})
        self.assertNotIn('image 5',p)
        self.assertIn('corresponding approved character image',p)
