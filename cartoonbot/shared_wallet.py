"""Attached SQLite wallet. DELETE journals provide atomic cross-database commits."""
import hashlib,os,secrets,time
from contextlib import contextmanager, nullcontext

class Wallet:
 def __init__(self,store,path,namespace):
  if namespace not in ('web','telegram'):raise ValueError('Wallet namespace required')
  self.s=store;self.db=store.db;self.ns=namespace
  self.db.execute('PRAGMA journal_mode=DELETE');self.db.execute('PRAGMA synchronous=FULL')
  self.db.execute('ATTACH DATABASE ? AS wallet',(path,))
  self.db.executescript('''PRAGMA wallet.journal_mode=DELETE; PRAGMA wallet.synchronous=FULL;
CREATE TABLE IF NOT EXISTS wallet.accounts(id TEXT PRIMARY KEY,balance INTEGER NOT NULL,reserved INTEGER NOT NULL,paid INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS wallet.members(namespace TEXT,uid INTEGER,account TEXT NOT NULL REFERENCES accounts(id),PRIMARY KEY(namespace,uid));
CREATE TABLE IF NOT EXISTS wallet.entries(id TEXT PRIMARY KEY,account TEXT,amount INTEGER,reserve_delta INTEGER,created REAL);
CREATE TABLE IF NOT EXISTS wallet.links(token TEXT PRIMARY KEY,web_uid INTEGER,tg_uid INTEGER,expires REAL,status TEXT NOT NULL);
''')
  if 'web_label' not in {r['name'] for r in self.db.execute('PRAGMA wallet.table_info(links)')}:
   self.db.execute("ALTER TABLE wallet.links ADD COLUMN web_label TEXT NOT NULL DEFAULT ''")
 @contextmanager
 def atomic(self):
  with self.s.lock:
   with (nullcontext() if self.db.in_transaction else self.s.transaction()):yield
 def account(self,uid):
  with self.atomic():
   row=self.db.execute('SELECT account FROM wallet.members WHERE namespace=? AND uid=?',(self.ns,uid)).fetchone()
   if row:return row[0]
   user=self.db.execute('SELECT balance,reserved FROM users WHERE id=?',(uid,)).fetchone()
   paid=self.db.execute("SELECT coalesce(sum(amount),0) FROM ledger WHERE user_id=? AND kind IN ('purchase','generation','refund')",(uid,)).fetchone()[0]
   key=self.ns+':'+str(uid)
   self.db.execute('INSERT INTO wallet.accounts VALUES(?,?,?,?)',(key,user[0],user[1],paid))
   self.db.execute('INSERT INTO wallet.members VALUES(?,?,?)',(self.ns,uid,key))
   return key
 def read(self,uid):
  with self.atomic():
   key=self.account(uid)
   return dict(self.db.execute('SELECT * FROM wallet.accounts WHERE id=?',(key,)).fetchone())
 def change(self,uid,key,amount=0,reserve=0,paid=0):
  if not self.db.in_transaction:raise RuntimeError('Wallet mutation requires transaction')
  account=self.account(uid);key=self.ns+':'+key
  if self.db.execute('SELECT 1 FROM wallet.entries WHERE id=?',(key,)).fetchone():return False
  row=self.db.execute('SELECT * FROM wallet.accounts WHERE id=?',(account,)).fetchone()
  if row['reserved']+reserve<0 or (reserve>0 and row['balance']-row['reserved']<reserve):raise ValueError('balance')
  self.db.execute('UPDATE wallet.accounts SET balance=balance+?,reserved=reserved+?,paid=paid+? WHERE id=?',(amount,reserve,paid,account))
  self.db.execute('INSERT INTO wallet.entries VALUES(?,?,?,?,?)',(key,account,amount,reserve,time.time()))
  return True
 def status(self,uid):
  key=self.account(uid)
  row=self.db.execute("SELECT uid FROM wallet.members WHERE account=? AND namespace='telegram'",(key,)).fetchone()
  pending=self.db.execute("SELECT tg_uid,expires FROM wallet.links WHERE web_uid=? AND status='claimed' AND expires>? ORDER BY expires DESC LIMIT 1",(uid,time.time())).fetchone()
  return {'linked':bool(row),'telegram_id':row[0] if row else None,'pending_telegram_id':pending[0] if pending else None}
 def begin(self,uid,label=''):
  if self.ns!='web':raise ValueError('Unavailable')
  with self.atomic():
   if self.status(uid)['linked']:raise ValueError('Telegram уже привязан.')
   self.db.execute("UPDATE wallet.links SET status='cancelled' WHERE web_uid=? AND status IN ('pending','claimed')",(uid,))
   token=secrets.token_urlsafe(24)
   self.db.execute('INSERT INTO wallet.links(token,web_uid,tg_uid,expires,status,web_label) VALUES(?,?,NULL,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),uid,time.time()+600,'pending',label))
   return token
 def preview(self,token):
  with self.atomic():
   row=self.db.execute("SELECT web_label FROM wallet.links WHERE token=? AND status='pending' AND expires>?",(hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
   if not row:raise ValueError('Ссылка истекла или уже использована. Создайте новую на сайте.')
   return row['web_label']
 def claim(self,uid,token):
  with self.atomic():return self._claim(uid,token)
 def _claim(self,uid,token):
  if self.ns!='telegram' or uid<=0:raise ValueError('Используйте личный чат с ботом.')
  self.account(uid)
  key=hashlib.sha256(token.encode()).hexdigest()
  row=self.db.execute("SELECT * FROM wallet.links WHERE token=? AND status='pending' AND expires>?",(key,time.time())).fetchone()
  if not row:raise ValueError('Ссылка истекла или уже использована. Создайте новую на сайте.')
  account=self.account(uid)
  if self.db.execute("SELECT 1 FROM wallet.members WHERE account=? AND namespace='web'",(account,)).fetchone():raise ValueError('Этот Telegram уже связан с кабинетом.')
  self.db.execute("UPDATE wallet.links SET tg_uid=?,status='claimed' WHERE token=?",(uid,key))
 def confirm(self,uid,tg_uid):
  if self.ns!='web' or type(tg_uid) is not int:raise ValueError('Некорректная привязка.')
  with self.atomic():
   row=self.db.execute("SELECT * FROM wallet.links WHERE web_uid=? AND tg_uid=? AND status='claimed' AND expires>?",(uid,tg_uid,time.time())).fetchone()
   if not row:raise ValueError('Подтверждение истекло. Начните привязку заново.')
   a=self.account(uid);b=self.db.execute("SELECT account FROM wallet.members WHERE namespace='telegram' AND uid=?",(tg_uid,)).fetchone()[0]
   if a==b:raise ValueError('Аккаунты уже связаны.')
   if self.db.execute("SELECT 1 FROM wallet.members WHERE (account=? AND namespace='telegram') OR (account=? AND namespace='web')",(a,b)).fetchone():raise ValueError('Один из аккаунтов уже привязан.')
   first=self.db.execute('SELECT * FROM wallet.accounts WHERE id=?',(a,)).fetchone();second=self.db.execute('SELECT * FROM wallet.accounts WHERE id=?',(b,)).fetchone()
   if first['reserved'] or second['reserved']:raise ValueError('Дождитесь завершения генераций перед привязкой.')
   self.db.execute('UPDATE wallet.accounts SET balance=balance+?,paid=paid+? WHERE id=?',(second['balance'],second['paid'],a))
   self.db.execute('UPDATE wallet.members SET account=? WHERE account=?',(a,b))
   self.db.execute('UPDATE wallet.entries SET account=? WHERE account=?',(a,b))
   self.db.execute('DELETE FROM wallet.accounts WHERE id=?',(b,))
   self.db.execute("UPDATE wallet.links SET status='used' WHERE web_uid=? OR tg_uid=?",(uid,tg_uid))
