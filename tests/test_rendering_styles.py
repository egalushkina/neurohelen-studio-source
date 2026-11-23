import unittest
from cartoonbot.continuity import style_contract, reference_contract

class RenderingStyles(unittest.TestCase):
    def test_all_styles_apply_to_all_subjects_and_override_reference_rendering(self):
        for style,word in [('3d','STYLIZED 3D'),('anime','2D JAPANESE ANIME'),('2d','2D CARTOON'),('clay','CLAY STOP-MOTION'),('watercolor','WATERCOLOR')]:
            with self.subTest(style=style):
                contract=style_contract({'style':style})
                self.assertIn(word,contract)
                self.assertIn('EVERY character, object and environment',contract)
                self.assertIn('earlier generated frames',contract)
                self.assertIn('without replacing the person',contract)
    def test_uploaded_scene_frames_are_not_restyled(self):
        self.assertEqual(style_contract({'style':'anime','visual_source':'upload'}),'')
    def test_original_face_overrides_invented_description_not_selected_rendering(self):
        contract=reference_contract({'refs':[{'kind':'photo','path':'original.png'}]},['frame.png','original.png'])
        self.assertIn('ignore conflicting invented',contract)
        self.assertIn('redraw',contract)
        self.assertIn('USER ORIGINAL — identity authority',contract)
