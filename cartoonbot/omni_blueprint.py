"""Compile the owner's structured Omni directing brief from the approved frame."""
import hashlib
import json
import re
from .schemas import obj, S, N, arr
from .dialogue_text import normalize_scene
from .voices import turn_taking

VERSION = 'owner-structure-8-frame-identity'
MAX_PROMPT_CHARS = 4000
MAX_DIRECTION_CHARS = 1600
SECTIONS = ('characters','timeline','audio','important')
BEAT = obj({'start':N,'end':N,'direction':S})
SCHEMA = obj({'characters':S,'timeline':arr(BEAT),'audio':S,'important':S})
STYLE_NAMES = {
    '3d':'3D Pixar-style cartoon animation', 'anime':'2D Japanese anime',
    '2d':'Classic hand-drawn 2D cartoon animation', 'clay':'Clay stop-motion cartoon animation',
    'realism':'Cinematic photorealism, live-action film lighting and natural materials; preserve referenced subjects, no cartoon stylization',
    'watercolor':'Hand-painted watercolor cartoon animation',
}
RULES = '''You direct ONE 10-second Omni Flash cartoon from its APPROVED FIRST FRAME.
Return concise English production directions, not an abstract essay. Follow the owner's
FORMAT / CHARACTERS / STYLE / TIMELINE / AUDIO / CONTINUITY / IMPORTANT structure;
code supplies fixed sections, the selected style and exact dialogue. No new plot or speech.
The attached image is the actual first frame, not loose inspiration. Keep each character's
species and material body throughout every shot. An animated vegetable, product or robot
is that literal entity, never a human actor or person in a costume. Voice gender describes
sound only, never anatomy. Begin with the supplied composition before moving the camera.
CHARACTERS: Use a short name/role to approved-frame binding for EACH present character.
Do not redescribe visible human face, hair, clothing or room. For each prominent object or
product CHARACTER, inspect the approved frame and include a compact design lock in CHARACTERS:
body shape/proportions, casing and trim colors, face/eye/mouth placement, limb design, label.
For a phone preserve screen bezel, back/camera layout when visible, case and screen orientation.
State only visible details, never invent unseen sides. This compact object lock is required,
even when an image is attached; it is not a long packaging description. Reference identity
is locked by code. Mention only distinguishing roles needed for actions/speakers or explicit
approved changes. Preserve actual ages; adult female is woman. No invented cast.
TIMELINE: Return 2–4 chronological beats covering 0 through 10 seconds without gaps or
overlaps. Each beat gives one short action/reaction and camera instruction. Describe only changes,
not the same position, surroundings or appearance again. Keep each beat simple. Adapt camera/framing to the
approved action, using cuts only when coherent with the requested camera and scene.
Approved speech_schedule runs concurrently with actions, never with other voices. Speakers take turns; listeners stay silent with closed mouths even in reaction shots: match the right speaker's mouth and reaction
in each beat, but DO NOT copy dialogue into direction. Code inserts exact timed lines into
TIMELINE separately so a long line is not squeezed into one short visual beat.
Start from the actual frame's pose and object contacts. Do not repeat completed setup
(e.g. jumping onto a bed when already on it). Preserve the approved key action and ending.
Do not invent beauty-ad hair gestures or hair growth: smoothing changes texture/shine,
not hair length/cut. All actions must be physically possible with the visible anatomy.
A reflection is the same person. Transformations require an explicit approved change and
must happen in a clear sequence; no unexplained objects, face/clothing swaps or duplicates.
Keep action/reactions/camera moving naturally with no empty pauses; do not invent plot
just to fill time, overstuff beats or force frantic cuts. Exact speech spans its provided
interval and overlaps action, never a separate silent intro/outro. Preserve user pacing.
Delivery defaults to a normal lively conversational pace, clear and expressive, never
slow or drawn-out. Brief natural breaths only; no artificial pauses between phrases.
Do not stretch a short approved line: maintain visual action/reactions after it ends.
Use motivated framing/reaction changes, respecting single-shot and user pacing requests.
AUDIO: Concise English sound directions and the approved music preference. If no music
was requested say No music. If unspecified use subtle environment sound, no invented music.
The code supplies spoken language and voice profiles; do not repeat those or write dialogue.
IMPORTANT: Only a few scene-specific restrictions not already covered by fixed continuity
and output rules; use None if unnecessary. Preserve product labels/logos in the reference;
prohibit only NEW overlays/logos/watermarks, not existing packaging or provider provenance.
Respect the selected cartoon style in every subject and shot. Never introduce photographic
rendering terminology. Do not use policy evasion or disguise ages/content.
Aim for a complete prompt of 1800–2500 characters, never over 4000 including fixed blocks
and exact dialogue. Use the supplied direction_budget_chars for your fields. No redundant boilerplate.
Return only the requested JSON.'''


def speech_schedule(dialogue):
    """Exact lines and named speakers, concurrent with the visual timeline."""
    if not dialogue.strip(): return []
    # Speaker labels may occur on a fresh line or after the previous quoted line.
    pattern=r'(?:^|\n|(?<=[»”".!?]))\s*([^:\n«»“”".!?]{1,60}):\s*'
    matches=list(re.finditer(pattern,dialogue))
    lines=[]
    if matches and not dialogue[:matches[0].start()].strip():
        for i,m in enumerate(matches):
            stop=matches[i+1].start() if i+1<len(matches) else len(dialogue)
            lines.append((m.group(1).strip(),dialogue[m.end():stop].strip()))
    else:
        lines=[('Speaker as approved',dialogue.strip())]
    weights=[max(1,len(re.findall(r'\w+',line))) for _,line in lines]
    total=sum(weights); cursor=0; schedule=[]
    for i,((speaker,line),weight) in enumerate(zip(lines,weights)):
        end=10.0 if i==len(lines)-1 else round(10*sum(weights[:i+1])/total,2)
        schedule.append({'speaker':speaker,'line':line,'start':cursor,'end':end})
        cursor=end
    return schedule


def _validate(result):
    if not isinstance(result,dict) or any(not isinstance(result.get(k),str) or not result[k].strip() for k in ('characters','audio','important')):
        raise ValueError('invalid_omni_blueprint')
    beats=result.get('timeline')
    if not isinstance(beats,list) or not 2<=len(beats)<=4: raise ValueError('invalid_omni_blueprint')
    cursor=0
    for beat in beats:
        if (not isinstance(beat,dict) or any(type(beat.get(k)) not in (int,float) for k in ('start','end'))
                or abs(beat['start']-cursor)>.001 or not cursor<beat['end']<=10
                or not isinstance(beat.get('direction'),str) or not beat['direction'].strip()):
            raise ValueError('invalid_omni_blueprint')
        cursor=beat['end']
    if abs(cursor-10)>.001: raise ValueError('invalid_omni_blueprint')
    return sum(len(result[k]) for k in ('characters','audio','important'))+sum(len(b['direction']) for b in beats)


def compile_prompt(provider, job, d, scene, refs):
    from .omni_prompts import scene_voices
    scene=normalize_scene(dict(scene))
    speech=speech_schedule(scene.get('dialogue',''))
    context={'scene':{k:scene.get(k,'') for k in ('id','action','dialogue','camera')},
             'style':d.get('style'),'style_profile':d.get('style_profile'),
             'language':d['lang'],'ratio':d['ratio'],'lip_sync':bool(d.get('lip')),
             'audio':d.get('brief',{}).get('audio',''),'voices':scene_voices(d,scene),
             'speech_schedule':speech,
             'cast_roles':[{'name':a.get('name',''),'description':a.get('description','').split('VOICE PROFILE')[0].strip()}
                           for a in d.get('script',{}).get('assets',[])]}
    signature=hashlib.sha256(json.dumps([VERSION,context,refs],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    cache=d.setdefault('omni_blueprints',{}); key=str(scene['id'])
    if cache.get(key,{}).get('signature')==signature and len(cache[key]['prompt'])<=MAX_PROMPT_CHARS:
        return cache[key]['prompt']
    # Reserve the actual fixed blocks, dialogue, style and voice profiles before generation.
    empty={'characters':'','timeline':[{'start':0,'end':3,'direction':''},
           {'start':3,'end':6,'direction':''},{'start':6,'end':10,'direction':''}],
           'audio':'','important':'None'}
    overhead=len(_render(empty,d,context,speech))
    budget=min(MAX_DIRECTION_CHARS,MAX_PROMPT_CHARS-overhead-100)
    if budget<200: raise ValueError('omni_prompt_too_long')
    context['direction_budget_chars']=budget
    result=provider.structured(job,RULES,context,SCHEMA,images=refs)
    _validate(result)
    prompt=_render(result,d,context,speech)
    for target in (1200, 800):
        if len(prompt)<=MAX_PROMPT_CHARS: break
        budget=min(target,budget,MAX_PROMPT_CHARS-overhead-100)
        if budget<200: raise ValueError('omni_prompt_too_long')
        result=provider.structured(job,
            f'Compress these English directions to at most {budget} characters combined. '
            'Use short frame-based character bindings, not appearance descriptions. '
            'Each beat needs only action/reaction and camera; omit unchanged surroundings. '
            'Preserve character distinctions, requested changes, timing and audio preferences. '
            'Keep the JSON schema and 0–10 second timeline. Important: only relevant restrictions. '
            'Do not add dialogue: exact approved lines are inserted separately.',result,SCHEMA)
        _validate(result)
        prompt=_render(result,d,context,speech)
    if len(prompt)>MAX_PROMPT_CHARS: raise ValueError('omni_prompt_too_long')
    cache[key]={'signature':signature,'prompt':prompt,'version':VERSION}
    return prompt


def _render(result,d,context,speech):
    style=STYLE_NAMES.get(d.get('style'))
    if not style:
        style=(d.get('style_profile') or {}).get('instructions') or 'The approved reference cartoon style'
    language={'ru':'Russian','uk':'Ukrainian','en':'English'}[d['lang']]
    timeline=[f"[{b['start']:g}-{b['end']:g}s] {b['direction']}" for b in result['timeline']]
    if speech:
        timeline.append('Concurrent dialogue, starting immediately; visual beat boundaries do not interrupt speech:')
        for turn in speech:
            mode='speaks' if d.get('lip') else 'voiceover'
            timeline.append(f"[{turn['start']:g}-{turn['end']:g}s] {turn['speaker']} ({mode} in {language}): {turn['line']}")
    else: timeline.append('No dialogue.')
    audio=result['audio']
    if re.search(r'без\s+муз[ыи]ки|no\s+music|without\s+music',context['audio'],re.I):
        audio+=' No music.'
    if speech: audio+=f' Dialogue in {language}. Natural lively conversational pace, brief breaths, no drawn-out words or added pauses; respect explicitly approved delivery. '+turn_taking(bool(d.get('lip')))
    if context['voices']: audio+='\n'+context['voices'].strip()+' Same voice identity in every scene: gender, timbre, pitch and accent stay fixed; emotion changes expression only.'
    important='No captions, subtitles, new on-screen text, overlay logos or added watermarks. Preserve reference product labels. No still-image outro.'
    if result['important'].strip().lower() not in ('none','none.'): important+=' '+result['important']
    prompt='\n\n'.join([
        f'FORMAT\n<FIRST_FRAME> {d["ratio"]}; 10-second video. Use attached image 1 as the exact opening frame and identity reference throughout. Animate its actual characters, not replacements. Dynamic pacing, no long pauses.',
        'CHARACTERS\n'+result['characters']+'',
        'STYLE\n'+style+'. Same style in every shot.',
        'TIMELINE\n'+'\n'.join(timeline),
        'AUDIO\n'+audio,
        'CONTINUITY\nPreserve the attached image identities in every shot: species, material body, face, proportions, clothing, props and setting. Talking vegetables/products remain literal vegetables/products with the same face and limbs, never human actors, human heads or costumes. Voice gender is audio only. Keep each speaker bound to that same visible character through camera cuts. Objects keep their exact shape, colors and labels; never replace or morph it. Reflections match; screens stay within the display.',
        'IMPORTANT\n'+important])
    return prompt
