"""Read explicit timing from imported screenplays without generating a new story."""
import re


def duration(text):
    declared=re.search(r'(?:duration|длительность|тривалість)\s*:\s*(\d+(?:\.\d+)?)\s*(?:sec|s\b|сек|с\b)',text,re.I)
    if declared:
        return float(declared[1])
    intervals=re.findall(r'(?m)^\s*(?:\d+[.)]\s*)?(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)\s*(?:sec|s\b|сек|с\b)',text,re.I)
    return max((float(end) for _,end in intervals),default=0)


def apply_seedance(d):
    text=d['supplied_script']
    # Language is inferred only as a default; exact imported speech remains authoritative.
    if re.search('[іїєґІЇЄҐ]',text): lang='uk'
    elif len(re.findall('[А-Яа-яЁё]',text))>len(re.findall('[A-Za-z]',text)): lang='ru'
    else: lang='en' if re.search('[A-Za-z]',text) else d['lang']
    d.update(lang=lang,lip=True,auto_duration=False,supplied_preserve_language=True)
    d.pop('audio_mode',None)  # No silent/translated override from a previous route.
    seconds=duration(text)
    if 0<seconds<=30:
        seconds=15 if seconds<=15 else 30
        d.update(seconds=seconds,seedance_mode=str(seconds),seedance_duration_chosen=True)
        if seconds==30: d['version']='2.5'


def scene_sections(text):
    """Only explicit scene headings; a separate final heading is also a scene."""
    pattern=r'(?im)^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:[^\w\s#*]+\s*)*(?:(?:сцена|scene|сцена)\s+\d+\b|финал\b|фінал\b|finale\b|final(?:\s+scene)?\b)[^\n]*'
    headings=list(re.finditer(pattern,text))
    return [text[h.start():headings[i+1].start() if i+1<len(headings) else len(text)].strip() for i,h in enumerate(headings)]


def merge_request(text):
    # Explicit adjacent scene numbers only; never interpret a visual change as a merge.
    match=re.search(r'(?:объедин\w*|обьедин\w*|соедин\w*|об[’\x27]?єдна\w*|merge|combine)\s+(?:сцен\w*|scenes?)\s*(\d+)\s*(?:и|і|and|&|,|с|with)\s*(?:сцен\w*|scenes?)?\s*(\d+)',text,re.I)
    return sorted({int(match[1]),int(match[2])}) if match else []


def merge_scenes(d, ids):
    import copy
    scenes=d['script']['scenes']
    if len(ids)!=2 or ids[1]!=ids[0]+1 or not all(1<=n<=len(scenes) for n in ids):
        raise ValueError('invalid_edit_targets')
    first,last=ids; result=copy.deepcopy(d['script'])
    left,right=result['scenes'][first-1:last]
    for field in ('action','dialogue','camera','image_prompt','video_prompt'):
        left[field]='\n'.join(x[field] for x in (left,right) if x.get(field))
    left['end']=right['end']
    del result['scenes'][last-1]
    cursor=0
    for index,scene in enumerate(result['scenes'],1):
        seconds=10 if d['route']=='omni' else scene['end']-scene['start']
        scene.update(id=index,start=cursor,end=cursor+seconds); cursor+=seconds
    d['seconds']=cursor
    return result
