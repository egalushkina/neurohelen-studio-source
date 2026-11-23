"""Isolated local browser pilot. No Telegram polling, API calls or real payments.
Run with: python -m webstudio.server --port 8090
"""
import argparse
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import secrets
import threading
import time
from collections import defaultdict, deque
from email.parser import BytesParser
from email.policy import default
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from PIL import Image, ImageOps
from cartoonbot.config import Config
from cartoonbot.store import Store
from cartoonbot.engine import Engine
from cartoonbot.providers import DemoProvider, LiveProvider
from cartoonbot.worker import Worker
from cartoonbot.catalog import PACKAGES, package_usd, omni_tokens, seedance_tokens
from cartoonbot import media

STATIC = Path(__file__).parent / 'static'
UPLOAD_STATES = {'upload','seedance_source_upload','omni_source_upload','references','photo_refs',
                 'photo_prompt','scene_upload','repair_visual','style_reference_upload'}

class Problem(Exception):
    def __init__(self, message, status=400): self.message, self.status = message, status

class BrowserStore(Store):
    def __init__(self, path, initial_balance):
        super().__init__(path,initial_balance)
        self.context=threading.local()
        self.access_config=None
        if 'web_project' not in {r['name'] for r in self.db.execute('PRAGMA table_info(outbox)')}:
            self.db.execute('ALTER TABLE outbox ADD COLUMN web_project TEXT')

    def enqueue(self, p, kind, payload=None, reserve=0):
        from cartoonbot.access import allowed
        if self.access_config and kind not in ('recover','reels_text') and not allowed(self,self.access_config,p['user_id'],reserve):
            raise Problem('Чтобы создать сценарий, изображения или видео, купите пакет токенов. Ответы опроса сохранены.',402)
        return super().enqueue(p,kind,payload,reserve)

    def claim(self):
        job=super().claim()
        self.context.project=job['project_id'] if job else None
        return job

    def emit(self, uid, text='', buttons=None, file=None, kind=None, navigation=False):
        # Background jobs keep their own project even if the user opens another.
        pid=getattr(self.context,'project',None) or self.user(uid)['current']
        body={'text':text,'buttons':buttons or [],'file':file,'kind':kind,'navigation':navigation}
        self.db.execute('INSERT INTO outbox(user_id,body,web_project) VALUES (?,?,?)',
                        (uid,json.dumps(body,ensure_ascii=False),pid))


class BrowserEngine(Engine):
    def queue(self,p,kind,payload=None,reserve=0):
        from cartoonbot.access import allowed
        if kind not in ('recover','reels_text') and not allowed(self.s,self.c,p['user_id'],reserve):
            p['data']['web_pending_generation']={'kind':kind,'payload':payload,'reserve':reserve}
            self.s.save(p)
            self.s.emit(p['user_id'],'Чтобы создать сценарий, изображения или видео, купите пакет токенов. Ответы опроса сохранены.',[[('Купить токены','token_shop'),('Продолжить после оплаты','web:resume')]])
            return
        return super().queue(p,kind,payload,reserve)


class Studio:
    def __init__(self, root, config=None):
        self.root = Path(root).resolve()
        # Never load .env or connect to the Telegram database in this local pilot.
        self.config = config or Config(root=self.root, mode='demo', billing_enabled=True, public_access=True)
        self.config.prepare()
        self.store = BrowserStore(self.root/'browser.sqlite3', initial_balance=3000 if self.config.mode=='demo' else 0)
        self.store.access_config=self.config
        self.engine = BrowserEngine(self.store, self.config)
        self.store.db.executescript('''
          CREATE TABLE IF NOT EXISTS web_accounts(uid INTEGER PRIMARY KEY, email TEXT UNIQUE, salt TEXT, password TEXT);
          CREATE TABLE IF NOT EXISTS web_sessions(token TEXT PRIMARY KEY, uid INTEGER, csrf TEXT, expires REAL);
          CREATE TABLE IF NOT EXISTS web_files(id TEXT PRIMARY KEY, uid INTEGER, path TEXT, UNIQUE(uid,path));
          CREATE TABLE IF NOT EXISTS web_requests(id INTEGER PRIMARY KEY AUTOINCREMENT);
        ''')
        from .support import Support
        self.support=Support(self.store,self.root,self.config)
        from .payments import Payments
        self.payments=Payments(self.store,self.root,self.config.admins)
        from .google_login import GoogleLogin
        self.google_login=GoogleLogin(self)
        from .firebase_login import FirebaseLogin
        self.firebase_login=FirebaseLogin(self)
        from .analytics import Analytics
        self.analytics=Analytics(self.store,self.config.admins)
        self.stop = threading.Event()
        self.rate = defaultdict(deque)
        self.rate_lock = threading.Lock()

    def throttle(self, key, limit=12):
        with self.rate_lock:
            q=self.rate[key]; now=time.monotonic()
            while q and q[0]<now-60:q.popleft()
            if len(q)>=limit:raise Problem('Слишком много запросов. Попробуйте через минуту.',429)
            q.append(now)

    @staticmethod
    def digest(password, salt):
        return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()

    def auth(self, email, password, register):
        if not isinstance(email,str) or not isinstance(password,str):raise Problem('Укажите email и пароль.')
        email=email.strip().lower()
        if not (3<=len(email)<=254 and '@' in email) or not 10<=len(password)<=256:
            raise Problem('Укажите email и пароль от 10 символов.')
        # Expensive password work stays outside the database lock.
        salt=secrets.token_hex(16)
        with self.store.lock:
            old=self.store.db.execute('SELECT * FROM web_accounts WHERE email=?',(email,)).fetchone()
        hashed=self.digest(password,old['salt'] if old else salt)
        if register:
            if self.config.mode=='live' and not self.config.public_access:raise Problem('Сейчас доступ открыт только владельцу студии.',403)
            if old:raise Problem('Аккаунт уже существует. Выберите «Войти».',409)
            with self.store.transaction():
                if self.store.db.execute('SELECT 1 FROM web_accounts WHERE email=?',(email,)).fetchone():
                    raise Problem('Аккаунт уже существует.',409)
                uid=-(secrets.randbelow(2**50)+1)
                self.store.user(uid,'ru')
                self.store.db.execute('INSERT INTO web_accounts VALUES (?,?,?,?)',(uid,email,salt,hashed))
        else:
            if not old or not hmac.compare_digest(old['password'],hashed):raise Problem('Неверный email или пароль.',401)
            uid=old['uid']
        return self.create_session(uid)

    def create_session(self,uid):
        token=secrets.token_urlsafe(32); csrf=secrets.token_urlsafe(32)
        with self.store.transaction():
            self.store.db.execute('DELETE FROM web_sessions WHERE expires<?',(time.time(),))
            self.store.db.execute('INSERT INTO web_sessions VALUES (?,?,?,?)',
                                  (hashlib.sha256(token.encode()).hexdigest(),uid,csrf,time.time()+7*86400))
        return token,csrf,uid

    def session(self, cookie):
        try:
            c=SimpleCookie();c.load(cookie or '');token=c['studio'].value
        except (KeyError,ValueError):raise Problem('Войдите в студию.',401)
        with self.store.lock:
            row=self.store.db.execute('SELECT * FROM web_sessions WHERE token=? AND expires>?',
                                      (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
        if not row:raise Problem('Войдите в студию.',401)
        return dict(row)

    def file_url(self, uid, path):
        path=Path(path).resolve()
        if not path.is_relative_to(self.root/'media') or not path.is_file():return None
        row=self.store.db.execute('SELECT id FROM web_files WHERE uid=? AND path=?',(uid,str(path))).fetchone()
        fid=row['id'] if row else secrets.token_hex(20)
        if not row:self.store.db.execute('INSERT INTO web_files VALUES (?,?,?)',(fid,uid,str(path)))
        return '/media/'+fid

    def snapshot(self, uid, csrf):
        with self.store.lock:
            user=self.store.user(uid)
            account=self.store.db.execute('SELECT email FROM web_accounts WHERE uid=?',(uid,)).fetchone()
            projects=[]
            for row in self.store.db.execute('SELECT * FROM projects WHERE user_id=? ORDER BY created DESC LIMIT 30',(uid,)):
                d=json.loads(row['body'])
                projects.append({'id':row['id'],'title':d.get('script',{}).get('title') or d.get('brief',{}).get('idea','Новый проект')[:65],
                                 'state':row['state'],'route':d['route'],'created':row['created']})
            p=self.store.project(user['current'],uid) if user['current'] else None
            if p and p['state']=='busy':
                active=self.store.db.execute("SELECT 1 FROM jobs WHERE project_id=? AND status IN ('queued','running') LIMIT 1",(p['id'],)).fetchone()
                pending=p['data'].get('pending_generation')
                job=self.store.db.execute('SELECT * FROM jobs WHERE id=? AND project_id=?',(pending.get('job'),p['id'])).fetchone() if isinstance(pending,dict) else None
                if not active and job and job['status'] not in ('queued','running') and not self.store.unresolved_charge(dict(job)):
                    # A terminal provider result can leave the project marked busy
                    # after later edits. Restore its saved stage without retrying generation.
                    resume=p['data'].get('return_state')
                    if resume in {'quote','video','finished','visuals'}:
                        p['data'].pop('pending_generation',None)
                        p['state']=resume
                        self.store.save(p)
                        if resume=='quote':self.engine.show(p)
            current=None
            if p:
                d=p['data']; scenes=[]
                for scene in d.get('script',{}).get('scenes',[]):
                    sid=str(scene['id'])
                    image=d.get('images',{}).get(sid,{})
                    video=d.get('videos',{}).get(sid,{})
                    scenes.append({'id':scene['id'],'action':scene['action'],'dialogue':scene['dialogue'],'camera':scene.get('camera',''),
                                   'image':self.file_url(uid,image['path']) if image.get('path') else None,
                                   'video':self.file_url(uid,video['path']) if video.get('path') else None})
                visuals=scenes
                if d['route']=='seedance' and d.get('visual_source')!='upload':
                    visuals=[{'id':a['id'],'name':a['name'],
                              'image':self.file_url(uid,d['images'][str(a['id'])]['path']) if d.get('images',{}).get(str(a['id']),{}).get('path') else None}
                             for a in d.get('script',{}).get('assets',[])]
                current={'id':p['id'],'revision':p['revision'],'state':p['state'],'route':d['route'],
                         'title':d.get('script',{}).get('title','Новый проект'),'scenes':scenes,'visuals':visuals,
                         'dialogue_proposal':({k:d['dialogue_proposal'].get(k) for k in ('id','original','proposed','return_state','failed')} if p['state']=='dialogue_proposal' and isinstance(d.get('dialogue_proposal'),dict) else None),
                         'complete_video':self.file_url(uid,d['final']) if (p['state']=='finished' or (p['state']=='busy' and d.get('return_state')=='finished')) and d.get('final') else None,
                         'assembled_video':self.file_url(uid,d['assembled']) if p['state']=='video' and d.get('assembled') else None,
                         'next_part':d.get('next_part'),'previous_part':d.get('previous_part'),'part_number':d.get('part_number'),'part_durations':d.get('web_part_durations'),
                         'can_upload':p['state'] in UPLOAD_STATES,
                         'can_text':p['state'] in {'intake','paste_script','recreate_changes','omni_source_dialogue','reference_assignment','references','photo_prompt','photo_edit','edit_input','edit_confirm','repair_visual','repair_visual_ready'},
                         'input_placeholder': 'Вставьте ваш сценарий…' if p['state']=='paste_script' else 'Опишите, что нужно изменить…' if p['state'] in {'photo_edit','edit_input','edit_confirm','repair_visual','repair_visual_ready','recreate_changes'} else 'Напишите ответ на вопрос выше…'}
            rows=list(self.store.db.execute('SELECT id,body FROM outbox WHERE user_id=? AND web_project IS ? ORDER BY id DESC LIMIT 80',(uid,p['id'] if p else None)))
            messages=[]
            for r in reversed(rows):
                body=json.loads(r['body'])
                # Do not expose internal paths or Telegram-only invoices.
                buttons=[]
                for group in body.get('buttons',[]):
                    for label,action in group:
                        if action.startswith(('pay:','https://t.me/','stats:')):continue
                        if action.endswith((':source_mode=direct', ':reference_length=15', ':reference_length=30')): continue
                        buttons.append({'label':label,'action':action,'disabled':False,'hint':''})
                messages.append({'id':r['id'],'text':body.get('text',''), 'buttons':buttons,
                                 'kind':body.get('kind'),'url':self.file_url(uid,body['file']) if body.get('file') else None})
            controls=[]
            if p and p['state']!='busy':
                prefix=f"p:{p['id']}:{p['revision']}:"
                for message in reversed(messages):
                    found=[b for b in message['buttons'] if b['action'].startswith(prefix) or (p['state']=='paste_script' and b['action'].startswith(('scripts:','use_script:')))]
                    if found:
                        controls=found;break
            if messages:
                menu=[b for b in messages[-1]['buttons'] if b['action']=='paste_own' or b['action'].startswith(('scripts:','use_script:'))]
                if menu: controls=menu
                if p and p['data'].get('web_pending_generation'):
                    controls=[{'label':'Купить токены','action':'token_shop'},{'label':'Продолжить после оплаты','action':'web:resume'}]
            if p and p['state']=='quote' and not controls and not p['data'].get('web_pending_generation'):
                # Quote text can remain in the outbox while its revision-bound button is
                # missing or stale. Rebuild the primary action from the current project.
                from cartoonbot.i18n import tr
                d=p['data']; reserve,seconds=self.engine.quote(d)
                rate=omni_tokens(self.config.token_prices) if d['route']=='omni' else seedance_tokens(d,d['version'],self.config.token_prices)
                tokens=None if rate is None else rate*(seconds//10 if d['route']=='omni' else 1)
                label=tr(user['lang'],'confirm_video_tokens',tokens=tokens) if tokens is not None else tr(user['lang'],'generate')
                controls=[{'label':label,'action':f"p:{p['id']}:{p['revision']}:generate"}]
            packages=[{'key':key,'tokens':tokens,'rub':rub,'usd':package_usd(key,rub)} for key,_,tokens,rub in PACKAGES]
            return {'is_owner':uid in self.config.admins,'demo':self.config.mode=='demo','email':account['email'],'balance':user['balance'],'reserved':user['reserved'],
                    'language':user['lang'],'language_manual':bool(user['manual']),'csrf':csrf,'projects':projects,'project':current,'messages':messages,'controls':controls,'packages':packages}

    def recommend(self, data):
        idea=data.get('idea',''); language=data.get('language'); seconds=data.get('seconds'); speech=data.get('speech')
        if not isinstance(idea,str) or not 3<=len(idea.strip())<=6000 or language not in ('ru','uk','en') or type(seconds) is not int or seconds not in (10,15,20,30,40,50,60) or type(speech) is not bool:
            raise Problem('Опишите идею и выберите озвучку, язык и длительность.')
        scenes=(seconds+9)//10; duration=15 if seconds<=15 else 30
        omni=scenes*omni_tokens(self.config.token_prices)
        if seconds>30:
            parts=[30,15 if seconds==40 else 30]
            seed=sum(seedance_tokens({'seconds':n},'2.5',self.config.token_prices) for n in parts)
            recommended='omni' if speech and (language in ('ru','uk') or omni<=seed) else 'seedance'
            return {'recommended':recommended,'options':[{'route':'omni','seconds':scenes*10,'scenes':scenes,'tokens':omni,'version':None},{'route':'seedance','seconds':sum(parts),'parts':parts,'scenes':None,'tokens':seed,'version':'2.5'}]}
        version='2.0-fast' if duration==15 else '2.5'
        seed=seedance_tokens({'seconds':duration},version,self.config.token_prices)
        recommended='omni' if speech and language in ('ru','uk') else 'seedance' if not speech else ('omni' if omni<=seed else 'seedance')
        return {'recommended':recommended,'options':[{'route':'omni','seconds':scenes*10,'scenes':scenes,'tokens':omni,'version':None},{'route':'seedance','seconds':duration,'scenes':None,'tokens':seed,'version':version}]}

    def start_recommended(self, uid, body):
        quote=self.recommend(body)
        route=body.get('route')
        if route not in {x['route'] for x in quote['options']}:raise Problem('Для этой длительности выберите Omni Flash.')
        with self.store.transaction():
            p=self.store.new_project(uid,route,self.store.user(uid)['lang'])
            d=p['data'];option=next(x for x in quote['options'] if x['route']==route)
            d['lang']=body['language'];d['audio_mode']=body['language'] if body['speech'] else 'none';d['lip']=body['speech']
            d['seconds']=option['seconds'];d['brief']['idea']=body['idea'].strip()
            d['brief']['audio']='Диалог на выбранном языке.' if body['speech'] else 'Без речи, диалогов и закадрового голоса.'
            d['brief']['idea']+='\nПожелание по длительности: '+str(body['seconds'])+' секунд. '+d['brief']['audio']
            d['seedance_source_selected']=True;d['omni_source_selected']=True
            d['auto_duration']=route=='omni'
            if route=='seedance':
                d['version']=option['version'];d['seedance_duration_chosen']=True
                if option.get('parts'):
                    d['web_part_durations']=option['parts']
                    d['brief']['duration_instruction']=f"Create ONE connected {option['seconds']}-second story, split into two parts of {option['parts'][0]} and {option['parts'][1]} seconds. Include an EXACT scene boundary at 30 seconds; no scene may cross it. Preserve the same cast, style and narrative continuity across both parts. The second part continues the first, with no repeated introduction. Both parts will be reviewed separately before video generation."

            p['state']='intake';self.store.save(p)
            if route=='omni':self.engine.queue(p,'script')
            else:self.engine.show(p)

    def edit_card(self, uid, body):
        with self.store.transaction():
            p=self.store.project(body.get('project'),uid)
            if not p or p['revision']!=body.get('revision'):
                raise Problem('Этот этап уже изменился. Обновите страницу.',409)
            d=p['data']; kind=body.get('kind')
            if kind=='script_request' and p['state'] in ('script','script_summary'):
                instruction=body.get('instruction','')
                if not isinstance(instruction,str) or not 1<=len(instruction.strip())<=6000:
                    raise Problem('Опишите изменения: до 6 000 символов.')
                self.engine.queue(p,'edit_script',{'request':instruction.strip()})
            elif kind=='script' and p['state'] in ('script','script_summary'):
                changes=body.get('scenes')
                original=d['script']['scenes']
                if not isinstance(changes,list) or [x.get('id') for x in changes]!=[x['id'] for x in original]:
                    raise Problem('Сохраните количество и порядок сцен.')
                for scene,change in zip(original,changes):
                    for key in ('action','dialogue','camera'):
                        value=change.get(key)
                        if not isinstance(value,str) or len(value)>6000 or (key=='action' and not value.strip()):
                            raise Problem(f"Сцена {scene['id']}: заполните поле «Действие». Длина каждого поля — до 6 000 символов.")
                for scene,change in zip(original,changes):
                    for key in ('action','dialogue','camera'):scene[key]=change[key].strip()
                if p['state']=='script_summary':
                    p['state']='script';d.pop('script_summary',None)
                self.store.save(p);self.engine.show(p)
            elif kind=='script_variant' and p['state']=='script':
                self.engine.queue(p,'edit_script',{'request':'Предложи ещё один вариант этого сценария. Сохрани идею, персонажей, число сцен, язык и длительность. Покажи новый вариант для утверждения.'})
            elif kind=='dialogue' and p['state']=='dialogue_proposal':
                proposal=d.get('dialogue_proposal'); text=body.get('dialogue')
                if not proposal or type(body.get('scene')) is not int or body['scene']!=proposal.get('id'):
                    raise Problem('Предложение диалога уже изменилось. Обновите страницу.',409)
                scene=next((x for x in d.get('script',{}).get('scenes',[]) if x.get('id')==proposal['id']),None)
                if not scene or scene.get('dialogue')!=proposal.get('original'):
                    raise Problem('Исходный диалог сцены уже изменился. Обновите страницу.',409)
                if not isinstance(text,str) or not text.strip() or len(text)>6000:
                    raise Problem('Введите диалог длиной до 6 000 символов.')
                proposal['proposed']=text.strip();proposal['failed']=False;proposal['manually_edited']=True
                self.store.save(p);self.engine.show(p)
            elif kind=='video' and p['state'] in ('video','finished'):
                sid=body.get('scene'); instruction=body.get('instruction','')
                if type(sid) is not int or str(sid) not in d.get('videos',{}) or not isinstance(instruction,str) or not 1<=len(instruction.strip())<=6000:
                    raise Problem('Выберите готовую видеосцену и опишите изменения.')
                d['video_edits']=[{'id':sid,'instruction':instruction.strip()}]
                d['resume_scene_ids']=[sid]
                d['skipped_scenes']=[n for n in d.get('skipped_scenes',[]) if n!=sid]
                self.engine.queue(p,'prepare_video_revision')
            elif kind=='visual' and p['state']=='visuals':
                sid=body.get('scene'); instruction=body.get('instruction','')
                if str(sid) not in d.get('images',{}) or not isinstance(instruction,str) or not 1<=len(instruction.strip())<=6000:
                    raise Problem('Выберите готовый кадр и опишите изменения.')
                self.engine.queue(p,'edit_visuals',{'edits':[{'id':sid,'instruction':instruction.strip()}]})
            else:raise Problem('Дождитесь завершения текущего этапа. Правки доступны до утверждения.',409)

    def dispatch(self, uid, action='', text='', upload=None, nonce=None):
        if not isinstance(action,str) or not isinstance(text,str) or len(action)>300 or len(text)>30000:
            raise Problem('Слишком длинное сообщение.')
        if action.endswith(':source_mode=direct') or action.endswith((':reference_length=15', ':reference_length=30')): raise Problem('Референсное видео используется только для анализа.')
        if action=='web:resume':
            with self.store.transaction():
                p=self.store.project(self.store.user(uid)['current'],uid)
                pending=p['data'].get('web_pending_generation') if p else None
                if not pending:raise Problem('Нет ожидающей генерации.')
                from cartoonbot.access import allowed
                if not allowed(self.store,self.config,uid,pending['reserve']):raise Problem('Сначала купите пакет токенов.',402)
                p['data'].pop('web_pending_generation',None)
                self.engine.queue(p,pending['kind'],pending['payload'],pending['reserve'])
            return
        if action.startswith(('pay:','stats:')):raise Problem('Это действие недоступно в браузерной демонстрации.')
        # Numeric IDs are never supplied by the browser. Engine enforces project ownership/revision.
        with self.store.transaction():
            cursor=self.store.db.execute('INSERT INTO web_requests DEFAULT VALUES')
            event_id=cursor.lastrowid
        event={'id':event_id,'user_id':uid,'language':self.store.user(uid)['lang'],'text':text,'action':action}
        if upload:event['media']=upload
        self.engine.handle(event)
        if action.endswith(':apply'):
            # Queue and shared-wallet reservation must commit together.
            with self.store.transaction():
                current=self.store.project(self.store.user(uid)['current'],uid)
                if current and current['state']=='quote' and current['data'].get('video_edits'):
                    self.engine.queue(current,'prepare_video_revision')

    def worker_loop(self):
        worker=Worker(self.store,self.config,(LiveProvider if self.config.mode=='live' else DemoProvider)(self.store,self.config),self.engine)
        while not self.stop.is_set():
            try:
                if not worker.run_once(): self.stop.wait(.4)
            except Exception as exc:
                print('Browser worker:',type(exc).__name__,flush=True);self.stop.wait(1)

class Handler(BaseHTTPRequestHandler):
    server_version='Studio'
    def log_message(self,*args):pass
    @property
    def app(self):return self.server.app

    def setup(self):
        super().setup()
        self.connection.settimeout(60)

    def client_ip(self):
        # Only the explicitly configured local reverse proxy can supply this header.
        if self.client_address[0] in getattr(self.server,'trusted_proxies',set()):
            value=self.headers.get('X-Forwarded-For','').split(',')[-1].strip()
            try:return str(ipaddress.ip_address(value))
            except ValueError:pass
        return self.client_address[0]

    def reply(self,status,body,ctype='application/json; charset=utf-8',cookie=None):
        if isinstance(body,(dict,list)):body=json.dumps(body,ensure_ascii=False).encode()
        elif isinstance(body,str):body=body.encode()
        self.send_response(status);self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if urlsplit(self.path).path=='/studio' or (urlsplit(self.path).path=='/' and urlsplit(self.path).query) or urlsplit(self.path).path.startswith(('/api/','/media/')):self.send_header('X-Robots-Tag','noindex, nofollow')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; style-src 'self' https://accounts.google.com/gsi/style; script-src 'self' https://accounts.google.com/gsi/client https://www.gstatic.com/firebasejs/ https://apis.google.com; connect-src 'self' https://accounts.google.com/gsi/ https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://neurohelen-grow.firebaseapp.com https://www.googleapis.com; frame-src https://accounts.google.com/gsi/ https://neurohelen-grow.firebaseapp.com; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if cookie:
            if self.server.origin.startswith('https://'):cookie+='; Secure'
            self.send_header('Set-Cookie',cookie)
        self.end_headers();self.wfile.write(body)

    def read_body(self,limit):
        try:size=int(self.headers.get('Content-Length','0'))
        except ValueError:raise Problem('Некорректный запрос.')
        if not 0<size<=limit:raise Problem('Файл слишком большой. Максимум 20 МБ.',413)
        return self.rfile.read(size)

    def send_signed_reference(self, include_body):
        from cartoonbot.reference_media import resolve
        import os
        try:file=resolve(self.app.root/'media',self.path,os.environ.get('MEDIA_SIGNING_KEY',''))
        except (ValueError,UnicodeError,OSError):raise Problem('Файл не найден или срок ссылки истёк.',404)
        size=file.stat().st_size;start=0;end=size-1
        header=self.headers.get('Range')
        if header:
            import re
            m=re.fullmatch(r'bytes=(\d+)-(\d*)',header)
            if not m:raise Problem('Неверный диапазон.',416)
            start=int(m[1]);end=min(int(m[2]) if m[2] else size-1,size-1)
            if start>end:raise Problem('Неверный диапазон.',416)
        self.send_response(206 if header else 200)
        self.send_header('Content-Type','video/mp4' if file.suffix.lower()=='.mp4' else 'video/quicktime')
        self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','private, no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if header:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.send_header('Content-Length',str(end-start+1));self.end_headers()
        if include_body:
            with file.open('rb') as f:
                f.seek(start);remaining=end-start+1
                while remaining:
                    chunk=f.read(min(65536,remaining))
                    if not chunk:break
                    self.wfile.write(chunk);remaining-=len(chunk)

    def send_example(self, include_body=True):
        import re
        name=urlsplit(self.path).path.removeprefix('/examples/')
        if not re.fullmatch(r'(?:video-[1-7]\.mp4|poster-[1-7]\.jpg)',name):
            raise Problem('Файл не найден.',404)
        file=STATIC/'examples'/name
        if not file.is_file():raise Problem('Файл не найден.',404)
        size=file.stat().st_size;start=0;end=size-1
        header=self.headers.get('Range')
        if header:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',header)
            if not match or not any(match.groups()):raise Problem('Неверный диапазон.',416)
            if match[1]:
                start=int(match[1]);end=min(int(match[2]) if match[2] else end,end)
            else:start=max(0,size-int(match[2]))
            if start>end:raise Problem('Неверный диапазон.',416)
        self.send_response(206 if header else 200)
        self.send_header('Content-Type',mimetypes.guess_type(file)[0])
        self.send_header('Accept-Ranges','bytes')
        self.send_header('Cache-Control','public, max-age=3600')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Length',str(end-start+1))
        if header:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        if include_body:
            with file.open('rb') as stream:
                stream.seek(start);remaining=end-start+1
                while remaining:
                    chunk=stream.read(min(65536,remaining))
                    if not chunk:break
                    self.wfile.write(chunk);remaining-=len(chunk)

    def do_HEAD(self):
        try:
            if urlsplit(self.path).path.startswith('/examples/'):return self.send_example(False)
            if not urlsplit(self.path).path.startswith('/v1/'):raise Problem('Страница не найдена.',404)
            self.send_signed_reference(False)
        except Problem as e:self.reply(e.status,{'error':e.message})
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception:self.reply(500,{'error':'Не удалось загрузить файл.'})

    def do_GET(self):
        try:
            path=urlsplit(self.path).path
            if path.startswith('/examples/'):return self.send_example()
            from .landing import COPY, SLUGS, render as render_landing, sitemap
            if path=='/sitemap.xml':
                return self.reply(200,sitemap(),'application/xml; charset=utf-8')
            parts=path.strip('/').split('/')
            if len(parts)<=2 and parts[0] in COPY and (len(parts)==1 or parts[1] in SLUGS[1:]):
                self.app.analytics.page(path,self.headers.get('Referer'),self.headers.get('User-Agent',''))
                return self.reply(200,render_landing(parts[0],parts[1] if len(parts)==2 else ''),'text/html; charset=utf-8')
            if path=='/' and not urlsplit(self.path).query:
                try:self.app.session(self.headers.get('Cookie'))
                except Problem:
                    return self.reply(200,render_landing(automatic=True),'text/html; charset=utf-8')
            if path=='/studio':
                return self.reply(200,(STATIC/'index.html').read_bytes(),'text/html; charset=utf-8')
            if path in ('/','/favicon.ico','/favicon.svg','/favicon-48.png','/apple-touch-icon.png','/app.js','/landing.js','/landing.css','/style.css','/art.svg','/hero.png','/style-3d.jpg','/style-anime.jpg','/style-2d.jpg','/style-clay.jpg','/style-watercolor.jpg','/style-realism.jpg','/languages.js','/firebase-auth.js','/robots.txt','/sitemap.xml','/googlee17ce3ce7f9b81d8.html','/yandex_178ba4fec78bbadc.html'):
                file=STATIC/('index.html' if path=='/' else path[1:])
                return self.reply(200,file.read_bytes(),mimetypes.guess_type(file)[0] or 'text/plain')
            if path.startswith('/v1/'):
                return self.send_signed_reference(True)
            if path=='/api/info':return self.reply(200,{'demo':self.app.config.mode=='demo','registration':self.app.config.public_access or self.app.config.mode=='demo','google_client_id':self.app.google_login.client_id if not self.app.firebase_login.enabled else '', 'firebase':self.app.firebase_login.public if self.app.firebase_login.enabled else None})
            session=self.app.session(self.headers.get('Cookie'));uid=session['uid']
            if path=='/api/analytics':
                if uid not in self.app.config.admins:raise Problem('Доступ только владельцу.',403)
                return self.reply(200,self.app.analytics.report())
            if path=='/api/telegram-link':
                if not self.app.store.wallet:return self.reply(200,{'available':False})
                with self.app.store.transaction():
                    result=self.app.store.wallet.status(uid)
                return self.reply(200,dict(result,available=True))
            if path=='/api/support/operator':
                if uid not in self.app.config.admins:raise Problem('Войдите под аккаунтом владельца.',403)
                from urllib.parse import parse_qs
                try:
                    ticket=int(parse_qs(urlsplit(self.path).query).get('ticket',['0'])[0])
                    return self.reply(200,self.app.support.operator_snapshot(ticket))
                except ValueError as exc:raise Problem(str(exc),404)
            if path=='/api/support':return self.reply(200,self.app.support.snapshot(uid))
            if path=='/api/state':return self.reply(200,self.app.snapshot(uid,session['csrf']))
            if path.startswith('/media/'):
                with self.app.store.lock:
                    row=self.app.store.db.execute('SELECT path FROM web_files WHERE id=? AND uid=?',(path[7:],uid)).fetchone()
                if not row:raise Problem('Файл не найден.',404)
                file=Path(row['path']).resolve()
                if not file.is_relative_to(self.app.root/'media') or not file.is_file():raise Problem('Файл не найден.',404)
                size=file.stat().st_size;start=0;end=size-1
                header=self.headers.get('Range')
                if header:
                    import re
                    m=re.fullmatch(r'bytes=(\d+)-(\d*)',header)
                    if not m:raise Problem('Неверный диапазон.',416)
                    start=int(m[1]);end=min(int(m[2]) if m[2] else size-1,size-1)
                    if start>end:raise Problem('Неверный диапазон.',416)
                self.send_response(206 if header else 200)
                self.send_header('Content-Type',mimetypes.guess_type(file)[0] or 'application/octet-stream')
                self.send_header('Accept-Ranges','bytes');self.send_header('Cache-Control','private, no-store')
                self.send_header('X-Content-Type-Options','nosniff')
                if header:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                self.send_header('Content-Length',str(end-start+1));self.end_headers()
                with file.open('rb') as f:
                    f.seek(start);remaining=end-start+1
                    while remaining:
                        chunk=f.read(min(65536,remaining))
                        if not chunk:break
                        self.wfile.write(chunk);remaining-=len(chunk)
                return
            raise Problem('Страница не найдена.',404)
        except Problem as e:self.reply(e.status,{'error':e.message})
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception:self.reply(500,{'error':'Не удалось загрузить данные. Попробуйте ещё раз.'})

    def do_POST(self):
        try:
            path=urlsplit(self.path).path
            # Exact configured origin; never infer trusted origin from an untrusted Host header.
            if self.headers.get('Origin')!=self.server.origin:raise Problem('Запрос из другого сайта запрещён.',403)
            self.app.throttle(('request',self.client_ip()),120)
            if path=='/api/firebase/login':
                self.app.throttle(('auth',self.client_ip()),12)
                data=self.json_body()
                try:token,csrf,uid=self.app.firebase_login.finish(data.get('credential'),data.get('password',''))
                except ValueError as exc:raise Problem(str(exc),400)
                return self.reply(200,{'csrf':csrf},cookie=f'studio={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800')
            if self.app.firebase_login.enabled and path in ('/api/google/begin','/api/google/login','/api/register','/api/login'):
                raise Problem('Используйте вход через Firebase.',400)
            if path in ('/api/google/begin','/api/google/login'):
                self.app.throttle(('auth',self.client_ip()),12)
                try:
                    if path.endswith('begin'):
                        browser,nonce=self.app.google_login.begin()
                        return self.reply(200,{'nonce':nonce},cookie=f'studio_google={browser}; HttpOnly; SameSite=Strict; Path=/api/google; Max-Age=600')
                    data=self.json_body();cookies=SimpleCookie();cookies.load(self.headers.get('Cookie',''))
                    browser=cookies['studio_google'].value if 'studio_google' in cookies else ''
                    token,csrf,uid=self.app.google_login.finish(browser,data.get('credential'),data.get('password',''))
                    return self.reply(200,{'csrf':csrf},cookie=f'studio={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800')
                except ValueError as exc:raise Problem(str(exc),400)
            if path in ('/api/register','/api/login'):
                self.app.throttle(('auth',self.client_ip()),12)
                data=self.json_body()
                token,csrf,uid=self.app.auth(data.get('email',''),data.get('password',''),path.endswith('register'))
                cookie=f'studio={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800'
                return self.reply(200,{'csrf':csrf},cookie=cookie)
            session=self.app.session(self.headers.get('Cookie'));uid=session['uid']
            if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']):raise Problem('Обновите страницу и повторите.',403)
            if path in ('/api/telegram-link/begin','/api/telegram-link/confirm'):
                import os
                self.app.throttle(('telegram-link',uid),6)
                wallet=self.app.store.wallet
                if not wallet:raise Problem('Привязка пока недоступна.',503)
                data=self.json_body()
                try:
                    if path.endswith('/begin'):
                        username=os.environ.get('STUDIO_TELEGRAM_USERNAME','')
                        if not username:raise Problem('Привязка пока недоступна.',503)
                        label=self.app.store.db.execute('SELECT email FROM web_accounts WHERE uid=?',(uid,)).fetchone()[0]
                        token=wallet.begin(uid,label)
                        return self.reply(200,{'url':'https://t.me/'+username+'?start=link_'+token})
                    wallet.confirm(uid,data.get('telegram_id'))
                    return self.reply(200,{'linked':True})
                except ValueError as exc:raise Problem(str(exc))
            if path=='/api/payments/create':
                data=self.json_body()
                self.app.throttle(('checkout',uid),5)
                try:return self.reply(200,self.app.payments.create(uid,data.get('package'),data.get('region'),self.server.origin))
                except ValueError as exc:raise Problem(str(exc))
            if path=='/api/payments/check':
                data=self.json_body()
                self.app.throttle(('payment-check',uid),6)
                try:return self.reply(200,self.app.payments.check(uid,data.get('order')))
                except ValueError as exc:raise Problem(str(exc))
            if path=='/api/logout':
                with self.app.store.transaction():self.app.store.db.execute('DELETE FROM web_sessions WHERE token=?',(session['token'],))
                return self.reply(200,{},cookie='studio=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
            if path=='/api/support/operator':
                if uid not in self.app.config.admins:raise Problem('Войдите под аккаунтом владельца.',403)
                data=self.json_body()
                if type(data.get('ticket')) is not int:raise Problem('Выберите обращение.')
                try:
                    result=self.app.support.join(data['ticket']) if data.get('join') else self.app.support.reply(data['ticket'],data.get('text'))
                    return self.reply(200,result)
                except ValueError as exc:raise Problem(str(exc))
            if path=='/api/support':
                self.app.throttle(('support',uid),12)
                try:return self.reply(200,self.app.support.send(uid,self.json_body()))
                except ValueError as exc:raise Problem(str(exc))
            if path=='/api/event':
                data=self.json_body();self.app.dispatch(uid,data.get('action',''),data.get('text',''))
            elif path=='/api/recommend':
                return self.reply(200,self.app.recommend(self.json_body()))
            elif path=='/api/start-recommended':
                self.app.start_recommended(uid,self.json_body())
            elif path=='/api/language':
                data=self.json_body();language=data.get('language')
                if language not in ('ru','uk','en'):raise Problem('Язык не поддерживается.')
                with self.app.store.transaction():
                    if data.get('automatic') is True:
                        self.app.store.db.execute('UPDATE users SET lang=? WHERE id=? AND manual=0',(language,uid))
                    else:
                        self.app.store.db.execute('UPDATE users SET lang=?,manual=1 WHERE id=?',(language,uid))
                    user=self.app.store.user(uid)
                    project=self.app.store.project(user['current'],uid) if user['current'] else None
                    if project:self.app.engine.show(project)
            elif path=='/api/card':
                self.app.edit_card(uid,self.json_body())
            elif path=='/api/upload':
                with self.app.store.lock:
                    u=self.app.store.user(uid);p=self.app.store.project(u['current'],uid) if u['current'] else None
                if not p or p['state'] not in UPLOAD_STATES:raise Problem('Сначала перейдите к загрузке референсов или визуала.')
                ctype=self.headers.get('Content-Type','')
                if not ctype.startswith('multipart/form-data;'):raise Problem('Нужен файл изображения или видео.')
                raw=self.read_body(20*1024*1024+65536)
                msg=BytesParser(policy=default).parsebytes(('Content-Type: '+ctype+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+raw)
                parts=[part for part in msg.iter_parts() if part.get_param('name',header='content-disposition')=='file']
                if len(parts)!=1:raise Problem('Загрузите один файл за раз.')
                part=parts[0];data=part.get_payload(decode=True)
                if not data or len(data)>20*1024*1024:raise Problem('Максимальный размер файла — 20 МБ.',413)
                mime=part.get_content_type();kind='photo' if mime in ('image/png','image/jpeg','image/webp') else 'video' if mime in ('video/mp4','video/quicktime','video/webm') else None
                if not kind:raise Problem('Поддерживаются JPG, PNG, WebP, MP4, MOV и WebM.')
                directory=self.app.root/'media'/str(uid)/'uploads';directory.mkdir(parents=True,exist_ok=True)
                file=directory/(secrets.token_hex(16)+('.png' if kind=='photo' else '.mp4'))
                try:
                    file.write_bytes(data);seconds=0
                    if kind=='photo':
                        with Image.open(file) as im:
                            if im.width*im.height>20000000:raise Problem('Изображение слишком большое.')
                            image=ImageOps.exif_transpose(im).convert('RGB');image.thumbnail((2048,2048));image.save(file,'PNG')
                    else:
                        seconds=media.info(file)['seconds']
                        if not 0<seconds<=60:raise Problem('Загрузите видео длительностью до 60 секунд.')
                    self.app.dispatch(uid,text='',upload={'path':str(file),'kind':kind,'seconds':seconds})
                except Exception:
                    file.unlink(missing_ok=True);raise
            else:raise Problem('Действие не найдено.',404)
            self.reply(200,self.app.snapshot(uid,session['csrf']))
        except Problem as e:self.reply(e.status,{'error':e.message})
        except (ValueError,KeyError,TypeError):self.reply(400,{'error':'Не удалось обработать запрос. Проверьте данные.'})
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception:self.reply(500,{'error':'Не удалось выполнить действие. Проект сохранён. Обновите страницу.'})

    def json_body(self):
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise Problem('Нужен JSON запрос.')
        data=json.loads(self.read_body(65536))
        if not isinstance(data,dict):raise Problem('Некорректный запрос.')
        return data


def serve(root, port=8090, config=None, *, host='127.0.0.1', origin=None, trusted_proxies=()):
    if origin and (urlsplit(origin).scheme!='https' or not urlsplit(origin).netloc or urlsplit(origin).path not in ('','/')):
        raise ValueError('Public origin must be an HTTPS site URL')
    if origin and config and config.mode=='live':
        import os
        os.environ['MEDIA_PUBLIC_BASE_URL']=origin.rstrip('/')
    app=Studio(root,config);app.store.recover()
    server=ThreadingHTTPServer((host,port),Handler)
    server.app=app;server.origin=origin.rstrip('/') if origin else f'http://127.0.0.1:{server.server_port}'
    server.trusted_proxies=set(trusted_proxies)
    threading.Thread(target=app.worker_loop,daemon=True).start()
    threading.Thread(target=app.support.loop,args=(app.stop,),daemon=True).start()
    threading.Thread(target=app.payments.loop,args=(app.stop,),daemon=True).start()
    print(f'Browser {app.config.mode}: {server.origin}',flush=True)
    try:server.serve_forever()
    finally:app.stop.set();server.server_close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8090)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent/'runtime/browser')
    args=parser.parse_args();serve(args.root,args.port)
