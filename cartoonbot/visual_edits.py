"""Resolve explicit scene lists and references to the remaining visuals."""
import re

def repair_edits(d, text):
    current=d['repair_scene']
    available={int(k) for k in d.get('images',{})}
    remaining=re.search(r'(?:все|всех|всі|усі|all)\s+(?:остальн\w*|інш\w*|решт\w*|remaining|other)',text,re.I)
    # Numbers following “как в сцене” name a reference, not an edit target.
    target_text=re.split(r'как\s+(?:в\s+)?|как\s+на\s+|like\s+|as\s+in\s+',text,flags=re.I)[0]
    match=re.search(r'(?:сцен\w*|кадр\w*|фото|изображени\w*|scenes?|frames?|images?)\s*(\d+(?:\s*(?:[-–—,]|и|and|&)\s*\d+)*)',target_text,re.I)
    ids=[]
    if remaining:
        ids=sorted(available-{current})
    elif match:
        for part in re.split(r'\s*(?:,|и|and|&)\s*',match[1]):
            ends=re.split(r'\s*[-–—]\s*',part)
            if len(ends)==2:
                start,end=map(int,ends)
                if end<start or end-start>len(available): raise ValueError('invalid_edit_targets')
                ids.extend(range(start,end+1))
            else: ids.append(int(part))
    else:
        ids=[current]
    if not ids or any(n not in available for n in ids):
        raise ValueError('invalid_edit_targets')
    instruction=text[:6000]
    if current not in ids:
        instruction+=f'\nUse existing scene {current} as the reference for the requested changed trait only. Preserve each target scene composition and all other details. Do not copy its entire scene.'
    return [{'id':n,'instruction':instruction} for n in dict.fromkeys(ids)]


def needs_character_details(text):
    normalized=re.sub(r'[.,!?;:]+',' ',text.casefold())
    normalized=re.sub(r'\b(?:пожалуйста|плиз|мне|сейчас|просто|давай|теперь)\b',' ',normalized)
    normalized=' '.join(normalized.split())
    return bool(re.fullmatch(
        r'(?:измени|изменить|замени|заменить|поменяй|поменять|переделай)\s+'
        r'(?:эту\s+|этого\s+)?(?:девушку|женщину|парня|мужчину|персонажа|героя|героиню)'
        r'(?:\s+персонажа)?(?:\s+(?:на|в)\s+(?:этом\s+|этой\s+)?'
        r'(?:фото|фотографии|изображении|кадре|сцене)(?:\s+\d+)?)?',normalized))


def wants_regeneration(text):
    """Explicit replacement verbs, excluding simple negated instructions."""
    text=re.sub(r'\b(?:не|not|don[’\x27]t)\s+(?:надо\s+|нужно\s+)?(?:замен\w*|передел\w*|перегенер\w*|regenerat\w*|replac\w*|redo)\b','',text,flags=re.I)
    return bool(re.search(r'\b(?:замени\w*|передел\w*|перегенер\w*|regenerat\w*|replac\w*|redo)\b|заново|с нуля',text,re.I))
