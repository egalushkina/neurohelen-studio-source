"""Hosted Stripe Checkout, verified webhooks and atomic, once-only fulfillment."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlsplit
import httpx


def configured(c):
    return (c.stripe_secret_key.startswith(('sk_live_','sk_test_','rk_live_','rk_test_'))
            and c.stripe_webhook_secret.startswith('whsec_'))


def checkout_url(s,c,uid,oid):
    if not configured(c): raise ValueError('stripe_not_configured')
    with s.lock:
        row=s.db.execute('SELECT * FROM payment_orders WHERE id=? AND user_id=?',(oid,uid)).fetchone()
        if not row or row['provider']!='stripe_checkout' or row['status']!='pending': return None
        r=dict(row)
    if r['created']<time.time()-1800: raise ValueError('invoice_expired')
    if r['stripe_live']!=int('_live_' in c.stripe_secret_key): raise ValueError('stripe_mode_changed')
    if not r['stripe_live'] and uid not in c.admins: raise ValueError('stripe_test_only')
    if r['stripe_url']: return r['stripe_url']
    data={'mode':'payment','payment_method_types[0]':'card',
          'client_reference_id':oid,'metadata[order_id]':oid,
          'line_items[0][price_data][currency]':r['currency'].lower(),
          'line_items[0][price_data][unit_amount]':str(r['amount']),
          'line_items[0][price_data][product_data][name]':f"{r['tokens']} tokens — Cartoon Studio",
          'line_items[0][quantity]':'1','success_url':'https://t.me/NeuroHelen_bot',
          'cancel_url':'https://t.me/NeuroHelen_bot'}
    # Never vary the request for this order: timeouts/retries return the same session.
    response=httpx.post('https://api.stripe.com/v1/checkout/sessions',data=data,
                        headers={'Authorization':'Bearer '+c.stripe_secret_key,
                                 'Idempotency-Key':'cartoon-order-'+oid,
                                 'Stripe-Version':'2024-06-20'},timeout=20)
    if response.status_code>=400: raise RuntimeError('stripe_checkout_unavailable')
    obj=response.json();url=obj.get('url','');sid=obj.get('id','')
    if (not sid.startswith('cs_') or urlsplit(url).scheme!='https' or
            urlsplit(url).hostname!='checkout.stripe.com' or obj.get('livemode')!=bool(r['stripe_live'])):
        raise ValueError('stripe_invalid_session')
    with s.transaction():
        s.db.execute('UPDATE payment_orders SET stripe_session=?,stripe_url=? WHERE id=?',(sid,url,oid))
    return url


def verify(raw,header,secret,now=None):
    if not secret.startswith('whsec_'): raise ValueError('stripe_not_configured')
    fields=[part.split('=',1) for part in header.split(',') if '=' in part]
    try: timestamp=next(v for k,v in fields if k=='t');stamp=int(timestamp)
    except (StopIteration,ValueError): raise ValueError('invalid_signature') from None
    if abs((time.time() if now is None else now)-stamp)>300: raise ValueError('expired_signature')
    expected=hmac.new(secret.encode(),timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected,v) for k,v in fields if k=='v1'):
        raise ValueError('invalid_signature')
    return json.loads(raw)


def receive(s,c,raw,header):
    if not c.stripe_webhook_secret: return 503
    try: event=verify(raw,header,c.stripe_webhook_secret)
    except (ValueError,UnicodeError): return 400
    if not isinstance(event,dict): return 400
    if event.get('type') not in ('checkout.session.completed','checkout.session.async_payment_succeeded'):
        return 200
    obj=event.get('data',{}).get('object',{})
    if obj.get('payment_status')!='paid': return 200
    oid=obj.get('client_reference_id')
    with s.transaction():
        r=s.db.execute('SELECT * FROM payment_orders WHERE id=?',(oid,)).fetchone()
        # Other applications can share the Stripe account: ignore their events.
        if not r or r['provider']!='stripe_checkout': return 200
        if not r['stripe_session']: return 503  # Retry after session persistence.
        if (obj.get('id')!=r['stripe_session'] or obj.get('metadata',{}).get('order_id')!=oid
                or obj.get('currency')!=r['currency'].lower() or obj.get('amount_total')!=r['amount']
                or obj.get('mode')!='payment' or obj.get('status')!='complete'
                or obj.get('livemode')!=bool(r['stripe_live']) or event.get('livemode')!=bool(r['stripe_live'])):
            return 400
        intent=obj.get('payment_intent')
        if not isinstance(intent,str) or not intent.startswith('pi_'): return 400
        if r['status']=='paid': return 200
        if s.db.execute('SELECT 1 FROM payment_orders WHERE provider_charge=?',('stripe:'+intent,)).fetchone(): return 400
        if s.wallet:s.wallet.account(r['user_id'])
        count=s.db.execute("INSERT OR IGNORE INTO ledger(id,user_id,amount,kind,created) VALUES (?,?,?,'purchase',?)",
                           ('stripe:'+r['stripe_session'],r['user_id'],r['tokens'],time.time())).rowcount
        if count:
            s.db.execute("UPDATE payment_orders SET status='paid',provider_charge=? WHERE id=?",('stripe:'+intent,oid))
            s.credit(r['user_id'],r['tokens'],'stripe:'+r['stripe_session'])
            s.emit(r['user_id'],f"Оплата получена. Начислено {r['tokens']} токенов.",[[('Баланс','balance')]])
    return 200
