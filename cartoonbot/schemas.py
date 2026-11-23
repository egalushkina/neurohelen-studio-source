def obj(fields):
    return {'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}
S={'type':'string'}
N={'type':'number'}
I={'type':'integer'}
def arr(item): return {'type':'array','items':item}
ASSET=obj({'id':I,'name':S,'description':S,'reference_photo_ids':arr(I)})
SCENE=obj({'id':I,'start':N,'end':N,'action':S,'dialogue':S,'camera':S,'image_prompt':S,'video_prompt':S})
SCRIPT=obj({'title':S,'assets':arr(ASSET),'scenes':arr(SCENE)})
EDITS=obj({'clarification':S,'changes':arr(obj({'id':I,'instruction':S}))})
ANALYSIS_SHOT=obj({'start':N,'end':N,'location':S,
                  'characters':arr(obj({'character_id':I,'wardrobe':S,'props':S})),
                  'action':S,'camera':S,'transition':S,'uncertainty':S})
ANALYSIS=obj({'summary':S,'route':{'type':'string','enum':['omni','seedance']},'has_dialogue':{'type':'boolean'},'characters':arr(obj({'id':I,'appearance':S,'role':S,'appearances':S,'uncertainty':S})),
              'shots':arr(ANALYSIS_SHOT)})


def validate_script(script, d):
    from .dialogue_text import normalize_scene
    scenes=script['scenes']
    for scene in scenes: normalize_scene(scene)
    if d.get('recreate') and all('reference_photo_ids' in a for a in script['assets']):
        photo_count=sum(r.get('kind')=='photo' for r in d.get('refs',[]))
        assigned=[i for a in script['assets'] for i in a['reference_photo_ids']]
        if len(assigned)!=len(set(assigned)) or any(type(i) is not int or not 1<=i<=photo_count for i in assigned):
            raise ValueError('invalid_reference_assignment')
    if not 1<=len(script['assets'])<=4 or [a['id'] for a in script['assets']]!=list(range(1,len(script['assets'])+1)):
        raise ValueError('invalid_assets')
    if not scenes: raise ValueError('invalid_scenes')
    cursor=0
    for i,s in enumerate(scenes,1):
        if s['id']!=i or abs(s['start']-cursor)>.01 or s['end']<=s['start']:
            raise ValueError('invalid_timeline')
        if d['route']=='omni' and abs(s['end']-s['start']-10)>.01:
            raise ValueError('invalid_omni_duration')
        # A Seedance clip can contain multiple timed locations/costumes in one prompt.
        # Keep bounded inputs without rejecting a complete, otherwise valid shot plan.
        limits={'action':4000,'dialogue':4000,'camera':4000,'image_prompt':4000,
                'video_prompt':12000 if d['route']=='seedance' else 4000}
        if any(len(s[k])>limit for k,limit in limits.items()):
            raise ValueError('script_too_long')
        cursor=s['end']
    if d.get('auto_duration') and d['route']=='omni':
        if not d.get('supplied_script') and cursor>d.get('max_seconds',60): raise ValueError('invalid_total_duration')
        d['seconds']=int(cursor)
    elif abs(cursor-d['seconds'])>.01: raise ValueError('invalid_total_duration')
    return script
