"""Expiring, signed access to individual reference videos; never serves directories."""
import base64
import hashlib
import hmac
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


def configured():
    return (os.getenv('MEDIA_PUBLIC_BASE_URL','').startswith('https://')
            and len(os.getenv('MEDIA_SIGNING_KEY','')) >= 32)


def signed_url(root, path):
    if not configured(): raise ValueError('reference_video_needs_public_media_url')
    path=Path(path).resolve(); root=Path(root).resolve()
    rel=path.relative_to(root).as_posix()
    if not path.is_file() or path.suffix.lower() not in ('.mp4','.mov'):
        raise ValueError('invalid_reference_video')
    encoded=base64.urlsafe_b64encode(rel.encode()).decode().rstrip('=')
    expires=int(time.time())+86400
    payload=f'{expires}/{encoded}'
    signature=hmac.new(os.environ['MEDIA_SIGNING_KEY'].encode(),payload.encode(),hashlib.sha256).hexdigest()
    return os.environ['MEDIA_PUBLIC_BASE_URL'].rstrip('/')+'/v1/'+payload+'/'+signature


def resolve(root, url_path, key, now=None):
    parts=urlsplit(url_path).path.split('/')
    if len(parts)!=5 or parts[1]!='v1': raise ValueError('not_found')
    _,_,expires,encoded,signature=parts
    payload=f'{expires}/{encoded}'
    expected=hmac.new(key.encode(),payload.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,expected): raise ValueError('not_found')
    if int(expires)<(time.time() if now is None else now): raise ValueError('expired')
    rel=base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)).decode()
    root=Path(root).resolve(); path=(root/rel).resolve(); path.relative_to(root)
    if not path.is_file() or path.suffix.lower() not in ('.mp4','.mov'): raise ValueError('not_found')
    return path


def start(root, store=None, config=None):
    if not configured(): return None
    key=os.environ['MEDIA_SIGNING_KEY']
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass  # Do not log bearer links.
        def do_POST(self):
            if urlsplit(self.path).path!='/payments/stripe/webhook' or store is None:
                self.send_error(404); return
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=1048576:
                    self.send_error(413); return
                self.connection.settimeout(10)
                raw=self.rfile.read(size)
                from .stripe_checkout import receive
                status=receive(store,config,raw,self.headers.get('Stripe-Signature',''))
            except Exception: status=500
            self.send_response(status); self.send_header('Content-Length','0'); self.end_headers()
        def do_HEAD(self): self.send_file(False)
        def do_GET(self): self.send_file(True)
        def send_file(self, body):
            try: path=resolve(root,self.path,key)
            except (ValueError,UnicodeError,OSError): self.send_error(404); return
            size=path.stat().st_size; start=0; end=size-1
            range_header=self.headers.get('Range')
            if range_header:
                match=re.fullmatch(r'bytes=(\d+)-(\d*)',range_header)
                if not match: self.send_error(416); return
                start=int(match[1]); end=min(int(match[2]) if match[2] else end,end)
                if start>end: self.send_error(416); return
            self.send_response(206 if range_header else 200)
            self.send_header('Content-Type','video/mp4' if path.suffix.lower()=='.mp4' else 'video/quicktime')
            self.send_header('Content-Length',str(end-start+1)); self.send_header('Accept-Ranges','bytes')
            self.send_header('Cache-Control','private, no-store')
            if range_header: self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            if body:
                try:
                    with path.open('rb') as f:
                        f.seek(start); remaining=end-start+1
                        while remaining:
                            chunk=f.read(min(65536,remaining))
                            if not chunk: break
                            self.wfile.write(chunk); remaining-=len(chunk)
                except (BrokenPipeError,ConnectionResetError): pass
    server=ThreadingHTTPServer((os.getenv('MEDIA_BIND','127.0.0.1'),int(os.getenv('MEDIA_PORT','8801'))),Handler)
    server.daemon_threads=True
    threading.Thread(target=server.serve_forever,daemon=True).start()
    return server
