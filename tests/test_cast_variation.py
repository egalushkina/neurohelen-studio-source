import unittest
from tests import test_workflow
from cartoonbot.cast_variation import direction

class VariationTests(unittest.TestCase):
    def setUp(self):
        self.h=test_workflow.Harness();self.h.setUp();self.s=self.h.s
        self.s.user(1);self.s.user(2)
    def tearDown(self):self.h.tearDown()
    def test_stable_within_project_and_preserves_approved_cast(self):
        p=self.s.new_project(1,'omni','ru');job={'project_id':p['id']}
        first=direction(self.s,job,p['data'])
        self.assertEqual(first,direction(self.s,job,p['data']))
        p['data']['script']={'assets':[{'id':1,'description':'Approved identity'}]}
        self.assertEqual(direction(self.s,job,p['data']),{'mode':'preserve_current_cast'})
    def test_recent_cast_is_owner_scoped_and_excludes_current(self):
        for uid,desc in ((1,'Previous own design'),(2,'Private other design')):
            p=self.s.new_project(uid,'omni','ru');p['data']['script']={'assets':[{'description':desc}]};self.s.save(p)
        new=self.s.new_project(1,'omni','ru')
        result=direction(self.s,{'project_id':new['id']},new['data'])
        self.assertNotIn('Previous own design',str(result))
        self.assertNotIn('Private other design',str(result))
        self.assertIn('hair_color',result)
        self.assertEqual(new['data']['cast_design_direction'],result)
    def test_supplied_identity_and_references_have_priority_in_instructions(self):
        from cartoonbot.cast_variation import RULES
        self.assertIn('ALWAYS take priority',RULES)
        self.assertIn('Never randomize a reference subject',RULES)
