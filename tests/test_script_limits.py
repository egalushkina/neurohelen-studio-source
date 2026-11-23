import unittest
from cartoonbot.schemas import validate_script
class ScriptLimits(unittest.TestCase):
 def test_detailed_seedance_prompt_and_upper_bound(self):
  s={'title':'Two locations','assets':[{'id':1,'name':'Hero','description':'Identity'}], 'scenes':[{'id':1,'start':0,'end':15,'action':'Action','dialogue':'','camera':'Cut','image_prompt':'Portrait','video_prompt':'x'*4184}]}
  d={'route':'seedance','seconds':15}
  self.assertIs(validate_script(s,d),s)
  s['scenes'][0]['video_prompt']='x'*12001
  with self.assertRaisesRegex(ValueError,'script_too_long'): validate_script(s,d)

 def test_imported_script_keeps_all_sections_beyond_six_and_sixty_seconds(self):
  from cartoonbot.supplied_script import scene_sections
  source='\n'.join(f'Сцена {n}\nДействие {i}' for i,n in enumerate([1,2,3,4,5,5,6,7]))+'\nФИНАЛ\nКонец'
  sections=scene_sections(source)
  self.assertEqual(len(sections),9)
  scenes=[dict(id=i,start=(i-1)*10,end=i*10,action=section,dialogue='',camera='',image_prompt='',video_prompt='') for i,section in enumerate(sections,1)]
  script={'assets':[{'id':1}], 'scenes':scenes}
  d={'route':'omni','auto_duration':True,'max_seconds':60,'supplied_script':source}
  self.assertIs(validate_script(script,d),script)
  self.assertEqual(d['seconds'],90)
  self.assertEqual([s['action'] for s in scenes],sections)
  # Revisions of the approved long screenplay remain valid too.
  d['auto_duration']=False
  validate_script(script,d)

 def test_empty_script_still_rejected(self):
  with self.assertRaisesRegex(ValueError,'invalid_scenes'):
   validate_script({'assets':[{'id':1}],'scenes':[]},{'route':'omni'})
