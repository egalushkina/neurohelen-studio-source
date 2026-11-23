"""Preserve project/media archives; expire only completed-job scratch responses."""
import json
import shutil
import time
from pathlib import Path


def cleanup(store,root,days=7):
    cutoff=time.time()-days*86400
    rows=store.db.execute("SELECT o.usage FROM operations o JOIN jobs j ON o.job_id=j.id WHERE j.status IN ('succeeded','recovered') AND j.created<?",(cutoff,)).fetchall()
    for row in rows:
        usage=json.loads(row['usage'])
        if usage.get('response_path'):
            path=Path(usage['response_path']).resolve()
            if path.is_relative_to((root/'responses').resolve()): path.unlink(missing_ok=True)
        if usage.get('result_path'):
            folder=Path(usage['result_path']).resolve().parent
            if folder.parent==(root/'codex').resolve() and folder.is_dir(): shutil.rmtree(folder)
