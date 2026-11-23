import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from cartoonbot.seedance_continuity import compile_prompt, render_plan

class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.plan={'shots':[
            {'start':0,'end':10,'location':'park','cast':[{'image':1,'wardrobe':'emerald jacket'},{'image':2,'wardrobe':'gray bomber'},{'image':3,'wardrobe':'red top'}],'action':'image 1 sees image 2 beside image 3','camera':'wide','dialogue':''},
            {'start':10,'end':15,'location':'bedroom','cast':[{'image':1,'wardrobe':'navy pajamas'},{'image':2,'wardrobe':'cream shirt, green pajamas'}],'action':'image 1 wakes beside image 2','camera':'hard cut','dialogue':''}], 'audio':'ambience only'}

    def test_audio_language_survives_fallback_without_dialogue_changes(self):
        from cartoonbot.seedance_continuity import supplied_prompt
        d={'audio_mode':'ru','seconds':15,'recreate':True,'seedance_reference_mode':'direct',
           'refs':[{'kind':'video'}], 'script':{'assets':[], 'scenes':[
           {'start':0,'end':15,'action':'Open the jar','camera':'Close up','dialogue':'Женщина: Милый, можешь открыть?'}]}}
        result=supplied_prompt(d,[])
        self.assertIn('All speech must be in Russian ONLY',result)
        self.assertIn('Женщина: Милый, можешь открыть?',result)
        self.assertIn('never translate it or copy source speech',result)
        self.assertNotIn('@Video 1',result)
        self.assertLessEqual(len(result),4000)
        d['audio_mode']='none'
        self.assertNotIn('Милый',supplied_prompt(d,[]))

    def test_analyzed_source_contract_uses_approved_character_map(self):
        from cartoonbot.seedance_continuity import source_contract
        d={'visual_source':'generated','video_reference_instructions':'Replace the source woman with Helen from the approved character image.',
           'script':{'assets':[{'id':1,'name':'Helen'},{'id':2,'name':'Talking Phone'}]}}
        prompt=source_contract(d)
        self.assertIn('source video was analyzed upstream but is NOT attached',prompt)
        self.assertIn('image 1: Helen identity',prompt)
        self.assertIn('image 2: Talking Phone identity',prompt)
        self.assertIn('Do not retain source actors, blend identities',prompt)
        self.assertIn(d['video_reference_instructions'],prompt)

    def test_same_identity_different_outfits_and_absent_stranger(self):
        p=SimpleNamespace(structured=Mock(return_value=self.plan))
        d={'seconds':15,'audio_mode':'none','script':{'assets':[], 'scenes':[]},
           'analysis':{'shots':[{'location':'office','characters':[{'character_id':7,'wardrobe':'blue suit'}]},
                                {'location':'beach','characters':[{'character_id':7,'wardrobe':'white linen shirt'}]}]}}
        result=compile_prompt(p,{},d,'Latest edit: pajamas in bedroom',['wife.png','husband.png','stranger.png'])
        self.assertIn('navy pajamas',result)
        self.assertIn('emerald jacket',result)
        self.assertEqual([x['image'] for x in self.plan['shots'][1]['cast']],[1,2])
        args,kwargs=p.structured.call_args
        self.assertEqual(kwargs['images'],['wife.png','husband.png','stranger.png'])
        self.assertIn('Latest edit',args[2]['production_request'])
        self.assertEqual(args[2]['source_analysis'],d['analysis'])

    def test_speaker_isolation_survives_compilation(self):
        p=SimpleNamespace(structured=Mock(return_value=self.plan))
        d={'seconds':15,'audio_mode':'ru','lip':True,'script':{'assets':[], 'scenes':[]}}
        result=compile_prompt(p,{},d,'',['one.png','two.png','three.png'])
        self.assertIn('no overlapping voices',result)
        self.assertIn('listeners stay silent with closed mouths',result)
        self.assertIn('Camera cuts never change the speaker',result)
        self.assertLessEqual(len(result),4000)

    def test_rejects_unknown_attachment_and_gapped_timeline(self):
        p=deepcopy(self.plan); p['shots'][1]['cast'][0]['image']=4
        with self.assertRaisesRegex(ValueError,'binding'): render_plan(p,15,3)

    def test_source_photos_stay_out_of_video_inputs(self):
        from cartoonbot.seedance_prompts import references
        d={'script':{'assets':[{'id':1,'name':'Woman','description':'approved pajamas'}]},
           'images':{'1':{'path':'approved.png'}},'refs':[
               {'kind':'photo','path':'portrait.png','purpose':'Identity only'},
               {'kind':'photo','path':'outfit.png','purpose':'Outfit'}]}
        refs,labels=references(d)
        self.assertEqual(refs,['approved.png'])
        self.assertEqual(len(labels),1)
        self.assertEqual(len(d['refs']),2)

    def test_analyzed_source_is_not_attached_and_only_approved_visuals_are_used(self):
        from cartoonbot.seedance_prompts import references, video_reference_prompt
        from cartoonbot.seedance_continuity import source_contract
        d={'recreate':True,'seedance_reference_mode':'direct','audio_mode':'ru','seconds':15,
           'analysis':{'characters':[{'id':1,'role':'dog','appearance':'brown dog'}]},
           'video_reference_instructions':'Replace dog with cat.',
           'script':{'assets':[{'id':1,'name':'Cat','description':'cat','source_character_ids':[1]}],'scenes':[]},
           'images':{'1':{'path':'approved-cat.png'}},
           'refs':[{'kind':'video','path':'source.mp4'}, {'kind':'photo','path':'cat.png'}]}
        paths,labels=references(d)
        self.assertEqual(paths,['approved-cat.png'])
        self.assertNotIn('source.mp4',paths)
        self.assertNotIn('cat.png',paths)
        self.assertIn('source video was analyzed upstream but is NOT attached',source_contract(d))
        self.assertIn('NOT attached to this generation',video_reference_prompt(d))
        self.assertNotIn('@Video 1',video_reference_prompt(d))
        self.assertIn('Replace dog with cat.',video_reference_prompt(d))

    def test_long_prompt_shortens_once_and_preserves_dialogue(self):
        long=deepcopy(self.plan); long['shots'][0]['camera']='wide '*800
        long['shots'][0]['dialogue']='Hello!'
        short=deepcopy(self.plan); short['shots'][0]['dialogue']='Hello!'
        p=SimpleNamespace(structured=Mock(side_effect=[long,short]))
        result=compile_prompt(p,{}, {'seconds':15,'script':{},'visual_source':'upload'},'', ['a','b','c'])
        self.assertLessEqual(len(result),4000)
        self.assertIn('Hello!',result)
        self.assertIn('complete scene frames',result)
        self.assertEqual(p.structured.call_count,2)
        self.assertNotIn('images',p.structured.call_args.kwargs)

    def test_failed_shortening_rejects_oversized_plan(self):
        long=deepcopy(self.plan); long['shots'][0]['camera']='wide '*800
        p=SimpleNamespace(structured=Mock(side_effect=[long,ValueError('invalid plan')]))
        with self.assertRaisesRegex(ValueError,'seedance_prompt_too_long'):
            compile_prompt(p,{}, {'seconds':15,'script':{}},'', ['a','b','c'])
        p=deepcopy(self.plan); p['shots'][1]['start']=11
        with self.assertRaisesRegex(ValueError,'timing'): render_plan(p,15,3)
        p=deepcopy(self.plan); p['shots'][1]['action']='image 9 wakes up'
        with self.assertRaisesRegex(ValueError,'binding'): render_plan(p,15,3)

    def test_supplied_script_is_rendered_without_rewriting(self):
        script={'assets':[{'id':1,'name':'Teacher'},{'id':2,'name':'Student'}], 'scenes':[
            {'id':1,'start':0,'end':15,'action':'Teacher sits left. Student holds the violin.', 'camera':'Continuous medium shot.', 'dialogue':'Teacher: Listen or teach?\nStudent: Just listen.', 'video_prompt':'WRONG OLD TEXT'},
            {'id':2,'start':15,'end':30,'action':'Student lowers the violin.', 'camera':'Gentle push-in.', 'dialogue':'Teacher: Now?\nStudent: Right away.'}]}
        original=deepcopy(script)
        p=SimpleNamespace(structured=Mock(side_effect=AssertionError('Must not rewrite')))
        d={'supplied_script':'original before approved edits','script':script,'seconds':30,'lip':True,'style':'anime'}
        result=compile_prompt(p,{},d,'',['teacher','student'])
        for scene in script['scenes']:
            for field in ('action','camera','dialogue'): self.assertIn(scene[field],result)
        self.assertNotIn('WRONG OLD TEXT',result)
        self.assertEqual(script,original)
        self.assertIn('image 1: Teacher',result)
        self.assertIn('no overlapping voices',result)
        p.structured.assert_not_called()
        # No references, uploaded scene frames and explicit edits use the same authority.
        result=compile_prompt(p,{},dict(d,visual_source='upload'),'Use a wide shot',['frame1','frame2'])
        self.assertIn('image 2: complete scene 2 frame',result)
        self.assertIn('Explicit user change only: Use a wide shot',result)
        self.assertIn('Student: Right away.',compile_prompt(p,{},d,'',[]))
        with self.assertRaisesRegex(ValueError,'seedance_prompt_too_long'):
            compile_prompt(p,{},d,'x'*4000,[])

    def test_recreation_speech_is_inserted_verbatim_even_after_compression(self):
        plan=deepcopy(self.plan);plan['shots'][0]['dialogue']='Wrong person: Changed words!'
        plan['shots'][0]['camera']='wide '*800
        compact=deepcopy(self.plan);compact['shots'][0]['dialogue']='Another rewrite!'
        d={'seconds':15,'recreate':True,'audio_mode':'ru','lip':True,'script':{'scenes':[
            {'start':0,'end':10,'dialogue':'Женщина: Открой, пожалуйста.\nМужчина: Сейчас.'},
            {'start':10,'end':15,'dialogue':'Женщина: Спасибо!'}]}}
        original=deepcopy(d)
        provider=SimpleNamespace(structured=Mock(side_effect=[plan,compact]))
        result=compile_prompt(provider,{},d,'change dialogue',['a','b','c'])
        for scene in d['script']['scenes']:
            self.assertEqual(result.count(scene['dialogue']),1)
        self.assertNotIn('Changed words',result);self.assertNotIn('Another rewrite',result)
        self.assertEqual(d,original);self.assertLessEqual(len(result),4000)
        self.assertEqual(provider.structured.call_count,2)
        silent=compile_prompt(SimpleNamespace(structured=Mock(return_value=compact)),{},dict(d,audio_mode='none'),'', ['a','b','c'])
        self.assertNotIn('Спасибо',silent);self.assertNotIn('Another rewrite',silent)

    def test_failed_compaction_uses_approved_script_without_restoryboarding(self):
        plan=deepcopy(self.plan);plan['shots'][0]['camera']='wide '*800
        provider=SimpleNamespace(structured=Mock(side_effect=[plan,ValueError('failed compression')]))
        d={'seconds':15,'style':'anime','script':{'assets':[{'id':1,'name':'Woman'}], 'scenes':[
            {'id':1,'start':0,'end':15,'action':'She opens the jar.', 'camera':'Medium shot.', 'dialogue':'Woman: Thank you!'}]}}
        original=deepcopy(d)
        result=compile_prompt(provider,{},d,'',['approved.png','b.png','c.png'])
        self.assertIn('She opens the jar.',result);self.assertIn('Woman: Thank you!',result)
        self.assertEqual(d,original);self.assertLessEqual(len(result),4000)

    def test_long_supplied_script_compacts_only_production_instructions(self):
        d={'supplied_script':'original', 'seconds':15, 'audio_mode':'ru', 'style':'3d',
           'script':{'assets':[], 'scenes':[{'id':1,'start':0,'end':15,
           'action':'She opens a jar. '*350,'camera':'Medium shot.',
           'dialogue':'Женщина: Открой, пожалуйста.'}]}}
        original=deepcopy(d)
        plan={'shots':[{'start':0,'end':15,'location':'Kitchen','cast':[],
              'action':'She opens a jar.','camera':'Medium shot.','dialogue':'WRONG'}], 'audio':''}
        provider=SimpleNamespace(structured=Mock(return_value=plan))
        result=compile_prompt(provider,{},d,'',[])
        self.assertLessEqual(len(result),4000)
        self.assertIn('She opens a jar.',result)
        self.assertEqual(result.count('Женщина: Открой, пожалуйста.'),1)
        self.assertNotIn('WRONG',result)
        self.assertEqual(d,original)
        provider.structured.assert_called_once()
