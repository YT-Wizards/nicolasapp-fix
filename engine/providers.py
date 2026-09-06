import base64
import json
import mimetypes
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import threading
from pathlib import Path

try:
    import requests
except ImportError:
    import sys

    vendor = Path(__file__).resolve().parent / "vendor"
    if vendor.exists():
        sys.path.insert(0, str(vendor))
    import requests

from requests.packages import urllib3


class ProviderError(RuntimeError):
    pass


class ProviderHTTPError(ProviderError):
    def __init__(self, status, message, retry_after=None):
        super().__init__(f"HTTP {status}: {message}")
        self.status = int(status)
        self.retry_after = retry_after


class ProviderTemporarilyUnavailableError(ProviderError):
    """A provider request should be retried later without buying a replacement."""

    def __init__(self, message, retry_after=30.0):
        super().__init__(message)
        self.retry_after = max(1.0, float(retry_after or 30.0))


class CircuitOpenError(ProviderTemporarilyUnavailableError):
    """The provider is temporarily paused after repeated transient failures."""


class ProviderRateLimitError(ProviderTemporarilyUnavailableError):
    """A paid POST could not acquire the provider-wide concurrency slot."""

    def __init__(self, message, retry_after=15.0):
        super().__init__(message, retry_after=retry_after)


class ProviderRequestGate:
    """Bound concurrent paid POST requests while leaving polling independent."""

    def __init__(self, capacity=1):
        self.capacity = max(1, int(capacity))
        self._semaphore = threading.BoundedSemaphore(self.capacity)

    def acquire(self, timeout=None):
        acquired = self._semaphore.acquire(timeout=timeout)
        if not acquired:
            raise ProviderRateLimitError(
                "El proveedor está ocupado; VYT mantiene el trabajo en cola sin comprar otro recurso."
            )
        return True

    def release(self):
        self._semaphore.release()


class ProviderCircuitBreaker:
    """Small thread-safe breaker shared by paid provider adapters."""

    def __init__(self, failure_threshold=3, cooldown=60.0, clock=None):
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown = max(0.0, float(cooldown))
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until = 0.0
        self._probe_in_flight = False

    def before_request(self):
        with self._lock:
            now = float(self.clock())
            if now < self._open_until:
                raise CircuitOpenError(
                    "El proveedor está temporalmente pausado tras varios fallos; "
                    f"se reintentará en {max(1, int(self._open_until - now))} s.",
                    retry_after=self._open_until - now,
                )
            if self._open_until and not self._probe_in_flight:
                self._probe_in_flight = True
            elif self._open_until and self._probe_in_flight:
                raise CircuitOpenError(
                    "El proveedor está probándose; se mantiene la cola en espera.",
                    retry_after=15,
                )

    def record_success(self):
        with self._lock:
            self._failures = 0
            self._open_until = 0.0
            self._probe_in_flight = False

    def record_failure(self):
        with self._lock:
            self._failures += 1
            self._probe_in_flight = False
            if self._failures >= self.failure_threshold:
                self._open_until = float(self.clock()) + self.cooldown


_PROVIDER_CIRCUITS = {}
_PROVIDER_CIRCUITS_LOCK = threading.Lock()
_PROVIDER_GATES = {}
_PROVIDER_GATES_LOCK = threading.Lock()


def provider_circuit(name):
    with _PROVIDER_CIRCUITS_LOCK:
        return _PROVIDER_CIRCUITS.setdefault(str(name), ProviderCircuitBreaker())


def provider_gate(name):
    with _PROVIDER_GATES_LOCK:
        return _PROVIDER_GATES.setdefault(str(name), ProviderRequestGate())


def _is_transient_provider_error(error):
    if isinstance(error, ProviderHTTPError):
        return error.status in {408, 425, 429, 500, 502, 503, 504}
    message = str(error or "").lower()
    return any(token in message for token in (
        "timed out", "timeout", "connection reset", "remote end closed",
        "temporary", "temporarily", "name or service not known",
    ))


class RegeneratableError(ProviderError):
    """The provider explicitly failed, or a completed asset failed visual QA."""
    pass


class PaidAssetRecoveryError(ProviderError):
    """A paid remote asset still exists, but cannot be delivered right now.

    This is deliberately not regeneratable: callers must preserve the remote
    identifier and resume it later instead of buying a replacement.
    """
    pass


_R2_ROUTE_LOCK = threading.Lock()
_R2_DIRECT_UNREACHABLE = set()
_R2_EDGE_IPS = []
_R2_GOOD_EDGE_IP = {}


def _snapgen_r2_parts(url):
    """Return the signed request target without ever weakening TLS checks."""
    parsed = urllib.parse.urlsplit(str(url or ""))
    hostname = str(parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname.endswith(".r2.cloudflarestorage.com"):
        raise ProviderError("SnapGen devolvió una dirección de almacenamiento no válida.")
    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"
    return hostname, target


def _snapgen_edge_ipv4s():
    """Resolve reachable Cloudflare edges used by SnapGen's own API."""
    global _R2_EDGE_IPS
    with _R2_ROUTE_LOCK:
        if _R2_EDGE_IPS:
            return list(_R2_EDGE_IPS)
    try:
        records = socket.getaddrinfo(
            "api.snapgen.ai", 443, socket.AF_INET, socket.SOCK_STREAM
        )
    except OSError as error:
        raise ProviderError("No se pudo resolver la ruta alternativa segura de SnapGen.") from error
    addresses = []
    for record in records:
        address = str(record[4][0])
        if address and address not in addresses:
            addresses.append(address)
    if not addresses:
        raise ProviderError("SnapGen no publicó ninguna ruta alternativa de descarga.")
    with _R2_ROUTE_LOCK:
        _R2_EDGE_IPS = list(addresses)
    return addresses


def _r2_direct_is_unreachable(hostname):
    with _R2_ROUTE_LOCK:
        return hostname in _R2_DIRECT_UNREACHABLE


def _remember_r2_edge(hostname, edge_ip):
    with _R2_ROUTE_LOCK:
        _R2_DIRECT_UNREACHABLE.add(hostname)
        _R2_GOOD_EDGE_IP[hostname] = edge_ip


def _download_r2_via_snapgen_edge(url, partial, deadline, edge_ips=None):
    """Download a signed R2 object through a reachable SnapGen Cloudflare edge.

    The connection goes to an alternate Cloudflare IP, while the original R2
    hostname remains both the TLS SNI/certificate identity and the HTTP Host.
    Therefore the signed URL, authentication and certificate verification stay
    exactly intact; this only avoids a broken network route.
    """
    hostname, target = _snapgen_r2_parts(url)
    partial = Path(partial)
    partial.parent.mkdir(parents=True, exist_ok=True)
    candidates = list(edge_ips or _snapgen_edge_ipv4s())
    with _R2_ROUTE_LOCK:
        preferred = _R2_GOOD_EDGE_IP.get(hostname)
    if preferred in candidates:
        candidates.remove(preferred)
        candidates.insert(0, preferred)
    last_error = None
    for edge_ip in candidates:
        response = None
        pool = None
        try:
            remaining = _remaining_timeout(deadline, 45)
            downloaded = partial.stat().st_size if partial.exists() else 0
            headers = {"Host": hostname, "User-Agent": "VYT/1.0"}
            if downloaded:
                headers["Range"] = f"bytes={downloaded}-"
            timeout = urllib3.Timeout(
                connect=min(8, remaining), read=min(30, remaining)
            )
            pool = urllib3.HTTPSConnectionPool(
                edge_ip,
                port=443,
                timeout=timeout,
                maxsize=1,
                block=True,
                retries=False,
                server_hostname=hostname,
                assert_hostname=hostname,
                cert_reqs="CERT_REQUIRED",
                ca_certs=requests.certs.where(),
            )
            response = pool.request(
                "GET", target, headers=headers, preload_content=False,
                redirect=False, retries=False, timeout=timeout,
            )
            if int(response.status) not in {200, 206}:
                raise ProviderError(
                    f"La ruta alternativa de SnapGen respondió HTTP {int(response.status)}."
                )
            append = bool(downloaded and int(response.status) == 206)
            with partial.open("ab" if append else "wb") as output:
                for chunk in response.stream(1024 * 1024):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("descarga fuera del tiempo máximo")
                    if chunk:
                        output.write(chunk)
            _remember_r2_edge(hostname, edge_ip)
            return partial
        except Exception as error:
            last_error = error
            if time.monotonic() >= deadline:
                break
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            if pool is not None:
                try:
                    pool.close()
                except Exception:
                    pass
    raise ProviderError(
        "Las rutas seguras de almacenamiento de SnapGen no respondieron."
    ) from last_error


def _json_request(url, method="GET", headers=None, payload=None, timeout=60):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    final_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        final_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=final_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        message = error.read().decode("utf-8", errors="replace")
        raw_retry_after = error.headers.get("Retry-After") if error.headers else None
        try:
            retry_after = max(0.0, float(raw_retry_after)) if raw_retry_after is not None else None
        except (TypeError, ValueError):
            retry_after = None
        raise ProviderHTTPError(error.code, message[:800], retry_after=retry_after) from error
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
        raise ProviderError(str(error)) from error


def _safe_provider_reason(error, fallback="El proveedor no respondió correctamente."):
    """Keep a useful provider reason without leaking signed URLs or credentials."""
    message = " ".join(str(error or "").split())
    message = re.sub(r"https?://\S+", "[dirección protegida]", message, flags=re.IGNORECASE)
    message = re.sub(
        r"(?i)(authorization|bearer|x-api-key|x-amz-[a-z-]+)\s*[:=]\s*[^\s,;]+",
        r"\1=[protegido]",
        message,
    )
    return (message or fallback)[:320]


def _remaining_timeout(deadline, cap):
    """Return a per-request timeout that can never outlive the parent job."""
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        raise ProviderError("VYT alcanzó el tiempo máximo mientras esperaba al proveedor.")
    return max(0.05, min(float(cap), remaining))


def _sleep_before(deadline, seconds):
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        return False
    time.sleep(min(float(seconds), remaining))
    return time.monotonic() < float(deadline)


def _retry_delay(error, fallback):
    retry_after = getattr(error, "retry_after", None)
    if retry_after is None:
        return float(fallback)
    return min(120.0, max(0.0, float(retry_after)))


def download(url, destination, timeout=120, deadline=None):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    deadline = float(deadline) if deadline is not None else time.monotonic() + float(timeout)
    partial = destination.with_suffix(destination.suffix + ".part")
    downloaded = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "VYT/1.0"}
    if downloaded:
        headers["Range"] = f"bytes={downloaded}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_remaining_timeout(deadline, timeout)) as response:
            append = bool(downloaded and getattr(response, "status", 200) == 206)
            with partial.open("ab" if append else "wb") as output:
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("descarga fuera del tiempo máximo")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
        partial.replace(destination)
    except Exception as error:
        # Keep the partial file so a later signed-URL refresh can continue it.
        raise ProviderError(f"No se pudo descargar el recurso: {error}") from error
    return destination


def _extract_json(text):
    text = str(text or "").strip()
    if not text:
        raise ProviderError("El analizador devolvió una respuesta vacía.")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start_candidates = [position for position in (text.find("{"), text.find("[")) if position >= 0]
        if not start_candidates:
            raise ProviderError("El analizador no devolvió JSON válido.")
        start = min(start_candidates)
        try:
            return json.JSONDecoder().raw_decode(text[start:])[0]
        except json.JSONDecodeError as error:
            raise ProviderError("El analizador devolvió JSON incompleto o inválido.") from error


class VercelGatewayClient:
    MODEL_PRICING = {
        # USD per million tokens. Sonnet 5 launch price is valid through 2026-08-31.
        "anthropic/claude-sonnet-5": (2.0, 10.0),
        "google/gemini-2.5-flash": (0.30, 2.50),
    }

    def __init__(
        self, api_key, model, provider_order=None,
        reserve_callback=None, release_callback=None,
        remaining_callback=None, usage_callback=None,
    ):
        if not api_key:
            raise ProviderError("Falta la API key de Vercel AI Gateway.")
        self.api_key = api_key
        self.model = model
        self.provider_order = list(provider_order or [])
        self.spent_usd = 0.0
        self._spend_lock = threading.Lock()
        self.reserve_callback = reserve_callback
        self.release_callback = release_callback
        self.remaining_callback = remaining_callback
        self.usage_callback = usage_callback

    def _request_cost_ceiling(self, system, prompt, images, max_tokens):
        """Pessimistic ceiling for up to three gateway attempts.

        Output tokens have a hard API limit. Text is deliberately counted at up
        to one token per UTF-8 byte and each image gets a generous visual-token
        allowance. The three-attempt multiplier keeps retry storms inside VYT's
        project budget as well.
        """
        input_rate, output_rate = self.MODEL_PRICING.get(self.model, (10.0, 30.0))
        text_tokens_ceiling = len(str(system).encode("utf-8")) + len(str(prompt).encode("utf-8")) + 1000
        image_tokens_ceiling = 12000 * len(list(images or []))
        one_attempt = (
            (text_tokens_ceiling + image_tokens_ceiling) * input_rate
            + max(1, int(max_tokens)) * output_rate
        ) / 1_000_000
        return max(0.001, one_attempt * 3.0)

    def _record_usage(self, usage):
        direct_cost = usage.get("cost")
        if direct_cost is not None:
            try:
                amount = float(direct_cost)
            except (TypeError, ValueError):
                amount = 0.0
        else:
            input_rate, output_rate = self.MODEL_PRICING.get(self.model, (0.0, 0.0))
            input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
            output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
            try:
                amount = (float(input_tokens) * input_rate + float(output_tokens) * output_rate) / 1_000_000
            except (TypeError, ValueError):
                amount = 0.0
        with self._spend_lock:
            self.spent_usd += max(0.0, amount)
        if self.usage_callback:
            self.usage_callback(max(0.0, amount))
        return max(0.0, amount)

    def chat_json(self, system, prompt, images=None, max_tokens=6000):
        reservation = self._request_cost_ceiling(system, prompt, images, max_tokens)
        reserved = False
        content = [{"type": "text", "text": prompt}]
        for image_path in images or []:
            image_path = Path(image_path)
            mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        }
        if self.provider_order:
            payload["providerOptions"] = {"gateway": {"order": self.provider_order}}
        last_error = None
        if self.reserve_callback:
            # Prepare local inputs before reserving; a missing image must not
            # strand budget. Reservation still precedes every network request.
            self.reserve_callback(reservation)
            reserved = True
        try:
            for attempt in range(3):
                try:
                    timeout = 240
                    if self.remaining_callback:
                        remaining = float(self.remaining_callback())
                        if remaining <= 0:
                            raise ProviderError("VYT alcanzó el tiempo máximo antes del análisis.")
                        timeout = max(0.05, min(timeout, remaining))
                    data = _json_request(
                        "https://ai-gateway.vercel.sh/v1/chat/completions",
                        method="POST",
                        headers={"Authorization": f"Bearer {self.api_key}", "User-Agent": "VYT/1.0"},
                        payload=payload,
                        timeout=timeout,
                    )
                    usage = data.get("usage") or {}
                    self._record_usage(usage)
                    raw_content = data["choices"][0]["message"]["content"]
                    if isinstance(raw_content, list):
                        raw_content = "\n".join(
                            str(item.get("text") or item.get("content") or "")
                            for item in raw_content if isinstance(item, dict)
                        )
                    return _extract_json(raw_content)
                except (ProviderError, KeyError, IndexError, TypeError) as error:
                    last_error = error
                    message = str(error).lower()
                    retryable = any(token in message for token in (
                        "timed out", "timeout", "temporarily", "connection reset",
                        "remote end closed", "http 429", "http 500", "http 502",
                        "http 503", "http 504",
                    ))
                    if attempt >= 2 or not retryable:
                        break
                    delay = 2 + attempt * 3
                    if self.remaining_callback:
                        remaining = float(self.remaining_callback())
                        if remaining <= 0:
                            break
                        time.sleep(min(delay, remaining))
                    else:
                        time.sleep(delay)
            if isinstance(last_error, ProviderError):
                raise last_error
            raise ProviderError(f"Respuesta inesperada del analizador: {last_error}") from last_error
        finally:
            if reserved and self.release_callback:
                self.release_callback(reservation)


class AlgrowClient:
    CREDIT_USD = 9.99 / 250.0

    def __init__(self, api_key, circuit_breaker=None, request_gate=None):
        if not api_key:
            raise ProviderError("Falta la API key de Algrow.")
        self.api_key = api_key
        self.spent_usd = 0.0
        self._spend_lock = threading.Lock()
        self.circuit_breaker = circuit_breaker or provider_circuit("algrow")
        self.request_gate = request_gate or provider_gate("algrow")

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def generate_image(self, prompt, output_path, timeout=600, resume_job_id=None, on_created=None, on_stage=None):
        deadline = time.monotonic() + max(0.0, float(timeout))
        resumed_existing_job = bool(str(resume_job_id or "").strip())
        job_id = str(resume_job_id or "").strip()
        if not job_id:
            payload = {"prompt": prompt, "model": "gpt-image-2", "aspect_ratio": "16:9", "fast": False}
            self.request_gate.acquire(timeout=_remaining_timeout(deadline, 90))
            try:
                self.circuit_breaker.before_request()
                try:
                    created = _json_request(
                        "https://api.algrow.online/api/generate-image",
                        method="POST",
                        headers=self.headers,
                        payload=payload,
                        timeout=_remaining_timeout(deadline, 90),
                    )
                except Exception as error:
                    if _is_transient_provider_error(error):
                        self.circuit_breaker.record_failure()
                    raise
                else:
                    self.circuit_breaker.record_success()
            finally:
                self.request_gate.release()
            job_id = str(created.get("job_id") or "").strip()
            if not job_id:
                raise ProviderError(f"Algrow no devolvió job_id: {str(created)[:600]}")
            credits = float(created.get("credits_used") or 0.35)
            with self._spend_lock:
                self.spent_usd += credits * self.CREDIT_USD
            if on_created:
                # Persist the paid job before the first status request. If VYT is
                # interrupted from this point onward, the next run resumes this
                # exact job instead of issuing another paid POST.
                on_created(job_id)
        if on_stage:
            on_stage("polling")
        poll_errors = 0
        last_poll_error = None
        while time.monotonic() < deadline:
            try:
                status = _json_request(
                    f"https://api.algrow.online/api/job-status/{urllib.parse.quote(job_id)}",
                    headers=self.headers,
                    timeout=_remaining_timeout(deadline, 45),
                )
                poll_errors = 0
                last_poll_error = None
            except ProviderError as error:
                message = str(error).lower()
                if resumed_existing_job and re.match(r"^http 404\b", message):
                    # Algrow has positively confirmed that this old identifier no
                    # longer exists. It is the one safe case where VYT may clear
                    # the stale pending ID and buy exactly one replacement.
                    raise RegeneratableError(
                        "Algrow ya no conserva el trabajo pagado (HTTP 404: Job not found)."
                    ) from error
                if "http 401" in message or "http 403" in message:
                    raise PaidAssetRecoveryError(
                        "Algrow no pudo consultar la imagen pagada: "
                        f"{_safe_provider_reason(error)}"
                    ) from error
                # The image job is already paid. Keep polling this job_id through
                # temporary timeouts and 5xx pages instead of purchasing another.
                last_poll_error = error
                poll_errors += 1
                _sleep_before(deadline, _retry_delay(error, min(20, 3 + poll_errors * 2)))
                continue
            state = str(status.get("status", "")).lower()
            if state in {"completed", "success", "succeeded"}:
                urls = status.get("image_urls") or ([status.get("image_url")] if status.get("image_url") else [])
                if not urls:
                    # Some completions publish the result URL a few polls later.
                    _sleep_before(deadline, 5)
                    continue
                if on_stage:
                    on_stage("download_pending")
                last_download_error = None
                for attempt in range(6):
                    try:
                        remaining = _remaining_timeout(deadline, 180)
                        return download(
                            urls[0], output_path, timeout=remaining
                        ), urls[0]
                    except ProviderError as error:
                        last_download_error = error
                        if time.monotonic() >= deadline:
                            break
                    _sleep_before(deadline, min(20, 3 + attempt * 3))
                raise PaidAssetRecoveryError(
                    "Algrow terminó la imagen pagada, pero su archivo todavía no pudo descargarse: "
                    f"{_safe_provider_reason(last_download_error)}"
                ) from last_download_error
            if state in {"failed", "error", "cancelled"}:
                # This is the only provider state that proves the paid job can no
                # longer complete. The pipeline may now clear it and purchase one
                # replacement; timeouts, auth failures and download errors retain
                # the job_id for recovery.
                raise RegeneratableError(status.get("error") or status.get("message") or "La imagen falló en Algrow.")
            _sleep_before(deadline, 5)
        if last_poll_error is not None:
            raise PaidAssetRecoveryError(
                "Algrow no pudo recuperar el trabajo pagado: "
                f"{_safe_provider_reason(last_poll_error)}"
            ) from last_poll_error
        raise PaidAssetRecoveryError(
            "Algrow mantiene la imagen pagada pendiente de entrega y superó el tiempo máximo."
        )


class GeminiGenClient:
    ESTIMATED_CLIP_USD = 0.02

    def __init__(self, api_key, circuit_breaker=None, request_gate=None):
        if not api_key:
            raise ProviderError("Falta la API key de GeminiGen.")
        self.api_key = api_key
        self.spent_usd = 0.0
        self._spend_lock = threading.Lock()
        self.circuit_breaker = circuit_breaker or provider_circuit("snapgen")
        self.request_gate = request_gate or provider_gate("snapgen")

    def ensure_available(self, minimum_success_rate=80.0):
        """Fail for free before any paid work when SnapGen reports Veo trouble."""
        try:
            response = requests.get(
                "https://api.snapgen.ai/api/v1/models/status",
                params={"window": "1h"},
                headers={"Accept": "application/json"},
                timeout=30,
            )
            if not response.ok:
                raise ProviderError(
                    "VYT no pudo comprobar gratuitamente el estado de Veo; "
                    "no inició el trabajo para proteger tus créditos."
                )
            payload = response.json()
        except (requests.RequestException, ValueError) as error:
            raise ProviderError(
                "VYT no pudo comprobar gratuitamente el estado de Veo; "
                "no inició el trabajo para proteger tus créditos."
            ) from error

        models = payload.get("models") or []
        model = next(
            (item for item in models if item.get("group_key") == "veo-3.1-fast"),
            None,
        )
        if not model:
            raise ProviderError(
                "SnapGen no publicó el estado de Veo 3.1 Fast. "
                "VYT no inició el trabajo para proteger tus créditos."
            )
        rate = float(model.get("success_rate") or 0)
        status = str(model.get("status") or "Unknown")
        if status.lower() != "operational" or rate < minimum_success_rate:
            raise ProviderError(
                f"Veo 3.1 Fast está inestable en SnapGen ({rate:.0f}% de éxito, {status}). "
                "VYT no inició el trabajo ni gastó créditos. Inténtalo más tarde."
            )
        return {"status": status, "success_rate": rate}

    @property
    def headers(self):
        return {"x-api-key": self.api_key}

    def generate_video(self, prompt, output_path, timeout=1500, resume_uuid=None, on_created=None, on_stage=None, reference_images=None):
        # Every ordinary beat is an independent text-to-video request.  A small
        # number of presenter-continuity beats may include the *source presenter*
        # under SnapGen's documented `ref_images` field.  We never feed a prior
        # generated scene back into Veo, which is what caused repeated framing in
        # the old pipeline.
        deadline = time.monotonic() + max(0.0, float(timeout))
        multipart = [
            ("prompt", (None, prompt)),
            ("model", (None, "veo-3.1-fast")),
            ("resolution", (None, "720p")),
            ("duration", (None, "8")),
            ("aspect_ratio", (None, "16:9")),
        ]
        attached_references = 0
        for reference in list(reference_images or [])[:3]:
            reference = Path(reference)
            if not reference.exists() or reference.stat().st_size <= 0:
                continue
            mime = mimetypes.guess_type(reference.name)[0] or "image/jpeg"
            multipart.append(("ref_images", (reference.name, reference.read_bytes(), mime)))
            attached_references += 1
        if attached_references:
            # SnapGen defaults references to frame mode (start/end frames).
            # Ingredient mode is the documented semantic for carrying a person,
            # object or style into the generated scene.
            multipart.append(("mode_image", (None, "ingredient")))
        conversion_uuid = str(resume_uuid or "").strip()
        if not conversion_uuid:
            self.request_gate.acquire(timeout=_remaining_timeout(deadline, 120))
            try:
                self.circuit_breaker.before_request()
                try:
                    response = requests.post(
                        "https://api.snapgen.ai/uapi/v1/video-gen/veo",
                        headers={**self.headers, "Accept": "application/json"},
                        files=multipart,
                        timeout=_remaining_timeout(deadline, 120),
                    )
                    if not response.ok:
                        retry_after = None
                        try:
                            retry_after = float(response.headers.get("Retry-After"))
                        except (AttributeError, TypeError, ValueError):
                            pass
                        raise ProviderHTTPError(
                            response.status_code, response.text[:800], retry_after=retry_after,
                        )
                    created = response.json()
                except requests.RequestException as error:
                    self.circuit_breaker.record_failure()
                    raise ProviderError(str(error)) from error
                except ProviderHTTPError as error:
                    if _is_transient_provider_error(error):
                        self.circuit_breaker.record_failure()
                    raise
                except ValueError as error:
                    self.circuit_breaker.record_failure()
                    raise ProviderError("SnapGen devolvió una respuesta que no era JSON.") from error
                else:
                    self.circuit_breaker.record_success()
            finally:
                self.request_gate.release()
            payload = created.get("data") if isinstance(created.get("data"), dict) else created
            conversion_uuid = str(payload.get("uuid") or "").strip()
            if not conversion_uuid:
                raise ProviderError(f"GeminiGen no devolvió UUID: {str(created)[:700]}")
            with self._spend_lock:
                self.spent_usd += self.ESTIMATED_CLIP_USD
            if on_created:
                on_created(conversion_uuid)
        if on_stage:
            on_stage("polling")
        last_percentage = 0
        poll_errors = 0
        download_errors = 0
        download_deadline = None
        while time.monotonic() < deadline:
            try:
                response = requests.get(
                    f"https://api.snapgen.ai/uapi/v1/history/{urllib.parse.quote(str(conversion_uuid))}",
                    headers={**self.headers, "Accept": "application/json"},
                    timeout=_remaining_timeout(deadline, 60),
                )
                if not response.ok:
                    # Authentication errors cannot recover. Temporary provider,
                    # proxy and rate-limit responses must keep polling this same
                    # paid UUID instead of accidentally purchasing another clip.
                    if response.status_code in {401, 403}:
                        raise PaidAssetRecoveryError(
                            f"No se pudo consultar el clip ya generado (HTTP {response.status_code})."
                        )
                    retry_after = None
                    try:
                        raw_retry_after = response.headers.get("Retry-After")
                        retry_after = float(raw_retry_after) if raw_retry_after is not None else None
                    except (AttributeError, TypeError, ValueError):
                        pass
                    raise ProviderHTTPError(
                        response.status_code, response.text[:300], retry_after=retry_after,
                    )
                status_json = response.json()
                status = status_json.get("data") if isinstance(status_json.get("data"), dict) else status_json
                if not isinstance(status, dict) or not status:
                    raise ValueError("respuesta de estado vacía")
                poll_errors = 0
            except PaidAssetRecoveryError:
                raise
            except ProviderHTTPError as error:
                poll_errors += 1
                active_deadline = download_deadline or deadline
                if time.monotonic() >= active_deadline:
                    raise PaidAssetRecoveryError(
                        "El clip ya fue generado y pagado, pero su consulta sigue pendiente."
                    ) from error
                _sleep_before(active_deadline, _retry_delay(error, min(20, 4 + poll_errors * 2)))
                continue
            except (requests.RequestException, ValueError) as error:
                poll_errors += 1
                # SnapGen occasionally returns an empty/non-JSON page or a read
                # timeout while Veo is still working. The UUID remains valid.
                # Once generation completed, signed-URL refreshes are bounded by
                # a short, operation-specific delivery window.
                active_deadline = download_deadline or deadline
                if time.monotonic() >= active_deadline:
                    raise PaidAssetRecoveryError(
                        "El clip ya fue generado y pagado, pero su descarga sigue pendiente. "
                        "Vuelve a ejecutar el mismo vídeo para recuperarlo sin comprarlo otra vez."
                    ) from error
                _sleep_before(active_deadline, min(20, 4 + poll_errors * 2))
                continue
            last_percentage = int(status.get("status_percentage") or last_percentage)
            if int(status.get("status") or 0) == 2:
                if download_deadline is None:
                    # Generation is complete and paid. Do not let flaky object
                    # storage consume the whole per-clip generation timeout.
                    download_deadline = min(deadline, time.monotonic() + 240)
                videos = status.get("generated_video") or []
                valid = [item for item in videos if item.get("video_url") and int(item.get("status") or 2) == 2]
                if not valid:
                    if time.monotonic() < download_deadline:
                        _sleep_before(download_deadline, 5)
                        continue
                    raise PaidAssetRecoveryError(
                        "El clip ya fue generado y pagado, pero SnapGen todavía no publicó su archivo descargable."
                    )
                usable = [item for item in valid if int(item.get("has_watermark") or 0) == 0]
                if not usable:
                    raise RegeneratableError("GeminiGen devolvió un clip con marca de agua.")
                if on_stage:
                    on_stage("download_pending")
                destination = Path(output_path)
                partial = destination.with_suffix(destination.suffix + ".part")
                destination.parent.mkdir(parents=True, exist_ok=True)
                last_download_error = None
                for index, selected in enumerate(usable):
                    # Different provider outputs are different files. Keep a
                    # partial download only for the primary output across signed
                    # URL refreshes; never append it to an alternate output.
                    if index and partial.exists():
                        partial.unlink(missing_ok=True)
                    try:
                        signed_url = selected["video_url"]
                        parsed_media_url = urllib.parse.urlsplit(str(signed_url or ""))
                        r2_hostname = str(parsed_media_url.hostname or "").lower()
                        is_snapgen_r2 = (
                            parsed_media_url.scheme == "https"
                            and r2_hostname.endswith(".r2.cloudflarestorage.com")
                        )
                        if is_snapgen_r2 and _r2_direct_is_unreachable(r2_hostname):
                            _download_r2_via_snapgen_edge(
                                signed_url, partial, download_deadline
                            )
                        else:
                            media = None
                            try:
                                downloaded = partial.stat().st_size if partial.exists() else 0
                                headers = {"User-Agent": "VYT/1.0"}
                                if downloaded:
                                    headers["Range"] = f"bytes={downloaded}-"
                                remaining = _remaining_timeout(download_deadline, 240)
                                media = requests.get(
                                    signed_url, headers=headers,
                                    timeout=(min(15, remaining), min(30, remaining)), stream=True,
                                )
                                media.raise_for_status()
                                # A server may ignore Range and return the whole file.
                                append = bool(downloaded and media.status_code == 206)
                                with partial.open("ab" if append else "wb") as output:
                                    for chunk in media.iter_content(chunk_size=1024 * 1024):
                                        if time.monotonic() >= download_deadline:
                                            raise requests.Timeout("descarga fuera del tiempo máximo")
                                        if chunk:
                                            output.write(chunk)
                            except (
                                requests.exceptions.ConnectTimeout,
                                requests.exceptions.ConnectionError,
                            ):
                                # Some ISP routes to the R2 account endpoint can
                                # black-hole while SnapGen's other Cloudflare
                                # edges remain reachable. Preserve SNI, Host,
                                # certificate checks and the signed query while
                                # changing only the network entry point.
                                if not is_snapgen_r2:
                                    raise
                                _download_r2_via_snapgen_edge(signed_url, partial, download_deadline)
                            finally:
                                if media is not None:
                                    close = getattr(media, "close", None)
                                    if callable(close):
                                        close()
                        with partial.open("rb") as candidate:
                            header = candidate.read(12)
                        if partial.stat().st_size < 1000 or header[4:8] != b"ftyp":
                            raise requests.RequestException("GeminiGen no devolvió todavía un MP4 válido.")
                        partial.replace(destination)
                        return Path(output_path)
                    except (requests.RequestException, ProviderError, TimeoutError, OSError) as error:
                        last_download_error = error
                        download_errors += 1
                # Refresh `/history/<uuid>` to obtain a new signed R2 URL. This
                # always resumes the same paid UUID and never repeats the POST.
                if time.monotonic() < download_deadline:
                    _sleep_before(download_deadline, min(30, 4 + download_errors * 3))
                    continue
                raise PaidAssetRecoveryError(
                    "El almacenamiento de SnapGen no está accesible desde esta conexión. "
                    "VYT conserva el clip ya generado y pagado para recuperarlo en el próximo intento."
                ) from last_download_error
            if int(status.get("status") or 0) == 3:
                raise RegeneratableError(status.get("error_message") or "La generación de Veo falló.")
            _sleep_before(deadline, 8 if last_percentage < 80 else 5)
        raise PaidAssetRecoveryError(
            "El clip de Veo ya está identificado y pagado, pero sigue pendiente de entrega. "
            "Vuelve a ejecutar el mismo vídeo para recuperarlo sin comprarlo otra vez."
        )
