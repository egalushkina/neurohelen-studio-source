import json
import copy
import math
import re
from .i18n import LANGUAGES, tr
import os

FIELDS = ['idea', 'purpose', 'hero', 'location', 'effects', 'audio', 'palette']
STYLES = ['3d', 'anime', '2d', 'clay', 'watercolor', 'realism']


class Engine:
    def __init__(self, store, config):
        self.s, self.c = store, config

    def message(self, uid, key, **kwargs):
        self.s.emit(uid, tr(self.s.user(uid)['lang'], key, **kwargs))

    def rejected_video(self, p):
        if p['data'].get('error')=='seedance_real_person': return False
        job=self.s.db.execute("SELECT id,status FROM jobs WHERE project_id=? AND kind='video' ORDER BY created DESC LIMIT 1",(p['id'],)).fetchone()
        if not job or job['status']!='attention': return False
        operations=self.s.db.execute('SELECT state,remote_id,usage FROM operations WHERE job_id=?',(job['id'],)).fetchall()
        if p['data'].get('error')=='seedance_ratio_constraint':
            return bool(operations) and all(x['state']=='failed' and json.loads(x['usage']).get('provider_status')=='InvalidParameter.TaskTypeConstraint' for x in operations)
        return bool(operations) and all(x['state']=='rejected' and not x['remote_id'] for x in operations)

    def home(self, uid, welcome=False):
        from .catalog import price_guide
        lang = self.s.user(uid)['lang']
        self.s.db.execute('UPDATE users SET current=NULL WHERE id=?',(uid,))
        if welcome:
            self.s.emit(uid,tr(lang,'welcome')+'\n\n'+price_guide(lang,self.c.token_prices),navigation=True)
        self.s.emit(uid, tr(lang,'home')+('' if welcome else '\n\n'+price_guide(lang,self.c.token_prices)),
                    [[('Omni Flash', 'new:omni')], [('Seedance', 'new:seedance')],
                     [(tr(lang, 'from_script'), 'script_menu')],
                     [(tr(lang, 'create_photo'), 'new:photo')],
                     [(tr(lang, 'projects'), 'projects'), (tr(lang, 'balance'), 'balance')],
                     [(tr(lang,'buy_tokens'),'token_shop')],
                     [(tr(lang, 'language'), 'language')],
                     [(tr(lang, 'support_button'), 'https://t.me/neurohelen_pro')]]+
                    ([[('📊 Статистика','stats:overview:all')]] if uid in self.c.admins else []))

    def show(self, p):
        uid, d, state = p['user_id'], p['data'], p['state']
        if state=='references' and d.get('visual_source')=='upload' and all(str(x['id']) in d['images'] for x in d['script']['scenes']):
            d['refs']=[r for r in d['refs'] if r['kind']!='photo']
            d['refs_ready']=True; p['state']=state='visuals'
            self.s.save(p)
        lang = self.s.user(uid)['lang']
        def button(key, action=None, raw=False):
            return (key if raw else tr(lang, key), f"p:{p['id']}:{p['revision']}:{action or key}")
        rows, text = [], ''
        if state=='photo_prompt':
            text=tr(lang,'photo_prompt'); rows=[[button('add_photo_refs','add_photo_refs')]]
        elif state=='photo_refs':
            text=tr(lang,'photo_refs'); rows=[[button('done')]] if d['refs'] else [[button('create_own','no_refs')]]
        elif state=='photo_ratio':
            text=tr(lang,'ratio'); rows=[[button(x,'photo_ratio='+x,True)] for x in ('1:1','9:16','16:9')]
        elif state=='photo_result':
            text=tr(lang,'photo_ready'); rows=[[button('approve','photo_approve'),button('edit','photo_edit')]]
        elif state=='photo_edit':
            text=tr(lang,'photo_edit'); rows=[[button('approve_current','photo_approve')]]
        elif state=='photo_finished':
            text=tr(lang,'photo_saved'); rows=[[button('done','photo_download')],[(tr(lang,'create_photo'),'new:photo')]]
        if state.startswith('photo_'):
            self.s.emit(uid,text,rows); return
        if state=='video_language' and d['route']=='seedance':
            p['state']='seedance_audio'; self.s.save(p); return self.show(p)
        if state=='seedance_audio':
            text=tr(lang,'seedance_audio'); rows=[[button('russian_voice','audio=ru')],[button('ukrainian_voice','audio=uk')],[button('english_voice','audio=en')],[button('no_voice','audio=none')],[button('switch','audio=omni')]]
        elif state in ('version','video_version'):
            from .catalog import seedance_tokens
            text=tr(lang,'seedance_without_reference',seconds=d['seconds'])
            versions=[('2.5','2.5')] if d.get('seconds')==30 else [('2.5','2.5'),('2.0','2'),('2.0-fast','2 Fast')]
            if d.get('seconds')==30: text+='\n'+tr(lang,'seedance_30_only')
            rows=[[button(f"Seedance {label} · {tr(lang,'price_tokens',tokens=seedance_tokens(d,version,self.c.token_prices))}",'version='+version,True)] for version,label in versions]
        elif state=='seedance_length':
            text=tr(lang,'seedance_length')+'\n\n'+d.get('duration_reason','')
            rows=[[button('seedance_one30','length=30')],[button('seedance_two15','length=split')],[button('seedance_short15','length=15')]]
        elif state == 'video_language':
            from .catalog import omni_tokens
            text = tr(lang, 'omni_scene_price',tokens=omni_tokens(self.c.token_prices))+'\n\n'+tr(lang,state)
            rows = [[button(tr(lang, 'language_' + code), 'vlang=' + code, True)] for code, name in LANGUAGES.items()]
        elif state == 'duration':
            # Compatibility for projects created before script-led duration.
            p['state']='intake'; d['auto_duration']=d['route']=='omni'
            self.s.save(p); return self.show(p)
        elif state == 'ratio':
            text = tr(lang, state); rows = [[button(x, 'ratio=' + x, True) for x in ('9:16','16:9')]]
        elif state=='video_revision_review':
            text=tr(lang,'video_revision_review')+'\n\n'+'\n\n'.join(
                f"{x['id']}. {x['start']}–{x['end']} s\n{x['action']}\n{x['dialogue']}\n🎥 {x['camera']}"
                for x in d['pending_video_revision']['scenes'] if x['id'] in {e['id'] for e in d.get('video_edits',[])})
            rows=[[button('approve','accept_video_revision')],[button('dialogue_keep','reject_video_revision')]]
        elif state == 'dialogue_proposal':
            proposal=d['dialogue_proposal']
            text=tr(lang,'dialogue_proposal_text',number=proposal['id'],original=proposal['original'],proposed=proposal['proposed'])
            rows=[[button('dialogue_apply','dialogue_apply')],[button('dialogue_keep','dialogue_keep')],[button('dialogue_edit','dialogue_edit')]]
            if proposal.get('failed'):
                text=tr(lang,'dialogue_proposal_failed',number=proposal['id'])
                rows=[[button('dialogue_retry','dialogue_retry')],[button('dialogue_keep','dialogue_keep')]]
        elif state == 'script_summary':
            summary=d['script_summary']
            price=tr(lang,'price_pending') if summary['tokens'] is None else tr(lang,'price_tokens',tokens=summary['tokens'])
            text=tr(lang,'script_summary',seconds=summary['seconds'],price=price)
            rows=[[button('continue','continue_visuals'),button('cancel','cancel_visuals')]]
        elif state == 'intake':
            text = tr(lang, 'omni_idea' if d['route']=='omni' else 'sound_only_question' if self.next_field(d)=='audio' and d.get('audio_mode')=='none' else self.next_field(d))
            rows = [] if d['route']=='omni' else [[button('own_choice', 'skip')]]
            if d['route']=='omni':
                rows.append([button('have_script','have_script')])
            elif self.next_field(d)=='hero':
                rows=[[button('hero_create','skip')],[button('hero_upload','hero_photo')]]
        elif state == 'lip':
            text = tr(lang, 'lip'); rows = [[button('yes', 'lip=1'), button('no', 'lip=0')]]
        elif state == 'lip_advice':
            text = tr(lang, state); rows = [[button('switch'), button('stay')]]
        elif state == 'style':
            text = tr(lang, state); rows = [[button(x, 'style=' + x)] for x in STYLES]
            rows.append([button('style_reference','style=reference')])
            if (d['route']=='omni' or d.get('references_before_script')) and d.get('refs_ready'):
                rows.append([button('back','style_refs_back')])
        elif state=='style_reference_upload':
            text=tr(lang,'style_reference_upload'); rows=[[button('back','style_back')]]
        elif state=='style_reference_review':
            text=tr(lang,'style_reference_review',summary=d['style_profile']['summary'])
            rows=[[button('approve','style_reference_approve')],[button('edit','style_reference_replace')],[button('back','style_back')]]
        elif state == 'visual_source':
            text=tr(lang,'visual_source_question'); rows=[[button('have_visuals','visual_source=upload')],[button('create_own','visual_source=create')]]
        elif state == 'scene_upload':
            scene=next(x for x in d['script']['scenes'] if str(x['id']) not in d['images'])
            text=tr(lang,'scene_upload_question',number=scene['id'],count=len(d['script']['scenes']),description=scene['action'])
            rows=[[button('back','scene_upload_back')]]
        elif state == 'reference_assignment':
            assigned={i for a in d['script']['assets'] for i in a.get('reference_photo_ids',[])}
            photos=[r for r in d['refs'] if r['kind']=='photo']
            number=next(i for i in range(1,len(photos)+1) if i not in assigned)
            text=tr(lang,'reference_assignment',number=number)
            rows=[[button(a['name'],'assign_reference='+str(a['id']),True)] for a in d['script']['assets']]
        elif state in ('seedance_source','omni_source'):
            text=tr(lang,'seedance_source_question'); rows=[[button('yes','source=yes')],[button('no','source=no')]]
        elif state == 'seedance_source_mode':
            p['state']='seedance_source_upload'; d['seedance_reference_mode']='analysis'; self.s.save(p); return self.show(p)
        elif state == 'seedance_reference_length':
            p['state']='seedance_source_upload'; d['seedance_reference_mode']='analysis'; self.s.save(p); return self.show(p)
        elif state in ('seedance_source_upload','omni_source_upload'):
            text=tr(lang,'omni_reference_upload' if state=='omni_source_upload' else 'seedance_analysis_upload'); rows=[[button('continue_without_video_analysis','source=no')]]
        elif state == 'omni_source_dialogue':
            transcript=(d.get('analysis',{}).get('transcript') or '').strip()
            text=tr(lang,'omni_source_dialogue')
            if transcript:
                text+='\n\n'+tr(lang,'omni_source_transcript',text=transcript[:3500])
                rows=[[button('source_use_transcript','source_dialogue=recognized')],
                      [button('source_edit_dialogue','source_dialogue=edit')],
                      [button('recreate_no_dialogue','source_dialogue=none')]]
            else:
                text+='\n\n'+tr(lang,'omni_source_dialogue_edit')
                rows=[[button('source_edit_dialogue','source_dialogue=edit')],
                      [button('recreate_no_dialogue','source_dialogue=none')]]
        elif state == 'omni_source_dialogue_edit':
            transcript=(d.get('analysis',{}).get('transcript') or '').strip()
            text=tr(lang,'omni_source_dialogue_edit')
            if transcript: text+='\n\n'+tr(lang,'omni_source_transcript',text=transcript[:3500])
            rows=[[button('recreate_no_dialogue','source_dialogue=none')]]
        elif state == 'recreate_changes':
            text=(d.get('analysis',{}).get('summary','')+'\n\n' if d.get('omni_source_flow') else '')+tr(lang,'recreate_changes')
            if d.get('omni_source_flow') and (d.get('analysis',{}).get('transcript') or '').strip():
                text+='\n\n'+tr(lang,'omni_source_transcript',text=d['analysis']['transcript'].strip()[:3500])
            rows=[[button('recreate_keep','recreate_keep')]]
        elif state == 'references':
            text = tr(lang, 'video_reference_question' if d.get('video_ref_question_pending') else 'references_video_received' if any(x['kind']=='video' for x in d['refs']) else state); rows = [[button('done')]] if d['refs'] else [[button('create_own','no_refs')]]
            if d.get('recreate'):
                text=tr(lang,'recreate_photos')
            elif d.get('seedance_source_selected'):
                text=tr(lang,'scene_photo_references')
            if d.get('references_before_script'):
                text=tr(lang,'references_before_script')
            if not d.get('visual_source') and not d.get('recreate') and not d.get('references_before_script'):
                rows.append([button('have_visuals','visual_source=upload')])
        elif state == 'paste_script':
            text=tr(lang,'paste_script_selected' if d.get('script_route_selected') else 'paste_script')
            rows=[[(tr(lang,'choose_my_projects'),'scripts:0')]]
        elif state == 'supplied_review':
            text=tr(lang,'supplied_accepted')+'\n\n'+d['supplied_script']
            rows=[[button('prepare_scenes','prepare_supplied')],[button('edit','replace_supplied')]]
        elif state == 'script_route':
            text=tr(lang,'script_route'); rows=[[button('Omni Flash','script_route=omni',True)],[button('Seedance','script_route=seedance',True)]]
        elif state == 'upload':
            text = tr(lang, state)
        elif state=='recreate_audio':
            text=d['analysis']['summary']+'\n\n'+tr(lang,'recreate_audio_question')
            rows=[[button('recreate_dialogue','recreate_audio=dialogue')],[button('recreate_no_dialogue','recreate_audio=none')]]
        elif state == 'route':
            text = tr(lang, state, summary=d['analysis']['summary'])
            rows = [[button('approve')], [button('Omni Flash', 'route=omni', True), button('Seedance', 'route=seedance', True)]]
        elif state == 'script':
            script = d['script']
            text = script['title'] + '\n' + tr(lang,'script_length',seconds=d['seconds'],count=len(script['scenes'])) + '\n\n' + '\n\n'.join(
                f"{x['id']}. {x['start']}–{x['end']} s\n{x['action']}\n{x['dialogue']}\n🎥 {x['camera']}"
                for x in script['scenes'])
            if d.get('part_number'): text=tr(lang,'seedance_part',number=d['part_number'])+'\n\n'+text
            rows = [[button('approve'), button('edit')]]
            if d['route'] == 'omni':
                rows.append([button('another')])
        elif state in ('visuals', 'video'):
            first=state=='visuals' and d.get('first_frame_pending')
            text = tr(lang, 'first_frame_ready' if first else 'visuals' if state == 'visuals' else 'video_ready')
            rows = [[button('approve_continue' if first else 'approve','approve'), button('edit')]]
        elif state=='video_edit_choice':
            text=tr(lang,'video_edit_choice')
            rows=[[button('edit_video_content','edit_video_content')],[button('edit_visual_content','edit_visual_content')],[button('back','video_edit_back')]]
        elif state=='video_visual_scene':
            text=tr(lang,'choose_visual_scene')
            rows=[[button(tr(lang,'ready_scene',number=x['id']),'visual_scene='+str(x['id']),True)] for x in d['script']['scenes'] if x['id'] not in d.get('skipped_scenes',[])]
            rows.append([button('back','video_edit_back')])
        elif state == 'edit_input':
            text = d.get('edit_clarification') or tr(lang, 'first_frame_edit' if d.get('first_frame_pending') and d['edit_target']=='visuals' else 'script_edit_question' if d['edit_target']=='script' else 'edit_question')
            rows=[[button('approve_current','approve_current')],[button('back','cancel_edit')]]
            if d.get('repair_scene'):
                text=tr(lang,'edit_one_scene_question',number=d['repair_scene'])
                rows=[[button('back','repair_cancel')]]
        elif state == 'edit_confirm':
            text = tr(lang, 'edit_plan', plan='\n'.join(f"{x['id']}: {x['instruction']}" for x in d['edits']))
            rows = [[button('apply'), button('back')]]
            if not d.get('repair_scene'): rows.append([button('approve_current','approve_current')])
        elif state == 'quote':
            _, seconds = self.quote(d)
            model='Omni Flash' if d['route']=='omni' else 'Seedance '+d['version']
            from .catalog import seedance_tokens, omni_tokens
            rate=omni_tokens(self.c.token_prices) if d['route']=='omni' else seedance_tokens(d,d['version'],self.c.token_prices)
            tokens=None if rate is None else rate*(seconds//10 if d['route']=='omni' else 1)
            price=tr(lang,'price_pending') if tokens is None else tr(lang,'price_tokens',tokens=tokens)
            text=tr(lang,'omni_video_confirmation',count=seconds//10,seconds=seconds,rate=rate,tokens=tokens) if d['route']=='omni' else tr(lang,'video_confirmation',model=model,seconds=seconds,price=price)
            if d['route']=='seedance' and d.get('seedance_source_flow'):
                text+='\n\n'+tr(lang,'seedance_analysis_only')
            from .omni_prompts import crowded_dialogue_scenes
            crowded=crowded_dialogue_scenes(d)
            if crowded: text+='\n\n'+tr(lang,'dialogue_timing_warning',numbers=', '.join(map(str,crowded)))
            if d['route']=='seedance' and d.get('video_edits'):
                text+='\n\n'+tr(lang,'seedance_edit_note',seconds=d['seconds'])
            from .omni_vertex import configured as omni_configured
            if self.c.mode=='codex' and not (omni_configured() if d['route']=='omni' else os.getenv('BYTEPLUS_API_KEY')):
                text+='\n\n'+tr(lang,'video_not_ready')
            if d.get('skipped_scenes'): text+='\n\n'+tr(lang,'skipped_scene_list',numbers=', '.join(map(str,d['skipped_scenes'])))
            rows = [[button(tr(lang,'confirm_video_tokens',tokens=tokens),'generate',True), button('back')],[button('edit','edit')]]
        elif state == 'subtitles':
            # Resume projects created before subtitle support was removed.
            self.finish_video(p)
            return self.show(p)
        elif state == 'finished':
            text = tr(lang, state)
            if d.get('reels_text') and d.get('reels_text_video')==d.get('final'):
                text+='\n\n'+tr(lang,'reels_text_ready')+'\n'+d['reels_text']
            elif d.get('reels_text_failed'): text+='\n\n'+tr(lang,'reels_text_failed')
            rows = [[button('done', 'download')], [(tr(lang,'new'),'home')]]
            if d.get('reels_text_failed'): rows.insert(0,[button('reels_text_retry','reels_text_retry')])
            if d.get('next_part'): rows.insert(0,[(tr(lang,'seedance_next_part'),'open:'+d['next_part'])])
        elif state == 'busy':
            active=self.s.db.execute("SELECT kind FROM jobs WHERE project_id=? AND status IN ('queued','running') ORDER BY created DESC LIMIT 1",(p['id'],)).fetchone()
            if active and active['kind']=='reels_text':
                self.s.emit(uid,tr(lang,'reels_text_working')); return
            text = tr(lang, 'recovery_checking' if active and active['kind']=='recover' else ('supplied_working' if d.get('return_state')=='supplied_review' else state))
        elif state == 'cancelled':
            text=tr(lang,'generation_cancelled')
        elif state=='skip_scene_confirm':
            text=tr(lang,'skip_scene_question',number=d['error_scene_id'])
            rows=[[button('continue','skip_scene_apply')],[button('back','repair_cancel')]]
        elif state=='repair_visual':
            text=tr(lang,'repair_visual_question',number=d['repair_scene'])
            rows=[[button('back','repair_cancel')]]
        elif state=='visual_sync_choice':
            text=tr(lang,'visual_sync_question',number=d['visual_sync_source'])
            rows=[[button('visual_sync_all','visual_sync_all')],[button('visual_sync_only','visual_sync_only')]]
        elif state=='repair_visual_ready':
            text=tr(lang,'repair_visual_ready',number=', '.join(map(str,d.get('repair_visual_ids') or [d['repair_scene']])))
            rows=[[button('approve','repair_visual_approve')],[button('edit','error_edit_visual')]]
            if d.get('repair_origin')!='video': rows.append([button('back','repair_cancel')])
        elif state=='resume_ready':
            text=tr(lang,'resume_ready')
            rows=[[button('continue','resume_generation')]]
        elif state=='retry_unknown_confirm':
            text=tr(lang,'retry_unknown_warning')
            rows=[[button('continue','retry_unknown_apply')],[button('back','retry_unknown_back')]]
        elif state == 'attention':
            text = tr(lang, 'failed', code=d.get('error','unknown'))
            if d.get('error')=='google_minor_safety': text=tr(lang,'google_minor_safety')+'\n\n'+text
            if d.get('error_scene_id'):
                ready=', '.join(k for k in sorted(d.get('videos',{}),key=int) if int(k) not in d.get('resume_scene_ids',[])) or '—'
                waiting=', '.join(str(x['id']) for x in d['script']['scenes'] if (str(x['id']) not in d['videos'] or x['id'] in d.get('resume_scene_ids',[])) and x['id']!=d['error_scene_id'] and x['id'] not in d.get('skipped_scenes',[])) or '—'
                text=tr(lang,'partial_video',number=d['error_scene_id'],ready=ready,waiting=waiting)
                if d.get('error')=='google_minor_safety': text=tr(lang,'blocked_scene',number=d['error_scene_id'])+'\n'+tr(lang,'try_visual_change')+'\n\n'+text
            if d.get('error') in ('seedance_prompt_too_long','seedance_dialogue_mismatch','recovery_pending','recovery_unavailable'):
                text=tr(lang,d['error'])
            if d.get('error')=='image_safety_rejected': text=tr(lang,'image_safety_rejected')
            if d.get('error')=='visual_prompt_too_long': text=tr(lang,'visual_prompt_too_long')+'\n\n'+text
            if d.get('error')=='omni_prompt_too_long': text=tr(lang,'omni_prompt_too_long')+'\n\n'+text
            rows=[[(tr(lang,'projects'),'projects')]]
            if d.get('error') in ('seedance_dialogue_mismatch','seedance_prompt_too_long'):
                rows.insert(0,[button('continue','dialogue_retry_quote')])
            else: rows.insert(0,[button('recover_check','recover_check')])
            if d.get('error')=='recovery_pending':
                rows.insert(1,[button('retry_unknown','retry_unknown')])
            if self.rejected_video(p):
                rows.insert(0,[button('retry_video_quote','retry_video_quote')])
            if d.get('error')=='seedance_ratio_constraint':
                text=tr(lang,'seedance_ratio_constraint')
            elif d.get('error')=='seedance_real_person':
                text=tr(lang,'seedance_real_person')
                rows.insert(0,[button('edit_visual_content','privacy_edit_visual')])
            elif d.get('error')=='reference_video_needs_public_media_url':
                text=tr(lang,'seedance_analysis_only')
            elif d.get('error_scene_id'):
                rows.insert(0,[button(tr(lang,'edit_one_scene',number=d['error_scene_id']),'error_edit_scene',True)])
                rows.insert(1,[button('change_character' if d.get('error')=='google_third_party_content' else 'edit_scene_visual','error_edit_visual')])
                rows.insert(2,[button('skip_scene','skip_scene')])
                if d.get('videos'): rows.append([button('get_ready_scenes')])
            elif d.get('script',{}).get('scenes'):
                rows.insert(0,[button('edit_script_button','error_edit_script')])
            if d.get('script') and d.get('images'):
                rows.append([button('back','attention_back')])
            if d.get('error')=='google_third_party_content':
                text=tr(lang,'google_third_party_content')+'\n\n'+text
        if state in ('video','finished') and d.get('skipped_scenes'):
            text+='\n\n'+tr(lang,'skipped_scene_list',numbers=', '.join(map(str,d['skipped_scenes'])))
        self.s.emit(uid, text, rows)

    @staticmethod
    def supplied_script_intent(text):
        # A pasted timed screenplay is already a script, not a new story idea.
        if re.search(r'(?m)^\s*(?:\d+[.)]\s*)?\d+\s*[-–—]\s*\d+\s*(?:sec|s\b|сек|с\b)',text,re.I):
            return True,text.strip()
        # Match an explicit navigation request, not a story mentioning a script.
        pattern = (r"(?:у меня (?:уже )?(?:есть (?:готовый )?сценарий|(?:сценарий (?:уже )?готов))|"
                   r"у мене (?:вже )?(?:є (?:готовий )?сценарій|сценарій (?:вже )?готовий)|"
                   r"i (?:already )?have (?:a |the |my )?(?:finished |ready |complete )?(?:script|screenplay))"
                   r"[.!? ]*(?:(?::|\n)\s*(.*))?")
        match=re.fullmatch(pattern,text.strip(),re.IGNORECASE|re.DOTALL)
        return (match is not None, (match.group(1) or '').strip() if match else '')

    def finish_video(self, p):
        p['data']['final']=p['data']['assembled']
        p['data'].pop('caption_style',None)
        p['state']='finished'
        self.s.emit(p['user_id'],file=p['data']['final'],kind='video')
        d=p['data']
        if d.get('reels_text_video')!=d['final'] or not d.get('reels_text'):
            d.pop('reels_text_failed',None)
            self.s.enqueue(p,'reels_text')
        else: self.s.save(p)

    @staticmethod
    def next_field(d):
        if d['route']=='omni': return 'idea' if not d['brief'].get('idea') else None
        return next((x for x in FIELDS if not d['brief'].get(x)), None)

    def quote(self,d):
        from .catalog import video_quote
        return video_quote(d,self.c.token_prices)

    def queue(self, p, kind, payload=None, reserve=0):
        from .access import allowed
        if kind!='recover' and not allowed(self.s,self.c,p['user_id'],reserve):
            lang=self.s.user(p['user_id'])['lang']
            self.s.emit(p['user_id'],tr(lang,'purchase_required'),[[(tr(lang,'buy_tokens'),'token_shop')]])
            return
        if kind=='video' and self.c.mode!='demo' and p['data'].get('video_edits'):
            self.queue(p,'prepare_video_revision'); return
        if kind=='video':
            from .dialogue_review import pending_scene
            scene=pending_scene(p['data'])
            if scene:
                self.queue(p,'dialogue_proposal',{'scene_id':scene['id'],'return_state':'quote'}); return
        if kind=='script' and p['data']['route']=='seedance' and not p['data'].get('refs_ready') and not p['data'].get('supplied_script'):
            p['data']['references_before_script']=True
            p['state']='references'; self.s.save(p); self.show(p); return
        if self.c.mode=='codex':
            from .omni_vertex import configured as omni_configured
            needed = ['BYTEPLUS_API_KEY'] if kind=='video' and p['data']['route']!='omni' else []
            if any(not os.getenv(key) for key in needed) or (kind=='video' and p['data']['route']=='omni' and not omni_configured()):
                self.s.save(p)
                self.message(p['user_id'],'media_not_connected')
                self.show(p)
                return
        p['data']['max_seconds']=self.c.max_seconds
        if reserve and kind=='video':
            payload=dict(payload or {})
            from .catalog import omni_tokens
            payload['unit_price']=reserve if p['data']['route']!='omni' else omni_tokens(self.c.token_prices)
        self.s.enqueue(p, kind, payload, reserve)
        self.show(p)

    def handle(self, event):
        uid = event['user_id']
        with self.s.transaction():
            if self.s.db.execute('SELECT 1 FROM updates WHERE id=?', (event['id'],)).fetchone():
                return
            self.s.db.execute('INSERT INTO updates VALUES (?)', (event['id'],))
            user = self.s.user(uid, event.get('language'))
            if text_link := event.get('text','').strip():
                if text_link.startswith('/start link_'):
                    if not self.s.wallet:
                        self.s.emit(uid,'Привязка пока недоступна.');return
                    try:
                        token=text_link.split('link_',1)[1]
                        label=self.s.wallet.preview(token)
                        self.s.emit(uid,f'Связать этот Telegram с кабинетом {label} на studio.neurohelen.com? Подтверждайте только свой кабинет. Остатки токенов объединятся, покупки и списания будут общими.',[[('Это мой кабинет · подтвердить','wallet_link:'+token)],[('Отмена','menu')]])
                    except ValueError as exc:self.s.emit(uid,str(exc))
                    return
            if event.get('action','').startswith('wallet_link:'):
                if not self.s.wallet:self.s.emit(uid,'Привязка пока недоступна.');return
                try:
                    self.s.wallet.claim(uid,event['action'].split(':',1)[1])
                    self.s.emit(uid,f'Ваш Telegram ID: {uid}. Вернитесь на сайт и подтвердите привязку в своём кабинете.',[[('Открыть сайт','https://studio.neurohelen.com/?link_telegram=1')]])
                except ValueError as exc:self.s.emit(uid,str(exc))
                return
            if event.get('payment'):
                from .payments import complete
                complete(self.s,uid,event['payment']); return
            if self.c.mode in ('live','codex') and not self.c.public_access and uid not in self.c.admins:
                self.message(uid,'denied'); return
            action, text = event.get('action',''), event.get('text','').strip()
            from . import admin_stats
            admin_stats.touch(self.s,event)
            if action.startswith('stats:') or text=='/stats':
                admin_stats.handle(self,uid,action if action.startswith('stats:') else 'stats:overview:all');return
            if action.startswith(('keep_generation:','cancel_generation:')):
                verb,jid=action.split(':',1)
                row=self.s.db.execute("SELECT j.* FROM jobs j JOIN projects p ON p.id=j.project_id WHERE j.id=? AND p.user_id=?",(jid,uid)).fetchone()
                if not row: self.message(uid,'stale'); return
                job=dict(row); p=self.s.project(job['project_id'],uid)
                self.s.db.execute('UPDATE users SET current=? WHERE id=?',(p['id'],uid))
                if job['status'] not in ('queued','running'):
                    self.show(p); return
                if verb=='keep_generation':
                    self.show(p); return
                if job['status']=='queued':
                    self.s.db.execute("UPDATE jobs SET status='cancelled' WHERE id=?",(jid,))
                    self.s.settle(job,False)
                    p['state']='cancelled'; self.s.save(p)
                    self.message(uid,'generation_cancelled'); self.home(uid)
                else:
                    self.s.db.execute("UPDATE jobs SET error='cancel_requested' WHERE id=?",(jid,))
                    self.message(uid,'generation_cancelling')
                return
            if text in ('/start','/menu','/new') or action=='home' or text in {tr(l,'main_menu') for l in LANGUAGES}:
                job=self.s.db.execute("SELECT j.id,j.error FROM jobs j JOIN projects p ON p.id=j.project_id WHERE p.user_id=? AND j.status IN ('queued','running') ORDER BY j.created LIMIT 1",(uid,)).fetchone()
                if job:
                    if job['error']=='cancel_requested': self.message(uid,'generation_cancelling'); return
                    self.s.emit(uid,tr(user['lang'],'generation_leave_question'),[
                        [(tr(user['lang'],'generation_keep'),'keep_generation:'+job['id'])],
                        [(tr(user['lang'],'generation_cancel'),'cancel_generation:'+job['id'])]])
                    return
                self.home(uid,welcome=text=='/start'); return
            if action.startswith('resend:'):
                try: oid=int(action.split(':')[1])
                except ValueError: return
                row=self.s.db.execute('SELECT body,delivered FROM outbox WHERE id=? AND user_id=?',(oid,uid)).fetchone()
                if row and row['delivered']==-1 and json.loads(row['body']).get('file'):
                    self.s.db.execute('UPDATE outbox SET delivered=0,attempts=0,next_attempt=0,error=NULL WHERE id=?',(oid,))
                return
            if action == 'language' or text == '/language':
                self.s.emit(uid,tr(user['lang'],'choose_language'),
                            [[(v,'lang:'+k)] for k,v in LANGUAGES.items()] + [[(tr(user['lang'],'auto'),'lang:auto')]])
                return
            if action.startswith('lang:'):
                value = action.split(':')[1]
                if value not in (*LANGUAGES, 'auto'):
                    return
                from .i18n import locale
                self.s.db.execute('UPDATE users SET lang=?,manual=? WHERE id=?',
                                  (locale(event.get('language')) if value=='auto' else value, value!='auto', uid))
                self.home(uid); return
            if action.startswith('pay:'):
                from .payments import create_invoice
                parts=action.split(':')
                if len(parts)==2: create_invoice(self.s,self.c,uid,parts[1])
                elif len(parts)==3: create_invoice(self.s,self.c,uid,parts[2],parts[1])
                return
            if action=='token_shop' or action.startswith(('token_shop:', 'package:')):
                from .catalog import PACKAGES, number, package_usd
                lang=user['lang']
                if action=='token_shop':
                    buttons=[[(tr(lang,'shop_rf'),'token_shop:rf')],[(tr(lang,'shop_world'),'token_shop:world')]]
                    self.s.emit(uid,tr(lang,'shop_region'),buttons)
                elif action.startswith('token_shop:'):
                    region=action.split(':',1)[1]
                    if region not in ('rf','world'): self.message(uid,'invalid'); return
                    rows=[];listing=[]
                    for key,name,tokens,rubles in PACKAGES:
                        price=number(rubles)+' ₽' if region=='rf' else '$'+package_usd(key,rubles)
                        rows.append([(f"{tr(lang,name)} · {tr(lang,'price_tokens',tokens=number(tokens))} · {price}",'package:'+region+':'+key)])
                        listing.append(f"{tr(lang,name)}: {tr(lang,'price_tokens',tokens=number(tokens))} · {price}")
                    rows.append([(tr(lang,'back'),'token_shop')])
                    from .payments import ready
                    intro='Выберите пакет. Оплата разовая, токены начисляются после подтверждения платежа.' if ready(self.c,region) else tr(lang,'shop_intro')
                    self.s.emit(uid,intro+'\n\n'+'\n'.join(listing),rows)
                else:
                    parts=action.split(':')
                    region=parts[1] if len(parts)==3 else 'rf'
                    if region not in ('rf','world'): self.message(uid,'invalid'); return
                    chosen=next((p for p in PACKAGES if p[0]==parts[-1]),None)
                    if chosen is None: self.message(uid,'invalid'); return
                    key,name,tokens,rubles=chosen
                    text=tr(lang,'package_details',name=tr(lang,name),tokens=number(tokens),rubles=number(rubles),dollars=package_usd(key,rubles))
                    if len(parts)==3:
                        dual=number(rubles)+' ₽ / ≈ $'+package_usd(key,rubles)
                        text=text.replace(dual,number(rubles)+' ₽' if region=='rf' else '$'+package_usd(key,rubles))
                    from .payments import ready
                    buttons=[[(tr(lang,'back'),'token_shop:'+region)],[(tr(lang,'support_button'),'https://t.me/neurohelen_pro')]]
                    if ready(self.c,region):
                        price=number(rubles)+' ₽' if region=='rf' else '$'+package_usd(key,rubles)
                        text=f"{tr(lang,name)} · {number(tokens)} токенов · {price}\nРазовая покупка. Токены начислятся после оплаты."
                        buttons.insert(0,[(f'Оплатить {price}','pay:'+region+':'+chosen[0])])
                    self.s.emit(uid,text,buttons)
                return
            if action == 'balance' or text == '/balance':
                self.s.emit(uid,tr(user['lang'],'wallet',balance=user['balance'],reserved=user['reserved']),[[(tr(user['lang'],'buy_tokens'),'token_shop')]]); return
            if text in ('/support','/paysupport'):
                self.s.emit(uid,'По вопросам оплаты и начисления токенов напишите в поддержку.',[[(tr(user['lang'],'support_button'),'https://t.me/neurohelen_pro')]]); return
            if text in ('/help','/terms'):
                self.message(uid,'help'); return
            if action=='script_menu':
                self.s.emit(uid,tr(user['lang'],'script_menu'),[[(tr(user['lang'],'paste_own'),'paste_own')],[(tr(user['lang'],'saved_scripts'),'scripts:0')]])
                return
            if action=='paste_own':
                count=self.s.db.execute("SELECT count(*) FROM jobs j JOIN projects p ON j.project_id=p.id WHERE p.user_id=? AND j.status IN ('queued','running')",(uid,)).fetchone()[0]
                if count: self.message(uid,'busy'); return
                p=self.s.new_project(uid,'omni',user['lang']); p['state']='paste_script'
                self.s.save(p); self.show(p); return
            if action.startswith('scripts:'):
                try: page=max(0,int(action.split(':')[1]))
                except ValueError: return
                saved=[]
                for row in self.s.db.execute('SELECT id,revision,body FROM projects WHERE user_id=? ORDER BY created DESC',(uid,)):
                    body=json.loads(row['body'])
                    if body.get('script',{}).get('scenes'): saved.append((row,body))
                page=min(page,max(0,(len(saved)-1)//8))
                buttons=[]
                for row,body in saved[page*8:page*8+8]:
                    label=f"{body['script']['title'][:38]} · {body['seconds']} s"
                    buttons.append([(label,f"use_script:{row['id']}:{row['revision']}")])
                nav=[]
                if page: nav.append(('←',f'scripts:{page-1}'))
                if (page+1)*8<len(saved): nav.append(('→',f'scripts:{page+1}'))
                if nav: buttons.append(nav)
                self.s.emit(uid,tr(user['lang'],'choose_script' if saved else 'no_scripts'),buttons)
                return
            if action.startswith('use_script:'):
                bits=action.split(':')
                if len(bits)!=3: return
                source=self.s.project(bits[1],uid)
                if not source or str(source['revision'])!=bits[2]: self.message(uid,'stale'); return
                if not source['data'].get('script',{}).get('scenes'): self.message(uid,'no_scripts'); return
                count=self.s.db.execute("SELECT count(*) FROM jobs j JOIN projects p ON j.project_id=p.id WHERE p.user_id=? AND j.status IN ('queued','running')",(uid,)).fetchone()[0]
                if count: self.message(uid,'busy'); return
                # A fresh production preserves the saved script and any existing finished video.
                d=source['data']; p=self.s.new_project(uid,d['route'],d['lang'])
                for key in ('script','brief','seconds','version','lip','auto_duration','max_seconds','ratio','supplied_script','audio_mode'):
                    if key in d: p['data'][key]=copy.deepcopy(d[key])
                p['data']['source_project']=source['id']; p['state']='script'
                self.s.save(p); self.message(uid,'script_loaded'); self.show(p); return
            if action.startswith('archive:'):
                try: page=max(0,int(action.split(':')[1]))
                except ValueError: return
                rows=self.s.db.execute('SELECT id,body FROM projects WHERE user_id=? ORDER BY created DESC LIMIT 11 OFFSET ?',(uid,10+page*10)).fetchall()
                buttons=[[(json.loads(r['body']).get('script',{}).get('title',r['id'])[:50],'open:'+r['id'])] for r in rows[:10]]
                if page: buttons.append([('←','archive:'+str(page-1))])
                if len(rows)>10: buttons.append([('→','archive:'+str(page+1))])
                self.s.emit(uid,tr(user['lang'],'archive' if rows else 'archive_empty'),buttons); return
            if action == 'projects' or text == '/projects':
                rows = self.s.db.execute('SELECT id,body FROM projects WHERE user_id=? ORDER BY created DESC LIMIT 10',(uid,)).fetchall()
                self.s.emit(uid, tr(user['lang'],'projects' if rows else 'empty'),
                            [[((tr(user['lang'],'seedance_part',number=json.loads(x['body'])['part_number'])+' · ' if json.loads(x['body']).get('part_number') else '')+json.loads(x['body']).get('script',{}).get('title',x['id'])[:50],'open:'+x['id'])] for x in rows])
                if self.s.db.execute('SELECT count(*) FROM projects WHERE user_id=?',(uid,)).fetchone()[0]>10:
                    self.s.emit(uid,tr(user['lang'],'archive_explanation'),[[(tr(user['lang'],'archive'),'archive:0')]])
                return
            if action.startswith('open:'):
                p = self.s.project(action[5:], uid)
                if p:
                    if p['data'].get('previous_part') and not p['data'].get('part_refs_copied'):
                        parent=self.s.project(p['data']['previous_part'],uid)
                        if parent:
                            for key in ('refs','video_reference_instructions','reference_video_planned','ratio','style','version'):
                                if key in parent['data']: p['data'][key]=copy.deepcopy(parent['data'][key])
                            p['data']['part_refs_copied']=True
                            self.s.save(p)
                    self.s.db.execute('UPDATE users SET current=? WHERE id=?',(p['id'],uid)); self.show(p)
                return
            if action.startswith('new:'):
                route = action[4:]
                if route not in ('omni','seedance','recreate','photo'): return
                count = self.s.db.execute("SELECT count(*) FROM jobs j JOIN projects p ON j.project_id=p.id WHERE p.user_id=? AND j.status IN ('queued','running')",(uid,)).fetchone()[0]
                if count: self.message(uid,'busy'); return
                p=self.s.new_project(uid,route,user['lang'])
                if route=='photo':
                    p['state']='photo_prompt'; p['data']['style']='Follow the user requested photographic or illustration style'; self.s.save(p)
                self.show(p); return
            p = self.s.project(user['current'],uid) if user['current'] else None
            if action.startswith('p:'):
                bits = action.split(':',3)
                if len(bits)!=4: return
                p = self.s.project(bits[1],uid)
                unchanged=False
                if p and bits[3]=='approve' and p['state'] in ('edit_input','edit_confirm'):
                    old=self.s.db.execute('SELECT state,body FROM history WHERE project_id=? AND revision=?',(p['id'],bits[2])).fetchone()
                    target=p['data'].get('edit_target')
                    if old and old['state']==target and target in ('script','visuals','video'):
                        old_data=self.s.history_data(old['body'])
                        keys={'script':('script',),'visuals':('script','images'),'video':('script','videos','assembled')}[target]
                        unchanged=all(old_data.get(k)==p['data'].get(k) for k in keys)
                if not p or (str(p['revision'])!=bits[2] and not unchanged):
                    self.message(uid,'stale')
                    if p: self.show(p)
                    return
                action=bits[3]
                self.s.db.execute('UPDATE users SET current=? WHERE id=?',(p['id'],uid))
            if not p:
                self.home(uid); return
            d, state = p['data'], p['state']
            if state == 'busy': self.message(uid,'busy'); return
            if state in ('edit_input','edit_confirm') and d.get('edit_target')=='video' and d.get('video_edit_return')=='quote' and not d.get('repair_scene') and action in ('approve','approve_current','cancel_edit'):
                p['state']='quote'
                for key in ('edits','edit_history','edit_clarification','safety_pending_ids'): d.pop(key,None)
                self.s.save(p); self.show(p); return
            if state in ('edit_input','edit_confirm') and d.get('repair_scene') and action in ('approve','approve_current','cancel_edit'):
                p['state']='attention'; self.s.save(p); self.show(p); return
            if state in ('edit_input','edit_confirm') and action in ('approve','approve_current','cancel_edit'):
                state=d['edit_target']; p['state']=state
                for key in ('edit_history','edit_clarification','edits','edit_target'): d.pop(key,None)
                if action=='cancel_edit':
                    self.s.save(p); self.show(p); return
                action='approve'
            has_script, script_body=self.supplied_script_intent(text)
            recover_script=(state=='attention' and d.get('return_state')=='intake' and not d.get('script'))
            if (state=='intake' or recover_script) and (action=='have_script' or has_script):
                if action=='have_script':
                    d['script_route_selected']=True
                else:
                    d.pop('script_route_selected',None)
                d.pop('error',None)
                p['state']=state='paste_script'
                text=script_body
                if not text:
                    self.s.save(p); self.show(p); return
            changed = True
            if state=='seedance_length' and action in ('length=30','length=split','length=15'):
                d['seedance_mode']=action.split('=')[1]
                d['seconds']=15 if d['seedance_mode']=='15' else 30
                d['seedance_duration_chosen']=True
                if d['seedance_mode']=='30': d['version']='2.5'
                if d['seedance_mode']=='15': d['brief']['duration_instruction']='User approved shortening to 15 seconds: preserve the core idea and summarize secondary events.'
                self.queue(p,'script'); return
            elif state=='attention' and action in ('dialogue_retry_quote','recover_check') and d.get('error') in ('seedance_dialogue_mismatch','seedance_prompt_too_long'):
                p['state']='quote'
            elif state=='attention' and action=='recover_check':
                old=self.s.db.execute("SELECT id FROM jobs WHERE project_id=? AND kind!='recover' ORDER BY created DESC LIMIT 1",(p['id'],)).fetchone()
                if old:
                    d['recover_source']=old['id']
                    self.queue(p,'recover',{'source':old['id']}); return
            elif state=='attention' and action=='retry_unknown' and d.get('recover_source'):
                p['state']='retry_unknown_confirm'
            elif state=='retry_unknown_confirm' and action=='retry_unknown_back':
                p['state']='attention'
            elif state=='retry_unknown_confirm' and action=='retry_unknown_apply':
                old=self.s.db.execute('SELECT * FROM jobs WHERE id=? AND project_id=?',(d['recover_source'],p['id'])).fetchone()
                if old: self.s.settle(dict(old),False)
                d.pop('pending_generation',None); p['state']='resume_ready'
            elif state=='resume_ready' and action=='resume_generation':
                old=self.s.db.execute('SELECT * FROM jobs WHERE id=? AND project_id=?',(d.get('recover_source'),p['id'])).fetchone()
                if not old: self.message(uid,'stale'); return
                if old['kind']=='video':
                    # Keep replacements pending even when an older video exists.
                    completed={str(r['unit']) for r in self.s.db.execute('SELECT unit FROM generation_units WHERE job_id=?',(old['id'],))}
                    pending=d.get('pending_generation',{})
                    selected=set(d.get('resume_scene_ids',[]))
                    selected.update(e['id'] for e in d.get('video_edits',[]))
                    if pending.get('job')==old['id'] and pending.get('unit') is not None:
                        selected.add(int(pending['unit']))
                    d['resume_scene_ids']=sorted(n for n in selected if str(n) not in completed)
                    d['video_edits']=[e for e in d.get('video_edits',[]) if str(e['id']) not in completed]
                    p['state']='quote'
                else:
                    payload=json.loads(old['payload'])
                    payload['completed_ids']=sorted(set(payload.get('completed_ids',[])+d.get('visual_completed',{}).get(old['id'],[])))
                    self.queue(p,old['kind'],payload); return
            elif state=='attention' and action=='privacy_edit_visual' and d.get('error')=='seedance_real_person':
                d['edit_target']='visuals'; p['state']='edit_input'
            elif state=='attention' and action=='retry_video_quote' and self.rejected_video(p):
                d.pop('error',None)
                p['state']='quote'
            elif state=='attention' and action=='skip_scene' and d.get('error_scene_id'):
                d['repair_scene']=d['error_scene_id']; p['state']='skip_scene_confirm'
            elif state=='skip_scene_confirm' and action=='skip_scene_apply':
                d['skipped_scenes']=sorted(set(d.get('skipped_scenes',[])+[d['error_scene_id']]))
                d['resume_scene_ids']=[x['id'] for x in d['script']['scenes'] if (str(x['id']) not in d['videos'] or x['id'] in d.get('resume_scene_ids',[])) and x['id'] not in d['skipped_scenes']]
                d.pop('video_edits',None); d.pop('safety_pending_ids',None)
                if not d['resume_scene_ids']:
                    if any(int(k) not in d['skipped_scenes'] for k in d['videos']):
                        self.queue(p,'video'); return
                    p['state']='cancelled'
                else: p['state']='quote'
            elif state in ('attention','repair_visual_ready') and action=='error_edit_visual' and (d.get('repair_scene') or d.get('error_scene_id')):
                d['repair_scene']=d.get('repair_scene') or d['error_scene_id']; p['state']='repair_visual'
            elif state=='repair_visual' and event.get('media'):
                m=event['media']
                if m['kind']!='photo': self.message(uid,'photo_only'); return
                key=str(d['repair_scene'])
                d['images'][key]={'path':m['path'],'description':'User supplied replacement frame','user_supplied':True}
                d['videos'].pop(key,None); d['repair_visual_changed']=True; d['repair_visual_ids']=[d['repair_scene']]
                p['state']='repair_visual_ready'
                if d['route']=='omni' and len(d['images'])>1:
                    d['visual_sync_source']=d['repair_scene']; d['visual_sync_return']=p['state']; p['state']='visual_sync_choice'
                self.s.emit(uid,file=m['path'],kind='photo')
            elif state in ('repair_visual','repair_visual_ready') and text:
                from .visual_edits import needs_character_details
                if needs_character_details(text):
                    self.message(uid,'character_change_details'); return
                from .visual_edits import repair_edits
                try: changes=repair_edits(d,text)
                except ValueError:
                    self.message(uid,'visual_scene_numbers'); return
                self.queue(p,'edit_visuals',{'edits':changes,'repair_scene':d['repair_scene']}); return
            elif state=='visual_sync_choice' and action=='visual_sync_only':
                p['state']=d.pop('visual_sync_return','repair_visual_ready')
                d.pop('visual_sync_source',None)
            elif state=='visual_sync_choice' and action=='visual_sync_all':
                source=d['visual_sync_source']
                changes=[{'id':int(k),'instruction':f'Use updated scene {source} as the authority for the changed character identity. Apply the same character replacement wherever that character appears in this target. Preserve other distinct characters, target action, composition and location-specific clothing. Do not add the character to scenes where absent.'} for k in d['images'] if int(k)!=source]
                self.queue(p,'edit_visuals',{'edits':changes,'repair_scene':d.get('repair_scene'), 'sync_source':source}); return
            elif state=='repair_visual_ready' and action=='repair_visual_approve':
                d['resume_scene_ids']=[x['id'] for x in d['script']['scenes'] if (str(x['id']) not in d['videos'] or x['id']==d['repair_scene'] or x['id'] in d.get('resume_scene_ids',[])) and x['id'] not in d.get('skipped_scenes',[])]
                p['state']='quote'
            elif state=='attention' and action=='get_ready_scenes':
                for number,video in sorted(d.get('videos',{}).items(),key=lambda x:int(x[0])):
                    self.s.emit(uid,tr(user['lang'],'ready_scene',number=number),file=video['path'],kind='video')
                return
            elif state=='attention' and action=='error_edit_scene' and d.get('error_scene_id'):
                d['repair_scene']=d['error_scene_id']; d['video_edit_return']='attention'; p['state']='video_edit_choice'
                for key in ('edit_history','edit_clarification','edits'): d.pop(key,None)
            elif action=='repair_cancel' and d.get('repair_scene'):
                if d.get('repair_origin')=='video':
                    p['state']='repair_visual_ready' if d.get('repair_visual_changed') else d.get('repair_return','video')
                    if not d.get('repair_visual_changed'): d.pop('repair_scene',None); d.pop('repair_origin',None)
                else: p['state']='attention'; d.pop('repair_scene',None)
            elif state=='attention' and action=='attention_back' and d.get('script') and d.get('images'):
                p['state']='visuals'
            elif state=='attention' and action=='error_edit_script' and d.get('script',{}).get('scenes'):
                d['edit_target']='script'; p['state']='edit_input'
                for key in ('edit_history','edit_clarification','edits','safety_review','safety_review_note','safety_pending_ids'):
                    d.pop(key,None)
            elif state=='attention' and action=='safety_apply' and d.get('safety_review',{}).get('id')==d.get('error_scene_id') and d.get('safety_review'):
                review=d.pop('safety_review')
                d['edits']=[review]; d['edit_target']='video'
                d['safety_pending_ids']=[s['id'] for s in d['script']['scenes'] if s['id']==review['id'] or str(s['id']) not in d['videos']]
                p['state']='edit_confirm'
            elif state=='photo_prompt' and action=='add_photo_refs':
                p['state']='photo_refs'
            elif state=='photo_prompt' and event.get('media'):
                m=event['media']
                if m['kind']!='photo': self.message(uid,'photo_only'); return
                if len(d['refs'])>=5: self.message(uid,'ref_limit'); return
                m['purpose']=text[:1000]; d['refs'].append(m)
                p['state']='photo_refs'
            elif state=='photo_prompt' and text:
                if len(text)>6000: self.message(uid,'invalid'); return
                d['photo_prompt']=text; p['state']='photo_ratio' if d.get('photo_refs_done') else 'photo_refs'
            elif state=='photo_refs' and event.get('media'):
                m=event['media']
                if m['kind']!='photo': self.message(uid,'photo_only'); return
                if len(d['refs'])>=5: self.message(uid,'ref_limit'); return
                m['purpose']=text[:1000]; d['refs'].append(m)
            elif state=='photo_refs' and (action=='done' or (action=='no_refs' and not d['refs'])):
                d['photo_refs_done']=True
                p['state']='photo_ratio' if d.get('photo_prompt') else 'photo_prompt'
            elif state=='photo_ratio' and action in ('photo_ratio=1:1','photo_ratio=9:16','photo_ratio=16:9'):
                d['ratio']=action.split('=')[1]; self.queue(p,'photo_image'); return
            elif state=='photo_result' and action=='photo_edit':
                p['state']='photo_edit'
            elif state=='photo_edit' and text:
                self.queue(p,'photo_image',{'edit':text[:6000]}); return
            elif state in ('photo_result','photo_edit') and action=='photo_approve':
                p['state']='photo_finished'
            elif state=='photo_finished' and action=='photo_download':
                self.s.emit(uid,file=d['photo_path'],kind='photo'); return
            elif state=='seedance_audio' and action in ('audio=none','audio=ru','audio=uk','audio=en','audio=omni'):
                if action=='audio=omni':
                    d['route']='omni'; d['auto_duration']=True; d.pop('audio_mode',None); p['state']='video_language'
                else:
                    d['audio_mode']=action.split('=')[1]; d['lip']=d['audio_mode'] in ('ru','uk','en')
                    d['lang']=d['audio_mode'] if d['lip'] else user['lang']; p['state']='intake'
                    if d.get('recreate'): p['state']='recreate_changes'
                    elif d.get('supplied_script'): p['state']='supplied_review'
                    elif not d.get('seedance_source_selected'): p['state']='seedance_source'
                    elif not self.next_field(d): self.queue(p,'script'); return
            elif state in ('seedance_source','seedance_source_upload','omni_source','omni_source_upload') and action in ('source=yes','source=no'):
                d['seedance_source_selected']=True
                if d['route']=='omni': d['omni_source_selected']=True
                d['reference_video_planned']=False
                if action=='source=yes':
                    d[d['route']+'_source_flow']=True
                    if d['route']=='seedance': d['seedance_reference_mode']='analysis'
                    p['state']='seedance_source_upload' if d['route']=='seedance' else 'omni_source_upload'
                else:
                    d.pop('seedance_source_flow',None); d.pop('omni_source_flow',None); p['state']='intake'
            elif state=='seedance_source_mode' and action in ('source_mode=analysis','source_mode=direct'):
                d['seedance_reference_mode']='analysis'
                p['state']='seedance_source_upload'
            elif state=='paste_script' and text:
                if len(text)>12000: self.message(uid,'script_too_long_input'); return
                d['supplied_script']=text; d['brief']['idea']=text
                selected=d.pop('script_route_selected',False)
                if selected:
                    if d['route']=='seedance':
                        from .supplied_script import apply_seedance
                        apply_seedance(d)
                    else:
                        d['lip']=True
                    p['state']='supplied_review'
                else:
                    p['state']='script_route'
            elif state=='supplied_review' and action=='prepare_supplied':
                self.queue(p,'script'); return
            elif state=='supplied_review' and action=='replace_supplied':
                d['script_route_selected']=True; p['state']='paste_script'
            elif state=='script_route' and action in ('script_route=omni','script_route=seedance'):
                d['route']=action.split('=')[1]; d['seconds']=10 if d['route']=='omni' else 15
                if d['route']=='seedance':
                    from .supplied_script import apply_seedance
                    apply_seedance(d); p['state']='supplied_review'
                else: p['state']='video_language'
            elif state=='video_version' and action in ('version=2.0','version=2.5','version=2.0-fast'):
                if d.get('seconds')==30 and action!='version=2.5': self.message(uid,'seedance_30_only'); return
                d['version']=action[8:]; p['state']='quote'
            elif state == 'version' and action in ('version=2.0','version=2.5','version=2.0-fast'):
                d['version']=action[8:]; p['state']='seedance_audio' if d['route']=='seedance' else 'video_language'
            elif state == 'video_language' and action.startswith('vlang=') and action[6:] in LANGUAGES:
                d['lang']=action[6:]; d['auto_duration']=d['route']=='omni'; p['state']='intake'
                if d.get('recreate'):
                    p['state']='recreate_changes'; self.s.save(p); self.show(p); return
                if d.get('supplied_script'):
                    if d['route']=='omni':
                        d['lip']=True
                    p['state']='supplied_review'; self.s.save(p); self.show(p); return
                if d['route']=='omni' and not d.get('omni_source_selected'):
                    p['state']='omni_source'; self.s.save(p); self.show(p); return
                if not self.next_field(d):
                    if d['route']=='omni': self.queue(p,'script'); return
                    p['state']='lip'
            elif state == 'ratio' and action in ('ratio=9:16','ratio=16:9'):
                d['ratio']=action[6:]
                if d.get('recreate') and d.get('refs_ready') and d.get('style'):
                    self.queue(p,'visuals'); return
                if d.get('script_references_prepared') and d.get('refs_ready') and d.get('style'):
                    self.queue(p,'visuals'); return
                p['state']='visual_source' if d.get('supplied_script') or d.get('source_project') else 'references' if d['route']=='omni' else 'style'
            elif state in ('visual_source','references','style') and (action=='visual_source=upload' or re.fullmatch(r'у меня (?:уже )?есть (?:готовые? )?визуал[ы]?',text,re.I)):
                if d.get('visual_source')!='upload':
                    d['images']={}; d['videos']={}
                d['visual_source']='upload'
                d['refs']=[r for r in d['refs'] if r['kind']!='photo']
                d.pop('first_frame_pending',None)
                d['style']='Preserve the exact style of the user-supplied scene frames.'
                p['state']='scene_upload'
            elif state=='scene_upload' and action=='scene_upload_back':
                p['state']='visual_source'
            elif state=='visual_source' and action=='visual_source=create':
                if d.get('visual_source')=='upload':
                    d['images']={}; d['videos']={}
                    d.pop('style',None); d.pop('refs_ready',None)
                d['visual_source']='create'
                p['state']='references' if d['route']=='omni' else 'style'
            elif state=='scene_upload' and event.get('media'):
                m=event['media']
                if m['kind']!='photo': self.message(uid,'photo_only'); return
                scene=next(x for x in d['script']['scenes'] if str(x['id']) not in d['images'])
                d['images'][str(scene['id'])]={'path':m['path'],'description':scene['image_prompt'],'user_supplied':True}
                self.s.emit(uid,tr(user['lang'],'scene_upload_saved',number=scene['id']))
                if len(d['images'])==len(d['script']['scenes']):
                    d['refs_ready']=True; p['state']='visuals'
            elif state == 'intake' and d['route']=='seedance' and self.next_field(d)=='hero' and action=='hero_photo':
                d['brief']['hero']=tr(user['lang'],'hero_photo_plan')
                self.message(uid,'hero_photo_later')
            elif state == 'intake' and (text or action=='skip'):
                if len(text)>6000: self.message(uid,'invalid'); return
                field=self.next_field(d)
                d['brief'][field]=text or tr(d['lang'],'own_choice')
                if not self.next_field(d):
                    if d['route']=='seedance':
                        if d.get('audio_mode'): self.queue(p,'script'); return
                        p['state']='lip'
                    else:
                        d['lip']=True; self.queue(p,'script'); return
            elif state == 'lip' and action in ('lip=0','lip=1'):
                d['lip']=action[-1]=='1'
                if d['lip'] and d['lang'] in ('ru','uk'): p['state']='lip_advice'
                else: self.queue(p,'script'); return
            elif state == 'lip_advice' and action in ('switch','stay'):
                if action=='switch':
                    d['route']='omni'; d['auto_duration']=True
                self.queue(p,'script'); return
            elif state == 'script' and action=='another' and d['route']=='omni':
                d['variant']+=1; self.queue(p,'script'); return
            elif state=='video_revision_review' and action in ('accept_video_revision','reject_video_revision'):
                if action=='accept_video_revision':
                    d['script']=d['pending_video_revision']
                    d['resume_scene_ids']=sorted(set(d.get('resume_scene_ids',[])+[x['id'] for x in d.get('video_edits',[])]))
                    d.pop('omni_blueprints',None)
                d.pop('pending_video_revision',None)
                d.pop('video_edits',None)
                p['state']='quote'
            elif state=='dialogue_proposal' and action=='dialogue_retry':
                proposal=d['dialogue_proposal']
                self.queue(p,'dialogue_proposal',{'scene_id':proposal['id'],'return_state':proposal.get('return_state','script')}); return
            elif state=='dialogue_proposal' and action in ('dialogue_apply','dialogue_keep'):
                proposal=d['dialogue_proposal']
                scene=next(s for s in d['script']['scenes'] if s['id']==proposal['id'])
                if scene['dialogue']!=proposal['original']:
                    p['state']='script'; self.s.save(p); self.show(p); return
                if action=='dialogue_apply' and proposal.get('failed'):
                    self.show(p); return
                if action=='dialogue_apply':
                    scene['dialogue']=proposal['proposed']
                    scene['video_prompt']=scene.get('video_prompt','').replace(proposal['original'],proposal['proposed'])
                    d.get('omni_blueprints',{}).pop(str(scene['id']),None)
                d.setdefault('dialogue_timing_accepted',{})[str(scene['id'])]=scene['dialogue']
                d.pop('dialogue_proposal',None)
                p['state']='quote' if proposal.get('return_state',d.get('return_state'))=='quote' else 'script'
            elif state == 'script' and action=='approve':
                from .dialogue_review import pending_scene
                scene=pending_scene(d)
                if scene:
                    self.queue(p,'dialogue_proposal',{'scene_id':scene['id']}); return
                model='omni' if d['route']=='omni' else 'seedance_'+d['version']
                from .catalog import omni_tokens
                rate=omni_tokens(self.c.token_prices) if d['route']=='omni' else None
                d['script_summary']={'seconds':d['seconds'],'tokens':None if rate is None else rate*(d['seconds']//10 if d['route']=='omni' else 1)}
                p['state']='script_summary'
            elif state == 'script_summary' and action=='continue_visuals':
                p['state']='ratio'
            elif state == 'script_summary' and action=='cancel_visuals':
                p['state']='script'; d.pop('script_summary',None)
                self.s.save(p); self.message(uid,'visuals_cancelled'); self.home(uid); return
            elif state=='style' and action=='style_refs_back' and (d['route']=='omni' or d.get('references_before_script')) and d.get('refs_ready'):
                d.pop('refs_ready',None)
                p['state']='references'
            elif state=='style' and action=='style=reference':
                p['state']='style_reference_upload'
            elif state in ('style_reference_upload','style_reference_review') and action=='style_back':
                p['state']='style'
            elif state=='style_reference_review' and action=='style_reference_replace':
                p['state']='style_reference_upload'
            elif state=='style_reference_upload' and event.get('media'):
                if event['media']['kind']!='photo': self.message(uid,'photo_only'); return
                d['style_reference']=event['media']; self.queue(p,'analyze_style'); return
            elif state=='style_reference_review' and action=='style_reference_approve':
                d['style']='reference'
                if d.pop('references_before_script',False):
                    d['script_references_prepared']=True; self.queue(p,'script'); return
                if d.get('recreate_preparing'):
                    d.pop('recreate_preparing',None); self.queue(p,'script'); return
                if d.get('refs_ready'): self.queue(p,'visuals'); return
                p['state']='references'
            elif state == 'style' and action.startswith('style=') and action[6:] in STYLES:
                d['style']=action[6:]
                d.pop('style_reference',None); d.pop('style_profile',None)
                if d.pop('references_before_script',False):
                    d['script_references_prepared']=True; self.queue(p,'script'); return
                if d.get('recreate_preparing'):
                    d.pop('recreate_preparing',None); self.queue(p,'script'); return
                if d.get('refs_ready'): self.queue(p,'visuals'); return
                p['state']='references'
            elif state=='reference_assignment' and action.startswith('assign_reference='):
                target=int(action.split('=')[1])
                asset=next((a for a in d['script']['assets'] if a['id']==target),None)
                if asset is None: self.show(p); return
                assigned={i for a in d['script']['assets'] for i in a.get('reference_photo_ids',[])}
                count=sum(r['kind']=='photo' for r in d['refs'])
                number=next(i for i in range(1,count+1) if i not in assigned)
                asset.setdefault('reference_photo_ids',[]).append(number)
                asset['description']+=' User photo '+str(number)+' defines this subject identity and overrides conflicting invented appearance; preserve selected animation style.'
                if len(assigned)+1==count:
                    if d.get('seedance_source_flow'):
                        d['source_photos_assigned']=True
                    p['state']='script'
            elif state=='recreate_changes' and (text or action=='recreate_keep'):
                instruction=text if text else tr(user['lang'],'recreate_keep')
                d['video_reference_instructions']=instruction
                d['brief']['changes']=instruction
                for ref in d['refs']:
                    if ref['kind']=='video': ref['purpose']=instruction
                d.pop('video_ref_question_pending',None)
                d['recreate_preparing']=True; p['state']='omni_source_dialogue' if d.get('omni_source_flow') else 'references'
            elif state=='omni_source_dialogue' and action=='source_dialogue=edit':
                p['state']='omni_source_dialogue_edit'; self.s.save(p); self.show(p); return
            elif state in ('omni_source_dialogue','omni_source_dialogue_edit') and (text or action in ('source_dialogue=none','source_dialogue=recognized')):
                if len(text)>12000: self.message(uid,'omni_source_transcript_too_long'); self.show(p); return
                if action=='source_dialogue=recognized':
                    text=(d.get('analysis',{}).get('transcript') or '').strip()
                    if not text:
                        self.message(uid,'omni_source_dialogue'); self.show(p); return
                    if len(text)>12000:
                        self.message(uid,'omni_source_transcript_too_long'); self.show(p); return
                    d['source_dialogue_is_transcript']=True
                else:
                    d.pop('source_dialogue_is_transcript',None)
                d['source_dialogue']=text if text else ''
                d['brief']['dialogue']=d['source_dialogue']
                d['lip']=bool(d['source_dialogue'])
                p['state']='references'
            elif state == 'references' and event.get('media'):
                m=event['media']
                if m['kind']=='video':
                    self.message(uid,'video_analysis_upload_only'); return
                if d.get('seedance_source_selected') and m['kind']!='photo':
                    self.message(uid,'photo_only'); return
                photos=sum(x['kind']=='photo' for x in d['refs'])
                if m['kind']=='photo' and photos>=5:
                    self.message(uid,'ref_limit'); return
                m['purpose']=text[:1000]; d['refs'].append(m)
                self.s.save(p)
                # Keep the Done callback usable after each album item by showing a new view.
                self.message(uid,'ref_saved',n=sum(x['kind']=='photo' for x in d['refs'])); self.show(p); return
            elif state == 'references' and text and not action:
                if any(x['kind']=='photo' for x in d['refs']) and not d.get('video_ref_question_pending'):
                    d['photo_reference_instructions']=text[:4000]
                    self.s.save(p); self.message(uid,'reference_instructions_saved'); self.show(p); return
                self.message(uid,'video_analysis_upload_only'); self.show(p); return
            elif state == 'references' and (action=='done' or (action=='no_refs' and not d['refs'])):
                if d.get('video_ref_question_pending'):
                    self.show(p); return
                d['refs_ready']=True
                if d.get('references_before_script'):
                    if d.get('style'):
                        d.pop('references_before_script',None); d['script_references_prepared']=True
                        self.queue(p,'script'); return
                    p['state']='style'
                elif d.get('recreate_preparing'): p['state']='style'
                elif d.get('visual_source')=='upload': p['state']='visuals'
                elif not d.get('style'): p['state']='style'
                else: self.queue(p,'visuals'); return
            elif state == 'visuals' and action=='approve':
                if d.get('first_frame_pending'):
                    if len(d['script']['scenes'])>1:
                        self.queue(p,'visuals',{'remaining':True}); return
                    d.pop('first_frame_pending',None)
                d.pop('video_edits',None); p['state']='video_version' if d['route']=='seedance' else 'quote'
            elif state in ('video','quote') and action=='edit':
                d['video_edit_return']=state; p['state']='video_edit_choice'
            elif state in ('video_edit_choice','video_visual_scene') and action=='video_edit_back':
                p['state']=d.get('video_edit_return','video')
            elif state=='video_edit_choice' and action=='edit_video_content':
                d['edit_target']='video'; p['state']='edit_input'
                for key in ('edit_history','edit_clarification'): d.pop(key,None)
                if not (d.get('error_scene_id') and d.get('repair_scene')):
                    d.pop('repair_scene',None); d.pop('repair_origin',None)
                    if d.get('video_edit_return')=='quote':
                        d['safety_pending_ids']=[x['id'] for x in d['script']['scenes'] if str(x['id']) not in d['videos'] and x['id'] not in d.get('skipped_scenes',[])]
            elif state=='video_edit_choice' and action=='edit_visual_content':
                if d.get('error_scene_id') and d.get('repair_scene'): p['state']='repair_visual'
                elif d['route']=='omni': p['state']='video_visual_scene'
                else:
                    d['edit_target']='visuals'; p['state']='edit_input'
                    for key in ('edit_history','edit_clarification','repair_scene'): d.pop(key,None)
            elif state=='video_visual_scene' and action.startswith('visual_scene='):
                try: number=int(action.split('=')[1])
                except ValueError: return
                if not any(x['id']==number for x in d['script']['scenes']) or number in d.get('skipped_scenes',[]): return
                d['repair_scene']=number; d['repair_origin']='video'; d['repair_return']=d.get('video_edit_return','video'); d.pop('repair_visual_changed',None)
                p['state']='repair_visual'
            elif state in ('script','visuals') and action=='edit':
                d.pop('edit_history',None); d.pop('edit_clarification',None)
                d['edit_target']=state; p['state']='edit_input'
            elif state in ('edit_input','edit_confirm') and text:
                if state=='edit_confirm':
                    d['edit_history']=[{'role':'assistant','text':str(d.get('edits',[]))}]
                from .supplied_script import merge_request
                merge_ids=merge_request(text)
                if merge_ids and d.get('edit_target') in ('video','script'):
                    self.queue(p,'edit_script',{'request':text[:6000],'merge_ids':merge_ids}); return
                if d.get('edit_target')=='visuals':
                    from .visual_edits import needs_character_details
                    if needs_character_details(text):
                        self.message(uid,'character_change_details'); return
                if d.get('repair_scene'):
                    if re.search(r'визуал|изображен|кадр|visual|image|frame|зображен',text,re.I):
                        from .visual_edits import repair_edits
                        try: changes=repair_edits(d,text)
                        except ValueError:
                            self.message(uid,'visual_scene_numbers'); return
                        self.queue(p,'edit_visuals',{'edits':changes,'repair_scene':d['repair_scene']}); return
                    d['edits']=[{'id':d['repair_scene'],'instruction':text[:6000]}]
                    d['safety_pending_ids']=[x['id'] for x in d['script']['scenes'] if (str(x['id']) not in d['videos'] or x['id']==d['repair_scene'] or x['id'] in d.get('resume_scene_ids',[])) and x['id'] not in d.get('skipped_scenes',[])]
                    p['state']='edit_confirm'; self.s.save(p); self.show(p); return
                if d.get('first_frame_pending') and d['edit_target']=='visuals':
                    self.queue(p,'edit_visuals',{'edits':[{'id':1,'instruction':text[:6000]}]}); return
                if d['edit_target']=='script':
                    self.queue(p,'edit_script',{'request':text[:6000]}); return
                self.queue(p,'plan_edits',{'text':text[:6000]}); return
            elif state == 'edit_confirm' and action=='back':
                p['state']='attention' if d.get('repair_scene') else d['edit_target']
            elif state == 'edit_confirm' and action=='apply':
                if d['edit_target']=='video':
                    d['resume_scene_ids']=sorted(set(d.get('resume_scene_ids',[])+d.pop('safety_pending_ids',[])+[x['id'] for x in d['edits']]))
                    d['video_edits']=d['edits']
                    d['skipped_scenes']=[n for n in d.get('skipped_scenes',[]) if n not in {x['id'] for x in d['edits']}]
                    p['state']='quote'
                else:
                    self.queue(p,'edit_'+d['edit_target'], {'edits':d['edits']}); return
            elif state=='quote' and action=='back':
                p['state']='repair_visual_ready' if d.get('repair_origin')=='video' else 'attention' if d.get('repair_scene') else 'video' if d.get('video_edits') else 'visuals'
            elif state=='quote' and action=='generate':
                reserve=self.quote(d)[0] if self.c.mode=='demo' or ((self.c.billing_enabled or self.c.require_purchase) and uid not in self.c.admins) else 0
                if user['balance']-user['reserved']<reserve: self.message(uid,'no_credit'); return
                self.queue(p,'video',reserve=reserve); return
            elif (state=='video' and action=='approve') or state=='subtitles':
                self.finish_video(p); self.show(p); return
            elif state=='finished' and action=='reels_text_retry' and d.get('reels_text_failed'):
                self.s.enqueue(p,'reels_text'); self.show(p); return
            elif state=='finished' and action=='download':
                self.s.emit(uid,file=d['final'],kind='video'); return
            elif state in ('upload','seedance_source_upload','omni_source_upload') and (event.get('media') or {}).get('kind')=='video':
                m=event['media']
                limit=60 if state=='omni_source_upload' else 30
                if state in ('seedance_source_upload','omni_source_upload') and (m.get('seconds',0)<.01 or m['seconds']>limit):
                    self.message(uid,'omni_reference_upload' if state=='omni_source_upload' else 'seedance_analysis_upload'); return
                if m.get('seconds',0)>self.c.max_seconds: self.message(uid,'media_error'); return
                d['source']=m; self.queue(p,'analyze'); return
            elif state=='recreate_audio' and action in ('recreate_audio=dialogue','recreate_audio=none'):
                dialogue=action=='recreate_audio=dialogue'
                d['analysis'].update(has_dialogue=dialogue,route='omni' if dialogue else 'seedance',audio_unknown=False)
                p['state']='route'
            elif state=='route' and (action=='approve' or action in ('route=omni','route=seedance')):
                d['route']=d['analysis']['route'] if action=='approve' else action[6:]
                # Analyze the whole source; regenerate only the user-approved duration.
                d['recreate']=True
                d['brief']={'idea':d['analysis']['summary']}
                d['auto_duration']=d['route']=='omni'
                d['seconds']=15 if d['route']=='seedance' else 10
                d['refs']=[r for r in d['refs'] if r['kind']!='video']
                d['reference_video_planned']=False
                d.pop('video_ref_question_pending',None)
                p['state']='seedance_audio' if d['route']=='seedance' else 'video_language'
            else:
                changed=False; self.message(uid,'invalid')
            if changed:
                self.s.save(p); self.show(p)
