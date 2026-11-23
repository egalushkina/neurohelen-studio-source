"""Private local live studio. Uses only explicitly provisioned provider configuration."""
import argparse
import json
import os
import sqlite3
from pathlib import Path
from cartoonbot.config import Config
from .server import serve


def configuration(root):
    root=Path(root).resolve()
    values=json.loads((root/'provider-config.json').read_text())
    allowed=('OPENAI_','BYTEPLUS_','OMNI_','SEEDANCE_','GOOGLE_','MEDIA_')
    for key,value in values.items():
        if key.startswith(allowed) and isinstance(value,str):os.environ[key]=value
    owners_file=root/'owners.json'
    if not owners_file.exists():raise ValueError('Configure explicit owner IDs in owners.json')
    owners=set(json.loads(owners_file.read_text()))
    if not owners or any(type(uid) is not int for uid in owners):raise ValueError('Invalid owner IDs')
    config=Config(root=root,mode='live',admins=owners,allow_live=True,public_access=True,billing_enabled=True,require_purchase=True)
    config.prepare()
    return config


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8090)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent.parent/'runtime/browser-live')
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--origin')
    parser.add_argument('--trusted-proxy',action='append',default=[])
    args=parser.parse_args()
    root=args.root
    config=configuration(root)
    if args.check:print('Live configuration valid; keys remain server-side.')
    else:serve(root,args.port,config,host=args.host,origin=args.origin,trusted_proxies=args.trusted_proxy)
