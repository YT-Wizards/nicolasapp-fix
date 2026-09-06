import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "engine"))

from providers import (
    AlgrowClient,
    CircuitOpenError,
    GeminiGenClient,
    ProviderCircuitBreaker,
    ProviderError,
    ProviderHTTPError,
    VercelGatewayClient,
    _extract_json,
    download,
    requests,
)


class ProviderParsingTests(unittest.TestCase):
    def test_circuit_breaker_opens_then_allows_one_probe_after_cooldown(self):
        now = [100.0]
        breaker = ProviderCircuitBreaker(failure_threshold=2, cooldown=30, clock=lambda: now[0])
        breaker.record_failure()
        breaker.record_failure()
        with self.assertRaises(CircuitOpenError):
            breaker.before_request()
        now[0] = 131.0
        breaker.before_request()
        with self.assertRaises(CircuitOpenError):
            breaker.before_request()
        breaker.record_success()
        breaker.before_request()

    @patch("providers.requests.post")
    def test_open_circuit_blocks_new_paid_veo_request(self, post):
        breaker = ProviderCircuitBreaker(failure_threshold=1, cooldown=60, clock=lambda: 100.0)
        breaker.record_failure()
        client = GeminiGenClient("key", circuit_breaker=breaker)
        with self.assertRaises(CircuitOpenError):
            client.generate_video("prompt", Path("/tmp/blocked-vyt.mp4"))
        post.assert_not_called()

    @patch("providers.urllib.request.urlopen")
    def test_download_resumes_partial_file_with_range(self, urlopen):
        class Response:
            status = 206
            def __enter__(self): return self
            def __exit__(self, *_args): pass
            def read(self, _size):
                if getattr(self, "used", False): return b""
                self.used = True
                return b"def"

        urlopen.return_value = Response()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "asset.png"
            Path(f"{destination}.part").write_bytes(b"abc")
            self.assertEqual(download("https://example.test/asset", destination), destination)
            self.assertEqual(destination.read_bytes(), b"abcdef")
            self.assertFalse(Path(f"{destination}.part").exists())
        headers = urlopen.call_args.args[0].headers
        self.assertEqual(headers.get("Range"), "bytes=3-")

    @patch("providers._json_request")
    def test_gateway_does_not_call_http_when_cost_reservation_is_rejected(self, request):
        reserved = []
        released = []

        def reject(amount):
            reserved.append(amount)
            raise ProviderError("budget exhausted")

        client = VercelGatewayClient(
            "test-key", "google/gemini-2.5-flash",
            reserve_callback=reject, release_callback=released.append,
        )
        with self.assertRaisesRegex(ProviderError, "budget exhausted"):
            client.chat_json("system", "prompt", max_tokens=1200)
        request.assert_not_called()
        self.assertEqual(len(reserved), 1)
        self.assertGreater(reserved[0], 0.0)
        self.assertEqual(released, [])

    @patch("providers.time.sleep", return_value=None)
    @patch("providers._json_request")
    def test_gateway_releases_reserved_maximum_cost_when_http_fails(self, request, _sleep):
        request.side_effect = ProviderError("HTTP 500: temporary failure")
        reserved = []
        released = []
        client = VercelGatewayClient(
            "test-key", "google/gemini-2.5-flash",
            reserve_callback=reserved.append, release_callback=released.append,
        )
        with self.assertRaisesRegex(ProviderError, "HTTP 500"):
            client.chat_json("system", "prompt", max_tokens=1200)
        self.assertEqual(len(reserved), 1)
        self.assertGreater(reserved[0], 0.0)
        self.assertEqual(released, reserved)

    @patch("providers.time.sleep", return_value=None)
    @patch("providers.requests.get")
    @patch("providers.requests.post")
    def test_veo_sends_presenter_reference_only_when_requested(self, post, get, _sleep):
        class CreatedResponse:
            ok = True
            status_code = 200
            def json(self):
                return {"data": {"uuid": "new-job"}}

        class StatusResponse:
            ok = True
            status_code = 200
            def json(self):
                return {"data": {"status": 3, "error_message": "stop after request inspection"}}

        post.return_value = CreatedResponse()
        get.return_value = StatusResponse()
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "presenter.jpg"
            reference.write_bytes(b"jpeg-reference")
            client = GeminiGenClient("key")
            with self.assertRaisesRegex(ProviderError, "stop after request inspection"):
                client.generate_video("prompt", Path(directory) / "video.mp4", reference_images=[reference])
        fields = post.call_args.kwargs["files"]
        field_names = [name for name, _value in fields]
        self.assertIn("ref_images", field_names)
        self.assertEqual(field_names.count("ref_images"), 1)
        self.assertIn(("mode_image", (None, "ingredient")), fields)

    @patch("providers.time.sleep", return_value=None)
    @patch("providers.requests.get")
    @patch("providers.requests.post")
    def test_plain_veo_request_never_leaks_a_reference(self, post, get, _sleep):
        class CreatedResponse:
            ok = True
            status_code = 200
            def json(self):
                return {"data": {"uuid": "new-job"}}

        class StatusResponse:
            ok = True
            status_code = 200
            def json(self):
                return {"data": {"status": 3, "error_message": "stop after request inspection"}}

        post.return_value = CreatedResponse()
        get.return_value = StatusResponse()
        client = GeminiGenClient("key")
        with self.assertRaisesRegex(ProviderError, "stop after request inspection"):
            client.generate_video("prompt", Path("/tmp/video.mp4"))
        fields = post.call_args.kwargs["files"]
        field_names = [name for name, _value in fields]
        self.assertNotIn("ref_images", field_names)
        self.assertNotIn("mode_image", field_names)

    @patch("providers.time.sleep", return_value=None)
    @patch("providers.requests.get")
    @patch("providers.requests.post")
    def test_veo_resume_uuid_does_not_purchase_a_second_clip(self, post, get, _sleep):
        class StatusResponse:
            ok = True
            status_code = 200
            def json(self):
                return {"data": {"status": 3, "error_message": "failed"}}

        get.return_value = StatusResponse()
        client = GeminiGenClient("key")
        with self.assertRaisesRegex(ProviderError, "failed"):
            client.generate_video("prompt", Path("/tmp/video.mp4"), resume_uuid="paid-uuid")
        post.assert_not_called()
        self.assertEqual(client.spent_usd, 0.0)

    def test_extracts_json_after_plain_language(self):
        value = _extract_json('Result follows: {"pass": true, "score": 80} trailing text')
        self.assertEqual(value, {"pass": True, "score": 80})

    def test_empty_reviewer_response_has_clear_error(self):
        with self.assertRaisesRegex(ProviderError, "respuesta vacía"):
            _extract_json("")

    @patch("providers.time.sleep", return_value=None)
    @patch("providers._json_request")
    def test_gateway_retries_a_timeout_without_restarting_the_job(self, request, _sleep):
        request.side_effect = [
            ProviderError("<urlopen error [Errno 60] Operation timed out>"),
            {"choices": [{"message": {"content": '{"ok":true}'}}], "usage": {}},
        ]
        client = VercelGatewayClient("test-key", "google/gemini-2.5-flash")
        self.assertEqual(client.chat_json("system", "prompt"), {"ok": True})
        self.assertEqual(request.call_count, 2)

    @patch("providers._json_request")
    def test_gateway_does_not_retry_authentication_errors(self, request):
        request.side_effect = ProviderError("HTTP 401: invalid key")
        client = VercelGatewayClient("test-key", "google/gemini-2.5-flash")
        with self.assertRaisesRegex(ProviderError, "HTTP 401"):
            client.chat_json("system", "prompt")
        self.assertEqual(request.call_count, 1)

    @patch("providers.time.sleep", return_value=None)
    @patch("providers.download")
    @patch("providers._json_request")
    def test_algrow_keeps_polling_the_same_paid_job_after_timeout(self, request, download, _sleep):
        request.side_effect = [
            {"job_id": "paid-job", "credits_used": 0.35},
            ProviderError("<urlopen error timed out>"),
            {"status": "completed", "image_url": "https://example.test/image.png"},
        ]
        download.return_value = Path("/tmp/image.png")
        client = AlgrowClient("test-key")
        output, url = client.generate_image("prompt", Path("/tmp/image.png"))
        self.assertEqual(output, Path("/tmp/image.png"))
        self.assertEqual(url, "https://example.test/image.png")
        self.assertEqual(request.call_count, 3)
        self.assertEqual(download.call_count, 1)

    @patch("providers.time.sleep")
    @patch("providers.download")
    @patch("providers._json_request")
    def test_algrow_honors_retry_after_without_buying_again(self, request, download, sleep):
        request.side_effect = [
            {"job_id": "paid-job", "credits_used": 0.35},
            ProviderHTTPError(429, "rate limited", retry_after=7),
            {"status": "completed", "image_url": "https://example.test/image.png"},
        ]
        download.return_value = Path("/tmp/image.png")
        client = AlgrowClient("test-key")
        client.generate_image("prompt", Path("/tmp/image.png"))
        sleep.assert_any_call(7.0)
        self.assertEqual(request.call_count, 3)

    @patch("providers.time.sleep", return_value=None)
    @patch("providers.download")
    @patch("providers._json_request")
    def test_algrow_resume_job_skips_second_purchase(self, request, download, _sleep):
        request.return_value = {
            "status": "completed", "image_url": "https://example.test/image.png",
        }
        download.return_value = Path("/tmp/image.png")
        created = []
        client = AlgrowClient("test-key")
        output, _url = client.generate_image(
            "prompt", Path("/tmp/image.png"), resume_job_id="already-paid-job",
            on_created=created.append,
        )
        self.assertEqual(output, Path("/tmp/image.png"))
        self.assertEqual(created, [])
        self.assertAlmostEqual(client.spent_usd, 0.0, places=6)
        self.assertEqual(request.call_count, 1)
        self.assertIn("/job-status/already-paid-job", request.call_args.args[0])

    @patch("providers.time.sleep", return_value=None)
    @patch("providers.download")
    @patch("providers._json_request")
    def test_algrow_persists_job_before_first_poll(self, request, download, _sleep):
        request.side_effect = [
            {"job_id": "new-paid-job", "credits_used": 0.35},
            {"status": "completed", "image_url": "https://example.test/image.png"},
        ]
        download.return_value = Path("/tmp/image.png")
        created = []
        client = AlgrowClient("test-key")
        client.generate_image("prompt", Path("/tmp/image.png"), on_created=created.append)
        self.assertEqual(created, ["new-paid-job"])
        self.assertGreater(client.spent_usd, 0.0)

    @patch("providers.time.sleep", return_value=None)
    @patch("providers.download")
    @patch("providers._json_request")
    def test_algrow_small_total_timeout_bounds_every_network_timeout(self, request, download, _sleep):
        api_timeouts = []
        download_timeouts = []

        def api_call(_url, **kwargs):
            api_timeouts.append(kwargs["timeout"])
            if len(api_timeouts) == 1:
                return {"job_id": "paid-job", "credits_used": 0.35}
            return {"status": "completed", "image_url": "https://example.test/image.png"}

        def fail_download(_url, _output, timeout):
            download_timeouts.append(timeout)
            raise ProviderError("object storage unavailable")

        request.side_effect = api_call
        download.side_effect = fail_download
        client = AlgrowClient("test-key")
        with self.assertRaises(ProviderError):
            client.generate_image("prompt", Path("/tmp/image.png"), timeout=0.25)
        self.assertGreaterEqual(len(api_timeouts), 2)
        self.assertGreaterEqual(len(download_timeouts), 1)
        self.assertTrue(all(0 < value <= 0.25 for value in api_timeouts + download_timeouts))

    @patch("providers.time.sleep", return_value=None)
    @patch("providers.time.monotonic")
    @patch("providers.requests.get")
    @patch("providers.requests.post")
    def test_veo_small_total_timeout_bounds_create_poll_and_download(self, post, get, monotonic, _sleep):
        clock = [100.0]

        def tick():
            value = clock[0]
            clock[0] += 0.01
            return value

        monotonic.side_effect = tick
        seen_timeouts = []

        class CreatedResponse:
            ok = True
            status_code = 200
            def json(self):
                return {"data": {"uuid": "new-job"}}

        class StatusResponse:
            ok = True
            status_code = 200
            def json(self):
                return {
                    "data": {
                        "status": 2,
                        "generated_video": [{
                            "status": 2, "has_watermark": 0,
                            "video_url": "https://media.example.test/video.mp4",
                        }],
                    }
                }

        def remember_timeout(value):
            if isinstance(value, tuple):
                seen_timeouts.extend(value)
            else:
                seen_timeouts.append(value)

        def create(*_args, **kwargs):
            remember_timeout(kwargs["timeout"])
            return CreatedResponse()

        def poll_or_download(url, **kwargs):
            remember_timeout(kwargs["timeout"])
            if "/history/" in url:
                return StatusResponse()
            raise requests.Timeout("object storage unavailable")

        post.side_effect = create
        get.side_effect = poll_or_download
        client = GeminiGenClient("test-key")
        with self.assertRaises(ProviderError):
            client.generate_video("prompt", Path("/tmp/video.mp4"), timeout=0.25)
        self.assertGreaterEqual(len(seen_timeouts), 4)
        self.assertTrue(all(0 < value <= 0.25 for value in seen_timeouts))

    @patch("providers.time.sleep", return_value=None)
    @patch("providers._json_request")
    def test_gateway_does_not_buy_the_same_truncated_json_three_times(self, request, _sleep):
        request.return_value = {
            "choices": [{"message": {"content": '{"scenes":['}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100},
        }
        client = VercelGatewayClient("test-key", "google/gemini-2.5-flash")
        with self.assertRaisesRegex(ProviderError, "JSON incompleto"):
            client.chat_json("system", "prompt")
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
