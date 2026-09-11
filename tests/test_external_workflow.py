import sys
import unittest
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from external_workflow import build_external_plan, validate_clip_inventory


class PromptWriter:
    def chat_json(self, _system, _prompt, **_kwargs):
        return {"scenes": [{
            "id": "001",
            "visual_prompt": "Over-the-shoulder shot of one adult at a kitchen table checking a phone held close to their body, with the display facing the person and soft window light from the side.",
            "reject_if": ["front-facing phone screen", "extra hands", "readable interface"],
        }]}


class BatchFailingPromptWriter:
    """Simulates an incomplete provider envelope for multi-scene batches."""
    def chat_json(self, _system, prompt, **_kwargs):
        ids = re.findall(r'"id":\s*"(\d+)"', prompt.split("TIMED BEATS:", 1)[1])
        if len(ids) > 1:
            raise ValueError("incomplete JSON")
        return {"scenes": [{
            "id": ids[0],
            "visual_prompt": "A single person at an ordinary table studies one phone from a side angle, with the display facing the person and natural daylight across the room.",
            "reject_if": ["front-facing screen", "extra hands", "readable interface"],
        }]}


class ExternalWorkflowTests(unittest.TestCase):
    def test_plan_numbers_prompts_and_keeps_beats_at_or_under_eight_seconds(self):
        transcript = [
            {"start": 0, "end": 4, "text": "First warning sentence."},
            {"start": 4, "end": 16, "text": "Second warning sentence with more detail."},
        ]
        plan = build_external_plan(
            "First warning sentence. Second warning sentence with more detail.",
            transcript, "realistic documentary"
        )
        self.assertEqual([item["number"] for item in plan["scenes"]], [1, 2, 3])
        self.assertTrue(all(item["duration"] <= 8 for item in plan["scenes"]))
        self.assertTrue(all(item["prompt"].startswith(f"{item['number']:03d}.") for item in plan["scenes"]))

    def test_plan_rewrites_each_scene_as_a_visual_prompt_when_ai_is_available(self):
        plan = build_external_plan(
            "A person checks a phone.",
            [{"start": 0, "end": 4, "text": "A person checks a phone."}],
            "realistic documentary", prompt_client=PromptWriter(),
        )
        scene = plan["scenes"][0]
        self.assertEqual(plan["prompt_generation"]["mode"], "ai")
        self.assertEqual(scene["prompt_source"], "ai")
        self.assertIn("Over-the-shoulder", scene["prompt"])
        self.assertIn("front-facing phone screen", scene["reject_if"])

    def test_retries_a_failed_batch_one_scene_at_a_time_before_using_fallback(self):
        plan = build_external_plan(
            "First person checks a phone. Second person puts a phone down.",
            [{"start": 0, "end": 4, "text": "First person checks a phone."}, {"start": 4, "end": 8, "text": "Second person puts a phone down."}],
            "realistic documentary", prompt_client=BatchFailingPromptWriter(),
        )
        self.assertEqual(plan["prompt_generation"]["mode"], "ai")
        self.assertTrue(all(scene["prompt_source"] == "ai" for scene in plan["scenes"]))

    def test_inventory_reports_missing_and_short_clips(self):
        plan = {"scenes": [
            {"number": 1, "duration": 4}, {"number": 2, "duration": 5}, {"number": 3, "duration": 3},
        ]}
        report = validate_clip_inventory(plan, {"001.mp4": 8, "002.mp4": 3})
        self.assertEqual(report["missing"], [3])
        self.assertEqual(report["too_short"], [{"number": 2, "required": 5.0, "actual": 3.0}])
        self.assertFalse(report["ready"])

    def test_plan_handles_one_whisper_segment_for_many_script_sentences(self):
        plan = build_external_plan(
            "First sentence. Second sentence. Third sentence.",
            [{"start": 0, "end": 12, "text": "all narration collapsed into one segment"}],
            "realistic documentary",
        )
        self.assertEqual(plan["scenes"][0]["start"], 0.0)
        self.assertAlmostEqual(plan["scenes"][-1]["end"], 12.0)
        self.assertTrue(all(item["duration"] > 0 for item in plan["scenes"]))
