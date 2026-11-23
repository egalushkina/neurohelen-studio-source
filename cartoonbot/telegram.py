import json
import time
import uuid
from pathlib import Path
import httpx
from PIL import Image, ImageOps
from . import media
from .i18n import tr, locale


class TelegramError(RuntimeError):
    def __init__(self,status,retry_after=5):
        self.status,self.retry_after=status,retry_after
        super().__init__('telegram_'+str(status))


class Telegram:
    def __init__(self, config, store, engine):
        self.c,self.s,self.engine=config,store,engine
        self.client=httpx.Client(timeout=httpx.Timeout(20,connect=8))
        self.base='https://api.telegram.org/bot'+config.token+'/'

    def call(self,method,data=None,files=None):
        r=self.client.post(self.base+method,data=data,files=files,timeout=3 if method in ('answerCallbackQuery','answerPreCheckoutQuery') else (120 if files else 20))
        result=r.json()
        if r.status_code>=400 or not result.get('ok'):
            raise TelegramError(result.get('error_code',r.status_code),result.get('parameters',{}).get('retry_after',5))
        return result['result']

    def download_media(self,message,uid,reference_video=False):
        if 'photo' in message:
            item=message['photo'][-1]; kind='photo'
        elif 'video' in message:
            item=message['video']; kind='video'
        elif 'document' in message:
            item=message['document']; mime=item.get('mime_type','')
            if mime not in ('video/mp4','image/jpeg','image/png','image/webp'): raise ValueError('media')
            kind='video' if mime=='video/mp4' else 'photo'
        else: return None
        if kind=='video' and reference_video and item.get('duration',0)>30:
            raise ValueError('video_reference_too_long')
        if item.get('file_size',0)>20*1024*1024: raise ValueError('size')
        file=self.call('getFile',{'file_id':item['file_id']})
        # Telegram supplies this path; never construct a local path from it.
        remote=file['file_path']
        if '..' in remote or remote.startswith('/') or ':' in remote: raise ValueError('path')
        directory=self.c.root/'media'/str(uid)/'uploads'; directory.mkdir(parents=True,exist_ok=True)
        path=directory/(uuid.uuid4().hex+('.mp4' if kind=='video' else '.png'))
        total=0
        try:
            with self.client.stream('GET','https://api.telegram.org/file/bot'+self.c.token+'/'+remote) as r:
                r.raise_for_status()
                with path.open('wb') as f:
                    for chunk in r.iter_bytes():
                        total+=len(chunk)
                        if total>20*1024*1024: raise ValueError('size')
                        f.write(chunk)
            seconds=0
            if kind=='photo':
                with Image.open(path) as image:
                    if image.width*image.height>20_000_000: raise ValueError('dimensions')
                    image=ImageOps.exif_transpose(image).convert('RGB')
                    image.thumbnail((2048,2048)); image.save(path,'PNG')
            else:
                details=media.info(path); seconds=details['seconds']
                if reference_video and seconds>30: raise ValueError('video_reference_too_long')
                if seconds>self.c.max_seconds+.2: raise ValueError('duration')
            return {'path':str(path.resolve()),'kind':kind,'seconds':seconds}
        except Exception:
            path.unlink(missing_ok=True); raise

    def event(self,update):
        if update.get('pre_checkout_query'):
            from .payments import approve_checkout
            q=update['pre_checkout_query']
            with self.s.transaction(): error=approve_checkout(self.s,self.c,q['from']['id'],q)
            self.call('answerPreCheckoutQuery',{'pre_checkout_query_id':q['id'],'ok':'false' if error else 'true',**({'error_message':error} if error else {})})
            return None
        callback=update.get('callback_query')
        message=callback.get('message',{}) if callback else update.get('message',{})
        user=callback['from'] if callback else message.get('from')
        if not user: return None
        uid=user['id']
        if callback:
            try: self.call('answerCallbackQuery',{'callback_query_id':callback['id']})
            except Exception: pass
        if message.get('chat',{}).get('type')!='private': return None
        result={'id':update['update_id'],'user_id':uid,'language':user.get('language_code'),
                'display_name':' '.join(x for x in (user.get('first_name'),user.get('last_name')) if x),
                'username':user.get('username',''),
                'text':message.get('text',message.get('caption','')) if not callback else '',
                'action':callback.get('data','') if callback else ''}
        if message.get('successful_payment'):
            result['payment']=message['successful_payment']
            return result
        with self.s.lock:
            if self.s.db.execute('SELECT 1 FROM updates WHERE id=?',(update['update_id'],)).fetchone(): return None
            if self.c.mode in ('live','codex') and not self.c.public_access and uid not in self.c.admins: return result
            row=self.s.user(uid,user.get('language_code'))
            p=self.s.project(row['current'],uid) if row['current'] else None
        if not callback and p and p['state'] in ('upload','seedance_source_upload','omni_source_upload','references','photo_refs','photo_prompt','scene_upload','repair_visual','style_reference_upload'):
            try:
                uploaded=self.download_media(message,uid,reference_video=p['state'] in ('references','seedance_source_upload'))
                if uploaded: result['media']=uploaded
            except Exception as exc:
                with self.s.transaction():
                    error='video_reference_too_long' if isinstance(exc,ValueError) and str(exc)=='video_reference_too_long' else 'media_error'
                    self.s.emit(uid,tr(row['lang'],error))
                    self.s.db.execute('INSERT OR IGNORE INTO updates VALUES (?)',(update['update_id'],))
                return None
        return result

    def deliver(self):
        with self.s.lock:
            row=self.s.db.execute('SELECT * FROM outbox WHERE delivered=0 AND next_attempt<=? ORDER BY id LIMIT 1',(time.time(),)).fetchone()
        if not row: return False
        try:
            self.send_row(row)
        except Exception as exc:
            attempts=row['attempts']+1
            permanent=isinstance(exc,(ValueError,FileNotFoundError)) or (isinstance(exc,TelegramError) and exc.status in (400,403))
            delay=exc.retry_after if isinstance(exc,TelegramError) else min(300,2**attempts)
            with self.s.transaction():
                self.s.db.execute('UPDATE outbox SET delivered=?,attempts=?,next_attempt=?,error=? WHERE id=?',
                                  (-1 if permanent or attempts>=8 else 0,attempts,time.time()+delay,type(exc).__name__,row['id']))
            if permanent or attempts>=8:
                body=json.loads(row['body'])
                if body.get('invoice'):
                    with self.s.transaction():
                        self.s.emit(row['user_id'],'Не удалось открыть оплату. Попробуйте позже или напишите /paysupport.',[[('Купить токены','token_shop')]])
                if body.get('file'):
                    with self.s.transaction():
                        lang=self.s.user(row['user_id'])['lang']
                        self.s.emit(row['user_id'],tr(lang,'delivery_failed_notice'),
                                    [[(tr(lang,'resend_result'),'resend:'+str(row['id']))]])
            return True
        with self.s.transaction(): self.s.db.execute('UPDATE outbox SET delivered=1 WHERE id=?',(row['id'],))
        return True

    def send_row(self,row):
        body=json.loads(row['body']); uid=row['user_id']
        if body.get('invoice'):
            from .payments import invoice_data
            with self.s.lock: order=self.s.db.execute('SELECT * FROM payment_orders WHERE id=? AND user_id=?',(body['invoice'],uid)).fetchone()
            if order and order['provider']=='stripe_checkout':
                from .stripe_checkout import checkout_url
                url=checkout_url(self.s,self.c,uid,body['invoice'])
                if url:
                    self.call('sendMessage',{'chat_id':uid,'text':'Оплатите пакет на защищённой странице Stripe. После оплаты токены поступят в бот автоматически.','reply_markup':json.dumps({'inline_keyboard':[[{'text':f"Оплатить ${order['amount']/100:.2f}",'url':url}]]})})
                return
            with self.s.lock: data=invoice_data(self.s,self.c,uid,body['invoice'])
            if data: self.call('sendInvoice',data)
            return
        keyboard={'inline_keyboard':[[{'text':label,('url' if action.startswith('https://') else 'callback_data'):action} for label,action in group] for group in body['buttons']]}
        with self.s.lock: lang=self.s.user(uid)['lang']
        if body['buttons'] and not any(a=='home' for g in body['buttons'] for _,a in g):
            keyboard['inline_keyboard'].append([{'text':tr(lang,'main_menu'),'callback_data':'home'}])
        if body['file']:
            path=Path(body['file']).resolve()
            if not path.is_relative_to((self.c.root/'media').resolve()): raise ValueError('invalid_delivery_path')
            kind=body['kind']; method='sendPhoto' if kind=='photo' else 'sendVideo'
            field='photo' if kind=='photo' else 'video'
            # Never silently recompress the approved file to satisfy upload limits.
            if path.stat().st_size>49*1024*1024:
                from .reference_media import configured, signed_url
                if kind=='video' and configured():
                    self.call('sendMessage',{'chat_id':uid,'text':tr(lang,'large_video_download'),
                        'reply_markup':json.dumps({'inline_keyboard':[[{'text':tr(lang,'download'),'url':signed_url(self.c.root/'media',path)}]]})})
                    return
                raise ValueError('telegram_output_too_large')
            caption=body['text']
            if kind=='video' and path.suffix.lower()=='.mp4':
                hint=tr(lang,'video_save_hint')
                caption=caption[:1024-len(hint)-2].rstrip()+'\n\n'+hint if caption else hint
            with path.open('rb') as f:
                self.call(method,{'chat_id':uid,'caption':caption[:1024],
                                  **({'supports_streaming':'true'} if kind=='video' else {})},files={field:(path.name,f)})
        else:
            text=body['text'] or '…'
            chunks=[text[i:i+3500] for i in range(0,len(text),3500)]
            for i,chunk in enumerate(chunks):
                data={'chat_id':uid,'text':chunk}
                if i==len(chunks)-1 and body['buttons']: data['reply_markup']=json.dumps(keyboard,ensure_ascii=False)
                if not body['buttons'] or body.get('navigation'):
                    data['reply_markup']=json.dumps({'keyboard':[[{'text':tr(lang,'main_menu')}]],'resize_keyboard':True,'is_persistent':True},ensure_ascii=False)
                self.call('sendMessage',data)

    def poll_once(self):
        with self.s.lock:
            last=self.s.db.execute('SELECT max(id) FROM updates').fetchone()[0]
        updates=self.call('getUpdates',{'offset':last+1 if last is not None else 0,'timeout':5,
                                        'allowed_updates':json.dumps(['message','callback_query','pre_checkout_query'])})
        for update in updates:
            event=self.event(update)
            if event: self.engine.handle(event)
            else:
                with self.s.transaction(): self.s.db.execute('INSERT OR IGNORE INTO updates VALUES (?)',(update['update_id'],))
