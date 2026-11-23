ROLE = '''Strict adult-character wording: in generated action, camera, asset and video prompt descriptions use «женщина» (with correct Russian inflection) or «woman» for an adult female, never «девушка» or «girl». Preserve actual ages: this rule does not turn children into adults. Preserve exact approved spoken dialogue.
Use lively, slightly brisk natural conversational speech with clear articulation and only brief purposeful pauses. Avoid slow, drawn-out delivery or long silent gaps; keep every word intelligible and never rush.
For Seedance create separate assets for each distinct character or important object.
Each asset description requests only that individual subject, not a storyboard, group or collage.
Use sharp readable outlines, clear eyes and facial details, distinct silhouettes, saturated midtones,
controlled contrast and a medium-dark neutral background separating the subject from the background.
Avoid pale/pastel palettes, washed-out colors, overexposure, bloom, haze, blur and crushed dark details.
Keep approved/reference identity and intrinsic colors; do not recolor a white character just to darken it.
Keep full character anatomy or the entire object visible, with no cropping or occlusion.
In each timed video_prompt refer to asset id 1 as @Image1, id 2 as @Image2, and so on.
State which referenced character acts or speaks. Give each line one named speaker and sequential non-overlapping speech timing. All other characters stay silent with closed mouths, including reaction shots. Preserve identity, proportions and object shape.
Separate permanent identity from wardrobe. Explicitly list each present image N and its
outfit for EVERY timed location, including a return to a location. A destination outfit
overrides portrait clothing only for that shot. At a park-to-bedroom cut keep the SAME
image N identities and hair, already wearing the approved bedroom clothes. Never invent
a new haircut or copy another character's face. Do not include absent characters.
Do not merge characters, morph faces or let clothes and objects drift between shots.
Keep all video_prompt fields together within a working budget of 4000 characters per
output clip. Use image N instead of repeating referenced faces, eyes, age, hair or body
descriptions. Include approved appearance and separate clothing details once in the opening reference list, not in every timed beat.
Prioritize exact dialogue, timing, actions, camera and scene-specific clothing changes.
Do not repeat global style/identity rules per beat. Never truncate dialogue to meet the budget.
'''

def references(d):
    paths=[]; labels=[]
    if d.get('visual_source')=='upload':
        for scene in d['script']['scenes']:
            paths.append(d['images'][str(scene['id'])]['path'])
            labels.append(f"image {len(paths)}: approved complete frame for scene {scene['id']} ({scene['start']}–{scene['end']}s). Preserve its characters, objects, composition and location.")
    for asset in ([] if d.get('visual_source')=='upload' else d['script']['assets']):
        paths.append(d['images'][str(asset['id'])]['path'])
        labels.append(f"image {len(paths)}: {asset['name']}. {asset['description']}")
    # Source photos are visual-creation inputs; video uses approved outputs only.
    return paths,labels


def video_reference_prompt(d):
    if d.get('recreate'):
        return ('The source video was analyzed upstream but is NOT attached to this generation. '
                'Create a new video only from the approved screenplay and approved visual references. '
                'Do not reference a source video or invent missing scenes. Apply only these confirmed changes: '
                + d.get('video_reference_instructions',''))
    if any(r.get('kind')=='video' for r in d.get('refs',[])):
        return ('The uploaded video is an analysis input only and is NOT attached to Seedance. '
                'Follow only the approved screenplay and approved visual references.')
    return ''


def finalize_reference_prompt(prompt, d):
    """Use actual attached assets, not private original-photo numbering."""
    import re
    if d.get('recreate') or d.get('refs'):
        mapping={str(photo):asset['id'] for asset in d.get('script',{}).get('assets',[])
                 for photo in asset.get('reference_photo_ids',[])}
        def replacement(match):
            asset=mapping.get(match[1])
            return f'image {asset}' if asset and d.get('visual_source')!='upload' else 'the corresponding approved character image'
        prompt=re.sub(r'\b(?:reference\s+photo|user\s+photo|photo[- ]reference|фото[- ]референс[ау]?|фото)\s*#?\s*(\d+)',replacement,prompt,flags=re.I)
    if d.get('audio_mode') != 'none':
        from .voices import turn_taking
        prompt += '\n' + turn_taking(bool(d.get('lip')))
    return prompt+'''\nFINAL CHARACTER CONTINUITY:
Each approved image is the identity authority for its named character in EVERY shot.
Preserve face, age, body proportions, hair color, haircut, hair length and hairstyle.
A location change, waking up or bedroom scene is NOT permission to invent a bun, ponytail,
longer hair or another haircut. Preserve the established haircut in every location; do not prescribe a default haircut.
Change hairstyle only if the user explicitly requests that change, never merely because
an earlier model-written scene description invented it. Approved images take priority.
Clothing changes explicitly specified for a scene apply to CLOTHING ONLY and never to
face or hair. Preserve clothing where no change is specified. Different women must remain
visually distinct; never substitute one for another across cuts.
'''

from .seedance_writing import RULES as WRITING_RULES
ROLE += "\n" + WRITING_RULES
