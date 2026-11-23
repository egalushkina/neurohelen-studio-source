import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from cartoonbot.providers import LiveProvider
from cartoonbot.i18n import tr

class ImageSafetyErrorTests(unittest.TestCase):
    def test_provider_refusal_is_classified_without_private_message(self):
        response=SimpleNamespace(status_code=400,json=lambda:{'error':{'code':'moderation_blocked','message':'private request details'}})
        store=SimpleNamespace(operation=Mock(return_value='operation'),record_operation=Mock())
        provider=SimpleNamespace(s=store,client=SimpleNamespace(post=Mock(return_value=response)))
        with self.assertRaisesRegex(ValueError,'^image_safety_rejected$'):
            LiveProvider.post(provider,{'id':'job'},'openai_image','https://api.openai.com/v1/images/generations',key='private-key',payload={})
        diagnostic=store.record_operation.call_args.kwargs['usage']
        self.assertEqual(diagnostic,{'http_status':400,'provider_status':'moderation_blocked'})
        for language in ('ru','en','uk'):
            text=tr(language,'image_safety_rejected')
            self.assertNotIn('RuntimeError',text)
            self.assertNotEqual(text,'image_safety_rejected')
