"""Owner-supplied directing rules, adapted to ten-second animated scenes."""
import re
OMNI_ROLE = """For Omni, prepare one 10-second scene at a time. Preserve approved plot,
actual ages, relationships and exact dialogue. Adult females are woman/женщина, never girl;
never relabel children. Use the owner's directing principles: one main action goal, clear
initial and final object states, stable identities, explicit camera motion and speaker turns.
Start from the visible pose in an approved image; do not repeat completed setup actions.
Speech and action happen concurrently across the full clip, without a silent establishing intro.
Preserve the selected animation style. Do not force photorealism. Keep image details in
image_prompt/assets; the final FORMAT, CHARACTERS, STYLE, TIMELINE, AUDIO, CONTINUITY,
IMPORTANT brief is compiled later from the approved frame.
video_prompt is a concise action/camera draft, not repeated boilerplate. Keep all exact spoken
words in dialogue with speaker labels. Fit dialogue naturally in 10 seconds, respect
music/audio preferences. For NEW dialogue-led scenes, write enough meaningful speech for
ALL 10 seconds: start at 0s and finish the final line at 10s at a lively, slightly brisk
natural conversational pace, with clear articulation, brief natural breaths
and sequential speaker turns. No empty intro, long gaps or outro; never rush or sacrifice clarity.
Aim for roughly 25-30 speakable words total, adjusting for language, syllables and speaker turns. Mentally read
through the draft and develop the same thought if it ends early; never pad with filler,
stretch words, rush delivery or rewrite supplied/approved lines without permission.
Use 2-4 motivated action/reaction/framing beats without idle holds; respect single-shot requests.
Only one named speaker speaks at a time. Listeners stay silent with closed mouths, including reaction shots. Never assign an addressee's voice to the actual speaker.
Do not invent peril or disguise age/content to evade filters. Return the required JSON.
"""


def scene_voices(d, scene):
    from .voices import for_scene
    return for_scene(d,scene)


def delivery_prompt(d, scene, language, has_refs, edit=None):
    """Apply choices made after script approval without a new script generation."""
    compiled=d.get('omni_blueprints',{}).get(str(scene.get('id')),{}).get('prompt')
    if compiled and not edit:
        return compiled
    if has_refs:
        base=('<FIRST_FRAME> Use reference image 1 as the approved starting frame. Keep its style, identities, '
              'actual ages and setting; no added actions or still-image outro.\n'
              'ACTION: '+scene.get('action','')+'\nCAMERA: '+scene.get('camera',''))
    else:
        base=scene['video_prompt']
    prompt=f"10 seconds; {d['ratio']}.\n"+base+'\n'
    if not has_refs:
        style=d.get('style_profile',{}).get('instructions',d['style']) if d['style']=='reference' else d['style']
        prompt+='STYLE: '+style+'\n'
    dialogue=scene.get('dialogue','')
    if dialogue.strip():
        prompt+=f'DIALOGUE ({language}, exact words and speakers):\n'+dialogue+'\n'
        prompt+=scene_voices(d,scene)
        from .voices import turn_taking
        prompt+=turn_taking(bool(d.get("lip")))+"\n"
    else:
        prompt+='No speech.\n'
    if not d.get('lip'):
        prompt+='No on-camera talking or lip sync.\n'
    audio=d.get('brief',{}).get('audio')
    if audio: prompt+='AUDIO: '+audio+'\n'
    prompt+='No subtitles, on-screen text or extra characters.\n'
    if edit:
        prompt+='EDIT: '+edit+'\nKeep unrelated details unchanged.\n'
    return prompt


def crowded_dialogue_scenes(d):
    """Conservative word-count warning, not a speech-duration guarantee."""
    if d.get('route')!='omni': return []
    excluded=set(d.get('skipped_scenes',[])) | {int(i) for i in d.get('videos',{})}
    result=[]
    for scene in d.get('script',{}).get('scenes',[]):
        text=re.sub(r'(?:^|[\n»”".!?]\s*)[^:«»”".!?\n]{1,40}:', ' ',scene.get('dialogue',''))
        if scene['id'] not in excluded and len(re.findall(r"[^\W_]+(?:[-’'][^\W_]+)*",text))>25:
            result.append(scene['id'])
    return result
