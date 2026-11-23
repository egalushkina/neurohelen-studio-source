import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PIL import Image
from cartoonbot.visual_prompt import compile_prompt,SECTIONS,render
from cartoonbot.providers import LiveProvider
from cartoonbot.codex_provider import CodexProvider

def brief():
    result={k:'None' for k in SECTIONS}
    result.update(STYLE='3D cartoon as approved.',CHARACTER='Same woman and bottle mascot.',
                  ACTION='Left hand holds the bottle, right hand holds the comb.',
                  CAMERA='Medium shot.',IMPORTANT='No extra arms.')
    return result

class VisualPromptTests(unittest.TestCase):
    def test_all_sections_reference_order_latest_request(self):
        p=Mock();p.structured.return_value=brief()
        text=compile_prompt(p,{}, {'ratio':'9:16','style':'3d'},['current.png','product.png'],'LATEST: remove extra arm','Keep selected style',2)
        self.assertEqual([b.split('\n')[0] for b in text.split('\n\n')][1:],list(SECTIONS))
        self.assertLessEqual(len(text),4000)
        self.assertEqual(p.structured.call_args.kwargs['images'],['current.png','product.png'])
        self.assertIn('LATEST: remove extra arm',p.structured.call_args.args[2]['brief'])
    def test_total_budget_compresses_without_slicing(self):
        big=brief();big['ACTION']='x'*4000
        p=Mock();p.structured.side_effect=[big,brief()]
        text=compile_prompt(p,{}, {'ratio':'9:16','style':'3d'},[],'new frame','',1)
        self.assertEqual(p.structured.call_count,2)
        self.assertEqual(text,render(brief(),'9:16'))
    def test_oversize_is_rejected_after_two_text_attempts(self):
        big=brief();big['ACTION']='x'*4000
        p=Mock();p.structured.return_value=big
        with self.assertRaisesRegex(ValueError,'visual_prompt_too_long'):
            compile_prompt(p,{}, {'ratio':'9:16','style':'3d'},[],'frame','',1)
        self.assertEqual(p.structured.call_count,3)
    def test_second_compression_can_recover_without_losing_edit(self):
        big=brief();big['ACTION']='x'*4000
        small=brief();small['ACTION']='Replace pose: left hand bottle, right hand comb.'
        p=Mock();p.structured.side_effect=[big,big,small]
        text=compile_prompt(p,{}, {'ratio':'9:16','style':'3d'},[],'Replace pose','',1)
        self.assertEqual(text,render(small,'9:16'))
        self.assertEqual(p.structured.call_count,3)

    def test_live_wire_contains_only_bounded_rendered_prompt(self):
        p=Mock();p.structured.return_value=brief()
        buf=io.BytesIO();Image.new('RGB',(8,8)).save(buf,format='PNG')
        p.post.return_value=({'data':[{'b64_json':base64.b64encode(buf.getvalue()).decode()}]},'op')
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'OPENAI_IMAGE_MODEL':'test','OPENAI_API_KEY':'test'}):
            target=Path(temp)/'out.png'
            LiveProvider.image(p,{},'Frame',[],target,{'route':'omni','ratio':'9:16','style':'3d'},1)
            payload=p.post.call_args.kwargs['payload']
            self.assertEqual(payload['prompt'],render(brief(),'9:16'))
            self.assertEqual(target.with_suffix('.prompt.txt').read_text(),payload['prompt'])
    def test_codex_compiles_before_native_image_and_keeps_reference_inputs(self):
        p=Mock()
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);folder=home/'generated_images';folder.mkdir()
            source=folder/'result.png';Image.new('RGB',(8,8)).save(source)
            p.structured.side_effect=[brief(),{'path':str(source)}]
            target=home/'out.png'
            with patch.dict(os.environ,{'CODEX_HOME':temp}):
                CodexProvider.image(p,{},'Replace pose',['ref.png'],target,{'route':'omni','ratio':'9:16','style':'3d'},2)
            self.assertEqual(p.structured.call_count,2)
            call=p.structured.call_args
            self.assertTrue(call.kwargs['native_image'])
            self.assertEqual(call.kwargs['images'],['ref.png'])
            self.assertEqual(call.args[2]['brief'],render(brief(),'9:16'))
            self.assertIn('VERBATIM',call.args[1])
