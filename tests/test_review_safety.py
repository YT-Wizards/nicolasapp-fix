import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))
from providers import ProviderError, VercelGatewayClient
from vyt import Pipeline, QualityReviewPendingError, validate_review_response, review_score
from planning import rebalance_scenes_for_budget


def accepted(video=False):
    result = {"pass": True, "semantic_score": 90, "realism_score": 90, "integrity_score": 90}
    if video:
        result.update(watermark=False, motion_score=90, continuity_score=90)
    return result


class ReviewSchemaTests(unittest.TestCase):
    def test_missing_and_invalid_scores_cannot_be_approved(self):
        for value in (None, float("nan"), float("inf"), -1, 101, True, "invalid"):
            with self.subTest(value=value):
                response = accepted()
                response["integrity_score"] = value
                with self.assertRaises(ProviderError):
                    validate_review_response(response)
                self.assertEqual(review_score(response, "integrity_score", 75), 0)
        response = accepted()
        del response["integrity_score"]
        with self.assertRaises(ProviderError):
            validate_review_response(response)

    def test_video_requires_explicit_motion_continuity_and_watermark(self):
        for field in ("motion_score", "continuity_score", "watermark"):
            response = accepted(True)
            del response[field]
            with self.assertRaises(ProviderError):
                validate_review_response(response, is_video=True)
        self.assertEqual(validate_review_response(accepted(True), True), accepted(True))

    @patch("providers._json_request")
    def test_missing_input_image_never_strands_budget(self, http):
        reserve, release = Mock(), Mock()
        client = VercelGatewayClient("fake", "google/gemini-2.5-flash", reserve_callback=reserve, release_callback=release)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                client.chat_json("system", "prompt", images=[Path(directory) / "missing.png"])
        reserve.assert_not_called()
        release.assert_not_called()
        http.assert_not_called()


@patch.dict(os.environ, {"VYT_GATEWAY_KEY": "fake", "VYT_ALGROW_KEY": "fake", "VYT_GEMINIGEN_KEY": "fake"})
class ReviewRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "source.mp4"
        self.source.write_bytes(b"source")
        self.config = {"id": "review-test", "source": str(self.source), "root_dir": str(root), "user_data_dir": str(root / "userdata"), "max_cost_usd": 7, "branding": False}

    def pipeline(self):
        with patch.dict(os.environ, {"VYT_GATEWAY_KEY": "fake", "VYT_ALGROW_KEY": "fake", "VYT_GEMINIGEN_KEY": "fake"}):
            pipeline = Pipeline(self.config)
        pipeline.prepare_checkpoint(self.source, 8)
        self.addCleanup(pipeline.cleanup)
        pipeline.reviewer.chat_json = Mock(side_effect=ProviderError("offline"))
        pipeline.director.chat_json = Mock(side_effect=ProviderError("offline"))
        pipeline.geminigen.generate_video = Mock(side_effect=AssertionError("must not buy a clip"))
        pipeline.algrow.generate_image = Mock(side_effect=AssertionError("must not buy an image"))
        return pipeline

    @patch("vyt.probe", return_value={"duration": 8, "width": 1920, "height": 1080})
    def test_failed_review_keeps_paid_image_and_resume_only_reviews_it(self, _probe):
        first = self.pipeline()
        scene = {"id": "b001", "type": "image", "narration": "A person holds a phone."}
        output = first.assets / "b001.png"
        output.write_bytes(b"x" * 2000)
        first.save_pending_image_job(scene, "already-paid")
        with self.assertRaises(QualityReviewPendingError):
            first.cached_asset_for(scene)
        self.assertTrue(output.exists())
        self.assertEqual(first.checkpoint["asset_reviews"]["b001"]["status"], "pending")
        self.assertEqual(first.pending_image_job_for(scene), "")
        self.assertNotIn("b001", first.checkpoint.get("completed_assets", {}))
        second = self.pipeline()
        second.reviewer.chat_json = Mock(return_value=accepted())
        self.assertEqual(second.cached_asset_for(scene), output)
        self.assertEqual(second.checkpoint["asset_reviews"]["b001"]["status"], "passed")
        self.assertEqual(second.cached_asset_for(scene), output)
        second.reviewer.chat_json.assert_called_once()
        for pipeline in (first, second):
            pipeline.algrow.generate_image.assert_not_called()
            pipeline.geminigen.generate_video.assert_not_called()

    @patch("vyt.probe", return_value={"duration": 8, "width": 1920, "height": 1080})
    def test_orphan_with_anatomy_failure_is_not_approved(self, _probe):
        pipeline = self.pipeline()
        scene = {"id": "b002", "type": "image", "narration": "one person"}
        output = pipeline.assets / "b002.png"
        output.write_bytes(b"x" * 2000)
        pipeline.reviewer.chat_json = Mock(return_value={**accepted(), "integrity_score": 25})
        self.assertIsNone(pipeline.cached_asset_for(scene))
        self.assertFalse(output.exists())
        self.assertNotIn("b002", pipeline.checkpoint.get("completed_assets", {}))
        pipeline.algrow.generate_image.assert_not_called()

    @patch("vyt.extract_review_strip", side_effect=RuntimeError("ffmpeg failed"))
    def test_video_contact_sheet_failure_is_pending_not_passed(self, _strip):
        pipeline = self.pipeline()
        scene = {"id": "b003", "type": "video", "narration": "one person"}
        output = pipeline.assets / "b003.mp4"
        output.write_bytes(b"x" * 2000)
        with self.assertRaises(QualityReviewPendingError):
            pipeline.review_asset(scene, output)
        self.assertTrue(output.exists())
        self.assertEqual(pipeline.checkpoint["asset_reviews"]["b003"]["status"], "pending")

    def test_invalid_primary_response_uses_valid_fallback(self):
        pipeline = self.pipeline()
        pipeline.reviewer.chat_json = Mock(return_value={"pass": True})
        pipeline.director.chat_json = Mock(return_value=accepted())
        response = pipeline.review_image({"id": "b004", "narration": "one phone"}, self.source)
        self.assertEqual(response, accepted())
        pipeline.director.chat_json.assert_called_once()

    def test_review_outage_after_generation_does_not_buy_replacement(self):
        pipeline = self.pipeline()
        scene = {"id": "b005", "type": "image", "narration": "one phone", "literal_subject": "one phone"}
        def generate(_prompt, output, **_kwargs):
            Path(output).write_bytes(b"x" * 2000)
            return Path(output), "https://example.test/image.png"
        pipeline.algrow.generate_image = Mock(side_effect=generate)
        with self.assertRaises(QualityReviewPendingError):
            pipeline.generate_one(scene)
        pipeline.algrow.generate_image.assert_called_once()
        self.assertTrue((pipeline.assets / "b005.png").exists())
        pipeline.geminigen.generate_video.assert_not_called()

    @patch("vyt.probe", return_value={"duration": 8, "width": 1920, "height": 1080})
    def test_replaced_file_cannot_reuse_an_old_approval(self, _probe):
        pipeline = self.pipeline()
        scene = {"id": "b006", "type": "image", "narration": "one phone"}
        output = pipeline.assets / "b006.png"
        output.write_bytes(b"x" * 2000)
        pipeline.record_asset_review(scene, output, "passed")
        pipeline.mark_asset_completed(scene, output)
        output.write_bytes(b"y" * 3000)
        with self.assertRaises(QualityReviewPendingError):
            pipeline.cached_asset_for(scene)
        self.assertTrue(output.exists())

    @patch("vyt.probe", return_value={"duration": 8, "width": 1920, "height": 1080})
    def test_pending_local_media_survives_budget_rebalance(self, _probe):
        pipeline = self.pipeline()
        scene = {"id": "b007", "type": "avatar", "narration": "one phone"}
        output = pipeline.assets / "b007.png"
        output.write_bytes(b"x" * 2000)
        pipeline.record_asset_review(scene, output, "pending")
        with self.assertRaises(QualityReviewPendingError):
            pipeline.ensure_no_pending_paid_jobs()
        paid = pipeline.protect_local_paid_assets([scene], {})
        scenes, cost = rebalance_scenes_for_budget([scene], 0, already_paid_ids=paid)
        self.assertEqual(scenes[0]["type"], "image")
        self.assertEqual(cost, 0)
        pipeline.reviewer.chat_json = Mock(return_value=accepted())
        self.assertEqual(pipeline.cached_asset_for(scenes[0]), output)
        pipeline.ensure_no_pending_paid_jobs()
        pipeline.algrow.generate_image.assert_not_called()
        pipeline.geminigen.generate_video.assert_not_called()

    @patch("vyt.probe", return_value={"duration": 8, "width": 1920, "height": 1080})
    def test_recovered_video_rejection_uses_image_fallback(self, _probe):
        pipeline = self.pipeline()
        scene = {"id": "b008", "type": "video", "narration": "one phone"}
        output = pipeline.assets / "b008.mp4"
        output.write_bytes(b"x" * 2000)
        pipeline.review_video = Mock(return_value={**accepted(True), "integrity_score": 25})
        self.assertIsNone(pipeline.cached_asset_for(scene))
        self.assertEqual(scene["type"], "image")
        pipeline.geminigen.generate_video.assert_not_called()

    def test_checkpoint_write_failure_does_not_repurchase(self):
        for failed_status in ("pending", "passed"):
            with self.subTest(status=failed_status):
                pipeline = self.pipeline()
                scene = {"id": "b009", "type": "image", "narration": "one phone"}
                def generate(_prompt, output, **_kwargs):
                    Path(output).write_bytes(b"x" * 2000)
                    return Path(output), "https://example.test/image.png"
                pipeline.algrow.generate_image = Mock(side_effect=generate)
                pipeline.reviewer.chat_json = Mock(return_value=accepted())
                original = pipeline.save_checkpoint
                def fail_save(**values):
                    receipt = values.get("asset_reviews", {}).get("b009", {})
                    if receipt.get("status") == failed_status:
                        raise OSError("disk full")
                    return original(**values)
                pipeline.save_checkpoint = fail_save
                with self.assertRaises(QualityReviewPendingError):
                    pipeline.generate_one(scene)
                pipeline.algrow.generate_image.assert_called_once()
                self.assertTrue((pipeline.assets / "b009.png").exists())
                pipeline.save_checkpoint = original

    def test_video_reviewer_outage_keeps_clip(self):
        pipeline = self.pipeline()
        scene = {"id": "b010", "type": "video", "narration": "one phone"}
        output = pipeline.assets / "b010.mp4"
        output.write_bytes(b"x" * 2000)
        strip = pipeline.workspace / "strip.jpg"
        strip.write_bytes(b"x")
        with patch("vyt.extract_review_strip", return_value=strip):
            with self.assertRaises(QualityReviewPendingError):
                pipeline.review_asset(scene, output)
        self.assertTrue(output.exists())
        self.assertFalse(strip.exists())
        pipeline.reviewer.chat_json.assert_called_once()
        pipeline.director.chat_json.assert_called_once()


if __name__ == "__main__":
    unittest.main()
