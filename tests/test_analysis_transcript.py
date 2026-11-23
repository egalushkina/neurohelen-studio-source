import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from cartoonbot.providers import LiveProvider

class AnalysisTranscriptTests(unittest.TestCase):
    def test_verbatim_audio_survives_visual_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider=Mock()
            provider.transcription.return_value={'text':'  Укроп: Я тебе что, носок?  ','language':'russian','words':[{'word':'Укроп','start':0,'end':1}]}
            provider.structured.return_value={'summary':'Herbs talking','shots':[]}
            with patch('cartoonbot.providers.media.sample_frames',return_value=['frame.png']), patch('cartoonbot.providers.media.info',return_value={'seconds':51}):
                result=LiveProvider.analyze(provider,{'id':'test'},str(Path(tmp)/'source.mp4'),'ru')
            self.assertEqual(result['transcript'],'Укроп: Я тебе что, носок?')
            self.assertEqual(result['transcript_language'],'russian')
            self.assertEqual(result['transcript_words'][0]['start'],0)
            self.assertEqual(result['summary'],'Herbs talking')
