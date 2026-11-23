import argparse
import json
import signal
import threading
import time
from .config import Config
from .store import Store
from .engine import Engine
from .providers import DemoProvider, LiveProvider
from .codex_provider import CodexProvider
from .worker import Worker
from .telegram import Telegram


def main():
    parser=argparse.ArgumentParser(description='Cartoon studio Telegram bot')
    parser.add_argument('--check',action='store_true',help='Validate local configuration without network calls')
    parser.add_argument('--jobs',action='store_true',help='List jobs requiring owner review (no API calls)')
    args=parser.parse_args()
    cfg=Config.from_env(); cfg.prepare()
    store=Store(cfg.root/'bot.sqlite3',initial_balance=0 if cfg.billing_enabled else 10000)
    if args.check:
        from .media import ffmpeg
        ffmpeg(['-version'])
        print('Local configuration OK. Mode: '+cfg.mode+'. Telegram token: '+('configured' if cfg.token else 'missing'))
        return
    if args.jobs:
        rows=store.db.execute('SELECT id,project_id,kind,status,error FROM jobs ORDER BY created DESC LIMIT 30').fetchall()
        print(json.dumps([dict(x) for x in rows],ensure_ascii=False,indent=2)); return
    if not cfg.token:
        print('Add TELEGRAM_BOT_TOKEN to .env. No connections or paid generations were started.')
        return
    import fcntl
    import os
    lock=(cfg.root/'bot.lock').open('w')
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print('Bot is already running.'); return
    (cfg.root/'bot.pid').write_text(str(os.getpid()))
    from .reference_media import start as start_media
    media_server=start_media(cfg.root/'media',store,cfg)
    store.recover()
    store.compact_history()
    from .maintenance import cleanup
    cleanup(store,cfg.root)
    engine=Engine(store,cfg); telegram=Telegram(cfg,store,engine)
    stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:stop.set())
    def worker_loop():
        provider={'demo':DemoProvider,'live':LiveProvider,'codex':CodexProvider}[cfg.mode](store,cfg)
        worker=Worker(store,cfg,provider,engine)
        while not stop.is_set():
            if not worker.run_once(): stop.wait(.5)
    def delivery_loop():
        last_cleanup=time.monotonic()
        while not stop.is_set():
            try:
                if time.monotonic()-last_cleanup>86400:
                    with store.lock: cleanup(store,cfg.root)
                    last_cleanup=time.monotonic()
                if not telegram.deliver(): stop.wait(.5)
            except Exception as exc:
                print('Delivery pending: '+type(exc).__name__)
                stop.wait(5)
    threads=[threading.Thread(target=worker_loop,daemon=True) for _ in range(cfg.workers)]
    threads.append(threading.Thread(target=delivery_loop,daemon=True))
    for t in threads: t.start()
    print('Bot running in '+cfg.mode+' mode.')
    while not stop.is_set():
        try: telegram.poll_once()
        except Exception as exc:
            print('Polling paused: '+type(exc).__name__); stop.wait(5)
    if media_server: media_server.shutdown()
    deadline=time.monotonic()+30
    for t in threads: t.join(timeout=max(0,deadline-time.monotonic()))


if __name__=='__main__': main()
