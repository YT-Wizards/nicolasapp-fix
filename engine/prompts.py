"""Single source of truth for VYT's approved FaceTuber-style visual language."""

import re

STYLE_CORE = """
An ordinary paused frame from a factual 1080p YouTube video recorded on a consumer smartphone or camcorder. Candid real-life footage, not a professional photograph. Natural perspective around 24–35 mm equivalent, readable surroundings, available window light indoors or ordinary daylight outdoors. Neutral white balance, moderate contrast and natural restrained colours. Exposure stays consistent. The framing is practical and believable, not staged for an advertisement.

The image shows one clearly readable factual moment. Surfaces behave like their actual materials: skin retains irregular natural texture without beauty retouching, fabric has ordinary folds, fur forms irregular soft clumps rather than individually polished hairs. Metal, glass and moist surfaces may have small physically appropriate reflections; do not turn everything into matte plastic or polished CGI. Add wear only where the scene calls for a used object, never a layer of dirt across the image. Very subtle 1080p video compression may soften the finest detail, but keep the subject clear. No deliberately added grain, block noise, blur, grime or damage. No dramatic lighting, HDR, shallow cinematic bokeh or commercial gloss.

No presenter looking into camera unless the narration specifically requires an interview. When a beat is explicitly marked for presenter continuity, the supplied presenter's identity may appear naturally inside the real-world action, never as a generated talking head. No invented text, labels, logos or brand packaging unless real branding is enabled and the narration explicitly names that brand. No captions, no title cards, no graphic design.
""".strip()

VIDEO_STYLE_CORE = STYLE_CORE.replace(
    "An ordinary paused frame", "An ordinary continuous shot"
).replace("The image shows", "The footage shows")

NEGATIVE_CORE = """
cinematic grading, advertising gloss, dramatic artificial lighting, HDR halos, waxy skin, plastic fur, CGI rendering, artificial micro-detail, excessive sharpening, heavy compression artifacts, added grain or dirt, camera shake, distorted anatomy, extra limbs, fused fingers, cloned subjects, duplicated props, impossible geometry, captions, title cards, collage
""".strip()

PHYSICAL_INTEGRITY_LOCK = """
PHYSICAL INTEGRITY LOCK: Use only the people and objects necessary for the described moment. Keep each person's anatomy natural: no extra or fused limbs or fingers. Natural cropping and occlusion are allowed; do not expose hidden limbs just to make them countable. If hands are necessary, use one easy-to-read grip or gesture, with the hand visibly connected to its wrist and the object resting in the palm or against the fingers. Avoid overlapping hands, tangled fingers and several people handling the same small object. If hands are not needed, frame the actual subject without inventing a hand demonstration. Do not clone people, animals or props. Preserve normal size, solid shape, gravity, contact and perspective; objects must not intersect bodies or float above their supporting surface.
""".strip()

VIDEO_MOTION = """
One continuous ordinary real-world shot with a locked-off stable camera. No handheld drift, shake, zoom, pan, orbit, focus breathing, exposure pumping, particles or cuts. Begin on the requested subject already in place. Show one restrained action at normal speed, or a quiet observable posture when action is unnecessary. Do not combine preparation, action and result. A person may hold a position after one short action instead of repeating it to fill the clip.

Keep the same people, objects, room layout, lighting and background throughout. An object may be naturally occluded, but must not multiply, change shape or appear in a different hand. No morphing or location change. For an essential opening action, maintain the same door, hinge side and visible adjoining space; never use the opening as a transition into another scene. If a moving interaction would be ambiguous, use its stable setup or visible result without inventing a sequence. No generated speech, text overlays, interface, watermark or talking head. Presenter identity references apply only when a presenter-continuity lock is explicitly supplied.
""".strip()

SCENE_FEASIBILITY = """
RELIABLE SCENE DESIGN: Write one concrete visible moment, not the whole narration acted out. State who or what is visible, the necessary object count, the simple pose or action, and where the important objects sit relative to one another. Add no incidental cast or busy background. Prefer a supported object resting on a surface or one person in a clear side/three-quarter view over intricate hand choreography, mirror views or occluded multi-person interaction. Do not force hands or a full body into an object-only shot. When illustrating an abstract claim, show literal evidence or an ordinary context, not glowing symbols or an invented physical event. Preserve the exact factual meaning: simplify staging, never invent a different fact. For comparisons choose one representative detail per beat, not two scenes inside one frame. A still image captures one instant; a clip contains at most one small action. Describe visual content only, never ask for prompt instructions to appear as printed text.
""".strip()

PLANNER_SYSTEM = """
You are the visual director for an automatic long-form factual YouTube documentary. Your job is semantic fidelity, not decoration. Every visual must depict the concrete meaning of its exact narration beat. Never use a vaguely related symbol when a literal real-world action, object, person, place or process is available.

The production style is ordinary, authentic YouTube documentary B-roll: factual, candid, matte, uncinematic, readable, and slightly imperfect. Never invent facts. Never add written words into the scene. Named recurring subjects must use the continuity description supplied in the story bible. Generic people or animals may vary naturally. Continuity means a believable shared world: keep the same supported environment, material, equipment and era through one passage. Do not force a generic person or animal to have an identical face in unrelated passages.

Return strict JSON only. Do not include Markdown.
""".strip()

STORY_BIBLE_PROMPT = """
Read this complete transcript and build a compact continuity bible for visual production.

Three attached images, when present, are candidate frames of the real HeyGen presenter. They show the same person at different moments. Describe only stable visible identity traits shared by the candidates and choose the clearest reference. Do not infer biography, ethnicity, health, profession or personality.

Return this JSON object:
{{
  "topic": "one sentence",
  "era_and_places": ["factually supported setting"],
  "recurring_subjects": [
    {{"id":"short_id","name":"name or descriptive label","kind":"person|animal|place|object","visual_identity":"stable factual appearance; do not invent unsupported specifics","evidence":"short transcript evidence"}}
  ],
  "presenter_profile": {{"reference_index":1,"stable_identity":"age range, face shape, hair, facial hair and other stable visible traits","wardrobe_anchor":"recognisable clothing/accessory colours that may be preserved when natural","source_set":"brief factual description of the original set"}},
  "presenter_reuse_strategy": "selective|none",
  "presenter_reuse_reason": "selective only when the host can naturally demonstrate recurring everyday actions; none when B-roll should depict victims, criminals, historical people, named experts or unrelated demographics",
  "presenter_pattern": "balanced|alternating|full_led",
  "visual_strategy": "hybrid|motion_led|image_led",
  "explicit_brands": ["only brands literally named"],
  "safety_notes": ["visual facts that must not be fabricated"]
}}

Branding enabled: {branding}

VISUAL STRATEGY RULE:
- hybrid: animal behaviour, relationships, conceptual advice or topics where paused evidence and moving actions both matter.
- motion_led: list-based safety, practical demonstrations, incidents or process explainers where a concrete human action, object interaction or consequence carries most clauses. Use only when each action can remain physically simple.
- image_led: archival, object-led or explanatory topics where a paused factual frame is clearer than invented action.

PRESENTER REUSE RULE:
- selective means roughly five percent of the final timeline may reuse the presenter as a character inside candid B-roll. Use it only for a generic host demonstration that strengthens continuity.
- none means the presenter remains only in the source avatar and split-screen returns.

PRESENTER RETURN PATTERN:
- alternating: narrator/explainer videos where FULL and exact 50/50 SPLIT should alternate, with the presenter always on the left in split.
- full_led: first-person authority, ranked lists and host-led inspections where nearly every return should be the full presenter and split should be rare.
- balanced: practical/animal stories that use more full presenter than split but still need both.

TRANSCRIPT:
{transcript}
""".strip()

SCENE_BATCH_PROMPT = """
Create the visual instructions for these timed beats. Each item already has a required visual type. Treat consecutive beats as one editorial passage: preserve the same supported place, era and recurring subject while changing the concrete action or framing. Never reuse an identical scene description merely because two beats are adjacent.

For each beat return exactly one object with:
- id: copy the supplied id
- literal_subject: the single concrete thing the viewer should see
- image_prompt: only for type "image", "split" or "still"; the exact visual content in English: subject, action, environment, place and era only. Empty string for type "video". Do not repeat the approved style because VYT appends it centrally. No alternatives and no camera-option lists.
- video_prompt: only for type "video"; describe one continuous real-world shot and restrained physical movement. Empty string for other types.
- continuity_ids: IDs from the story bible that genuinely appear
- named_brand: only a literally named brand if branding is enabled, otherwise empty
- presenter_broll: boolean; true only for a type "video" beat in which the real presenter can naturally demonstrate the exact generic action. Never use it for a victim, criminal, historical figure, doctor/expert, named third party or a different demographic.
- presenter_broll_reason: one short concrete reason when true, otherwise empty
- presenter_broll_value: integer 0–5 measuring how much identity continuity improves this exact beat; 0 whenever presenter_broll is false
- reject_if: 3–6 short scene-specific visual errors

Do not write captions into any image. The presenter normally appears only through avatar and split beats. The sole exception is a video beat explicitly marked presenter_broll=true: use the stable presenter identity from the story bible as the person candidly performing the action, not speaking to camera. A split beat requires an image that complements the presenter rather than another presenter. Use the neighboring context only for continuity; do not create output objects for it.

FIRST-PERSON IDENTITY LOCK: If the narration says "my name is", "I worked", "I ran", "I sat", "I retired", or otherwise gives the narrator's biography, keep the real source presenter as the visual anchor. Never invent a new talking head or switch between unrelated faces. For an explicit across-the-table or interview beat, show the interaction from a natural side view with the same people throughout, never a direct-to-camera portrait.

Presenter B-roll is rare, never consecutive and must not exceed six percent of all beats. Prefer false whenever the narration is equally clear with an object, hands, an animal, a location or an anonymous subject.

Complexity rule: a video beat must contain one simple action with the minimum cast and props. Never ask a video model to stage a crowd, several simultaneous actions, a full rally, several balls or repeated equipment. In sport, explicitly state the exact participant count, exactly one ball, exactly one court/field and correctly oriented play. Use a detail or single movement rather than an entire match. The required type cannot be changed here, so simplify the action until it is robust.

SHOT GRAMMAR: each video is a single visual sentence. Choose only one of these reliable forms: (1) stable context view with almost no action, (2) one person performing one short action, (3) close detail of one object or pair of hands, or (4) one clear result/evidence shot. Never combine setup, action and result in the same clip. Never describe “then”, “before and after”, a sequence of steps, or a transformation. When narration contains several claims, depict only its most concrete visible noun or action. For products and practical advice, prefer hands, material, surface, tool and result details. For phone topics, use one person and one device with no readable screen. For animals, use one animal and at most one handler, one observable posture or gesture, and no breed change inside the shot.

APPROVED IMAGE STYLE:
{style}

NEGATIVE RULES:
{negative}

APPROVED VIDEO MOTION:
{motion}

STORY BIBLE:
{bible}

NEIGHBORING CONTEXT OUTSIDE THIS BATCH:
{context}

BEATS:
{beats}

Return: {{"scenes":[...]}}
""".strip()

SCENE_PLAN_REVIEW_PROMPT = """
Perform the final editorial review before any paid image or video is generated. Correct every supplied scene in place.

Rules:
- The visual must illustrate the exact words of its narration beat, not merely the general topic.
- Prefer literal actions, objects, places, evidence and cause/effect details over atmosphere or symbolism.
- Consecutive scenes in the same explanation should feel filmed during one coherent real-world passage, while each scene advances to a new useful detail. Preserve identity through factual text descriptions only; never require one generated image to become the opening frame of several later clips.
- Do not repeat an identical prompt, action or framing unless the narration explicitly continues that exact action.
- Preserve supported recurring identities and factual era/place. Do not invent unsupported specifics.
- Keep mundane YouTube realism. No cinematic polish, cards, captions, graphics, watermark or generated words. A presenter inside B-roll is permitted only when presenter_broll=true and it is a useful candid demonstration rather than a talking head.
- Reject any generated talking head when the narration does not explicitly require an interview or a named speaker's visible testimony. For first-person biography, the source presenter avatar is the preferred visual, even if the requested media mix would otherwise choose a generated clip.
- For video, require one simple continuous physically plausible action. For image/split/still, video_prompt must be empty.
- Reject and rewrite any video prompt that leaves actor count, object count or direction of action ambiguous. It must explicitly minimize them.
- For sport, require exactly one ball, one court/playing area, one correctly placed net when applicable, and the minimum participants (one for technique, two for singles, four only when doubles is explicit). Reject background players, adjacent active courts, crossed/duplicated nets, backwards play, multiple balls, simultaneous rallies and impossible trajectories.
- When the narration describes a broad match, comparison or group event, reduce the video to one representative setup, stance, grip, footwork step, serve, strike or return. Never stage the whole event at once.
- Reject verbs or connectors that imply multiple stages inside one clip: then, afterwards, transforms, before-and-after, opens to reveal, moves through several rooms, or performs several steps. Rewrite as one stable instant or one short action.
- For practical/home scenes, use one pair of hands, one tool and one work surface. For phone scenes, one person and one phone with no readable interface. For animal scenes, one animal and at most one handler with one stable observable behaviour.
- Perform a physical-integrity pass before approving every prompt. Remove nonessential visible hands and background people. If a hand is essential, specify one uncomplicated grip or gesture with at most one visible pair. Explicitly prevent extra/fused fingers or limbs, repeated faces, cloned people and duplicated props in reject_if.

Return one compact result for every supplied id, in the same order. Do not repeat an
already-correct prompt. For a correct scene return only {{"id":"...","pass":true}}.
For a scene that needs correction return pass:false plus only the fields that must be
replaced. Never change its type. Use this schema:
{{"scenes":[{{"id":"...","pass":true}},{{"id":"...","pass":false,"literal_subject":"corrected only when needed","image_prompt":"corrected only when needed","video_prompt":"corrected only when needed","continuity_ids":[],"named_brand":"","presenter_broll":false,"presenter_broll_reason":"","presenter_broll_value":0,"reject_if":[]}}]}}

STORY BIBLE:
{bible}

RECENT SCENES ALREADY APPROVED:
{recent}

SCENES WITH THEIR EXACT NARRATION:
{scenes}
""".strip()

IMAGE_REVIEW_PROMPT = """
Act as a strict internal quality reviewer for an ordinary factual YouTube documentary. Compare this generated frame to its narration and intended subject. Inspect anatomy and object counts deliberately, not as a vague overall impression.

Reject if any of these apply: semantically unrelated or merely generic to the overall topic; wrong named subject/species/place/era; cinematic or commercial polish; CGI/plastic/glossy look; intentionally dirty/vintage/heavily degraded; malformed anatomy or objects; invented visible words; watermark/logo when disallowed; presenter-style talking head; excessive blur; unusable composition. The image must support the exact sentence even when viewed without surrounding scenes.

MANDATORY PHYSICAL CHECK: count the visible people, heads, arms, hands and important repeated props. Reject any extra, fused, detached or duplicated limb, hand, finger, face or body; impossible joint, grip or body orientation; cloned person; repeated animal; duplicated tool/object; impossible contact, perspective, topology, scale or gravity. Normal cropping and genuine occlusion are allowed: do not reject a limb merely because it is naturally outside the frame or hidden. If hands are too small or obscured to judge, do not invent a defect; reject only a visible anomaly.

Do not reject merely because the frame is mundane, imperfect, compressed, or because a generic human/animal differs from another generic example.
Distinguish synthetic gloss from real material response: a small reflection on metal, glass, eyes or a moist nose is natural, not itself a defect. Clean objects need not be dirty. Judge whether the depicted contact and material are physically plausible; do not demand noise, matte skin or visible compression artifacts.

NARRATION: {narration}
INTENDED SUBJECT: {subject}
SCENE-SPECIFIC REJECTION RULES: {reject_if}

Return strict JSON: {{"pass":true,"semantic_score":0-100,"realism_score":0-100,"integrity_score":0-100,"issues":["..."],"retry_guidance":"one concise correction or empty"}}
""".strip()

VIDEO_REVIEW_PROMPT = """
Act as a strict internal reviewer. These three frames come from the start, middle and end of one generated B-roll clip. Inspect anatomy and object counts independently in all three frames and across time.

Reject if it is semantically unrelated, cinematic/CGI/glossy, has visible generated text, shows morphing/duplicated subjects, has a strange zoom or camera move, particles, discontinuity, repeated action, or unusable anatomy. Reject a door/gate/portal reveal that changes to an unrelated physical location unless that transition is explicitly required by the narration. Reject a clip that merely repeats the previous scene's likely composition instead of illustrating this exact beat. Reject unnecessary people or props, duplicated objects, multiple simultaneous actions, impossible geometry, impossible direction of travel or physically incorrect body orientation. In ball sports reject more than one visible ball, more than one court or net, crossed/duplicated nets, background players, a player facing away from the natural direction of play, more than two players unless doubles is explicit, or more than four players in any case. This must be one restrained real-world documentary shot, not an animation or montage. When PRESENTER REUSE is no, reject a generated presenter clip. When it is yes, the first attached image is the identity reference and the remaining strip shows the result: require the same recognisable person acting candidly, with no direct-to-camera speech and no face morphing. A tiny provider label reading only “Veo” in the extreme lower-right corner may be ignored because VYT removes that border during final rendering; reject every other watermark or logo.
IDENTITY AND TALKING-HEAD CHECK: If the narration is first-person biography, a generated face is invalid unless the exact beat explicitly requires a visible interview. If the narration does not require a named speaker or interview, reject any direct-to-camera talking head as generic filler. For an across-the-table interview, require the same two people and room across the strip, with no identity swap and no direct-to-camera speech.

MANDATORY PHYSICAL CHECK: in each frame count visible people, heads, arms, hands and important props, then compare those counts across the strip. Reject an extra/fused/detached limb, hand, finger, face or body; a cloned person; a duplicated animal or prop; an impossible grip, joint, contact, perspective, topology, scale or gravity; or a subject/object that appears, vanishes, merges or multiplies without a real occlusion. Allow natural cropping and occlusion. Do not claim a defect that is not visibly supported by the strip.
Natural small reflections on metal, glass, eyes or moist surfaces are allowed; artificial plastic rendering is not. Stable camera framing and a quiet posture are valid, not insufficient motion. Judge only visible defects; sampled frames cannot prove all intervening motion is correct.

NARRATION: {narration}
INTENDED SUBJECT: {subject}
SCENE-SPECIFIC REJECTION RULES: {reject_if}
PRESENTER REUSE: {presenter_reuse}

Return strict JSON: {{"pass":true,"semantic_score":0-100,"realism_score":0-100,"integrity_score":0-100,"motion_score":0-100,"continuity_score":0-100,"watermark":false,"issues":["..."],"retry_guidance":"one concise correction or empty"}}
""".strip()

SCENE_BATCH_PROMPT = SCENE_FEASIBILITY + "\n\n" + SCENE_BATCH_PROMPT
SCENE_PLAN_REVIEW_PROMPT = SCENE_FEASIBILITY + "\n\n" + SCENE_PLAN_REVIEW_PROMPT


def scene_specific_constraints(scene: dict, medium: str) -> str:
    """Apply physical rules to depicted content, never unrelated topic keywords."""
    content = " ".join(str(scene.get(key) or "") for key in (f"{medium}_prompt", "literal_subject")).lower()
    rules = []
    if re.search(r"\b(pickleball|tennis|tenis|pádel|padel|badminton|bádminton)\b", content):
        rules.append(
            "RACKET-SPORT DETAIL: Prefer one player's ready stance, paddle grip or one small footwork step over a rally. "
            "For a visible playing court, show a single court with one net spanning its width, not crossed nets or adjacent games. "
            "Use one player for a detail, two for singles, four only for explicitly requested doubles. "
            "At most one ball in the depicted play; do not add a ball or net to an equipment-only close-up. "
            "Players face their target; keep equipment, limbs and court lines distinct."
        )
    if re.search(r"\b(golf|golfer|golfista|golfistas)\b", content):
        rules.append(
            "GOLF DETAIL: One golfer with one club; at most one ball when needed. Prefer an address posture, "
            "a small practice movement or a settled follow-through over a complete fast swing and ball flight. "
            "No extra equipment, background golfers or invented trajectory."
        )
    if re.search(r"\b(dog|dogs|puppy|canine|malinois|perro|perros|cachorro|cat|cats|kitten|gato|gatos)\b", content):
        rules.append(
            "ANIMAL DETAIL: Keep the specified species, coat and body proportions consistent. "
            "Show one observable posture or small gesture, not several behaviours. Four-legged animals retain "
            "their normal joint structure and paws; hidden paws may remain hidden. Fur has irregular soft clumps, "
            "not shiny strands or a plastic surface. Do not add another animal or a handler unless the scene needs one."
        )
    if re.search(r"\b(phone|smartphone|telephone|teléfono|telefono|móvil|movil)\b", content):
        rules.append(
            "PHONE DETAIL: One device per necessary user, held in one uncomplicated grip or resting flat on a surface. "
            "Keep its shape and hand contact stable. Turn the display away or keep it unreadable unless exact real content "
            "is explicitly provided. Do not invent chat bubbles, floating icons, digits or screen text to explain the narration."
        )
    return "\n".join(rules)


def image_prompt(scene: dict, retry_guidance: str = "") -> str:
    identity = str(scene.get("image_prompt") or scene.get("literal_subject") or "").strip()
    if scene.get("type") == "split":
        identity += (
            " Compose this as one normal full-frame scene with the essential subject, hands, object and action "
            "fully contained in the central 8:9 portrait-safe area, because that centre area will appear beside "
            "the real presenter. Keep the outer sides expendable background; do not create a split screen, panel, "
            "border, caption or second presenter inside the generated image."
        )
    correction = f"\nMANDATORY CORRECTION: {retry_guidance.strip()}" if retry_guidance else ""
    return (
        f"SCENE TO DEPICT: {identity}\n\n{STYLE_CORE}\n\n{PHYSICAL_INTEGRITY_LOCK}\n"
        f"{scene_specific_constraints(scene, 'image')}\n\nAVOID: {NEGATIVE_CORE}"
        f"{correction}\n\nCreate one full-frame camera image. All instructions describe visual appearance; none are text to print in the image."
    )


def video_prompt(scene: dict, retry_guidance: str = "") -> str:
    correction = f" Mandatory correction: {retry_guidance.strip()}." if retry_guidance else ""
    presenter_lock = ""
    if scene.get("presenter_broll"):
        identity = str(scene.get("presenter_identity") or "the person in the attached identity reference").strip()
        presenter_lock = (
            " PRESENTER CONTINUITY LOCK: use the attached reference as the same real person in this scene. "
            f"Preserve these stable visible traits: {identity}. Preserve recognisable face, age, hair and facial hair; "
            "adapt pose and practical clothing only as the action naturally requires. The person is absorbed in the task, "
            "does not address the camera, does not lip-sync and is not framed as a talking head."
        )
    content = str(scene.get("video_prompt") or scene.get("literal_subject") or "").strip()
    return (
        f"SCENE TO DEPICT: {content}{presenter_lock}\n\n{VIDEO_STYLE_CORE}\n\n{VIDEO_MOTION}\n\n"
        f"{PHYSICAL_INTEGRITY_LOCK}\n{scene_specific_constraints(scene, 'video')}{correction}\n"
        "All instructions describe the shot; do not display them as text."
    )
