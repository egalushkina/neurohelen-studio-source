import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from http.server import ThreadingHTTPServer
import httpx
from PIL import Image
from webstudio.server import Studio, Handler, Problem

class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=Studio(Path(self.tmp.name)/'browser')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.server.app=self.app
        self.base=f'http://127.0.0.1:{self.server.server_port}';self.server.origin=self.base
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.client=httpx.Client(base_url=self.base,headers={'Origin':self.base})
    def tearDown(self):
        self.client.close();self.server.shutdown();self.server.server_close();self.thread.join()
        self.app.store.db.close();self.tmp.cleanup()
    def register(self,client=None,email='test@example.test'):
        client=client or self.client
        response=client.post('/api/register',json={'email':email,'password':'Test-password-2026'})
        self.assertEqual(response.status_code,200)
        client.headers['X-CSRF-Token']=response.json()['csrf']
        return client.get('/api/state').json()
    def test_public_origin_uses_secure_cookie_and_rejects_foreign_origin(self):
        self.server.origin='https://studio.neurohelen.com'
        self.client.headers['Origin']=self.server.origin
        response=self.client.post('/api/register',json={'email':'secure@example.test','password':'Test-password-2026'})
        self.assertEqual(response.status_code,200)
        self.assertIn('; Secure',response.headers['set-cookie'])
        self.client.headers['Origin']='https://other.example'
        self.assertEqual(self.client.post('/api/login',json={}).status_code,403)

    def test_device_language_does_not_override_manual_choice(self):
        self.register()
        auto=self.client.post('/api/language',json={'language':'uk','automatic':True}).json()
        self.assertEqual(auto['language'],'uk');self.assertFalse(auto['language_manual'])
        manual=self.client.post('/api/language',json={'language':'ru'}).json()
        self.assertTrue(manual['language_manual'])
        later=self.client.post('/api/language',json={'language':'en','automatic':True}).json()
        self.assertEqual(later['language'],'ru')

    def test_video_language_names_follow_interface_language(self):
        self.register()
        self.client.post('/api/language',json={'language':'en'})
        result=self.client.post('/api/event',json={'action':'new:omni'}).json()
        choices={b['action'].rsplit('=',1)[-1]:b['label'] for b in result['controls'] if 'vlang=' in b['action']}
        self.assertEqual(choices,{'ru':'Russian','en':'English','uk':'Ukrainian'})

    def test_language_controls_survive_invalid_text(self):
        self.register()
        first=self.client.post('/api/event',json={'action':'new:omni'}).json()
        later=self.client.post('/api/event',json={'text':'непонятно'}).json()
        self.assertTrue(any(b['action'].endswith('vlang=ru') for b in first['controls']))
        self.assertEqual(first['controls'],later['controls'])
        self.assertFalse(later['project']['can_text'])
        p=later['project']
        selected=self.client.post('/api/event',json={'action':f"p:{p['id']}:{p['revision']}:vlang=ru"}).json()
        self.assertFalse(selected['project']['can_text'])
        p=selected['project']
        idea=self.client.post('/api/event',json={'action':f"p:{p['id']}:{p['revision']}:source=no"}).json()
        self.assertTrue(idea['project']['can_text'])
        self.assertIn('ответ',idea['project']['input_placeholder'])

    def test_saved_script_menu_and_selected_model(self):
        self.register()
        menu=self.client.post('/api/event',json={'action':'script_menu'}).json()
        self.assertTrue(any(b['action']=='scripts:0' for b in menu['controls']))
        self.client.post('/api/event',json={'action':'new:omni'})
        p=self.app.store.project(self.client.get('/api/state').json()['project']['id'])
        p['state']='intake'; self.app.store.save(p)
        selected=self.client.post('/api/event',json={'action':f"p:{p['id']}:{p['revision']}:have_script"}).json()
        self.assertTrue(any(b['action']=='scripts:0' for b in selected['controls']))
        accepted=self.client.post('/api/event',json={'text':'Сцена 1. Кот машет рукой. Привет!'}).json()
        self.assertEqual(accepted['project']['state'],'supplied_review')
        self.assertEqual(accepted['project']['route'],'omni')

    def test_apply_video_edit_queues_atomically_with_shared_wallet(self):
        from cartoonbot.shared_wallet import Wallet
        self.register()
        state=self.client.post('/api/event',json={'action':'new:omni'}).json()
        store=self.app.store
        p=store.project(state['project']['id'])
        store.wallet=Wallet(store,str(Path(self.tmp.name)/'wallet.sqlite3'),'web')
        p['state']='edit_confirm'
        p['data'].update(edit_target='video',edits=[{'id':1,'instruction':'Keep hair'}],
                         script={'scenes':[{'id':1,'start':0,'end':10,'action':'Brain','dialogue':'Hi','camera':'close'}]})
        store.save(p)
        action=f"p:{p['id']}:{p['revision']}:apply"
        response=self.client.post('/api/event',json={'action':action})
        self.assertEqual(response.status_code,200,response.text)
        current=store.project(p['id'])
        self.assertEqual(current['state'],'busy')
        job=store.db.execute('SELECT * FROM jobs WHERE project_id=?',(p['id'],)).fetchone()
        self.assertEqual(job['kind'],'prepare_video_revision')
        self.assertEqual(job['revision'],current['revision'])
        self.client.post('/api/event',json={'action':action})
        self.assertEqual(store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)

    def test_video_card_edit_waits_for_payment_confirmation(self):
        self.register()
        state=self.client.post('/api/event',json={'action':'new:omni'}).json()
        p=self.app.store.project(state['project']['id']);p['state']='video'
        p['data']['script']={'title':'Тест','scenes':[{'id':1,'start':0,'end':10,'action':'Кот сидит','dialogue':'Привет','camera':'medium shot'}]}
        p['data']['videos']={'1':{'status':'done'}}
        self.app.store.save(p)
        response=self.client.post('/api/card',json={'project':p['id'],'revision':p['revision'],'kind':'video','scene':1,'instruction':'Кот машет рукой'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['project']['state'],'busy')
        updated=self.app.store.project(p['id'])
        self.assertEqual(updated['data']['video_edits'],[{'id':1,'instruction':'Кот машет рукой'}])
        self.assertEqual(self.app.store.db.execute('SELECT kind FROM jobs').fetchone()[0],'prepare_video_revision')

    def test_quote_action_is_rebuilt_if_revision_bound_outbox_button_is_missing(self):
        self.register()
        state=self.client.post('/api/event',json={'action':'new:omni'}).json()
        p=self.app.store.project(state['project']['id'])
        p['state']='quote'
        p['data']['script']={'title':'Тест','scenes':[{'id':6,'start':50,'end':60,'action':'Героиня меняет сцену','dialogue':'Готово','camera':'medium shot'}]}
        p['data']['video_edits']=[{'id':6,'instruction':'Измени сцену'}]
        self.app.store.save(p)
        result=self.client.get('/api/state').json()
        self.assertEqual(result['project']['state'],'quote')
        self.assertEqual(len(result['controls']),1)
        self.assertEqual(result['controls'][0]['action'],f"p:{p['id']}:{p['revision']}:generate")
        self.assertIn('200',result['controls'][0]['label'])

    def test_orphaned_busy_project_returns_to_quote_after_terminal_rejection(self):
        self.register()
        state=self.client.post('/api/event',json={'action':'new:omni'}).json()
        p=self.app.store.project(state['project']['id'])
        p['state']='busy'
        p['data'].update(return_state='quote',pending_generation={'job':'failed-job','unit':6},
                         script={'title':'Тест','scenes':[{'id':6,'start':50,'end':60,'action':'Изменённая сцена','dialogue':'Готово','camera':'medium shot'}]},
                         video_edits=[{'id':6,'instruction':'Измени сцену'}])
        self.app.store.save(p)
        with self.app.store.transaction():
            self.app.store.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',
                ('failed-job',p['id'],p['revision'],'video','{}','attention',0,'RuntimeError',1))
            self.app.store.db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)',
                ('failed-op','failed-job','google_omni','rejected',None,'{}',1))

        result=self.client.get('/api/state').json()

        self.assertEqual(result['project']['state'],'quote')
        self.assertEqual(result['project']['scenes'][0]['id'],6)
        self.assertEqual(result['controls'][0]['action'],f"p:{p['id']}:{result['project']['revision']}:generate")
        repaired=self.app.store.project(p['id'])
        self.assertNotIn('pending_generation',repaired['data'])
        self.assertEqual(repaired['data']['video_edits'],[{'id':6,'instruction':'Измени сцену'}])

    def test_dialogue_proposal_can_be_manually_edited_without_applying_or_generating(self):
        self.register()
        state=self.client.post('/api/event',json={'action':'new:omni'}).json()
        p=self.app.store.project(state['project']['id'])
        p['state']='dialogue_proposal'
        p['data']['script']={'title':'Тест','scenes':[{'id':1,'start':0,'end':10,'action':'Персонажи говорят','dialogue':'Исходный диалог','camera':'medium shot'}]}
        p['data']['dialogue_proposal']={'id':1,'original':'Исходный диалог','proposed':'Предложенный вариант','failed':False,'return_state':'quote'}
        self.app.store.save(p);self.app.engine.show(p)
        shown=self.client.get('/api/state').json()
        self.assertEqual(shown['project']['dialogue_proposal']['proposed'],'Предложенный вариант')
        self.assertEqual(len(shown['controls']),3)
        self.assertTrue(any(b['action'].endswith(':dialogue_edit') for b in shown['controls']))

        edited=self.client.post('/api/card',json={'project':p['id'],'revision':shown['project']['revision'],
            'kind':'dialogue','scene':1,'dialogue':'Моя короткая реплика.'})

        self.assertEqual(edited.status_code,200,edited.text)
        result=edited.json();self.assertEqual(result['project']['state'],'dialogue_proposal')
        self.assertEqual(result['project']['dialogue_proposal']['proposed'],'Моя короткая реплика.')
        saved=self.app.store.project(p['id'])
        self.assertEqual(saved['data']['script']['scenes'][0]['dialogue'],'Исходный диалог')
        self.assertEqual(self.app.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)

    def test_complete_video_exposed_after_approval(self):
        self.register()
        response=self.client.post('/api/event',json={'action':'new:omni'}).json()
        p=self.app.store.project(response['project']['id'])
        path=self.app.root/'media'/'assembled.mp4';path.parent.mkdir(exist_ok=True);path.write_bytes(b'test-video')
        p['data']['assembled']=str(path);p['state']='video';self.app.store.save(p)
        preview=self.client.get('/api/state').json()['project']
        self.assertIsNotNone(preview['assembled_video']);self.assertIsNone(preview['complete_video'])
        result=self.client.post('/api/event',json={'action':f"p:{p['id']}:{p['revision']}:approve"})
        self.assertEqual(result.status_code,200,result.text)
        self.assertIsNotNone(result.json()['project']['complete_video'])
        self.assertEqual(self.client.get(result.json()['project']['complete_video']).content,b'test-video')

    def test_script_notes_queue_revision_instead_of_replacing_action(self):
        self.register()
        state=self.client.post('/api/event',json={'action':'new:omni'}).json()
        p=self.app.store.project(state['project']['id']);p['state']='script'
        p['data']['script']={'title':'Тест','scenes':[{'id':1,'start':0,'end':10,'action':'Кот сидит','dialogue':'Привет','camera':'medium shot'}]}
        self.app.store.save(p)
        r=self.client.post('/api/card',json={'project':p['id'],'revision':p['revision'],'kind':'script_request','instruction':'Измени финал'})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['project']['state'],'busy')
        self.assertEqual(r.json()['project']['scenes'][0]['action'],'Кот сидит')
        self.assertEqual(self.app.store.db.execute('SELECT kind FROM jobs').fetchone()[0],'edit_script')

    def test_support_operator_requires_owner_login(self):
        self.register()
        uid=self.app.store.db.execute('SELECT uid FROM web_accounts').fetchone()[0]
        self.app.support.send(uid,{'text':'Соедините с агентом reader@example.test'})
        self.assertEqual(self.client.get('/api/support/operator?ticket=1').status_code,403)
        self.assertEqual(self.client.post('/api/support/operator',json={'ticket':1,'text':'Ответ'}).status_code,403)
        self.app.config.admins={uid}
        self.assertEqual(self.client.get('/api/support/operator?ticket=1').status_code,200)
        self.assertEqual(self.client.post('/api/support/operator',json={'ticket':1,'join':True}).status_code,200)
        response=self.client.post('/api/support/operator',json={'ticket':1,'text':'Помогу вам'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['messages'][-1]['body'],'Помогу вам')
        self.assertEqual(self.app.store.db.execute('SELECT count(*) FROM support_mail').fetchone()[0],1)

    def test_manual_script_edit_requires_current_revision_and_approval(self):
        self.register()
        state=self.client.post('/api/event',json={'action':'new:omni'}).json()
        pid=state['project']['id']
        with self.app.store.transaction():
            p=self.app.store.project(pid);p['state']='script'
            p['data']['script']={'title':'Тест','scenes':[{'id':1,'start':0,'end':10,'action':'Кот сидит','dialogue':'Привет','camera':'medium shot'}]}
            self.app.store.save(p);self.app.engine.show(p)
        body={'project':pid,'revision':p['revision'],'kind':'script','scenes':[{'id':1,'action':'Кот машет','dialogue':'Добрый день','camera':'close-up'}]}
        response=self.client.post('/api/card',json=body)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['project']['state'],'script')
        self.assertEqual(response.json()['project']['scenes'][0]['dialogue'],'Добрый день')
        self.assertEqual(self.client.post('/api/card',json=body).status_code,409)

    def test_manual_script_edit_after_summary_returns_to_approval(self):
        self.register()
        state=self.client.post('/api/event',json={'action':'new:omni'}).json()
        pid=state['project']['id']
        with self.app.store.transaction():
            p=self.app.store.project(pid);p['state']='script_summary'
            p['data']['script']={'title':'Тест','scenes':[{'id':1,'start':0,'end':10,'action':'Кот сидит','dialogue':'Привет','camera':'medium shot'}]}
            p['data']['script_summary']={'seconds':10,'tokens':250}
            self.app.store.save(p)
        body={'project':pid,'revision':p['revision'],'kind':'script','scenes':[{'id':1,'action':'Кот машет','dialogue':'Добрый день','camera':'close-up'}]}
        response=self.client.post('/api/card',json=body)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['project']['state'],'script')
        self.assertEqual(response.json()['project']['scenes'][0]['action'],'Кот машет')

    def test_live_mode_closes_registration_and_reports_real_mode(self):
        self.register()
        self.app.config.mode='live'
        self.app.config.public_access=False
        self.assertFalse(self.client.get('/api/info').json()['demo'])
        self.assertFalse(self.client.get('/api/state').json()['demo'])
        result=self.client.post('/api/register',json={'email':'new@example.test','password':'Another-password-2026'})
        self.assertEqual(result.status_code,403)
        self.assertEqual(self.client.post('/api/event',json={'action':'p:old:0:source_mode=direct'}).status_code,400)

    def test_interface_language_persists_without_changing_video_language(self):
        self.register()
        first=self.client.post('/api/event',json={'action':'new:omni'}).json()
        pid=first['project']['id']
        before=dict(self.app.store.project(pid)['data'])
        response=self.client.post('/api/language',json={'language':'en'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['language'],'en')
        self.assertEqual(self.app.store.project(pid)['data'],before)
        self.assertEqual(self.client.get('/api/state').json()['language'],'en')
        self.assertEqual(self.client.post('/api/language',json={'language':'xx'}).status_code,400)

    def test_recommendations_and_start_preserve_answers(self):
        self.register()
        body={'idea':'Кот и сова разговаривают','language':'ru','speech':True,'seconds':30}
        response=self.client.post('/api/recommend',json=body).json()
        self.assertEqual(response['recommended'],'omni')
        self.assertEqual([x['tokens'] for x in response['options']],[600,1300])
        body.update(speech=False)
        self.assertEqual(self.client.post('/api/recommend',json=body).json()['recommended'],'seedance')
        body.update(speech=True,language='en',seconds=15)
        self.assertEqual(self.client.post('/api/recommend',json=body).json()['recommended'],'omni')
        result=self.client.post('/api/start-recommended',json={**body,'route':'seedance'})
        self.assertEqual(result.status_code,200,result.text)
        p=self.app.store.project(result.json()['project']['id'])
        self.assertEqual(p['data']['audio_mode'],'en')
        self.assertEqual(p['data']['version'],'2.0-fast')
        self.assertIn(body['idea'],p['data']['brief']['idea'])
        self.assertEqual(p['state'],'intake')
        self.assertEqual(self.client.post('/api/recommend',json={**body,'seconds':-10}).status_code,400)

    def test_long_recommendation_durations(self):
        self.register()
        for seconds,cost in [(40,800),(50,1000),(60,1200)]:
            body={'idea':'Кот снимает мультфильм','language':'ru','speech':False,'seconds':seconds}
            result=self.client.post('/api/recommend',json=body)
            self.assertEqual(result.status_code,200)
            data=result.json()
            self.assertEqual(data['recommended'],'seedance')
            self.assertEqual(len(data['options']),2)
            self.assertEqual(data['options'][0]['tokens'],cost)
            self.assertEqual(data['options'][0]['seconds'],seconds)
            self.assertEqual(data['options'][1]['parts'],[30,15 if seconds==40 else 30])
            self.assertEqual(data['options'][1]['tokens'],2000 if seconds==40 else 2600)

    def test_seedance_sixty_second_story_is_two_thirty_second_parts(self):
        from cartoonbot.worker import Worker
        from cartoonbot.providers import DemoProvider
        self.register()
        body={'idea':'Кот и сова снимают мультфильм','language':'en','speech':True,'seconds':60,'route':'seedance'}
        result=self.client.post('/api/start-recommended',json=body)
        self.assertEqual(result.status_code,200,result.text)
        pid=result.json()['project']['id']
        for _ in range(20):
            p=self.app.store.project(pid)
            if p['state']!='intake':break
            self.client.post('/api/event',json={'action':f"p:{pid}:{p['revision']}:skip"})
        for expected,action in [('references','done'),('style','style=3d')]:
            p=self.app.store.project(pid)
            self.assertEqual(p['state'],expected)
            response=self.client.post('/api/event',json={'action':f"p:{pid}:{p['revision']}:{action}"})
            self.assertEqual(response.status_code,200,response.text)
        worker=Worker(self.app.store,self.app.config,DemoProvider(self.app.store,self.app.config),self.app.engine)
        self.assertTrue(worker.run_once())
        p=self.app.store.project(pid)
        self.assertEqual(p['state'],'script',p['data'].get('error'))
        self.assertEqual(p['data']['seconds'],30)
        child=self.app.store.project(p['data']['next_part'])
        self.assertEqual(child['data']['seconds'],30)
        self.assertEqual(child['data']['script']['scenes'][0]['start'],0)
        self.assertEqual(child['data']['script']['scenes'][-1]['end'],30)
        self.assertEqual(p['data']['script']['assets'],child['data']['script']['assets'])
        self.assertEqual(child['data']['previous_part'],pid)

    def test_auth_and_csrf(self):
        self.assertEqual(self.client.get('/api/state').status_code,401)
        self.register()
        self.assertEqual(self.client.post('/api/event',json={'action':'new:omni'},headers={'X-CSRF-Token':'wrong'}).status_code,403)
        self.assertEqual(self.client.post('/api/event',json={'action':'new:omni'},headers={'Origin':'https://evil.test'}).status_code,403)
        self.assertEqual(self.client.post('/api/logout',json={}).status_code,200)
        self.assertEqual(self.client.get('/api/state').status_code,401)
    def test_project_ownership_and_no_paths_in_response(self):
        self.register()
        result=self.client.post('/api/event',json={'action':'new:omni'}).json()
        pid=result['project']['id']
        with httpx.Client(base_url=self.base,headers={'Origin':self.base}) as other:
            self.register(other,'other@example.test')
            result=other.post('/api/event',json={'action':'open:'+pid}).json()
            self.assertIsNone(result['project']);self.assertEqual(result['projects'],[])
        self.assertNotIn(str(self.app.root),json.dumps(result))
    def test_upload_and_protected_media(self):
        self.register();self.client.post('/api/event',json={'action':'new:omni'})
        with self.app.store.transaction():
            uid=self.app.session(self.client.cookies.get('studio') and 'studio='+self.client.cookies.get('studio'))['uid']
            p=self.app.store.project(self.app.store.user(uid)['current']);p['state']='references';self.app.store.save(p)
        stream=io.BytesIO();Image.new('RGB',(32,32),'orange').save(stream,'PNG')
        result=self.client.post('/api/upload',files={'file':('ref.png',stream.getvalue(),'image/png')})
        self.assertEqual(result.status_code,200,result.text)
        with self.app.store.lock:
            p=self.app.store.project(p['id']);self.assertEqual(len(p['data']['refs']),1)
            url=self.app.file_url(uid,p['data']['refs'][0]['path'])
        self.assertEqual(self.client.get(url).status_code,200)
        with httpx.Client(base_url=self.base,headers={'Origin':self.base}) as other:
            self.register(other,'other@example.test');self.assertEqual(other.get(url).status_code,404)
        self.assertEqual(self.client.get(url,headers={'Range':'bytes=0-9'}).status_code,206)

    def test_signed_seedance_reference_is_public_but_limited_to_signed_media(self):
        import os
        from unittest.mock import patch
        from cartoonbot.reference_media import signed_url
        self.register()
        root=self.app.root/'media';source=root/'7'/'uploads'/'source.mp4'
        source.parent.mkdir(parents=True);source.write_bytes(b'approved reference video')
        public_base='https://studio.example.test'
        with patch.dict(os.environ,{'MEDIA_PUBLIC_BASE_URL':public_base,'MEDIA_SIGNING_KEY':'s'*48}):
            url=signed_url(root,source)
            route_url=self.base+url[len(public_base):]
            with httpx.Client(base_url=self.base) as external:
                response=external.get(route_url)
                head=external.head(route_url)
                partial=external.get(route_url,headers={'Range':'bytes=0-7'})
                invalid=external.get(route_url[:-64]+'0'*64)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.content,b'approved reference video')
            self.assertEqual(head.status_code,200)
            self.assertEqual(head.headers['content-length'],str(len(b'approved reference video')))
            self.assertEqual(partial.status_code,206)
            self.assertEqual(partial.content,b'approved')
            self.assertEqual(invalid.status_code,404)
    def test_prices_demo_and_no_payment(self):
        state=self.register();self.assertTrue(state['demo']);self.assertEqual(state['balance'],3000)
        self.assertEqual((state['packages'][0]['usd'],state['packages'][0]['rub']),('9.00',760))
        self.assertEqual(self.client.post('/api/event',json={'action':'pay:rf:trial'}).status_code,400)
        self.assertEqual(self.app.config.mode,'demo');self.assertEqual(self.app.config.token,'')
    def test_separate_user_sessions_and_password_hashing(self):
        self.register()
        with self.app.store.lock:row=self.app.store.db.execute('SELECT password FROM web_accounts').fetchone()
        self.assertNotIn('Test-password',row['password'])
        self.client.post('/api/logout',json={})
        self.assertEqual(self.client.post('/api/login',json={'email':'test@example.test','password':'wrong-password'}).status_code,401)
        self.assertEqual(self.client.post('/api/login',json={'email':'test@example.test','password':'Test-password-2026'}).status_code,200)
    def test_full_demo_video_via_browser_actions(self):
        from cartoonbot.worker import Worker
        from cartoonbot.providers import DemoProvider
        self.register()
        worker=Worker(self.app.store,self.app.config,DemoProvider(self.app.store,self.app.config),self.app.engine)
        def event(action='',text=''):
            response=self.client.post('/api/event',json={'action':action,'text':text})
            self.assertEqual(response.status_code,200,response.text)
            return response.json()
        def state():return self.client.get('/api/state').json()
        def press(action):
            p=state()['project'];return event(f"p:{p['id']}:{p['revision']}:{action}")
        def finish():
            self.assertTrue(worker.run_once())
            self.assertNotEqual(state()['project']['state'],'attention',state()['messages'][-1]['text'])
        event('new:omni');press('vlang=ru');press('source=no');event(text='История про ёжика и сову')
        for _ in range(12):
            if state()['project']['state']!='intake':break
            press('skip')
        finish();press('approve');press('continue_visuals');press('ratio=9:16');press('done');press('style=3d');finish()
        self.assertTrue(state()['project']['scenes'][0]['image'])
        p=state()['project']
        response=self.client.post('/api/card',json={'project':p['id'],'revision':p['revision'],'kind':'visual','scene':1,'instruction':'Измени ракурс, сохрани героев и стиль.'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['project']['state'],'busy')
        finish()
        self.assertEqual(state()['project']['state'],'visuals')
        press('approve');press('generate');finish();press('approve');finish()
        result=state();self.assertEqual(result['project']['state'],'finished')
        video=result['project']['scenes'][0]['video'];self.assertTrue(video)
        self.assertEqual(self.client.get(video,headers={'Range':'bytes=0-127'}).status_code,206)
        self.assertEqual(result['balance'],2800)
        # Switching projects must not mix the previous dialogue or media into the new one.
        event('new:photo');self.assertFalse(any(m['url'] for m in state()['messages']))
