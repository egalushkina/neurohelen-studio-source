"""Recover saved results without resubmitting a generation request."""
import json
import os
import shutil
from pathlib import Path
from . import media


def recover(worker, p, source):
    s=worker.s; d=p['data']
    old=s.db.execute('SELECT * FROM jobs WHERE id=? AND project_id=?',(source,p['id'])).fetchone()
    if not old: raise ValueError('recovery_unavailable')
    old=dict(old); directory=worker.c.root/'media'/str(p['user_id'])/p['id']/source
    pending=d.get('pending_generation',{})
    if pending.get('job')!=source:
        # Checkpointed output may already be complete; do not infer an unknown remote result.
        pending={}
    unit=pending.get('unit'); kind=old['kind']
    operations=s.db.execute('SELECT * FROM operations WHERE job_id=? ORDER BY created',(source,)).fetchall()
    relevant=[o for o in operations if o['provider'] in ('google_omni','byteplus_seedance','openai_image') or
              (o['provider']=='local_codex' and json.loads(o['usage']).get('native_image'))]
    if unit is not None:
        relevant=[o for o in relevant if json.loads(o['usage']).get('unit')==unit]
    op=dict(relevant[-1]) if relevant else None
    candidate=Path(pending['target']) if pending.get('target') else None
    if candidate and not candidate.resolve().is_relative_to(directory.resolve()): raise ValueError('recovery_unavailable')
    if candidate and not candidate.exists() and op:
        usage=json.loads(op['usage'])
        if op['provider']=='byteplus_seedance' and op['remote_id']:
            r=worker.provider.client.get('https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks/'+op['remote_id'],headers={'Authorization':'Bearer '+os.environ['BYTEPLUS_API_KEY']})
            r.raise_for_status(); result=r.json(); state=result.get('status'); op['state']=state
            s.record_operation(op['id'],state or 'unknown',op['remote_id'],result.get('usage',{}))
            if state=='succeeded': worker.provider.download(result['content']['video_url'],candidate)
            elif state not in ('failed','expired','cancelled'): raise ValueError('recovery_pending')
        elif op['provider']=='google_omni' and op['remote_id'] and os.getenv('OMNI_BACKEND')=='vertex':
            from . import omni_vertex
            from urllib.parse import quote
            r=worker.provider.client.get(omni_vertex.endpoint()+'/'+quote(op['remote_id'],safe=''),
                headers={'Authorization':'Bearer '+omni_vertex.access_token()})
            r.raise_for_status(); result=r.json(); state=result.get('status')
            if state=='completed':
                omni_vertex.save_video(result,candidate); op['state']='succeeded'
            elif state in ('failed','cancelled'): op['state']=state
            else: raise ValueError('recovery_pending')
            s.record_operation(op['id'],op['state'],op['remote_id'],result.get('usage',{}))
        elif usage.get('response_path'):
            path=Path(usage['response_path'])
            if not path.resolve().is_relative_to((worker.c.root/'responses').resolve()): raise ValueError('recovery_unavailable')
            result=json.loads(path.read_text())
            if op['provider']=='google_omni':
                from .omni_vertex import save_video
                if result.get('output_video',{}).get('data'):
                    import base64
                    candidate.write_bytes(base64.b64decode(result['output_video']['data']))
                else: save_video(result,candidate)
            elif op['provider']=='openai_image':
                import base64
                candidate.write_bytes(base64.b64decode(result['data'][0]['b64_json']))
        elif usage.get('result_path'):
            path=Path(usage['result_path'])
            if not path.resolve().is_relative_to((worker.c.root/'codex').resolve()): raise ValueError('recovery_unavailable')
            if path.exists():
                value=json.loads(path.read_text()); image=Path(value.get('path','')).resolve()
                # The local process finished, but explicitly returned no image.
                # This is a failed result, not a remote task still in progress.
                if op['provider']=='local_codex' and op['state']=='succeeded' and value.get('path')=='':
                    op['state']='failed'
                    s.record_operation(op['id'],'failed')
                allowed=Path(os.getenv('CODEX_HOME',str(Path.home()/'.codex')))/'generated_images'
                if image.is_file() and image.is_relative_to(allowed.resolve()): shutil.copy2(image,candidate)
    if candidate and candidate.exists():
        if kind in ('visuals','edit_visuals'):
            from PIL import Image
            with Image.open(candidate) as image: image.verify()
            d['images'][str(unit)]={'path':str(candidate),'description':'Recovered result'}
            if d['route']=='omni': d['videos'].pop(str(unit),None)
            else: d['videos']={}
            for key in ('assembled','final'): d.pop(key,None)
            completed=d.setdefault('visual_completed',{}).setdefault(source,[])
            if unit not in completed: completed.append(unit)
            with s.transaction(): s.emit(p['user_id'],file=str(candidate),kind='photo',text=str(unit))
        elif kind=='video':
            seconds=10 if d['route']=='omni' else d['seconds']
            if abs(media.info(candidate)['seconds']-seconds)>.55: raise ValueError('duration_needs_review')
            d['videos'][str(unit)]={'path':str(candidate),'remote_id':op['remote_id'] if op else None}
            with s.transaction():
                s.complete_unit(old,unit)
                s.emit(p['user_id'],file=str(candidate),kind='video',text=str(unit))
        d.pop('pending_generation',None)
    elif op and op['state'] not in ('rejected','failed','expired','cancelled') and (candidate or op['state']!='succeeded'):
        raise ValueError('recovery_pending')
    with s.transaction():
        s.settle(old,False)
        s.db.execute("UPDATE jobs SET status='recovered' WHERE id=?",(source,))
    d['recover_source']=source
    p['state']='resume_ready'
