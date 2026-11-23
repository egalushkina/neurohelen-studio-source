"""Dialogue revisions remain proposals until the user explicitly accepts."""
def pending_scene(d):
    from .omni_prompts import crowded_dialogue_scenes
    check=dict(d)
    targets=set(d.get('resume_scene_ids',[]))
    check['videos']={k:v for k,v in d.get('videos',{}).items() if int(k) not in targets}
    crowded=set(crowded_dialogue_scenes(check))
    accepted=d.get('dialogue_timing_accepted',{})
    return next((s for s in d.get('script',{}).get('scenes',[]) if s['id'] in crowded and accepted.get(str(s['id']))!=s.get('dialogue','')),None)


def validate_proposal(original, proposed):
    from .omni_blueprint import speech_schedule
    before=speech_schedule(original); after=speech_schedule(proposed)
    if not after or [t['speaker'] for t in before]!=[t['speaker'] for t in after]:
        raise ValueError('seedance_dialogue_mismatch')
    if len(proposed)>4000: raise ValueError('script_too_long')
    return proposed
