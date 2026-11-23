"""Google Cloud Omni authentication and Interactions wire format."""
import base64
import os
import re
from pathlib import Path


def configured():
    if os.getenv('OMNI_BACKEND') == 'vertex':
        return bool(os.getenv('GOOGLE_CLOUD_PROJECT') and os.getenv('GOOGLE_APPLICATION_CREDENTIALS'))
    return bool(os.getenv('GOOGLE_API_KEY'))


def endpoint():
    project = os.environ['GOOGLE_CLOUD_PROJECT']
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]|[0-9]+', project):
        raise ValueError('invalid_google_project')
    return f'https://aiplatform.googleapis.com/v1beta1/projects/{project}/locations/global/interactions'


def access_token():
    # Only the bot's service account is used; never inherit a user's cloud login.
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    credentials = service_account.Credentials.from_service_account_file(
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'],
        scopes=['https://www.googleapis.com/auth/cloud-platform'])
    if credentials.project_id != os.environ['GOOGLE_CLOUD_PROJECT']:
        raise ValueError('google_credential_project_mismatch')
    credentials.refresh(Request())
    return credentials.token


def payload(prompt, refs, ratio, previous=None, edit=None):
    if ratio not in ('9:16', '16:9'):
        raise ValueError('invalid_video_ratio')
    content = [{'type': 'text', 'text': prompt}]
    for ref in refs[:5]:
        path = Path(ref)
        mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.webp': 'image/webp'}.get(path.suffix.lower())
        if not mime or path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError('invalid_omni_reference')
        content.append({'type': 'image', 'mime_type': mime,
                        'data': base64.b64encode(path.read_bytes()).decode()})
    result = {
        'model': os.getenv('OMNI_MODEL', 'gemini-omni-1.1-flash-preview'),
        'input': content,
        'response_format': [{'type': 'video', 'aspect_ratio': ratio,
                             'resolution': '720p', 'duration': '10s'}],
    }
    # Scene revisions are fresh generations from the approved still. Chaining
    # previous video interactions was rejected by Vertex; retain image inputs
    # and the explicit generation task for revisions as well as first runs.
    result['generation_config'] = {'video_config': {'task': 'image_to_video' if refs else 'text_to_video'}}
    return result


def save_video(result, target):
    # Inspect only generated content, never a video echoed from user input.
    parts = [part for step in result.get('steps', []) if step.get('type') == 'model_output'
             for part in step.get('content', [])]
    if not parts:
        parts = result.get('outputs', [])
    videos = [p for p in parts if p.get('type') == 'video' and p.get('data')]
    if len(videos) != 1:
        raise RuntimeError('omni_missing_inline_video')
    data = base64.b64decode(videos[0]['data'], validate=True)
    if not data or len(data) > 100 * 1024 * 1024:
        raise ValueError('invalid_omni_video_size')
    Path(target).write_bytes(data)
