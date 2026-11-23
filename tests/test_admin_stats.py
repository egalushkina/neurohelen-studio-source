import json
import unittest
from tests import test_workflow as workflow

class AdminStatsTests(unittest.TestCase):
    def harness(self):
        h=workflow.Harness();h.setUp();self.addCleanup(h.tearDown);h.cfg.admins={1};return h
    def last(self,h):
        return json.loads(h.s.db.execute('SELECT body FROM outbox ORDER BY id DESC LIMIT 1').fetchone()[0])
    def test_owner_menu_and_denied_forged_callback(self):
        h=self.harness();h.event('home');self.assertIn('stats:overview',str(self.last(h)['buttons']))
        h.event('home',uid=2);self.assertNotIn('stats:',str(self.last(h)['buttons']))
        for action in ('stats:overview:all','stats:users:0','stats:user:1'):
            h.event(action,uid=2);self.assertNotIn('Баланс:',self.last(h)['text']);self.assertNotIn('Статистика:',self.last(h)['text'])
        h.event(text='/stats',uid=2);self.assertNotIn('Статистика:',self.last(h)['text'])
    def test_real_purchase_ledger_separate_from_grants_and_balance(self):
        h=self.harness();h.s.user(2)
        for key,amount,kind in [('p',500,'purchase'),('bonus',10000,'bonus'),('s',-250,'generation'),('r',0,'release')]:
            h.s.db.execute('INSERT INTO ledger VALUES (?,?,?,?,?)',(key,2,amount,kind,1))
        h.event('stats:overview:all');text=self.last(h)['text']
        self.assertIn('Куплено токенов: 500',text);self.assertIn('Списано токенов: 250',text)
        self.assertIn('Приём оплаты ещё не подключён',text)
        h.event('stats:user:2');self.assertIn('Куплено токенов: 500',self.last(h)['text'])
    def test_stats_preserve_current_project_and_do_not_start_jobs(self):
        h=self.harness();h.script();pid=h.current()['id'];n=h.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0]
        h.event(text='/stats',display_name='Helen',username='helen')
        self.assertEqual(h.current()['id'],pid)
        self.assertEqual(h.s.db.execute('SELECT count(*) FROM jobs').fetchone()[0],n)
        h.event('stats:user:1');text=self.last(h)['text']
        self.assertIn('Helen',text);self.assertIn('@helen',text);self.assertIn('Сценарий',text)
    def test_pagination_and_invalid_inputs(self):
        h=self.harness()
        for uid in range(1,13):h.s.user(uid)
        h.event('stats:users:0');b=self.last(h)['buttons'];self.assertIn('stats:users:1',str(b))
        h.event('stats:users:1');self.assertNotIn('stats:users:2',str(self.last(h)['buttons']))
        for action in ('stats:users:-1','stats:user:no','stats:overview:bad'):h.event(action)
    def test_activity_updates_without_inventing_old_user_names(self):
        h=self.harness();h.s.user(2);h.event('stats:user:2')
        self.assertIn('Последнее обращение: нет данных',self.last(h)['text'])
        h.event('home',uid=2,display_name='Tester',username='test')
        h.event('stats:users:0');self.assertIn('Tester',str(self.last(h)['buttons']))
