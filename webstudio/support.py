"""Persisted support conversations; optional dedicated Telegram support bot."""
import json
import re
import time
import threading
import imaplib
import email
from email.header import decode_header
from email.message import Message
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import httpx

class Support:
    def __init__(self, store, root, app_config=None):
        self.s=store
        from .support_ai import SupportAI
        self.assistant=SupportAI(store,app_config)
        self.turn_locks=[threading.Lock() for _ in range(64)]
        path=Path(root)/'support-config.json'
        self.config=json.loads(path.read_text()) if path.exists() else {}
        inbox_path=Path(root)/'support-inbox.json'
        self.inbox_config=json.loads(inbox_path.read_text()) if inbox_path.exists() else {}
        self.inbox_checked=0.0
        self.s.db.executescript('''
        CREATE TABLE IF NOT EXISTS support_tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,uid INTEGER UNIQUE,status TEXT NOT NULL DEFAULT 'bot',email_reply INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS support_messages(id INTEGER PRIMARY KEY AUTOINCREMENT,ticket INTEGER,role TEXT,body TEXT,created REAL,notified INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS support_mail(message INTEGER PRIMARY KEY,recipient TEXT,body TEXT,sent INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS support_updates(id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS support_ai_usage(message INTEGER PRIMARY KEY,usage TEXT);
        CREATE TABLE IF NOT EXISTS support_inbox_state(mailbox TEXT PRIMARY KEY,last_uid INTEGER NOT NULL DEFAULT 0);
        ''')
        columns={r['name'] for r in self.s.db.execute('PRAGMA table_info(support_tickets)')}
        for name,kind in [('waiting_since','REAL'),('away_notified','INTEGER DEFAULT 0'),('contact_email','TEXT'),('handoff_requested','INTEGER DEFAULT 0'),('help_attempts','INTEGER DEFAULT 0')]:
            if name not in columns:self.s.db.execute(f'ALTER TABLE support_tickets ADD COLUMN {name} {kind}')

    @staticmethod
    def working(now=None):
        local=datetime.fromtimestamp(time.time() if now is None else now,ZoneInfo('Asia/Dubai'))
        return local.weekday()<6 and 10<=local.hour<20

    def unavailable(self,ticket):
        self.add(ticket,'system','Сейчас специалист поддержки недоступен. Мы ответим здесь позже и автоматически отправим ответ на ваш email. Переписка сохранена. Живая поддержка: пн–сб, 10:00–20:00 по Дубаю (UTC+4).')
        self.s.db.execute('UPDATE support_tickets SET away_notified=1 WHERE id=?',(ticket,))

    def check_waiting(self):
        with self.s.transaction():
            for row in self.s.db.execute("SELECT id,waiting_since FROM support_tickets WHERE status='waiting' AND away_notified=0").fetchall():
                if not self.working() or (row['waiting_since'] is not None and time.time()-row['waiting_since']>=300):self.unavailable(row['id'])

    def operator_snapshot(self,ticket):
        with self.s.lock:
            t=self.s.db.execute('SELECT uid FROM support_tickets WHERE id=?',(ticket,)).fetchone()
            if not t:raise ValueError('Обращение не найдено.')
            result=self.snapshot(t['uid']);result['ticket']=ticket
            return result

    def join(self,ticket):
        with self.s.transaction():
            t=self.s.db.execute('SELECT status FROM support_tickets WHERE id=?',(ticket,)).fetchone()
            if not t:raise ValueError('Обращение не найдено.')
            if t['status']!='human':
                self.s.db.execute("UPDATE support_tickets SET status='human' WHERE id=?",(ticket,))
                self.add(ticket,'system','Специалист подключился к разговору.')
        return self.operator_snapshot(ticket)

    def reply(self,ticket,text):
        if not isinstance(text,str) or not 1<=len(text.strip())<=4000:raise ValueError('Напишите ответ до 4 000 символов.')
        with self.s.transaction():
            if not self.s.db.execute('SELECT id FROM support_tickets WHERE id=?',(ticket,)).fetchone():raise ValueError('Обращение не найдено.')
            self.add(ticket,'operator',text.strip());self.queue_mail(ticket,text.strip())
            self.s.db.execute("UPDATE support_tickets SET status='human' WHERE id=?",(ticket,))
            self.s.db.execute("UPDATE support_messages SET notified=1 WHERE ticket=? AND role='operator'",(ticket,))
        return self.operator_snapshot(ticket)

    def snapshot(self,uid):
        with self.s.lock:
            t=self.s.db.execute('SELECT * FROM support_tickets WHERE uid=?',(uid,)).fetchone()
            messages=[dict(r) for r in self.s.db.execute('SELECT id,role,body,created FROM support_messages WHERE ticket=? ORDER BY id',(t['id'],))] if t else []
            if not messages:messages=[{'id':0,'role':'bot','body':'Здравствуйте! Я помощник мультстудии. Напишите ваш email для ответа и опишите, что случилось. Постараюсь помочь здесь.','created':0}]
            return {'status':t['status'] if t else 'bot','messages':messages,'hours':self.config.get('hours','Пн–сб, 10:00–20:00 по Дубаю (UTC+4). Вне этих часов оставьте сообщение — ответим в рабочее время.'),'email_reply':bool(t and t['email_reply']),'email_available':bool(self.config.get('resend_key'))}

    def add(self,ticket,role,text):
        self.s.db.execute('INSERT INTO support_messages(ticket,role,body,created) VALUES (?,?,?,?)',(ticket,role,text,time.time()))

    @staticmethod
    def asks_operator(text):
        text=text.lower().strip()
        if re.search(r'(не надо|не нужно|не хочу|не соедин|не зови|без оператора|don.t|do not)',text):return False
        return bool(re.fullmatch(r'(оператор|агент|менеджер|operator|agent|human)[!?. ]*',text) or re.search(
            r'(соедин|свяж|переключ|позов|приглас|хочу поговорить|нужен|нужна|можно).*?(оператор|агент|человек|менеджер|специалист|жив.{0,12}поддерж)|'
            r'(connect|transfer|speak|talk).*?(agent|human|operator)|'
            r'(з.єдн|поклич|переключ).*?(оператор|агент|людин)',text))

    def handoff(self,ticket):
        self.s.db.execute("UPDATE support_tickets SET status='waiting',waiting_since=?,away_notified=0,handoff_requested=0 WHERE id=?",(time.time(),ticket))
        self.add(ticket,'system','Передаю вопрос специалисту вместе с перепиской. Ответ появится здесь и придёт на email.')
        if not self.working():self.unavailable(ticket)

    def send(self,uid,data):
        with self.turn_locks[uid % len(self.turn_locks)]:return self._send(uid,data)

    def _send(self,uid,data):
        ai_request=None
        text=data.get('text','')
        if not isinstance(text,str) or not 1<=len(text.strip())<=4000:raise ValueError('Напишите сообщение до 4 000 символов.')
        text=text.strip()
        with self.s.transaction():
            self.s.db.execute('INSERT OR IGNORE INTO support_tickets(uid) VALUES (?)',(uid,))
            t=self.s.db.execute('SELECT * FROM support_tickets WHERE uid=?',(uid,)).fetchone()
            self.add(t['id'],'user',text)
            if t['status']=='human':
                self.s.db.execute("UPDATE support_tickets SET status='waiting',waiting_since=?,away_notified=0 WHERE id=?",(time.time(),t['id']))
                if not self.working():self.unavailable(t['id'])
            elif t['status']=='bot':
                wants=self.asks_operator(text) or bool(t['handoff_requested'])
                match=re.search(r"(?<![\w@])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",text)
                email=match.group(0) if match and len(match.group(0))<=254 else t['contact_email']
                if email:
                    self.s.db.execute('UPDATE support_tickets SET contact_email=? WHERE id=?',(email,t['id']))
                if not email:
                    self.s.db.execute('UPDATE support_tickets SET handoff_requested=? WHERE id=?',(int(wants),t['id']))
                    self.add(t['id'],'bot','Напишите, пожалуйста, email, на который прислать ответ. Ваш вопрос уже сохранён.'+(' После этого передам переписку специалисту.' if wants else ' Затем попробуем решить его здесь.'))
                elif wants:self.handoff(t['id'])
                else:
                    question=(text[:match.start()]+text[match.end():]).strip(' ,.;\n') if match else text
                    if not question:
                        prior=self.s.db.execute("SELECT body FROM support_messages WHERE ticket=? AND role='user' AND id<(SELECT MAX(id) FROM support_messages WHERE ticket=?) ORDER BY id DESC LIMIT 1",(t['id'],t['id'])).fetchone()
                        question=prior['body'] if prior else ''
                    if not question:
                        self.add(t['id'],'bot','Спасибо! Опишите, что не получается и на каком шаге вы остановились.')
                    else:
                        ai_request=(t['id'],self.assistant.context(uid,t['id']))
        if ai_request:
            ticket,context=ai_request
            try:
                result,usage=self.assistant.respond(context)
            except Exception:
                result={'answer':'Сейчас не удалось получить ответ помощника. Передам ваш вопрос специалисту вместе с перепиской.','handoff':True};usage={}
            with self.s.transaction():
                current=self.s.db.execute('SELECT status FROM support_tickets WHERE id=?',(ticket,)).fetchone()
                # Never speak over an operator who joined while the API was responding.
                if current['status']=='bot':
                    self.add(ticket,'bot',result['answer'])
                    mid=self.s.db.execute('SELECT MAX(id) FROM support_messages WHERE ticket=?',(ticket,)).fetchone()[0]
                    self.s.db.execute('INSERT INTO support_ai_usage VALUES (?,?)',(mid,json.dumps(usage)))
                    if result['handoff']:self.handoff(ticket)
        return self.snapshot(uid)

    def telegram(self,method,**data):
        r=httpx.post('https://api.telegram.org/bot'+self.config['telegram_token']+'/'+method,json=data,timeout=15)
        r.raise_for_status();result=r.json()
        if not result.get('ok'):raise RuntimeError('Support delivery failed')
        return result['result']

    @staticmethod
    def decode_header_value(value):
        parts=[]
        for chunk,encoding in decode_header(value or ''):
            if isinstance(chunk,bytes):parts.append(chunk.decode(encoding or 'utf-8','replace'))
            else:parts.append(chunk)
        return ''.join(parts)

    @staticmethod
    def plain_email_body(message):
        candidates=message.walk() if message.is_multipart() else [message]
        for part in candidates:
            if part.get_content_type()!='text/plain' or part.get_content_disposition()=='attachment':continue
            payload=part.get_payload(decode=True)
            if payload:
                charset=part.get_content_charset() or 'utf-8'
                return re.sub(r'\s+',' ',payload.decode(charset,'replace')).strip()[:700]
        return ''

    def poll_inbox(self):
        cfg=self.inbox_config
        if not all(cfg.get(k) for k in ('email','password')) or not self.config.get('telegram_token') or not self.config.get('admin_id'):return
        now=time.monotonic()
        if now-self.inbox_checked<60:return
        self.inbox_checked=now
        mailbox=cfg['email'].strip().lower()
        connection=None
        try:
            connection=imaplib.IMAP4_SSL(cfg.get('host','mail.privateemail.com'),int(cfg.get('port',993)),timeout=20)
            connection.login(mailbox,cfg['password'])
            status,_=connection.select('INBOX',readonly=True)
            if status!='OK':return
            with self.s.lock:
                row=self.s.db.execute('SELECT last_uid FROM support_inbox_state WHERE mailbox=?',(mailbox,)).fetchone()
            if row is None:
                status,data=connection.uid('search',None,'ALL')
                latest=max((int(x) for x in data[0].split()),default=0) if status=='OK' and data and data[0] else 0
                with self.s.transaction():self.s.db.execute('INSERT OR IGNORE INTO support_inbox_state(mailbox,last_uid) VALUES (?,?)',(mailbox,latest))
                return
            last_uid=row['last_uid']
            status,data=connection.uid('search',None,f'UID {last_uid+1}:*')
            if status!='OK' or not data or not data[0]:return
            for raw_uid in data[0].split():
                uid=int(raw_uid)
                if uid<=last_uid:continue
                status,parts=connection.uid('fetch',str(uid),'(RFC822)')
                if status!='OK':continue
                raw=next((item[1] for item in parts if isinstance(item,tuple) and isinstance(item[1],bytes)),None)
                if raw is None:continue
                message=email.message_from_bytes(raw)
                sender=self.decode_header_value(message.get('From','')).replace('\n',' ')[:300]
                subject=self.decode_header_value(message.get('Subject','(без темы)')).replace('\n',' ')[:300]
                preview=self.plain_email_body(message)
                text=f'Новое письмо · {mailbox}\nОт: {sender}\nТема: {subject}'
                if preview:text+='\n\n'+preview
                self.telegram('sendMessage',chat_id=int(self.config['admin_id']),text=text[:3500])
                with self.s.transaction():
                    self.s.db.execute('INSERT INTO support_inbox_state(mailbox,last_uid) VALUES (?,?) ON CONFLICT(mailbox) DO UPDATE SET last_uid=MAX(last_uid,excluded.last_uid)',(mailbox,uid))
                last_uid=uid
        finally:
            if connection is not None:
                try:connection.logout()
                except Exception:pass

    def tick(self):
        self.check_waiting()
        # Must be a dedicated support bot: never consume the production bot's updates.
        if not self.config.get('dedicated_bot') or not self.config.get('telegram_token') or not self.config.get('admin_id'):return
        admin=int(self.config['admin_id'])
        with self.s.lock:
            pending=[dict(r) for r in self.s.db.execute("SELECT m.*,t.uid FROM support_messages m JOIN support_tickets t ON t.id=m.ticket WHERE t.status!='bot' AND m.notified=0 ORDER BY m.id LIMIT 10")]
        for m in pending:
            for start in range(0,len(m['body']),3500):
                self.telegram('sendMessage',chat_id=admin,text=f"Мультстудия · обращение #{m['ticket']} · {m['role']}\n{m['body'][start:start+3500]}\n\nОткрыть чат: {self.config.get('studio_url','http://127.0.0.1:8090/').rstrip('/')}?supportTicket={m['ticket']}")
            with self.s.transaction():self.s.db.execute('UPDATE support_messages SET notified=1 WHERE id=?',(m['id'],))
    def queue_mail(self,ticket,body):
        row=self.s.db.execute('SELECT COALESCE(t.contact_email,a.email) AS email FROM support_tickets t LEFT JOIN web_accounts a ON a.uid=t.uid WHERE t.id=?',(ticket,)).fetchone()
        if row and row['email']:
            message=self.s.db.execute('SELECT MAX(id) FROM support_messages WHERE ticket=?',(ticket,)).fetchone()[0]
            self.s.db.execute('INSERT OR IGNORE INTO support_mail(message,recipient,body) VALUES (?,?,?)',(message,row['email'],body))

    def flush_mail(self):
        if not self.config.get('resend_key'):return
        with self.s.lock:
            pending=[dict(r) for r in self.s.db.execute('SELECT * FROM support_mail WHERE sent=0 ORDER BY message LIMIT 5')]
        for item in pending:
            sender=self.config.get('mail_from','support@neurohelen.com')
            response=httpx.post('https://api.resend.com/emails',headers={'Authorization':'Bearer '+self.config['resend_key'],'Idempotency-Key':'cartoon-support-'+str(item['message'])},json={'from':'NeuroHelen Studio <'+sender+'>','to':[item['recipient']],'reply_to':sender,'subject':'Ответ поддержки мультстудии NeuroHelen','text':item['body']+'\n\nОтвет также сохранён в чате поддержки мультстудии.'},timeout=15)
            response.raise_for_status()
            with self.s.transaction():self.s.db.execute('UPDATE support_mail SET sent=1 WHERE message=?',(item['message'],))

    def loop(self,stop):
        while not stop.is_set():
            try:self.tick()
            except Exception:pass
            try:self.poll_inbox()
            except Exception:pass
            try:self.flush_mail()
            except Exception:pass # Keep messages pending for retry; never log credentials.
            stop.wait(4)
