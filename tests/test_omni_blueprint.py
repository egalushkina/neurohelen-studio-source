import unittest
from unittest.mock import Mock, patch
from cartoonbot.omni_blueprint import compile_prompt, speech_schedule
from cartoonbot.omni_prompts import delivery_prompt

def blueprint():
    return {'characters':'Lena, adult woman with short black pixie hair and ivory shirt; OLLIN bottle mascot with arms, legs and a face.',
            'timeline':[{'start':0,'end':3,'direction':'At the mirror Lena lightly touches her hair, curious. Medium shot.'},
                        {'start':3,'end':6,'direction':'The bottle gestures on the table, confident. Push in.'},
                        {'start':6,'end':10,'direction':'Lena smiles at the bottle beside her, reassured. Hold close-up.'}],
            'audio':'Subtle room sound. No music.','important':'Keep the bottle label readable.'}

class BlueprintTests(unittest.TestCase):
    def setUp(self):
        self.provider=Mock();self.provider.structured.return_value=blueprint()
        self.d={'lang':'ru','ratio':'9:16','style':'anime','lip':True,'brief':{'audio':'Без музыки'}}
        self.scene={'id':1,'action':'Кот будит Лену.','camera':'Dolly in','dialogue':'Кот: «Лена, вставай!»'}
    def test_actual_delivery_seven_blocks_exact_dialogue_and_frame(self):
        compiled=compile_prompt(self.provider,{},self.d,self.scene,['approved.png'])
        self.assertEqual(delivery_prompt(self.d,self.scene,'Russian',True),compiled)
        headers=[x.split('\n')[0] for x in compiled.split('\n\n')]
        self.assertEqual(headers,['FORMAT','CHARACTERS','STYLE','TIMELINE','AUDIO','CONTINUITY','IMPORTANT'])
        self.assertIn('Objects keep their exact shape',compiled);self.assertIn('never replace or morph it',compiled)
        self.assertIn('exact opening frame',compiled)
        self.assertIn('never human actors, human heads or costumes',compiled)
        self.assertIn('Voice gender is audio only',compiled)
        self.assertIn('<FIRST_FRAME>',compiled);self.assertIn('2D Japanese anime',compiled)
        self.assertIn('«Лена, вставай!»',compiled);self.assertIn('No music.',compiled)
        self.assertIn('[0-10s] Кот (speaks in Russian)',compiled)
        self.assertEqual(self.provider.structured.call_args.kwargs['images'],['approved.png'])
        self.assertNotIn('live action',compiled)
    def test_two_speakers_take_turns_and_listeners_never_lip_sync(self):
        self.scene['dialogue']='Кот: «Лена, вставай!»\nЛена: «Уже встаю!»'
        compiled=compile_prompt(self.provider,{},self.d,self.scene,['approved.png'])
        turns=speech_schedule(self.scene['dialogue'])
        self.assertEqual(turns[0]['end'],turns[1]['start'])
        for text in ('no overlapping voices','listeners stay silent with closed mouths','Camera cuts never change the speaker'):
            self.assertIn(text,compiled)
        edited=delivery_prompt(self.d,self.scene,'Russian',True,'Closer reaction shot')
        self.assertIn('listeners stay silent with closed mouths',edited)
        self.assertLessEqual(len(compiled),4000)

    def test_cache_reused_only_for_identical_inputs(self):
        compile_prompt(self.provider,{},self.d,self.scene,['a.png']);compile_prompt(self.provider,{},self.d,self.scene,['a.png'])
        self.assertEqual(self.provider.structured.call_count,1)
        compile_prompt(self.provider,{},self.d,self.scene,['b.png'])
        self.scene['action']='Кот машет лапой.';compile_prompt(self.provider,{},self.d,self.scene,['b.png'])
        self.d['lip']=False;compiled=compile_prompt(self.provider,{},self.d,self.scene,['b.png'])
        self.assertEqual(self.provider.structured.call_count,4);self.assertIn('No on-camera talking',compiled)
    def test_gaps_overlaps_and_bad_durations_not_cached(self):
        for start,end in ((4,6),(2,6),(3,11),(3,3)):
            with self.subTest(start=start,end=end):
                value=blueprint();value['timeline'][1].update(start=start,end=end)
                self.provider.structured.return_value=value
                with self.assertRaises(ValueError):compile_prompt(self.provider,{},self.d,self.scene,['a.png'])
                self.assertEqual(self.d['omni_blueprints'],{})
    def test_oversized_directions_compressed_without_truncating_dialogue(self):
        value=blueprint();value['characters']='x'*3300
        self.provider.structured.side_effect=[value,blueprint()]
        compiled=compile_prompt(self.provider,{},self.d,self.scene,['a.png'])
        self.assertEqual(self.provider.structured.call_count,2)
        self.assertIn('«Лена, вставай!»',compiled)
    def test_dialogue_dashes_removed_not_spelling_hyphens(self):
        self.scene['dialogue']='Кот: «Кто-то — пришёл!»'
        compiled=compile_prompt(self.provider,{},self.d,self.scene,['a.png'])
        self.assertIn('«Кто-то пришёл!»',compiled)
    def test_existing_product_logo_is_not_prohibited(self):
        compiled=compile_prompt(self.provider,{},self.d,self.scene,['a.png'])
        self.assertIn('Preserve reference product labels',compiled);self.assertNotIn('No logos.',compiled)
    def test_two_speakers_share_full_window_exact_lines(self):
        turns=speech_schedule('Лена: «Это кто-то новый?» OLLIN: «Это я!»')
        self.assertEqual([t['speaker'] for t in turns],['Лена','OLLIN'])
        self.assertEqual([t['line'] for t in turns],['«Это кто-то новый?»','«Это я!»'])
        self.assertEqual(turns[0]['start'],0);self.assertEqual(turns[-1]['end'],10)
        self.assertEqual(turns[0]['end'],turns[1]['start'])
    def test_silent_scene(self):
        self.scene['dialogue']='';compiled=compile_prompt(self.provider,{},self.d,self.scene,['a.png'])
        self.assertIn('No dialogue.',compiled);self.assertNotIn('speaks in',compiled)

    def test_total_limit_includes_fixed_blocks_and_dialogue(self):
        long=blueprint();long['characters']='x'*3300
        self.scene['dialogue']='Кот: «'+('слово '*90)+'»'
        self.provider.structured.side_effect=[long,blueprint()]
        prompt=compile_prompt(self.provider,{},self.d,self.scene,['a.png'])
        self.assertLessEqual(len(prompt),4000)
        self.assertEqual(self.provider.structured.call_count,2)
        self.assertIn('«'+('слово '*90)+'»',prompt)

    def test_failed_compression_not_cached(self):
        long=blueprint();long['characters']='x'*4100
        self.provider.structured.return_value=long
        with self.assertRaisesRegex(ValueError,'omni_prompt_too_long'):
            compile_prompt(self.provider,{},self.d,self.scene,['a.png'])
        self.assertEqual(self.d['omni_blueprints'],{})
        self.assertEqual(self.provider.structured.call_count,3)

    def test_unfittable_dialogue_is_not_cut_or_sent_for_text_generation(self):
        self.scene['dialogue']='Кот: «'+('слово '*800)+'»'
        with self.assertRaisesRegex(ValueError,'omni_prompt_too_long'):
            compile_prompt(self.provider,{},self.d,self.scene,['a.png'])
        self.provider.structured.assert_not_called()

    def test_wire_guard_rejects_old_oversized_cached_prompt(self):
        from cartoonbot.providers import LiveProvider
        provider=Mock()
        d={**self.d,'route':'omni','omni_blueprints':{'1':{'prompt':'x'*4001}}}
        with self.assertRaisesRegex(ValueError,'omni_prompt_too_long'):
            LiveProvider.video(provider,{},d,self.scene,[],Mock())
        provider.post.assert_not_called();provider.vertex_video.assert_not_called()

class WorkerBlueprintTest(unittest.TestCase):
    def test_worker_compiles_before_video_dispatch(self):
        from tests.test_workflow import Harness
        h=Harness();h.setUp()
        try:
            h.visuals('omni');h.press('approve');h.press('generate');h.cfg.mode='codex'
            original=h.provider.video
            def video(job,d,scene,*args,**kwargs):
                self.assertIn('TIMELINE\n',delivery_prompt(d,scene,'Russian',True))
                return original(job,d,scene,*args,**kwargs)
            with patch('cartoonbot.voices.ensure'),patch.object(h.provider,'structured',return_value=blueprint(),create=True),patch.object(h.provider,'video',side_effect=video):h.finish_job()
            self.assertIn('1',h.current()['data']['omni_blueprints'])
        finally:h.tearDown()
