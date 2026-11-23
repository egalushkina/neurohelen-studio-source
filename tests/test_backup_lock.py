import sqlite3,tempfile,threading,time,unittest
from pathlib import Path
from deploy.backup_wallet import snapshot

class BackupLockTests(unittest.TestCase):
 def test_attached_database_temporarily_locked(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);paths={n:root/(n+'.db') for n in ('telegram','wallet','web')}
   for path in paths.values():
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE sample(value)');db.execute('INSERT INTO sample VALUES (7)')
   ready=threading.Event()
   def block():
    db=sqlite3.connect(paths['wallet']);db.execute('BEGIN EXCLUSIVE');ready.set();time.sleep(.7);db.rollback();db.close()
   thread=threading.Thread(target=block);thread.start();ready.wait()
   snapshot(paths,root/'backup');thread.join()
   self.assertTrue((root/'backup'/'complete').exists())
   for name in paths:
    with sqlite3.connect(root/'backup'/(name+'.sqlite3')) as db:self.assertEqual(db.execute('SELECT value FROM sample').fetchone()[0],7)
