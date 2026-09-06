import os
import ast
import inspect
import json
import math
import sys
import tempfile
import threading
import time
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from vyt import BudgetExhaustedError, Pipeline, QualityReviewError, minimum_required_videos, stop_requested


class PipelineResumeTests(unittest.TestCase):
    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_pipeline_does_not_expire_after_seventy_five_minutes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            stop_requested.clear()
            with patch("vyt.time.monotonic", return_value=pipeline.started + 75 * 60 + 1):
                pipeline.check_stop()
            pipeline.cleanup()

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_check_stop_still_honors_manual_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            try:
                stop_requested.set()
                with self.assertRaisesRegex(InterruptedError, "cancelada"):
                    pipeline.check_stop()
            finally:
                stop_requested.clear()
                pipeline.cleanup()

    def test_parallel_asset_wait_has_no_job_wide_timeout(self):
        source = textwrap.dedent(inspect.getsource(Pipeline.run))
        tree = ast.parse(source)
        completed_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "as_completed"
        ]
        self.assertTrue(
            all(not any(keyword.arg == "timeout" for keyword in call.keywords) for call in completed_calls),
            "as_completed no debe volver a imponer un límite global a la producción",
        )
        wait_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "wait"
        ]
        self.assertTrue(completed_calls or wait_calls, "Pipeline.run debe esperar a sus recursos paralelos")
        stop_checks = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.func.attr == "check_stop"
        ]
        self.assertTrue(stop_checks, "la espera paralela debe seguir atendiendo la cancelación manual")
        for call in wait_calls:
            timeout = next((keyword.value for keyword in call.keywords if keyword.arg == "timeout"), None)
            if timeout is not None:
                self.assertNotIn(
                    "remaining_time", ast.unparse(timeout),
                    "wait puede sondear para cancelar, pero no usar el tiempo restante del trabajo",
                )
                self.assertIsInstance(timeout, ast.Constant)
                self.assertLessEqual(float(timeout.value), 5.0)
            return_when = next((keyword.value for keyword in call.keywords if keyword.arg == "return_when"), None)
            if return_when is not None:
                self.assertEqual(ast.unparse(return_when), "FIRST_COMPLETED")

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_cost_cap_is_configurable_to_seven_dollars(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            config = self.config(root, source)
            config["max_cost_usd"] = 7.0
            pipeline = Pipeline(config)

            pipeline.reserve(6.99)
            self.assertAlmostEqual(pipeline.reserved_usd, 6.99, places=4)
            pipeline.release(6.99)
            pipeline.director.spent_usd = 6.99
            with self.assertRaises(BudgetExhaustedError):
                pipeline.reserve(0.02)
            pipeline.cleanup()

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_budget_exhaustion_does_not_retry_video_or_buy_image_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            scene = {
                "id": "b0021", "type": "video", "requested_type": "video",
                "narration": "one person answers one phone", "literal_subject": "one person",
            }
            calls = {"video": 0, "image": 0}

            def exhausted_video(*_args, **_kwargs):
                calls["video"] += 1
                raise BudgetExhaustedError("budget exhausted")

            def forbidden_image(*_args, **_kwargs):
                calls["image"] += 1
                return root / "must-not-exist.png"

            pipeline.generate_video_scene = exhausted_video
            pipeline.generate_image_scene = forbidden_image
            with self.assertRaises(BudgetExhaustedError):
                pipeline.generate_one(scene)
            self.assertEqual(calls, {"video": 1, "image": 0})
            self.assertEqual(scene["type"], "video")
            pipeline.cleanup()

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_budget_exhaustion_does_not_retry_an_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            scene = {
                "id": "b0022", "type": "image", "requested_type": "image",
                "narration": "one ordinary object", "literal_subject": "one object",
            }
            calls = {"image": 0}

            def exhausted_image(*_args, **_kwargs):
                calls["image"] += 1
                raise BudgetExhaustedError("budget exhausted")

            pipeline.generate_image_scene = exhausted_image
            with self.assertRaises(BudgetExhaustedError):
                pipeline.generate_one(scene)
            self.assertEqual(calls["image"], 1)
            self.assertEqual(scene["type"], "image")
            pipeline.cleanup()

    def test_video_quality_floor_does_not_disappear_when_the_gate_uses_fallbacks(self):
        self.assertEqual(minimum_required_videos(10), 7)
        self.assertEqual(minimum_required_videos(0), 0)

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_quality_rejection_skips_second_video_and_falls_back_to_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            scene = {
                "id": "b0011", "type": "video", "requested_type": "video",
                "narration": "one caller holds one phone", "literal_subject": "one caller",
            }
            calls = {"video": 0, "image": 0}

            def reject_video(*_args, **_kwargs):
                calls["video"] += 1
                raise QualityReviewError("bad physics")

            def accept_image(*_args, **_kwargs):
                calls["image"] += 1
                return root / "fallback.png"

            pipeline.generate_video_scene = reject_video
            pipeline.generate_image_scene = accept_image
            self.assertEqual(pipeline.generate_one(scene), root / "fallback.png")
            self.assertEqual(calls, {"video": 1, "image": 1})
            self.assertEqual(scene["type"], "image")
            pipeline.cleanup()

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    @patch("vyt.probe", return_value={"duration": 8.0, "width": 1280, "height": 720})
    def test_unindexed_paid_asset_is_recovered_from_disk(self, _probe):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            pipeline.prepare_checkpoint(source, 8.0)
            scene = {"id": "b0042", "type": "video"}
            orphan = pipeline.assets / "b0042.mp4"
            orphan.write_bytes(b"x" * 2000)
            pipeline.review_video = lambda *_args: {"pass": True, "watermark": False, "semantic_score": 90, "realism_score": 90, "integrity_score": 90, "motion_score": 90, "continuity_score": 90}
            self.assertEqual(pipeline.cached_asset_for(scene), orphan)
            self.assertTrue(pipeline.checkpoint["completed_assets"]["b0042"]["recovered"])
            pipeline.clear_checkpoint()
            pipeline.cleanup()

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_parallel_checkpoint_writes_use_distinct_atomic_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            pipeline.prepare_checkpoint(source, 8.0)
            threads = [threading.Thread(target=pipeline.save_checkpoint, kwargs={f"worker_{i}": i}) for i in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            saved = json.loads(pipeline.checkpoint_path.read_text())
            self.assertTrue(all(saved[f"worker_{i}"] == i for i in range(20)))
            pipeline.clear_checkpoint()
            pipeline.cleanup()

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_paid_veo_uuid_survives_restart_until_download_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            scene = {"id": "b0007", "type": "video"}
            first = Pipeline(self.config(root, source))
            first.prepare_checkpoint(source, 8.0)
            first.save_pending_video_job(scene, "paid-uuid")
            first.cleanup()
            resumed = Pipeline(self.config(root, source))
            resumed.prepare_checkpoint(source, 8.0)
            self.assertEqual(resumed.pending_video_job_for(scene), "paid-uuid")
            resumed.clear_checkpoint()
            resumed.cleanup()

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_paid_veo_uuid_is_recovered_even_when_the_cost_cap_is_already_full(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            pipeline.prepare_checkpoint(source, 8.0)
            scene = {
                "id": "b0014", "type": "video", "requested_type": "video",
                "narration": "one ordinary action", "literal_subject": "one person",
            }
            pipeline.save_pending_video_job(scene, "already-paid-uuid")
            pipeline.director.spent_usd = 4.0
            seen = {}

            def resume_video(_prompt, output, **kwargs):
                seen.update(kwargs)
                Path(output).write_bytes(b"x" * 2000)

            pipeline.geminigen.generate_video = resume_video
            pipeline.review_video = lambda *_args, **_kwargs: {
                "pass": True, "watermark": False, "semantic_score": 90,
                "realism_score": 90, "motion_score": 90, "continuity_score": 90, "integrity_score": 90,
            }
            asset = pipeline.generate_one(scene)
            self.assertEqual(asset, pipeline.assets / "b0014.mp4")
            self.assertEqual(seen["resume_uuid"], "already-paid-uuid")
            self.assertEqual(scene["type"], "video")
            self.assertAlmostEqual(pipeline.total_spent, 4.0, places=4)
            pipeline.clear_checkpoint()
            pipeline.cleanup()

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_paid_algrow_job_survives_restart_and_resumes_at_full_cost_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            scene = {
                "id": "b0015", "type": "image", "requested_type": "image",
                "narration": "one ordinary object", "literal_subject": "one object",
                "image_prompt": "one object on a table",
            }
            first = Pipeline(self.config(root, source))
            first.prepare_checkpoint(source, 8.0)
            first.algrow.spent_usd = 0.35 * first.algrow.CREDIT_USD
            first.save_pending_image_job(scene, "already-paid-image-job")
            first.cleanup()

            resumed = Pipeline(self.config(root, source))
            resumed.prepare_checkpoint(source, 8.0)
            self.assertEqual(resumed.pending_image_job_for(scene), "already-paid-image-job")
            resumed.director.spent_usd = 4.0 - resumed.total_spent
            seen = {}

            def resume_image(_prompt, output, **kwargs):
                seen.update(kwargs)
                Path(output).write_bytes(b"x" * 2000)
                return Path(output), "https://example.test/image.png"

            resumed.algrow.generate_image = resume_image
            resumed.review_image = lambda *_args, **_kwargs: {
                "pass": True, "semantic_score": 90, "realism_score": 90, "integrity_score": 90,
            }
            asset = resumed.generate_one(scene)
            self.assertEqual(asset, resumed.assets / "b0015.png")
            self.assertEqual(seen["resume_job_id"], "already-paid-image-job")
            self.assertEqual(resumed.pending_image_job_for(scene), "")
            self.assertAlmostEqual(resumed.total_spent, 4.0, places=4)
            resumed.clear_checkpoint()
            resumed.cleanup()

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_pending_video_fallback_image_resumes_before_buying_another_video(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            pipeline.prepare_checkpoint(source, 8.0)
            scene = {
                "id": "b0016", "type": "video", "requested_type": "video",
                "narration": "one caller holds one phone", "literal_subject": "one caller",
            }
            pipeline.save_pending_image_job(scene, "paid-fallback-image")
            seen = {}

            def resume_image(_prompt, output, **kwargs):
                seen.update(kwargs)
                Path(output).write_bytes(b"x" * 2000)
                return Path(output), "https://example.test/image.png"

            pipeline.geminigen.generate_video = lambda *_args, **_kwargs: self.fail("must not buy Veo")
            pipeline.algrow.generate_image = resume_image
            pipeline.review_image = lambda *_args, **_kwargs: {
                "pass": True, "semantic_score": 90, "realism_score": 90, "integrity_score": 90,
            }
            asset = pipeline.generate_one(scene)
            self.assertEqual(asset, pipeline.assets / "b0016.png")
            self.assertEqual(seen["resume_job_id"], "paid-fallback-image")
            self.assertEqual(scene["type"], "image")
            pipeline.clear_checkpoint()
            pipeline.cleanup()

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_image_generation_receives_a_finite_operation_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            scene = {
                "id": "b0017", "type": "image", "requested_type": "image",
                "narration": "one object", "literal_subject": "one object",
                "image_prompt": "one object on a table",
            }
            seen = {}

            def generate(_prompt, output, **kwargs):
                seen.update(kwargs)
                Path(output).write_bytes(b"x" * 2000)
                return Path(output), "https://example.test/image.png"

            pipeline.algrow.generate_image = generate
            pipeline.review_image = lambda *_args, **_kwargs: {
                "pass": True, "semantic_score": 90, "realism_score": 90, "integrity_score": 90,
            }
            pipeline.generate_image_scene(scene)
            self.assertIsInstance(seen.get("timeout"), (int, float))
            self.assertGreater(seen["timeout"], 0)
            self.assertTrue(math.isfinite(seen["timeout"]))
            pipeline.cleanup()

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_video_generation_receives_a_finite_operation_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            scene = {
                "id": "b0018", "type": "video", "requested_type": "video",
                "narration": "one action", "literal_subject": "one person",
                "video_prompt": "one person performs one action",
            }
            seen = {}

            def generate(_prompt, output, **kwargs):
                seen.update(kwargs)
                Path(output).write_bytes(b"x" * 2000)

            pipeline.geminigen.generate_video = generate
            pipeline.review_video = lambda *_args, **_kwargs: {
                "pass": True, "watermark": False, "semantic_score": 90,
                "realism_score": 90, "motion_score": 90, "continuity_score": 90, "integrity_score": 90,
            }
            pipeline.generate_video_scene(scene)
            self.assertIsInstance(seen.get("timeout"), (int, float))
            self.assertGreater(seen["timeout"], 0)
            self.assertTrue(math.isfinite(seen["timeout"]))
            pipeline.cleanup()

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    def test_checkpoint_changes_when_qr_configuration_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            first_config = self.config(root, source)
            first_config["product_sale"] = {"enabled": False}
            first = Pipeline(first_config)
            first.prepare_checkpoint(source, 8.0)
            without_qr = first.checkpoint_path
            first.cleanup()

            card = root / "card.png"
            card.write_bytes(b"x" * 1200)
            second_config = self.config(root, source)
            second_config["product_sale"] = {"enabled": True, "card_path": str(card)}
            second = Pipeline(second_config)
            second.prepare_checkpoint(source, 8.0)
            with_qr = second.checkpoint_path
            second.cleanup()
            self.assertNotEqual(without_qr, with_qr)

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway", "VYT_ALGROW_KEY": "algrow", "VYT_GEMINIGEN_KEY": "geminigen",
    })
    @patch("vyt.probe", return_value={"duration": 0.0, "width": 1920, "height": 1080})
    def test_video_scene_recovers_its_reviewed_image_fallback(self, _probe):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            pipeline.prepare_checkpoint(source, 8.0)
            scene = {"id": "b0009", "type": "video"}
            fallback = pipeline.assets / "b0009.png"
            fallback.write_bytes(b"x" * 2000)
            pipeline.review_image = lambda *_args: {"pass": True, "semantic_score": 90, "realism_score": 90, "integrity_score": 90}
            self.assertEqual(pipeline.cached_asset_for(scene), fallback)
            self.assertEqual(scene["type"], "image")
            pipeline.clear_checkpoint()
            pipeline.cleanup()

    def config(self, root, source):
        return {
            "id": "12345678-test",
            "source": str(source),
            "root_dir": str(root),
            "user_data_dir": str(root / "userdata"),
            "max_cost_usd": 4.0,
            "branding": False,
        }

    @patch.dict(os.environ, {
        "VYT_GATEWAY_KEY": "gateway",
        "VYT_ALGROW_KEY": "algrow",
        "VYT_GEMINIGEN_KEY": "geminigen",
    })
    @patch("vyt.probe", return_value={"duration": 8.0, "width": 1280, "height": 720})
    def test_paid_asset_and_cost_resume_after_a_failed_run(self, _probe):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            config = self.config(root, source)

            first = Pipeline(config)
            first.prepare_checkpoint(source, 10.0)
            scene = {"id": "b0003", "type": "video"}
            asset = first.assets / "b0003.mp4"
            asset.write_bytes(b"x" * 1200)
            first.director.spent_usd = 0.25
            first.mark_asset_completed(scene, asset)
            first.cleanup()

            resumed = Pipeline(config)
            resumed.prepare_checkpoint(source, 10.0)
            self.assertEqual(resumed.cached_asset_for(scene), resumed.assets / "b0003.mp4")
            self.assertAlmostEqual(resumed.total_spent, 0.25, places=4)
            resumed.clear_checkpoint()
            resumed.cleanup()


if __name__ == "__main__":
    unittest.main()
