import json
import unittest
from cartoonbot.continuity import reference_contract

class ReferenceContractTests(unittest.TestCase):
    def test_edit_frame_does_not_displace_original_product(self):
        d={'refs':[{'kind':'photo','path':'/tmp/product.png','purpose':''}]}
        text=reference_contract(d,['/tmp/frame.png','/tmp/product.png','/tmp/scene.png'])
        mapping=json.loads(text.split('REFERENCE ATTACHMENT MAP:\n')[1])
        self.assertEqual(mapping[1]['image'],2)
        self.assertTrue(mapping[1]['role'].startswith('USER ORIGINAL'))
        self.assertTrue(mapping[0]['role'].startswith('GENERATED FRAME'))
        self.assertIn('caption does not make an uploaded reference optional',text)
        self.assertIn('original user photos take priority',text)

    def test_no_original_does_not_invent_reference(self):
        self.assertEqual(reference_contract({'refs':[]},['/tmp/frame.png']),'')
