import os
import gzip
import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from .i18n import locale


def dump(value):
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, path, initial_balance=10000):
        self.initial_balance=initial_balance
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode='+('DELETE' if os.environ.get('STUDIO_WALLET_PATH') else 'WAL'))
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, lang TEXT, manual INTEGER DEFAULT 0,
          current TEXT, balance INTEGER DEFAULT 10000, reserved INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id),
          state TEXT, revision INTEGER, body TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS history(project_id TEXT, revision INTEGER, body TEXT, state TEXT,
          UNIQUE(project_id,revision));
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, project_id TEXT, revision INTEGER,
          kind TEXT, payload TEXT, status TEXT, reserve INTEGER DEFAULT 0, error TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, job_id TEXT, provider TEXT,
          state TEXT, remote_id TEXT, usage TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS ledger(id TEXT PRIMARY KEY, user_id INTEGER, amount INTEGER,
          kind TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS generation_units(job_id TEXT, unit TEXT, amount INTEGER,
          PRIMARY KEY(job_id,unit));
        CREATE TABLE IF NOT EXISTS payment_orders(id TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id),
          package TEXT, tokens INTEGER, amount INTEGER, currency TEXT, created REAL,
          status TEXT DEFAULT 'pending', telegram_charge TEXT UNIQUE, provider_charge TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS user_activity(user_id INTEGER PRIMARY KEY REFERENCES users(id),
          display_name TEXT, username TEXT, last_seen REAL);
        CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,
          body TEXT, delivered INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
          next_attempt REAL DEFAULT 0, error TEXT);
        ''')
        payment_columns={r['name'] for r in self.db.execute('PRAGMA table_info(payment_orders)')}
        for name,definition in [('stripe_session','TEXT'),('stripe_url','TEXT'),('stripe_live','INTEGER')]:
            if name not in payment_columns: self.db.execute(f'ALTER TABLE payment_orders ADD COLUMN {name} {definition}')
        self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS stripe_session_unique ON payment_orders(stripe_session)')
        if 'provider' not in {r['name'] for r in self.db.execute('PRAGMA table_info(payment_orders)')}:
            self.db.execute("ALTER TABLE payment_orders ADD COLUMN provider TEXT NOT NULL DEFAULT 'yookassa'")
        if 'checkout_query' not in {r['name'] for r in self.db.execute('PRAGMA table_info(payment_orders)')}:
            self.db.execute('ALTER TABLE payment_orders ADD COLUMN checkout_query TEXT')
        columns={r['name'] for r in self.db.execute('PRAGMA table_info(outbox)')}
        for name,definition in [('attempts','INTEGER DEFAULT 0'),('next_attempt','REAL DEFAULT 0'),('error','TEXT')]:
            if name not in columns: self.db.execute(f'ALTER TABLE outbox ADD COLUMN {name} {definition}')

        self.wallet=None
        if os.environ.get('STUDIO_WALLET_PATH'):
            from .shared_wallet import Wallet
            self.wallet=Wallet(self,os.environ['STUDIO_WALLET_PATH'],os.environ.get('STUDIO_WALLET_NAMESPACE'))

    def credit(self,uid,amount,key):
        if self.wallet and not self.wallet.change(uid,key,amount=amount,paid=amount):return
        self.db.execute('UPDATE users SET balance=balance+? WHERE id=?',(amount,uid))

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def user(self, uid, code=None):
        self.db.execute('INSERT OR IGNORE INTO users(id,lang,balance) VALUES (?,?,?)', (uid, locale(code),self.initial_balance))
        row = dict(self.db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())
        if not row['manual'] and code:
            row['lang'] = locale(code)
            self.db.execute('UPDATE users SET lang=? WHERE id=?', (row['lang'], uid))
        if self.wallet:
            shared=self.wallet.read(uid);row.update(balance=shared['balance'],reserved=shared['reserved'])
        return row

    def project(self, pid, uid=None):
        row = self.db.execute('SELECT * FROM projects WHERE id=?', (pid,)).fetchone()
        if row is None or (uid is not None and row['user_id'] != uid):
            return None
        result = dict(row)
        result['data'] = json.loads(result.pop('body'))
        return result

    def new_project(self, uid, route, lang):
        pid = uuid.uuid4().hex[:10]
        self.db.execute('INSERT INTO projects VALUES (?,?,?,?,?,?)',
                        (pid, uid, 'upload' if route == 'recreate' else 'seedance_audio' if route=='seedance' else 'video_language',
                         0, dump({'route': route, 'lang': lang, 'seconds': 15 if route == 'seedance' else 10,
                                  'ratio': '9:16', 'refs': [], 'brief': {}, 'images': {}, 'videos': {},
                                  'version': '2.0', 'variant': 0, 'auto_duration':route=='omni'}), time.time()))
        self.db.execute('UPDATE users SET current=? WHERE id=?', (pid, uid))
        return self.project(pid)

    def save(self, p):
        previous=self.db.execute('SELECT id,revision,body,state FROM projects WHERE id=?',(p['id'],)).fetchone()
        if previous:
            self.db.execute('INSERT OR IGNORE INTO history VALUES (?,?,?,?)',
                (previous['id'],previous['revision'],gzip.compress(previous['body'].encode()),previous['state']))
        p['revision'] += 1
        self.db.execute('UPDATE projects SET state=?,revision=?,body=? WHERE id=?',
                        (p['state'], p['revision'], dump(p['data']), p['id']))

    @staticmethod
    def history_data(body):
        return json.loads(gzip.decompress(body) if isinstance(body,bytes) else body)

    def compact_history(self):
        with self.transaction():
            for row in self.db.execute("SELECT project_id,revision,body FROM history WHERE typeof(body)='text'").fetchall():
                self.db.execute('UPDATE history SET body=? WHERE project_id=? AND revision=?',
                    (gzip.compress(row['body'].encode()),row['project_id'],row['revision']))

    def emit(self, uid, text='', buttons=None, file=None, kind=None, navigation=False):
        self.db.execute('INSERT INTO outbox(user_id,body) VALUES (?,?)',
                        (uid, dump({'text': text, 'buttons': buttons or [], 'file': file, 'kind': kind, 'navigation':navigation})))

    def enqueue(self, p, kind, payload=None, reserve=0):
        uid = p['user_id']
        row = self.user(uid)
        if reserve < 0 or row['balance'] - row['reserved'] < reserve:
            raise ValueError('balance')
        job_id = uuid.uuid4().hex
        p['data']['return_state'] = p['state']
        p['state'] = 'busy'
        self.save(p)
        if self.wallet:self.wallet.change(uid,'reserve:'+job_id,reserve=reserve)
        self.db.execute('UPDATE users SET reserved=reserved+? WHERE id=?', (reserve, uid))
        self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',
                        (job_id, p['id'], p['revision'], kind, dump(payload or {}), 'queued', reserve, None, time.time()))
        return job_id

    def claim(self):
        with self.transaction():
            row = self.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:
                return None
            self.db.execute("UPDATE jobs SET status='running' WHERE id=?", (row['id'],))
            return dict(row)

    def unresolved_charge(self,job):
        return job['reserve']>0 and self.db.execute("SELECT 1 FROM operations WHERE job_id=? AND provider IN ('google_omni','byteplus_seedance') AND state NOT IN ('succeeded','rejected','failed','expired','cancelled') LIMIT 1",(job['id'],)).fetchone() is not None

    def complete_unit(self,job,unit):
        price=json.loads(job['payload']).get('unit_price',0)
        self.db.execute('INSERT OR IGNORE INTO generation_units VALUES (?,?,?)',(job['id'],str(unit),price))

    def settle(self, job, success):
        p = self.project(job['project_id'])
        uid, n = p['user_id'], job['reserve']
        key = 'settle:' + job['id']
        if self.db.execute('SELECT 1 FROM ledger WHERE id=?', (key,)).fetchone():
            return
        completed=self.db.execute('SELECT COALESCE(sum(amount),0) FROM generation_units WHERE job_id=?',(job['id'],)).fetchone()[0]
        charge=n if success else min(n,completed)
        if self.wallet:self.wallet.change(uid,key,amount=-charge,reserve=-n,paid=-charge)
        self.db.execute('UPDATE users SET reserved=reserved-?,balance=balance-? WHERE id=?', (n,charge,uid))
        self.db.execute('INSERT INTO ledger VALUES (?,?,?,?,?)', (key,uid,-charge,
                         'generation' if charge else 'release',time.time()))

    def operation(self, job_id, provider):
        oid = uuid.uuid4().hex
        with self.transaction():
            row=self.db.execute('SELECT p.body FROM projects p JOIN jobs j ON j.project_id=p.id WHERE j.id=?',(job_id,)).fetchone()
            pending=json.loads(row['body']).get('pending_generation',{}) if row else {}
            context={k:pending[k] for k in ('unit','target') if k in pending} if pending.get('job')==job_id else {}
            self.db.execute('INSERT INTO operations VALUES (?,?,?,?,?,?,?)',
                            (oid, job_id, provider, 'submitted', None, dump(context), time.time()))
        return oid

    def record_operation(self, oid, state, remote_id=None, usage=None):
        with self.transaction():
            old=self.db.execute('SELECT usage FROM operations WHERE id=?',(oid,)).fetchone()
            usage={**(json.loads(old['usage']) if old else {}),**(usage or {})}
            self.db.execute('UPDATE operations SET state=?,remote_id=COALESCE(?,remote_id),usage=? WHERE id=?',
                            (state, remote_id, dump(usage or {}), oid))

    def recover(self):
        # Never blindly resubmit a job that could already have incurred provider charges.
        with self.transaction():
            rows = self.db.execute("SELECT * FROM jobs WHERE status='running'").fetchall()
            for row in rows:
                self.db.execute("UPDATE jobs SET status='attention',error='interrupted' WHERE id=?", (row['id'],))
                p = self.project(row['project_id'])
                p['state'] = 'attention'
                p['data']['error'] = 'interrupted'
                self.save(p)
            return len(rows)
