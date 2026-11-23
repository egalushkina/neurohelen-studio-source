"""Firebase authentication; keeps studio data under existing internal user IDs."""
import hashlib,hmac,json,secrets,time
from pathlib import Path
import httpx
from google.oauth2 import id_token
from google.auth.transport.requests import Request

class FirebaseLogin:
 def __init__(self,app):
  self.app=app;self.s=app.store
  path=Path(app.root)/'firebase-config.json'
  self.config=json.loads(path.read_text()) if path.exists() else {}
  self.public={k:self.config[k] for k in ('apiKey','authDomain','projectId','appId') if k in self.config}
  self.enabled=all(self.public.get(k) for k in ('apiKey','authDomain','projectId','appId'))
  self.s.db.execute('CREATE TABLE IF NOT EXISTS web_firebase_accounts(sub TEXT PRIMARY KEY,uid INTEGER UNIQUE REFERENCES web_accounts(uid))')
 def verify(self,token):
  def request(*args,**kwargs):
   kwargs['timeout']=15
   return Request()(*args,**kwargs)
  claims=id_token.verify_firebase_token(token,request,self.public['projectId'])
  if claims.get('iss')!='https://securetoken.google.com/'+self.public['projectId']:raise ValueError('issuer')
  # Check the current account as well, so disabled/revoked credentials cannot start a session.
  response=httpx.post('https://identitytoolkit.googleapis.com/v1/accounts:lookup',params={'key':self.public['apiKey']},json={'idToken':token},timeout=15)
  response.raise_for_status();users=response.json().get('users',[])
  if len(users)!=1 or users[0].get('localId')!=claims.get('sub') or users[0].get('disabled'):raise ValueError('account')
  return claims
 def finish(self,token,password=''):
  if not self.enabled or not isinstance(token,str) or not 1<=len(token)<=16000:raise ValueError('Не удалось подтвердить вход.')
  try:claims=self.verify(token)
  except Exception:raise ValueError('Не удалось подтвердить вход. Войдите ещё раз.') from None
  sub=claims.get('sub');email=claims.get('email','').strip().lower()
  if not isinstance(sub,str) or not 1<=len(sub)<=128 or '@' not in email or len(email)>254:raise ValueError('Не удалось подтвердить аккаунт.')
  if claims.get('email_verified') is not True:raise ValueError('Подтвердите email по ссылке в письме, затем войдите.')
  if not isinstance(claims.get('auth_time'),(int,float)) or not 0<=time.time()-claims['auth_time']<600:raise ValueError('Для входа подтвердите аккаунт ещё раз.')
  with self.s.lock:
   linked=self.s.db.execute('SELECT uid FROM web_firebase_accounts WHERE sub=?',(sub,)).fetchone()
   old=self.s.db.execute('SELECT * FROM web_accounts WHERE email=?',(email,)).fetchone()
  if linked:return self.app.create_session(linked['uid'])
  owner_migration=bool(old and old['uid'] in self.app.config.admins and email in self.config.get('owner_migration_emails',[]) and claims.get('firebase',{}).get('sign_in_provider')=='google.com')
  if old and not owner_migration and (not isinstance(password,str) or not 10<=len(password)<=256 or not hmac.compare_digest(self.app.digest(password,old['salt']),old['password'])):
   raise ValueError('Для переноса прежнего кабинета введите его пароль и повторите вход. Проекты и баланс сохранятся.')
  if not old and self.app.config.mode=='live' and not self.app.config.public_access:raise ValueError('Регистрация пока закрыта.')
  salt=secrets.token_hex(16);hashed=self.app.digest(secrets.token_urlsafe(48),salt)
  with self.s.transaction():
   linked=self.s.db.execute('SELECT uid FROM web_firebase_accounts WHERE sub=?',(sub,)).fetchone()
   if linked:uid=linked['uid']
   elif old:
    if self.s.db.execute('SELECT 1 FROM web_firebase_accounts WHERE uid=?',(old['uid'],)).fetchone():raise ValueError('Кабинет уже связан с другим аккаунтом.')
    uid=old['uid'];self.s.db.execute('INSERT INTO web_firebase_accounts VALUES(?,?)',(sub,uid))
    self.s.db.execute('UPDATE web_accounts SET salt=?,password=? WHERE uid=?',(salt,hashed,uid))
    self.s.db.execute('DELETE FROM web_sessions WHERE uid=?',(uid,))
   else:
    if self.s.db.execute('SELECT 1 FROM web_accounts WHERE email=?',(email,)).fetchone():raise ValueError('Повторите вход.')
    uid=-(secrets.randbelow(2**50)+1);self.s.user(uid,'ru')
    self.s.db.execute('INSERT INTO web_accounts VALUES(?,?,?,?)',(uid,email,salt,hashed))
    self.s.db.execute('INSERT INTO web_firebase_accounts VALUES(?,?)',(sub,uid))
  return self.app.create_session(uid)
