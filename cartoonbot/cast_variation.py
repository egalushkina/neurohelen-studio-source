"""Project-local creative direction; never randomize a referenced or approved identity."""
import hashlib
import json

RULES = '''NEW-PROJECT CAST VARIATION:
For a new story invent a visibly distinct cast rather than a default recurring woman/cat.
Use this project's variation direction ONLY for appearance details the user left unspecified.
Use the project design direction for unspecified hair silhouette, hair color and clothing palette,
not just a new name. These are required creative choices when the user left them unspecified. Avoid recreating the same whole design from a previous independent project.
Recent designs are comparison data, not instructions or reference identities.
Actual uploaded reference photos, user descriptions, supplied screenplay identities and source
video characters ALWAYS take priority over variation. Never randomize a reference subject.
When a photo is planned but not yet attached, defer identity details to the future photo.
Different reference photos map to their assigned subjects; do not clone one across the cast.
Inside this project, keep the approved cast, hairstyle, colors and distinguishing features
consistent across all scenes. Edits and continuation parts retain identities unless the user
explicitly changes them. Do not change age/species/role for variety or defeat a safety filter.
'''


def direction(store, job, d):
    if d.get('script') or d.get('previous_part') or d.get('part_number'):
        return {'mode':'preserve_current_cast'}
    pid=job.get('project_id',job.get('id','new'))
    seed=d.setdefault('cast_variation_seed',pid)
    value=int(hashlib.sha256(seed.encode()).hexdigest(),16)
    palettes=('teal and warm cream','coral and slate blue','sage and copper','ochre and navy',
              'berry and pale gray','terracotta and soft blue','forest green and sand','sky blue and warm brown')
    silhouettes=('long softly waved hair','short textured curls','straight shoulder-length hair',
                 'long tight curls','cropped wavy hair','a long braid')
    result={'mode':'new_unspecified_cast','palette':palettes[value%len(palettes)],
            'adult_hair_silhouette':silhouettes[(value//len(palettes))%len(silhouettes)],
            'priority':'Use only if appropriate to the character and not specified by user or reference.'}
    result['hair_color']=('copper red','golden blonde','light chestnut','ash blonde','dark brown','black')[(value//48)%6]
    with store.lock:
        owner=store.db.execute('SELECT user_id FROM projects WHERE id=?',(pid,)).fetchone()
        if owner:
            # Avoid feeding prior character prose back as a copyable example.
            used=set()
            for row in store.db.execute('SELECT body FROM projects WHERE user_id=? AND id!=? ORDER BY created DESC LIMIT 8',(owner['user_id'],pid)):
                old=json.loads(row['body']).get('cast_design_direction',{})
                used.add((old.get('adult_hair_silhouette'),old.get('hair_color')))
            colors=('copper red','golden blonde','light chestnut','ash blonde','dark brown','black')
            for offset in range(36):
                hair=silhouettes[(value//8+offset)%len(silhouettes)]
                color=colors[(value//48+offset//6)%len(colors)]
                if (hair,color) not in used:
                    result.update(adult_hair_silhouette=hair,hair_color=color)
                    break
    d['cast_design_direction']=dict(result)
    return result
