"""Shared production rules for identity, locations and recurring voices."""
import json
from pathlib import Path


STYLE_CONTINUITY = 'При создании каждого кадра строго сохраняй выбранный стиль на протяжении всего мультфильма. Не смешивай разные визуальные стили между сценами.\n'

DEFAULT_CHARACTER_APPEARANCE = """For newly invented human characters whose appearance is unspecified, default to
Slavic/European appearance in the selected cartoon style, with varied individual faces,
hairstyles and clothing. This is only a creative default, not an ethnicity inference from photos.
Explicit user descriptions, character references, source-video identities and already approved
characters take priority. Never redesign them to apply this default. Do not apply it to animals
or product characters. Keep each established identity consistent within its cartoon.
"""

EDIT_PRIORITY = DEFAULT_CHARACTER_APPEARANCE + """EXPLICIT USER EDIT HAS PRIORITY:
When the user explicitly requests replacing a character or changing their appearance,
apply that change to the named target even when earlier scene descriptions, original
photos or generated frames show the old design. Identity locks apply ONLY to unmodified
subjects and unrequested traits. A request for a different person requires a visibly
different identity, not a near-identical rerender. A hair-only or clothing-only edit must
preserve the person's face. Keep actual age unless the user explicitly requests a different
age. Preserve other characters, setting, action and selected style. Never silently replace
every character. Use the latest requested design rather than restoring an earlier frame.
"""


def style_contract(d):
    """Expand UI style IDs into rendering instructions, independently of identity."""
    if d.get('style')=='reference' and d.get('visual_source')!='upload':
        return '\nSELECTED RENDERING STYLE (overrides earlier rendering descriptions only): '+d.get('style_profile',{}).get('instructions','')+'\nMatch the style reference as closely as possible: image technique, character shape language, palette, lighting, degree of realism, detail level and overall atmosphere. Do not copy its specific characters or objects. Preserve the target identities and scene; transfer style only.\n'+STYLE_CONTINUITY
    if d.get('visual_source')=='upload': return ''
    if d.get('style')=='realism':
        return '\nSELECTED RENDERING STYLE — '+'CINEMATIC PHOTOREALISM. Live-action feature-film appearance: realistic anatomy and proportions, natural skin, hair and fur, physically plausible materials, believable environments and props. Motivated cinematic lighting, deliberate framing, controlled depth of field and coherent film color grading. Use purposeful camera movement appropriate to the approved scene. Preserve recognizable reference identity, actual age, hairstyle, wardrobe, animal markings and product shape. Apply the same realism to EVERY character, object and environment. Preserve fantastical or talking-object subjects as designed; realism does not turn them into humans. This selected style overrides cartoon, anime, Pixar, clay and illustration wording in earlier generated frames or draft prompts. Preserve approved actions and dialogue without replacing the person. No forced animation-feature stylization.'+'\n'
    styles={
        '3d':'STYLIZED 3D ANIMATED FEATURE (Pixar-style). Sculpted rounded forms, designed hair shapes, clean animated-film materials and soft cinematic lighting. Animals must look like designed animated-feature characters: sculpted rounded muzzle and ears, readable expressive shapes, simplified grouped fur tufts and stylized material response, not a photographed animal pasted into a cartoon scene.',
        'anime':'2D JAPANESE ANIME. Modern Japanese anime style, expressive large eyes, clean outlines, stylized facial features, dynamic composition, soft cel shadows and saturated colors. Preserve recognizable identity while translating features into anime. No 3D/Pixar rendering, photographic skin, photographic textures or live-action elements.',
        '2d':'2D CARTOON. Classic hand-drawn two-dimensional animation, clear outlines, flat colors, expressive characters, minimal volume and the feeling of a classic cartoon. No 3D-rendered or photographic subjects.',
        'clay':'CLAY STOP-MOTION. Handmade clay animation: characters and surroundings look sculpted from plasticine, visible material texture, rounded forms, a miniature world and soft studio lighting. Visible fingerprints, tool marks, tiny dents and seams in matte plasticine on every face, hair strand, garment and prop. Hair is sculpted clay clumps; clothes are clay with molded folds, NOT woven fabric. No skin pores, realistic fabric fibers, glossy CGI skin or smooth Pixar-style rendering. The entire subject must unmistakably look hand-built from modeling clay. No photographic humans or flat anime subjects.',
        'watercolor':'WATERCOLOR ILLUSTRATION. Delicate hand-painted watercolor, translucent colors, soft transitions, visible paper texture, light artistic brushstrokes and an airy fairytale atmosphere. No photographic subjects or glossy 3D rendering.',
    }
    rendering=styles.get(d.get('style'))
    if not rendering: return ''
    return STYLE_CONTINUITY+'\nSELECTED RENDERING STYLE — '+rendering+"""
Apply this SAME rendering to EVERY character, object and environment in every output,
including subjects based on photos. A realistic photo requires an actual rendering conversion,
not merely new lighting, larger eyes or a different background. Rebuild every referenced
subject in the selected medium while keeping its recognizable features. Never mix a 3D woman with an anime man or photographic props.
User photographs define recognizable identity, age, hairstyle, animal coat markings, clothing and product
shape/label design, NOT photographic rendering or the reference rendering technique. Translate those features into the
selected style without replacing the person, changing age, haircut or product packaging.
This style overrides conflicting rendering words in draft asset descriptions, scene prompts,
reference photos AND earlier generated frames. Identity preservation does not mean copying
photographic/3D shading. On edits correct an existing style mismatch while preserving
composition and other unrequested details. Before returning inspect EVERY subject for
style adherence and reference identity, not only the main character.
"""



def reference_contract(d, refs):
    """Give uploaded identities priority over invented descriptions and frames."""
    originals={str(Path(r['path']).resolve()):r.get('purpose','')
               for r in d.get('refs',[]) if r.get('kind')=='photo'}
    if not originals: return ''
    mapping=[{'image':i,'role':'USER ORIGINAL — identity authority for portraits; wardrobe authority for outfits; product authority for objects' if str(Path(p).resolve()) in originals else 'GENERATED FRAME — composition/continuity only',
              'preserve_request':originals.get(str(Path(p).resolve()),'')}
             for i,p in enumerate(refs,1)]
    for item,p in zip(mapping,refs):
        if d.get('style_reference',{}).get('path')==str(p): item['role']='STYLE ONLY — never identity or composition'
    instructions=d.get('photo_reference_instructions','')
    return '\nUSER REFERENCE INSTRUCTIONS: '+instructions+'\n'+'''
USER REFERENCE IDENTITY CONTRACT (overrides conflicting creative descriptions):
Inspect every USER ORIGINAL before generation. Match its subject to the corresponding
story character or object by appearance and context. Determine each reference's role:
identity, outfit/accessories, product, or style. A clothing flat lay, outfit collage or
garment photo is WARDROBE authority, not another character identity. Use its garments,
colors, cut, layers, shoes and accessories on the intended character; do not copy a
clothing model's face or hair. With one heroine portrait and one outfit photo, combine
the portrait identity with that outfit, not with invented script clothes. An empty
caption does not make an uploaded reference optional. Apply an outdoor outfit at the
scripted dressing/going-out beat, not to earlier sleepwear beats. Explicit user scope
overrides this default. Never transfer shopping-collage text or layout into the scene.
User wardrobe references override earlier invented clothes in scripts and frames.
For people preserve recognizable facial identity, actual age, hair length, haircut and color
from the USER ORIGINAL; ignore conflicting invented hair/face/body descriptions in the script.
Do not copy another reference person's identity. Reference rendering is not identity: redraw
that same person in the selected style. Keep facial landmarks recognizable under stylization.
For animals retain species, recognizable muzzle/ear shape, eye color, coat color and
marking layout. These identity cues do NOT require photographic fur or realistic shading.
For stylized 3D, translate the same animal into sculpted animated-character forms with
grouped fur shapes; keep it recognizably the same animal, not a generic replacement.
Do not retain a photographic animal just because its identity is locked. Selected rendering
applies equally to animals and humans, including on revisions and later scenes.
For products preserve the exact shape, proportions, colors, material, cap/pump,
packaging, label layout, brand logo and visible lettering from the original photo.
Never replace a supplied product with a generic or fictional product. Earlier screenplay
descriptions and generated frames may be wrong: original user photos take priority over
them, even when an earlier frame was approved. Explicit user requests to change that
identity are the only exception. Generic 'no text/no logos' rules prohibit added overlays,
not existing labels on reference products. Cartoon styling may adapt rendering; it must
not redesign the packaging. Scripted arms/eyes must not hide or replace the product label.
Generated frame references govern composition and locations, not conflicting product identity.
Pass original reference photos to the image generation tool, not just a text description.
Before returning, inspect the generated image against the originals for subject identity,
product shape, dominant color and label. Do not claim a generic substitute matches.
REFERENCE ATTACHMENT MAP:
'''+json.dumps(mapping,ensure_ascii=False)+'\n'

ANATOMY_RULES = """ANATOMY AND POSE:
Preserve each character's approved anatomy. For an ordinary human use two arms and two
legs, one hand per arm; no extra/floating/fused limbs, duplicate hands or merged fingers.
Respect explicitly designed anatomy, disabilities, animal species and the selected
cartoon finger convention. Hidden or cropped limbs stay hidden, never add copies to
make all limbs visible. Attach each visible limb to the correct character and joint.
Assign each hand one physically possible task. If both hands hold a bottle and comb,
do not add a third hand touching the head. Put successive actions into separate poses
or moments; do not depict all steps simultaneously. Object contact and grip must make
sense. Mirror reflections repeat the same pose and anatomy, not another set of hands.
For edits replace/remove the defective limb instead of drawing a new hand over it.
Before returning visually check every character's limbs, joints, hands, fingers, grips
and reflections; preserve identity, intended action, framing and selected style.
"""

OBJECT_HERO_RULES = """OBJECT MAIN CHARACTER:
When the story's main character is a product, bottle, package or other inanimate object,
design it as an expressive cartoon character from the FIRST generated visual: two arms
with hands, two legs with feet, eyes and a mouth, all in the selected rendering style.
Include this anatomy in its asset description and image prompts; keep it consistent
throughout the film. Frame it so the anatomy is readable unless the shot calls for a close-up.
A realistic product reference defines the recognizable bottle/body shape, cap, colors,
label and branding. Preserve these while translating the object into the selected cartoon
medium and adding limbs and a face; do not substitute generic packaging or hide the label.
Keep the original product shape, packaging design, label, text and visual identity unchanged.
Do not redesign the product or alter its name, logo, brand colors, lettering, label layout
or packaging details. Eyes and mouth must not cover the name or break the label; attach
arms and legs without deforming the package. Keep existing text readable and unobstructed.
The face and limbs complement the recognizable product, never replace its body. Preserve
the same face design, limb design and placement along with the same packaging in later scenes.
For a product hero include concise relevant negatives: no product/label/text/logo/packaging
or brand color changes, no distorted shape, no hidden label, no extra or missing limbs.
This includes cups, cans, boxes, fruit, cosmetics and phones when cast as the protagonist.
This applies only to the object cast as a MAIN CHARACTER, not background props, ordinary
products held by a human protagonist, or every object in the scene. Do not add a speaking
line unless the screenplay assigns one. Respect explicit user requests for no limbs/face
and later design edits. Never modify a user-supplied ready visual automatically.
"""

VISUAL_RULES = EDIT_PRIORITY + STYLE_CONTINUITY + ANATOMY_RULES + '''VISUAL CONTINUITY LOCK:
The first approved depiction establishes each character and location for this story.
Preserve each character's face, species, age, gender, body proportions, hair/fur/feathers,
colors, clothing, accessories and distinctive features in every subsequent scene.
Preserve the reference haircut, hair length, hair color and hairstyle in every location.
Never invent a bun, ponytail or longer hair for bedtime or waking up. Keep the actual established haircut; no default haircut is prescribed.
Change hair only when the user explicitly requests it. Clothing changes do not imply
a hair or face change. Original reference hair overrides invented screenplay hair.
For the SAME location preserve its architecture, room layout, doors, windows, furniture,
materials, palette, lighting and object positions. A camera angle or close-up changes
framing only, never the room or character design. Use previous scene images as visual
evidence for the corresponding characters and locations, including when returning to a room.
Change a character, wardrobe, location, weather or lighting ONLY when explicitly required
by the approved screenplay or requested edit. A new location must not redesign the cast.
Do not copy a previous pose/action instead of the current scripted action.
For an edit preserve every unrequested detail and do not silently modify other frames.
'''

VOICE_RULES = '''RECURRING VOICE CAST LOCK:
Use one fixed voice identity for each named speaking character across the entire story.
Preserve the character's voice gender, perceived age, pitch range, timbre, resonance,
accent, speaking rate, articulation and habitual delivery. A male voice stays male in
every scene; a female voice stays female. Never swap voices between characters.
Use a lively, slightly brisk natural conversational pace with clear articulation and only
brief purposeful pauses; avoid slow, drawn-out delivery or long silent gaps. Keep speech
easy to understand and never rushed.
Emotion may change performance and volume, not the underlying voice identity.
Match each exact approved line to its named speaker; only that character speaks and
moves their mouth. Do not add speech to a silent character, object or silent scene.
The shared cast descriptions below override contradictory scene-level voice styling.
'''

SCRIPT_RULES = VISUAL_RULES + VOICE_RULES + OBJECT_HERO_RULES + '''
FAMILY SCENE WRITING:
For ordinary family, school, grooming and product stories, default to comfortable,
cheerful characters and gentle everyday actions. Do not invent crying, pain, panic,
humiliation, pulling hair, forceful handling or danger just to create a dramatic hook.
Use concrete positive action descriptions rather than unnecessary distress imagery or
repeated negative prohibitions. For example, a caring parent gently combs hair while
the child sits comfortably; do not invent a child suffering as a product demonstration.
This is a content choice, not a keyword filter or a moderation workaround. Never conceal
ages, relationships or unsafe actions with euphemisms. Preserve user-supplied stories
and exact dialogue unless the user approves a substantive change; do not silently
sanitize or rewrite them. Do not promise that a safe script will pass a provider filter.

Write one concise fixed VOICE PROFILE (at most 160 characters, one line) inside each speaking character's asset description:
name, voice gender consistent with the story, perceived age, pitch, timbre, resonance,
accent, a lively slightly brisk conversational pace, and clear articulation. Keep it
natural and intelligible, not rushed; avoid slow delivery and long pauses. For non-speaking
objects state no voice. Establish these
once for the whole screenplay, not separately for each scene. Repeat the SAME profile
verbatim in AUDIO of every video_prompt where that character speaks. Never infer a new
voice from the scene emotion. Respect any voice description supplied by the user.
Describe recurring locations consistently in image_prompt, including their stable layout.
'''


def voice_prompt(d):
    return VOICE_RULES + '\nSHARED APPROVED CAST (same for every scene):\n' + json.dumps(
        d.get('script', {}).get('assets', []), ensure_ascii=False)


CAST_ANALYSIS_RULES = """
Inventory EVERY distinct visible character across all sampled frames in characters.
For each record stable id, appearance, visible role, ordered frames of appearances and
uncertainty. Do not assume two women are the same person. Compare faces, hair, clothing
and context; a new outfit alone does not establish a new person. Do not invent family
relationships. Note ambiguous identities/roles in the summary for user clarification.
Keep the summary and character inventory consistent; include briefly appearing people.
Record each character's clothing separately for every location/time interval. State
which same character returns in a different outfit; keep identity and wardrobe separate.
Fill shots for the WHOLE video, not just its opening. Split at visible changes in location,
wardrobe, cast, props or action. Record approximate start/end seconds, the location and
each present character_id from the inventory, their outfit, carried/used props, actions,
camera and transition. Describe garment types, colors and distinguishing accessories.
Track who holds which object and who enters/exits; do not transfer props to another person.
Record uncertainty rather than inventing details hidden or absent from sampled frames.
These rules apply to every source video, regardless of setting, genre or character count.
"""

CAST_REPLACEMENT_RULES = """
CAST AND REPLACEMENT SCOPE: In each asset reference_photo_ids list only the numbered
photo_references assigned to THIS subject. Use [] for every other character/object.
Assign each user photo to at most ONE asset. Never clone one photo across different cast
members. If a user supplies one photo, only one character receives that identity.
Preserve every distinct character from the video inventory
unless the user explicitly removes or merges them. Give each a separate asset identity.
The explicit video_reference_instructions override conflicting generic brief.hero text and
incorrect earlier asset descriptions about who should match a user photo.
A photo replacing ONE character (for example the wife) applies ONLY to that character,
never all characters or all women. Other characters retain distinct faces, gender, age
and roles. User cast corrections supersede uncertain analysis. Do not invent relationships.
For image generation the current asset is the ONLY subject; a reference photo for another
cast member must NOT transfer its face to this asset. Identity preservation rules apply
only to the matching subject, not indiscriminately to every attached photograph.

SOURCE SHOT CONTINUITY: When analysis.shots exists, carry its location-specific outfits,
props, character participation and transitions into the corresponding timed production
prompts. Match source character_id to the approved replacement by role; source IDs are
NOT attachment numbers. User replacements change only the requested traits. Keep all
other scene details, subject to explicit approved edits. Do not infer unseen details.
One portrait depicts ONE character in ONE outfit (their first appearance), never a collage
of outfits or duplicate bodies. Put alternate outfits in the relevant timed scene prompts.
In image_prompt describe the outfit for that scene only; in video_prompt repeat the full
outfit and stable image N identity at every change and return. A generic 'preserve costume'
must not override a planned clothing change. Do not invent a new person for another outfit.
"""

DEVICE_GEOMETRY = '''DEVICE GEOMETRY — apply on the first render as well as edits:
A laptop has one display on the INNER side of its lid, facing its keyboard and the user.
The outer lid is plain casing, never a second display or mirrored interface. A phone/tablet
has a screen on its FRONT and casing/cameras on its BACK, never a screen on both sides.
If the viewer sees the back, show the back. When screen content must be visible, use an
appropriate side or over-the-shoulder view without moving the screen onto the back.
Keep hinges, hands, gaze and device orientation physically consistent. Check before output.
'''


def approved_frame_authority(d, refs):
    if d.get('route')!='omni': return ''
    first=d.get('images',{}).get('1',{})
    if not first.get('path') or first['path'] not in refs: return ''
    number=refs.index(first['path'])+1
    return (f'\nAPPROVED CHARACTER AUTHORITY: image {number} is the LATEST scene 1 frame. '
            'For characters visible in it, use its actual face, age appearance and hairstyle across scenes. '
            'It overrides conflicting original asset descriptions, original photos and older scene frames. '
            'Other scenes provide design and room geometry, NEVER a template for this scene composition/action. '
            'The CURRENT scene alone governs pose, seated/standing placement and camera. Do not copy scene 1 pose, closed eyes '
            'or clothing into a different action/location unless required there. Characters absent from '
            'scene 1 keep their separate identities. A new explicit user correction still has priority.\n')
