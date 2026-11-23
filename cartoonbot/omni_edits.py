"""Resolve an approved scene edit before sending a single consistent video brief."""
from .schemas import obj, S
from .dialogue_text import RULES as DIALOGUE_RULES, normalize_scene

SCHEMA = obj({'action': S, 'dialogue': S, 'camera': S})
RULES = '''Strict adult-character wording: in generated action, camera, asset and video prompt descriptions use «женщина» (with correct Russian inflection) or «woman» for an adult female, never «девушка» or «girl». Preserve actual ages: this rule does not turn children into adults. Preserve exact approved spoken dialogue.
Apply the user's approved edit to this ONE animated scene. Return the final
action, dialogue and camera, not editing instructions, old/new alternatives or a commentary.
If a replacement scene is supplied, replace conflicting old actions, dialogue and camera.
Otherwise preserve all unrequested details and exact approved dialogue. A request to regenerate
without changes preserves the scene. Use the attached approved frame for visible identities.
Do not repeat appearance descriptions. Keep actions concrete and concise.
Never conceal actual age, harm or sexual meaning to bypass a provider's safety filters.
A request to remove 'trigger words' means clear, accurate wording without irrelevant boilerplate,
not a license to disguise the scene or invent a different plot. Do not add peril or violence.
Keep the original language. This is text preparation only; do not generate media.'''

def resolve(provider, job, d, scene, instruction, refs):
    result = provider.structured(job, RULES+DIALOGUE_RULES, {
        'scene': {k: scene.get(k, '') for k in ('action', 'dialogue', 'camera')},
        'approved_edit': instruction,
        'language': d['lang'],
    }, SCHEMA, images=refs)
    if not isinstance(result, dict) or any(not isinstance(result.get(k), str) or len(result[k]) > 4000 for k in ('action', 'dialogue', 'camera')) or not result['action'].strip():
        raise ValueError('invalid_scene_edit')
    normalize_scene(result)
    return {**scene, **result, 'video_prompt': 'ACTION: '+result['action']+'\nCAMERA: '+result['camera']+'\nDIALOGUE: '+result['dialogue']}
