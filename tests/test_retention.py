import tempfile
import unittest
from pathlib import Path
from cartoonbot.store import Store
from cartoonbot.retention import prune

class RetentionTests(unittest.TestCase):
    def test_limit_shared_files_current_and_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=Store(root/'test.db');s.user(1)
            ps=[s.new_project(1,'omni','ru') for _ in range(12)]
            folder=root/'media'/'1'/ps[0]['id'];folder.mkdir(parents=True)
            unused=folder/'old.mp4';unused.write_bytes(b'old')
            shared=folder/'shared.png';shared.write_bytes(b'shared')
            ps[-1]['data']['refs']=[{'path':str(shared)}];s.save(ps[-1])
            self.assertEqual(prune(root/'test.db',root)['projects'],2)
            self.assertTrue(unused.exists())
            prune(root/'test.db',root,True)
            self.assertEqual(s.db.execute('SELECT count(*) FROM projects').fetchone()[0],10)
            self.assertFalse(unused.exists());self.assertTrue(shared.exists())
            self.assertEqual(prune(root/'test.db',root,True)['projects'],0)
            s.db.close()

    def test_active_user_is_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);s=Store(root/'test.db');s.user(1)
            ps=[s.new_project(1,'omni','ru') for _ in range(12)]
            with s.transaction():s.enqueue(ps[0],'script')
            self.assertEqual(prune(root/'test.db',root,True)['projects'],0)
            s.db.close()
