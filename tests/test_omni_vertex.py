import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from cartoonbot.config import Config
from cartoonbot.providers import LiveProvider
from cartoonbot.store import Store
from cartoonbot import omni_vertex


class VertexOmniTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'db')
        self.provider = LiveProvider(self.store, Config(root=self.root))
        self.env = patch.dict(os.environ, {'OMNI_BACKEND': 'vertex',
            'GOOGLE_CLOUD_PROJECT': 'cartoon-test', 'OMNI_MODEL': 'gemini-omni-1.1-flash-preview'})
        self.env.start()

    def tearDown(self):
        self.provider.client.close()
        self.store.db.close()
        self.env.stop()
        self.tmp.cleanup()

    def run_video(self, handler):
        self.provider.client.close()
        self.provider.client = httpx.Client(transport=httpx.MockTransport(handler))
        with patch('cartoonbot.omni_vertex.access_token', return_value='oauth-test'), patch('cartoonbot.providers.time.sleep'):
            return self.provider.vertex_video({'id': 'test'}, 'Speak Russian', [], self.root / 'video.mp4', '9:16')

    def test_submission_and_poll_extract_generated_video_only(self):
        calls = []
        def handler(request):
            calls.append(request.method)
            self.assertEqual(request.headers['authorization'], 'Bearer oauth-test')
            self.assertNotIn('x-goog-api-key', request.headers)
            if request.method == 'POST':
                body = json.loads(request.content)
                self.assertEqual(body['response_format'][0]['duration'], '10s')
                self.assertEqual(body['response_format'][0]['resolution'], '720p')
                return httpx.Response(200, json={'id': 'task', 'status': 'in_progress'})
            return httpx.Response(200, json={'id': 'task', 'status': 'completed', 'usage': {'total_tokens': 123},
                'steps': [{'type': 'user_input', 'content': [{'type': 'video', 'data': 'bad'}]},
                          {'type': 'model_output', 'content': [{'type': 'video', 'data': base64.b64encode(b'generated').decode()}]}]})
        result = self.run_video(handler)
        self.assertEqual(calls, ['POST', 'GET'])
        self.assertEqual(Path(result['path']).read_bytes(), b'generated')
        self.assertEqual(self.store.db.execute('SELECT state FROM operations').fetchone()[0], 'succeeded')

    def test_minor_filter_rejection_is_classified_without_retry(self):
        calls=[]
        def handler(request):
            calls.append(request.method)
            return httpx.Response(400,json={'error':{'message':'Output violates minor safety'}})
        with self.assertRaisesRegex(ValueError,'google_minor_safety'):
            self.run_video(handler)
        self.assertEqual(calls,['POST'])
        self.assertEqual(self.store.db.execute('SELECT state FROM operations').fetchone()[0],'rejected')

    def test_poll_failure_never_resubmits(self):
        calls = []
        def handler(request):
            calls.append(request.method)
            return httpx.Response(200, json={'id': 'task', 'status': 'in_progress'}) if request.method == 'POST' else httpx.Response(503)
        with self.assertRaisesRegex(RuntimeError, 'omni_poll_http_503'):
            self.run_video(handler)
        self.assertEqual(calls, ['POST', 'GET'])
        self.assertEqual(self.store.db.execute('SELECT state FROM operations').fetchone()[0], 'accepted')

    def test_image_payload_and_edit_context(self):
        image = self.root / 'ref.png'; image.write_bytes(b'image')
        first = omni_vertex.payload('animate', [image], '16:9')
        self.assertEqual(first['generation_config']['video_config']['task'], 'image_to_video')
        self.assertEqual(base64.b64decode(first['input'][1]['data']), b'image')
        edit = omni_vertex.payload('red hat', [image], '16:9', 'previous', 'red hat')
        self.assertNotIn('previous_interaction_id', edit)
        self.assertEqual(edit['input'][0]['text'], 'red hat')
        self.assertEqual(base64.b64decode(edit['input'][1]['data']), b'image')
        self.assertEqual(edit['generation_config']['video_config']['task'], 'image_to_video')

    def test_does_not_treat_echoed_input_as_output(self):
        with self.assertRaisesRegex(RuntimeError, 'missing_inline_video'):
            omni_vertex.save_video({'steps': [{'type': 'user_input', 'content': [{'type': 'video', 'data': 'aGVsbG8='}]}]}, self.root / 'no.mp4')
        self.assertFalse((self.root / 'no.mp4').exists())
