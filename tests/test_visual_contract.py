import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from visual_contract import (  # noqa: E402 - deliberate test seam
    VisualSceneContract,
    apply_visual_contract,
    validate_contract,
)


class VisualContractRegressionTests(unittest.TestCase):
    def contract_for(self, narration, **scene):
        base = {
            "id": "b001", "type": "image", "start": 0, "end": 4,
            "narration": narration, "literal_subject": narration,
        }
        base.update(scene)
        return VisualSceneContract.from_scene(base, job_id="job-a")

    def test_receiving_sos_does_not_mean_showing_a_phone_screen(self):
        contract = self.contract_for("He received an SOS message during the night.")
        required = {item["id"] for item in contract.constraints["required"]}
        self.assertNotIn("display_faces_camera", required)
        self.assertNotIn("exact_screen_text", required)

    def test_reading_device_requires_gaze_and_display_access_for_actor(self):
        contract = self.contract_for("She reads a warning on her phone.")
        required = {item["id"] for item in contract.constraints["required"]}
        self.assertIn("actor_looks_at_device", required)
        self.assertIn("display_faces_actor", required)
        self.assertNotIn("display_faces_camera", required)

    def test_explicitly_showing_screen_to_viewer_allows_camera_facing_display(self):
        contract = self.contract_for("She shows the phone screen to the viewer.")
        required = {item["id"] for item in contract.constraints["required"]}
        self.assertIn("display_faces_camera", required)
        self.assertNotIn("actor_looks_at_device", required)

    def test_holding_phone_while_addressing_camera_allows_camera_gaze(self):
        contract = self.contract_for("He holds a phone and addresses the camera.")
        required = {item["id"] for item in contract.constraints["required"]}
        self.assertIn("actor_looks_at_camera", required)
        self.assertNotIn("actor_looks_at_device", required)

    def test_laptop_uses_same_surface_relation_as_phone(self):
        contract = self.contract_for("He checks his laptop while on a call.")
        required = {item["id"] for item in contract.constraints["required"]}
        self.assertIn("display_faces_actor", required)
        self.assertIn("actor_looks_at_device", required)

    def test_interaction_avatar_is_replanned_not_faked_with_source_slice(self):
        scene = {
            "id": "b002", "type": "avatar", "requested_type": "avatar",
            "start": 4, "end": 8,
            "narration": "He reads the warning on his phone.",
            "literal_subject": "HeyGen source presenter",
        }
        adjusted = apply_visual_contract(scene, job_id="job-a", is_opening=False)
        self.assertEqual(adjusted["type"], "image")
        self.assertEqual(adjusted["realization"]["route"], "stable_image")

    def test_contract_rejects_contradictory_screen_requirements_before_submit(self):
        contract = self.contract_for("She reads a phone.")
        contract.constraints["required"].append({
            "id": "display_faces_camera", "relation": "display_surface->faces->camera", "severity": "critical",
        })
        errors = validate_contract(contract)
        self.assertTrue(any("contradictory" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
