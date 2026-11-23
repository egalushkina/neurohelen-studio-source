import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from webstudio.server import Studio
from webstudio.support import Support

class SupportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=Studio(Path(self.tmp.name))
        self.app.support.working=lambda now=None:True
        self.app.support.assistant.respond=lambda context: ({'answer':'Откройте «Мои проекты».','handoff':'Не помогло' in context['conversation'][-1]['body']},{'total_tokens':20})
    def tearDown(self):
        self.app.store.db.close();self.tmp.cleanup()
    def test_handoff_preserves_history_and_silences_bot(self):
        service=self.app.support
        a=service.send(1,{'text':'Где мой проект?'})
        self.assertEqual([x['role'] for x in a['messages']],['user','bot'])
        b=service.send(1,{'text':'Соедините с агентом reader@example.test'})
        self.assertEqual(b['status'],'waiting')
        c=service.send(1,{'text':'Нужна помощь с последней сценой'})
        self.assertEqual(len(c['messages']),5)
        self.assertEqual(c['messages'][-1]['role'],'user')
        self.assertIn('email',service.snapshot(2)['messages'][0]['body'])
        restored=Support(self.app.store,self.app.root).snapshot(1)
        self.assertEqual(restored['messages'],c['messages'])
    def test_operator_reply_automatically_queues_email(self):
        self.app.auth('reader@example.test','Test-password-2026',True)
        uid=self.app.store.db.execute('SELECT uid FROM web_accounts').fetchone()[0]
        service=self.app.support;service.send(uid,{'text':'Соедините с агентом reader@example.test'})
        with self.app.store.transaction():
            service.add(1,'operator','Ваш вопрос решён')
            service.queue_mail(1,'Ваш вопрос решён')
            service.queue_mail(1,'Ваш вопрос решён')
        mail=self.app.store.db.execute('SELECT recipient,body FROM support_mail').fetchall()
        self.assertEqual(len(mail),1)
        self.assertEqual(tuple(mail[0]),('reader@example.test','Ваш вопрос решён'))

    def test_bad_message_rejected(self):
        for text in ('','x'*4001,[]):
            with self.assertRaises(ValueError):self.app.support.send(1,{'text':text})
    def test_notification_contains_link_not_command(self):
        service=self.app.support;service.send(1,{'text':'Соедините с оператором reader@example.test'})
        service.config={'dedicated_bot':True,'telegram_token':'test','admin_id':99}
        calls=[]
        service.telegram=lambda method,**kw:calls.append((method,kw))
        service.tick()
        self.assertTrue(all(method=='sendMessage' for method,_ in calls))
        self.assertTrue(all('?supportTicket=1' in args['text'] for _,args in calls))
        self.assertFalse(any('/studio_reply' in args['text'] for _,args in calls))

    def test_unavailable_once_after_five_minutes(self):
        service=self.app.support
        with patch('webstudio.support.time.time',return_value=1000):service.send(1,{'text':'Соедините с агентом reader@example.test'})
        with patch('webstudio.support.time.time',return_value=1301):service.check_waiting();service.check_waiting()
        messages=service.snapshot(1)['messages']
        self.assertEqual(sum('недоступен' in x['body'] for x in messages),1)
        service.join(1)
        self.assertEqual(service.snapshot(1)['status'],'human')

    def test_outside_hours_immediately(self):
        service=self.app.support;service.working=lambda:False
        data=service.send(1,{'text':'Соедините с агентом reader@example.test'})
        self.assertIn('недоступен',data['messages'][-1]['body'])

    def test_dubai_schedule_boundaries(self):
        zone=ZoneInfo('Asia/Dubai')
        for day,hour,expected in [(5,9,False),(5,10,True),(5,19,True),(5,20,False),(10,10,True),(11,10,False)]:
            self.assertEqual(Support.working(datetime(2026,10,day,hour,tzinfo=zone).timestamp()),expected)

    def test_email_first_then_help_without_notification(self):
        service=self.app.support
        first=service.send(1,{'text':'Где найти проект?'})
        self.assertIn('email',first['messages'][-1]['body'])
        second=service.send(1,{'text':'contact@example.test'})
        self.assertEqual(second['status'],'bot')
        self.assertIn('Мои проекты',second['messages'][-1]['body'])
        service.config={'dedicated_bot':True,'telegram_token':'test','admin_id':99}
        service.telegram=lambda *a,**kw:self.fail('Routine question notified owner')
        service.tick()

    def test_unsolved_escalates_and_uses_contact_email(self):
        service=self.app.support
        service.send(1,{'text':'contact@example.test где найти проект?'})
        result=service.send(1,{'text':'Не помогло, проект пропал'})
        self.assertEqual(result['status'],'waiting')
        service.reply(1,'Проверю проект')
        self.assertEqual(self.app.store.db.execute('SELECT recipient FROM support_mail').fetchone()[0],'contact@example.test')

    def test_operator_intent_not_keyword(self):
        for text in ['Соедините с агентом','Позовите живого человека','Хочу поговорить с оператором','connect me to an agent']:
            self.assertTrue(Support.asks_operator(text),text)
        for text in ['Когда работает оператор?','Не надо оператора','Оператор ответил вчера']:
            self.assertFalse(Support.asks_operator(text),text)

    def test_direct_request_waits_for_email_and_old_button_is_ignored(self):
        service=self.app.support
        first=service.send(1,{'text':'Соедините с агентом'})
        self.assertEqual(first['status'],'bot')
        self.assertEqual(service.send(1,{'text':'contact@example.test'})['status'],'waiting')
        with self.assertRaises(ValueError):service.send(2,{'handoff':True})
        self.assertEqual(service.send(2,{'text':'other@example.test Где мой проект?', 'handoff':True})['status'],'bot')

    def test_ai_context_is_scoped_to_authenticated_user(self):
        self.app.store.user(1);self.app.store.user(2)
        own=self.app.store.new_project(1,'omni','ru')
        other=self.app.store.new_project(2,'omni','ru')
        other['data']['script']={'title':'PRIVATE OTHER USER','scenes':[]};self.app.store.save(other)
        seen=[]
        self.app.support.assistant.respond=lambda context:(seen.append(context) or {'answer':'Ваш проект сохранён.','handoff':False},{'total_tokens':30})
        self.app.support.send(1,{'text':'contact@example.test Где мой проект?'})
        self.assertEqual([p['id'] for p in seen[0]['projects']],[own['id']])
        self.assertNotIn('PRIVATE OTHER USER',str(seen))
        self.assertTrue(seen[0]['conversation'])
        self.assertEqual(self.app.store.db.execute('SELECT count(*) FROM support_ai_usage').fetchone()[0],1)

    def test_ai_failure_hands_off_without_leaking_error(self):
        def fail(context):raise RuntimeError('secret-token')
        self.app.support.assistant.respond=fail
        result=self.app.support.send(1,{'text':'contact@example.test Где мой проект?'})
        self.assertEqual(result['status'],'waiting')
        self.assertNotIn('secret-token',str(result))

    def test_operator_join_during_response_suppresses_ai(self):
        def reply(context):
            self.app.support.join(1)
            return {'answer':'This stale answer must not appear','handoff':False},{}
        self.app.support.assistant.respond=reply
        result=self.app.support.send(1,{'text':'contact@example.test Где мой проект?'})
        self.assertEqual(result['status'],'human')
        self.assertNotIn('This stale answer',str(result))
