import copy
import json
import re
import threading
from pathlib import Path
from . import media
from .i18n import tr


class GenerationCancelled(Exception):
    pass


class Worker:
    def __init__(self, store, config, provider, engine):
        self.s,self.c,self.provider,self.engine=store,config,provider,engine

    def run_once(self):
        job=self.s.claim()
        if not job: return False
        with self.s.lock:
            p=self.s.project(job['project_id'])
            lang=self.s.user(p['user_id'])['lang']
        d=p['data']; d['interface_lang']=lang
        payload=json.loads(job['payload'])
        directory=self.c.root/'media'/str(p['user_id'])/p['id']/job['id']
        directory.mkdir(parents=True,exist_ok=True)
        outputs=[]; notice=None; second_script=None; active_scene=None
        done=threading.Event()
        def progress():
            for _ in range(2):
                if done.wait(45): return
                with self.s.transaction():
                    current=self.s.project(p['id'])
                    if current['state']=='busy' and current['revision']==job['revision']:
                        self.s.emit(p['user_id'],tr(lang,'supplied_working' if d.get('return_state')=='supplied_review' else 'script_working'))
        if job['kind'] in ('script','edit_script'):
            threading.Thread(target=progress,daemon=True).start()
        def check_cancelled():
            with self.s.lock:
                row=self.s.db.execute('SELECT error FROM jobs WHERE id=?',(job['id'],)).fetchone()
                if row and row['error']=='cancel_requested': raise GenerationCancelled()
        try:
            check_cancelled()
            from .access import allowed
            if job['kind'] not in ('recover','reels_text') and not allowed(self.s,self.c,p['user_id'],job['reserve'],job['reserve']):
                raise ValueError('purchase_required')
            kind=job['kind']
            if kind=='recover':
                from .recovery import recover
                recover(self,p,payload['source'])
            elif kind=='reels_text':
                if not d.get('final') or d.get('final')!=d.get('assembled'):
                    raise ValueError('no_approved_video')
                try:
                    d['reels_text']=self.provider.reels_text(job,d)
                    d['reels_text_video']=d['final']
                    d.pop('reels_text_failed',None)
                except Exception:
                    d['reels_text_failed']=True
                p['state']='finished'
            elif kind=='analyze_style':
                profile=self.provider.analyze_style(job,d['style_reference']['path'],lang)
                if not isinstance(profile,dict) or not all(isinstance(profile.get(k),str) and profile[k].strip() for k in ('summary','instructions')):
                    raise ValueError('invalid_style_analysis')
                d['style_profile']={'summary':profile['summary'][:250],'instructions':profile['instructions'][:1200]}
                p['state']='style_reference_review'
            elif kind=='photo_image':
                refs=[r['path'] for r in d['refs']]
                prompt=d['photo_prompt']+'\nReference preservation requirements: '+json.dumps([r.get('purpose','Preserve identity and appearance') for r in d['refs']],ensure_ascii=False)
                if payload.get('edit'):
                    refs=[d['photo_path']]+refs
                    prompt='Edit the FIRST image, the latest photo result. LATEST USER CORRECTION: '+payload['edit']+'\nApply this correction visibly. It overrides conflicting original appearance and references. Preserve only unrelated details. Remaining images are supporting references, not instructions to undo the correction.'
                d['photo_path']=self.provider.image(job,prompt,refs,directory/'photo.png',d,1)
                outputs.append((d['photo_path'],'photo','')); p['state']='photo_result'
            elif kind=='prepare_video_revision':
                from .omni_edits import resolve
                revised=copy.deepcopy(d['script'])
                edits={x['id']:x['instruction'] for x in d['video_edits']}
                for scene in revised['scenes']:
                    if scene['id'] not in edits: continue
                    refs=([d['images'][str(scene['id'])]['path']] if d['route']=='omni' else [x['path'] for x in d['images'].values()])
                    scene.update(resolve(self.provider,job,d,scene,edits[scene['id']],refs))
                d['pending_video_revision']=revised
                p['state']='video_revision_review'
            elif kind=='dialogue_proposal':
                scene=next(s for s in d['script']['scenes'] if s['id']==payload['scene_id'])
                failed=False
                try:
                    proposed=self.provider.propose_dialogue(job,scene)
                except ValueError as exc:
                    if str(exc) not in ('seedance_dialogue_mismatch','script_too_long'): raise
                    proposed=''; failed=True
                d['dialogue_proposal']={'id':scene['id'],'original':scene['dialogue'],'proposed':proposed,'failed':failed,'return_state':payload.get('return_state','script')}
                p['state']='dialogue_proposal'
            elif kind=='edit_script' and payload.get('merge_ids'):
                from .supplied_script import merge_scenes
                d['script']=merge_scenes(d,payload['merge_ids'])
                first=min(payload['merge_ids'])
                for key in ('images','videos'):
                    d[key]={k:v for k,v in d.get(key,{}).items() if int(k)<first}
                for key in ('assembled','final','video_edits','script_summary','first_frame_pending','resume_scene_ids','applied_scene_edits','edits','safety_pending_ids','skipped_scenes','repair_scene','error_scene_id'):
                    d.pop(key,None)
                d['script_edit_history']=(d.get('script_edit_history',[])+[{'request':payload['request'],'result_scene_count':len(d['script']['scenes'])}])[-6:]
                p['state']='script'
            elif kind in ('script','edit_script'):
                assessment=None
                if d['route']=='seedance' and d.get('supplied_script'):
                    from .supplied_script import apply_seedance
                    apply_seedance(d)
                short_source=(d.get('source',{}).get('kind')=='video' and
                              0 < d.get('source',{}).get('seconds',0) <= 30)
                if d['route']=='seedance' and short_source and not d.get('seedance_duration_chosen'):
                    duration=30 if d['source']['seconds']>15 else 15
                    d.update(recreate=True,seconds=duration,seedance_mode=str(duration),seedance_duration_chosen=True)
                    if duration==30: d['version']='2.5'
                if kind=='script' and d['route']=='seedance' and not d.get('seedance_duration_chosen'):
                    assessment=self.provider.assess_seedance(job,d)
                    if type(assessment.get('needs_longer')) is not bool: raise ValueError('invalid_duration_assessment')
                if assessment and d.get('supplied_script'):
                    from .supplied_script import duration
                    if duration(d['supplied_script'])<=30:
                        seconds=30 if assessment['needs_longer'] else 15
                        d.update(seconds=seconds,seedance_mode=str(seconds),seedance_duration_chosen=True)
                        if seconds==30: d['version']='2.5'
                        assessment=None
                if assessment and assessment['needs_longer']:
                    d['duration_reason']=assessment.get('reason','')[:1500]
                    p['state']='seedance_length'
                else:
                    if d['route']=='seedance': d['seedance_duration_chosen']=True
                    d['script']=self.provider.script(job,d,payload.get('edits'),request=payload.get('request'))
                    if payload.get('request'):
                        d['script_edit_history']=(d.get('script_edit_history',[])+[{'request':payload['request'],'result_scene_count':len(d['script']['scenes'])}])[-6:]
                    if re.search(r'голос|voice|тембр|timbre|accent|акцент',payload.get('request',''),re.I):
                        d['requested_voice_edit']=payload['request']
                    if d.get('web_part_durations') and not d.get('part_number'):
                        from .seedance_duration import halves
                        d['script'],second_script=halves(d['script'],d['web_part_durations'][0])
                        d['seconds']=d['web_part_durations'][0];d['part_number']=1
                    elif d.get('seedance_mode')=='split' and not d.get('part_number'):
                        from .seedance_duration import halves
                        d['script'],second_script=halves(d['script'])
                        d['seconds']=15; d['part_number']=1
                    # Editing upstream content invalidates derived outputs.
                    d['images']={}; d['videos']={}
                    for key in ('assembled','final','video_edits','script_summary','first_frame_pending','resume_scene_ids','applied_scene_edits'): d.pop(key,None)
                    p['state']='script'
                    photos=[r for r in d.get('refs',[]) if r['kind']=='photo']
                    if (kind=='script' and d.get('seedance_source_flow')
                            and photos
                            and not d.get('source_photos_assigned')):
                        # For source-video replacements, the user—not the script model—
                        # confirms which uploaded photo replaces each character.
                        for asset in d['script']['assets']:
                            asset['reference_photo_ids']=[]
                    assigned={i for a in d['script']['assets'] for i in a.get('reference_photo_ids',[])}
                    if d.get('recreate') and any(i not in assigned for i in range(1,len(photos)+1)):
                        p['state']='reference_assignment'
            elif kind=='plan_edits':
                target=d['edit_target']
                ids=[int(x) for x in d['images']] if target=='visuals' else [s['id'] for s in d['script']['scenes']]
                result=self.provider.edits(job,d,payload['text'],ids)
                changes=result['changes']
                if result['clarification'] or not changes:
                    question=result['clarification'] or tr(lang,'edit_question')
                    history=d.setdefault('edit_history',[])
                    history.extend([{'role':'user','text':payload['text']},{'role':'assistant','text':question}])
                    d['edit_history']=history[-12:]; d['edit_clarification']=question
                    p['state']='edit_input'
                else:
                    if len({x['id'] for x in changes})!=len(changes) or any(x['id'] not in ids for x in changes):
                        raise ValueError('invalid_edit_targets')
                    from .visual_edits import wants_regeneration
                    if target=='visuals' and wants_regeneration(payload['text']):
                        for change in changes: change['regenerate']=True
                    d['edits']=changes; d.pop('edit_clarification',None); d.pop('edit_history',None); p['state']='edit_confirm'
            elif kind in ('visuals','edit_visuals'):
                from .visual_edits import wants_regeneration
                edits={x['id']:x['instruction'] for x in payload.get('edits',[])}
                regenerate_ids={x['id'] for x in payload.get('edits',[]) if x.get('regenerate') or wants_regeneration(x['instruction'])}
                refs=[x['path'] for x in d['refs'] if x['kind']=='photo']
                purposes=[x.get('purpose','') for x in d['refs'] if x['kind']=='photo']
                if d.get('recreate') and d['route']=='seedance' and d.get('visual_source')!='upload' and len(refs)==1 and len(edits)==1:
                    target_id,instruction=next(iter(edits.items()))
                    if re.search(r'референс|reference|предоставленн.*фото|загруженн.*фото',instruction,re.I):
                        for asset in d['script']['assets']:
                            asset['reference_photo_ids']=[1] if asset['id']==target_id else [i for i in asset.get('reference_photo_ids',[]) if i!=1]
                identity=json.dumps(d['script']['assets'],ensure_ascii=False)
                from .continuity import VISUAL_RULES
                units=d['script']['scenes'] if d['route']=='omni' or d.get('visual_source')=='upload' else d['script']['assets']
                total_visuals=len(units)
                if d['route']=='omni' and kind=='visuals':
                    if payload.get('remaining'):
                        units=units[1:]
                    else:
                        units=units[:1]
                        d['first_frame_pending']=True
                if d.get('previous_part'):
                    with self.s.lock:
                        parent=self.s.project(d['previous_part'],p['user_id'])
                    if parent and parent['data'].get('style')==d.get('style') and parent['data'].get('images'):
                        for asset in parent['data']['images'].values():
                            if asset['path'] not in refs: refs.append(asset['path'])
                # All frames receive the same approved references and character bible.
                for unit in units:
                    check_cancelled()
                    number=unit['id']; key=str(number)
                    if number in payload.get('completed_ids',[]): continue
                    if edits and number not in edits: continue
                    prompt=unit['image_prompt'] if d['route']=='omni' or d.get('visual_source')=='upload' else unit['description']
                    prompt+=f"\nAnimation style: {d['style']}. Character/object bible: {identity}. Reference purposes: {purposes}."
                    if d.get('video_reference_instructions'):
                        prompt+='\nUSER REQUESTED CHARACTER REPLACEMENTS: '+d['video_reference_instructions']+'\nIdentify the requested subject from the attached photos by appearance and description. Attachment numbers are internal only; the user does not need to supply them. Create the requested replacement character while preserving the chosen style.'
                    prompt+='\nAPPROVED SCENE CONTEXT: '+json.dumps([
                        {'scene':s['id'],'action':s['action'],'visual':s['image_prompt']}
                        for s in d['script']['scenes'] if s['id']==number],ensure_ascii=False)
                    if d['route']=='seedance' and d.get('visual_source')!='upload':
                        from .seedance_prompts import ROLE as seedance_role
                        prompt+='\n'+seedance_role+f"\nThis image depicts ONLY asset {number}: {unit['name']}. Other assets in the bible are context; do not draw them. No labels or text."
                    prompt+='\nCURRENT TARGET: '+unit.get('name',str(number))+'. Render only this target when generating an asset. User photo replacement scope: '+d.get('video_reference_instructions','')
                    if d['route']=='omni' and number!=1 and d.get('images',{}).get('1') and not edits:
                        # Once frame 1 is approved, old image prompts/cast descriptions can
                        # contradict a user replacement. Use scene action, not stale looks.
                        prompt=(f'Render scene {number} in the approved style.\n'
                                'ACTION: '+unit.get('action','')+'\nCAMERA: '+unit.get('camera','')+'\n'
                                'CURRENT SCENE STAGING: '+unit.get('image_prompt','')+'\n'
                                'Use this staging for seated/standing pose, blocking and location; '
                                'ignore only stale identity/style details contradicted by the approved character frame.\n'
                                'Use the latest scene 1 for shared character identity. Preserve this '
                                'scene action and setting; do not repeat its pose or facial expression.\n'
                                'Other characters, if absent from scene 1: '+json.dumps([{'id':a['id'],'name':a['name']} for a in d['script']['assets']],ensure_ascii=False)+
                                '\nThat cast description applies ONLY to characters absent from the approved first frame.')
                    anatomy_repair=bool(edits and re.search(r'анатом|anatom|лишн.{0,24}(?:рук|ног|пальц)|extra.{0,24}(?:arm|hand|leg|finger)',edits.get(number,''),re.I))
                    used_refs=list(refs)
                    if d['route']=='omni' and number!=1 and not edits and d.get('images',{}).get('1'):
                        anchor=d['images']['1']['path']; used_refs=[anchor]+[r for r in used_refs if r!=anchor]
                    if d.get('recreate') and d['route']=='seedance' and d.get('visual_source')!='upload' and 'reference_photo_ids' in unit:
                        selected=unit.get('reference_photo_ids',[])
                        used_refs=[r['path'] for i,r in enumerate((r for r in d['refs'] if r['kind']=='photo'),1) if i in selected]
                        if not selected: prompt+='\nThis character has NO assigned user photo. Create a distinct identity according to this asset; do not copy another cast member.'
                    if edits:
                        used_refs=[d['images'][key]['path']]+used_refs
                        prompt=(f'Edit ONLY image 1, the current approved frame for target {number}.\n'
                                'LATEST REQUESTED CORRECTION: '+edits[number]+'\n'
                                'This correction overrides any conflicting original depiction. '
                                'Fix the actual visible defect, not just lighting or texture. '
                                'Preserve unrelated characters, identity and setting. '
                                'Allow the minimal pose, object orientation, occlusion or camera changes '
                                'needed to make the requested correction physically coherent. '
                                'Do not preserve an impossible geometry just to keep composition identical. '
                                'For screens, the display and keyboard face the user on the same side; '
                                'the outer lid has no duplicate display. If the camera sees the outer lid, '
                                'show the plain lid; change viewpoint only when seeing the screen is required. '
                                'Before returning, compare the requested change to the actual result.')
                    if number in regenerate_ids:
                        prompt=(f'Create a NEW replacement image for scene/target {number}. Render the scene anew, not a near-identical retouch.\n'
                                'LATEST REQUEST: '+edits[number]+'\n'
                                'APPROVED ACTION: '+unit.get('action','')+'\nAPPROVED CAMERA: '+unit.get('camera','')+'\n'
                                'Image 1 supplies the existing character and setting identity only. Do not copy its defective pose, anatomy or composition. '
                                'Rebuild a coherent pose and framing that visibly fulfils the request. Preserve the approved story, selected style and '
                                'unrequested identity traits; replace any explicitly requested traits. Original user photos define the corresponding identity '
                                'except where the latest request changes it. Produce a newly generated output file, never return an input image.')
                    if anatomy_repair:
                        used_refs=[d['images'][key]['path']]
                        prompt+='\nANATOMY REPAIR OVERRIDES THE OLD POSE: rebuild the affected character’s complete shoulder-to-hand anatomy, not a cosmetic rerender. Trace each arm from its shoulder through elbow to its single hand. Remove the entire extra limb and its sleeve/attachment, restoring the covered clothing/background. Repose both real arms if necessary to perform the requested tasks without an extra arm. Keep the face, hair, outfit design, product identity and cartoon style. Count this character separately from the product mascot and mirror reflections. Do not keep a defective old arm merely to preserve composition.'
                    if payload.get('sync_source'):
                        source=str(payload['sync_source']); source_path=d['images'][source]['path']
                        if source_path not in used_refs: used_refs.append(source_path)
                        prompt+=f'\nImage {used_refs.index(source_path)+1} is the UPDATED scene {source}: authoritative identity for this correction. Other frames cannot override it.'
                    if d['route']=='omni' and not anatomy_repair and number not in regenerate_ids:
                        for frame_key,frame in sorted(d['images'].items(),key=lambda item:int(item[0])):
                            if frame_key==key or frame['path'] in used_refs: continue
                            used_refs.append(frame['path'])
                            prompt+=f"\nReference image {len(used_refs)} is existing scene {frame_key}. Preserve its character designs and its background when the screenplay uses the same location. Do not copy that scene's camera, pose or character placement into this scene. Follow the current scene's action and camera. These older frames are supporting context only. The LATEST REQUESTED CORRECTION overrides any conflicting appearance in them. Do not undo a requested replacement to match older frames; preserve only unrelated details."
                    if self.c.mode=='codex':
                        with self.s.transaction(): self.s.emit(p['user_id'],tr(lang,'visual_working',number=number,count=total_visuals))
                    target=directory/f'image_{number}.png'
                    d['pending_generation']={'job':job['id'],'unit':number,'target':str(target)}
                    with self.s.transaction():
                        self.s.db.execute('UPDATE projects SET body=? WHERE id=? AND revision=?',(json.dumps(d,ensure_ascii=False),p['id'],job['revision']))
                    before=d['images'].get(key,{}).get('path')
                    path=self.provider.image(job,prompt,used_refs,target,d,number)
                    if edits and before and self.c.mode!='demo':
                        from .quality import review
                        try: assessment=review(self.provider,job,before,path,edits[number],d['style'])
                        except Exception: assessment={'matches_request':False,'note':'review_unavailable'}
                        d.setdefault('visual_reviews',{})[key]=assessment
                        if not assessment['matches_request']:
                            with self.s.transaction(): self.s.emit(p['user_id'],tr(lang,'visual_review_warning',number=number))
                    d.pop('pending_generation',None)
                    d['images'][key]={'path':path,'description':prompt}
                    # Commit each new frame and invalidate dependent media together.
                    if d['route']=='omni': d['videos'].pop(key,None)
                    else: d['videos']={}
                    for derived in ('assembled','final'): d.pop(derived,None)
                    d.setdefault('visual_completed',{}).setdefault(job['id'],[]).append(number)
                    with self.s.transaction():
                        current=self.s.project(p['id'])
                        if current['revision']!=job['revision'] or current['state']!='busy':
                            raise GenerationCancelled()
                        self.s.db.execute('UPDATE projects SET body=? WHERE id=? AND revision=?',
                                          (json.dumps(d,ensure_ascii=False),p['id'],job['revision']))
                        self.s.emit(p['user_id'],file=path,kind='photo',text=str(number))
                if payload.get('repair_scene'):
                    d['repair_visual_changed']=True
                    d['repair_visual_ids']=list(edits)
                    for changed in edits: d['videos'].pop(str(changed),None)
                    for key in ('assembled','final'): d.pop(key,None)
                    p['state']='repair_visual_ready'
                else:
                    if edits and d['route']=='omni':
                        for changed in edits: d['videos'].pop(str(changed),None)
                    else:
                        d['videos']={}
                    for key in ('assembled','final','video_edits','resume_scene_ids'): d.pop(key,None)
                    if payload.get('remaining'): d.pop('first_frame_pending',None)
                    p['state']='visuals'
                if payload.get('sync_source'):
                    d.pop('visual_sync_source',None); d.pop('visual_sync_return',None)
                elif edits and d['route']=='omni' and len(edits)==1 and len(d['images'])>1:
                    d['visual_sync_source']=next(iter(edits)); d['visual_sync_return']=p['state']; p['state']='visual_sync_choice'
            elif kind=='video':
                if self.c.mode!='demo' and d.get('audio_mode')!='none':
                    from .voices import ensure
                    ensure(self.provider,job,d)
                    with self.s.transaction():
                        self.s.db.execute('UPDATE projects SET body=? WHERE id=? AND revision=?',
                                          (json.dumps(d,ensure_ascii=False),p['id'],job['revision']))
                edits={x['id']:x['instruction'] for x in d.get('video_edits',[])}
                if d['route']=='omni':
                    for scene in d['script']['scenes']:
                        check_cancelled()
                        number=scene['id']; key=str(number)
                        if number in d.get('skipped_scenes',[]) and number not in edits: continue
                        targets=d.get('resume_scene_ids') or edits
                        if targets and number not in targets: continue
                        if key in d['videos'] and not targets: continue
                        active_scene=number
                        previous=d['videos'].get(key,{}).get('remote_id')
                        refs=[d['images'][key]['path']]
                        if number in edits:
                            d['skipped_scenes']=[n for n in d.get('skipped_scenes',[]) if n!=number]
                        delivery_edit=edits.get(number)
                        if delivery_edit and self.c.mode!='demo':
                            signature={'instruction':delivery_edit,'frame':refs[0]}
                            applied=d.setdefault('applied_scene_edits',{})
                            if applied.get(key)!=signature:
                                from .omni_edits import resolve
                                revised=resolve(self.provider,job,d,scene,delivery_edit,refs)
                                scene.update(revised)
                                applied[key]=signature
                            delivery_edit=None
                            # Save the actual revised scene even if the provider later rejects it.
                            with self.s.transaction():
                                current=self.s.project(p['id'])
                                if current['revision']!=job['revision'] or current['state']!='busy':
                                    raise GenerationCancelled()
                                self.s.db.execute('UPDATE projects SET body=? WHERE id=? AND revision=?',
                                                  (json.dumps(d,ensure_ascii=False),p['id'],job['revision']))
                        if self.c.mode!='demo':
                            from .omni_blueprint import compile_prompt
                            compile_prompt(self.provider,job,d,scene,refs)
                            with self.s.transaction():
                                current=self.s.project(p['id'])
                                if current['revision']!=job['revision'] or current['state']!='busy':
                                    raise GenerationCancelled()
                                self.s.db.execute('UPDATE projects SET body=? WHERE id=? AND revision=?',
                                                  (json.dumps(d,ensure_ascii=False),p['id'],job['revision']))
                        target=directory/f'scene_{number}.mp4'
                        d['pending_generation']={'job':job['id'],'unit':number,'target':str(target)}
                        with self.s.transaction():
                            self.s.db.execute('UPDATE projects SET body=? WHERE id=? AND revision=?',(json.dumps(d,ensure_ascii=False),p['id'],job['revision']))
                        result=self.provider.video(job,d,scene,refs,target,previous,delivery_edit)
                        # Do not silently cut dialogue to force an incorrect output into 10 seconds.
                        if abs(media.info(result['path'])['seconds']-10)>.55:
                            raise ValueError('duration_needs_review')
                        d.pop('pending_generation',None)
                        d['videos'][key]=result
                        if 'resume_scene_ids' in d:
                            d['resume_scene_ids']=[n for n in d['resume_scene_ids'] if n!=number]
                        d['video_edits']=[e for e in d.get('video_edits',[]) if e['id']!=number]
                        # Commit the clip and its delivery together before starting the next scene.
                        # Keep the revision: this is a checkpoint of the same running job.
                        with self.s.transaction():
                            current=self.s.project(p['id'])
                            if current['revision']!=job['revision'] or current['state']!='busy':
                                raise RuntimeError('job_version_conflict')
                            self.s.db.execute('UPDATE projects SET body=? WHERE id=? AND revision=?',
                                              (json.dumps(d,ensure_ascii=False),p['id'],job['revision']))
                            self.s.complete_unit(job,number)
                            self.s.emit(p['user_id'],text=tr(lang,'ready_scene',number=number),file=result['path'],kind='video')
                        active_scene=None
                    check_cancelled()
                    paths=[d['videos'][str(s['id'])]['path'] for s in d['script']['scenes'] if s['id'] not in d.get('skipped_scenes',[])]
                    d['assembled']=media.assemble(paths,directory/'assembled.mp4',d['ratio'])
                else:
                    from .seedance_prompts import references
                    refs,d['seedance_image_labels']=references(d)
                    if len(refs)>(30 if d['version']=='2.5' else 9): raise ValueError('too_many_model_references')
                    edit='\n'.join(f"Beat {k}: {v}" for k,v in edits.items()) if edits else None
                    d['pending_generation']={'job':job['id'],'unit':1,'target':str(directory/'seedance.mp4')}
                    with self.s.transaction():
                        self.s.db.execute('UPDATE projects SET body=? WHERE id=? AND revision=?',(json.dumps(d,ensure_ascii=False),p['id'],job['revision']))
                    result=self.provider.video(job,d,None,refs,directory/'seedance.mp4',edit=edit)
                    if abs(media.info(result['path'])['seconds']-d['seconds'])>.55: raise ValueError('duration_needs_review')
                    d.pop('pending_generation',None)
                    d['videos']={'1':result}; d['assembled']=result['path']
                    with self.s.transaction(): self.s.complete_unit(job,1)
                outputs.append((d['assembled'],'video',''))
                for key in ('video_edits','resume_scene_ids','error_scene_id','repair_scene','repair_origin','repair_return','video_edit_return','repair_visual_changed','error','safety_review','safety_review_note'): d.pop(key,None)
                p['state']='video'
            elif kind=='subtitles':
                # Legacy queued tasks complete without transcription or rendering.
                d['final']=d['assembled']; d.pop('caption_style',None)
                p['state']='finished'; outputs.append((d['final'],'video',''))
            elif kind=='analyze':
                d['analysis']=self.provider.analyze(job,d['source']['path'],lang)
                if d.get('omni_source_flow'):
                    d.update(route='omni',recreate=True,auto_duration=True,reference_video_planned=False)
                    d['brief']={'idea':d['analysis']['summary']}
                    d['refs']=[r for r in d['refs'] if r['kind']!='video']
                    p['state']='recreate_changes'
                elif d.get('seedance_source_flow'):
                    d['route']='seedance'; d['recreate']=True; d['auto_duration']=False
                    d['seconds']=30 if d['source']['seconds']>15 else 15
                    if d['seconds']==30: d['version']='2.5'
                    d['reference_video_planned']=False
                    d['brief']={'idea':d['analysis']['summary']}
                    d['refs']=[r for r in d['refs'] if r['kind']!='video']
                    p['state']='recreate_changes'
                else:
                    p['state']='recreate_audio' if d['analysis'].get('audio_unknown') else 'route'
            else: raise ValueError('unknown_job')
            with self.s.transaction():
                check_cancelled()
                current=self.s.project(p['id'])
                if current['revision']!=job['revision'] or current['state']!='busy':
                    raise RuntimeError('job_version_conflict')
                if second_script is not None:
                    child=self.s.new_project(p['user_id'],'seedance',d['lang'])
                    child['data']=copy.deepcopy(d)
                    child['data']['script']=second_script
                    if d.get('web_part_durations'):
                        child['data']['seconds']=d['web_part_durations'][1]
                        child['data']['seedance_mode']=str(child['data']['seconds'])
                    child['data']['part_number']=2; child['data']['previous_part']=p['id']
                    child['data'].pop('next_part',None)
                    child['state']='script'; self.s.save(child)
                    d['next_part']=child['id']
                    self.s.db.execute('UPDATE users SET current=? WHERE id=?',(p['id'],p['user_id']))
                self.s.settle(job,True)
                self.s.db.execute("UPDATE jobs SET status='succeeded' WHERE id=?",(job['id'],))
                self.s.save(p)
                for path,kind,label in outputs:
                    self.s.emit(p['user_id'],text=label,file=path,kind=kind)
                if notice: self.s.emit(p['user_id'],notice)
                self.engine.show(p)
        except GenerationCancelled:
            with self.s.transaction():
                self.s.db.execute("UPDATE jobs SET status='cancelled',error=NULL WHERE id=?",(job['id'],))
                if not self.s.unresolved_charge(job): self.s.settle(job,False)
                p['state']='cancelled'; self.s.save(p)
                for path,kind,label in outputs:
                    self.s.emit(p['user_id'],text=label,file=path,kind=kind)
                self.s.emit(p['user_id'],tr(lang,'generation_cancelled'))
        except Exception as exc:
            # Provider messages and credentials never go to Telegram or logs.
            safe_codes={'image_safety_rejected','invalid_voice_profile','seedance_prompt_too_long','seedance_dialogue_mismatch','recovery_pending','recovery_unavailable','visual_prompt_too_long','invalid_visual_prompt','omni_prompt_too_long','google_third_party_content','image_generation_no_result','google_minor_safety','seedance_real_person','seedance_ratio_constraint','invalid_edit_targets','invalid_timeline','invalid_assets','invalid_scenes','invalid_omni_duration',
                        'invalid_total_duration','duration_needs_review','too_many_model_references','reference_video_needs_public_media_url'}
            code=str(exc) if str(exc) in safe_codes else type(exc).__name__
            if code=='seedance_ratio_constraint': d['seedance_adaptive_ratio']=True
            if job['kind']=='video' and d['route']=='omni' and active_scene is not None:
                d['error_scene_id']=active_scene
            # Successful clips must be delivered even if a later clip fails.
            with self.s.transaction():
                for path,output_kind,label in outputs:
                    self.s.emit(p['user_id'],text=tr(lang,'ready_scene',number=label) if output_kind=='video' and label else label,file=path,kind=output_kind)
            # Errors use short templates, never a paid automatic story review.
            for key in ('safety_review','safety_review_note','safety_review_attempted'):
                d.pop(key,None)
            with self.s.transaction():
                self.s.db.execute("UPDATE jobs SET status='attention',error=? WHERE id=?",(code,job['id']))
                current=self.s.project(p['id'])
                if current['revision']==job['revision']:
                    # Keep partial artifacts and original versions for owner reconciliation.
                    p['state']='attention'; p['data']['error']=code; self.s.save(p)
                if not self.s.unresolved_charge(job): self.s.settle(job,False)
                self.engine.show(p)
        finally:
            done.set()
        return True
