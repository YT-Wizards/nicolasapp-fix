import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from operations import OperationLedger, OperationRecoveryRequired


class OperationLedgerTests(unittest.TestCase):
    def test_prepared_operation_blocks_duplicate_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = OperationLedger(Path(directory) / "vyt.sqlite")
            key = ledger.operation_key("job", "scene", "video", "snapgen", "literal prompt", {"duration": 8})
            self.assertEqual(
                ledger.prepare(key, "job", "scene", "snapgen", "video", ledger.prompt_hash("literal prompt")),
                "",
            )
            with self.assertRaises(OperationRecoveryRequired):
                ledger.prepare(key, "job", "scene", "snapgen", "video", ledger.prompt_hash("literal prompt"))
            ledger.close()

    def test_submitted_operation_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = OperationLedger(Path(directory) / "vyt.sqlite")
            key = ledger.operation_key("job", "scene", "image", "algrow", "prompt")
            ledger.prepare(key, "job", "scene", "algrow", "image", ledger.prompt_hash("prompt"))
            ledger.mark_submitted(key, "remote-123")
            self.assertEqual(
                ledger.prepare(key, "job", "scene", "algrow", "image", ledger.prompt_hash("prompt")),
                "remote-123",
            )
            ledger.close()

    def test_cost_events_are_grouped_by_kind(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = OperationLedger(Path(directory) / "vyt.sqlite")
            ledger.record_cost("job", "reserved", 0.20, provider="budget")
            ledger.record_cost("job", "released", 0.20, provider="budget")
            ledger.record_cost("job", "charged", 0.14, provider="algrow", operation_key="op")
            ledger.record_cost("job", "avoided_duplicate", 0.14, provider="algrow", operation_key="op")
            totals = ledger.cost_totals("job")
            self.assertEqual(totals["charged"], 0.14)
            self.assertEqual(totals["avoided_duplicate"], 0.14)
            ledger.close()


if __name__ == "__main__":
    unittest.main()
