"""Owner-only, read-only reports over bot activity and the token ledger."""
import time
from datetime import datetime, timezone

KINDS={'script':'Сценарий','edit_script':'Правка сценария','visuals':'Визуалы',
       'edit_visuals':'Правка визуалов','video':'Видео','photo_image':'Фото',
       'analyze':'Разбор видео','analyze_style':'Разбор стиля','plan_edits':'Подготовка правок','recover':'Проверка результата'}
STATES={'succeeded':'готово','attention':'нужна проверка','queued':'в очереди','running':'в работе','recovered':'результат проверен','cancelled':'отменено'}
PERIODS={'day':86400,'week':7*86400,'month':30*86400,'all':None}
LABELS={'day':'24 часа','week':'7 дней','month':'30 дней','all':'всё время'}

def clean(value,limit=65):
    return ' '.join(str(value or '').split())[:limit]

def stamp(value):
    return datetime.fromtimestamp(value,timezone.utc).strftime('%d.%m %H:%M UTC') if value else 'нет данных'

def touch(store,event):
    store.db.execute('''INSERT INTO user_activity(user_id,display_name,username,last_seen) VALUES (?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
        display_name=CASE WHEN excluded.display_name!='' THEN excluded.display_name ELSE user_activity.display_name END,
        username=CASE WHEN ? THEN excluded.username ELSE user_activity.username END,last_seen=excluded.last_seen''',
        (event['user_id'],clean(event.get('display_name')),clean(event.get('username')),time.time(),'username' in event))

def money_note(config):
    from .payments import ready
    return ('Покупки: учитываются только записи о подтверждённых покупках. Тестовые выдачи не считаются покупками.\n'
            +('ЮKassa подключена. Покупки учитываются после подтверждения платежа.\n' if ready(config) else 'Приём оплаты ещё не подключён. Выручка пока не учитывается.\n')+
            ('Списание токенов включено.' if config.billing_enabled else 'Тестовый режим: списание токенов отключено.'))

def handle(engine,uid,action):
    # Check again here as well as at the router: never trust callback data.
    if uid not in engine.c.admins:
        engine.message(uid,'denied');return
    db=engine.s.db
    parts=action.split(':'); mode=parts[1] if len(parts)>1 else 'overview'
    back=[[('Статистика','stats:overview:all')],[('Главное меню','home')]]
    if mode=='overview':
        period=parts[2] if len(parts)>2 else 'all'
        if period not in PERIODS: return
        since=0 if PERIODS[period] is None else time.time()-PERIODS[period]
        total=db.execute('SELECT count(*) FROM users').fetchone()[0]
        admins=sum(db.execute('SELECT count(*) FROM users WHERE id=?',(x,)).fetchone()[0] for x in engine.c.admins)
        active=db.execute('''SELECT count(*) FROM (SELECT user_id FROM user_activity WHERE last_seen>=?
            UNION SELECT p.user_id FROM jobs j JOIN projects p ON p.id=j.project_id WHERE j.created>=?)''',(since,since)).fetchone()[0]
        projects=db.execute('SELECT count(*) FROM projects WHERE created>=?',(since,)).fetchone()[0]
        states=dict(db.execute('SELECT status,count(*) FROM jobs WHERE created>=? GROUP BY status',(since,)).fetchall())
        kinds=db.execute('SELECT kind,count(*) n FROM jobs WHERE created>=? GROUP BY kind ORDER BY n DESC',(since,)).fetchall()
        purchased=db.execute("SELECT coalesce(sum(amount),0) FROM ledger WHERE kind='purchase' AND amount>0 AND created>=?",(since,)).fetchone()[0]
        spent=db.execute("SELECT coalesce(-sum(amount),0) FROM ledger WHERE kind='generation' AND amount<0 AND created>=?",(since,)).fetchone()[0]
        text=(f'📊 Статистика: {LABELS[period]}\n\nПользователей всего: {total}\nИз них администраторов: {admins}\n'
              f'Активных за период: {active}\nНовых проектов: {projects}\n\nЗаданий: {sum(states.values())}\n'+
              '\n'.join(f'{STATES.get(k,k)}: {v}' for k,v in states.items())+
              '\n\nЧто запускали:\n'+'\n'.join(f'{KINDS.get(r[0],r[0])}: {r[1]}' for r in kinds)+
              f'\n\nКуплено токенов: {purchased}\nСписано токенов: {spent}\n\n'+money_note(engine.c)+
              '\n\nЗадание может включать несколько кадров. Статус «готово» не оценивает качество результата. '
              'Посещения меню учитываются с подключения статистики; ранняя активность известна по заданиям.')
        buttons=[[(LABELS[k],'stats:overview:'+k) for k in ('day','week')],[(LABELS[k],'stats:overview:'+k) for k in ('month','all')],
                 [('Пользователи','stats:users:0')],[('Обновить','stats:overview:'+period)],[('Главное меню','home')]]
    elif mode=='users':
        try: page=int(parts[2])
        except (IndexError,ValueError): return
        if not 0<=page<=100000: return
        rows=db.execute('''SELECT u.id,a.display_name,a.username,a.last_seen FROM users u
            LEFT JOIN user_activity a ON a.user_id=u.id ORDER BY coalesce(a.last_seen,0) DESC,u.id LIMIT 11 OFFSET ?''',(page*10,)).fetchall()
        text=f'👥 Пользователи · страница {page+1}\nВыберите пользователя. Старые имена появятся после его следующего обращения к боту.'
        buttons=[]
        for r in rows[:10]:
            label=clean(r['display_name']) or ('@'+r['username'] if r['username'] else str(r['id']))
            buttons.append([(label+(' · администратор' if r['id'] in engine.c.admins else ''),'stats:user:'+str(r['id']))])
        nav=[]
        if page: nav.append(('Назад','stats:users:'+str(page-1)))
        if len(rows)>10: nav.append(('Далее','stats:users:'+str(page+1)))
        if nav: buttons.append(nav)
        buttons+=back
    elif mode=='user':
        try: target=int(parts[2])
        except (IndexError,ValueError): return
        r=db.execute('SELECT u.*,a.display_name,a.username,a.last_seen FROM users u LEFT JOIN user_activity a ON a.user_id=u.id WHERE u.id=?',(target,)).fetchone()
        if not r:return
        r=dict(r);current=engine.s.user(target);r.update(balance=current["balance"],reserved=current["reserved"])
        bought=db.execute("SELECT coalesce(sum(amount),0) FROM ledger WHERE user_id=? AND kind='purchase' AND amount>0",(target,)).fetchone()[0]
        spent=db.execute("SELECT coalesce(-sum(amount),0) FROM ledger WHERE user_id=? AND kind='generation' AND amount<0",(target,)).fetchone()[0]
        count=db.execute('SELECT count(*) FROM projects WHERE user_id=?',(target,)).fetchone()[0]
        jobs=db.execute('''SELECT j.kind,j.status,j.error,j.created,p.body FROM jobs j JOIN projects p ON p.id=j.project_id
            WHERE p.user_id=? ORDER BY j.created DESC LIMIT 10''',(target,)).fetchall()
        import json
        text=(f'👤 {clean(r["display_name"]) or "Пользователь"}\nID: {target}\n'+('@'+clean(r['username'])+'\n' if r['username'] else '')+
              f'Последнее обращение: {stamp(r["last_seen"])}\nПроектов: {count}\nБаланс: {r["balance"]}\n'
              f'Зарезервировано: {r["reserved"]}\nКуплено токенов: {bought}\nСписано: {spent}\n\nПоследние 10 заданий:\n')
        for j in jobs:
            d=json.loads(j['body']);title=clean(d.get('script',{}).get('title') or d.get('photo_prompt') or d.get('route'),50)
            text+=f'{stamp(j["created"])} · {KINDS.get(j["kind"],j["kind"])} · {STATES.get(j["status"],j["status"])}\n{title}\n'
            if j['error']:text+='Причина: '+clean(j['error'],70)+'\n'
        buttons=[[('К списку пользователей','stats:users:0')]]+back
    else:return
    engine.s.emit(uid,text,buttons)
