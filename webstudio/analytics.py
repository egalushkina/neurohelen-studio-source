"""Owner-only aggregate reporting. No prompts, emails or credentials in reports."""
import time
from urllib.parse import urlsplit

class Analytics:
    def __init__(self, store, admins):
        self.s, self.admins = store, admins
        with store.lock:
            store.db.executescript('''
              CREATE TABLE IF NOT EXISTS web_page_counts(day TEXT,path TEXT,source TEXT,hits INTEGER DEFAULT 0,
                PRIMARY KEY(day,path,source));
              CREATE TABLE IF NOT EXISTS web_analytics_meta(key TEXT PRIMARY KEY,value REAL);
            ''')
            store.db.execute("INSERT OR IGNORE INTO web_analytics_meta VALUES('started',?)",(time.time(),))

    def page(self,path,referrer,agent):
        if any(w in agent.lower() for w in ('bot','crawler','spider','headless','httpx','curl')):return
        try:source=(urlsplit(referrer or '').hostname or '')[:120]
        except ValueError:source=''
        if source=='studio.neurohelen.com':source='internal'
        source=source or 'direct'
        # Anonymous daily counters only: no IP, cookie, email or full referrer URL.
        with self.s.lock:
            self.s.db.execute("INSERT INTO web_page_counts VALUES(date('now'),?,?,1) ON CONFLICT(day,path,source) DO UPDATE SET hits=hits+1",(path,source))

    def report(self):
        with self.s.lock:
            db=self.s.db
            excluded=tuple(self.admins) or (-1,)
            slots=','.join('?' for _ in excluded)
            def rows(sql):return [dict(r) for r in db.execute(sql,excluded)]
            accounts=rows(f'SELECT count(*) n FROM web_accounts WHERE uid NOT IN ({slots})')[0]['n']
            orders=rows(f"SELECT currency,count(*) purchases,count(DISTINCT uid) buyers,sum(amount) minor,sum(tokens) tokens FROM web_orders WHERE status='paid' AND live=1 AND uid NOT IN ({slots}) GROUP BY currency")
            packages=rows(f"SELECT package,currency,count(*) purchases,sum(amount) minor FROM web_orders WHERE status='paid' AND live=1 AND uid NOT IN ({slots}) GROUP BY package,currency")
            jobs=rows(f'SELECT j.kind,j.status,count(*) n FROM jobs j JOIN projects p ON p.id=j.project_id WHERE p.user_id NOT IN ({slots}) GROUP BY j.kind,j.status')
            stages=rows(f'SELECT state,count(*) n FROM projects WHERE user_id NOT IN ({slots}) GROUP BY state')
            creators=rows(f'SELECT count(DISTINCT user_id) n FROM projects WHERE user_id NOT IN ({slots})')[0]['n']
            projects=rows(f'SELECT count(*) n FROM projects WHERE user_id NOT IN ({slots})')[0]['n']
            buyers=rows(f"SELECT count(DISTINCT uid) n FROM web_orders WHERE status='paid' AND live=1 AND uid NOT IN ({slots})")[0]['n']
            views=[dict(r) for r in db.execute('SELECT day,sum(hits) hits FROM web_page_counts GROUP BY day ORDER BY day DESC LIMIT 30')]
            sources=[dict(r) for r in db.execute('SELECT source,sum(hits) hits FROM web_page_counts GROUP BY source ORDER BY hits DESC LIMIT 20')]
            return dict(accounts=accounts,creators=creators,projects=projects,buyers=buyers,orders=orders,packages=packages,jobs=jobs,stages=stages,views=views,sources=sources,started=db.execute("SELECT value FROM web_analytics_meta WHERE key='started'").fetchone()[0])
