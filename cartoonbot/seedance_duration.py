"""Seedance length assessment and linked two-part screenplay preparation."""
import copy
import re
SCHEMA={'type':'object','additionalProperties':False,'properties':{
    'needs_longer':{'type':'boolean'},'reason':{'type':'string'}},
    'required':['needs_longer','reason']}
RULES="""Assess whether this idea or supplied screenplay can be told naturally in 15 seconds.
Judge the number of meaningful actions, locations, transitions and naturally spoken dialogue,
not the number of words in the user's description. Return needs_longer=true if fitting 15s
would require dropping important events or rushing speech. Respect an explicit request
for 30 seconds or two parts. Do not rewrite or shorten the idea. Explain briefly in the
interface language. If even 30s is insufficient, say so; do not promise it all fits.
"""


def halves(script, boundary=15):
    first=copy.deepcopy(script); second=copy.deepcopy(script)
    first['scenes']=[s for s in first['scenes'] if s['end']<=boundary]
    second['scenes']=[s for s in second['scenes'] if s['start']>=boundary]
    if len(first['scenes'])+len(second['scenes'])!=len(script['scenes']) or not first['scenes'] or not second['scenes']:
        raise ValueError('invalid_split_boundary')
    for i,scene in enumerate(second['scenes'],1):
        scene['id']=i; scene['start']-=boundary; scene['end']-=boundary
        def shift(m):
            a,b=float(m[1]),float(m[2])
            return f'[{a-boundary:g}–{b-boundary:g}s]' if a>=boundary else m[0]
        scene['video_prompt']=re.sub(r'\[(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)s\]',shift,scene['video_prompt'])
    return first,second
