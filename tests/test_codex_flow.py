import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cartoonbot.config import Config
from cartoonbot.store import Store
from cartoonbot.engine import Engine
from cartoonbot.codex_provider import CodexProvider
from cartoonbot.schemas import validate_script
from cartoonbot.telegram import Telegram

class CodexFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.cfg=Config(root=Path(self.tmp.name),mode='codex',admins={1},workers=1)
        self.cfg.prepare(); self.s=Store(self.cfg.root/'db'); self.e=Engine(self.s,self.cfg); self.n=0
    def tearDown(self): self.s.db.close(); self.tmp.cleanup()
    def event(self,action='',text=''):
        self.n+=1; self.e.handle({'id':self.n,'user_id':1,'language':'ru','action':action,'text':text})
    def test_script_first_and_home_while_busy(self):
        self.event('new:omni'); p=self.s.project(self.s.user(1)['current'])
        self.event(f"p:{p['id']}:{p['revision']}:vlang=ru")
        p=self.s.project(p['id'])
        self.event(f"p:{p['id']}:{p['revision']}:source=no")
        self.assertEqual(self.s.project(p['id'])['state'],'intake')
        self.event(text='Ёжик у совы-психолога: боится объятий.')
        current=self.s.project(p['id']); self.assertEqual(current['state'],'busy')
        self.assertIn('Ёжик',current['data']['brief']['idea'])
        self.event(text='🏠 Главное меню'); self.assertEqual(self.s.user(1)['current'],p['id'])
        self.assertEqual(self.s.project(p['id'])['state'],'busy')
    def test_codex_video_analysis_without_openai_api_key(self):
        provider=CodexProvider(self.s,self.cfg)
        video=self.cfg.root/'source.mp4'; video.write_bytes(b'video')
        analysis={'summary':'A moving character','route':'omni','has_dialogue':True}
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}), patch('cartoonbot.media.info',return_value={'audio':False,'seconds':5}), patch('cartoonbot.media.sample_frames',return_value=[]), patch.object(provider,'structured',return_value=analysis):
            result=provider.analyze({'id':'test'},str(video),'ru')
        self.assertFalse(result['has_dialogue'])
        self.assertFalse(result['audio_unknown'])
        self.assertEqual(result['route'],'seedance')
        provider.client.close()

    def test_recreate_keeps_source_for_analysis_only(self):
        for duration in (13.47,30):
            self.event('new:recreate')
            p=self.s.project(self.s.user(1)['current'])
            p['state']='route'
            p['data']['source']={'kind':'video','path':'source.mp4','seconds':duration}
            p['data']['analysis']={'summary':'A woman turns a laptop','route':'seedance'}
            self.s.save(p)
            self.event(f"p:{p['id']}:{p['revision']}:route=seedance")
            d=self.s.project(p['id'])['data']
            self.assertEqual(d['refs'],[])
            self.assertTrue(d['recreate'])
            self.assertEqual(d['source']['path'],'source.mp4')
            self.assertNotIn('video_ref_question_pending',d)

    def test_supplied_scene_frames_skip_references(self):
        from cartoonbot.seedance_prompts import references
        for route in ('omni','seedance'):
            self.event('new:'+route)
            p=self.s.project(self.s.user(1)['current'])
            p['state']='ratio'; d=p['data']; d['supplied_script']='My script'
            d['script']={'assets':[{'id':1,'name':'Cat','description':'A cat'}],
                         'scenes':[{'id':i,'start':(i-1)*5,'end':i*5,'action':'Cat walks','image_prompt':'Cat'} for i in (1,2,3)]}
            self.s.save(p)
            def press(action):
                current=self.s.project(p['id'])
                self.event(f"p:{p['id']}:{current['revision']}:{action}")
            press('ratio=9:16'); self.assertEqual(self.s.project(p['id'])['state'],'visual_source')
            press('visual_source=upload')
            for i in (1,2,3):
                self.n+=1
                self.e.handle({'id':self.n,'user_id':1,'media':{'kind':'photo','path':f'frame{i}.png'}})
            current=self.s.project(p['id']); self.assertEqual(current['state'],'visuals')
            self.assertEqual(len(current['data']['images']),3)
            self.assertEqual(current['data']['refs'],[])
            d=self.s.project(p['id'])['data']
            self.assertEqual(d['images']['2']['path'],'frame2.png')
            self.assertEqual(len(d['refs']),0)
            if route=='seedance':
                paths,labels=references(d)
                self.assertEqual(paths,['frame1.png','frame2.png','frame3.png'])
                self.assertIn('scene 2',labels[1])
            self.assertEqual(self.s.db.execute('select count(*) from jobs').fetchone()[0],0)
            press('approve')
            self.assertEqual(self.s.project(p['id'])['state'],'quote' if route=='omni' else 'video_version')

    def test_import_uses_production_role_not_creative_rewrite(self):
        provider=CodexProvider(self.s,self.cfg)
        d={'route':'omni','lang':'ru','auto_duration':True,'brief':{},'supplied_script':'A ready script'}
        with patch.object(provider,'structured',return_value={}) as call,patch('cartoonbot.providers.validate_script',return_value={}):
            provider.script({'id':'import'},d)
            role=call.call_args.args[1]
            self.assertIn('Copy supplied action, dialogue, camera directions',role)
            self.assertNotIn('Create a strong opening',role)
        provider.client.close()

    def test_auto_duration_accepts_forty_and_fifty(self):
        for count in (4,5):
            d={'route':'omni','seconds':10,'auto_duration':True,'max_seconds':60}
            script={'assets':[{'id':1}], 'scenes':[dict(id=i+1,start=i*10,end=(i+1)*10,action='a',dialogue='d',camera='c',image_prompt='i',video_prompt='v') for i in range(count)]}
            validate_script(script,d); self.assertEqual(d['seconds'],count*10)
    def test_codex_uses_schema_no_secret_env_and_no_demo_fallback(self):
        provider=CodexProvider(self.s,self.cfg)
        def run(args,**kw):
            self.assertNotIn('TELEGRAM_BOT_TOKEN',kw['env']); self.assertIn('read-only',args)
            self.assertIn('ёжик',kw['input']); Path(args[args.index('-o')+1]).write_text('{"ok":true}')
            return subprocess.CompletedProcess(args,0)
        with patch.dict(os.environ,{'TELEGRAM_BOT_TOKEN':'secret'}),patch('cartoonbot.codex_provider.subprocess.run',side_effect=run):
            self.assertEqual(provider.structured({'id':'test'},'role',{'idea':'ёжик'},{}),{'ok':True})
        with patch('cartoonbot.codex_provider.subprocess.run',return_value=subprocess.CompletedProcess([],1)):
            with self.assertRaises(RuntimeError): provider.structured({'id':'fail'},'role',{}, {})
        provider.client.close()
    def test_freeform_edit_reaches_script_prompt(self):
        provider=CodexProvider(self.s,self.cfg)
        d={'route':'omni','lang':'ru','auto_duration':True,'brief':{'idea':'ёжик'},'script':{'old':'script'}}
        with patch.object(provider,'structured',return_value={'result':1}) as request,patch('cartoonbot.providers.validate_script',side_effect=lambda r,d:r):
            provider.script({'id':'x'},d,request='Измени финал и сцены 1 и 3')
            data=request.call_args.args[2]
            self.assertEqual(data['requested_change'],'Измени финал и сцены 1 и 3'); self.assertIn('previous',data)
        provider.client.close()
    def test_owner_omni_role_applies_only_to_omni(self):
        provider=CodexProvider(self.s,self.cfg)
        for route in ('omni','seedance'):
            d={'route':route,'lang':'uk','auto_duration':route=='omni','seconds':15,'brief':{}}
            with patch.object(provider,'structured',return_value={}) as call,patch('cartoonbot.providers.validate_script',return_value={}):
                provider.script({'id':'role'},d)
                instructions=call.call_args.args[1]
                self.assertEqual('prepare one 10-second scene at a time' in instructions,route=='omni')
                if route=='omni':
                    self.assertIn('exact dialogue',instructions)
                    self.assertIn('music/audio preferences',instructions)
        provider.client.close()

    def test_omni_final_settings_keep_approved_dialogue_and_edits(self):
        from cartoonbot.omni_prompts import delivery_prompt
        d={'ratio':'16:9','style':'anime','lip':True,'brief':{'audio':'Без музыки'}}
        scene={'video_prompt':'TIMELINE: [0-10s] A quiet conversation.', 'dialogue':'Їжак: «Мені страшно». '}
        prompt=delivery_prompt(d,scene,'Ukrainian',True,'Крупный план')
        for value in ('10 seconds','16:9','reference image',scene['dialogue'],'Без музыки','Крупный план','No subtitles'):
            self.assertIn(value,prompt)

    def test_native_image_copies_valid_output_and_passes_references(self):
        from PIL import Image
        provider=CodexProvider(self.s,self.cfg)
        home=self.cfg.root/'codex_home'; folder=home/'generated_images'; folder.mkdir(parents=True)
        source=folder/'result.png'; Image.new('RGB',(32,32),'red').save(source)
        target=self.cfg.root/'media'/'image.png'
        with patch.dict(os.environ,{'CODEX_HOME':str(home)}),patch.object(provider,'structured',return_value={'path':str(source)}) as call:
            result=provider.image({'id':'x'},'preserve identity',['reference.png'],target,{'ratio':'9:16','style':'3d'},1)
            self.assertEqual(Path(result).read_bytes(),source.read_bytes())
            self.assertTrue(call.call_args.kwargs['native_image'])
            self.assertEqual(call.call_args.kwargs['images'],['reference.png'])
            self.assertIn('STYLIZED 3D ANIMATED FEATURE',call.call_args.args[1])
            self.assertIn('NOT photographic rendering',call.call_args.args[1])
        with patch.object(provider,'structured',return_value={'path':'/etc/passwd'}):
            with self.assertRaises(ValueError):provider.image({'id':'x'},'x',[],target,{'ratio':'9:16','style':'3d'},1)
        provider.client.close()

    def test_persistent_keyboard_and_inline_home(self):
        self.event(text='/start'); telegram=Telegram(self.cfg,self.s,self.e)
        with patch.object(telegram,'call') as call:
            rows=self.s.db.execute('SELECT * FROM outbox ORDER BY id').fetchall()
            for row in rows: telegram.send_row(row)
            markups=[json.loads(c.args[1]['reply_markup']) for c in call.call_args_list]
            self.assertTrue(markups[0]['is_persistent'])
            self.assertEqual(markups[1]['inline_keyboard'][-1][0]['callback_data'],'home')
        telegram.client.close()

if __name__=='__main__': unittest.main()
