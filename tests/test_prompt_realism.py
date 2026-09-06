import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from prompts import (
    STYLE_CORE, VIDEO_STYLE_CORE, SCENE_BATCH_PROMPT, SCENE_PLAN_REVIEW_PROMPT,
    image_prompt, video_prompt, scene_specific_constraints,
)


class PromptRealismTests(unittest.TestCase):
    def test_same_natural_material_style_reaches_images_and_video(self):
        for builder, kind in ((image_prompt, "image"), (video_prompt, "video")):
            text = builder({"type": kind, f"{kind}_prompt": "One steel kettle on a kitchen counter."})
            self.assertIn(STYLE_CORE if kind == "image" else VIDEO_STYLE_CORE, text)
            self.assertIn("physically appropriate reflections", text)
            self.assertIn("keep the subject clear", text)

    def test_phone_clip_does_not_receive_unrelated_sport_or_animal_instructions(self):
        text = video_prompt({"video_prompt": "One older person holds one phone."})
        self.assertIn("PHONE DETAIL:", text)
        for unrelated in ("RACKET-SPORT DETAIL:", "GOLF DETAIL:", "ANIMAL DETAIL:", "one court"):
            self.assertNotIn(unrelated, text)

    def test_golf_does_not_require_a_net(self):
        text = scene_specific_constraints({"video_prompt": "One golfer at address."}, "video")
        self.assertIn("GOLF DETAIL:", text)
        self.assertNotIn("net", text)
        self.assertNotIn("court", text)

    def test_pickleball_equipment_shot_does_not_require_players_or_ball(self):
        text = image_prompt({"image_prompt": "One pickleball paddle lying on a bench."})
        self.assertIn("RACKET-SPORT DETAIL:", text)
        self.assertIn("do not add a ball or net to an equipment-only close-up", text)

    def test_constraints_follow_depicted_scene_not_entire_narration(self):
        scene = {"image_prompt": "One ceramic cup on a shelf.", "narration": "After golf, walk your dog and answer your phone."}
        self.assertEqual(scene_specific_constraints(scene, "image"), "")

    def test_camera_is_stable_without_positive_drift_request(self):
        text = video_prompt({"video_prompt": "One dog resting beside a sofa."})
        self.assertIn("locked-off stable camera", text)
        self.assertIn("No handheld drift, shake, zoom", text)
        self.assertNotIn("very slight natural handheld drift", text)
        self.assertNotIn("paused frame", text)
        self.assertIn("ANIMAL DETAIL:", text)

    def test_feasibility_instructions_reach_planning_and_review(self):
        for prompt in (SCENE_BATCH_PROMPT, SCENE_PLAN_REVIEW_PROMPT):
            self.assertIn("RELIABLE SCENE DESIGN:", prompt)
            self.assertIn("simplify staging, never invent a different fact", prompt)
            self.assertIn("Do not force hands or a full body into an object-only shot", prompt)

    def test_split_crop_and_presenter_identity_contracts_are_preserved(self):
        text = image_prompt({"type": "split", "image_prompt": "One oven rack."})
        self.assertIn("central 8:9 portrait-safe area", text)
        text = video_prompt({"video_prompt": "A host inspects a shelf.", "presenter_broll": True, "presenter_identity": "short grey hair"})
        self.assertIn("PRESENTER CONTINUITY LOCK", text)
        self.assertIn("short grey hair", text)
        self.assertIn("does not lip-sync", text)

    def test_correction_and_null_content_are_safe(self):
        for builder, kind in ((image_prompt, "image"), (video_prompt, "video")):
            text = builder({f"{kind}_prompt": None, "literal_subject": "one wooden chair"}, "Keep the chair legs distinct")
            self.assertIn("one wooden chair", text)
            self.assertIn("Keep the chair legs distinct", text)


if __name__ == "__main__":
    unittest.main()
