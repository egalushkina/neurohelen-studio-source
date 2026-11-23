import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cartoonbot import media

class SamplingTests(unittest.TestCase):
    def test_sampling_uses_media_seconds_and_covers_whole_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            for seconds,fps in ((15,2),(60,0.5)):
                with patch.object(media,'info',return_value={'seconds':seconds}),patch.object(media,'ffmpeg') as ff:
                    media.sample_frames('video.mp4',Path(tmp)/str(seconds))
                    args=ff.call_args.args[0]
                    self.assertEqual(args[args.index('-vf')+1],f'fps={fps},scale=480:-2')
                    self.assertEqual(args[args.index('-frames:v')+1],'30')
