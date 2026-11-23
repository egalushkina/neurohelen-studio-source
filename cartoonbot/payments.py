"""Telegram provider payments. Call mutations inside the Store transaction."""
import json
import time
import uuid
from .catalog import PACKAGES, package_usd
from decimal import Decimal

# Live payment checks are closed. Old buttons cannot create new check orders.
PAYMENT_CHECK_AMOUNTS = {}


def provider_token(c,provider):
    if provider=='yookassa': return c.payment_provider_token
    if provider=='stripe': return c.stripe_provider_token
    return ''


def ready(c,region='rf'):
    if region=='world':
        from .stripe_checkout import configured
        return configured(c) or bool(c.stripe_provider_token)
    return bool(region=='rf' and c.payment_provider_token and (c.payment_receipts=='external' or
                (c.payment_receipts=='provider' and c.payment_vat_code>0)))


def order_enabled(c,r,uid):
    provider=r['provider']
    region={'yookassa':'rf','stripe':'world'}.get(provider)
    return (ready(c,region) and r['currency']=={'yookassa':'RUB','stripe':'USD'}[provider]
            and (':TEST:' not in provider_token(c,provider) or uid in c.admins))


def create_invoice(s,c,uid,key,region='rf'):
    checking=key=='payment_check'
    if checking and region not in PAYMENT_CHECK_AMOUNTS:
        s.emit(uid,'Оплата недоступна.'); return
    package=(key,'',1,0) if checking else next((p for p in PACKAGES if p[0]==key),None)
    if not ready(c,region) or not package:
        s.emit(uid,'Оплата пока недоступна. Попробуйте позже.'); return
    from .stripe_checkout import configured
    provider=('stripe_checkout' if configured(c) else 'stripe') if region=='world' else 'yookassa'
    if provider=='stripe_checkout' and '_test_' in c.stripe_secret_key and uid not in c.admins:
        s.emit(uid,'Оплата пока недоступна.'); return
    currency='USD' if region=='world' else 'RUB'
    amount=int(Decimal(package_usd(key,package[3]))*100) if region=='world' else package[3]*100
    if checking: amount=PAYMENT_CHECK_AMOUNTS[region]
    if ':TEST:' in provider_token(c,provider) and uid not in c.admins:
        s.emit(uid,'Оплата пока недоступна.'); return
    # Reuse a recent unpaid order; repeated clicks cannot create separate credits.
    row=s.db.execute("SELECT id FROM payment_orders WHERE user_id=? AND package=? AND provider=? AND currency=? AND amount=? AND status='pending' AND created>? ORDER BY created DESC LIMIT 1",(uid,key,provider,currency,amount,time.time()-1800)).fetchone()
    oid=row['id'] if row else uuid.uuid4().hex
    if not row:
        s.db.execute('INSERT INTO payment_orders(id,user_id,package,tokens,amount,currency,created,provider) VALUES (?,?,?,?,?,?,?,?)',
                     (oid,uid,key,package[2],amount,currency,time.time(),provider))
    if provider=='stripe_checkout':
        s.db.execute('UPDATE payment_orders SET stripe_live=? WHERE id=? AND stripe_live IS NULL',(int('_live_' in c.stripe_secret_key),oid))
    s.db.execute('INSERT INTO outbox(user_id,body) VALUES (?,?)',(uid,json.dumps({'invoice':oid})))


def checkout_error(s,c,uid,p):
    row=s.db.execute('SELECT * FROM payment_orders WHERE id=? AND user_id=?',(p.get('invoice_payload'),uid)).fetchone()
    if row and not order_enabled(c,row,uid):
        return 'Оплата пока недоступна.'
    if not row or row['status']!='pending' or row['created']<time.time()-1800:
        return 'Счёт устарел или уже оплачен. Откройте «Купить токены».'
    if p.get('currency')!=row['currency'] or p.get('total_amount')!=row['amount']:
        return 'Стоимость не совпадает. Откройте новый счёт.'
    return None


def approve_checkout(s,c,uid,p):
    # Reserve one checkout per order, including across concurrent/repeated updates.
    row=s.db.execute('SELECT * FROM payment_orders WHERE id=? AND user_id=?',(p.get('invoice_payload'),uid)).fetchone()
    if row and order_enabled(c,row,uid) and row['status']=='checkout' and row['checkout_query']==p.get('id'):
        return None if p.get('currency')==row['currency'] and p.get('total_amount')==row['amount'] else 'Стоимость не совпадает.'
    error=checkout_error(s,c,uid,p)
    if error: return error
    s.db.execute("UPDATE payment_orders SET status='checkout',checkout_query=? WHERE id=?",(p['id'],row['id']))
    return None


def invoice_data(s,c,uid,oid):
    r=s.db.execute('SELECT * FROM payment_orders WHERE id=? AND user_id=?',(oid,uid)).fetchone()
    if not r or r['status']!='pending' or not order_enabled(c,r,uid): return None
    label=f"Пакет {r['tokens']} токенов"
    data={'chat_id':uid,'title':label,'description':'Разовая покупка токенов для генерации. Начисление после успешной оплаты.',
          'payload':oid,'provider_token':provider_token(c,r['provider']),'currency':r['currency'],
          'prices':json.dumps([{'label':label,'amount':r['amount']}]),'start_parameter':'order_'+oid}
    if r['provider']=='yookassa' and c.payment_receipts=='provider':
        data.update(need_email='true',send_email_to_provider='true',provider_data=json.dumps({'receipt':{'items':[
            {'description':label,'quantity':'1.00','amount':{'value':f"{r['amount']/100:.2f}",'currency':'RUB'},'vat_code':c.payment_vat_code}]}},ensure_ascii=False))
    return data


def complete(s,uid,p):
    r=s.db.execute('SELECT * FROM payment_orders WHERE id=? AND user_id=?',(p.get('invoice_payload'),uid)).fetchone()
    charge=p.get('telegram_payment_charge_id'); provider=p.get('provider_payment_charge_id')
    if not r or r['provider']=='stripe_checkout' or not charge or not provider or p.get('currency')!=r['currency'] or p.get('total_amount')!=r['amount']:
        s.emit(uid,'Платёж требует проверки. Напишите /paysupport.'); return
    if r['status']=='paid': return
    if s.db.execute('SELECT 1 FROM payment_orders WHERE telegram_charge=? OR provider_charge=?',(charge,provider)).fetchone(): return
    if s.wallet:s.wallet.account(uid)
    inserted=s.db.execute("INSERT OR IGNORE INTO ledger(id,user_id,amount,kind,created) VALUES (?,?,?,'purchase',?)",('payment:'+charge,uid,r['tokens'],time.time())).rowcount
    if not inserted: return
    s.db.execute("UPDATE payment_orders SET status='paid',telegram_charge=?,provider_charge=? WHERE id=?",(charge,provider,r['id']))
    s.credit(uid,r['tokens'],'payment:'+charge)
    s.emit(uid,f"Оплата получена. Начислено {r['tokens']} токенов.",[[('Баланс','balance')]])
