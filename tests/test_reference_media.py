import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from cartoonbot.reference_media import signed_url, resolve


class ReferenceMediaTests(unittest.TestCase):
    def test_signed_access_expiry_tampering_and_scope(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ,{'MEDIA_PUBLIC_BASE_URL':'https://media.example.com','MEDIA_SIGNING_KEY':'k'*40}):
            root=Path(tmp)/'media'; root.mkdir(); video=root/'a.mp4'; video.write_bytes(b'video')
            with patch('cartoonbot.reference_media.time.time',return_value=100):
                url=signed_url(root,video)
            path=urlsplit(url).path
            self.assertEqual(resolve(root,path,'k'*40,101),video.resolve())
            for wrong in (path+'x',path.replace('/v1/','/v2/'),'/'):
                with self.assertRaises(ValueError): resolve(root,wrong,'k'*40,101)
            with self.assertRaises(ValueError): resolve(root,path,'k'*40,86600)
            outside=Path(tmp)/'secret.mp4'; outside.write_bytes(b'secret')
            with self.assertRaises(ValueError): signed_url(root,outside)
            secret=root/'config.env'; secret.write_text('secret')
            with self.assertRaises(ValueError): signed_url(root,secret)

    def test_silent_reference_preserves_frames_timing_and_original(self):
        from cartoonbot import media
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source.mp4'; target=Path(tmp)/'silent.mp4'
            media.ffmpeg(['-f','lavfi','-i','color=c=red:s=64x64:r=24','-f','lavfi','-i','sine=frequency=440','-t','1','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',source])
            before=hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertTrue(media.info(source)['audio'])
            media.silent_reference(source,target)
            self.assertFalse(media.info(target)['audio'])
            self.assertAlmostEqual(media.info(source)['seconds'],media.info(target)['seconds'],places=1)
            self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())
            for path,name in [(source,'a'),(target,'b')]:
                media.ffmpeg(['-i',path,'-map','0:v:0','-f','framemd5',Path(tmp)/name])
            self.assertEqual((Path(tmp)/'a').read_text(),(Path(tmp)/'b').read_text())
