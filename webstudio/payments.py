"""Hosted, one-time package purchases; fulfill only from authenticated provider reads.
Polling works locally without exposing a development server to the internet.
"""
import json
import time
import uuid
from decimal import Decimal
from urllib.parse import urlsplit
import httpx
from cartoonbot.catalog import PACKAGES, package_usd


class Payments:
    def __init__(self, store, root, admins):
        self.s=store;self.admins=admins
        path=root/'payment-config.json'
        self.config=json.loads(path.read_text()) if path.exists() else {}
        yookassa=root/'yookassa-config.json'
        if yookassa.exists():
            try:values=json.loads(yookassa.read_text())
            except (ValueError,OSError):values={}
            if not isinstance(values,dict):values={}
            self.config.update({k:values[k] for k in ('yookassa_shop_id','yookassa_key') if values.get(k)})
        self.s.db.executescript('''CREATE TABLE IF NOT EXISTS web_orders(
          id TEXT PRIMARY KEY,uid INTEGER,package TEXT,tokens INTEGER,amount INTEGER,
          currency TEXT,provider TEXT,remote TEXT UNIQUE,url TEXT,status TEXT,
          created REAL,checked REAL DEFAULT 0,live INTEGER,return_url TEXT);
        ''')

    def ready(self, provider):
        c=self.config
        if provider=='stripe':return c.get('stripe_key','').startswith(('sk_live_','rk_live_','sk_test_','rk_test_'))
        return bool(c.get('yookassa_shop_id') and c.get('yookassa_key') and c.get('receipts')=='external')

    def request(self, provider, method, suffix, **kwargs):
        if provider=='stripe':
            url='https://api.stripe.com/v1/checkout/sessions'+suffix
            kwargs['auth']=(self.config['stripe_key'],'')
            kwargs.setdefault('headers',{})['Stripe-Version']='2024-06-20'
        else:
            url='https://api.yookassa.ru/v3/payments'+suffix
            kwargs['auth']=(self.config['yookassa_shop_id'],self.config['yookassa_key'])
        response=httpx.request(method,url,timeout=20,**kwargs)
        if response.status_code>=400:raise ValueError('Платёжный сервис временно недоступен. Попробуйте позже.')
        return response.json()

    def create(self, uid, key, region, origin):
        package=next((p for p in PACKAGES if p[0]==key),None)
        if not package or region not in ('ru','international'):raise ValueError('Выберите пакет и страну.')
        provider='yookassa' if region=='ru' else 'stripe'
        if not self.ready(provider):raise ValueError('Этот способ оплаты ещё подключается. Попробуйте позже.')
        live=('_live_' in self.config['stripe_key']) if provider=='stripe' else not self.config.get('yookassa_test',False)
        if not live and uid not in self.admins:raise ValueError('Оплата ещё тестируется. Попробуйте позже.')
        amount=package[3]*100 if region=='ru' else int(Decimal(package_usd(key,package[3]))*100)
        currency='RUB' if region=='ru' else 'USD'
        with self.s.transaction():
            row=self.s.db.execute("SELECT * FROM web_orders WHERE uid=? AND package=? AND provider=? AND amount=? AND live=? AND status='pending' AND created>? AND return_url LIKE ? ORDER BY created DESC LIMIT 1",(uid,key,provider,amount,int(live),time.time()-1800,origin+'/?payment=%')).fetchone()
            if not row:
                oid=uuid.uuid4().hex
                self.s.db.execute('INSERT INTO web_orders(id,uid,package,tokens,amount,currency,provider,status,created,live,return_url) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(oid,uid,key,package[2],amount,currency,provider,'pending',time.time(),int(live),origin+'/?payment='+oid))
                row=self.s.db.execute('SELECT * FROM web_orders WHERE id=?',(oid,)).fetchone()
            r=dict(row)
        if r['url']:return {'url':r['url'],'order':r['id']}
        title=f"NeuroHelen: {r['tokens']} токенов"
        if provider=='stripe':
            obj=self.request(provider,'POST','',headers={'Idempotency-Key':'studio-'+r['id']},data={
                'mode':'payment','adaptive_pricing[enabled]':'false','client_reference_id':r['id'],'metadata[studio_order]':r['id'],
                'line_items[0][price_data][currency]':currency.lower(),
                'line_items[0][price_data][unit_amount]':str(amount),
                'line_items[0][price_data][product_data][name]':title,'line_items[0][quantity]':'1',
                'success_url':r['return_url'],'cancel_url':r['return_url']})
            url=obj.get('url','');valid=obj.get('id','').startswith('cs_') and obj.get('livemode')==bool(r['live'])
            hosts={'checkout.stripe.com'}
        else:
            obj=self.request(provider,'POST','',headers={'Idempotence-Key':r['id']},json={
                'amount':{'value':f'{amount/100:.2f}','currency':currency},'capture':True,
                'confirmation':{'type':'redirect','return_url':r['return_url']},
                'description':title,'metadata':{'studio_order':r['id']}})
            url=obj.get('confirmation',{}).get('confirmation_url','');valid=bool(obj.get('id')) and obj.get('test')==not_live(r)
            hosts={'yookassa.ru','yoomoney.ru'}
        parsed=urlsplit(url)
        if not valid or parsed.scheme!='https' or parsed.hostname not in hosts:raise ValueError('Не удалось создать платёж. Обратитесь в поддержку.')
        with self.s.transaction():self.s.db.execute('UPDATE web_orders SET remote=?,url=? WHERE id=?',(obj['id'],url,r['id']))
        return {'url':url,'order':r['id']}

    def check(self, uid, oid):
        with self.s.lock:
            row=self.s.db.execute('SELECT * FROM web_orders WHERE id=? AND uid=?',(oid,uid)).fetchone()
            if not row:raise ValueError('Платёж не найден.')
            r=dict(row)
        if r['status']!='pending' or not r['remote']:return {'status':r['status']}
        obj=self.request(r['provider'],'GET','/'+r['remote'])
        if obj.get('id')!=r['remote']:raise ValueError('Платёж требует проверки.')
        if r['provider']=='stripe':
            paid=obj.get('payment_status')=='paid' and obj.get('status')=='complete'
            valid=(obj.get('amount_total')==r['amount'] and obj.get('currency')==r['currency'].lower()
                   and obj.get('livemode')==bool(r['live']) and obj.get('mode')=='payment'
                   and obj.get('client_reference_id')==oid and obj.get('metadata',{}).get('studio_order')==oid)
            ended=obj.get('status')=='expired'
        else:
            paid=obj.get('status')=='succeeded' and obj.get('paid') is True
            value=Decimal(obj.get('amount',{}).get('value','-1'))*100
            valid=(value==r['amount'] and obj.get('amount',{}).get('currency')==r['currency']
                   and obj.get('test')==not_live(r) and obj.get('metadata',{}).get('studio_order')==oid)
            ended=obj.get('status')=='canceled'
        if paid and not valid:raise ValueError('Платёж требует проверки. Напишите в поддержку.')
        with self.s.transaction():
            self.s.db.execute('UPDATE web_orders SET checked=? WHERE id=?',(time.time(),oid))
            if paid:
                if self.s.wallet:self.s.wallet.account(uid)
                inserted=self.s.db.execute("INSERT OR IGNORE INTO ledger(id,user_id,amount,kind,created) VALUES (?,?,?,'purchase',?)",('web:'+r['provider']+':'+r['remote'],uid,r['tokens'],time.time())).rowcount
                if inserted:self.s.credit(uid,r['tokens'],'web:'+r['provider']+':'+r['remote'])
                self.s.db.execute("UPDATE web_orders SET status='paid' WHERE id=?",(oid,))
            elif ended:self.s.db.execute("UPDATE web_orders SET status='canceled' WHERE id=?",(oid,))
        return {'status':'paid' if paid else 'canceled' if ended else 'pending'}

    def loop(self, stop):
        while not stop.wait(30):
            with self.s.lock:
                rows=self.s.db.execute("SELECT id,uid FROM web_orders WHERE status='pending' AND remote IS NOT NULL AND checked<? ORDER BY checked LIMIT 10",(time.time()-60,)).fetchall()
            for row in rows:
                if stop.is_set():return
                try:self.check(row['uid'],row['id'])
                except Exception:
                    with self.s.transaction():self.s.db.execute('UPDATE web_orders SET checked=? WHERE id=?',(time.time(),row['id']))


def not_live(order):return not bool(order['live'])
