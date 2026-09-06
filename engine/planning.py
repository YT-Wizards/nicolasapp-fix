import json
import math
import re
from collections import Counter

from prompts import (
    NEGATIVE_CORE,
    PLANNER_SYSTEM,
    SCENE_BATCH_PROMPT,
    SCENE_PLAN_REVIEW_PROMPT,
    STORY_BIBLE_PROMPT,
    STYLE_CORE,
    VIDEO_MOTION,
)


VISUAL_STRATEGIES = {
    # Measured from FaceTuber's practical/animal format: roughly 14% host,
    # 8% exact 50/50 split and a near-even mix of motion and animated stills.
    "hybrid": {"video": 0.40, "image": 0.38, "avatar": 0.14, "split": 0.08, "still": 0.0},
    # Process/incident explainers benefit from more literal moving footage.
    "motion_led": {"video": 0.68, "image": 0.09, "avatar": 0.13, "split": 0.10, "still": 0.0},
    # Archival/object-led explanations are clearer and more reliable as stills.
    "image_led": {"video": 0.30, "image": 0.48, "avatar": 0.14, "split": 0.08, "still": 0.0},
}
TARGET_RATIOS = VISUAL_STRATEGIES["hybrid"]
MEDIA_COST = {"video": 0.020, "image": 0.014, "split": 0.014, "still": 0.014, "avatar": 0.0}
# Planning with provider list prices is useful for the first rough schedule, but
# the last production showed why it cannot be the final guard: completed clips
# also need visual QA and a portion of Veo jobs require a literal image fallback.
# These deliberately conservative figures are used only after the paid analysis
# has finished, when VYT knows the real balance left for media.
EXPECTED_ASSET_COST = {"video": 0.032, "image": 0.020, "split": 0.020, "still": 0.020, "avatar": 0.0}
# Keep the FaceTuber cadence where it matters without overwhelming providers or
# the per-video budget: cap separately planned shots and Veo generations.
# Excess motion beats become animated documentary images, never a presenter tail.
MAX_SCENES_PER_JOB = 260
MAX_VEO_SCENES_PER_JOB = 72


def analysis_reserve_for_duration(duration):
    """Reserve room for the planning and review calls that still remain.

    Short reference videos retain the measured FaceTuber cadence.  The reserve
    also covers the observed cost of plan review and a modest media-fallback
    margin, so the last part is not unexpectedly replaced by the presenter.
    """
    minutes = max(0.0, float(duration)) / 60.0
    return round(min(1.80, 1.10 + 0.03 * minutes), 4)


def visual_ratios_for_bible(bible):
    strategy = str((bible or {}).get("visual_strategy") or "hybrid").strip().lower()
    ratios = dict(VISUAL_STRATEGIES.get(strategy, VISUAL_STRATEGIES["hybrid"]))
    pattern = str((bible or {}).get("presenter_pattern") or "balanced").strip().lower()
    host_share = ratios["avatar"] + ratios["split"]
    if pattern == "alternating":
        ratios["avatar"] = round(host_share * 0.53, 4)
        ratios["split"] = round(host_share - ratios["avatar"], 4)
    elif pattern == "full_led":
        ratios["split"] = min(0.01, host_share)
        ratios["avatar"] = round(host_share - ratios["split"], 4)
    return ratios


def narration_for_interval(transcript, start, end):
    pieces = []
    for segment in transcript:
        segment_start = float(segment["start"])
        segment_end = float(segment["end"])
        if segment_end <= start or segment_start >= end:
            continue
        words = segment.get("text", "").strip().split()
        if not words:
            continue
        span = max(0.001, segment_end - segment_start)
        first = max(0, min(len(words) - 1, math.floor((max(start, segment_start) - segment_start) / span * len(words))))
        last = max(first + 1, min(len(words), math.ceil((min(end, segment_end) - segment_start) / span * len(words))))
        pieces.append(" ".join(words[first:last]))
    return " ".join(piece for piece in pieces if piece).strip()


def make_beats(duration, transcript, max_beats=None, duration_types=None):
    beats = []
    cursor = 0.0
    index = 0
    # FaceTuber references cut at a measured median of 3.37–3.40 s.  Use real
    # Whisper phrase endings whenever they fall inside a readable 2.2–5.2 s
    # window; the cadence sequence is only a fallback for long transcription
    # segments, never the primary clock.
    sequence = [3.2, 3.7, 3.4, 4.1, 3.0, 3.8, 3.5]
    phrase_ends = sorted({
        round(min(float(duration), max(0.0, float(item.get("end") or 0))), 3)
        for item in transcript or []
        if 0.05 < float(item.get("end") or 0) <= float(duration) + 0.05
    })
    while cursor < duration - 0.05:
        target = min(duration, cursor + sequence[index % len(sequence)])
        candidates = [
            boundary for boundary in phrase_ends
            if cursor + 2.2 <= boundary <= min(duration, cursor + 5.2)
        ]
        end = min(candidates, key=lambda boundary: abs(boundary - target)) if candidates else target
        if duration - end < 2.2:
            end = duration
        beats.append({
            "id": f"b{index + 1:04d}",
            "start": round(cursor, 3),
            "end": round(end, 3),
            "duration": round(end - cursor, 3),
            "narration": "",
        })
        cursor = end
        index += 1
    if max_beats is not None and len(beats) > max(1, int(max_beats)):
        count = min(len(beats), max(1, int(max_beats)))
        typed_timing = list(duration_types or [])
        if len(typed_timing) != count:
            typed_timing = ["image"] * count
        # A final video could inherit all residual duration.  Move it into an
        # earlier non-video slot so the closing image/avatar safely absorbs any
        # phrase-boundary adjustment without ever looping an eight-second Veo.
        if typed_timing[-1] == "video":
            swap_index = next(
                (index for index in range(count - 2, 0, -1) if typed_timing[index] != "video"),
                0,
            )
            typed_timing[-1], typed_timing[swap_index] = typed_timing[swap_index], typed_timing[-1]
            if duration_types is not None:
                duration_types[:] = typed_timing

        average = float(duration) / count
        video_target = min(8.0, average)
        video_count = typed_timing.count("video")
        other_count = count - video_count
        other_target = (
            (float(duration) - video_count * video_target) / other_count
            if other_count else average
        )
        targets = [video_target if media_type == "video" else other_target for media_type in typed_timing]
        cumulative_targets = []
        cumulative = 0.0
        for target in targets:
            cumulative += target
            cumulative_targets.append(cumulative)
        beats = []
        cursor = 0.0
        for index in range(count):
            if index == count - 1:
                end = float(duration)
            else:
                ideal = cumulative_targets[index]
                remaining = count - index - 1
                target = targets[index]
                minimum_gap = min(2.2, max(0.35, target * 0.65))
                minimum_future = remaining * 0.35
                radius = max(0.8, target * 0.30)
                lower = max(cursor + minimum_gap, ideal - radius)
                upper = min(float(duration) - minimum_future, ideal + radius)
                if typed_timing[index] == "video":
                    upper = min(upper, cursor + 8.0)
                if lower > upper:
                    lower = max(cursor + 0.05, upper)
                candidates = [boundary for boundary in phrase_ends if lower <= boundary <= upper]
                end = min(candidates, key=lambda boundary: abs(boundary - ideal)) if candidates else min(max(ideal, lower), upper)
            beats.append({
                "id": f"b{index + 1:04d}",
                "start": round(cursor, 3),
                "end": round(end, 3),
                "duration": round(end - cursor, 3),
                "narration": "",
            })
            cursor = end
    # Whisper frequently returns sentences longer than one visual beat. Assign each
    # word to exactly one beat so adjacent prompts never receive a duplicated sentence.
    narration_parts = [[] for _ in beats]
    beat_index = 0
    for segment in transcript:
        words = segment.get("text", "").strip().split()
        if not words:
            continue
        segment_start = max(0.0, float(segment["start"]))
        segment_end = min(duration, float(segment["end"]))
        span = max(0.001, segment_end - segment_start)
        for word_index, word in enumerate(words):
            timestamp = segment_start + (word_index + 0.5) / len(words) * span
            while beat_index < len(beats) - 1 and timestamp >= beats[beat_index]["end"]:
                beat_index += 1
            while beat_index > 0 and timestamp < beats[beat_index]["start"]:
                beat_index -= 1
            narration_parts[beat_index].append(word)
    for beat, words in zip(beats, narration_parts):
        beat["narration"] = " ".join(words).strip()
    return beats


def weighted_types(count, ratios=None):
    ratios = ratios or TARGET_RATIOS
    total_ratio = sum(ratios.values()) or 1.0
    normalized = {name: ratio / total_ratio for name, ratio in ratios.items()}
    desired = {name: ratio * count for name, ratio in normalized.items()}
    assigned = Counter()
    result = []
    names = list(ratios)
    for index in range(count):
        candidates = sorted(names, key=lambda name: (desired[name] * (index + 1) / count - assigned[name], normalized[name]), reverse=True)
        choice = candidates[0]
        result.append(choice)
        assigned[choice] += 1
    return result


def editorial_types(count, ratios=None, presenter_pattern="balanced"):
    """Build the measured FULL/SPLIT presenter pattern and four-shot B-roll runs."""
    if count <= 0:
        return []
    counts = Counter(weighted_types(count, ratios or TARGET_RATIOS))
    if counts["avatar"] == 0:
        donor = max((name for name in counts if name != "avatar"), key=counts.get)
        counts[donor] -= 1
        counts["avatar"] = 1

    host_counts = {"avatar": counts["avatar"], "split": counts["split"]}
    hosts = ["avatar"]
    host_counts["avatar"] -= 1
    remaining_hosts = host_counts["avatar"] + host_counts["split"]
    if presenter_pattern == "full_led":
        # The rare split is a later visual variation, not part of the hook.
        late_start = math.floor(remaining_hosts * 0.40)
        split_slots = _distributed_subset(range(late_start, remaining_hosts), host_counts["split"])
        hosts.extend("split" if index in split_slots else "avatar" for index in range(remaining_hosts))
    else:
        next_host = "split" if host_counts["split"] else "avatar"
        while host_counts["avatar"] or host_counts["split"]:
            chosen = next_host if host_counts[next_host] else ("avatar" if host_counts["avatar"] else "split")
            hosts.append(chosen)
            host_counts[chosen] -= 1
            other = "avatar" if chosen == "split" else "split"
            next_host = other if host_counts[other] else chosen

    broll_counts = {name: counts[name] for name in ("video", "image", "still")}
    broll = weighted_types(sum(broll_counts.values()), broll_counts) if sum(broll_counts.values()) else []
    # Own the host positions deterministically.  The former fixed 4-shot runs
    # exhausted B-roll first and appended 20–50 seconds of presenter at the end.
    host_positions = {0}
    host_positions.update(_distributed_subset(range(1, count), len(hosts) - 1))
    sequence = []
    host_cursor = 0
    broll_cursor = 0
    for index in range(count):
        if index in host_positions and host_cursor < len(hosts):
            sequence.append(hosts[host_cursor])
            host_cursor += 1
        elif broll_cursor < len(broll):
            sequence.append(broll[broll_cursor])
            broll_cursor += 1
        else:
            sequence.append(hosts[host_cursor])
            host_cursor += 1
    return sequence


def enforce_budget(types, max_cost, reserve=0.10):
    cap = max(0.0, max_cost - reserve)
    estimate = sum(MEDIA_COST[item] for item in types)
    if estimate <= cap:
        return types, estimate
    adjusted = list(types)

    def distributed_subset(indexes, count):
        if count <= 0 or not indexes:
            return set()
        count = min(len(indexes), int(count))
        return {
            indexes[min(len(indexes) - 1, math.floor((position + 0.5) * len(indexes) / count))]
            for position in range(count)
        }

    # A still image is cheaper and safer than increasing presenter coverage.
    # Downgrade moving scenes evenly across the programme before removing any
    # visual support altogether.
    saving_per_video = MEDIA_COST["video"] - MEDIA_COST["image"]
    video_indexes = [index for index, item in enumerate(adjusted) if item == "video"]
    needed_downgrades = max(0, math.ceil((estimate - cap - 1e-9) / saving_per_video))
    if needed_downgrades <= len(video_indexes) and estimate - needed_downgrades * saving_per_video <= cap + 1e-9:
        downgrade_count = needed_downgrades
    else:
        # On a severely constrained long film retain a meaningful moving-footage
        # bank; otherwise converting every Veo request would recreate the old
        # all-images failure mode.
        downgrade_count = min(needed_downgrades, math.ceil(len(video_indexes) * 0.45))
    for index in distributed_subset(video_indexes, downgrade_count):
        adjusted[index] = "image"
    estimate = sum(MEDIA_COST[item] for item in adjusted)
    if estimate <= cap + 1e-9:
        return adjusted, max(0.0, estimate)

    paid_by_type = {
        name: [index for index, item in enumerate(adjusted) if item == name]
        for name in ("video", "image", "split", "still")
    }
    scale = cap / max(0.0001, estimate)
    desired = {name: len(indexes) * scale for name, indexes in paid_by_type.items()}
    keep_counts = {name: math.floor(value) for name, value in desired.items()}
    used = sum(keep_counts[name] * MEDIA_COST[name] for name in keep_counts)
    while True:
        options = sorted(
            (
                (desired[name] - keep_counts[name], name)
                for name, indexes in paid_by_type.items()
                if keep_counts[name] < len(indexes) and used + MEDIA_COST[name] <= cap + 1e-9
            ),
            reverse=True,
        )
        if not options:
            break
        name = options[0][1]
        keep_counts[name] += 1
        used += MEDIA_COST[name]
    keep_indexes = set()
    for name, indexes in paid_by_type.items():
        keep_indexes.update(distributed_subset(indexes, keep_counts[name]))
    for index, item in enumerate(adjusted):
        if item != "avatar" and index not in keep_indexes:
            adjusted[index] = "avatar"
    estimate = sum(MEDIA_COST[item] for item in adjusted)
    return adjusted, max(0.0, estimate)


def build_schedule(duration, transcript, max_cost, bible=None):
    beats = make_beats(duration, transcript)
    presenter_pattern = str((bible or {}).get("presenter_pattern") or "balanced").strip().lower()
    ratios = visual_ratios_for_bible(bible)
    reserve = min(max(0.0, float(max_cost)), analysis_reserve_for_duration(duration))
    media_cap = max(0.0, float(max_cost) - reserve)
    target_count = min(len(beats), MAX_SCENES_PER_JOB)
    while True:
        candidate_types = editorial_types(target_count, ratios, presenter_pattern=presenter_pattern)
        video_indexes = [index for index, item in enumerate(candidate_types) if item == "video"]
        keep_videos = _distributed_subset(video_indexes, min(len(video_indexes), MAX_VEO_SCENES_PER_JOB))
        candidate_types = [
            "image" if item == "video" and index not in keep_videos else item
            for index, item in enumerate(candidate_types)
        ]
        if sum(MEDIA_COST[item] for item in candidate_types) <= media_cap + 1e-9 or target_count <= 1:
            break
        target_count -= 1
    types = candidate_types
    if target_count < len(beats):
        beats = make_beats(duration, transcript, max_beats=target_count, duration_types=types)
    # Silence and breaths should not trigger a paid, semantically empty asset.
    for index, beat in enumerate(beats):
        if beat["narration"] or types[index] == "avatar":
            continue
        swap_index = next((candidate for candidate in range(max(2, index + 1), len(beats)) if types[candidate] == "avatar" and beats[candidate]["narration"]), None)
        if swap_index is not None:
            types[index], types[swap_index] = types[swap_index], types[index]
        else:
            types[index] = "avatar"
    types, estimate = enforce_budget(types, max_cost, reserve=reserve)
    for beat, media_type in zip(beats, types):
        beat["type"] = media_type
    return beats, estimate


def force_avatar_window(items, window):
    """Keep the HeyGen presenter visible for every beat touched by a sales QR."""
    if not window:
        return items
    windows = [window] if isinstance(window, dict) else window
    for active_window in windows:
        window_start = float(active_window["start"])
        window_end = float(active_window["end"])
        for item in items:
            if min(float(item["end"]), window_end) - max(float(item["start"]), window_start) <= 0.02:
                continue
            item["type"] = "avatar"
            item["requested_type"] = "avatar"
            item["literal_subject"] = "HeyGen source presenter"
            item["image_prompt"] = ""
            item["video_prompt"] = ""
            item["continuity_ids"] = []
            item["named_brand"] = ""
            item["presenter_broll"] = False
            item["presenter_broll_reason"] = ""
            item["presenter_broll_value"] = 0
            item["presenter_identity"] = ""
            item["reject_if"] = []
    return items


def compact_transcript(transcript):
    return "\n".join(f"[{item['start']:.1f}-{item['end']:.1f}] {item['text'].strip()}" for item in transcript)


def build_story_bible(client, transcript, branding, presenter_images=None):
    prompt = STORY_BIBLE_PROMPT.format(branding="yes" if branding else "no", transcript=compact_transcript(transcript))
    result = client.chat_json(PLANNER_SYSTEM, prompt, images=list(presenter_images or []), max_tokens=3400)
    if not isinstance(result, dict):
        raise ValueError("La biblia visual no tiene el formato correcto.")
    if str(result.get("visual_strategy") or "").lower() not in VISUAL_STRATEGIES:
        result["visual_strategy"] = "hybrid"
    if str(result.get("presenter_reuse_strategy") or "").lower() not in {"selective", "none"}:
        result["presenter_reuse_strategy"] = "none"
    if str(result.get("presenter_pattern") or "").lower() not in {"balanced", "alternating", "full_led"}:
        result["presenter_pattern"] = "balanced"
    profile = result.get("presenter_profile")
    if not isinstance(profile, dict):
        profile = {}
    try:
        profile["reference_index"] = min(3, max(1, int(profile.get("reference_index") or 1)))
    except (TypeError, ValueError):
        profile["reference_index"] = 1
    profile["stable_identity"] = str(profile.get("stable_identity") or "the same presenter shown in the attached reference").strip()
    profile["wardrobe_anchor"] = str(profile.get("wardrobe_anchor") or "").strip()
    profile["source_set"] = str(profile.get("source_set") or "").strip()
    result["presenter_profile"] = profile
    return result


def _default_scene(beat):
    narration = beat.get("narration") or "the concrete factual action described by the narration"
    return {
        "id": beat["id"],
        "literal_subject": narration[:240],
        "image_prompt": f"A literal candid documentary view of {narration[:500]}",
        "video_prompt": f"A single continuous candid documentary shot depicting {narration[:500]}",
        "continuity_ids": [],
        "named_brand": "",
        "presenter_broll": False,
        "presenter_broll_reason": "",
        "presenter_broll_value": 0,
        "reject_if": ["unrelated subject", "visible text", "cinematic lighting"],
    }


def _normalize_scene(scene):
    """Keep provider output safe and usable even when a field is null or mistyped."""
    narration = str(scene.get("narration") or "the exact factual narration beat").strip()
    media_type = str(scene.get("type") or "avatar")
    scene["literal_subject"] = str(scene.get("literal_subject") or narration[:240]).strip()
    scene["named_brand"] = str(scene.get("named_brand") or "").strip()
    raw_presenter = scene.get("presenter_broll", False)
    scene["presenter_broll"] = raw_presenter is True or str(raw_presenter).strip().lower() == "true"
    scene["presenter_broll_reason"] = str(scene.get("presenter_broll_reason") or "").strip()
    try:
        scene["presenter_broll_value"] = min(5, max(0, int(scene.get("presenter_broll_value") or 0)))
    except (TypeError, ValueError):
        scene["presenter_broll_value"] = 0
    for key in ("continuity_ids", "reject_if"):
        value = scene.get(key)
        scene[key] = [str(item).strip() for item in value if str(item).strip()] if isinstance(value, list) else []
    if media_type == "avatar":
        scene["literal_subject"] = "HeyGen source presenter"
        scene["image_prompt"] = ""
        scene["video_prompt"] = ""
        scene["presenter_broll"] = False
        scene["presenter_broll_reason"] = ""
        scene["presenter_broll_value"] = 0
    elif media_type == "video":
        scene["image_prompt"] = ""
        scene["video_prompt"] = str(
            scene.get("video_prompt") or f"A single continuous candid documentary shot depicting {narration[:500]}"
        ).strip()
    else:
        scene["image_prompt"] = str(
            scene.get("image_prompt") or f"A literal candid documentary view of {narration[:500]}"
        ).strip()
        scene["video_prompt"] = ""
        scene["presenter_broll"] = False
        scene["presenter_broll_reason"] = ""
        scene["presenter_broll_value"] = 0
    return scene


def _scene_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def scene_fingerprint(scene):
    """Return the stable semantic identity used by the pre-generation gate."""
    prompt = scene.get("video_prompt") or scene.get("image_prompt") or scene.get("literal_subject")
    return (
        _scene_text(scene.get("literal_subject")),
        _scene_text(prompt),
        tuple(sorted(_scene_text(item) for item in scene.get("continuity_ids", []) if item)),
    )


def validate_scene_plan(scenes, duration):
    """Validate timeline ranges and neutralize confirmed duplicate paid beats.

    This is intentionally deterministic and conservative. Similar but distinct
    scenes are only annotated; a scene is converted to the free presenter view
    when its narration overlaps or its complete semantic fingerprint repeats
    immediately, preventing a second paid asset for the same beat.
    """
    warnings = []
    previous_end = 0.0
    seen_narration = {}
    seen_fingerprints = {}
    for index, scene in enumerate(scenes or []):
        try:
            start = float(scene.get("start", 0))
            end = float(scene.get("end", start))
        except (TypeError, ValueError):
            raise ValueError(f"La escena {scene.get('id', index)} tiene un rango temporal inválido.")
        if start < -0.01 or end <= start or end > float(duration) + 0.25:
            raise ValueError(f"La escena {scene.get('id', index)} está fuera de la duración del vídeo.")
        if start < previous_end - 0.25:
            raise ValueError(f"La escena {scene.get('id', index)} se solapa incorrectamente con la escena anterior.")
        previous_end = end
        narration = _scene_text(scene.get("narration"))
        fingerprint = scene_fingerprint(scene)
        duplicate_of = None
        if narration and narration in seen_narration:
            prior_index, prior_end = seen_narration[narration]
            overlap = min(end, prior_end) - max(start, float(scenes[prior_index].get("start", 0)))
            if overlap > 0 or abs(index - prior_index) <= 1:
                duplicate_of = scenes[prior_index].get("id")
                warnings.append({"scene_id": scene.get("id"), "kind": "repeated_narration", "duplicate_of": duplicate_of})
        if fingerprint in seen_fingerprints and duplicate_of is None:
            prior_index = seen_fingerprints[fingerprint]
            if abs(index - prior_index) <= 2:
                duplicate_of = scenes[prior_index].get("id")
                warnings.append({"scene_id": scene.get("id"), "kind": "repeated_scene_fingerprint", "duplicate_of": duplicate_of})
        if duplicate_of and scene.get("type") != "avatar":
            scene["duplicate_of"] = duplicate_of
            scene["duplicate_strategy"] = "presenter_fallback"
            scene["requested_type"] = scene.get("requested_type", scene.get("type"))
            scene["type"] = "avatar"
            scene = _normalize_scene(scene)
        if narration:
            seen_narration[narration] = (index, end)
        if fingerprint != ("", "", ()):
            seen_fingerprints[fingerprint] = index
    return warnings


def enforce_presenter_broll(scenes, bible, max_share=0.06):
    """Keep character reuse selective, reference-backed and non-consecutive.

    The director proposes semantically useful moments.  This deterministic gate
    owns the quota so an enthusiastic model cannot turn the whole documentary
    into synthetic footage of the host.
    """
    strategy = str((bible or {}).get("presenter_reuse_strategy") or "none").strip().lower()
    profile = (bible or {}).get("presenter_profile")
    if not isinstance(profile, dict):
        profile = {}
    identity = str(profile.get("stable_identity") or "the same presenter shown in the reference image").strip()
    quota = max(0, math.ceil(len(scenes) * float(max_share))) if strategy == "selective" else 0
    candidates = []
    for index, scene in enumerate(scenes):
        requested = bool(scene.get("presenter_broll"))
        eligible = (
            requested and scene.get("type") == "video"
            and bool(str(scene.get("presenter_broll_reason") or "").strip())
            and int(scene.get("presenter_broll_value") or 0) >= 3
            and index >= 2
        )
        if eligible:
            candidates.append(index)
        scene["presenter_broll"] = False
        scene["presenter_identity"] = ""

    selected = []
    # Spread the rare identity moments through the whole story.  Taking simply
    # the first N proposals creates a synthetic-looking cluster in the hook.
    for bucket in range(min(quota, len(candidates))):
        lower = len(scenes) * bucket / max(1, quota)
        upper = len(scenes) * (bucket + 1) / max(1, quota)
        middle = (lower + upper) / 2
        pool = [
            index for index in candidates if lower <= index < upper
            and all(abs(index - chosen) > 1 for chosen in selected)
        ]
        if not pool:
            continue
        chosen = max(pool, key=lambda index: (int(scenes[index].get("presenter_broll_value") or 0), -abs(index - middle)))
        selected.append(chosen)
    if len(selected) < quota:
        remaining = sorted(
            (index for index in candidates if index not in selected),
            key=lambda index: (-int(scenes[index].get("presenter_broll_value") or 0), index),
        )
        for index in remaining:
            if len(selected) >= quota:
                break
            if all(abs(index - chosen) > 1 for chosen in selected):
                selected.append(index)
    for index in sorted(selected):
        scenes[index]["presenter_broll"] = True
        scenes[index]["presenter_identity"] = identity
    for scene in scenes:
        if not scene.get("presenter_broll") and strategy != "selective":
            scene["presenter_broll_reason"] = ""
            scene["presenter_broll_value"] = 0
    return scenes


def image_fallback_scene(scene):
    """Turn a failed motion shot into the same literal beat, never into presenter filler."""
    narration = str(scene.get("narration") or "the exact factual narration beat").strip()
    subject = str(scene.get("literal_subject") or narration[:240]).strip()
    fallback = dict(scene)
    fallback["type"] = "image"
    fallback["image_prompt"] = (
        f"A candid paused documentary frame showing {subject}. "
        f"It must directly illustrate this exact spoken idea: {narration[:420]}. "
        "One principal subject, one place, one clear factual detail, no staged action."
    )
    fallback["video_prompt"] = ""
    fallback["fallback_from"] = scene.get("requested_type", scene.get("type", "video"))
    return _normalize_scene(fallback)


def _distributed_subset(indexes, count):
    """Select *count* positions evenly, including the whole programme."""
    indexes = list(indexes or [])
    count = min(len(indexes), max(0, int(count or 0)))
    if not indexes or count <= 0:
        return set()
    if count >= len(indexes):
        return set(indexes)
    return {
        indexes[min(len(indexes) - 1, math.floor((position + 0.5) * len(indexes) / count))]
        for position in range(count)
    }


def _fill_distribution(indexes, count, anchors=None, span=None):
    """Fill the largest timeline gaps around existing anchors."""
    candidates = set(indexes or [])
    selected = set(anchors or [])
    count = min(len(candidates), max(0, int(count or 0)))
    extent = max(1, int(span or ((max(candidates | selected) + 1) if (candidates or selected) else 1)))
    chosen = set()
    for _ in range(count):
        boundaries = {-1, extent}
        references = selected | chosen | boundaries
        best = max(
            candidates - chosen,
            key=lambda index: (min(abs(index - reference) for reference in references), -index),
        )
        chosen.add(best)
    return chosen


def expected_media_cost(scenes, already_paid_ids=None, costs=None):
    """Conservative generation+QA estimate for assets which are not paid yet."""
    paid = set(already_paid_ids or [])
    prices = dict(EXPECTED_ASSET_COST if costs is None else costs)
    return sum(
        0.0 if scene.get("id") in paid else float(prices.get(scene.get("type"), 0.0))
        for scene in scenes or []
    )


def _budget_avatar_scene(scene):
    fallback = dict(scene)
    fallback["budget_fallback_from"] = scene.get("type", "")
    fallback["type"] = "avatar"
    return _normalize_scene(fallback)


def rebalance_scenes_for_budget(scenes, available_usd, buffer=0.12, already_paid_ids=None):
    """Fit the reviewed plan to the *real* remaining balance without a dead tail.

    First turn only the necessary Veo shots into literal images, keeping the
    surviving motion shots evenly spread from hook to ending.  If the balance is
    exceptionally small, paid visuals are also retained at even intervals.  A
    previous build simply bought from the start onward and made every remaining
    scene avatar once the cap was reached; this deterministic pass prevents that.
    """
    result = [dict(scene) for scene in scenes or []]
    protected = set(already_paid_ids or [])
    cap = max(0.0, float(available_usd or 0.0) - max(0.0, float(buffer or 0.0)))
    estimate = expected_media_cost(result, protected)
    if estimate <= cap + 1e-9:
        return result, round(estimate, 4)

    unpaid_visuals = [
        index for index, scene in enumerate(result)
        if scene.get("id") not in protected and scene.get("type") != "avatar"
    ]
    video_indexes = [index for index in unpaid_visuals if result[index].get("type") == "video"]
    protected_visuals = [
        index for index, scene in enumerate(result)
        if scene.get("id") in protected and scene.get("type") != "avatar"
    ]
    target_video_floor = max(1, round(len(result) * 0.24)) if video_indexes else 0
    affordable_video_floor = math.floor(cap * 0.55 / EXPECTED_ASSET_COST["video"] + 1e-9)
    video_keep_count = min(len(video_indexes), target_video_floor, affordable_video_floor)

    # First maximize coverage at image price, then spend remaining room on Veo.
    # If the preferred motion floor does not fit alongside every visual, retain
    # fewer total supports rather than erasing motion entirely.
    all_image_cost = len(unpaid_visuals) * EXPECTED_ASSET_COST["image"]
    if all_image_cost <= cap + 1e-9:
        extra_video_room = math.floor(
            (cap - all_image_cost) /
            (EXPECTED_ASSET_COST["video"] - EXPECTED_ASSET_COST["image"])
            + 1e-9
        )
        video_keep_count = min(len(video_indexes), max(video_keep_count, extra_video_room))
    remaining_after_video = max(0.0, cap - video_keep_count * EXPECTED_ASSET_COST["video"])
    other_keep_count = min(
        len(unpaid_visuals) - video_keep_count,
        math.floor(remaining_after_video / EXPECTED_ASSET_COST["image"] + 1e-9),
    )

    total_keep = video_keep_count + other_keep_count
    if protected_visuals:
        keep_all = _fill_distribution(
            unpaid_visuals, total_keep, anchors=set(protected_visuals), span=len(result),
        )
    else:
        keep_all = _distributed_subset(unpaid_visuals, total_keep)
    video_candidates_in_grid = [index for index in video_indexes if index in keep_all]
    keep_videos = _distributed_subset(
        video_candidates_in_grid,
        min(video_keep_count, len(video_candidates_in_grid)),
    )
    missing_videos = video_keep_count - len(keep_videos)
    if missing_videos > 0:
        additions = _distributed_subset(
            [index for index in video_indexes if index not in keep_all], missing_videos,
        )
        for addition in additions:
            replaceable = [index for index in keep_all if index not in keep_videos]
            if not replaceable:
                break
            replacement = min(replaceable, key=lambda index: (abs(index - addition), index))
            keep_all.remove(replacement)
            keep_all.add(addition)
            keep_videos.add(addition)
    keep_other = keep_all - keep_videos
    for index in unpaid_visuals:
        if index in keep_videos:
            continue
        if index in keep_other:
            if result[index].get("type") == "video":
                result[index] = image_fallback_scene(result[index])
            continue
        result[index] = _budget_avatar_scene(result[index])
    estimate = expected_media_cost(result, protected)
    return result, round(min(cap, estimate), 4)


def stratified_generation_order(scenes, buckets=10):
    """Round-robin time buckets so provider drift cannot consume only the start."""
    items = list(scenes or [])
    if len(items) < 2:
        return items
    bucket_count = min(len(items), max(1, int(buckets or 1)))
    end = max(float(scene.get("end") or scene.get("start") or 0.0) for scene in items)
    if end <= 0:
        end = float(len(items))
    groups = [[] for _ in range(bucket_count)]
    for position, scene in enumerate(items):
        midpoint = (
            (float(scene.get("start") or 0.0) + float(scene.get("end") or scene.get("start") or 0.0)) / 2
            if scene.get("start") is not None else (position + 0.5) / len(items) * end
        )
        bucket = min(bucket_count - 1, max(0, int(midpoint / max(0.001, end) * bucket_count)))
        groups[bucket].append(scene)
    def midpoint_order(group):
        ordered_group = []
        intervals = [(0, len(group))]
        while intervals:
            following = []
            for lower, upper in intervals:
                if lower >= upper:
                    continue
                middle = (lower + upper - 1) // 2
                ordered_group.append(group[middle])
                following.extend(((lower, middle), (middle + 1, upper)))
            intervals = following
        return ordered_group

    groups = [midpoint_order(group) for group in groups]
    ordered = []
    depth = 0
    while any(depth < len(group) for group in groups):
        for group in groups:
            if depth < len(group):
                ordered.append(group[depth])
        depth += 1
    return ordered


def _is_partial_json_error(error):
    message = str(error).lower()
    return any(token in message for token in (
        "json incompleto", "no devolvió json válido", "respuesta vacía",
        "no devolvió todos los planos",
    ))


def plan_scenes(client, beats, bible, batch_size=6, progress=None, resume_planned=None, checkpoint=None):
    planned = [_normalize_scene(scene) for scene in list(resume_planned or [])]
    expected_prefix = [beat["id"] for beat in beats[:len(planned)]]
    if [scene.get("id") for scene in planned] != expected_prefix:
        planned = []
    total_batches = math.ceil(len(beats) / batch_size)
    for start in range(len(planned), len(beats), batch_size):
        batch_number = start // batch_size + 1
        batch = beats[start:start + batch_size]
        beat_payload = [
            {"id": item["id"], "type": item["type"], "start": item["start"], "end": item["end"], "narration": item["narration"]}
            for item in batch
            if item["type"] != "avatar"
        ]
        if beat_payload:
            context_payload = [
                {"id": item["id"], "start": item["start"], "end": item["end"], "narration": item["narration"]}
                for item in beats[max(0, start - 2):start] + beats[start + batch_size:start + batch_size + 2]
            ]

            def request_plans(items):
                payload = [
                    {"id": item["id"], "type": item["type"], "start": item["start"], "end": item["end"], "narration": item["narration"]}
                    for item in items if item["type"] != "avatar"
                ]
                if not payload:
                    return {}
                prompt_text = SCENE_BATCH_PROMPT.format(
                    style=STYLE_CORE,
                    negative=NEGATIVE_CORE,
                    motion=VIDEO_MOTION,
                    bible=json.dumps(bible, ensure_ascii=False),
                    context=json.dumps(context_payload, ensure_ascii=False),
                    beats=json.dumps(payload, ensure_ascii=False),
                )
                try:
                    result = client.chat_json(PLANNER_SYSTEM, prompt_text, max_tokens=3500)
                    returned = result.get("scenes", []) if isinstance(result, dict) else []
                    mapped = {str(scene.get("id")): scene for scene in returned if isinstance(scene, dict)}
                    if any(item["id"] not in mapped for item in payload):
                        raise ValueError("El analizador no devolvió todos los planos.")
                    return mapped
                except Exception as error:
                    if not _is_partial_json_error(error):
                        raise
                    # A cut response is never repurchased as the same large request.
                    # Split it until it succeeds; for a single stubborn beat, the
                    # literal fallback remains safe and will still receive review.
                    if len(items) <= 1:
                        return {}
                    middle = max(1, len(items) // 2)
                    return {**request_plans(items[:middle]), **request_plans(items[middle:])}

            by_id = request_plans(batch)
        else:
            by_id = {}
        for beat in batch:
            scene = by_id.get(beat["id"], _default_scene(beat))
            scene.update({key: beat[key] for key in ["id", "start", "end", "duration", "narration", "type"]})
            scene["requested_type"] = beat["type"]
            planned.append(_normalize_scene(scene))
        if checkpoint:
            checkpoint(planned)
        if progress:
            progress(batch_number, total_batches)
    return planned


def review_scene_plan(client, scenes, bible, batch_size=12, progress=None, resume_reviewed=None, checkpoint=None):
    """Correct semantic or continuity mistakes before spending on media generation."""
    reviewed = [_normalize_scene(scene) for scene in list(resume_reviewed or [])]
    expected_prefix = [scene["id"] for scene in scenes[:len(reviewed)]]
    if [scene.get("id") for scene in reviewed] != expected_prefix:
        reviewed = []
    batches = list(range(len(reviewed), len(scenes), batch_size))
    total_batches = math.ceil(len(scenes) / batch_size)
    for start in batches:
        batch_number = start // batch_size + 1
        batch = scenes[start:start + batch_size]
        payload = [
            {key: scene.get(key) for key in ("id", "type", "narration", "literal_subject", "image_prompt", "video_prompt", "continuity_ids", "named_brand", "presenter_broll", "presenter_broll_reason", "presenter_broll_value", "reject_if")}
            for scene in batch if scene["type"] != "avatar"
        ]
        if payload:
            recent_payload = [
                {key: item.get(key) for key in ("id", "narration", "literal_subject", "image_prompt", "video_prompt")}
                for item in reviewed[-8:]
            ]

            def request_review(items):
                prompt = SCENE_PLAN_REVIEW_PROMPT.format(
                    bible=json.dumps(bible, ensure_ascii=False),
                    recent=json.dumps(recent_payload, ensure_ascii=False),
                    scenes=json.dumps(items, ensure_ascii=False),
                )
                try:
                    result = client.chat_json(PLANNER_SYSTEM, prompt, max_tokens=3000)
                    returned = result.get("scenes", []) if isinstance(result, dict) else []
                    mapped = {str(item.get("id")): item for item in returned if isinstance(item, dict)}
                    if any(item["id"] not in mapped for item in items):
                        raise ValueError("La revisión interna no devolvió todos los planos.")
                    return mapped
                except Exception as error:
                    if not _is_partial_json_error(error):
                        raise
                    if len(items) <= 1:
                        return {}
                    middle = max(1, len(items) // 2)
                    return {**request_review(items[:middle]), **request_review(items[middle:])}

            by_id = request_review(payload)
        else:
            by_id = {}
        for scene in batch:
            correction = by_id.get(scene["id"])
            if correction:
                for key in ("literal_subject", "image_prompt", "video_prompt", "continuity_ids", "named_brand", "presenter_broll", "presenter_broll_reason", "presenter_broll_value", "reject_if"):
                    if key in correction:
                        scene[key] = correction[key]
            reviewed.append(_normalize_scene(scene))
        if checkpoint:
            checkpoint(reviewed)
        if progress:
            progress(batch_number, total_batches)
    return reviewed
