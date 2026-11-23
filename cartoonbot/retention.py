"""Keep ten recent projects per user; never prune active work or shared files."""
import argparse
import gzip
import json
import sqlite3
from pathlib import Path


def data(value):
    return json.loads(gzip.decompress(value) if isinstance(value,bytes) else value)


def paths(value,root):
    found=set()
    if isinstance(value,dict):
        for v in value.values():found.update(paths(v,root))
    elif isinstance(value,list):
        for v in value:found.update(paths(v,root))
    elif isinstance(value,str) and value.startswith('/'):
        p=Path(value).resolve()
        if p.is_relative_to(root):found.add(p)
    return found


def prune(database,root,apply=False,limit=10):
    root=Path(root).resolve();media=root/'media'
    db=sqlite3.connect(database,timeout=30);db.row_factory=sqlite3.Row
    db.execute('BEGIN IMMEDIATE')
    try:
        projects=list(db.execute('SELECT * FROM projects ORDER BY created DESC,rowid DESC'))
        bodies={p['id']:data(p['body']) for p in projects}
        keep=set();counts={}
        # Protect all projects of users with live jobs: workers may reuse references.
        active={r[0] for r in db.execute("SELECT DISTINCT p.user_id FROM projects p JOIN jobs j ON p.id=j.project_id WHERE j.status IN ('queued','running')")}
        keep.update(r[0] for r in db.execute('SELECT current FROM users WHERE current IS NOT NULL'))
        uncertain={r[0] for r in db.execute("SELECT DISTINCT j.project_id FROM jobs j JOIN operations o ON o.job_id=j.id WHERE o.provider IN ('google_omni','byteplus_seedance') AND o.state NOT IN ('succeeded','rejected','failed','expired','cancelled')")}
        keep.update(uncertain)
        for p in projects:
            uid=p['user_id'];counts[uid]=counts.get(uid,0)+1
            if counts[uid]<=limit or uid in active or p['state']=='busy':keep.add(p['id'])
        # Linked parts and imported projects may still be needed on resume.
        while True:
            before=len(keep)
            for pid in list(keep):
                d=bodies.get(pid,{})
                keep.update(d[k] for k in ('previous_part','next_part','source_project') if d.get(k) in bodies)
            if len(keep)==before:break
        expired=[p for p in projects if p['id'] not in keep]
        protected=set()
        for pid in keep:protected.update(paths(bodies.get(pid,{}),media))
        for row in db.execute('SELECT project_id,body FROM history'):
            if row['project_id'] in keep:protected.update(paths(data(row['body']),media))
        for row in db.execute('SELECT body FROM outbox WHERE delivered=0'):
            protected.update(paths(data(row[0]),media))
        files=set()
        old=list(db.execute('SELECT id,user_id FROM retired_projects')) if db.execute("SELECT 1 FROM sqlite_master WHERE name='retired_projects'").fetchone() else []
        for p in expired+old:
            directory=media/str(p['user_id'])/p['id']
            if directory.is_dir():
                files.update(f for f in directory.rglob('*') if f.is_file() and not f.is_symlink() and f.resolve().is_relative_to(media) and f.resolve() not in protected)
        result={'projects':len(expired),'files':len(files),'bytes':sum(f.stat().st_size for f in files)}
        if apply:
            db.execute('CREATE TABLE IF NOT EXISTS retired_projects(id TEXT PRIMARY KEY,user_id INTEGER,created REAL,removed REAL DEFAULT (unixepoch()))')
            for p in expired:
                db.execute('INSERT OR IGNORE INTO retired_projects(id,user_id,created) VALUES (?,?,?)',(p['id'],p['user_id'],p['created']))
                db.execute('DELETE FROM history WHERE project_id=?',(p['id'],))
                db.execute('DELETE FROM projects WHERE id=?',(p['id'],))
            web_files=db.execute("SELECT 1 FROM sqlite_master WHERE name='web_files'").fetchone()
            for f in files:
                f.unlink(missing_ok=True)
                if web_files:db.execute('DELETE FROM web_files WHERE path=?',(str(f),))
            db.commit()
        else:db.rollback()
        return result
    except BaseException:
        db.rollback();raise
    finally:db.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('database');p.add_argument('root');p.add_argument('--apply',action='store_true');a=p.parse_args()
    print(json.dumps(prune(a.database,a.root,a.apply)))
