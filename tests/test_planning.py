import sys
import unittest
import json
from collections import Counter
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from planning import (
    analysis_reserve_for_duration, build_schedule, editorial_types, enforce_budget, enforce_presenter_broll,
    enforce_narration_visual_contract,
    force_avatar_window, image_fallback_scene, make_beats,
    plan_scenes, rebalance_scenes_for_budget, review_scene_plan,
    stratified_generation_order, validate_scene_plan, visual_ratios_for_bible,
)
from prompts import (
    IMAGE_REVIEW_PROMPT, SCENE_BATCH_PROMPT, SCENE_PLAN_REVIEW_PROMPT,
    VIDEO_MOTION, VIDEO_REVIEW_PROMPT, image_prompt, video_prompt,
)
from providers import ProviderError


class FakeClient:
    def chat_json(self, _system, prompt, max_tokens=0):
        if "final editorial review" in prompt:
            payload = prompt.split("SCENES WITH THEIR EXACT NARRATION:\n", 1)[1].strip()
            return {"scenes": json.loads(payload)}
        return {"scenes": []}


class TruncatingPlanner:
    def __init__(self):
        self.calls = 0

    def chat_json(self, _system, prompt, max_tokens=0):
        self.calls += 1
        payload = json.loads(prompt.split("BEATS:\n", 1)[1].split("\n\nReturn:", 1)[0])
        if len(payload) > 2:
            raise ProviderError("El analizador devolvió JSON incompleto o inválido.")
        return {"scenes": [{
            "id": item["id"],
            "literal_subject": item["narration"],
            "image_prompt": "literal image",
            "video_prompt": "literal movement" if item["type"] == "video" else "",
            "continuity_ids": [], "named_brand": "", "reject_if": [],
        } for item in payload]}


class MalformedFieldsPlanner:
    def chat_json(self, _system, prompt, max_tokens=0):
        payload = json.loads(prompt.split("BEATS:\n", 1)[1].split("\n\nReturn:", 1)[0])
        return {"scenes": [{
            "id": item["id"], "literal_subject": None,
            "image_prompt": None, "video_prompt": None,
            "continuity_ids": "wrong type", "named_brand": None,
            "reject_if": None,
        } for item in payload]}


class PlanningTests(unittest.TestCase):
    def test_first_person_biography_uses_source_presenter_not_generated_faces(self):
        scenes = [{
            "id": "b001", "type": "video", "requested_type": "video",
            "narration": "My name is Frank Delaney. I worked in financial crimes.",
            "literal_subject": "A man speaking directly to camera in an office",
            "video_prompt": "A portrait interview subject speaking to camera in an office",
        }]
        enforce_narration_visual_contract(scenes)
        self.assertEqual(scenes[0]["type"], "avatar")
        self.assertEqual(scenes[0]["contract_fallback"], "source_presenter_for_identity_or_talking_head")

    def test_across_table_first_person_beat_uses_source_presenter(self):
        scenes = [{
            "id": "b002", "type": "video", "requested_type": "video",
            "narration": "I sat across the table from people who got taken.",
            "literal_subject": "Two people in an interview",
            "video_prompt": "Two people talking in an interview",
        }]
        enforce_narration_visual_contract(scenes)
        self.assertEqual(scenes[0]["type"], "avatar")
        self.assertEqual(scenes[0]["contract_fallback"], "source_presenter_for_identity_or_talking_head")

    def test_scene_validation_converts_adjacent_duplicate_narration_to_free_presenter(self):
        scenes = [
            {"id": "b0001", "type": "image", "requested_type": "image", "start": 0, "end": 4, "narration": "one factual sentence", "literal_subject": "one object", "image_prompt": "one object on a table"},
            {"id": "b0002", "type": "video", "requested_type": "video", "start": 4, "end": 8, "narration": "one factual sentence", "literal_subject": "one object", "video_prompt": "one object on a table"},
        ]
        warnings = validate_scene_plan(scenes, 8)
        self.assertEqual(scenes[1]["type"], "avatar")
        self.assertEqual(scenes[1]["duplicate_of"], "b0001")
        self.assertEqual(warnings[0]["kind"], "repeated_narration")

    def test_scene_validation_rejects_invalid_timeline_ranges(self):
        with self.assertRaisesRegex(ValueError, "fuera"):
            validate_scene_plan([{"id": "b0001", "type": "image", "start": 0, "end": 12}], 10)

    def test_post_analysis_rebalance_stays_inside_available_balance_and_buffer(self):
        scenes = [{
            "id": f"b{index + 1:04d}",
            "type": "avatar" if index == 0 else "video",
            "requested_type": "avatar" if index == 0 else "video",
            "start": index * 4.0,
            "end": (index + 1) * 4.0,
            "duration": 4.0,
        } for index in range(100)]

        adjusted, estimate = rebalance_scenes_for_budget(
            scenes, available_usd=0.70, buffer=0.12,
        )

        self.assertLessEqual(estimate, 0.58 + 1e-9)
        self.assertEqual(len(adjusted), len(scenes))
        self.assertEqual([scene["id"] for scene in adjusted], [scene["id"] for scene in scenes])
        self.assertEqual(adjusted[0]["type"], "avatar")

    def test_post_analysis_rebalance_spreads_paid_visuals_across_every_decile(self):
        scenes = [{
            "id": f"b{index + 1:04d}",
            "type": "avatar" if index == 0 else "video",
            "requested_type": "avatar" if index == 0 else "video",
            "start": index * 4.0,
            "end": (index + 1) * 4.0,
            "duration": 4.0,
        } for index in range(100)]

        adjusted, _estimate = rebalance_scenes_for_budget(
            scenes, available_usd=0.70, buffer=0.12,
        )
        paid = {"video", "image", "split", "still"}
        paid_per_decile = [
            sum(scene["type"] in paid for scene in adjusted[start:start + 10])
            for start in range(0, 100, 10)
        ]
        paid_per_quarter = [
            sum(scene["type"] in paid for scene in adjusted[start:start + 25])
            for start in range(0, 100, 25)
        ]

        self.assertTrue(all(count > 0 for count in paid_per_decile), paid_per_decile)
        self.assertTrue(all(count > 0 for count in paid_per_quarter), paid_per_quarter)
        self.assertTrue(any(scene["type"] in paid for scene in adjusted[-10:]))
        longest_avatar_run = 0
        current_avatar_run = 0
        for scene in adjusted:
            current_avatar_run = current_avatar_run + 1 if scene["type"] == "avatar" else 0
            longest_avatar_run = max(longest_avatar_run, current_avatar_run)
        self.assertLessEqual(longest_avatar_run, 4)

    def test_stratified_generation_order_covers_the_whole_timeline_first(self):
        scenes = [{"id": f"b{index + 1:04d}", "type": "image"} for index in range(100)]

        ordered = stratified_generation_order(scenes, buckets=10)

        self.assertEqual(len(ordered), len(scenes))
        self.assertEqual({scene["id"] for scene in ordered}, {scene["id"] for scene in scenes})
        for prefix_size, expected_per_decile in ((10, 1), (20, 2), (30, 3)):
            completed = {int(scene["id"][1:]) - 1 for scene in ordered[:prefix_size]}
            per_decile = [
                sum(index in completed for index in range(start, start + 10))
                for start in range(0, 100, 10)
            ]
            self.assertEqual(per_decile, [expected_per_decile] * 10)
        completed = {int(scene["id"][1:]) - 1 for scene in ordered[:30]}
        longest_missing_run = current = 0
        for index in range(100):
            current = 0 if index in completed else current + 1
            longest_missing_run = max(longest_missing_run, current)
        self.assertLessEqual(longest_missing_run, 3)

    def test_video_prompts_lock_simple_physical_sport_action(self):
        combined = " ".join((VIDEO_MOTION, SCENE_BATCH_PROMPT, SCENE_PLAN_REVIEW_PROMPT, VIDEO_REVIEW_PROMPT)).lower()
        self.assertIn("exactly one ball", combined)
        self.assertIn("crossed/duplicated nets", combined)
        self.assertIn("backwards play", combined)
        self.assertIn("minimum participants", combined)

    def test_every_generated_asset_has_explicit_anatomy_and_count_guards(self):
        image_text = image_prompt({"type": "image", "image_prompt": "one person holding one phone"}).lower()
        video_text = video_prompt({"type": "video", "video_prompt": "one person lifts one box"}).lower()
        review_text = " ".join((IMAGE_REVIEW_PROMPT, VIDEO_REVIEW_PROMPT, SCENE_PLAN_REVIEW_PROMPT)).lower()

        for generated_prompt in (image_text, video_text):
            self.assertIn("physical integrity lock", generated_prompt)
            self.assertIn("extra", generated_prompt)
            self.assertIn("finger", generated_prompt)
            self.assertIn("do not clone", generated_prompt)
        self.assertIn("count the visible people", review_text)
        self.assertIn("integrity_score", review_text)
        self.assertIn("natural cropping and occlusion", review_text)

    def test_qr_window_forces_every_overlapping_beat_to_avatar(self):
        items = [
            {"id": "b1", "start": 0, "end": 5, "type": "video", "video_prompt": "paid"},
            {"id": "b2", "start": 5, "end": 10, "type": "image", "image_prompt": "paid"},
            {"id": "b3", "start": 10, "end": 15, "type": "video", "video_prompt": "paid"},
        ]
        force_avatar_window(items, {"start": 4.5, "end": 10.5})
        self.assertEqual([item["type"] for item in items], ["avatar", "avatar", "avatar"])
        self.assertTrue(all(not item.get("image_prompt") and not item.get("video_prompt") for item in items))

    def test_qr_window_does_not_change_non_overlapping_beats(self):
        items = [{"id": "b1", "start": 0, "end": 5, "type": "video"}]
        force_avatar_window(items, {"start": 5, "end": 10})
        self.assertEqual(items[0]["type"], "video")

    def test_words_are_assigned_once_across_beats(self):
        transcript = [{
            "start": 0.0,
            "end": 12.0,
            "text": "one two three four five six seven eight nine ten eleven twelve",
        }]
        beats = make_beats(12.0, transcript)
        words = " ".join(item["narration"] for item in beats).split()
        self.assertEqual(words, transcript[0]["text"].split())

    def test_reference_mix_starts_with_host_and_matches_hybrid_facetuber_pattern(self):
        types = editorial_types(240)
        counts = Counter(types)
        self.assertEqual(types[0], "avatar")
        self.assertAlmostEqual(counts["video"] / 240, 0.40, delta=0.02)
        self.assertAlmostEqual(counts["image"] / 240, 0.38, delta=0.02)
        self.assertAlmostEqual(counts["avatar"] / 240, 0.14, delta=0.02)
        self.assertAlmostEqual(counts["split"] / 240, 0.08, delta=0.02)
        self.assertEqual(counts["still"], 0)

    def test_presenter_returns_alternate_full_and_split_when_both_are_available(self):
        types = editorial_types(140)
        returns = [media_type for media_type in types if media_type in {"avatar", "split"}]
        self.assertEqual(returns[0], "avatar")
        alternating = returns[:2 * returns.count("split") + 1]
        for previous, current in zip(alternating, alternating[1:]):
            self.assertNotEqual(previous, current)

    def test_motion_led_bible_uses_more_video_without_changing_host_share(self):
        motion = visual_ratios_for_bible({"visual_strategy": "motion_led"})
        hybrid = visual_ratios_for_bible({"visual_strategy": "hybrid"})
        self.assertGreater(motion["video"], hybrid["video"])
        self.assertAlmostEqual(motion["avatar"] + motion["split"], 0.23, delta=0.01)

    def test_full_led_list_format_keeps_split_rare_and_late(self):
        bible = {"visual_strategy": "motion_led", "presenter_pattern": "full_led"}
        ratios = visual_ratios_for_bible(bible)
        self.assertGreater(ratios["avatar"], 0.20)
        self.assertLessEqual(ratios["split"], 0.01)
        types = editorial_types(170, ratios, presenter_pattern="full_led")
        split_indexes = [index for index, item in enumerate(types) if item == "split"]
        self.assertLessEqual(len(split_indexes), 2)
        if split_indexes:
            self.assertGreater(split_indexes[0], len(types) * 0.35)

    def test_presenter_positions_never_accumulate_at_the_end(self):
        types = editorial_types(
            200,
            visual_ratios_for_bible({"visual_strategy": "motion_led", "presenter_pattern": "full_led"}),
            presenter_pattern="full_led",
        )
        longest_host_run = current = 0
        for media_type in types:
            current = current + 1 if media_type in {"avatar", "split"} else 0
            longest_host_run = max(longest_host_run, current)
        self.assertLessEqual(longest_host_run, 1)
        self.assertNotIn(types[-2:], (["avatar", "avatar"], ["split", "avatar"], ["avatar", "split"]))

    def test_seven_dollar_schedule_respects_time_capacity_without_erasing_veo(self):
        duration = 35 * 60.0
        transcript = [{"start": 0.0, "end": duration, "text": "ordinary factual narration " * 1400}]
        beats, estimate = build_schedule(
            duration, transcript, 7.0,
            bible={"visual_strategy": "motion_led", "presenter_pattern": "full_led"},
        )
        counts = Counter(item["type"] for item in beats)
        self.assertLessEqual(len(beats), 260)
        self.assertLessEqual(counts["video"], 72)
        self.assertGreaterEqual(counts["video"], 55)
        self.assertLessEqual(estimate, 7.0 - analysis_reserve_for_duration(duration) + 0.001)
        self.assertLessEqual(max(item["duration"] for item in beats if item["type"] == "video"), 8.001)

    def test_beats_prefer_real_phrase_boundaries_over_a_blind_timer(self):
        transcript = [
            {"start": 0.0, "end": 3.2, "text": "The first concrete sentence ends here."},
            {"start": 3.2, "end": 6.7, "text": "The next sentence explains another visible action."},
            {"start": 6.7, "end": 10.1, "text": "A third sentence finishes the thought."},
        ]
        beats = make_beats(10.1, transcript)
        self.assertEqual([beat["end"] for beat in beats], [3.2, 6.7, 10.1])

    def test_long_video_never_stretches_or_repeats_an_eight_second_veo_clip(self):
        duration = 35 * 60.0
        transcript = [{
            "start": 0.0,
            "end": duration,
            "text": "ordinary factual narration " * 1200,
        }]
        beats, estimate = build_schedule(
            duration, transcript, 4.0,
            bible={"visual_strategy": "hybrid", "presenter_pattern": "alternating"},
        )
        counts = Counter(item["type"] for item in beats)
        self.assertLessEqual(estimate, 4.0 - analysis_reserve_for_duration(duration) + 0.001)
        self.assertLessEqual(
            max(item["duration"] for item in beats if item["type"] == "video"),
            8.001,
        )
        self.assertGreater(max(item["duration"] for item in beats if item["type"] == "image"), 8.0)
        self.assertGreater(counts["video"], 0)
        self.assertGreater(counts["image"], 0)
        self.assertLess(counts["avatar"] / len(beats), 0.22)
        quarter = len(beats) // 4
        early_avatar = sum(item["type"] == "avatar" for item in beats[:quarter]) / quarter
        late_avatar = sum(item["type"] == "avatar" for item in beats[-quarter:]) / quarter
        self.assertLess(abs(early_avatar - late_avatar), 0.12)

    def test_small_budget_reduction_prefers_images_over_more_avatar(self):
        original = ["avatar", "video", "image", "video", "split"]
        adjusted, estimate = enforce_budget(original, 0.06, reserve=0.0)
        self.assertLessEqual(estimate, 0.06)
        self.assertEqual(adjusted.count("avatar"), 1)
        self.assertGreater(adjusted.count("image"), original.count("image"))

    def test_budget_adaptation_keeps_short_reference_videos_at_facetuber_cadence(self):
        duration = 8 * 60.0
        transcript = [{"start": 0.0, "end": duration, "text": "one visible action " * 400}]
        beats, _estimate = build_schedule(duration, transcript, 4.0)
        self.assertGreater(len(beats), 125)
        self.assertLess(duration / len(beats), 4.0)

    def test_presenter_reference_is_rare_semantic_and_never_consecutive(self):
        scenes = []
        for index in range(40):
            scenes.append({
                "id": f"b{index + 1:04d}",
                "type": "video" if index % 2 == 0 else "image",
                "presenter_broll": index % 3 == 0,
                "presenter_broll_reason": "the host demonstrates this ordinary action",
                "presenter_broll_value": 4,
            })
        enforce_presenter_broll(scenes, {"presenter_reuse_strategy": "selective"})
        selected = [index for index, scene in enumerate(scenes) if scene.get("presenter_broll")]
        self.assertLessEqual(len(selected), 3)
        self.assertTrue(all(scenes[index]["type"] == "video" for index in selected))
        self.assertTrue(all(right - left > 1 for left, right in zip(selected, selected[1:])))
        self.assertGreater(selected[-1], len(scenes) // 2)

    def test_presenter_reference_is_disabled_when_story_does_not_need_the_host(self):
        scenes = [{"id": "b0001", "type": "video", "presenter_broll": True}]
        enforce_presenter_broll(scenes, {"presenter_reuse_strategy": "none"})
        self.assertFalse(scenes[0]["presenter_broll"])

    def test_silence_does_not_create_paid_media(self):
        beats, estimate = build_schedule(20.0, [], 4.0)
        self.assertTrue(all(item["type"] == "avatar" for item in beats))
        self.assertEqual(estimate, 0.0)

    def test_prompt_context_and_review_keep_all_scenes(self):
        beats, _ = build_schedule(15.0, [{"start": 0, "end": 15, "text": "a literal practical action continues across the whole explanation"}], 4.0)
        bible = {"topic": "test", "recurring_subjects": []}
        scenes = plan_scenes(FakeClient(), beats, bible, batch_size=2)
        reviewed = review_scene_plan(FakeClient(), scenes, bible, batch_size=2)
        self.assertEqual([item["id"] for item in reviewed], [item["id"] for item in beats])

    def test_truncated_planning_batch_is_split_automatically(self):
        beats, _ = build_schedule(32.0, [{
            "start": 0, "end": 32,
            "text": "one two three four five six seven eight nine ten eleven twelve thirteen fourteen",
        }], 4.0)
        client = TruncatingPlanner()
        planned = plan_scenes(client, beats, {"topic": "test"}, batch_size=6)
        self.assertEqual([item["id"] for item in planned], [item["id"] for item in beats])
        self.assertGreater(client.calls, 2)

    def test_malformed_optional_fields_are_normalized_before_generation(self):
        beats, _ = build_schedule(15.0, [{"start": 0, "end": 15, "text": "literal factual narration"}], 4.0)
        planned = plan_scenes(MalformedFieldsPlanner(), beats, {"topic": "test"}, batch_size=6)
        for scene in planned:
            self.assertIsInstance(scene["literal_subject"], str)
            self.assertIsInstance(scene["image_prompt"], str)
            self.assertIsInstance(scene["video_prompt"], str)
            self.assertIsInstance(scene["continuity_ids"], list)
            self.assertIsInstance(scene["reject_if"], list)

    def test_failed_video_becomes_same_beat_image_not_avatar(self):
        scene = {
            "id": "b0042", "type": "video", "requested_type": "video",
            "narration": "The caller asks for the verification code.",
            "literal_subject": "one older person holding one phone",
            "video_prompt": "one person answers one phone",
        }
        fallback = image_fallback_scene(scene)
        self.assertEqual(fallback["type"], "image")
        self.assertEqual(fallback["requested_type"], "video")
        self.assertIn("verification code", fallback["image_prompt"])
        self.assertEqual(fallback["video_prompt"], "")


if __name__ == "__main__":
    unittest.main()
