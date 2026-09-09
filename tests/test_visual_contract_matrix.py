import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from visual_contract import VisualSceneContract, validate_contract


# Regression matrix: each row is an independently labelled semantic contract,
# not a timestamp or a workaround for the Nicolas reference video.
SCENARIOS = [
    ("phone_read", "She reads a phone.", {"actor_looks_at_device", "display_faces_actor"}, set()),
    ("phone_check", "He checks his phone.", {"actor_looks_at_device", "display_faces_actor"}, set()),
    ("phone_hold", "She holds a phone.", {"natural_grip"}, set()),
    ("phone_call", "He talks on the phone.", {"call_position"}, {"display_faces_actor"}),
    ("phone_camera", "He holds a phone and addresses the camera.", {"actor_looks_at_camera"}, {"actor_looks_at_device"}),
    ("phone_viewer", "She shows the phone screen to the viewer.", {"display_faces_camera"}, {"actor_looks_at_device"}),
    ("phone_recipient", "He shows the phone to another person.", {"display_faces_recipient"}, {"display_faces_camera"}),
    ("phone_message", "She received an SOS message.", set(), {"display_faces_camera", "exact_screen_text"}),
    ("phone_exact", "She shows the phone screen to the viewer: 'SOS'.", {"display_faces_camera", "exact_screen_text"}, set()),
    ("laptop_check", "He checks his laptop while on a call.", {"actor_looks_at_device", "display_faces_actor", "call_position"}, set()),
    ("laptop_type", "She types on a laptop.", {"actor_looks_at_device", "display_faces_actor"}, set()),
    ("tablet_read", "She reads a tablet.", {"actor_looks_at_device", "display_faces_actor"}, set()),
    ("tablet_show", "She shows the tablet to the camera.", {"display_faces_camera"}, set()),
    ("monitor_observe", "He looks at a monitor.", {"actor_looks_at_device", "display_faces_actor"}, set()),
    ("monitor_context", "A monitor is on a desk.", set(), {"display_faces_actor"}),
    ("book_read", "She reads a book.", {"actor_looks_at_device"}, set()),
    ("book_hold", "He holds a book.", {"natural_grip"}, set()),
    ("tool_hold", "She holds a hammer.", {"natural_grip"}, set()),
    ("tool_use", "He uses a screwdriver.", {"actor_looks_at_device"}, set()),
    ("mug_hold", "She holds a mug.", {"natural_grip"}, set()),
    ("empty_context", "A quiet kitchen at night.", set(), set()),
    ("ru_phone_read", "Она читает телефон.", {"actor_looks_at_device", "display_faces_actor"}, set()),
    ("ru_phone_hold", "Он держит телефон.", {"natural_grip"}, set()),
    ("ru_phone_camera", "Он держит телефон и смотрит в камеру.", {"actor_looks_at_camera"}, {"actor_looks_at_device"}),
    ("es_phone_read", "Ella lee el teléfono.", {"actor_looks_at_device", "display_faces_actor"}, set()),
    ("es_phone_hold", "Él sostiene un teléfono.", set(), set()),
    ("read_and_call", "She reads the phone while on a call.", {"actor_looks_at_device", "call_position"}, set()),
    ("viewer_and_text", "He shows the phone to the viewer: 'Call now'.", {"display_faces_camera", "exact_screen_text"}, set()),
    ("no_action_phone", "The phone rang at night.", set(), {"display_faces_actor"}),
    ("generic_person", "A person listens in a room.", set(), set()),
    ("interview", "An expert explains the result in an interview.", set(), set()),
    ("laptop_camera", "He holds a laptop and addresses the camera.", {"actor_looks_at_camera"}, {"actor_looks_at_device"}),
    ("two_devices", "She reads a phone beside a laptop.", {"actor_looks_at_device"}, set()),
    ("phone_turn", "He turns his phone off.", {"actor_looks_at_device", "display_faces_actor"}, set()),
    ("phone_browse", "She browses a phone.", {"actor_looks_at_device", "display_faces_actor"}, set()),
    ("monitor_use", "He uses a monitor.", {"actor_looks_at_device", "display_faces_actor"}, set()),
    ("book_context", "A book lies on a table.", set(), set()),
    ("container_context", "A bottle is on the counter.", set(), set()),
    ("recipient_not_viewer", "She shows the tablet to a friend.", {"display_faces_recipient"}, {"display_faces_camera"}),
    ("explicit_sos_event", "He received an SOS text and looks worried.", set(), {"exact_screen_text"}),
]


class VisualContractMatrixTests(unittest.TestCase):
    def test_forty_general_semantic_contracts(self):
        self.assertEqual(len(SCENARIOS), 40)
        for name, narration, includes, excludes in SCENARIOS:
            with self.subTest(name=name):
                contract = VisualSceneContract.from_scene({
                    "id": name, "type": "image", "start": 0, "end": 4,
                    "narration": narration, "literal_subject": narration,
                })
                ids = {item["id"] for item in contract.constraints["required"]}
                self.assertTrue(includes <= ids, (name, ids))
                self.assertFalse(excludes & ids, (name, ids))
                self.assertEqual(validate_contract(contract), [])
