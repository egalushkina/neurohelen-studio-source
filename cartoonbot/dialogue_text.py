"""Dialogue punctuation rules; preserve words and spelling hyphens."""
import re

RULES = '''\nDIALOGUE PUNCTUATION: No em dashes, en dashes or standalone hyphens in spoken
lines, including dialogue copied into video prompts. Use normal sentences and commas.
Label speakers with Name: followed by their line, never a dash prefix.
Keep spelling hyphens inside words (e.g. кто-то). For supplied lines preserve all words;
only remove dash punctuation. This punctuation rule also applies to revisions.\n'''

def clean(text):
    text=re.sub(r'[—–‒―]|(?<!\w)-(?!\w)', ' ', text)
    return '\n'.join(re.sub(r'[^\S\n]+',' ',line).strip() for line in text.split('\n'))

def normalize_scene(scene):
    old=scene.get('dialogue','')
    new=clean(old)
    if old!=new:
        scene['dialogue']=new
        if old and isinstance(scene.get('video_prompt'),str):
            scene['video_prompt']=scene['video_prompt'].replace(old,new)
    return scene
