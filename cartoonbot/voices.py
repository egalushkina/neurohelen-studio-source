"""Project-scoped canonical voice descriptions, shared by both video compilers."""
import hashlib
import re
from .schemas import obj,S


def profile(asset):
    match=re.search(r'VOICE PROFILE\s*:\s*([^\n]+)',asset.get('description',''),re.I)
    return match[1].strip() if match else ''


def resolve(assets,speaker):
    name=speaker.casefold().strip()
    exact=[a for a in assets if a.get('name','').casefold()==name]
    if len(exact)==1:return exact[0]
    prefix=[a for a in assets if a.get('name','').casefold().startswith(name+' ')]
    if len(prefix)==1:return prefix[0]
    tokens=[a for a in assets if name in re.split(r'[(),;]+',a.get('name','').casefold())]
    return tokens[0] if len(tokens)==1 else None


def ensure(provider,job,d):
    from .omni_blueprint import speech_schedule
    assets=d.get('script',{}).get('assets',[])
    cast=d.setdefault('voice_cast',{})
    missing={}
    for scene in d.get('script',{}).get('scenes',[]):
        for turn in speech_schedule(scene.get('dialogue','')):
            speaker=turn['speaker'];asset=resolve(assets,speaker)
            key=(asset['name'] if asset else speaker).casefold()
            if key in cast:continue
            value=profile(asset) if asset else ''
            if value and len(value)<=180 and value.casefold() not in ('none','no voice','silent'):
                cast[key]=value
            else:
                missing[key]={'speaker':speaker,'character':asset or {},'line':turn['line'],'existing_profile':value}
    if missing:
        values=provider.structured(job,
            'Assign one concise English VOICE PROFILE per supplied speaking character. '
            'Follow explicitly supplied voice preferences; use approved character facts, never infer a voice from a photo. '
            'If existing_profile is supplied, shorten it without changing its voice characteristics. '
            'Fix voice gender, perceived vocal age, pitch/timbre, accent and a lively, slightly brisk natural conversational pace with clear articulation; avoid slow delivery, long pauses and rushing. '
            'Keep distinct speakers distinct. At most 160 characters per profile. These profiles persist for the whole film; '
            'emotion may change intensity, never voice identity. Return only the requested JSON.',
            missing,obj({k:S for k in missing}))
        if any(not isinstance(values.get(k),str) or not values[k].strip()  for k in missing):
            raise ValueError('invalid_voice_profile')
        cast.update({k:values[k].strip() for k in missing})
    requests=[e['instruction'] for e in d.get('video_edits',[]) if re.search(r'голос|voice|тембр|timbre|accent|акцент',e.get('instruction',''),re.I)]
    if d.get('requested_voice_edit'):requests.append(d['requested_voice_edit'])
    signature=hashlib.sha256(str(requests).encode()).hexdigest()
    if requests and cast and d.get('voice_edit_signature')!=signature:
        revised=provider.structured(job,
            'Apply ONLY the explicit requested voice changes to this canonical cast. Keep every unmentioned '
            'speaker profile exactly unchanged. Each changed English profile should be at most 160 characters. '
            'Return the same JSON keys, no new characters.',
            {'voices':cast,'requests':requests},obj({k:S for k in cast}))
        if any(not isinstance(revised.get(k),str) or not revised[k].strip() for k in cast):
            raise ValueError('invalid_voice_profile')
        cast.update({k:revised[k].strip() for k in cast});d['voice_edit_signature']=signature
        d.pop('requested_voice_edit',None)
    return cast


def for_scene(d,scene):
    from .omni_blueprint import speech_schedule
    assets=d.get('script',{}).get('assets',[]);result=[]
    for speaker in dict.fromkeys(t['speaker'] for t in speech_schedule(scene.get('dialogue',''))):
        asset=resolve(assets,speaker)
        key=(asset['name'] if asset else speaker).casefold()
        value=d.get('voice_cast',{}).get(key) or (profile(asset) if asset else '')
        if value:result.append(speaker+': '+value)
    return ('VOICES: '+'; '.join(result)+'\n') if result else ''


def for_video(d):
    if d.get('audio_mode')=='none':return ''
    from .omni_blueprint import speech_schedule
    assets=d.get('script',{}).get('assets',[])
    lines=[];seen=set()
    for scene in d.get('script',{}).get('scenes',[]):
        for turn in speech_schedule(scene.get('dialogue','')):
            speaker=turn['speaker'];asset=resolve(assets,speaker)
            key=(asset['name'] if asset else speaker).casefold()
            if key in seen:continue
            seen.add(key)
            value=d.get('voice_cast',{}).get(key) or (profile(asset) if asset else '')
            if value:lines.append(speaker+': '+value)
    if not lines:return ''
    return '\nVOICES (fixed across scenes): '+ '; '.join(lines)+'. Keep identity and pace unchanged; lively, slightly brisk, clear speech, never slow or rushed.'


def turn_taking(lip=True):
    rule = ('Lively, slightly brisk, clear speech; brief pauses, never slow or rushed. One speaker at a time; no overlapping voices. ')
    if lip:
        rule += ('Only the speaker lip-syncs; listeners stay silent with closed mouths, reacting with eyes/gestures. '
                 'Camera cuts never change the speaker or lip-sync owner.')
    else:
        rule += 'No on-camera talking or lip sync, including during camera cuts.'
    return rule
