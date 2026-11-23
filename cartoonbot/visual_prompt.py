"""Owner's scene-visual brief with a whole-output character budget."""
from .schemas import obj, S
from .continuity import DEFAULT_CHARACTER_APPEARANCE

MAX_CHARS = 4000
SECTIONS = ('STYLE','CHARACTER','SCENE','ACTION','CAMERA','CONTINUITY',
            'OBJECTS','SCREENS','PRODUCT CHARACTER','IMPORTANT')
SCHEMA = obj({name:S for name in SECTIONS})
# Budgets leave room for headings, aspect ratio and whitespace under MAX_CHARS.
SECTION_BUDGETS = dict(zip(SECTIONS, (180, 480, 220, 450, 180, 300, 220, 200, 350, 300)))
RULES = DEFAULT_CHARACTER_APPEARANCE + """Write ONE compact English still-image brief in the ten JSON sections.
Target 1800–2500 characters total, including headings; never exceed 4000.
Use short concrete sentences, not explanations. State each fact ONCE in its best section.
Use 'None' for absent elements. Do not copy production rules, dialogue or a video timeline.
Inspect references: identify their character/style/location roles by attachment number.
For a referenced character use its reference number and a short identity lock instead of
listing every facial feature, hair strand, item of clothing or background object again.
For a new character without a reference give only distinguishing design details.
Latest explicit edits/replacements override prior designs/poses. Render replacements anew.
For scene_frames, current_scene defines this frame's action, physical pose, blocking and
location. Adjacent scenes explain narrative progression, not actions to copy. A reference
image_prompt may contain obsolete facial/hair/clothing or style prose: extract ONLY
its posture, spatial blocking, props and location, using approved-frame identity authority
for appearance. Never restore an earlier hairstyle just because it remains in staging text.
The current scene's staging is not permission to override a latest explicit user correction.
Each reference
frame locks character DESIGN, not the whole picture. Recompose the shot for this scene:
entering, sitting/talking and returning/reacting must not all become standing beside a table.
Preserve explicit seated/standing positions and select ONE readable instant of the current
scene. Do not reduce a new scene to a facial-expression edit of frame 1. Use its requested
shot size/viewpoint; when unspecified choose a story-motivated composition that reveals
the new action. Keep room architecture physically consistent despite a new camera angle.
Do not invent actions, new locations or arbitrary pose changes just to make frames different.
On edits, the latest requested correction in brief overrides older current_scene staging;
small targeted edits may keep their own frame composition. Identity-only assets are not scene frames.
STYLE: selected cartoon medium for EVERY subject, including photographic references.
CHARACTER: exact cast count, distinct identities and actual ages. Preserve reference identity,
hair, clothes and proportions unless explicitly changed; do not turn all people into one reference.
SCENE: location and essential surroundings. ACTION: one possible still pose, relevant
hand tasks/contacts and a short visible emotional cue. CAMERA: one shot size and angle.
CONTINUITY: a single reference lock for unchanged room/props/light; describe
only scripted changes, never copy a different reference location into this scene. Include
a short light/time/weather cue only if needed, without repeating the room description.
OBJECTS: essential props/grips; preserve shape, color and logical positions. No extra objects.
PRODUCT CHARACTER: None unless the main hero is a product/object. Such a hero has expressive eyes,
mouth, two arms and legs in the chosen style. Preserve shape, label/text/logo/colors/layout;
face and limbs must not cover lettering or deform packaging. Keep its design between frames.
Ordinary props remain objects; respect explicit no-limb requests and design corrections.
SCREENS: only if a screen is present, content inside display borders in correct
perspective, never on the back/outer lid. Change camera angle to reveal the screen.
IMPORTANT: only scene-specific priorities not already stated, at most 3 short relevant
restrictions. Never append a separate or full negative block. For limb interactions require correct
anatomy and separate bodies; no extra/fused limbs. Respect species/approved cartoon anatomy,
Ordinary humans have exactly two arms and two legs, five fingers per visible hand,
when applicable; do not force hidden limbs visible. For product heroes preserve readable branding; do not
contradict this with 'no text/logo'. No invented captions or watermark.
Check continuity, requested changes, anatomy and screen geometry silently; output JSON only.
""" + "\nMaximum characters per section value: " + str(SECTION_BUDGETS)



def render(result,ratio):
    if not isinstance(result,dict) or any(not isinstance(result.get(k),str) or not result[k].strip() for k in SECTIONS):
        raise ValueError('invalid_visual_prompt')
    return f'Aspect ratio: {ratio}.\n\n'+'\n\n'.join(k+'\n'+result[k].strip() for k in SECTIONS)


def compile_prompt(provider,job,d,refs,brief,contracts,number):
    data={'scene_number':number,'selected_style':d.get('style'),
          'aspect_ratio':d['ratio'],'brief':brief,'reference_contracts':contracts}
    scene_frames=d.get('route')=='omni' or d.get('visual_source')=='upload'
    data['visual_kind']='scene_frames' if scene_frames else 'identity_asset'
    if scene_frames:
        scenes=d.get('script',{}).get('scenes',[])
        data['current_scene']=next((s for s in scenes if s['id']==number),{})
        data['adjacent_scene_actions']=[{'id':s['id'],'action':s.get('action',''),'camera':s.get('camera','')}
                                        for s in scenes if abs(s['id']-number)==1]
    result=provider.structured(job,RULES,data,SCHEMA,images=refs)
    prompt=render(result,d['ratio'])
    # Text-only revisions: never pay for an image with an oversized brief, never slice it.
    for target in (2500, 1800):
        if len(prompt)<=MAX_CHARS: break
        result=provider.structured(job,
            f'Compress this English still-image brief to at most {target} characters across values. '
            'Keep all ten JSON keys. Use short phrases and None for absent elements. '
            'State each fact once; replace detailed reference appearance/room lists with numbered '
            'reference locks. Preserve cast mapping, selected style, latest requested changes, '
            'action/hand tasks, seated/standing pose, scene-specific blocking and camera, screen geometry and readable product branding. No truncation or new content. '
            'The previous response exceeded the 4000-character TOTAL budget including headings.',
            result,SCHEMA)
        prompt=render(result,d['ratio'])
    if len(prompt)>MAX_CHARS: raise ValueError('visual_prompt_too_long')
    return prompt
