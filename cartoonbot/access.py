"""Paid access excludes promotional/test credit; owners remain exempt."""
def requires_purchase(config, uid):
    return config.require_purchase and uid not in config.admins


def paid_available(store, uid, own_reserve=0):
    user=store.user(uid)
    paid=store.db.execute("SELECT COALESCE(sum(amount),0) FROM ledger WHERE user_id=? AND kind IN ('purchase','generation','refund')",(uid,)).fetchone()[0]
    if store.wallet:paid=store.wallet.read(uid)['paid']
    return max(0,min(user['balance'],paid)-user['reserved']+own_reserve)


def allowed(store, config, uid, required=1, own_reserve=0):
    return not requires_purchase(config,uid) or paid_available(store,uid,own_reserve)>=max(1,required)
