import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from media import (
    assemble,
    extract_presenter_reference_frames,
    extract_review_strip,
    extract_split_review_crop,
    image_motion_filter,
    image_zoom_travel,
    image_zoom_expression,
    product_overlay_for_scene,
    product_qr_reminder_window,
    product_qr_window,
    render_segment,
    run,
    set_job_deadline,
)
from media import _merge_transcription_segments


class ImageMotionTests(unittest.TestCase):
    def test_transcription_merge_removes_overlap_duplicates_and_clamps_time(self):
        merged = _merge_transcription_segments([
            {"start": -0.5, "end": 2.0, "text": "The same sentence"},
            {"start": 1.2, "end": 2.6, "text": "The same sentence"},
            {"start": 2.4, "end": 4.0, "text": "A new factual detail"},
        ], 3.0)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["start"], 0.0)
        self.assertEqual(merged[-1]["end"], 3.0)
        self.assertLessEqual(merged[0]["end"], merged[1]["start"])

    def test_transcription_merge_rejects_empty_output(self):
        with self.assertRaisesRegex(RuntimeError, "narración"):
            _merge_transcription_segments([], 10.0)

    def test_legacy_job_deadline_does_not_shorten_an_explicit_operation_timeout(self):
        try:
            with patch("media.time.monotonic", return_value=500.0), patch("media.subprocess.run") as process:
                set_job_deadline(501.25)
                run(["fake-tool"], timeout=120)
            self.assertEqual(process.call_args.kwargs["timeout"], 120)
        finally:
            set_job_deadline(None)

    def test_local_process_still_starts_after_the_old_global_deadline(self):
        try:
            with patch("media.time.monotonic", return_value=700.0), patch("media.subprocess.run") as process:
                set_job_deadline(699.0)
                run(["fake-tool"], timeout=120)
            process.assert_called_once()
            self.assertEqual(process.call_args.kwargs["timeout"], 120)
        finally:
            set_job_deadline(None)

    def test_local_process_without_an_explicit_timeout_still_has_a_finite_limit(self):
        with patch("media.subprocess.run") as process:
            run(["fake-tool"])
        timeout = process.call_args.kwargs["timeout"]
        self.assertIsInstance(timeout, (int, float))
        self.assertGreater(timeout, 0)

    def test_video_review_uses_only_the_prefix_that_will_be_rendered(self):
        with patch("media.probe", return_value={"duration": 8.0}), patch("media.run") as mocked_run:
            extract_review_strip("/tmp/veo.mp4", Path("/tmp/review-prefix.jpg"), visible_duration=3.5)
        seek_positions = [
            float(command[command.index("-ss") + 1])
            for command in (call.args[0] for call in mocked_run.call_args_list[:3])
        ]
        self.assertLessEqual(max(seek_positions), 3.5)
        self.assertAlmostEqual(seek_positions[-1], 3.08, places=2)
        first_filter = mocked_run.call_args_list[0].args[0]
        self.assertIn("iw*0.92", first_filter[first_filter.index("-vf") + 1])

    def test_split_review_previews_the_actual_center_crop(self):
        with patch("media.run") as mocked_run:
            extract_split_review_crop("/tmp/asset.png", Path("/tmp/split-preview.jpg"))
        command = mocked_run.call_args.args[0]
        crop_filter = command[command.index("-vf") + 1]
        self.assertIn("scale=960:1080", crop_filter)
        self.assertIn("crop=960:1080", crop_filter)

    def test_presenter_references_are_three_clean_crops_from_different_moments(self):
        with patch("media.run") as mocked_run:
            paths = extract_presenter_reference_frames(
                "/tmp/host.mp4", 100.0, Path("/tmp/vyt-presenter-test")
            )
        self.assertEqual(len(paths), 3)
        commands = [call.args[0] for call in mocked_run.call_args_list]
        self.assertEqual([command[command.index("-ss") + 1] for command in commands], ["17.000", "47.000", "77.000"])
        self.assertTrue(all("crop=" in command[command.index("-vf") + 1] for command in commands))

    def test_images_alternate_between_zoom_in_and_zoom_out(self):
        zoom_in = image_zoom_expression({"id": "b0002"}, 5.0, 30)
        zoom_out = image_zoom_expression({"id": "b0003"}, 5.0, 30)
        self.assertIn("1.0+on*", zoom_in)
        self.assertIn("-on*", zoom_out)

    def test_zoom_keeps_a_gentle_near_constant_speed_across_durations(self):
        short = image_zoom_expression({"id": "b0002"}, 4.0, 30)
        long = image_zoom_expression({"id": "b0002"}, 8.0, 30)
        short_step = float(short.split("on*")[1].split(",")[0])
        long_step = float(long.split("on*")[1].split(",")[0])
        self.assertGreater(short_step, long_step)
        self.assertAlmostEqual(image_zoom_travel(4.0) / 4.0, image_zoom_travel(5.0) / 5.0, places=5)
        self.assertLess(image_zoom_travel(4.0), 0.085)
        self.assertLessEqual(image_zoom_travel(8.0), 0.065)

    def test_image_motion_uses_four_x_workspace_and_one_continuous_sequence(self):
        motion = image_motion_filter({"id": "b0002"}, 5.0, 30, 1920, 1080)
        self.assertIn("scale=7680:4320", motion)
        self.assertIn("flags=lanczos", motion)
        self.assertIn("d=150", motion)
        self.assertIn("s=1920x1080", motion)
        self.assertNotIn("random", motion.lower())
        self.assertNotIn("sin(", motion.lower())
        self.assertNotIn("cos(", motion.lower())

    def test_split_image_motion_keeps_the_same_four_x_precision(self):
        motion = image_motion_filter({"id": "b0003"}, 5.0, 30, 960, 1080)
        self.assertIn("scale=3840:4320", motion)
        self.assertIn("d=150", motion)
        self.assertIn("s=960x1080", motion)

    def test_split_always_places_the_real_presenter_on_the_left(self):
        scene = {"id": "b0003", "start": 0.0, "duration": 4.0, "type": "split"}
        with patch("media.run") as mocked_run, patch("media.probe", return_value={"duration": 4.0}):
            render_segment(scene, "/tmp/host.mp4", "/tmp/still.png", "/tmp/split-out.mp4")
        command = mocked_run.call_args.args[0]
        graph = command[command.index("-filter_complex") + 1]
        self.assertIn("[avatar][visual]hstack", graph)
        self.assertNotIn("[visual][avatar]hstack", graph)

    def test_still_image_is_not_looped_and_rescaled_again_every_frame(self):
        scene = {"id": "b0002", "start": 0.0, "duration": 5.0, "type": "image"}
        with patch("media.run") as mocked_run, patch("media.probe", return_value={"duration": 5.0}):
            render_segment(scene, "/tmp/host.mp4", "/tmp/still.png", "/tmp/still-out.mp4")
        command = mocked_run.call_args.args[0]
        self.assertNotIn("-loop", command)
        self.assertEqual(command[command.index("-frames:v") + 1], "150")
        visual_filter = command[command.index("-vf") + 1]
        self.assertIn("scale=7680:4320", visual_filter)
        self.assertIn("d=150", visual_filter)

    def test_veo_action_is_never_restarted_when_a_file_is_short(self):
        scene = {"id": "b0004", "start": 0.0, "duration": 7.9, "type": "video"}
        with patch("media.run") as mocked_run, patch("media.probe", return_value={"duration": 7.9}):
            render_segment(scene, "/tmp/host.mp4", "/tmp/veo.mp4", "/tmp/video-out.mp4")
        command = mocked_run.call_args.args[0]
        self.assertNotIn("-stream_loop", command)
        video_filter = command[command.index("-vf") + 1]
        self.assertIn("tpad=stop_mode=clone", video_filter)
        self.assertEqual(command[command.index("-t") + 1], "7.900")

    def test_final_video_can_never_overwrite_the_heygen_source(self):
        source = Path("/tmp/vyt-source-collision.mp4")
        with self.assertRaisesRegex(RuntimeError, "no puede sobrescribir"):
            assemble([], source, source, 10.0, Path("/tmp"))

    def test_product_qr_prefers_the_last_named_mention(self):
        transcript = [
            {"start": 12.0, "end": 15.0, "text": "Today we introduce La Casa Limpia."},
            {"start": 82.0, "end": 86.0, "text": "Get La Casa Limpia using the link below."},
        ]
        window = product_qr_window(transcript, 100.0, "La Casa Limpia")
        self.assertAlmostEqual(window["start"], 81.9, places=2)
        self.assertAlmostEqual(window["end"], 86.1, places=2)

    def test_product_qr_does_not_invent_a_sales_moment(self):
        window = product_qr_window([], 600.0, "Producto")
        self.assertIsNone(window)

    def test_product_qr_is_split_across_scene_boundaries(self):
        window = {"start": 8.0, "end": 14.0}
        first = product_overlay_for_scene({"start": 5.0, "end": 10.0}, window)
        second = product_overlay_for_scene({"start": 10.0, "end": 15.0}, window)
        self.assertEqual(first, {"start": 3.0, "end": 5.0})
        self.assertEqual(second, {"start": 0.0, "end": 4.0})

    def test_product_qr_matches_the_real_cta_segment_not_six_fixed_seconds(self):
        transcript = [{"start": 20.0, "end": 27.4, "text": "Scan the QR code and order the guide today."}]
        window = product_qr_window(transcript, 90.0, "Guide")
        self.assertAlmostEqual(window["start"], 19.9)
        self.assertAlmostEqual(window["end"], 27.5)

    def test_product_qr_reminder_uses_one_existing_later_avatar(self):
        items = [
            {"start": 20.0, "end": 25.0, "type": "avatar"},
            {"start": 60.0, "end": 64.0, "type": "image"},
            {"start": 68.0, "end": 73.0, "type": "avatar"},
            {"start": 84.0, "end": 89.0, "type": "avatar"},
        ]
        reminder = product_qr_reminder_window(items, {"start": 20.0, "end": 25.0}, 100.0)
        self.assertEqual(reminder, {"start": 68.0, "end": 73.0, "kind": "reminder"})


if __name__ == "__main__":
    unittest.main()
