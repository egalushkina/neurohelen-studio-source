"""Google Identity Services: verified identities, browser-bound one-use challenges."""
import hashlib,hmac,json,secrets,time
from pathlib import Path
from google.oauth2 import id_token
from google.auth.transport.requests import Request

class GoogleLogin:
    def __init__(self, app):
        self.app=app;self.s=app.store
        path=Path(app.root)/'google-login.json'
        self.client_id=json.loads(path.read_text()).get('client_id','') if path.exists() else ''
        self.s.db.executescript("""
          CREATE TABLE IF NOT EXISTS web_google_accounts(sub TEXT PRIMARY KEY,uid INTEGER UNIQUE REFERENCES web_accounts(uid));
          CREATE TABLE IF NOT EXISTS web_google_challenges(id TEXT PRIMARY KEY,nonce TEXT,expires REAL);
        """)
    def begin(self):
        if not self.client_id:raise ValueError('Вход через Google пока подключается. Используйте email и пароль.')
        browser=secrets.token_urlsafe(32);nonce=secrets.token_urlsafe(32)
        with self.s.transaction():
            self.s.db.execute('DELETE FROM web_google_challenges WHERE expires<?',(time.time(),))
            self.s.db.execute('INSERT INTO web_google_challenges VALUES(?,?,?)',(hashlib.sha256(browser.encode()).hexdigest(),nonce,time.time()+600))
        return browser,nonce
    def verify(self, credential):
        def request(*args,**kwargs):
            kwargs['timeout']=15
            return Request()(*args,**kwargs)
        return id_token.verify_oauth2_token(credential,request,self.client_id)
    def finish(self,browser,credential,password=''):
        if not self.client_id or not isinstance(credential,str) or not 1<=len(credential)<=16000:
            raise ValueError('Не удалось подтвердить вход через Google.')
        with self.s.transaction():
            key=hashlib.sha256(browser.encode()).hexdigest()
            challenge=self.s.db.execute('SELECT * FROM web_google_challenges WHERE id=? AND expires>?',(key,time.time())).fetchone()
            self.s.db.execute('DELETE FROM web_google_challenges WHERE id=?',(key,))
        if not challenge:raise ValueError('Обновите страницу и повторите вход через Google.')
        try:claims=self.verify(credential)
        except Exception:raise ValueError('Google не подтвердил вход. Повторите попытку.') from None
        if not isinstance(claims.get('nonce'),str) or not hmac.compare_digest(claims['nonce'],challenge['nonce']):
            raise ValueError('Не удалось подтвердить запрос входа.')
        sub=claims.get('sub');email=claims.get('email','').strip().lower()
        if not isinstance(sub,str) or not sub or claims.get('email_verified') is not True or '@' not in email or len(email)>254:
            raise ValueError('Для входа нужна подтверждённая почта Google.')
        with self.s.lock:
            linked=self.s.db.execute('SELECT uid FROM web_google_accounts WHERE sub=?',(sub,)).fetchone()
            old=self.s.db.execute('SELECT * FROM web_accounts WHERE email=?',(email,)).fetchone()
        if linked:return self.app.create_session(linked['uid'])
        # Never merge an unverified password account merely because its email matches.
        if old and (not isinstance(password,str) or not 10<=len(password)<=256 or not hmac.compare_digest(self.app.digest(password,old['salt']),old['password'])):
            raise ValueError('Этот email уже зарегистрирован. Введите пароль вашего кабинета и снова нажмите кнопку Google, чтобы связать вход.')
        if not old and self.app.config.mode=='live' and not self.app.config.public_access:
            raise ValueError('Регистрация пока закрыта.')
        salt=secrets.token_hex(16);hashed=self.app.digest(secrets.token_urlsafe(48),salt)
        with self.s.transaction():
            linked=self.s.db.execute('SELECT uid FROM web_google_accounts WHERE sub=?',(sub,)).fetchone()
            if linked:uid=linked['uid']
            elif old:
                current=self.s.db.execute('SELECT * FROM web_accounts WHERE uid=?',(old['uid'],)).fetchone()
                if not current or current['password']!=old['password']:raise ValueError('Повторите вход.')
                if self.s.db.execute('SELECT 1 FROM web_google_accounts WHERE uid=?',(old['uid'],)).fetchone():raise ValueError('К кабинету уже подключён другой Google аккаунт.')
                uid=old['uid'];self.s.db.execute('INSERT INTO web_google_accounts VALUES(?,?)',(sub,uid))
            else:
                if self.s.db.execute('SELECT 1 FROM web_accounts WHERE email=?',(email,)).fetchone():raise ValueError('Повторите вход и подтвердите пароль кабинета.')
                uid=-(secrets.randbelow(2**50)+1);self.s.user(uid,'ru')
                self.s.db.execute('INSERT INTO web_accounts VALUES(?,?,?,?)',(uid,email,salt,hashed))
                self.s.db.execute('INSERT INTO web_google_accounts VALUES(?,?)',(sub,uid))
        return self.app.create_session(uid)
