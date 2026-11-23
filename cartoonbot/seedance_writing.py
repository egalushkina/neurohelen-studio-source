"""Shared authoring rules for every Seedance version and supported duration."""
RULES = '''SEEDANCE PROMPT WRITING STANDARD (all versions: 2 Fast, 2, 2.5; 15s or 30s).
Write asset descriptions, wardrobe descriptions and video_prompt fields in English. Preserve approved dialogue VERBATIM in its
original selected language, with its named speaker. Never translate or shorten speech.
Use this order in each complete production prompt:
1. REFERENCES: list every actually attached file as @Image1, @Image2, etc., in exact
attachment order. Explain each role (identity, clothing, object or complete scene frame).
Never invent @Video or @Audio attachments: this product analyzes source videos upstream
and does not send them to Seedance. Photo identities and outfit references are distinct.
2. FORMAT: requested 9:16 or 16:9 and exact requested duration. Keep 30 seconds when
selected for Seedance 2.5; do not impose a universal 15-second limit.
3. STYLE AND PALETTE: the selected visual medium and concrete colors. Clay means visible
handmade plasticine, not glossy CGI. Keep animation style coherent across all subjects.
4. LOCATION AND ATMOSPHERE: concrete layout, lighting, time of day and mood from the
approved screenplay. Never add a location or change the plot for cinematic effect.
5. TIMELINE: distinct intervals [0–3s], [3–7s], etc., covering the full selected duration.
Describe subject action, shot framing, camera movement and filming mode (stabilized,
handheld or drone where appropriate) in every interval. Examples: slow push in, tracking
shot, orbital shot, low angle, crash zoom. Use a static camera when the approved shot is
static; do not add motion merely to fill a field. Do not invent extra scenes or cuts.
6. TECHNICAL REQUIREMENTS at the end: Stable face throughout. No morphing. No deformation.
No flickering. No ghosting. Realistic physics. 4K cinematic.
The phrase 4K cinematic describes an aesthetic only: do not claim to change API output
resolution. Do not force film grain, IMAX, drone shots or 120fps into every animation.
Use slow motion 120fps or snap to real-time only when requested and compatible with the
approved action, dialogue timing and available duration.
CHARACTER: describe approved hairstyle, face and body proportions once in the reference
section, then refer to its tag. Use @ImageN as strict identity reference. Preserve exact
likeness throughout. No beautification. No deformation. Stable face throughout.
Describe clothing separately: garment, colors, layers, cut and accessories. Explicit
approved costume changes affect clothing only. Keep all recurring voices consistent.
In timeline blocks, only one character speaks at a time; other characters listen with
closed mouths. Use slightly brisk natural speech with intelligible words and short pauses.
Use concrete descriptions rather than cool, nice or beautiful. No conflicting directions.
Do not create a separate negative-prompt block; use positive scene descriptions and the
short technical tail above. Add only scene-relevant constraints beyond that tail.
Never add celebrity likenesses or copyrighted characters. Preserve approved character
identity and selected style. Do not claim that a full-body photo bypasses moderation;
never silently rewrite approved content to evade a rejection.
Fit the complete submitted prompt within 4000 characters, including references, dialogue
and technical tail. Remove redundant prose first; never truncate approved dialogue.
'''
