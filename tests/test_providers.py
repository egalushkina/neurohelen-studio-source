import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from cartoonbot.config import Config
from cartoonbot.providers import LiveProvider
from cartoonbot.store import Store


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.cfg=Config(root=self.root); self.cfg.prepare(); self.s=Store(self.root/'db')
        self.p=LiveProvider(self.s,self.cfg); self.job={'id':'test-job'}
        self.env=patch.dict(os.environ,{'OPENAI_API_KEY':'test-key','OPENAI_TEXT_MODEL':'test-text',
                                        'OPENAI_IMAGE_MODEL':'test-image','GOOGLE_API_KEY':'test-google','BYTEPLUS_API_KEY':'test-byte'})
        self.env.start()

    def tearDown(self):
        self.p.client.close(); self.s.db.close(); self.env.stop(); self.tmp.cleanup()

    def transport(self,handler):
        self.p.client.close(); self.p.client=httpx.Client(transport=httpx.MockTransport(handler))

    def test_script_revision_does_not_restore_original_brief(self):
        current={'assets':[], 'scenes':[{'id':1,'dialogue':'Full current dialogue'}]}
        d={'lang':'ru','route':'omni','seconds':10,'script':current,
           'brief':{'idea':'OLD FOUR SCENES','audio':'No music'},
           'supplied_script':'OLD IMPORT', 'refs':[],
           'script_edit_history':[{'request':'Only scene 1','result_scene_count':1}]}
        with patch.object(self.p,'structured',return_value=current) as call, patch('cartoonbot.providers.validate_script',return_value=current):
            self.p.script(self.job,d,request='Restore the missing dialogue')
        data=call.call_args.args[2]
        self.assertEqual(data['previous'],current)
        self.assertNotIn('OLD FOUR SCENES',str(data))
        self.assertNotIn('OLD IMPORT',str(data))
        self.assertEqual(data['brief']['audio'],'No music')
        self.assertEqual(data['current_scene_count'],1)
        self.assertEqual(data['script_edit_history'][0]['request'],'Only scene 1')

    def test_source_analysis_screenplay_requires_character_mapping(self):
        current={'title':'Dog becomes a cat','assets':[{'id':1,'name':'Cat','description':'Orange cat',
            'reference_photo_ids':[1],'source_character_ids':[1]}], 'scenes':[]}
        d={'lang':'ru','route':'seedance','seconds':15,'script':current,
           'brief':{'idea':'A dog meets a man'},'seedance_source_flow':True,
           'seedance_reference_mode':'analysis','recreate':True,'source':{'kind':'video','seconds':15},
           'analysis':{'characters':[{'id':1,'role':'dog','appearance':'brown dog'}]},
           'refs':[{'kind':'photo','path':'cat.png','purpose':'Replace dog with this cat'}]}
        with patch.object(self.p,'structured',return_value=current) as call, patch('cartoonbot.providers.validate_script',return_value=current):
            self.p.script(self.job,d)
        payload=call.call_args.args[2]
        schema=call.call_args.args[3]
        self.assertIn('source_character_map',payload)
        self.assertIn('dog-to-cat',payload['source_character_mapping_rule'])
        self.assertIn('source_character_ids',schema['properties']['assets']['items']['required'])

    def test_openai_structured_contract_and_usage(self):
        def respond(request):
            self.assertEqual(str(request.url),'https://api.openai.com/v1/responses')
            body=json.loads(request.content)
            self.assertEqual(body['text']['format']['type'],'json_schema')
            self.assertTrue(body['text']['format']['strict'])
            self.assertFalse(body['store'])
            self.assertEqual(body['max_output_tokens'],12000)
            return httpx.Response(200,json={'id':'resp_123','output':[{'content':[{'type':'output_text','text':'{"ok":true}'}]}],
                                           'usage':{'input_tokens':12,'output_tokens':5}})
        self.transport(respond)
        result=self.p.structured(self.job,'role',{}, {'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False})
        self.assertTrue(result['ok'])
        row=self.s.db.execute('SELECT * FROM operations').fetchone()
        self.assertEqual(row['state'],'succeeded'); self.assertIn('input_tokens',row['usage'])

    def test_incomplete_text_is_reported_without_parsing_partial_json(self):
        self.transport(lambda request:httpx.Response(200,json={'id':'partial','status':'incomplete','output':[]}))
        with self.assertRaisesRegex(ValueError,'openai_response_incomplete'):
            self.p.structured(self.job,'role',{}, {'type':'object'})
        self.assertEqual(self.s.db.execute('SELECT state FROM operations').fetchone()[0],'failed')

    def test_image_edit_passes_all_references_and_exact_vertical_ratio(self):
        import io
        from PIL import Image
        data=io.BytesIO();Image.new('RGB',(16,16),'blue').save(data,format='PNG');png=data.getvalue()
        refs=[]
        for n in (1,2):
            path=self.root/f'ref{n}.png';path.write_bytes(png);refs.append(str(path))
        def respond(request):
            self.assertTrue(str(request.url).endswith('/images/edits'))
            body=request.content
            self.assertEqual(body.count(b'name="image[]"'),2)
            self.assertIn(b'864x1536',body)
            return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(png).decode()}]})
        self.transport(respond)
        with patch.dict(os.environ,{'OPENAI_IMAGE_MODEL':'gpt-image-2.5-sunburst'}):
            result=self.p.image(self.job,'Keep refs',refs,self.root/'nested'/'out.png',
                                {'route':'photo','refs':[],'ratio':'9:16','style':'3d'},1)
        self.assertEqual(Path(result).read_bytes(),png)

    def test_omni_edit_uses_previous_interaction(self):
        def respond(request):
            self.assertEqual(request.headers['x-goog-api-key'],'test-google')
            body=json.loads(request.content)
            self.assertEqual(body['previous_interaction_id'],'previous')
            self.assertIsInstance(body['input'],str)
            return httpx.Response(200,json={'id':'next','output_video':{'data':base64.b64encode(b'video-bytes').decode()}})
        self.transport(respond)
        d={'route':'omni','style':'3d','lang':'uk','ratio':'9:16','lip':True}
        out=self.p.video(self.job,d,{'video_prompt':'A scene'},[],self.root/'video.mp4',previous='previous',edit='red hat')
        self.assertEqual(out['remote_id'],'next')
        self.assertEqual(Path(out['path']).read_bytes(),b'video-bytes')

    def test_seedance_person_filter_retains_rejected_status(self):
        self.transport(lambda request:httpx.Response(400,json={'error':{'code':'InputImageSensitiveContentDetected.PrivacyInformation','message':'possible real person'}}))
        with self.assertRaisesRegex(ValueError,'seedance_real_person'):
            self.p.post(self.job,'byteplus_seedance','https://example.com',key='test-byte',payload={})
        self.assertEqual(self.s.db.execute('SELECT state FROM operations').fetchone()[0],'rejected')

    def test_seedance_rejection_preserves_reason_without_credentials(self):
        self.transport(lambda request: httpx.Response(400,json={'error':{
            'code':'InvalidParameter', 'message':'Unsupported duration; test-byte https://media.example.com/private/signature'}}))
        with self.assertRaisesRegex(RuntimeError,'byteplus_seedance_http_400'):
            self.p.post(self.job,'byteplus_seedance','https://example.com',key='test-byte',payload={})
        row=self.s.db.execute('SELECT * FROM operations').fetchone()
        diagnostic=json.loads(row['usage'])
        self.assertEqual(row['state'],'rejected')
        self.assertEqual(diagnostic['provider_status'],'InvalidParameter')
        self.assertIn('Unsupported duration',diagnostic['provider_message'])
        self.assertNotIn('test-byte',row['usage'])
        self.assertNotIn('/private/signature',row['usage'])

    def test_timeout_records_unknown_and_never_retries_post(self):
        calls=[]
        def respond(request):
            calls.append(request); raise httpx.ReadTimeout('secret url not for users')
        self.transport(respond)
        with self.assertRaisesRegex(RuntimeError,'unknown_status'):
            self.p.post(self.job,'test','https://example.com',key='test-key',payload={})
        self.assertEqual(len(calls),1)
        self.assertEqual(self.s.db.execute('SELECT state FROM operations').fetchone()[0],'unknown')

    def test_seedance_job_submission_poll_and_download(self):
        calls=[]
        def respond(request):
            calls.append(request.method)
            self.assertEqual(request.url.host,'ark.ap-southeast.bytepluses.com')
            self.assertTrue(request.url.path.startswith('/api/v3/contents/generations/tasks'))
            if request.method=='POST':
                body=json.loads(request.content)
                self.assertEqual(body['model'],'dreamina-seedance-2-5-260628')
                self.assertEqual(body['duration'],15)
                self.assertEqual(body['ratio'],'9:16')
                return httpx.Response(200,json={'id':'task123'})
            return httpx.Response(200,json={'status':'succeeded','content':{'video_url':'https://example.com/out.mp4'},'usage':{'total_tokens':123}})
        self.transport(respond)
        d={'route':'seedance','version':'2.5','style':'3d','lang':'uk','ratio':'9:16','lip':True,'refs':[],
           'script':{'scenes':[{'start':0,'end':15,'video_prompt':'Ukrainian dialogue'}]}}
        with patch('cartoonbot.providers.time.sleep'), patch.object(self.p,'download',side_effect=lambda url,path:path.write_bytes(b'output')):
            out=self.p.video(self.job,d,None,[],self.root/'video.mp4')
        self.assertEqual(calls,['POST','GET'])
        self.assertEqual(out['remote_id'],'task123')
        self.assertEqual(self.s.db.execute('SELECT state FROM operations').fetchone()[0],'succeeded')

    def test_seedance_ratio_constraint_and_explicit_retry_setting(self):
        ratios=[]
        def respond(request):
            if request.method=='POST':
                ratios.append(json.loads(request.content)['ratio'])
                return httpx.Response(200,json={'id':'ratio-task'})
            return httpx.Response(200,json={'status':'failed','error':{'code':'InvalidParameter.TaskTypeConstraint','message':'ratio must be adaptive'}})
        self.transport(respond)
        d={'route':'seedance','version':'2.5','style':'3d','lang':'ru','ratio':'9:16','lip':False,'refs':[],
           'script':{'scenes':[{'start':0,'end':15,'video_prompt':'Scene'}]}}
        with patch('cartoonbot.providers.time.sleep'):
            with self.assertRaisesRegex(ValueError,'seedance_ratio_constraint'):
                self.p.video(self.job,d,None,[],self.root/'v.mp4')
            self.assertEqual(ratios,['9:16'])  # No automatic paid resubmission.
            d['seedance_adaptive_ratio']=True
            with self.assertRaisesRegex(ValueError,'seedance_ratio_constraint'):
                self.p.video(self.job,d,None,[],self.root/'v.mp4')
        self.assertEqual(ratios,['9:16','adaptive'])

    def test_recreation_never_transmits_source_video_or_original_photo(self):
        from cartoonbot.catalog import has_video_reference, seedance_tokens
        from cartoonbot.seedance_prompts import references
        d={'recreate':True,'route':'seedance','version':'2.5','seconds':15,'style':'3d','lang':'ru','ratio':'9:16','lip':False,
           'refs':[{'kind':'video','path':'missing.mp4'},{'kind':'photo','path':'private-original.png'}],
           'script':{'assets':[], 'scenes':[{'start':0,'end':15,'video_prompt':'Scene'}]}}
        self.assertFalse(has_video_reference(d))
        self.assertEqual(seedance_tokens(d,'2.5',{}),700)
        self.assertEqual(references(d)[0],[])
        def respond(request):
            if request.method=='POST':
                body=json.loads(request.content)
                self.assertEqual([x['type'] for x in body['content']],['text'])
                self.assertNotIn('missing.mp4',request.content.decode())
                return httpx.Response(200,json={'id':'task'})
            return httpx.Response(200,json={'status':'succeeded','content':{'video_url':'https://example.com/out.mp4'}})
        self.transport(respond)
        with patch('cartoonbot.providers.time.sleep'),patch.object(self.p,'download'):
            self.p.video(self.job,d,None,[],self.root/'v.mp4')

    def test_legacy_direct_recreation_never_transmits_source_video(self):
        from cartoonbot.catalog import has_video_reference
        image=self.root/'approved.png'; image.write_bytes(b'image')
        source=self.root/'media'/'source.mp4'; source.parent.mkdir(exist_ok=True); source.write_bytes(b'video')
        d={'recreate':True,'seedance_reference_mode':'direct','route':'seedance','version':'2.5',
           'seconds':30,'style':'3d','lang':'ru','audio_mode':'ru','ratio':'9:16','lip':True,
           'refs':[{'kind':'video','path':str(source),'seconds':30}],
           'supplied_script':'locked','script':{'assets':[{'id':1,'name':'Cat'}], 'scenes':[{'id':1,'start':0,'end':30,
           'action':'Cat waves','camera':'Push in','dialogue':'Кот: Привет!','video_prompt':'Cat waves'}]}}
        self.assertFalse(has_video_reference(d))
        def respond(request):
            if request.method=='POST':
                body=json.loads(request.content)
                self.assertEqual(body['duration'],30)
                self.assertEqual([x['type'] for x in body['content']],['text','image_url'])
                self.assertNotIn('source.mp4',request.content.decode())
                self.assertIn('NOT attached',body['content'][0]['text'])
                return httpx.Response(200,json={'id':'analysis-task'})
            return httpx.Response(200,json={'status':'succeeded','content':{'video_url':'https://example.com/out.mp4'}})
        self.transport(respond)
        with patch('cartoonbot.providers.time.sleep'), patch.object(self.p,'download'):
            self.p.video(self.job,d,None,[str(image)],self.root/'analysis.mp4')

    def test_seedance_30_seconds_only_25(self):
        d={'route':'seedance','version':'2.5','seconds':30,'style':'3d','lang':'ru','ratio':'9:16','lip':True,'refs':[],
           'script':{'scenes':[{'start':0,'end':30,'video_prompt':'A calm scene'}]}}
        def respond(request):
            if request.method=='POST':
                self.assertEqual(json.loads(request.content)['duration'],30)
                return httpx.Response(200,json={'id':'long-task'})
            return httpx.Response(200,json={'status':'succeeded','content':{'video_url':'https://example.com/out.mp4'}})
        self.transport(respond)
        with patch('cartoonbot.providers.time.sleep'), patch.object(self.p,'download'):
            self.p.video(self.job,d,None,[],self.root/'long.mp4')
        d['version']='2.0-fast'
        with self.assertRaisesRegex(ValueError,'invalid_seedance_duration'):
            self.p.video(self.job,d,None,[],self.root/'invalid.mp4')

    def test_seedance_fast_uses_its_own_model(self):
        def respond(request):
            if request.method=='POST':
                self.assertEqual(json.loads(request.content)['model'],'dreamina-seedance-2-0-fast-260128')
                return httpx.Response(200,json={'id':'fast-task'})
            return httpx.Response(200,json={'status':'succeeded','content':{'video_url':'https://example.com/out.mp4'}})
        self.transport(respond)
        d={'route':'seedance','version':'2.0-fast','style':'3d','lang':'en','ratio':'9:16','lip':False,'refs':[],
           'script':{'scenes':[{'start':0,'end':15,'video_prompt':'A scene'}]}}
        with patch('cartoonbot.providers.time.sleep'), patch.object(self.p,'download'):
            self.assertEqual(self.p.video(self.job,d,None,[],self.root/'fast.mp4')['remote_id'],'fast-task')

    def test_subtitles_use_actual_audio_and_word_timestamps(self):
        audio=self.root/'audio.wav'; audio.write_bytes(b'audio')
        def respond(request):
            self.assertIn(b'timestamp_granularities[]',request.content)
            self.assertIn(b'whisper-1',request.content)
            return httpx.Response(200,json={'words':[{'word':'Привіт','start':0,'end':1}]})
        self.transport(respond)
        with patch('cartoonbot.media.audio_file',return_value=audio),patch('cartoonbot.media.info',return_value={'seconds':1}):
            words=self.p.transcribe(self.job,'video.mp4',self.root,'uk')
        self.assertEqual(words[0]['word'],'Привіт')


if __name__=='__main__': unittest.main()
