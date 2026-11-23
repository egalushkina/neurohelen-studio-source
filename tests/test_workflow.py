import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from cartoonbot.config import Config
from cartoonbot.store import Store
from cartoonbot.engine import Engine, FIELDS
from cartoonbot.providers import DemoProvider, LiveProvider
from cartoonbot.worker import Worker
from cartoonbot.i18n import TEXT, locale
from cartoonbot import media


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.cfg=Config(root=Path(self.tmp.name),workers=1)
        self.cfg.prepare(); self.s=Store(self.cfg.root/'test.sqlite3')
        self.e=Engine(self.s,self.cfg); self.provider=DemoProvider(self.s,self.cfg)
        self.w=Worker(self.s,self.cfg,self.provider,self.e); self.counter=0

    def tearDown(self):
        self.s.db.close(); self.tmp.cleanup()

    def event(self,action='',text='',uid=1,language='ru',**kw):
        self.counter+=1
        ev={'id':self.counter,'user_id':uid,'language':language,'action':action,'text':text,**kw}
        self.e.handle(ev); return ev

    def current(self,uid=1):
        return self.s.project(self.s.user(uid)['current'])

    def press(self,action,uid=1,language='ru'):
        p=self.current(uid)
        return self.event(f"p:{p['id']}:{p['revision']}:{action}",uid=uid,language=language)

    def finish_job(self):
        self.assertTrue(self.w.run_once())
        p=self.current()
        self.assertNotEqual(p['state'],'attention',p['data'].get('error'))
        return p

    def script(self,route='omni',language='ru',seconds=10):
        self.event('new:'+route,language=language)
        self.press('audio=none' if route=='seedance' else 'vlang='+language,language=language)
        self.press('source=no',language=language)
        if route=='omni':
            with self.s.transaction():
                p=self.current(); p['data']['seconds']=seconds; self.s.save(p)
            self.event(text='История про ёжика и сову',language=language)
        while self.current()['state']=='intake': self.press('skip',language=language)
        if self.current()['data'].get('references_before_script'):
            self.press('no_refs',language=language); self.press('style=3d',language=language)
        return self.finish_job()

    def visuals(self,route='omni',language='ru',seconds=10):
        self.script(route,language,seconds); self.press('approve',language=language); self.press('continue_visuals',language=language); self.press('ratio=9:16',language=language)
        if route=='omni':
            self.press('done',language=language); self.press('style=3d',language=language)
        else:
            if self.current()['state']=='style':
                self.press('style=3d',language=language); self.press('done',language=language)
        p=self.finish_job()
        if route=='omni' and seconds>10:
            self.press('approve',language=language); p=self.finish_job()
        return p

    def test_omni_style_back_returns_to_reference_upload(self):
        self.script(); self.press('approve'); self.press('continue_visuals'); self.press('ratio=9:16')
        self.press('no_refs')
        self.assertEqual(self.current()['state'],'style')
        self.press('style_refs_back')
        self.assertEqual(self.current()['state'],'references')
        self.assertFalse(self.current()['data'].get('refs_ready'))
        self.event(media={'kind':'photo','path':'cat.png'})
        self.assertEqual(len(self.current()['data']['refs']),1)
        self.press('done')
        self.assertEqual(self.current()['state'],'style')
        self.press('style_refs_back')
        self.assertEqual(len(self.current()['data']['refs']),1)
        self.press('done'); self.press('style=3d')
        self.assertEqual(self.current()['state'],'busy')

    def test_script_summary_fifty_seconds_and_configured_tokens(self):
        self.cfg.token_prices={'omni':12}
        self.script(seconds=50); before=self.s.user(1)['balance']; self.press('approve')
        p=self.current()
        self.assertEqual(p['state'],'script_summary')
        self.assertEqual(p['data']['script_summary'],{'seconds':50,'tokens':60})
        count=self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        self.press('ratio=9:16'); self.assertEqual(self.current()['state'],'script_summary')
        self.press('continue_visuals'); self.assertEqual(self.current()['state'],'ratio')
        self.assertEqual(self.s.user(1)['balance'],before)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],count)

    def test_summary_cancel_keeps_script_and_does_not_generate(self):
        self.script(seconds=40); self.press('approve'); p=self.current()
        self.assertEqual(p['data']['script_summary']['tokens'],800)
        self.press('cancel_visuals')
        self.assertIsNone(self.s.user(1)['current'])
        self.assertEqual(self.s.project(p['id'])['state'],'script')
        self.assertFalse(self.w.run_once())

    def test_seedance_summary_price(self):
        self.cfg.token_prices={'seedance_2.0':25}
        self.script('seedance'); self.press('approve')
        self.assertEqual(self.current()['data']['script_summary'],{'seconds':15,'tokens':None})

    def test_full_omni_no_subtitles_has_no_processing(self):
        self.visuals(); self.press('approve'); self.press('generate'); p=self.finish_job()
        before=Path(p['data']['assembled']).read_bytes()
        count=self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        with patch.object(self.provider,'transcribe',side_effect=AssertionError('must not run')), patch('cartoonbot.media.burn_captions',side_effect=AssertionError('must not render')):
            ev=self.press('approve')
            self.e.handle(ev)
            self.assertTrue(self.w.run_once())
            self.assertFalse(self.w.run_once())
        p=self.current()
        self.assertEqual(p['state'],'finished')
        self.assertEqual(p['data']['final'],p['data']['assembled'])
        self.assertEqual(Path(p['data']['final']).read_bytes(),before)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],count+1)
        self.assertTrue(p['data']['reels_text'])
        self.assertEqual(self.s.user(1)['reserved'],0)
        self.assertEqual(self.s.user(1)['balance'],10000-200)

    def test_legacy_subtitle_project_finishes_on_open(self):
        self.event('new:omni')
        p=self.current(); p['state']='subtitles'; p['data']['assembled']='existing.mp4'
        self.s.save(p)
        self.event('open:'+p['id'])
        self.finish_job()
        self.assertEqual(self.current()['state'],'finished')
        self.assertEqual(self.current()['data']['final'],'existing.mp4')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)

    def test_start_shows_text_without_welcome_video(self):
        folder=self.cfg.root/'media'/'welcome'; folder.mkdir()
        (folder/'owl.mp4').write_bytes(b'video')
        self.event(text='/start')
        rows=[json.loads(r[0]) for r in self.s.db.execute('SELECT body FROM outbox')]
        self.assertFalse(any(row['file'] for row in rows))
        self.assertIn('Добро пожаловать',rows[0]['text'])
        self.assertNotIn('субтитр',rows[0]['text'])
        self.s.db.execute('DELETE FROM outbox')
        self.event('home')
        self.assertFalse(any(json.loads(r[0])['file'] for r in self.s.db.execute('SELECT body FROM outbox')))

    def test_existing_script_intent_keeps_selected_route_and_language(self):
        for route, lang, phrase in [('omni','ru','У меня уже есть сценарий'), ('omni','uk','У мене вже є сценарій'), ('seedance','en','I already have a script')]:
            with self.subTest(route=route,lang=lang):
                self.event('new:'+route,language=lang)
                self.press('audio=en' if route=='seedance' else 'vlang='+lang,language=lang)
                self.press('source=no',language=lang)
                before=self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0]
                self.event(text=phrase,language=lang)
                self.assertEqual(self.current()['state'],'paste_script')
                self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],before)
                self.event(text='Сцена 1: Сова встречает ёжика. Сова: Привет!',language=lang)
                self.assertEqual(self.current()['state'],'script_route')
                self.press('script_route='+route,language=lang)
                if route=='omni': self.press('vlang='+lang,language=lang)
                p=self.current()
                self.assertEqual(p['state'],'supplied_review')
                self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],before)
                self.assertEqual(p['data']['route'],route)
                self.assertEqual(p['data']['lang'],'ru' if route=='seedance' else lang)
                self.assertIn('Сова встречает',p['data']['supplied_script'])
                self.press('prepare_supplied',language=lang)
                self.finish_job()

    def test_script_intent_does_not_consume_story_or_negation(self):
        for text in ('У меня нет сценария', 'История про сценариста, у него есть сценарий', 'I do not have a script'):
            self.assertFalse(self.e.supplied_script_intent(text)[0])
        self.assertEqual(self.e.supplied_script_intent('У меня есть сценарий:\nСова летит.'),(True,'Сова летит.'))

    def test_package_catalog_does_not_charge_or_create_jobs(self):
        from cartoonbot.catalog import PACKAGES,number
        for lang in ('ru','en','uk'):
            self.event(text='/start',language=lang)
            before=dict(self.s.user(1))
            self.event('token_shop',language=lang)
            for key,_,tokens,rubles in PACKAGES:
                self.event('package:'+key,language=lang)
                body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
                self.assertIn(number(tokens),body['text'])
                self.assertIn(number(rubles)+' ₽',body['text'])
                self.assertEqual(self.s.user(1)['balance'],before['balance'])
                self.assertEqual(self.s.user(1)['reserved'],before['reserved'])
            self.event('package:unknown',language=lang)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM ledger').fetchone()[0],0)

    def test_first_frame_approval_and_revision_gate_remaining_scenes(self):
        self.script(seconds=30); self.press('approve'); self.press('continue_visuals')
        self.press('ratio=9:16'); self.press('done'); self.press('style=3d')
        self.finish_job()
        self.assertEqual(set(self.current()['data']['images']),{'1'})
        self.assertTrue(self.current()['data']['first_frame_pending'])
        self.assertFalse(self.w.run_once())
        original=self.current()['data']['images']['1']['path']
        self.press('edit'); self.event(text='Сделай сове зелёные очки')
        self.finish_job()
        revised=self.current()['data']['images']['1']['path']
        self.assertNotEqual(original,revised)
        self.assertEqual(set(self.current()['data']['images']),{'1'})
        self.press('approve')
        with patch.object(self.provider,'image',wraps=self.provider.image) as generate:
            self.finish_job()
        self.assertEqual([c.args[-1] for c in generate.call_args_list],[2,3])
        for call in generate.call_args_list: self.assertIn(revised,call.args[2])
        self.assertFalse(self.current()['data'].get('first_frame_pending'))
        self.assertEqual(self.current()['data']['images']['1']['path'],revised)
        self.press('approve'); self.assertEqual(self.current()['state'],'quote')

    def test_seedance_prices_are_the_same_with_or_without_analyzed_source(self):
        self.visuals('seedance'); self.press('approve')
        expected={'2.5':700,'2.0':550,'2.0-fast':450}
        for has_video in (False,True):
            p=self.current(); p['state']='video_version'
            p['data']['reference_video_planned']=True
            p['data']['refs']=[{'kind':'video','path':'reference.mp4','seconds':15}] if has_video else []
            self.s.save(p); self.e.show(p)
            body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
            self.assertEqual(len(body['buttons']),3)
            for row,(version,tokens) in zip(body['buttons'],expected.items()):
                self.assertIn(str(tokens),row[0][0])
            self.press('version=2.5')
            body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
            self.assertIn(str(expected['2.5']),body['text'])

    def test_omni_scene_price_total_and_edit_price(self):
        self.visuals(seconds=60); self.press('approve')
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertIn('Сцен к созданию: 6',body['text'])
        self.assertIn('1200',body['buttons'][0][0][0])
        p=self.current(); p['data']['video_edits']=[{'id':2,'instruction':'Change camera'}]
        self.s.save(p); self.e.show(p)
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertIn('Сцен к созданию: 1',body['text'])
        self.assertIn('200',body['buttons'][0][0][0])

    def test_seedance_uk_pipeline(self):
        self.visuals('seedance','uk')
        self.assertEqual(len(self.current()['data']['images']),1)
        self.press('approve',language='uk'); self.press('version=2.5',language='uk'); self.press('generate',language='uk'); self.finish_job()
        self.press('approve',language='uk'); self.finish_job()
        self.assertEqual(self.current()['data']['lang'],'uk')
        self.assertEqual(self.current()['state'],'finished')

    def test_duplicate_and_stale_generation(self):
        self.visuals(); self.press('approve'); old=copy.deepcopy(self.current())
        ev=self.press('generate'); self.e.handle(ev)
        self.event(f"p:{old['id']}:{old['revision']}:generate")
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM jobs WHERE kind='video'").fetchone()[0],1)
        self.assertEqual(self.s.user(1)['reserved'],200)

    def test_user_isolation(self):
        self.event('new:omni'); p=self.current()
        self.event(f"p:{p['id']}:{p['revision']}:vlang=en",uid=2)
        self.assertEqual(self.s.project(p['id'])['state'],'video_language')
        self.assertIsNone(self.s.project(p['id'],2))

    def test_manual_locale_and_film_language_independent(self):
        self.event('new:omni',language='uk-UA'); self.press('vlang=en',language='uk-UA')
        pid=self.current()['id']
        self.event('lang:ru',language='uk-UA')
        self.event(text='/start',language='en-US')
        self.assertEqual(self.s.user(1)['lang'],'ru')
        self.assertEqual(self.s.project(pid)['data']['lang'],'en')
        self.event('lang:auto',language='uk-UA')
        self.assertEqual(self.s.user(1)['lang'],'uk')

    def test_restart_preserves_project_and_quarantines_running_job(self):
        self.event('new:omni'); p=self.current()
        with self.s.transaction(): jid=self.s.enqueue(p,'script')
        self.s.claim()
        self.s.db.close(); self.s=Store(self.cfg.root/'test.sqlite3')
        self.assertEqual(self.s.recover(),1)
        self.assertEqual(self.s.project(p['id'])['state'],'attention')
        self.assertIsNone(self.s.claim())
        self.assertEqual(self.s.db.execute('SELECT status FROM jobs WHERE id=?',(jid,)).fetchone()[0],'attention')

    def test_multi_frame_edit_preserves_untouched(self):
        self.visuals(seconds=30); before=copy.deepcopy(self.current()['data']['images'])
        self.press('edit'); self.event(text='В 1 и 3 — красная куртка'); self.finish_job()
        self.assertEqual([x['id'] for x in self.current()['data']['edits']],[1,3])
        self.press('apply'); p=self.finish_job()
        self.assertEqual(p['data']['images']['2'],before['2'])
        self.assertNotEqual(p['data']['images']['1']['path'],before['1']['path'])
        self.assertNotEqual(p['data']['images']['3']['path'],before['3']['path'])
        self.assertEqual(p['state'],'visuals')

    def test_ambiguous_edits_do_not_generate_images(self):
        self.visuals(seconds=20); self.press('edit'); self.event(text='Сделай куртку красной'); self.finish_job()
        self.assertEqual(self.current()['state'],'edit_input')
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM jobs WHERE kind='edit_visuals'").fetchone()[0],0)

    def test_reference_buttons_follow_uploaded_files(self):
        self.script(); self.press('approve'); self.press('continue_visuals'); self.press('ratio=9:16')
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(body['buttons'][0][0][0],'✨ Создай сам')
        self.event(media={'kind':'photo','path':'/test/photo.png'})
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(body['buttons'][0][0][0],'Готово')
        self.press('no_refs'); self.assertEqual(len(self.current()['data']['refs']),1)
        self.press('done'); self.assertEqual(self.current()['state'],'style')

    def test_create_own_skips_empty_references(self):
        self.script(); self.press('approve'); self.press('continue_visuals'); self.press('ratio=9:16')
        self.press('no_refs'); self.assertEqual(self.current()['state'],'style')
        self.assertEqual(self.current()['data']['refs'],[])

    def test_reuse_saved_script_without_generation_or_overwrite(self):
        source=copy.deepcopy(self.script(seconds=50))
        count=self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        self.event('scripts:0')
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.event(body['buttons'][0][0][1])
        p=self.current()
        self.assertNotEqual(p['id'],source['id'])
        self.assertEqual(p['data']['script'],source['data']['script'])
        self.assertEqual(p['data']['images'],{})
        self.assertEqual(self.s.project(source['id']),source)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],count)
        self.press('approve'); self.assertEqual(self.current()['state'],'script_summary')
        self.assertEqual(self.current()['data']['script_summary']['seconds'],50)

    def test_saved_script_list_excludes_incomplete_and_other_users(self):
        source=self.script(); self.event('new:omni')
        self.event('scripts:0')
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(len(body['buttons']),1)
        self.event(f"use_script:{source['id']}:{source['revision']}",uid=2)
        self.assertIsNone(self.s.user(2)['current'])
        self.event('scripts:0',uid=2)
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(body['buttons'],[])

    def test_pasted_script_is_preserved_and_skips_idea_questions(self):
        self.event('script_menu'); self.event('paste_own')
        self.assertEqual(self.current()['state'],'paste_script')
        text='Ёжик: «Я тебя ждал». Сова садится рядом. Конец.'
        self.event(text=text); self.press('script_route=omni'); self.press('vlang=ru')
        self.assertEqual(self.current()['state'],'supplied_review')
        self.assertEqual(self.current()['data']['supplied_script'],text)
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
        self.press('prepare_supplied')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)
        self.finish_job()
        self.assertEqual(self.current()['state'],'script')
        self.assertEqual(self.current()['data']['supplied_script'],text)

    def test_pasted_thirty_second_script_has_no_intake_or_audio_questions(self):
        self.event('paste_own'); text='Duration: 30 sec\n1. 0–15 s\nTeacher: Hello.\n2. 15–30 s\nStudent: Thank you.'
        self.event(text=text); self.press('script_route=seedance')
        self.assertEqual(self.current()['state'],'supplied_review')
        self.assertEqual(self.current()['data']['seconds'],30)
        self.press('prepare_supplied')
        with patch.object(self.provider,'assess_seedance',side_effect=AssertionError('Explicit duration')):
            self.finish_job()
        self.assertEqual(self.current()['data']['supplied_script'],text)
        self.assertEqual(self.current()['data']['script']['scenes'][-1]['end'],30)

    def test_pasted_seedance_script_uses_english_audio(self):
        self.event('paste_own'); self.event(text='A cat says hello.')
        self.press('script_route=seedance'); self.assertEqual(self.current()['state'],'supplied_review')
        self.assertEqual(self.current()['state'],'supplied_review')
        self.assertEqual(self.current()['data']['lang'],'en')
        self.assertTrue(self.current()['data']['lip'])


    def test_seedance_selects_version_after_visual_approval(self):
        self.visuals('seedance')
        self.press('approve'); self.assertEqual(self.current()['state'],'video_version')
        self.press('generate'); self.assertEqual(self.current()['state'],'video_version')
        self.press('version=2.5'); self.assertEqual(self.current()['state'],'quote')
        self.assertEqual(self.current()['data']['version'],'2.5')

    def test_seedance_image_order_matches_asset_numbers(self):
        from cartoonbot.seedance_prompts import references
        d={'script':{'assets':[{'id':1,'name':'Cat','description':'Red cat'},{'id':2,'name':'Ball','description':'Blue ball'}]},
           'images':{'2':{'path':'ball.png'},'1':{'path':'cat.png'}},'refs':[{'kind':'photo','path':'user.png','purpose':'eyes'}]}
        paths,labels=references(d)
        self.assertEqual(paths,['cat.png','ball.png'])
        self.assertTrue(labels[0].startswith('image 1: Cat'))
        self.assertTrue(labels[1].startswith('image 2: Ball'))

    def test_edit_yes_keeps_original_request_and_question(self):
        self.visuals(seconds=20); self.press('edit')
        question='На изображении 1 пересадить ёжика на сиденье?'
        with patch.object(self.provider,'edits',return_value={'clarification':question,'changes':[]}):
            self.event(text='1 изображение: ёжик сидит на подлокотнике'); self.finish_job()
        self.assertEqual(self.current()['data']['edit_history'][0]['text'],'1 изображение: ёжик сидит на подлокотнике')
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(body['text'],question)
        def resolve(job,d,text,ids):
            self.assertEqual(text,'Да'); self.assertEqual(d['edit_history'][-1]['text'],question)
            return {'clarification':'','changes':[{'id':1,'instruction':'Пересадить ёжика на сиденье кресла'}]}
        with patch.object(self.provider,'edits',side_effect=resolve):
            self.event(text='Да'); self.finish_job()
        self.assertEqual(self.current()['state'],'edit_confirm')
        self.assertNotIn('edit_history',self.current()['data'])

    def test_old_approve_works_after_edit_if_visual_unchanged(self):
        source=self.visuals(); old=f"p:{source['id']}:{source['revision']}:approve"
        self.press('edit'); count=self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        self.event(old); self.assertEqual(self.current()['state'],'quote')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],count)
        self.assertEqual(self.current()['data']['images'],source['data']['images'])

    def test_approve_without_changes_and_back_on_script(self):
        self.script(); self.press('edit'); self.press('cancel_edit')
        self.assertEqual(self.current()['state'],'script')
        self.press('edit'); self.press('approve_current')
        self.assertEqual(self.current()['state'],'script_summary')

    def test_old_visual_approval_rejected_after_image_changed(self):
        source=self.visuals(); old=f"p:{source['id']}:{source['revision']}:approve"
        with self.s.transaction():
            p=self.current(); p['data']['images']['1']['path']='new.png'; self.s.save(p)
        self.press('edit'); self.event(old)
        self.assertEqual(self.current()['state'],'edit_input')
        self.press('approve_current'); self.assertEqual(self.current()['state'],'quote')

    def test_video_confirmation_has_no_fake_price_or_unrelated_model(self):
        self.visuals(); self.press('approve')
        text=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])['text']
        self.assertIn('Omni Flash',text); self.assertIn('Итого: 200 токенов',text)
        self.assertNotIn('Seedance',text); self.assertNotIn('кредитов',text)

    def test_thirty_second_source_keeps_duration(self):
        self.event('new:seedance'); self.press('audio=ru'); self.press('source=yes'); self.press('source_mode=analysis')
        self.provider.analyze=Mock(return_value={'summary':'Two characters talking','route':'seedance'})
        self.event(media={'kind':'video','path':'source.mp4','seconds':30})
        self.finish_job()
        self.assertEqual(self.current()['state'],'recreate_changes')
        self.assertEqual(self.current()['data']['seconds'],30)
        self.press('recreate_keep'); self.press('done'); self.press('style=3d')
        with patch.object(self.provider,'assess_seedance',side_effect=AssertionError('Use source duration')):
            self.finish_job()
        self.assertEqual(self.current()['data']['seconds'],30)
        self.assertEqual(self.current()['data']['version'],'2.5')

    def test_omni_source_branch_asks_changes_and_exact_dialogue(self):
        self.event('new:omni'); self.press('vlang=ru')
        self.assertEqual(self.current()['state'],'omni_source')
        self.press('source=yes')
        self.assertEqual(self.current()['state'],'omni_source_upload')
        self.event(media={'kind':'video','path':'source.mp4','seconds':61})
        self.assertEqual(self.current()['state'],'omni_source_upload')
        self.provider.analyze=Mock(return_value={'summary':'Cat beside woman','route':'seedance','audio_unknown':True,
                                                 'transcript':'Женщина: Доброе утро! Кот: Мяу.'})
        self.event(media={'kind':'video','path':'source.mp4','seconds':60})
        self.finish_job()
        self.assertEqual(self.current()['data']['route'],'omni')
        self.assertEqual(self.current()['state'],'recreate_changes')
        out=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertIn('Женщина: Доброе утро!',out['text'])
        self.event(text='Сохранить действия, заменить кота')
        self.assertEqual(self.current()['state'],'omni_source_dialogue')
        self.press('source_dialogue=edit')
        self.assertEqual(self.current()['state'],'omni_source_dialogue_edit')
        self.event(text='Женщина: Доброе утро!\nКот: Доброе утро!')
        self.assertEqual(self.current()['state'],'references')
        self.assertEqual(self.current()['data']['source_dialogue'],'Женщина: Доброе утро!\nКот: Доброе утро!')
        self.assertNotIn('source_dialogue_is_transcript',self.current()['data'])
        self.press('no_refs'); self.press('style=3d'); self.finish_job()
        self.assertEqual(self.current()['state'],'script')
        self.event('home'); self.event('new:omni'); self.press('vlang=ru'); self.press('source=no')
        self.assertEqual(self.current()['state'],'intake')

        self.event('home'); self.event('new:omni'); self.press('vlang=ru'); self.press('source=yes')
        self.event(media={'kind':'video','path':'source.mp4','seconds':10}); self.finish_job()
        self.event(text='Без изменений')
        self.press('source_dialogue=recognized')
        self.assertEqual(self.current()['state'],'references')
        self.assertEqual(self.current()['data']['source_dialogue'],'Женщина: Доброе утро! Кот: Мяу.')
        self.assertTrue(self.current()['data']['source_dialogue_is_transcript'])

    def test_omni_reference_transcript_is_forwarded_verbatim_to_scriptwriter(self):
        transcript='Мама: Спи, малыш.\nПапа: Я рядом.'
        current={'title':'Источник','assets':[], 'scenes':[]}
        d={'lang':'ru','route':'omni','seconds':10,'script':current,'brief':{'idea':'Источник','dialogue':transcript},
           'omni_source_flow':True,'source_dialogue':transcript,'source_dialogue_is_transcript':True,
           'recreate':True,'source':{'seconds':10},'refs':[]}
        provider=LiveProvider(self.s,self.cfg)
        try:
            with patch.object(provider,'structured',return_value=current) as call, patch('cartoonbot.providers.validate_script',return_value=current):
                provider.script({'id':'job-1'},d)
        finally:
            provider.client.close()
        payload=call.call_args.args[2]
        instructions=call.call_args.args[1]
        self.assertEqual(payload['source_dialogue'],transcript)
        self.assertIn('SOURCE AUDIO TRANSCRIPT AUTHORITY',instructions)
        self.assertIn('preserve every recognized word',payload['source_dialogue_rule'].lower())

    def test_seedance_reference_video_is_analysis_only_and_regular_price(self):
        from cartoonbot.catalog import has_video_reference, seedance_tokens
        self.event('new:seedance'); self.press('audio=ru'); self.press('source=yes')
        self.assertEqual(self.current()['state'],'seedance_source_upload')
        self.assertEqual(self.current()['data']['seedance_reference_mode'],'analysis')
        self.event(media={'kind':'video','path':'too-long.mp4','seconds':31})
        self.assertEqual(self.current()['state'],'seedance_source_upload')
        self.provider.analyze=Mock(return_value={'summary':'A dog meets a man','route':'seedance',
            'characters':[{'id':1,'role':'dog','appearance':'brown dog','appearances':'all shots','uncertainty':''},
                          {'id':2,'role':'man','appearance':'adult man','appearances':'all shots','uncertainty':''}],
            'shots':[]})
        self.event(media={'kind':'video','path':'source.mp4','seconds':15}); self.finish_job()
        d=self.current()['data']
        self.assertEqual(self.current()['state'],'recreate_changes')
        self.assertFalse(has_video_reference(d))
        self.assertFalse(any(r['kind']=='video' for r in d['refs']))
        self.assertEqual(seedance_tokens(d,'2.5',{}),700)
        self.assertEqual(seedance_tokens(d,'2.0',{}),550)
        self.assertEqual(seedance_tokens(d,'2.0-fast',{}),450)
        self.event(text='Replace the dog with the cat in my photo; keep the man')
        self.event(media={'kind':'photo','path':'cat.png'})
        self.press('done'); self.press('style=3d'); self.finish_job()
        self.assertEqual(self.current()['state'],'reference_assignment')
        self.assertEqual(self.current()['data']['script']['assets'][0]['reference_photo_ids'],[])
        self.press('assign_reference=1')
        self.assertEqual(self.current()['data']['script']['assets'][0]['reference_photo_ids'],[1])

    def test_thirty_second_source_is_analysis_only_and_uses_base_price(self):
        from cartoonbot.catalog import has_video_reference, seedance_tokens
        self.event('new:seedance'); self.press('audio=ru'); self.press('source=yes')
        self.provider.analyze=Mock(return_value={'summary':'The source clip summary','route':'seedance',
            'characters':[],'shots':[],'transcript':'','has_dialogue':False})
        self.event(media={'kind':'video','path':'source.mp4','seconds':30}); self.finish_job()
        d=self.current()['data']
        self.assertFalse(has_video_reference(d))
        self.assertFalse(any(r['kind']=='video' for r in d['refs']))
        self.assertEqual(d['seconds'],30); self.assertEqual(d['version'],'2.5')
        self.assertEqual(seedance_tokens(d,'2.5',{}),1300)
        self.assertEqual(d['brief']['idea'],'The source clip summary')

    def test_legacy_direct_projects_do_not_transfer_video_or_charge_reference_price(self):
        from cartoonbot.catalog import has_video_reference, seedance_tokens
        self.script('seedance'); self.visuals()
        p=self.current(); d=p['data']
        d.update(route='seedance',seedance_source_flow=True,recreate=True,seedance_reference_mode='direct',seconds=30,version='2.5')
        d['refs']=[{'kind':'video','path':'source.mp4','seconds':30}]
        p['state']='video_version'; self.s.save(p)
        self.assertFalse(has_video_reference(d))
        self.assertEqual(seedance_tokens(d,'2.5',{}),1300)
        self.press('version=2.5')
        self.assertEqual(self.current()['state'],'quote')
        last=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertIn('1300',last['text'])
        self.assertIn('анализируется для подготовки сценария',last['text'])

    def test_seedance_source_choice_before_intake(self):
        self.event('new:seedance'); self.press('audio=ru')
        self.assertEqual(self.current()['state'],'seedance_source')
        self.press('source=yes')
        self.assertEqual(self.current()['state'],'seedance_source_upload')
        self.event(media={'kind':'video','path':'source.mp4','seconds':31})
        self.assertEqual(self.current()['state'],'seedance_source_upload')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
        self.provider.analyze=Mock(return_value={'summary':'Two distinct characters in two rooms','route':'omni','audio_unknown':True})
        self.event(media={'kind':'video','path':'source.mp4','seconds':15})
        self.finish_job()
        p=self.current()
        self.assertEqual(p['state'],'recreate_changes')
        self.assertFalse(any(r['kind']=='video' for r in p['data']['refs']))
        self.assertEqual(p['data']['audio_mode'],'ru')
        self.assertEqual(p['data']['route'],'seedance')
        self.assertTrue(p['data']['recreate'])
        self.assertEqual(p['data']['refs'],[])
        self.press('recreate_keep'); self.assertEqual(self.current()['state'],'references')
        self.event('home'); self.event('new:seedance'); self.press('audio=none'); self.press('source=no')
        self.assertEqual(self.current()['state'],'intake')
        while self.current()['state']=='intake':
            self.assertNotEqual(self.e.next_field(self.current()['data']),'ref_plan')
            self.press('skip')
        self.assertEqual(self.current()['state'],'references')
        self.press('no_refs');self.press('style=3d')
        self.finish_job()

    def test_style_reference_is_analyzed_then_approved_separately_from_identity(self):
        self.event('new:seedance');self.press('audio=none');self.press('source=no')
        while self.current()['state']=='intake':self.press('skip')
        self.press('no_refs')
        self.press('style=reference');self.assertEqual(self.current()['state'],'style_reference_upload')
        self.event(media={'kind':'video','path':'wrong.mp4','seconds':1})
        self.assertEqual(self.current()['state'],'style_reference_upload')
        self.event(media={'kind':'photo','path':'style.png'})
        self.finish_job();self.assertEqual(self.current()['state'],'style_reference_review')
        self.assertEqual(self.current()['data']['refs'],[])
        self.press('style_reference_approve')
        self.assertEqual(self.current()['state'],'busy')
        self.assertEqual(self.current()['data']['style'],'reference')

    def test_home_has_no_recreate_tab(self):
        self.event('home')
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertNotIn('new:recreate',str(body))
        self.assertIn('new:seedance',str(body))

    def test_home_during_generation_requires_explicit_cancel(self):
        self.event('new:omni'); self.press('vlang=ru'); self.press('source=no'); self.event(text='История про кота')
        before=self.current()
        job=dict(self.s.db.execute("SELECT * FROM jobs WHERE status='queued'").fetchone())
        self.event('home')
        self.assertEqual(self.current()['revision'],before['revision'])
        self.assertEqual(self.current()['state'],'busy')
        self.event('keep_generation:'+job['id'])
        self.assertEqual(self.current()['state'],'busy')
        self.event('cancel_generation:'+job['id'],uid=2)
        self.assertEqual(self.s.db.execute('SELECT status FROM jobs WHERE id=?',(job['id'],)).fetchone()[0],'queued')
        self.event('cancel_generation:'+job['id'])
        self.assertEqual(self.s.project(before['id'])['state'],'cancelled')
        self.assertFalse(self.w.run_once())

    def test_cancel_running_visuals_preserves_frame_and_stops_next(self):
        self.script('omni',seconds=30)
        self.press('approve'); self.press('continue_visuals'); self.press('ratio=9:16')
        self.press('done'); self.press('style=3d'); self.finish_job()
        self.press('approve')
        original=self.provider.image
        calls=[]
        def generate(job,*args,**kwargs):
            calls.append(args[-1])
            path=original(job,*args,**kwargs)
            self.event('cancel_generation:'+job['id'])
            return path
        with patch.object(self.provider,'image',side_effect=generate):
            self.w.run_once()
        self.assertEqual(calls,[2])
        p=self.current()
        self.assertEqual(p['state'],'cancelled')
        self.assertEqual(set(p['data']['images']),{'1','2'})

    def test_partial_video_delivered_and_only_failed_scene_retried(self):
        self.visuals('omni',seconds=30)
        p=self.current(); p['data']['safety_review_attempted']=True; self.s.save(p)
        self.press('approve'); self.press('generate')
        original=self.provider.video; calls=[]
        def generate(job,d,scene,*args,**kwargs):
            calls.append(scene['id'])
            persisted=self.current()['data']['videos']
            self.assertEqual(set(persisted),{str(i) for i in range(1,scene['id'])})
            delivered=[json.loads(row[0]) for row in self.s.db.execute('select body from outbox')]
            for result in persisted.values():
                self.assertEqual(sum(x.get('file')==result['path'] for x in delivered),1)
            if scene['id']==3: raise ValueError('google_minor_safety')
            return original(job,d,scene,*args,**kwargs)
        with patch.object(self.provider,'video',side_effect=generate): self.w.run_once()
        p=self.current(); self.assertEqual(p['state'],'attention')
        self.assertEqual(p['data']['error_scene_id'],3)
        first=copy.deepcopy(p['data']['videos'])
        self.assertEqual(set(first),{'1','2'})
        delivered=[json.loads(row[0]) for row in self.s.db.execute('select body from outbox')]
        for result in first.values(): self.assertEqual(sum(x.get('file')==result['path'] for x in delivered),1)
        self.press('error_edit_scene'); self.press('edit_video_content'); self.event(text='Флакон спокойно стоит и говорит короткую реплику.')
        self.assertEqual(self.current()['state'],'edit_confirm')
        self.press('apply'); self.assertEqual(self.current()['data']['resume_scene_ids'],[3])
        self.assertEqual(self.e.quote(self.current()['data'])[1],10)
        self.press('generate'); calls=[]
        def retry(job,d,scene,*args,**kwargs):
            calls.append(scene['id']); return original(job,d,scene,*args,**kwargs)
        with patch.object(self.provider,'video',side_effect=retry): self.w.run_once()
        self.assertEqual(calls,[3]); self.assertEqual(self.current()['state'],'video')
        for key in first: self.assertEqual(self.current()['data']['videos'][key],first[key])

    def test_omni_video_and_revision_only_receive_approved_scene_frame(self):
        self.visuals('omni',seconds=20)
        p=self.current()
        p['data']['refs']=[{'kind':'photo','path':'original-other-character.png'}]
        self.s.save(p)
        self.press('approve'); self.press('generate')
        original=self.provider.video; calls=[]
        def generate(job,d,scene,refs,*args,**kwargs):
            self.assertEqual(refs,[d['images'][str(scene['id'])]['path']])
            self.assertEqual(d['refs'][0]['path'],'original-other-character.png')
            calls.append(scene['id'])
            return original(job,d,scene,refs,*args,**kwargs)
        with patch.object(self.provider,'video',side_effect=generate): self.finish_job()
        self.assertEqual(calls,[1,2])
        p=self.current(); p['data']['video_edits']=[{'id':2,'instruction':'Slow camera'}]
        p['state']='quote'; self.s.save(p); self.press('generate')
        with patch.object(self.provider,'video',side_effect=generate): self.finish_job()
        self.assertEqual(calls,[1,2,2])

    def test_explicit_edit_restores_skipped_scene_and_preserves_other_clips(self):
        self.visuals('omni',seconds=20)
        p=self.current(); p['data']['skipped_scenes']=[1]
        self.s.save(p); self.press('approve'); self.press('generate'); self.finish_job()
        second=copy.deepcopy(self.current()['data']['videos']['2'])
        self.press('edit'); self.press('edit_video_content'); self.event(text='1: Перегенерировать')
        self.finish_job(); self.press('apply')
        self.assertNotIn(1,self.current()['data']['skipped_scenes'])
        self.press('generate')
        original=self.provider.video; calls=[]
        def generate(job,d,scene,*args,**kwargs):
            calls.append(scene['id']); return original(job,d,scene,*args,**kwargs)
        with patch.object(self.provider,'video',side_effect=generate): self.finish_job()
        self.assertEqual(calls,[1])
        self.assertEqual(self.current()['data']['videos']['2'],second)
        self.assertEqual(set(self.current()['data']['videos']),{'1','2'})

    def test_live_scene_edit_replaces_old_content_and_survives_rejection(self):
        self.visuals('omni',seconds=20)
        p=self.current(); d=p['data']; old_second=copy.deepcopy(d['script']['scenes'][1])
        d['video_edits']=[{'id':1,'instruction':'Replace with a crystal scene'}]
        d['script']['scenes'][0]['action']='OLD BRIDGE ACTION'
        self.s.save(p)
        self.cfg.mode='live'
        updated={'action':'Touches a crystal.', 'dialogue':'Hello!', 'camera':'Medium shot.'}
        calls=[]
        def rejected(job,d,scene,refs,target,previous=None,edit=None):
            calls.append(scene.copy())
            self.assertIsNone(edit)
            self.assertEqual(scene['action'],updated['action'])
            self.assertNotIn('OLD BRIDGE',scene['video_prompt'])
            raise ValueError('google_minor_safety')
        from tests.test_omni_blueprint import blueprint
        def structured(job,rules,context,schema,**kwargs):
            return blueprint() if 'timeline' in schema.get('properties',{}) else updated
        with patch('cartoonbot.voices.ensure'),patch.object(self.provider,'structured',create=True,side_effect=structured) as compiler, patch.object(self.provider,'video',side_effect=rejected):
            for _ in range(2):
                self.s.enqueue(self.current(),'video'); self.w.run_once()
                self.assertEqual(self.current()['state'],'attention')
                self.assertEqual(self.current()['data']['script']['scenes'][0]['action'],updated['action'])
                self.assertEqual(self.current()['data']['script']['scenes'][1],old_second)
            self.assertEqual(compiler.call_count,2)  # One edit resolution and one cached blueprint.
        self.assertEqual(len(calls),2)

    def test_repair_middle_scene_then_continues_remaining_scenes(self):
        self.visuals('omni',seconds=30)
        p=self.current(); p['data']['safety_review_attempted']=True; self.s.save(p)
        self.press('approve'); self.press('generate')
        original=self.provider.video
        def fail_second(job,d,scene,*args,**kwargs):
            if scene['id']==2: raise ValueError('google_minor_safety')
            return original(job,d,scene,*args,**kwargs)
        with patch.object(self.provider,'video',side_effect=fail_second): self.w.run_once()
        first=self.current()['data']['videos']['1']['path']
        self.press('error_edit_scene'); self.press('edit_video_content'); self.event(text='Спокойное действие.')
        self.press('apply')
        self.assertEqual(self.current()['data']['resume_scene_ids'],[2,3])
        self.press('generate'); calls=[]
        def resume(job,d,scene,*args,**kwargs):
            calls.append(scene['id'])
            if scene['id']==3: self.assertIn('2',self.current()['data']['videos'])
            return original(job,d,scene,*args,**kwargs)
        with patch.object(self.provider,'video',side_effect=resume): self.w.run_once()
        self.assertEqual(calls,[2,3]); self.assertEqual(self.current()['state'],'video')
        self.assertEqual(self.current()['data']['videos']['1']['path'],first)

    def blocked_third_of_four(self):
        self.visuals('omni',seconds=40)
        p=self.current(); p['data']['safety_review_attempted']=True; self.s.save(p)
        self.press('approve'); self.press('generate')
        original=self.provider.video
        def fail(job,d,scene,*args,**kwargs):
            if scene['id']==3: raise ValueError('google_minor_safety')
            return original(job,d,scene,*args,**kwargs)
        with patch.object(self.provider,'video',side_effect=fail): self.w.run_once()
        self.assertEqual(self.current()['state'],'attention')
        return copy.deepcopy(self.current()['data']['videos'])

    def test_skip_blocked_scene_generates_four_and_assembles_without_three(self):
        first=self.blocked_third_of_four()
        self.press('skip_scene'); self.assertEqual(self.current()['state'],'skip_scene_confirm')
        self.press('skip_scene_apply')
        self.assertEqual(self.current()['data']['resume_scene_ids'],[4])
        self.assertEqual(self.e.quote(self.current()['data'])[1],10)
        self.press('generate'); original=self.provider.video; calls=[]
        def generate(job,d,scene,*args,**kwargs):
            calls.append(scene['id']); return original(job,d,scene,*args,**kwargs)
        with patch.object(self.provider,'video',side_effect=generate): self.w.run_once()
        self.assertEqual(calls,[4]); d=self.current()['data']
        self.assertEqual(set(d['videos']),{'1','2','4'})
        self.assertAlmostEqual(media.info(d['assembled'])['seconds'],30,delta=.6)
        for key in first: self.assertEqual(d['videos'][key],first[key])

    def test_visual_repair_preserves_completed_videos_and_requires_approval(self):
        first=self.blocked_third_of_four(); old_images=copy.deepcopy(self.current()['data']['images'])
        self.press('error_edit_scene'); self.press('edit_video_content'); self.event(text='Измени визуал: добавь дерево.')
        job=self.s.db.execute("select kind from jobs where status='queued'").fetchone()
        self.assertEqual(job['kind'],'edit_visuals'); self.w.run_once()
        self.assertEqual(self.current()['state'],'visual_sync_choice'); self.press('visual_sync_only')
        p=self.current(); self.assertEqual(p['state'],'repair_visual_ready')
        self.assertEqual(p['data']['videos'],first)
        self.assertNotEqual(p['data']['images']['3']['path'],old_images['3']['path'])
        for key in ('1','2','4'): self.assertEqual(p['data']['images'][key],old_images[key])
        self.press('repair_visual_approve'); self.assertEqual(self.current()['state'],'quote')
        self.assertEqual(self.current()['data']['resume_scene_ids'],[3,4])
        self.assertEqual(self.s.db.execute("select count(*) from jobs where status='queued'").fetchone()[0],0)
        self.press('back'); self.press('error_edit_visual')
        self.counter+=1
        self.e.handle({'id':self.counter,'user_id':1,'media':{'kind':'photo','path':'replacement.png'}})
        self.assertEqual(self.current()['state'],'visual_sync_choice'); self.press('visual_sync_only')
        self.assertEqual(self.current()['state'],'repair_visual_ready')
        self.assertEqual(self.current()['data']['videos'],first)
        self.assertEqual(self.current()['data']['images']['3']['path'],'replacement.png')

    def test_completed_video_offers_visual_edit_and_repeated_approval_loop(self):
        self.visuals('omni',seconds=20); self.press('approve'); self.press('generate'); self.w.run_once()
        first=copy.deepcopy(self.current()['data']['videos'])
        self.press('edit'); self.assertEqual(self.current()['state'],'video_edit_choice')
        self.press('edit_visual_content'); self.assertEqual(self.current()['state'],'video_visual_scene')
        self.press('visual_scene=1')
        last_path=self.current()['data']['images']['1']['path']
        for change in ('Добавь дерево','Сделай дерево зелёным','Добавь облако'):
            self.assertEqual(self.current()['state'],'repair_visual')
            self.event(text=change); self.w.run_once()
            self.assertEqual(self.current()['state'],'visual_sync_choice'); self.press('visual_sync_only')
            p=self.current(); self.assertEqual(p['state'],'repair_visual_ready')
            self.assertNotEqual(p['data']['images']['1']['path'],last_path)
            last_path=p['data']['images']['1']['path']
            self.assertEqual(p['data']['videos']['2'],first['2'])
            self.assertEqual(self.s.db.execute("select count(*) from jobs where status='queued'").fetchone()[0],0)
            if change!='Добавь облако': self.press('error_edit_visual')
        self.press('repair_visual_approve'); self.assertEqual(self.current()['state'],'quote')
        self.assertEqual(self.current()['data']['resume_scene_ids'],[1])
        self.press('back'); self.assertEqual(self.current()['state'],'repair_visual_ready')
        self.press('error_edit_visual'); self.assertEqual(self.current()['state'],'repair_visual')
        self.press('repair_cancel'); self.assertEqual(self.current()['state'],'repair_visual_ready')

    def test_video_edit_choice_can_select_actions_or_return_without_changes(self):
        self.visuals(); self.press('approve'); self.press('generate'); self.w.run_once()
        self.press('edit'); self.press('video_edit_back'); self.assertEqual(self.current()['state'],'video')
        self.press('edit'); self.press('edit_video_content')
        self.assertEqual(self.current()['state'],'edit_input')
        self.assertEqual(self.current()['data']['edit_target'],'video')

    def test_edit_choice_is_available_before_generation_and_after_error(self):
        self.visuals('omni',seconds=20); self.press('approve')
        self.press('edit'); self.assertEqual(self.current()['state'],'video_edit_choice')
        self.press('edit_visual_content'); self.press('visual_scene=1')
        self.press('repair_cancel'); self.assertEqual(self.current()['state'],'quote')
        p=self.current(); p['state']='attention'; p['data']['error_scene_id']=2
        p['data']['error']='google_minor_safety'; self.s.save(p)
        self.press('error_edit_scene'); self.assertEqual(self.current()['state'],'video_edit_choice')
        self.press('edit_visual_content'); self.assertEqual(self.current()['state'],'repair_visual')
        self.assertEqual(self.current()['data']['repair_scene'],2)
        self.press('repair_cancel'); self.assertEqual(self.current()['state'],'attention')
        self.press('error_edit_scene'); self.press('edit_video_content')
        self.assertEqual(self.current()['state'],'edit_input')
        self.assertEqual(self.current()['data']['repair_scene'],2)

    def test_error_uses_short_template_and_never_calls_story_reviewer(self):
        self.visuals('omni',seconds=10)
        p=self.current();p['data']['safety_review']={'id':9,'instruction':'Old daughter scene'}
        p['data']['safety_review_note']='Stale explanation';self.s.save(p)
        self.press('approve'); self.press('generate')
        with patch.object(self.provider,'video',side_effect=ValueError('google_minor_safety')), patch.object(self.provider,'structured',create=True) as review:
            self.w.run_once();review.assert_not_called()
        self.assertNotIn('safety_review',self.current()['data'])
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertLess(len(body['text']),400)
        self.assertNotIn('daughter',body['text'])
        self.assertNotIn('Stale',body['text'])

    def test_old_scene_proposal_not_shown_or_applied_to_another_scene(self):
        self.visuals('omni',seconds=40)
        p=self.current();p['state']='attention';p['data'].update(error_scene_id=4,error='google_minor_safety',safety_review={'id':1,'instruction':'Old daughter scene'},safety_review_note='Daughter explanation')
        self.s.save(p);self.e.show(p)
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertIn('Сцена 4',body['text'])
        self.assertNotIn('daughter',body['text']);self.assertNotIn('safety_apply',str(body))
        self.press('safety_apply');self.assertEqual(self.current()['state'],'attention')

    def test_error_allows_script_edit_without_starting_video(self):
        self.script()
        with self.s.transaction():
            p=self.current(); p['state']='attention'; p['data']['error']='google_minor_safety'; self.s.save(p)
        self.press('error_edit_script')
        self.assertEqual(self.current()['state'],'edit_input')
        self.assertEqual(self.current()['data']['edit_target'],'script')
        self.event(text='Пусть персонажи спокойно улыбаются')
        job=self.s.db.execute("SELECT kind FROM jobs WHERE status='queued'").fetchone()
        self.assertEqual(job[0],'edit_script')
        self.finish_job(); self.assertEqual(self.current()['state'],'script')

    def test_seedance_voice_languages_independent_of_interface(self):
        for lang in ('ru','uk','en'):
            self.event('new:seedance',language='en')
            self.press('audio='+lang,language='en')
            p=self.current()
            self.assertEqual(p['state'],'seedance_source')
            self.assertEqual(p['data']['lang'],lang)
            self.assertEqual(p['data']['audio_mode'],lang)
            self.assertTrue(p['data']['lip'])
            self.assertEqual(self.s.user(1)['lang'],'en')

    def test_seedance_large_idea_30_second_flow(self):
        with patch.object(self.provider,'assess_seedance',return_value={'needs_longer':True,'reason':'Нужно больше времени.'}):
            self.script('seedance')
        self.assertEqual(self.current()['state'],'seedance_length')
        self.assertNotIn('script',self.current()['data'])
        self.press('length=30'); self.finish_job()
        self.assertEqual(self.current()['data']['seconds'],30)
        self.assertEqual(self.current()['data']['version'],'2.5')
        self.press('approve'); self.press('continue_visuals'); self.press('ratio=9:16'); self.press('style=3d'); self.press('done'); self.finish_job()
        self.press('approve'); self.press('version=2.0')
        self.assertEqual(self.current()['state'],'video_version')
        self.press('version=2.5')
        from cartoonbot.catalog import seedance_tokens
        self.assertEqual(seedance_tokens(self.current()['data'],'2.5',{}),1300)
        self.press('generate'); self.finish_job()
        self.assertAlmostEqual(media.info(self.current()['data']['assembled'])['seconds'],30,delta=.55)

    def test_seedance_two_parts_have_shared_cast_and_independent_approval(self):
        with patch.object(self.provider,'assess_seedance',return_value={'needs_longer':True,'reason':'Две части.'}):
            self.script('seedance')
        self.press('length=split'); self.finish_job()
        first=self.current(); second=self.s.project(first['data']['next_part'])
        self.assertEqual(first['data']['seconds'],15)
        self.assertEqual(second['data']['seconds'],15)
        self.assertEqual(second['state'],'script')
        self.assertEqual(second['data']['script']['scenes'][0]['start'],0)
        self.assertEqual(second['data']['script']['scenes'][-1]['end'],15)
        self.assertEqual(first['data']['script']['assets'],second['data']['script']['assets'])
        self.assertEqual(second['data']['previous_part'],first['id'])
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM jobs WHERE status='queued'").fetchone()[0],0)

    def test_photo_references_before_description(self):
        for direct in (False,True):
            self.event('new:photo')
            if not direct: self.press('add_photo_refs')
            self.event(text='Сохранить лицо',media={'kind':'photo','path':'/tmp/hero.png'})
            self.assertEqual(self.current()['state'],'photo_refs')
            self.press('done')
            self.assertEqual(self.current()['state'],'photo_prompt')
            self.event(text='Герой в лесу')
            p=self.current()
            self.assertEqual(p['state'],'photo_ratio')
            self.assertEqual(p['data']['refs'][0]['purpose'],'Сохранить лицо')
            self.assertEqual(p['data']['photo_prompt'],'Герой в лесу')

    def test_standalone_photo_generate_edit_approve(self):
        self.event('new:photo'); self.assertEqual(self.current()['state'],'photo_prompt')
        self.event(text='Рыжий кот на синем фоне, 3D')
        self.assertEqual(self.current()['state'],'photo_refs')
        self.press('no_refs'); self.press('photo_ratio=9:16'); self.finish_job()
        original=self.current()['data']['photo_path']
        self.assertTrue(Path(original).is_file())
        self.press('photo_edit'); self.event(text='Сделай фон зелёным'); self.finish_job()
        self.assertNotEqual(original,self.current()['data']['photo_path'])
        self.press('photo_approve'); self.assertEqual(self.current()['state'],'photo_finished')
        self.assertTrue(Path(original).is_file())
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM jobs WHERE kind='script'").fetchone()[0],0)

    def test_photo_reference_limit_and_video_rejection(self):
        self.event('new:photo'); self.event(text='Сохрани персонажей с фото')
        self.event(media={'kind':'video','path':'v.mp4'})
        self.assertEqual(self.current()['data']['refs'],[])
        for i in range(6):self.event(media={'kind':'photo','path':f'{i}.png'},text='Сохрани лицо')
        self.assertEqual(len(self.current()['data']['refs']),5)
        self.assertEqual(self.current()['data']['refs'][0]['purpose'],'Сохрани лицо')
        self.press('no_refs'); self.assertEqual(self.current()['state'],'photo_refs')
        self.press('done'); self.assertEqual(self.current()['state'],'photo_ratio')

    def test_five_photo_limit(self):
        self.script(); self.press('approve'); self.press('continue_visuals'); self.press('ratio=9:16')
        for i in range(6): self.event(media={'kind':'photo','path':f'/test/{i}.png'})
        self.assertEqual(len(self.current()['data']['refs']),5)

    def test_wrong_state_callback_cannot_skip_approvals(self):
        self.event('new:omni'); self.press('generate')
        self.assertEqual(self.current()['state'],'video_language')
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)

    def test_seedance_russian_lip_switch(self):
        self.event('new:seedance'); self.assertEqual(self.current()['state'],'seedance_audio')
        self.press('audio=omni'); self.assertEqual(self.current()['state'],'video_language')
        self.press('vlang=ru'); self.press('source=no'); self.event(text='Ёжик разговаривает с совой.'); self.finish_job()
        self.assertEqual(self.current()['data']['route'],'omni')
        self.assertTrue(self.current()['data']['lip'])


    def test_balances_settle_once(self):
        self.event('new:omni'); p=self.current()
        with self.s.transaction(): self.s.enqueue(p,'video',reserve=100)
        job=self.s.claim()
        with self.s.transaction():
            self.s.settle(job,True); self.s.settle(job,True)
        self.assertEqual(self.s.user(1)['balance'],9900)
        self.assertEqual(self.s.user(1)['reserved'],0)

    def test_insufficient_balance_does_not_queue(self):
        self.visuals(); self.press('approve')
        self.s.db.execute('UPDATE users SET balance=0 WHERE id=1')
        self.press('generate')
        self.assertEqual(self.current()['state'],'quote')
        self.assertEqual(self.s.db.execute("SELECT count(*) FROM jobs WHERE kind='video'").fetchone()[0],0)

    def test_public_access_allows_guests_but_keeps_projects_private(self):
        self.cfg.mode='codex'; self.cfg.admins={9}; self.cfg.public_access=True
        self.event('new:omni',uid=1)
        first=self.current(1)
        self.assertIsNotNone(first)
        self.event('new:omni',uid=2)
        second=self.current(2)
        self.event('open:'+first['id'],uid=2)
        self.assertEqual(self.current(2)['id'],second['id'])
        self.assertIsNone(self.s.project(first['id'],2))

    def test_real_mode_closed_to_non_owner(self):
        self.cfg.mode='live'; self.cfg.admins={9}
        self.event('new:omni')
        self.assertIsNone(self.s.user(1)['current'])

    def test_failure_does_not_auto_retry(self):
        self.script(); self.press('approve'); self.press('continue_visuals'); self.press('ratio=9:16'); self.press('done'); self.press('style=3d')
        with patch.object(self.provider,'image',side_effect=RuntimeError('secret should not leak')): self.w.run_once()
        self.assertEqual(self.current()['state'],'attention')
        self.assertFalse(self.w.run_once())
        messages=' '.join(x[0] for x in self.s.db.execute('SELECT body FROM outbox'))
        self.assertNotIn('secret should not leak',messages)

    def test_single_photo_is_delivered_for_explicit_character_edit(self):
        self.visuals('seedance')
        with self.s.transaction():
            p=self.current();d=p['data'];d['recreate']=True
            d['refs']=[{'kind':'photo','path':'wife-reference.png','purpose':''}]
            for asset in d['script']['assets']:asset['reference_photo_ids']=[]
            self.s.save(p)
            self.s.enqueue(p,'edit_visuals',{'edits':[{'id':1,'instruction':'Внешность по референсу'}]})
        with patch.object(self.provider,'image',wraps=self.provider.image) as image:
            self.finish_job()
        self.assertIn('wife-reference.png',image.call_args.args[2])
        assets=self.current()['data']['script']['assets']
        self.assertEqual(assets[0]['reference_photo_ids'],[1])
        self.assertTrue(all(not a['reference_photo_ids'] for a in assets[1:]))

    def test_recreate_routes_after_analysis(self):
        p=self.cfg.root/'media'/'input.png'; v=p.with_suffix('.mp4')
        media.placeholder(p,'DEMO',1); media.demo_video(p,v,1)
        self.event('new:recreate'); self.event(media={'path':str(v),'kind':'video','seconds':1})
        self.finish_job(); self.assertEqual(self.current()['state'],'route')
        self.press('route=omni')
        self.assertEqual(self.current()['data']['route'],'omni')
        self.assertIn('source',self.current()['data'])
        self.press('vlang=ru')
        self.assertEqual(self.current()['state'],'recreate_changes')
        self.event(text='Сохранить всё, заменить только жену')
        self.assertEqual(self.current()['state'],'references')
        self.press('no_refs')
        self.assertEqual(self.current()['state'],'style')
        self.press('style=3d'); self.finish_job()
        self.press('approve'); self.press('continue_visuals'); self.press('ratio=9:16')
        self.assertEqual(self.current()['state'],'busy')
        self.assertNotIn('purpose',self.current()['data']['brief'])

    def test_recreate_seedance_skips_intake_and_reuses_video(self):
        self.event('new:recreate')
        with self.s.transaction():
            p=self.current(); p['state']='route'
            p['data']['analysis']={'summary':'Мужчина и две женщины','route':'seedance','has_dialogue':False}
            p['data']['source']={'kind':'video','path':'source.mp4','seconds':15}
            self.s.save(p)
        self.press('route=seedance'); self.press('audio=none')
        self.assertEqual(self.current()['state'],'recreate_changes')
        self.press('recreate_keep'); self.press('done')
        self.assertEqual(self.current()['state'],'style')
        self.assertEqual(len(self.current()['data']['refs']),0)
        self.press('style=3d')
        with patch.object(self.provider,'assess_seedance',side_effect=AssertionError('Source already fits 15 seconds')):
            self.finish_job()
        self.assertEqual(self.current()['data']['seconds'],15)
        self.press('approve'); self.press('continue_visuals'); self.press('ratio=9:16')
        self.assertEqual(self.current()['state'],'busy')


    def test_prompt_error_has_edit_and_back_without_losing_visuals(self):
        self.visuals('seedance')
        p=self.current();original=copy.deepcopy(p['data'])
        p['state']='attention';p['data']['error']='seedance_prompt_too_long'
        self.s.save(p);self.e.show(p)
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        actions=[b[1].split(':')[-1] for row in body['buttons'] for b in row]
        self.assertIn('attention_back',actions);self.assertIn('error_edit_script',actions)
        self.press('error_edit_script');self.assertEqual(self.current()['state'],'edit_input')
        self.press('cancel_edit');self.assertEqual(self.current()['state'],'script')
        p=self.current();p['state']='attention';self.s.save(p)
        self.press('attention_back');self.assertEqual(self.current()['state'],'visuals')
        self.assertEqual(self.current()['data']['images'],original['images'])
        self.assertEqual(self.current()['data']['script'],original['script'])

    def test_stale_button_shows_current_controls_without_starting_job(self):
        self.script();p=self.current()
        count=self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        self.event(f"p:{p['id']}:{p['revision']-1}:approve")
        body=json.loads(self.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertTrue(body['buttons'])
        self.assertEqual(self.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],count)


class LocalizationTests(unittest.TestCase):
    def test_locale_fallback(self):
        for code,expected in [('uk-UA','uk'),('ru_RU','ru'),('en-GB','en'),(None,'en'),('de','en')]:
            self.assertEqual(locale(code),expected)

    def test_all_translation_keys_and_placeholders_match(self):
        from string import Formatter
        keys=set(TEXT['ru'])
        for lang,rows in TEXT.items():
            self.assertEqual(set(rows),keys)
            for key,text in rows.items():
                expected={f for _,f,_,_ in Formatter().parse(TEXT['ru'][key]) if f}
                actual={f for _,f,_,_ in Formatter().parse(text) if f}
                self.assertEqual(actual,expected,(lang,key))
                self.assertTrue(text.strip())


class MediaTests(unittest.TestCase):
    def test_four_caption_styles_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); image=root/'image.png'; video=root/'video.mp4'
            media.placeholder(image,'Тест ґ є і ї',1); media.demo_video(image,video,2)
            words=[{'word':'Привіт,','start':.1,'end':.7},{'word':'світе!','start':.8,'end':1.5}]
            for style in ('classic','karaoke','words','boxed'):
                folder=root/style; folder.mkdir()
                out=media.burn_captions(video,words,folder/'result.mp4',style)
                self.assertTrue(Path(out).exists())
                self.assertAlmostEqual(media.info(out)['seconds'],2,delta=.2)
                self.assertIn('світе',(folder/'captions.ass').read_text())

    def test_caption_injection_escaped(self):
        self.assertEqual(media.safe_ass(r'{\pos(0,0)}hello'),'pos(0,0)hello')

    def test_assembly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); paths=[]
            for i in range(2):
                p=root/f'{i}.png'; v=root/f'{i}.mp4'
                media.placeholder(p,'DEMO',i); media.demo_video(p,v,1); paths.append(str(v))
            out=media.assemble(paths,root/'assembled.mp4','9:16')
            self.assertAlmostEqual(media.info(out)['seconds'],2,delta=.3)


if __name__=='__main__': unittest.main()
