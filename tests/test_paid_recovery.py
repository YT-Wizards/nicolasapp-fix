import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

import providers
from providers import (
    AlgrowClient, GeminiGenClient, PaidAssetRecoveryError, ProviderError,
    RegeneratableError, requests,
)
from vyt import Pipeline


API_ENV = {
    "VYT_GATEWAY_KEY": "gateway",
    "VYT_ALGROW_KEY": "algrow",
    "VYT_GEMINIGEN_KEY": "geminigen",
}


class _HistoryResponse:
    ok = True
    status_code = 200

    def __init__(self, video_url):
        self.video_url = video_url

    def json(self):
        return {
            "data": {
                "status": 2,
                "generated_video": [{
                    "status": 2,
                    "has_watermark": 0,
                    "video_url": self.video_url,
                }],
            }
        }


class _MediaResponse:
    def __init__(self, status_code=200, chunks=()):
        self.status_code = status_code
        self._chunks = list(chunks)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=None):
        del chunk_size
        yield from self._chunks

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _PinnedResponse:
    def __init__(self, status=200, chunks=()):
        self.status = status
        self._chunks = list(chunks)
        self._data = b"".join(self._chunks)
        self._offset = 0
        self.released = False
        self.closed = False

    def stream(self, amt=None, decode_content=False):
        del amt, decode_content
        yield from self._chunks

    def read(self, amt=None, decode_content=False):
        del decode_content
        if amt is None:
            result = self._data[self._offset:]
            self._offset = len(self._data)
            return result
        result = self._data[self._offset:self._offset + amt]
        self._offset += len(result)
        return result

    def release_conn(self):
        self.released = True

    def close(self):
        self.closed = True


class _PinnedPool:
    def __init__(self, ip, response=None, error=None):
        self.ip = ip
        self.response = response
        self.error = error
        self.requests = []
        self.closed = False

    def request(self, method, target, **kwargs):
        self.requests.append((method, target, kwargs))
        if self.error:
            raise self.error
        return self.response

    def urlopen(self, method, target, **kwargs):
        return self.request(method, target, **kwargs)

    def close(self):
        self.closed = True


class PinnedR2DownloadTests(unittest.TestCase):
    R2_HOST = "7a4964de26acd06ff740870066a92ff8.r2.cloudflarestorage.com"

    def signed_url(self):
        return (
            f"https://{self.R2_HOST}/bucket/video%20one.mp4"
            "?response-content-type=application%2Foctet-stream"
            "&X-Amz-Credential=private-credential%2Fscope"
            "&X-Amz-Signature=super-secret-signature"
        )

    @patch("providers.urllib3.HTTPSConnectionPool")
    def test_pinned_r2_preserves_sni_host_and_exact_signed_path_query(self, pool_class):
        payload = b"\x00\x00\x00\x18ftypisom" + b"x" * 1200
        response = _PinnedResponse(status=200, chunks=[payload])
        pool = _PinnedPool("104.26.4.45", response=response)
        pool_class.return_value = pool

        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "clip.mp4.part"
            providers._download_r2_via_snapgen_edge(
                self.signed_url(), partial, time.monotonic() + 20,
                edge_ips=["104.26.4.45"],
            )
            self.assertEqual(partial.read_bytes(), payload)

        args, kwargs = pool_class.call_args
        connected_ip = args[0] if args else kwargs.get("host")
        self.assertEqual(connected_ip, "104.26.4.45")
        self.assertEqual(kwargs.get("server_hostname"), self.R2_HOST)
        self.assertEqual(kwargs.get("assert_hostname"), self.R2_HOST)
        self.assertNotIn(kwargs.get("cert_reqs"), (False, "CERT_NONE", 0))
        method, target, request_kwargs = pool.requests[0]
        self.assertEqual(method, "GET")
        self.assertEqual(
            target,
            "/bucket/video%20one.mp4?response-content-type=application%2Foctet-stream"
            "&X-Amz-Credential=private-credential%2Fscope"
            "&X-Amz-Signature=super-secret-signature",
        )
        self.assertEqual(request_kwargs["headers"]["Host"], self.R2_HOST)
        self.assertTrue(response.released or response.closed)
        self.assertTrue(pool.closed)

    @patch("providers.urllib3.HTTPSConnectionPool")
    def test_pinned_r2_range_206_appends_existing_partial(self, pool_class):
        original = b"\x00\x00\x00\x18ftypisom" + b"a" * 100
        remainder = b"b" * 200
        response = _PinnedResponse(status=206, chunks=[remainder])
        pool = _PinnedPool("104.26.4.45", response=response)
        pool_class.return_value = pool

        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "clip.mp4.part"
            partial.write_bytes(original)
            providers._download_r2_via_snapgen_edge(
                self.signed_url(), partial, time.monotonic() + 20,
                edge_ips=["104.26.4.45"],
            )
            self.assertEqual(partial.read_bytes(), original + remainder)

        headers = pool.requests[0][2]["headers"]
        self.assertEqual(headers["Range"], f"bytes={len(original)}-")

    @patch("providers.urllib3.HTTPSConnectionPool")
    def test_pinned_r2_range_200_restarts_instead_of_corrupting_partial(self, pool_class):
        old_partial = b"\x00\x00\x00\x18ftypisom" + b"old" * 100
        complete_replacement = b"\x00\x00\x00\x18ftypisom" + b"new" * 500
        response = _PinnedResponse(status=200, chunks=[complete_replacement])
        pool = _PinnedPool("104.26.4.45", response=response)
        pool_class.return_value = pool

        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "clip.mp4.part"
            partial.write_bytes(old_partial)
            providers._download_r2_via_snapgen_edge(
                self.signed_url(), partial, time.monotonic() + 20,
                edge_ips=["104.26.4.45"],
            )
            self.assertEqual(partial.read_bytes(), complete_replacement)

        headers = pool.requests[0][2]["headers"]
        self.assertEqual(headers["Range"], f"bytes={len(old_partial)}-")

    @patch("providers.socket.getaddrinfo")
    @patch("providers.urllib3.HTTPSConnectionPool")
    def test_pinned_r2_uses_snapgen_ipv4_edges_and_fails_over(self, pool_class, getaddrinfo):
        first_ip = "104.26.4.45"
        second_ip = "172.67.72.52"
        getaddrinfo.return_value = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (first_ip, 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (second_ip, 443)),
        ]
        pools = {
            first_ip: _PinnedPool(
                first_ip,
                error=providers.urllib3.exceptions.ConnectTimeoutError("edge timed out"),
            ),
            second_ip: _PinnedPool(
                second_ip,
                response=_PinnedResponse(status=200, chunks=[
                    b"\x00\x00\x00\x18ftypisom" + b"x" * 1200,
                ]),
            ),
        }

        def make_pool(*args, **kwargs):
            ip = args[0] if args else kwargs["host"]
            return pools[ip]

        pool_class.side_effect = make_pool
        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "clip.mp4.part"
            providers._download_r2_via_snapgen_edge(
                self.signed_url(), partial, time.monotonic() + 20,
            )
            self.assertGreater(partial.stat().st_size, 1000)

        resolver_args = getaddrinfo.call_args.args
        resolver_kwargs = getaddrinfo.call_args.kwargs
        self.assertEqual(resolver_args[0], "api.snapgen.ai")
        self.assertEqual(resolver_args[1], 443)
        family = resolver_args[2] if len(resolver_args) > 2 else resolver_kwargs.get("family")
        self.assertEqual(family, socket.AF_INET)
        self.assertEqual(pool_class.call_count, 2)
        self.assertTrue(pools[first_ip].closed)
        self.assertTrue(pools[second_ip].closed)

    @patch("providers.urllib3.HTTPSConnectionPool")
    def test_pinned_r2_rejects_foreign_hosts_before_connecting(self, pool_class):
        secret_url = (
            "https://evil.example.test/video.mp4?"
            "X-Amz-Credential=private-credential&X-Amz-Signature=secret"
        )
        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "clip.mp4.part"
            with self.assertRaises(ProviderError) as raised:
                providers._download_r2_via_snapgen_edge(
                    secret_url, partial, time.monotonic() + 20,
                    edge_ips=["104.26.4.45"],
                )

        pool_class.assert_not_called()
        message = str(raised.exception)
        self.assertNotIn(secret_url, message)
        self.assertNotIn("private-credential", message)
        self.assertNotIn("X-Amz-", message)

    @patch("providers.urllib3.HTTPSConnectionPool")
    def test_pinned_r2_failure_redacts_the_entire_signed_url(self, pool_class):
        pool_class.side_effect = providers.urllib3.exceptions.ConnectTimeoutError(
            f"timeout while fetching {self.signed_url()}"
        )
        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "clip.mp4.part"
            with self.assertRaises(ProviderError) as raised:
                providers._download_r2_via_snapgen_edge(
                    self.signed_url(), partial, time.monotonic() + 20,
                    edge_ips=["104.26.4.45", "172.67.72.52"],
                )

        message = str(raised.exception)
        self.assertNotIn(self.signed_url(), message)
        self.assertNotIn("private-credential", message)
        self.assertNotIn("super-secret-signature", message)
        self.assertNotIn("X-Amz-", message)

    @patch("providers._download_r2_via_snapgen_edge")
    @patch("providers.time.sleep", return_value=None)
    @patch("providers.requests.get")
    @patch("providers.requests.post")
    def test_connect_timeout_uses_pinned_fallback_without_second_post_or_cost(
        self, post, get, _sleep, pinned_download,
    ):
        signed_url = self.signed_url()
        valid_mp4 = b"\x00\x00\x00\x18ftypisom" + b"x" * 1200

        class CreatedResponse:
            ok = True
            status_code = 200

            def json(self):
                return {"data": {"uuid": "new-paid-uuid"}}

        def request(url, **_kwargs):
            if "/history/" in url:
                return _HistoryResponse(signed_url)
            raise requests.ConnectTimeout("direct R2 route timed out")

        def recover(_url, partial, _deadline, edge_ips=None):
            del edge_ips
            Path(partial).write_bytes(valid_mp4)
            return Path(partial)

        post.return_value = CreatedResponse()
        get.side_effect = request
        pinned_download.side_effect = recover
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "clip.mp4"
            client = GeminiGenClient("test-key")
            result = client.generate_video("one literal shot", output, timeout=30)
            self.assertEqual(result, output)
            self.assertEqual(output.read_bytes(), valid_mp4)

        self.assertEqual(post.call_count, 1)
        self.assertAlmostEqual(client.spent_usd, client.ESTIMATED_CLIP_USD, places=6)
        pinned_download.assert_called_once()
        self.assertEqual(pinned_download.call_args.args[0], signed_url)


class PaidAssetProviderRecoveryTests(unittest.TestCase):
    @patch("providers.time.sleep", return_value=None)
    @patch("providers.time.monotonic")
    @patch("providers._json_request")
    def test_resumed_algrow_job_not_found_is_terminal_without_post_or_cost(
        self, json_request, monotonic, _sleep,
    ):
        ticks = iter((100.0, 100.1, 100.2, 106.0, 106.0))
        monotonic.side_effect = lambda: next(ticks, 106.0)
        json_request.side_effect = ProviderError('HTTP 404: {"error":"Job not found"}')
        on_created = Mock()
        client = AlgrowClient("test-key")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RegeneratableError) as raised:
                client.generate_image(
                    "one literal still", Path(directory) / "image.png",
                    timeout=5, resume_job_id="missing-paid-image-job",
                    on_created=on_created,
                )

        self.assertNotIsInstance(raised.exception, PaidAssetRecoveryError)
        self.assertIn("Job not found", str(raised.exception))
        self.assertEqual(client.spent_usd, 0.0)
        on_created.assert_not_called()
        self.assertEqual(json_request.call_count, 1)
        request = json_request.call_args
        self.assertIn("/job-status/missing-paid-image-job", request.args[0])
        self.assertNotEqual(request.kwargs.get("method", "GET"), "POST")

    @patch("providers.time.sleep", return_value=None)
    @patch("providers.time.monotonic")
    @patch("providers._json_request")
    def test_resumed_algrow_job_timeout_is_paid_recovery_and_keeps_safe_reason(
        self, json_request, monotonic, _sleep,
    ):
        ticks = iter((100.0, 100.1, 100.2, 106.0, 106.0))
        monotonic.side_effect = lambda: next(ticks, 106.0)
        safe_reason = "Algrow status HTTP 503 while resuming the paid image"
        json_request.side_effect = ProviderError(safe_reason)
        client = AlgrowClient("test-key")

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PaidAssetRecoveryError) as raised:
                client.generate_image(
                    "one literal still", Path(directory) / "image.png",
                    timeout=5, resume_job_id="paid-image-job",
                )

        self.assertIn("Algrow", str(raised.exception))
        self.assertIn("HTTP 503", str(raised.exception))
        self.assertTrue(json_request.call_args_list)
        self.assertTrue(all("/job-status/" in call.args[0] for call in json_request.call_args_list))
        self.assertEqual(client.spent_usd, 0.0)

    @patch("providers.time.sleep", return_value=None)
    @patch("providers.requests.get")
    @patch("providers.requests.post")
    def test_r2_failure_refreshes_history_and_resumes_same_uuid_without_purchase(
        self, post, get, _sleep,
    ):
        expired_url = "https://r2.example.test/expired.mp4"
        fresh_url = "https://r2.example.test/fresh.mp4"
        history_calls = []
        media_calls = []
        valid_mp4 = b"\x00\x00\x00\x18ftypisom" + b"x" * 1200

        def request(url, **_kwargs):
            if "/history/" in url:
                history_calls.append(url)
                return _HistoryResponse(expired_url if len(history_calls) == 1 else fresh_url)
            media_calls.append(url)
            if url == expired_url:
                return _MediaResponse(status_code=403)
            return _MediaResponse(status_code=200, chunks=[valid_mp4])

        get.side_effect = request
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "clip.mp4"
            client = GeminiGenClient("test-key")
            result = client.generate_video(
                "one literal shot", output, timeout=30, resume_uuid="paid-uuid",
            )

            self.assertEqual(result, output)
            self.assertEqual(output.read_bytes(), valid_mp4)
            self.assertFalse(output.with_suffix(".mp4.part").exists())

        post.assert_not_called()
        self.assertEqual(client.spent_usd, 0.0)
        self.assertGreaterEqual(len(history_calls), 2)
        self.assertEqual(media_calls[:2], [expired_url, fresh_url])

    @patch("providers.time.sleep", return_value=None)
    @patch("providers.time.monotonic")
    @patch("providers.requests.get")
    @patch("providers.requests.post")
    def test_exhausted_r2_delivery_raises_paid_recovery_error_without_purchase(
        self, post, get, monotonic, _sleep,
    ):
        ticks = iter((100.0, 100.1, 100.2, 100.3, 100.4, 106.0))
        monotonic.side_effect = lambda: next(ticks, 106.0)
        broken_url = "https://r2.example.test/still-broken.mp4"

        def request(url, **_kwargs):
            if "/history/" in url:
                return _HistoryResponse(broken_url)
            raise requests.Timeout("R2 unavailable")

        get.side_effect = request
        client = GeminiGenClient("test-key")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PaidAssetRecoveryError):
                client.generate_video(
                    "one literal shot", Path(directory) / "clip.mp4",
                    timeout=5, resume_uuid="paid-uuid",
                )

        post.assert_not_called()
        self.assertEqual(client.spent_usd, 0.0)

    @patch("providers.time.sleep", return_value=None)
    @patch("providers.time.monotonic")
    @patch("providers.requests.get")
    @patch("providers.requests.post")
    def test_paid_delivery_error_never_exposes_the_signed_r2_url(
        self, post, get, monotonic, _sleep,
    ):
        ticks = iter((100.0, 100.1, 100.2, 100.3, 100.4, 106.0))
        monotonic.side_effect = lambda: next(ticks, 106.0)
        signed_url = (
            "https://r2.example.test/paid.mp4?X-Amz-Credential=private-credential"
            "&X-Amz-Signature=super-secret-signature&X-Amz-Date=20260829T120000Z"
        )

        def request(url, **_kwargs):
            if "/history/" in url:
                return _HistoryResponse(signed_url)
            raise requests.ConnectTimeout(f"connection timed out for {url}")

        get.side_effect = request
        client = GeminiGenClient("test-key")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PaidAssetRecoveryError) as raised:
                client.generate_video(
                    "one literal shot", Path(directory) / "clip.mp4",
                    timeout=5, resume_uuid="paid-uuid",
                )

        message = str(raised.exception)
        post.assert_not_called()
        self.assertEqual(client.spent_usd, 0.0)
        self.assertNotIn(signed_url, message)
        self.assertNotIn("private-credential", message)
        self.assertNotIn("super-secret-signature", message)
        self.assertNotIn("X-Amz-", message)


class PaidAssetPipelineRecoveryTests(unittest.TestCase):
    def config(self, root, source, max_cost=7.0):
        return {
            "id": "paid-recovery-test",
            "source": str(source),
            "output": str(root / "final.mp4"),
            "root_dir": str(root),
            "user_data_dir": str(root / "userdata"),
            "max_cost_usd": max_cost,
            "branding": False,
        }

    @patch.dict(os.environ, API_ENV)
    def test_qr_checkpoint_identity_ignores_card_path_mtime_and_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            first_card = root / "first-job-card.png"
            second_card = root / "different-job-card.png"
            first_card.write_bytes(b"a" * 1200)
            second_card.write_bytes(b"different-content" * 100)

            first_config = self.config(root, source)
            first_config["product_sale"] = {
                "enabled": True,
                "card_path": str(first_card),
            }
            first = Pipeline(first_config)
            first.prepare_checkpoint(source, 8.0)
            first.save_checkpoint(resume_marker="same-enabled-qr-job")
            enabled_checkpoint = first.checkpoint_path
            first.cleanup()

            second_config = self.config(root, source)
            second_config["product_sale"] = {
                "enabled": True,
                "card_path": str(second_card),
            }
            second = Pipeline(second_config)
            second.prepare_checkpoint(source, 8.0)
            self.assertEqual(second.checkpoint_path, enabled_checkpoint)
            self.assertEqual(second.checkpoint.get("resume_marker"), "same-enabled-qr-job")
            second.cleanup()

            disabled_config = self.config(root, source)
            disabled_config["product_sale"] = {"enabled": False}
            disabled = Pipeline(disabled_config)
            disabled.prepare_checkpoint(source, 8.0)
            self.assertNotEqual(disabled.checkpoint_path, enabled_checkpoint)
            disabled.clear_checkpoint()
            disabled.cleanup()

            # Remove the enabled checkpoint only after both identities were checked.
            second.clear_checkpoint()

    @patch.dict(os.environ, API_ENV)
    def test_paid_delivery_error_does_not_buy_image_mutate_scene_or_clear_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            pipeline.prepare_checkpoint(source, 8.0)
            scene = {
                "id": "b0007", "type": "video", "requested_type": "video",
                "start": 0.0, "end": 8.0, "duration": 8.0,
                "narration": "one person answers one phone",
                "literal_subject": "one person with one phone",
                "video_prompt": "one person answers one phone",
            }
            pipeline.save_pending_video_job(scene, "paid-uuid")
            image_calls = []

            def unavailable_video(*_args, **_kwargs):
                raise PaidAssetRecoveryError("R2 delivery unavailable")

            def forbidden_image(*args, **_kwargs):
                image_calls.append(args)
                return root / "must-not-exist.png"

            pipeline.geminigen.generate_video = unavailable_video
            pipeline.generate_image_scene = forbidden_image
            with self.assertRaises(PaidAssetRecoveryError):
                pipeline.generate_one(scene)

            self.assertEqual(scene["type"], "video")
            self.assertEqual(pipeline.pending_video_job_for(scene), "paid-uuid")
            self.assertEqual(image_calls, [])
            self.assertNotIn(scene["id"], pipeline.checkpoint.get("completed_assets", {}))
            pipeline.clear_checkpoint()
            pipeline.cleanup()

    @patch.dict(os.environ, API_ENV)
    def test_pending_paid_jobs_block_checkpoint_clearance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            pipeline = Pipeline(self.config(root, source))
            pipeline.prepare_checkpoint(source, 8.0)
            scene = {"id": "b0008", "type": "video"}
            pipeline.save_pending_video_job(scene, "paid-uuid")

            with self.assertRaises(PaidAssetRecoveryError):
                pipeline.ensure_no_pending_paid_jobs()
            self.assertTrue(pipeline.checkpoint_path.exists())
            self.assertEqual(pipeline.pending_video_job_for(scene), "paid-uuid")

            pipeline.clear_pending_video_job(scene)
            pipeline.ensure_no_pending_paid_jobs()
            pipeline.clear_checkpoint()
            pipeline.cleanup()

    def _prime_run_checkpoint(self, pipeline, source, scenes, duration):
        transcript = [{"start": 0.0, "end": duration, "text": "literal narration"}]
        bible = {"presenter_profile": {}, "continuity": []}
        beats = [
            {
                "id": scene["id"], "type": scene["type"],
                "start": scene["start"], "end": scene["end"],
                "duration": scene["duration"], "narration": scene["narration"],
            }
            for scene in scenes
        ]
        pipeline.prepare_checkpoint(source, duration)
        pipeline.save_checkpoint(
            transcript=transcript,
            bible=bible,
            beats=beats,
            estimated_media=0.04,
            planned_scenes=[dict(scene) for scene in scenes],
            reviewed_scenes=scenes,
        )

    def _run_patches(self, duration):
        def fake_probe(_path):
            return {"duration": duration, "width": 1920, "height": 1080}

        def fake_assemble(_segments, _source, output, _duration, _workspace):
            output = Path(output)
            output.write_bytes(b"final")
            return output

        return (
            patch("vyt.probe", side_effect=fake_probe),
            patch("vyt.extract_presenter_reference_frames", return_value=[]),
            patch("vyt.rebalance_scenes_for_budget", side_effect=lambda scenes, *_args, **_kwargs: (scenes, 0.04)),
            patch("vyt.enforce_presenter_broll", side_effect=lambda scenes, _bible: scenes),
            patch("vyt.stratified_generation_order", side_effect=lambda scenes, **_kwargs: list(scenes)),
            patch("vyt.render_segment", return_value=None),
            patch("vyt.assemble", side_effect=fake_assemble),
        )

    @patch.dict(os.environ, API_ENV)
    def test_paid_recovery_error_crosses_the_video_gate_and_preserves_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            duration = 16.0
            scenes = [
                {
                    "id": "b0000", "type": "avatar", "requested_type": "avatar",
                    "start": 0.0, "end": 8.0, "duration": 8.0,
                    "narration": "presenter opens the story", "literal_subject": "presenter",
                },
                {
                    "id": "b0001", "type": "video", "requested_type": "video",
                    "start": 8.0, "end": 16.0, "duration": 8.0,
                    "narration": "one person answers one phone", "literal_subject": "one person",
                    "video_prompt": "one person answers one phone",
                },
            ]
            pipeline = Pipeline(self.config(root, source))
            self._prime_run_checkpoint(pipeline, source, scenes, duration)
            pipeline.geminigen.ensure_available = Mock(return_value={"status": "Operational"})
            pipeline.event = Mock()
            attempted = []

            def paid_failure(scene):
                attempted.append(scene)
                pipeline.save_pending_video_job(scene, "paid-gate-uuid")
                raise PaidAssetRecoveryError("R2 delivery unavailable")

            pipeline.generate_one = paid_failure
            pipeline.clear_checkpoint = Mock()
            contexts = self._run_patches(duration)
            with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], contexts[6]:
                with self.assertRaises(PaidAssetRecoveryError):
                    pipeline.run()

            self.assertEqual(len(attempted), 1)
            self.assertEqual(attempted[0]["type"], "video")
            self.assertEqual(pipeline.pending_video_job_for(scenes[1]), "paid-gate-uuid")
            pipeline.clear_checkpoint.assert_not_called()
            pipeline.cleanup()

    @patch.dict(os.environ, API_ENV)
    def test_paid_recovery_error_crosses_worker_and_cannot_finish_successfully(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            duration = 24.0
            scenes = [
                {
                    "id": "b0000", "type": "avatar", "requested_type": "avatar",
                    "start": 0.0, "end": 8.0, "duration": 8.0,
                    "narration": "presenter opens the story", "literal_subject": "presenter",
                },
                {
                    "id": "b0001", "type": "video", "requested_type": "video",
                    "start": 8.0, "end": 16.0, "duration": 8.0,
                    "narration": "one ordinary action", "literal_subject": "one person",
                    "video_prompt": "one person performs one ordinary action",
                },
                {
                    "id": "b0002", "type": "video", "requested_type": "video",
                    "start": 16.0, "end": 24.0, "duration": 8.0,
                    "narration": "one person answers one phone", "literal_subject": "one person",
                    "video_prompt": "one person answers one phone",
                },
            ]
            pipeline = Pipeline(self.config(root, source))
            self._prime_run_checkpoint(pipeline, source, scenes, duration)
            pipeline.geminigen.ensure_available = Mock(return_value={"status": "Operational"})
            pipeline.event = Mock()
            attempted = {}

            def generated_or_pending(scene):
                attempted[scene["id"]] = scene
                if scene["id"] == "b0001":
                    asset = pipeline.assets / "b0001.mp4"
                    asset.write_bytes(b"x" * 2000)
                    return asset
                pipeline.save_pending_video_job(scene, "paid-worker-uuid")
                raise PaidAssetRecoveryError("R2 delivery unavailable")

            pipeline.generate_one = generated_or_pending
            pipeline.clear_checkpoint = Mock()
            contexts = self._run_patches(duration)
            with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], contexts[6]:
                with self.assertRaises(PaidAssetRecoveryError):
                    pipeline.run()

            self.assertEqual(attempted["b0002"]["type"], "video")
            self.assertEqual(pipeline.pending_video_job_for(scenes[2]), "paid-worker-uuid")
            pipeline.clear_checkpoint.assert_not_called()
            pipeline.cleanup()

    @patch.dict(os.environ, API_ENV)
    def test_resume_recovers_every_pending_paid_job_before_any_new_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            duration = 40.0
            scenes = [
                {
                    "id": "b0000", "type": "avatar", "requested_type": "avatar",
                    "start": 0.0, "end": 8.0, "duration": 8.0,
                    "narration": "presenter opens the story", "literal_subject": "presenter",
                },
                {
                    "id": "b0001", "type": "video", "requested_type": "video",
                    "start": 8.0, "end": 16.0, "duration": 8.0,
                    "narration": "a new motion beat", "literal_subject": "one person",
                    "video_prompt": "one person performs one ordinary action",
                },
                {
                    "id": "b0002", "type": "image", "requested_type": "image",
                    "start": 16.0, "end": 24.0, "duration": 8.0,
                    "narration": "a paid still awaits delivery", "literal_subject": "one phone",
                    "image_prompt": "one ordinary phone on a table",
                },
                {
                    "id": "b0003", "type": "video", "requested_type": "video",
                    "start": 24.0, "end": 32.0, "duration": 8.0,
                    "narration": "a paid clip awaits delivery", "literal_subject": "one person",
                    "video_prompt": "one person answers one phone",
                },
                {
                    "id": "b0004", "type": "image", "requested_type": "image",
                    "start": 32.0, "end": 40.0, "duration": 8.0,
                    "narration": "a new still beat", "literal_subject": "one kitchen",
                    "image_prompt": "one ordinary kitchen counter",
                },
            ]
            pipeline = Pipeline(self.config(root, source))
            self._prime_run_checkpoint(pipeline, source, scenes, duration)
            pipeline.save_pending_image_job(scenes[2], "paid-image-job")
            pipeline.save_pending_video_job(scenes[3], "paid-video-uuid")
            pipeline.geminigen.ensure_available = Mock(return_value={"status": "Operational"})
            pipeline.event = Mock()
            call_order = []

            def complete_asset(scene):
                call_order.append(scene["id"])
                suffix = ".mp4" if scene["type"] == "video" else ".png"
                asset = pipeline.assets / f"{scene['id']}{suffix}"
                asset.write_bytes(b"x" * 2000)
                if scene["id"] == "b0002":
                    pipeline.clear_pending_image_job(scene)
                elif scene["id"] == "b0003":
                    pipeline.clear_pending_video_job(scene)
                return asset

            pipeline.generate_one = complete_asset
            contexts = self._run_patches(duration)
            with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], contexts[6]:
                result = pipeline.run()

            self.assertTrue(result["ok"])
            self.assertEqual(set(call_order[:2]), {"b0002", "b0003"})
            self.assertNotIn("b0001", call_order[:2])
            self.assertNotIn("b0004", call_order[:2])
            self.assertEqual(call_order.count("b0002"), 1)
            self.assertEqual(call_order.count("b0003"), 1)
            pipeline.cleanup()

    @patch.dict(os.environ, API_ENV)
    @patch("providers.requests.post")
    def test_pending_paid_failure_blocks_all_new_posts_and_new_spending(self, post):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            duration = 40.0
            scenes = [
                {
                    "id": "b0000", "type": "avatar", "requested_type": "avatar",
                    "start": 0.0, "end": 8.0, "duration": 8.0,
                    "narration": "presenter opens the story", "literal_subject": "presenter",
                },
                {
                    "id": "b0001", "type": "video", "requested_type": "video",
                    "start": 8.0, "end": 16.0, "duration": 8.0,
                    "narration": "a new motion beat", "literal_subject": "one person",
                    "video_prompt": "one person performs one ordinary action",
                },
                {
                    "id": "b0002", "type": "image", "requested_type": "image",
                    "start": 16.0, "end": 24.0, "duration": 8.0,
                    "narration": "a paid still awaits delivery", "literal_subject": "one phone",
                    "image_prompt": "one ordinary phone on a table",
                },
                {
                    "id": "b0003", "type": "video", "requested_type": "video",
                    "start": 24.0, "end": 32.0, "duration": 8.0,
                    "narration": "a paid clip awaits delivery", "literal_subject": "one person",
                    "video_prompt": "one person answers one phone",
                },
                {
                    "id": "b0004", "type": "image", "requested_type": "image",
                    "start": 32.0, "end": 40.0, "duration": 8.0,
                    "narration": "a new still beat", "literal_subject": "one kitchen",
                    "image_prompt": "one ordinary kitchen counter",
                },
            ]
            pipeline = Pipeline(self.config(root, source))
            self._prime_run_checkpoint(pipeline, source, scenes, duration)
            pipeline.save_pending_image_job(scenes[2], "paid-image-job")
            pipeline.save_pending_video_job(scenes[3], "paid-video-uuid")
            pipeline.geminigen.ensure_available = Mock(return_value={"status": "Operational"})
            pipeline.event = Mock()
            pipeline.clear_checkpoint = Mock()
            total_before_resume = pipeline.total_spent
            call_order = []
            new_asset_calls = []

            def recover_or_forbid_purchase(scene):
                call_order.append(scene["id"])
                if scene["id"] == "b0002":
                    asset = pipeline.assets / "b0002.png"
                    asset.write_bytes(b"x" * 2000)
                    pipeline.clear_pending_image_job(scene)
                    return asset
                if scene["id"] == "b0003":
                    raise PaidAssetRecoveryError("El recurso pagado sigue pendiente de entrega.")
                new_asset_calls.append(scene["id"])
                post("https://api.snapgen.ai/forbidden-new-purchase")
                if scene["type"] == "video":
                    pipeline.geminigen.spent_usd += pipeline.geminigen.ESTIMATED_CLIP_USD
                else:
                    pipeline.algrow.spent_usd += pipeline.algrow.ESTIMATED_IMAGE_USD
                asset = pipeline.assets / f"{scene['id']}.png"
                asset.write_bytes(b"x" * 2000)
                return asset

            pipeline.generate_one = recover_or_forbid_purchase
            contexts = self._run_patches(duration)
            with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], contexts[6]:
                with self.assertRaises(PaidAssetRecoveryError):
                    pipeline.run()

            post.assert_not_called()
            self.assertEqual(new_asset_calls, [])
            self.assertTrue(call_order)
            self.assertTrue(set(call_order).issubset({"b0002", "b0003"}))
            self.assertEqual(pipeline.total_spent, total_before_resume)
            self.assertEqual(pipeline.pending_video_job_for(scenes[3]), "paid-video-uuid")
            pipeline.clear_checkpoint.assert_not_called()
            pipeline.cleanup()

    def _assert_preflight_preserves_safe_paid_reason(self, media_type):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            duration = 16.0
            media_scene = {
                "id": "b0001", "type": media_type, "requested_type": media_type,
                "start": 8.0, "end": 16.0, "duration": 8.0,
                "narration": "one paid resource awaits recovery",
                "literal_subject": "one ordinary subject",
            }
            if media_type == "video":
                media_scene["video_prompt"] = "one ordinary person performs one action"
                safe_reason = "SnapGen history HTTP 503 while resuming the paid clip"
            else:
                media_scene["image_prompt"] = "one ordinary object on a table"
                safe_reason = "Algrow status HTTP 503 while resuming the paid image"
            scenes = [
                {
                    "id": "b0000", "type": "avatar", "requested_type": "avatar",
                    "start": 0.0, "end": 8.0, "duration": 8.0,
                    "narration": "presenter opens the story", "literal_subject": "presenter",
                },
                media_scene,
            ]
            pipeline = Pipeline(self.config(root, source))
            self._prime_run_checkpoint(pipeline, source, scenes, duration)
            if media_type == "video":
                pipeline.save_pending_video_job(media_scene, "paid-video-uuid")
            else:
                pipeline.save_pending_image_job(media_scene, "paid-image-job")
            pipeline.geminigen.ensure_available = Mock(return_value={"status": "Operational"})
            pipeline.event = Mock()
            pipeline.clear_checkpoint = Mock()
            total_before_resume = pipeline.total_spent
            pipeline.generate_one = Mock(side_effect=ProviderError(safe_reason))

            contexts = self._run_patches(duration)
            with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], contexts[6]:
                with self.assertRaises(PaidAssetRecoveryError) as raised:
                    pipeline.run()

            message = str(raised.exception)
            self.assertIn(safe_reason, message)
            if media_type == "image":
                self.assertNotIn("pendiente en SnapGen", message)
            matching_failures = [
                item for item in pipeline.failures
                if item["id"] == media_scene["id"] and item["stage"] == "paid_asset_recovery"
            ]
            self.assertTrue(matching_failures)
            self.assertIn(safe_reason, matching_failures[-1]["error"])
            self.assertEqual(pipeline.total_spent, total_before_resume)
            if media_type == "video":
                self.assertEqual(pipeline.pending_video_job_for(media_scene), "paid-video-uuid")
            else:
                self.assertEqual(pipeline.pending_image_job_for(media_scene), "paid-image-job")
            pipeline.clear_checkpoint.assert_not_called()
            pipeline.cleanup()

    @patch.dict(os.environ, API_ENV)
    def test_video_preflight_categorizes_paid_provider_error_and_preserves_safe_reason(self):
        self._assert_preflight_preserves_safe_paid_reason("video")

    @patch.dict(os.environ, API_ENV)
    def test_image_preflight_categorizes_paid_provider_error_and_preserves_safe_reason(self):
        self._assert_preflight_preserves_safe_paid_reason("image")

    @patch.dict(os.environ, API_ENV)
    def test_terminal_image_404_clears_only_its_pending_then_buys_one_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"source")
            duration = 24.0
            terminal_image = {
                "id": "b0001", "type": "image", "requested_type": "image",
                "start": 8.0, "end": 16.0, "duration": 8.0,
                "narration": "one paid still no longer exists",
                "literal_subject": "one ordinary phone",
                "image_prompt": "one ordinary phone on a table",
            }
            other_pending = {
                "id": "b0002", "type": "video", "requested_type": "video",
                "start": 16.0, "end": 24.0, "duration": 8.0,
                "narration": "another paid clip still exists",
                "literal_subject": "one ordinary person",
                "video_prompt": "one ordinary person answers one phone",
            }
            scenes = [
                {
                    "id": "b0000", "type": "avatar", "requested_type": "avatar",
                    "start": 0.0, "end": 8.0, "duration": 8.0,
                    "narration": "presenter opens the story", "literal_subject": "presenter",
                },
                terminal_image,
                other_pending,
            ]
            pipeline = Pipeline(self.config(root, source))
            self._prime_run_checkpoint(pipeline, source, scenes, duration)
            pipeline.save_pending_image_job(terminal_image, "missing-paid-image-job")
            pipeline.save_pending_video_job(other_pending, "other-paid-video-uuid")
            pipeline.geminigen.ensure_available = Mock(return_value={"status": "Operational"})
            pipeline.event = Mock()
            pipeline.clear_checkpoint = Mock()
            media_calls = []
            other_pending_survived_terminal_clear = []
            image_unit_cost = 0.35 * pipeline.algrow.CREDIT_USD
            total_before_resume = pipeline.total_spent

            def image_provider(_prompt, output, **kwargs):
                resume_job_id = kwargs.get("resume_job_id") or ""
                media_calls.append(("image", resume_job_id))
                if resume_job_id:
                    other_pending_survived_terminal_clear.append(
                        pipeline.pending_video_job_for(other_pending)
                    )
                    raise RegeneratableError('HTTP 404: {"error":"Job not found"}')
                pipeline.algrow.spent_usd += image_unit_cost
                output = Path(output)
                output.write_bytes(b"x" * 2000)
                return output, "https://example.test/replacement.png"

            def video_provider(_prompt, output, **kwargs):
                media_calls.append(("video", kwargs.get("resume_uuid") or ""))
                output = Path(output)
                output.write_bytes(b"x" * 2000)
                return output

            pipeline.algrow.generate_image = image_provider
            pipeline.geminigen.generate_video = video_provider
            pipeline.review_image = Mock(return_value={
                "pass": True, "semantic_score": 90, "realism_score": 90, "integrity_score": 90,
            })
            pipeline.review_video = Mock(return_value={
                "pass": True, "watermark": False, "semantic_score": 90,
                "realism_score": 90, "motion_score": 90, "continuity_score": 90, "integrity_score": 90,
            })

            contexts = self._run_patches(duration)
            with contexts[0], contexts[1], contexts[2], contexts[3], contexts[4], contexts[5], contexts[6]:
                result = pipeline.run()

            self.assertTrue(result["ok"])
            self.assertEqual(media_calls, [
                ("image", "missing-paid-image-job"),
                ("video", "other-paid-video-uuid"),
                ("image", ""),
            ])
            self.assertEqual(other_pending_survived_terminal_clear, ["other-paid-video-uuid"])
            self.assertEqual(pipeline.pending_image_job_for(terminal_image), "")
            self.assertEqual(pipeline.pending_video_job_for(other_pending), "")
            self.assertAlmostEqual(
                pipeline.total_spent - total_before_resume, image_unit_cost, places=6,
            )
            terminal_failures = [
                item for item in pipeline.failures
                if item["id"] == terminal_image["id"] and item["stage"] == "paid_asset_terminal"
            ]
            self.assertEqual(len(terminal_failures), 1)
            self.assertIn("Job not found", terminal_failures[0]["error"])
            pipeline.clear_checkpoint.assert_called_once()
            pipeline.cleanup()


if __name__ == "__main__":
    unittest.main()
