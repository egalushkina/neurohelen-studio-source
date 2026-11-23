import unittest
from cartoonbot.continuity import style_contract
from cartoonbot.engine import STYLES
from cartoonbot.i18n import tr
from cartoonbot.omni_blueprint import STYLE_NAMES
from cartoonbot.seedance_continuity import supplied_prompt

class RealismTests(unittest.TestCase):
    def test_selection_and_identity(self):
        self.assertIn('realism',STYLES)
        for lang in ('ru','en','uk'): self.assertNotEqual(tr(lang,'realism'),'realism')
        text=style_contract({'style':'realism'})
        self.assertIn('CINEMATIC PHOTOREALISM',text)
        self.assertIn('actual age',text)
        self.assertIn('realism does not turn them into humans',text)
        self.assertNotIn('Rebuild every referenced',text)
        self.assertEqual(style_contract({'style':'realism','visual_source':'upload'}),'')
        self.assertIn('photorealism',STYLE_NAMES['realism'])
    def test_seedance_preserves_realism_and_speech(self):
        d={'style':'realism','seconds':15,'lang':'ru','script':{'assets':[], 'scenes':[{'id':1,'start':0,'end':15,'action':'A cat walks.','camera':'Tracking shot.','dialogue':'Кот: Привет!'}]}}
        result=supplied_prompt(d,[])
        self.assertIn('Cinematic photorealism',result)
        self.assertIn('Кот: Привет!',result)
