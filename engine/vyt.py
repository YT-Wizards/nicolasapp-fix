#!/usr/bin/env python3
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from media import (
    assemble, extract_presenter_reference_frames, extract_review_strip, extract_split_review_crop, probe, product_overlay_for_scene,
    product_qr_reminder_window, product_qr_window, render_segment, set_job_deadline, transcribe,
)
from planning import (
    build_schedule, build_story_bible, enforce_presenter_broll,
    force_avatar_window, image_fallback_scene, plan_scenes,
    rebalance_scenes_for_budget, review_scene_plan, stratified_generation_order,
    validate_scene_plan,
)
from prompts import IMAGE_REVIEW_PROMPT, PLANNER_SYSTEM, VIDEO_REVIEW_PROMPT, image_prompt, video_prompt
from providers import (
    AlgrowClient, GeminiGenClient, VercelGatewayClient,
    PaidAssetRecoveryError, ProviderError, ProviderTemporarilyUnavailableError,
    RegeneratableError,
)
from operations import OperationLedger, OperationRecoveryRequired


stop_requested = threading.Event()
ANALYSIS_CACHE_VERSION = "2026-08-28-budget-distribution-v6"
IMAGE_OPERATION_TIMEOUT = 10 * 60
VIDEO_OPERATION_TIMEOUT = 25 * 60
AI_OPERATION_TIMEOUT = 4 * 60


class QualityReviewError(RegeneratableError):
    """A completed paid asset was valid media but unsuitable editorially."""


class QualityReviewPendingError(PaidAssetRecoveryError):
    """Keep paid local media until a valid visual review is available."""


class BudgetExhaustedError(ProviderError):
    """The per-video cap rejected a new purchase before it reached a provider."""


def handle_stop(_signum, _frame):
    stop_requested.set()


signal.signal(signal.SIGTERM, handle_stop)
signal.signal(signal.SIGINT, handle_stop)


def review_passed(review):
    value = review.get("pass") if isinstance(review, dict) else False
    return value is True or str(value).strip().lower() == "true"


def review_score(review, key, default):
    try:
        value = review.get(key, default)
        score = float(value)
        return score if not isinstance(value, bool) and math.isfinite(score) and 0 <= score <= 100 else 0.0
    except (AttributeError, TypeError, ValueError):
        return 0.0


def validate_review_response(review, is_video=False):
    """An incomplete evaluation is unavailable, never a visual rejection."""
    keys = ["semantic_score", "realism_score", "integrity_score"]
    if is_video:
        keys += ["motion_score", "continuity_score"]
    valid = isinstance(review, dict) and isinstance(review.get("pass"), bool)
    if valid:
        for key in keys:
            value = review.get(key)
            try:
                score = float(value)
                valid = not isinstance(value, bool) and math.isfinite(score) and 0 <= score <= 100
            except (TypeError, ValueError):
                valid = False
            if not valid:
                break
    if is_video and valid:
        valid = isinstance(review.get("watermark"), bool)
    if not valid or review.get("review_unavailable"):
        raise ProviderError("La revisión visual devolvió datos incompletos o inválidos.")
    return review


def review_guidance(review, fallback):
    if isinstance(review, dict) and review.get("retry_guidance"):
        return str(review["retry_guidance"])
    issues = review.get("issues") if isinstance(review, dict) else None
    if isinstance(issues, list):
        text = "; ".join(str(item) for item in issues if str(item).strip())
        if text:
            return text
    return fallback


def safe_paid_recovery_reason(error, provider):
    """Explain a resumable paid-resource failure without exposing signed URLs."""
    message = " ".join(str(error or "").split())
    message = re.sub(r"https?://\S+", "[dirección protegida]", message, flags=re.IGNORECASE)
    message = re.sub(
        r"(?i)(authorization|bearer|x-api-key|x-amz-[a-z-]+)\s*[:=]\s*[^\s,;]+",
        r"\1=[protegido]",
        message,
    )
    message = (message or "el proveedor no respondió correctamente")[:320]
    prefix = "" if message.lower().startswith(str(provider).lower()) else f"{provider}: "
    return (
        f"{prefix}{message}. VYT conserva el identificador pagado y no compró un reemplazo."
    )


def minimum_required_videos(requested):
    requested = max(0, int(requested or 0))
    return max(1, math.ceil(requested * 0.70)) if requested else 0


class Pipeline:
    def __init__(self, config):
        self.config = config
        self.started = time.monotonic()
        # A long production must be allowed to finish.  Individual provider and
        # local-media operations still have their own finite timeouts, and the
        # Cancel button is handled independently through ``stop_requested``.
        self.deadline = None
        set_job_deadline(None)
        self.workspace = Path(tempfile.mkdtemp(prefix=f"VYT-{config['id'][:8]}-"))
        self.assets = self.workspace / "assets"
        self.segments = self.workspace / "segments"
        self.assets.mkdir(); self.segments.mkdir()
        self.media_cost = 0.0
        self.media_lock = threading.Lock()
        self.reserved_usd = 0.0
        self.last_event = {}
        self.failure_lock = threading.Lock()
        self.failures = []
        self.generated_counts = {"video": 0, "image": 0, "avatar": 0, "split": 0, "still": 0}
        self.checkpoint_path = None
        self.asset_cache_dir = None
        self.checkpoint = {}
        self.checkpoint_lock = threading.RLock()
        database_root = Path(config.get("user_data_dir") or Path.home() / "Library" / "Application Support" / "vyt")
        self.operation_ledger = OperationLedger(database_root / "vyt.sqlite")
        self.prior_cost_breakdown = {"video": 0.0, "image": 0.0, "analysis": 0.0, "review": 0.0}
        self.presenter_reference_images = []
        self.presenter_reference = None
        self.recovering_paid_assets = False
        gateway_key = os.environ.get("VYT_GATEWAY_KEY", "")
        self.director = VercelGatewayClient(
            gateway_key,
            "anthropic/claude-sonnet-5",
            provider_order=["anthropic", "vertex", "bedrock", "claudeaws"],
            reserve_callback=self.reserve,
            release_callback=self.release,
            remaining_callback=self.remaining_time,
            usage_callback=self.persist_ai_usage,
        )
        self.reviewer = VercelGatewayClient(
            gateway_key,
            "google/gemini-2.5-flash",
            provider_order=["google", "vertex"],
            reserve_callback=self.reserve,
            release_callback=self.release,
            remaining_callback=self.remaining_time,
            usage_callback=self.persist_ai_usage,
        )
        self.algrow = AlgrowClient(os.environ.get("VYT_ALGROW_KEY", ""))
        self.geminigen = GeminiGenClient(os.environ.get("VYT_GEMINIGEN_KEY", ""))

    @property
    def current_spent(self):
        return self.algrow.spent_usd + self.geminigen.spent_usd + self.director.spent_usd + self.reviewer.spent_usd

    @property
    def total_spent(self):
        return sum(self.prior_cost_breakdown.values()) + self.current_spent

    def current_cost_breakdown(self):
        return {
            "video": round(self.geminigen.spent_usd, 4),
            "image": round(self.algrow.spent_usd, 4),
            "analysis": round(self.director.spent_usd, 4),
            "review": round(self.reviewer.spent_usd, 4),
        }

    def cost_breakdown(self):
        current = self.current_cost_breakdown()
        return {
            key: round(float(self.prior_cost_breakdown.get(key, 0)) + float(current.get(key, 0)), 4)
            for key in ("video", "image", "analysis", "review")
        }

    def record_failure(self, scene, stage, error):
        with self.failure_lock:
            self.failures.append({
                "id": scene.get("id", ""),
                "requested_type": scene.get("requested_type", scene.get("type", "")),
                "stage": stage,
                "error": str(error)[:500],
            })

    def event(self, progress, phase, detail="", status="running", estimate=None, units_done=None, units_total=None):
        elapsed = time.monotonic() - self.started
        eta = None
        if units_done and units_total and units_done > 0:
            eta = max(0, elapsed / units_done * (units_total - units_done))
        elif progress > 2:
            eta = max(0, elapsed / progress * (100 - progress))
        payload = {
            "progress": round(progress, 1), "phase": phase, "detail": detail, "status": status,
            "spent_usd": round(self.total_spent, 4), "estimate_usd": round(estimate or 0, 4),
            "eta_seconds": round(eta) if eta is not None else None,
        }
        self.last_event = payload
        print("VYT_EVENT:" + json.dumps(payload, ensure_ascii=False), flush=True)

    def check_stop(self):
        if stop_requested.is_set():
            raise InterruptedError("Producción cancelada.")

    def remaining_time(self, operation_timeout=AI_OPERATION_TIMEOUT):
        """Return a finite timeout for one operation, never a job-wide limit."""
        return max(0.05, float(operation_timeout))

    def persist_ai_usage(self, _amount=0.0):
        # Persist every billed AI response immediately. A crash after a review
        # can no longer forget that charge and overspend on the next resume.
        self.operation_ledger.record_cost(self.config["id"], "charged", _amount, provider="ai-gateway")
        if self.checkpoint_path:
            self.save_checkpoint()

    def reserve(self, amount):
        with self.media_lock:
            if self.total_spent + self.reserved_usd + amount > float(self.config["max_cost_usd"]):
                raise BudgetExhaustedError("El límite de coste ha sido alcanzado.")
            self.reserved_usd += amount
        self.operation_ledger.record_cost(self.config["id"], "reserved", amount, provider="budget")

    def release(self, amount):
        with self.media_lock:
            self.reserved_usd = max(0.0, self.reserved_usd - amount)
        self.operation_ledger.record_cost(self.config["id"], "released", amount, provider="budget")

    def find_model(self):
        configured_model = os.environ.get("VYT_WHISPER_MODEL", "").strip()
        candidates = ([Path(configured_model).expanduser()] if configured_model else []) + [
            Path(self.config["root_dir"]) / "assets" / "models" / "ggml-base.bin",
            Path(self.config["root_dir"]) / "assets" / "models" / "ggml-small.bin",
            Path(self.config["root_dir"]) / "assets" / "models" / "ggml-small.en.bin",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise RuntimeError("Falta el modelo local de transcripción de VYT.")

    def prepare_checkpoint(self, source, duration):
        source = Path(source).resolve()
        stat = source.stat()
        product_sale = self.config.get("product_sale") or {}
        # The card is a render-only overlay. Electron intentionally copies it to
        # a new temporary path for every job, so path/mtime/content must not
        # invalidate paid analysis or media. Enabled/disabled remains part of the
        # identity because it changes the avatar windows used by the edit.
        identity_payload = {
            "version": ANALYSIS_CACHE_VERSION,
            "source": str(source),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "duration": round(float(duration), 3),
            "branding": bool(self.config.get("branding")),
            "max_cost_usd": round(float(self.config.get("max_cost_usd") or 0), 4),
            "product_sale": {
                "enabled": bool(product_sale.get("enabled")),
                # Retain the old `card: null` shape so non-QR checkpoints keep
                # their existing identity while QR jobs become stable.
                "card": None,
            },
        }
        identity = json.dumps(identity_payload, sort_keys=True).encode("utf-8")
        user_data = self.config.get("user_data_dir")
        cache_dir = (Path(user_data) if user_data else Path.home() / "Library" / "Application Support" / "vyt") / "checkpoints"
        cache_dir.mkdir(parents=True, exist_ok=True)
        stale_before = time.time() - 14 * 24 * 60 * 60
        for stale_json in cache_dir.glob("*.json"):
            try:
                if stale_json.stat().st_mtime < stale_before:
                    stale_json.unlink(missing_ok=True)
                    shutil.rmtree(cache_dir / f"{stale_json.stem}.assets", ignore_errors=True)
            except OSError:
                pass
        cache_key = hashlib.sha256(identity).hexdigest()
        self.checkpoint_path = cache_dir / f"{cache_key}.json"
        self.asset_cache_dir = cache_dir / f"{cache_key}.assets"
        self.asset_cache_dir.mkdir(parents=True, exist_ok=True)
        # Paid media lives beside its checkpoint until the final video passes QA.
        # The disposable workspace remains reserved for review strips and renders.
        self.assets = self.asset_cache_dir
        self.segments = self.asset_cache_dir / "segments"
        self.segments.mkdir(parents=True, exist_ok=True)
        try:
            loaded = json.loads(self.checkpoint_path.read_text()) if self.checkpoint_path.exists() else {}
            self.checkpoint = loaded if loaded.get("version") == ANALYSIS_CACHE_VERSION else {}
        except (OSError, json.JSONDecodeError):
            self.checkpoint = {}
        self.checkpoint["version"] = ANALYSIS_CACHE_VERSION
        self.checkpoint["checkpoint_identity"] = identity_payload
        saved_costs = self.checkpoint.get("cumulative_cost_breakdown")
        if isinstance(saved_costs, dict):
            self.prior_cost_breakdown = {
                key: max(0.0, float(saved_costs.get(key) or 0))
                for key in ("video", "image", "analysis", "review")
            }

    def save_checkpoint(self, **values):
        if not self.checkpoint_path:
            return
        with self.checkpoint_lock:
            self.checkpoint.update(values)
            self.checkpoint["cumulative_cost_breakdown"] = self.cost_breakdown()
            # Four media workers may finish at almost the same instant. Each
            # writer gets its own temporary path and the final rename is atomic;
            # one worker can never remove another worker's temporary file.
            temporary = self.checkpoint_path.with_name(
                f".{self.checkpoint_path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                temporary.write_text(json.dumps(self.checkpoint, ensure_ascii=False))
                temporary.replace(self.checkpoint_path)
            finally:
                temporary.unlink(missing_ok=True)

    def clear_checkpoint(self):
        if self.checkpoint_path:
            self.checkpoint_path.unlink(missing_ok=True)
        if self.asset_cache_dir:
            shutil.rmtree(self.asset_cache_dir, ignore_errors=True)

    def ensure_no_pending_paid_jobs(self):
        """Refuse a successful cleanup while a paid remote asset is recoverable."""
        reviews = self.checkpoint.get("asset_reviews") or {}
        if any(isinstance(record, dict) and record.get("status") == "pending" for record in reviews.values()):
            raise QualityReviewPendingError("Quedan recursos pagados pendientes de revisión. VYT los conserva; reanuda el mismo vídeo para continuar.")
        pending = []
        for key in ("pending_video_jobs", "pending_image_jobs"):
            records = self.checkpoint.get(key)
            if isinstance(records, dict):
                pending.extend(str(item) for item, remote_id in records.items() if remote_id)
        if pending:
            raise PaidAssetRecoveryError(
                "Quedan recursos ya generados y pagados pendientes de descarga "
                f"({', '.join(sorted(set(pending))[:5])}). Vuelve a ejecutar el mismo vídeo para recuperarlos."
            )

    @staticmethod
    def asset_fingerprint(path):
        path = Path(path)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "file": path.name,
            "size": path.stat().st_size,
            "sha256": digest.hexdigest(),
        }

    def cached_asset_for(self, scene):
        with self.checkpoint_lock:
            completed = self.checkpoint.get("completed_assets")
            if not isinstance(completed, dict):
                completed = {}
            indexed_record = completed.get(scene["id"])
            candidates = []
            if isinstance(indexed_record, dict) and indexed_record.get("file"):
                candidates.append(self.assets / str(indexed_record["file"]))
            preferred = ".mp4" if scene["type"] == "video" else ".png"
            candidates.extend((self.assets / f"{scene['id']}{preferred}", self.assets / f"{scene['id']}.png", self.assets / f"{scene['id']}.mp4"))
            path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
            # Structural validation alone does not establish visual quality.
            indexed = scene["id"] in completed
            if not indexed and not path.exists():
                return None
            try:
                if not path.exists() or path.stat().st_size < 1000:
                    raise ValueError("archivo incompleto")
                info = probe(path)
                if info["width"] <= 0 or info["height"] <= 0:
                    raise ValueError("recurso sin imagen válida")
                recovered_type = "video" if path.suffix.lower() == ".mp4" else "image"
                if recovered_type == "video" and info["duration"] < 1.0:
                    raise ValueError("clip demasiado corto")
                scene["type"] = recovered_type if scene["type"] in {"video", "image"} else scene["type"]
            except Exception:
                path.unlink(missing_ok=True)
                completed.pop(scene["id"], None)
                reviews = self.checkpoint.get("asset_reviews") or {}
                reviews.pop(scene["id"], None)
                self.save_checkpoint(completed_assets=completed, asset_reviews=reviews)
                return None
            receipt = self.checkpoint.get("asset_reviews", {}).get(scene["id"])
            if recovered_type == "video":
                self.clear_pending_video_job(scene)
            else:
                self.clear_pending_image_job(scene)
            # Legacy completed records remain reusable. New/unindexed resources
            # must have an approval bound to the exact downloaded file.
            legacy_approved = indexed and receipt is None
            stamp = self.asset_fingerprint(path)
            approved = (
                isinstance(receipt, dict)
                and receipt.get("status") == "passed"
                and receipt.get("file") == stamp["file"]
                and receipt.get("size") == stamp["size"]
                and receipt.get("sha256") == stamp["sha256"]
            )
            if not legacy_approved and not approved:
                try:
                    self.review_asset(scene, path)
                except QualityReviewError:
                    scene.update(image_fallback_scene(scene))
                    completed.pop(scene["id"], None)
                    self.save_checkpoint(completed_assets=completed)
                    return None
                except RegeneratableError:
                    completed.pop(scene["id"], None)
                    self.save_checkpoint(completed_assets=completed)
                    return None
            completed[scene["id"]] = {"type": recovered_type, "file": path.name, "recovered": True}
            self.save_checkpoint(completed_assets=completed)
            return path

    def record_asset_review(self, scene, path, status, warnings=None):
        path = Path(path)
        try:
            with self.checkpoint_lock:
                records = self.checkpoint.setdefault("asset_reviews", {})
                records[scene["id"]] = {
                    **self.asset_fingerprint(path),
                    "status": status,
                }
                if warnings:
                    records[scene["id"]]["warnings"] = list(warnings)
                self.save_checkpoint(asset_reviews=records)
        except Exception as error:
            raise QualityReviewPendingError("El recurso pagado se conserva, pero no se pudo guardar su revisión. Comprueba el espacio disponible antes de reanudar.") from error

    def review_asset(self, scene, path):
        """Review paid local media; outages preserve it for a review-only resume."""
        is_video = Path(path).suffix.lower() == ".mp4"
        try:
            self.record_asset_review(scene, path, "pending")
            if is_video:
                self.clear_pending_video_job(scene)
            else:
                self.clear_pending_image_job(scene)
            review = self.review_video(scene, path) if is_video else self.review_image(scene, path)
            validate_review_response(review, is_video=is_video)
        except QualityReviewPendingError:
            raise
        except Exception as error:
            raise QualityReviewPendingError("El recurso pagado está guardado y pendiente de revisión. Reanuda el mismo vídeo para continuar sin regenerarlo.") from error
        passed = review_passed(review)
        thresholds = {"semantic_score": 70, "realism_score": 65 if is_video else 68, "integrity_score": 75}
        if is_video:
            thresholds.update(motion_score=65, continuity_score=75)
        hard_failure = bool(
            (is_video and review.get("watermark") is True)
            or review.get("safety_violation") is True
            or review_score(review, "integrity_score", 0) < 50
        )
        soft_warnings = []
        if not passed:
            soft_warnings.append("AI review did not fully approve the editorial match")
        for key, minimum in thresholds.items():
            if review_score(review, key, 0) < minimum:
                soft_warnings.append(f"{key} below preferred threshold")
        if hard_failure:
            self.record_asset_review(scene, path, "rejected")
            Path(path).unlink(missing_ok=True)
            error_type = QualityReviewError if is_video else RegeneratableError
            raise error_type("Revisión rechazada: " + review_guidance(review, "use a simple, literal, ordinary real-world shot"))
        try:
            self.record_asset_review(scene, path, "passed", soft_warnings)
        except Exception as error:
            raise QualityReviewPendingError("El recurso pagado se conserva, pero no se pudo guardar su aprobación. Reanuda sin regenerarlo.") from error
        return path

    def protect_local_paid_assets(self, scenes, planned_by_id):
        """Local downloads, including crash-orphans, are not new media purchases."""
        paid_ids = set()
        for scene in scenes:
            scene_id = str(scene.get("id") or "")
            receipt = (self.checkpoint.get("asset_reviews") or {}).get(scene_id) or {}
            record = (self.checkpoint.get("completed_assets") or {}).get(scene_id) or {}
            names = [receipt.get("file"), record.get("file"), f"{scene_id}.png", f"{scene_id}.mp4"]
            path = next((self.assets / name for name in names if name and (self.assets / name).is_file()), None)
            if not path:
                continue
            paid_ids.add(scene_id)
            if scene.get("type") == "avatar":
                original = planned_by_id.get(scene_id) or {}
                scene.update(original)
                scene["type"] = "video" if path.suffix.lower() == ".mp4" else (original.get("type") if original.get("type") in {"image", "split", "still"} else "image")
        return paid_ids

    def mark_asset_completed(self, scene, asset):
        with self.checkpoint_lock:
            completed = self.checkpoint.get("completed_assets")
            if not isinstance(completed, dict):
                completed = {}
            completed[scene["id"]] = {
                "type": scene["type"],
                "file": Path(asset).name,
            }
            self.save_checkpoint(completed_assets=completed)

    def pending_video_job_for(self, scene):
        with self.checkpoint_lock:
            pending = self.checkpoint.get("pending_video_jobs")
            if not isinstance(pending, dict):
                return ""
            return str(pending.get(scene["id"]) or "")

    def save_pending_video_job(self, scene, conversion_uuid):
        with self.checkpoint_lock:
            pending = self.checkpoint.get("pending_video_jobs")
            if not isinstance(pending, dict):
                pending = {}
            pending[scene["id"]] = str(conversion_uuid)
            self.save_checkpoint(pending_video_jobs=pending)

    def clear_pending_video_job(self, scene):
        with self.checkpoint_lock:
            pending = self.checkpoint.get("pending_video_jobs")
            if not isinstance(pending, dict) or scene["id"] not in pending:
                return
            pending.pop(scene["id"], None)
            self.save_checkpoint(pending_video_jobs=pending)

    def pending_image_job_for(self, scene):
        with self.checkpoint_lock:
            pending = self.checkpoint.get("pending_image_jobs")
            if not isinstance(pending, dict):
                return ""
            return str(pending.get(scene["id"]) or "")

    def save_pending_image_job(self, scene, job_id):
        with self.checkpoint_lock:
            pending = self.checkpoint.get("pending_image_jobs")
            if not isinstance(pending, dict):
                pending = {}
            pending[scene["id"]] = str(job_id)
            self.save_checkpoint(pending_image_jobs=pending)

    def clear_pending_image_job(self, scene):
        with self.checkpoint_lock:
            pending = self.checkpoint.get("pending_image_jobs")
            if not isinstance(pending, dict) or scene["id"] not in pending:
                return
            pending.pop(scene["id"], None)
            self.save_checkpoint(pending_image_jobs=pending)

    def review_image(self, scene, path):
        prompt = IMAGE_REVIEW_PROMPT.format(
            narration=scene["narration"], subject=scene.get("literal_subject", ""), reject_if=json.dumps(scene.get("reject_if", []), ensure_ascii=False)
        )
        review_path = Path(path)
        temporary_preview = None
        if scene.get("type") == "split":
            try:
                temporary_preview = extract_split_review_crop(
                    path, self.workspace / f"review-split-{scene['id']}.jpg"
                )
                review_path = temporary_preview
            except Exception as error:
                self.record_failure(scene, "split_review_crop", error)
        last_error = None
        response_schema = {
            "type": "object",
            "properties": {
                "pass": {"type": "boolean"},
                "semantic_score": {"type": "number", "minimum": 0, "maximum": 100},
                "realism_score": {"type": "number", "minimum": 0, "maximum": 100},
                "integrity_score": {"type": "number", "minimum": 0, "maximum": 100},
                "issues": {"type": "array", "items": {"type": "string"}},
                "retry_guidance": {"type": "string"},
            },
            "required": ["pass", "semantic_score", "realism_score", "integrity_score", "issues", "retry_guidance"],
            "additionalProperties": False,
        }
        try:
            try:
                return validate_review_response(self.reviewer.chat_json(PLANNER_SYSTEM, prompt, images=[review_path], max_tokens=1600, response_schema=response_schema))
            except Exception as error:
                last_error = error
            try:
                result = validate_review_response(self.director.chat_json(PLANNER_SYSTEM, prompt, images=[review_path], max_tokens=1400, response_schema=response_schema))
                self.record_failure(scene, "image_review_fallback", last_error)
                return result
            except Exception as fallback_error:
                last_error = fallback_error
            # The asset already exists and has been paid for. A temporary JSON/API
            # failure in the reviewer must not trigger another image purchase.
            self.record_failure(scene, "image_review_unavailable", last_error)
            raise QualityReviewPendingError("La imagen pagada está guardada, pero su revisión no está disponible. Reanuda el mismo vídeo para revisarla sin regenerarla.") from last_error
        finally:
            if temporary_preview:
                temporary_preview.unlink(missing_ok=True)

    def review_video(self, scene, path):
        try:
            strip = extract_review_strip(
                path, self.workspace / f"review-{scene['id']}.jpg",
                visible_duration=scene.get("duration"),
            )
        except Exception as error:
            self.record_failure(scene, "video_review_strip", error)
            raise QualityReviewPendingError("El clip pagado está guardado, pero no se pudo preparar su revisión. Reanuda el mismo vídeo sin regenerarlo.") from error
        prompt = VIDEO_REVIEW_PROMPT.format(
            narration=scene["narration"], subject=scene.get("literal_subject", ""),
            reject_if=json.dumps(scene.get("reject_if", []), ensure_ascii=False),
            presenter_reuse=(
                "yes — compare the first attached identity reference with the generated strip"
                if scene.get("presenter_broll") and self.presenter_reference else "no"
            ),
        )
        review_images = [strip]
        if scene.get("presenter_broll") and self.presenter_reference:
            review_images = [self.presenter_reference, strip]
        last_error = None
        response_schema = {
            "type": "object",
            "properties": {
                "pass": {"type": "boolean"},
                "semantic_score": {"type": "number", "minimum": 0, "maximum": 100},
                "realism_score": {"type": "number", "minimum": 0, "maximum": 100},
                "integrity_score": {"type": "number", "minimum": 0, "maximum": 100},
                "motion_score": {"type": "number", "minimum": 0, "maximum": 100},
                "continuity_score": {"type": "number", "minimum": 0, "maximum": 100},
                "watermark": {"type": "boolean"},
                "issues": {"type": "array", "items": {"type": "string"}},
                "retry_guidance": {"type": "string"},
            },
            "required": ["pass", "semantic_score", "realism_score", "integrity_score", "motion_score", "continuity_score", "watermark", "issues", "retry_guidance"],
            "additionalProperties": False,
        }
        try:
            try:
                return validate_review_response(self.reviewer.chat_json(PLANNER_SYSTEM, prompt, images=review_images, max_tokens=1800, response_schema=response_schema), is_video=True)
            except Exception as error:
                last_error = error
            try:
                result = validate_review_response(self.director.chat_json(PLANNER_SYSTEM, prompt, images=review_images, max_tokens=1600, response_schema=response_schema), is_video=True)
                self.record_failure(scene, "video_review_fallback", last_error)
                return result
            except Exception as fallback_error:
                last_error = fallback_error
            self.record_failure(scene, "video_review_unavailable", last_error)
            raise QualityReviewPendingError("El clip pagado está guardado, pero su revisión no está disponible. Reanuda el mismo vídeo para revisarlo sin regenerarlo.") from last_error
        finally:
            strip.unlink(missing_ok=True)

    def generate_image_scene(self, scene, retry_guidance=""):
        output = self.assets / f"{scene['id']}.png"
        prompt = image_prompt(scene, retry_guidance)
        operation_key = self.operation_ledger.operation_key(
            self.config["id"], scene["id"], "image", "algrow", prompt,
            {"model": "gpt-image-2", "aspect_ratio": "16:9", "fast": False},
        )
        try:
            resume_job_id = self.pending_image_job_for(scene) or self.operation_ledger.prepare(
                operation_key, self.config["id"], scene["id"], "algrow", "image",
                self.operation_ledger.prompt_hash(prompt),
            )
        except OperationRecoveryRequired as error:
            raise PaidAssetRecoveryError(str(error)) from error
        if resume_job_id:
            self.operation_ledger.record_cost(
                self.config["id"], "avoided_duplicate", 0.35 * self.algrow.CREDIT_USD,
                provider="algrow", operation_key=operation_key,
            )
        estimated = 0.0 if resume_job_id else 0.35 * self.algrow.CREDIT_USD
        charged_before = self.algrow.spent_usd
        self.reserve(estimated)
        try:
            try:
                output, _remote_url = self.algrow.generate_image(
                    prompt,
                    output,
                    timeout=self.remaining_time(IMAGE_OPERATION_TIMEOUT),
                    resume_job_id=resume_job_id,
                    on_created=lambda job_id: (
                        self.operation_ledger.mark_submitted(operation_key, job_id),
                        self.save_pending_image_job(scene, job_id),
                    ),
                    on_stage=lambda stage: self.operation_ledger.mark_polling(operation_key)
                    if stage == "polling" else self.operation_ledger.mark_download_pending(operation_key),
                )
            except ProviderTemporarilyUnavailableError as error:
                # No paid POST was accepted. Clear the prepared ledger row so
                # the next retry may safely submit the same idempotent operation.
                if not resume_job_id:
                    self.operation_ledger.mark_failed(operation_key, str(error))
                raise
            except RegeneratableError:
                # Algrow explicitly marked this job terminal. Only now is it safe
                # for the normal one-retry path to purchase a replacement image.
                self.clear_pending_image_job(scene)
                self.operation_ledger.mark_failed(operation_key, "provider terminal failure")
                raise
        finally:
            self.release(estimated)
        charged = max(0.0, self.algrow.spent_usd - charged_before)
        self.operation_ledger.record_cost(
            self.config["id"], "charged", charged, provider="algrow", operation_key=operation_key,
        )
        self.operation_ledger.mark_completed(operation_key, _remote_url)
        return self.review_asset(scene, output)

    def generate_video_scene(self, scene, retry_guidance=""):
        output = self.assets / f"{scene['id']}.mp4"
        prompt = video_prompt(scene, retry_guidance)
        operation_key = self.operation_ledger.operation_key(
            self.config["id"], scene["id"], "video", "snapgen", prompt,
            {"model": "veo-3.1-fast", "resolution": "720p", "duration": "8", "aspect_ratio": "16:9"},
        )
        try:
            resume_uuid = self.pending_video_job_for(scene) or self.operation_ledger.prepare(
                operation_key, self.config["id"], scene["id"], "snapgen", "video",
                self.operation_ledger.prompt_hash(prompt),
            )
        except OperationRecoveryRequired as error:
            raise PaidAssetRecoveryError(str(error)) from error
        if resume_uuid:
            self.operation_ledger.record_cost(
                self.config["id"], "avoided_duplicate", self.geminigen.ESTIMATED_CLIP_USD,
                provider="snapgen", operation_key=operation_key,
            )
        estimated = 0.0 if resume_uuid else self.geminigen.ESTIMATED_CLIP_USD
        charged_before = self.geminigen.spent_usd
        self.reserve(estimated)
        try:
            try:
                self.geminigen.generate_video(
                    prompt,
                    output,
                    timeout=self.remaining_time(VIDEO_OPERATION_TIMEOUT),
                    resume_uuid=resume_uuid,
                    on_created=lambda conversion_uuid: (
                        self.operation_ledger.mark_submitted(operation_key, conversion_uuid),
                        self.save_pending_video_job(scene, conversion_uuid),
                    ),
                    on_stage=lambda stage: self.operation_ledger.mark_polling(operation_key)
                    if stage == "polling" else self.operation_ledger.mark_download_pending(operation_key),
                    idempotency_key=operation_key,
                    reference_images=(
                        [self.presenter_reference]
                        if scene.get("presenter_broll") and self.presenter_reference else None
                    ),
                )
            except ProviderTemporarilyUnavailableError as error:
                if not resume_uuid:
                    self.operation_ledger.mark_failed(operation_key, str(error))
                raise
            except RegeneratableError:
                # Google explicitly failed this UUID; only this case permits the
                # quality retry to purchase a fresh generation.
                self.clear_pending_video_job(scene)
                self.operation_ledger.mark_failed(operation_key, "provider terminal failure")
                raise
        finally:
            self.release(estimated)
        charged = max(0.0, self.geminigen.spent_usd - charged_before)
        self.operation_ledger.record_cost(
            self.config["id"], "charged", charged, provider="snapgen", operation_key=operation_key,
        )
        self.operation_ledger.mark_completed(operation_key, resume_uuid)
        return self.review_asset(scene, output)

    def generate_one(self, scene):
        self.check_stop()
        media_type = scene["type"]
        if media_type == "avatar":
            return None
        recovering_paid_image = bool(self.pending_image_job_for(scene))
        if recovering_paid_image and media_type == "video":
            # A prior run may have reached the paid still-image fallback after a
            # Veo failure while the reviewed plan still says "video". Resume that
            # exact paid Algrow job before considering any new video purchase.
            scene.update(image_fallback_scene(scene))
            media_type = "image"
        recovering_paid_video = media_type == "video" and bool(self.pending_video_job_for(scene))
        recovery_only = self.recovering_paid_assets and (recovering_paid_video or recovering_paid_image)
        if not recovering_paid_video and not recovering_paid_image and self.total_spent >= float(self.config["max_cost_usd"]) - 0.03:
            raise BudgetExhaustedError("El límite de coste ha sido alcanzado.")
        try:
            if media_type == "video":
                try:
                    return self.generate_video_scene(scene)
                except QualityReviewError as quality_error:
                    # The provider worked and the paid file was inspected. Buying
                    # another random motion clip is slow and often repeats the same
                    # physical mistake; move directly to the literal-image fallback.
                    self.record_failure(scene, "video_quality", quality_error)
                    if recovery_only:
                        raise
                    raise
                except RegeneratableError as first_error:
                    self.record_failure(scene, "video_first_attempt", first_error)
                    if recovery_only:
                        raise
                    self.check_stop()
                    # Purchase exactly one replacement only when Google/provider
                    # failed technically before a usable reviewed clip existed.
                    output = self.assets / f"{scene['id']}.mp4"
                    output.unlink(missing_ok=True)
                    try:
                        return self.generate_video_scene(scene, f"Previous attempt failed: {str(first_error)[:180]}. Make the shot simpler, literal and stable")
                    except BudgetExhaustedError:
                        raise
                    except Exception as second_error:
                        self.record_failure(scene, "video_retry", second_error)
                        raise
            try:
                return self.generate_image_scene(scene)
            except BudgetExhaustedError:
                raise
            except PaidAssetRecoveryError:
                raise
            except Exception as first_error:
                if recovery_only:
                    raise
                self.check_stop()
                output = self.assets / f"{scene['id']}.png"
                output.unlink(missing_ok=True)
                return self.generate_image_scene(scene, f"Previous attempt failed: {str(first_error)[:180]}. Make the subject more literal and the frame more ordinary")
        except BudgetExhaustedError:
            # Budget is not a provider/quality failure. Never buy a retry or a
            # fallback after this point; the caller applies the reviewed plan's
            # evenly distributed free fallback.
            raise
        except ProviderTemporarilyUnavailableError:
            raise
        except PaidAssetRecoveryError:
            # The remote UUID/job_id is already paid. Never mutate this scene,
            # buy an image fallback or let the renderer turn it into avatar.
            raise
        except Exception as error:
            if recovery_only:
                # Recovery is a read of an existing paid remote job.  A terminal
                # or QA failure may clear that identifier, but replacements are
                # considered only after every other paid job has been recovered.
                raise
            self.check_stop()
            if media_type == "video":
                # A failed motion shot must not silently inflate presenter time.
                # Preserve the exact beat as a literal reviewed image instead.
                stage = "video_quality" if isinstance(error, RegeneratableError) else "video_provider"
                self.record_failure(scene, stage, error)
                fallback = image_fallback_scene(scene)
                scene.update(fallback)
                try:
                    asset = self.generate_image_scene(
                        scene,
                        "Veo could not produce this beat. Use one static, literal, physically ordinary factual frame",
                    )
                    self.record_failure(scene, "video_to_image_fallback", error)
                    return asset
                except BudgetExhaustedError:
                    raise
                except PaidAssetRecoveryError:
                    raise
                except Exception as fallback_error:
                    self.record_failure(scene, "video_fallback_image", fallback_error)
                    scene["type"] = "avatar"
                    return None
            self.record_failure(scene, "image", error)
            scene["type"] = "avatar"
            return None

    def run(self):
        source = Path(self.config["source"])
        if not source.exists():
            raise RuntimeError("No encuentro el vídeo fuente de HeyGen.")
        product_sale = self.config.get("product_sale") or {}
        product_card = None
        if product_sale.get("enabled"):
            product_card = Path(str(product_sale.get("card_path") or ""))
            if not product_card.exists() or product_card.stat().st_size < 500:
                raise RuntimeError("No encuentro la tarjeta del QR. Vuelve a seleccionar el QR antes de crear el vídeo.")
        info = probe(source)
        duration = min(info["duration"], float(self.config.get("test_seconds") or info["duration"]))
        if duration <= 1:
            raise RuntimeError("El vídeo fuente no tiene una duración válida.")
        self.prepare_checkpoint(source, duration)
        cached_transcript = self.checkpoint.get("transcript")
        if isinstance(cached_transcript, list) and cached_transcript:
            transcript = cached_transcript
            self.event(2, "Retomando análisis", "Narración y tiempos recuperados")
        else:
            self.event(2, "Analizando el vídeo", "Extrayendo narración y tiempos")
            transcript = transcribe(source, duration, self.workspace, self.find_model())
            self.save_checkpoint(transcript=transcript)
        # Candidate identity frames are cheap local extractions.  Recreate them
        # on every resume because the checkpoint stores no private face image and
        # the disposable workspace may have changed between app launches.
        self.presenter_reference_images = extract_presenter_reference_frames(
            source, duration, self.workspace / "presenter-references"
        )
        qr_window = product_qr_window(
            transcript, duration, product_sale.get("product_name", "")
        ) if product_card else None
        qr_windows = [qr_window] if qr_window else []
        self.check_stop()

        cached_bible = self.checkpoint.get("bible")
        if isinstance(cached_bible, dict) and cached_bible:
            bible = cached_bible
            self.event(8, "Retomando análisis", "Continuidad visual recuperada")
        else:
            self.event(8, "Entendiendo la historia", "Creando continuidad de personas, animales y lugares")
            try:
                bible = build_story_bible(
                    self.director, transcript, bool(self.config.get("branding")),
                    presenter_images=self.presenter_reference_images,
                )
            except ProviderError as error:
                if "json" not in str(error).lower() and "respuesta vacía" not in str(error).lower():
                    raise
                # A malformed Claude envelope must not discard transcription or
                # stop the project. Gemini can produce this small compact object.
                bible = build_story_bible(
                    self.reviewer, transcript, bool(self.config.get("branding")),
                    presenter_images=self.presenter_reference_images,
                )
            self.save_checkpoint(bible=bible)
        profile = bible.get("presenter_profile") if isinstance(bible, dict) else {}
        try:
            reference_index = min(3, max(1, int((profile or {}).get("reference_index") or 1)))
        except (TypeError, ValueError):
            reference_index = 1
        if self.presenter_reference_images:
            self.presenter_reference = self.presenter_reference_images[reference_index - 1]
        cached_beats = self.checkpoint.get("beats")
        if isinstance(cached_beats, list) and cached_beats and abs(float(cached_beats[-1].get("end") or 0) - duration) < 0.1:
            beats = cached_beats
            estimated_media = float(self.checkpoint.get("estimated_media") or 0)
        else:
            remaining_budget = max(0.0, float(self.config["max_cost_usd"]) - self.total_spent)
            beats, estimated_media = build_schedule(duration, transcript, remaining_budget, bible=bible)
        force_avatar_window(beats, qr_window)
        self.save_checkpoint(beats=beats, estimated_media=estimated_media)
        self.event(11, "Diseñando el montaje", f"{len(beats)} planos · coste previsto {estimated_media:.2f} $")

        def planning_progress(done, total):
            self.event(11 + 7 * done / total, "Diseñando el montaje", f"Bloque {done} de {total}", estimate=estimated_media, units_done=done, units_total=total)

        expected_ids = [beat["id"] for beat in beats]
        cached_scenes = self.checkpoint.get("planned_scenes")
        cached_scene_ids = [scene.get("id") for scene in cached_scenes] if isinstance(cached_scenes, list) else []
        cached_scene_types = [scene.get("type") for scene in cached_scenes] if isinstance(cached_scenes, list) else []
        expected_types = [beat.get("type") for beat in beats]
        if cached_scene_ids == expected_ids and cached_scene_types == expected_types:
            scenes = cached_scenes
            self.event(18, "Retomando análisis", "Plan visual recuperado sin volver a pagarlo")
        else:
            resume_planned = cached_scenes if cached_scene_ids == expected_ids[:len(cached_scene_ids)] else []

            def save_plan_checkpoint(planned):
                self.save_checkpoint(planned_scenes=planned, reviewed_scenes=[])

            scenes = plan_scenes(
                self.director, beats, bible, progress=planning_progress,
                resume_planned=resume_planned, checkpoint=save_plan_checkpoint,
            )
            self.save_checkpoint(planned_scenes=scenes, reviewed_scenes=[])
        self.event(18, "Revisando el montaje", "Comprobando que cada plano corresponda a su frase")

        def review_progress(done, total):
            self.event(18 + 1 * done / total, "Revisando el montaje", f"Bloque {done} de {total}", estimate=estimated_media, units_done=done, units_total=total)

        resumed_review = self.checkpoint.get("reviewed_scenes")
        if not isinstance(resumed_review, list):
            resumed_review = []

        def save_review_checkpoint(reviewed):
            self.save_checkpoint(reviewed_scenes=reviewed)

        scenes = review_scene_plan(
            self.reviewer, scenes, bible, progress=review_progress,
            resume_reviewed=resumed_review, checkpoint=save_review_checkpoint,
        )
        planning_warnings = validate_scene_plan(scenes, duration)
        force_avatar_window(scenes, qr_window)
        # Older builds could mutate a scene to avatar/image after a temporary
        # delivery failure while retaining its paid remote identifier. Restore
        # the original media contract so the same UUID/job_id is actually polled.
        planned_by_id = {
            str(item.get("id")): item for item in (cached_scenes or [])
            if isinstance(item, dict) and item.get("id")
        }
        pending_video_ids = set((self.checkpoint.get("pending_video_jobs") or {}).keys())
        pending_image_ids = set((self.checkpoint.get("pending_image_jobs") or {}).keys())
        for scene in scenes:
            scene_id = str(scene.get("id") or "")
            if scene_id in pending_video_ids and scene.get("type") != "video":
                original = planned_by_id.get(scene_id) or {}
                scene.update(original)
                scene["type"] = "video"
            elif scene_id in pending_image_ids and scene.get("type") == "avatar":
                original = planned_by_id.get(scene_id) or {}
                scene.update(original)
                scene["type"] = original.get("type") if original.get("type") in {"image", "split"} else "image"
        paid_ids = self.protect_local_paid_assets(scenes, planned_by_id)
        for key in ("completed_assets", "pending_video_jobs", "pending_image_jobs"):
            records = self.checkpoint.get(key)
            if isinstance(records, dict):
                paid_ids.update(str(item) for item in records)
        remaining_media_budget = max(0.0, float(self.config["max_cost_usd"]) - self.total_spent)
        scenes, estimated_media = rebalance_scenes_for_budget(
            scenes,
            remaining_media_budget,
            already_paid_ids=paid_ids,
        )
        enforce_presenter_broll(scenes, bible)
        qr_reminder = product_qr_reminder_window(scenes, qr_window, duration)
        if qr_reminder:
            qr_windows.append(qr_reminder)
        self.save_checkpoint(
            reviewed_scenes=scenes,
            estimated_media=estimated_media,
            planning_warnings=planning_warnings,
        )
        if not scenes or scenes[0]["type"] != "avatar":
            raise RuntimeError("La planificación no comenzó con el avatar como estaba previsto.")
        requested_counts = {
            media_type: sum(1 for scene in scenes if scene["type"] == media_type)
            for media_type in ("video", "image", "avatar", "split", "still")
        }
        (self.workspace / "plan.json").write_text(json.dumps({"bible": bible, "scenes": scenes}, ensure_ascii=False, indent=2))
        self.check_stop()

        generatable = [scene for scene in scenes if scene["type"] != "avatar"]
        assets = {}
        for scene in generatable:
            cached_asset = self.cached_asset_for(scene)
            if cached_asset:
                assets[scene["id"]] = cached_asset
        done = len(assets)
        recovered_detail = f"{done} recuperados · " if done else ""
        self.event(19, "Creando B-roll", f"{recovered_detail}{done} de {len(generatable)} recursos revisados", estimate=estimated_media)
        pending = [scene for scene in generatable if scene["id"] not in assets]
        # Recover every remote job that is already paid before the gate or worker
        # pool is allowed to create anything new.  This stage is deliberately
        # sequential: if storage is still unavailable, stop on the first UUID and
        # leave the whole paid set intact for the next run.
        pending_remote_ids = {
            str(scene_id)
            for key in ("pending_video_jobs", "pending_image_jobs")
            for scene_id, remote_id in (self.checkpoint.get(key) or {}).items()
            if remote_id
        }
        pending_by_id = {str(scene["id"]): scene for scene in pending}
        missing_paid_scenes = pending_remote_ids - set(pending_by_id)
        if missing_paid_scenes:
            raise PaidAssetRecoveryError(
                "VYT conserva recursos pagados que no puede asociar con seguridad al montaje actual. "
                "Vuelve a ejecutar el mismo vídeo y los mismos ajustes."
            )
        recovery_candidates = [
            scene for scene in pending if str(scene["id"]) in pending_remote_ids
        ]
        if recovery_candidates:
            self.event(
                19, "Recuperando recursos pagados",
                f"0 de {len(recovery_candidates)} pendientes · sin nuevas compras",
                estimate=estimated_media, units_done=0,
                units_total=len(recovery_candidates),
            )
        self.recovering_paid_assets = True
        try:
            for recovered_done, scene in enumerate(recovery_candidates, start=1):
                self.check_stop()
                try:
                    asset = self.generate_one(scene)
                except PaidAssetRecoveryError as error:
                    self.record_failure(scene, "paid_asset_recovery", error)
                    raise
                except ProviderTemporarilyUnavailableError as error:
                    self.record_failure(scene, "provider_temporarily_unavailable", error)
                    raise
                except Exception as error:
                    # A provider-declared terminal failure or a completed asset
                    # rejected by QA has no recoverable remote purchase left.
                    # Defer any replacement until the other paid IDs are handled.
                    if self.pending_video_job_for(scene) or self.pending_image_job_for(scene):
                        provider = "Algrow" if self.pending_image_job_for(scene) else "SnapGen"
                        recovery_error = PaidAssetRecoveryError(
                            safe_paid_recovery_reason(error, provider)
                        )
                        self.record_failure(scene, "paid_asset_recovery", recovery_error)
                        raise recovery_error from error
                    self.record_failure(scene, "paid_asset_terminal", error)
                    continue
                if asset:
                    assets[scene["id"]] = asset
                    self.mark_asset_completed(scene, asset)
                done += 1
                self.event(
                    19, "Recuperando recursos pagados",
                    f"{recovered_done} de {len(recovery_candidates)} pendientes · sin nuevas compras",
                    estimate=estimated_media, units_done=recovered_done,
                    units_total=len(recovery_candidates),
                )
        finally:
            self.recovering_paid_assets = False
        pending = [scene for scene in generatable if scene["id"] not in assets]
        # Validate Veo only when this resume actually needs a new video purchase.
        # A checkpoint with recoverable/finished assets must remain usable during a
        # temporary Veo outage; the preflight must not block review or rendering.
        video_candidates = stratified_generation_order(
            [scene for scene in pending if scene["type"] == "video"]
        )
        if video_candidates:
            # This public status check is free and protects direct/CLI runs if the
            # desktop-side check was skipped or the status changed meanwhile.
            self.event(19, "Comprobando Veo", "Verificando el servicio sin gastar créditos")
            try:
                self.geminigen.ensure_available()
            except ProviderError as error:
                # Veo is optional for the editorial result. Preserve the exact
                # beat and continue with a still through Algrow instead of
                # discarding the analysis and every already recovered asset.
                fallback_reason = f"Veo no disponible; imagen automática: {error}"
                for scene in video_candidates:
                    scene.update(image_fallback_scene(scene))
                    scene["fallback_reason"] = fallback_reason
                video_candidates = []
                self.save_checkpoint(
                    reviewed_scenes=scenes,
                    estimated_media=estimated_media,
                    fallback_notice=fallback_reason,
                )
                self.event(
                    19, "Creando B-roll",
                    "Veo no disponible; las escenas de vídeo pasan a imágenes sin detener el job",
                    estimate=estimated_media,
                )
        first_video = None
        gate_attempted = set()
        gate_fallbacks = 0
        for candidate in video_candidates[:3]:
            gate_attempted.add(candidate["id"])
            budget_blocked = False
            try:
                first_asset = self.generate_one(candidate)
            except PaidAssetRecoveryError as error:
                self.record_failure(candidate, "paid_asset_recovery", error)
                raise
            except ProviderTemporarilyUnavailableError as error:
                self.record_failure(candidate, "provider_temporarily_unavailable", error)
                raise
            except BudgetExhaustedError as error:
                self.record_failure(candidate, "budget", error)
                candidate["type"] = "avatar"
                first_asset = None
                budget_blocked = True
            done += 1
            if candidate["type"] == "video" and first_asset:
                first_video = candidate
                assets[candidate["id"]] = first_asset
                self.mark_asset_completed(candidate, first_asset)
                self.event(19 + 57 * done / max(1, len(generatable)), "Creando B-roll", f"Clip de Veo validado · {done} de {len(generatable)}", estimate=estimated_media, units_done=done, units_total=len(generatable))
                break
            candidate_failures = [item for item in self.failures if item["id"] == candidate["id"]]
            provider_failure = next((item for item in reversed(candidate_failures) if item.get("stage") == "video_provider"), None)
            if first_asset:
                # Visual QA or temporary storage delivery may reject a video.
                # Its reviewed image replacement is a successful beat, not a
                # reason to abort the entire paid production.
                assets[candidate["id"]] = first_asset
                self.mark_asset_completed(candidate, first_asset)
                gate_fallbacks += 1
                self.event(
                    19 + 57 * done / max(1, len(generatable)), "Creando B-roll",
                    f"Plano sustituido por imagen revisada · {done} de {len(generatable)}",
                    estimate=estimated_media, units_done=done, units_total=len(generatable),
                )
            else:
                assets[candidate["id"]] = None
                if budget_blocked:
                    # A provider can occasionally charge more than the
                    # conservative estimate. Keep the hard cap and continue
                    # with the evenly distributed free fallback.
                    gate_fallbacks += 1
                elif provider_failure:
                    raise ProviderError(f"VYT detuvo el lote: Veo no está respondiendo correctamente. {provider_failure.get('error', '')}")
        if video_candidates and not first_video:
            if not gate_fallbacks:
                raise ProviderError(
                    f"VYT detuvo el lote: los {len(gate_attempted)} primeros planos no produjeron ningún recurso utilizable. "
                    "El análisis queda guardado y no se generó el resto."
                )
            self.event(
                19 + 57 * done / max(1, len(generatable)), "Creando B-roll",
                f"Veo falló la revisión inicial; VYT continúa con {gate_fallbacks} sustituciones literales",
                estimate=estimated_media, units_done=done, units_total=len(generatable),
            )
        remaining = stratified_generation_order(
            [scene for scene in pending if scene["id"] not in gate_attempted]
        )
        # Each job uses four local workers. Paid provider submissions remain
        # bounded by ProviderRequestGate, while polling can continue in parallel.
        executor = ThreadPoolExecutor(max_workers=4)
        completed_pool = False
        futures = {}
        try:
            futures = {executor.submit(self.generate_one, scene): scene for scene in remaining}
            waiting = set(futures)
            while waiting:
                self.check_stop()
                ready, waiting = wait(waiting, timeout=1.0, return_when=FIRST_COMPLETED)
                for future in ready:
                    scene = futures[future]
                    self.check_stop()
                    try:
                        asset = future.result()
                    except PaidAssetRecoveryError as error:
                        self.record_failure(scene, "paid_asset_recovery", error)
                        raise
                    except ProviderTemporarilyUnavailableError as error:
                        self.record_failure(scene, "provider_temporarily_unavailable", error)
                        raise
                    except BudgetExhaustedError as error:
                        asset = None
                        self.record_failure(scene, "budget", error)
                        scene["type"] = "avatar"
                    except Exception as error:
                        self.check_stop()
                        asset = None
                        self.record_failure(scene, "worker", error)
                        if scene["type"] != "avatar":
                            scene["type"] = "avatar"
                    assets[scene["id"]] = asset
                    if asset:
                        self.mark_asset_completed(scene, asset)
                    done += 1
                    self.event(19 + 57 * done / max(1, len(generatable)), "Creando B-roll", f"{done} de {len(generatable)} recursos revisados", estimate=estimated_media, units_done=done, units_total=len(generatable))
            completed_pool = True
        finally:
            if not completed_pool:
                for future in futures:
                    future.cancel()
            executor.shutdown(wait=completed_pool, cancel_futures=not completed_pool)

        self.check_stop()
        rendered = []
        self.generated_counts = {"video": 0, "image": 0, "avatar": 0, "split": 0, "still": 0}
        total = len(scenes)
        saved_segments = self.checkpoint.get("rendered_segments")
        if not isinstance(saved_segments, dict):
            saved_segments = {}
        self.event(77, "Montando el vídeo", f"0 de {total} planos")
        for index, scene in enumerate(scenes):
            self.check_stop()
            segment = self.segments / f"{index:04d}.mp4"
            asset = assets.get(scene["id"])
            if scene["type"] != "avatar" and not asset:
                if self.pending_video_job_for(scene) or self.pending_image_job_for(scene):
                    raise PaidAssetRecoveryError(
                        f"El recurso {scene['id']} ya está pagado y sigue pendiente; VYT conserva el trabajo para reanudarlo."
                    )
                scene["type"] = "avatar"
            self.generated_counts[scene["type"]] = self.generated_counts.get(scene["type"], 0) + 1
            saved_name = saved_segments.get(scene["id"])
            reusable = self.segments / str(saved_name) if saved_name else segment
            can_reuse = reusable.exists() and reusable.stat().st_size > 1000
            if can_reuse:
                try:
                    segment_info = probe(reusable)
                    can_reuse = (
                        abs(segment_info["duration"] - float(scene["duration"])) <= max(0.18, 2.0 / 30.0)
                        and segment_info["width"] == 1920
                        and segment_info["height"] == 1080
                    )
                except Exception:
                    can_reuse = False
            if can_reuse:
                segment = reusable
            else:
                segment.unlink(missing_ok=True)
                render_segment(
                    scene, source, asset, segment,
                    product_card=product_card,
                    product_overlay=product_overlay_for_scene(scene, qr_windows),
                )
                saved_segments[scene["id"]] = segment.name
                self.save_checkpoint(rendered_segments=saved_segments)
            rendered.append(segment)
            self.event(77 + 19 * (index + 1) / total, "Montando el vídeo", f"Plano {index + 1} de {total}", units_done=index + 1, units_total=total)

        self.check_stop()
        self.event(97, "Terminando", "Uniendo el audio original y guardando en Descargas")
        output = assemble(rendered, source, self.config["output"], duration, self.workspace)
        output_info = probe(output)
        if abs(output_info["duration"] - duration) > 1.2 or output_info["width"] != 1920 or output_info["height"] != 1080:
            raise RuntimeError("La comprobación final detectó una duración o resolución incorrecta.")
        minimum_videos = minimum_required_videos(requested_counts.get("video", 0))
        visual_count = self.generated_counts.get("video", 0) + self.generated_counts.get("image", 0) + self.generated_counts.get("split", 0)
        avatar_ratio = self.generated_counts.get("avatar", 0) / max(1, total)
        if self.generated_counts.get("video", 0) < minimum_videos:
            # Never destroy an already rendered result after media has been paid.
            # Preserve it in Downloads and expose a clear quality warning.
            quality_warning = (
                f"Montaje conservado con {self.generated_counts.get('video', 0)} "
                f"de {requested_counts.get('video', 0)} clips de Veo previstos."
            )
        else:
            quality_warning = ""
        if avatar_ratio > 0.28:
            extra = f"Avatar excesivo ({avatar_ratio:.0%}); la cobertura visual quedó por debajo del estándar."
            quality_warning = f"{quality_warning} {extra}".strip()
        if visual_count < math.floor(total * 0.70):
            extra = f"Solo {visual_count} de {total} planos contienen apoyo visual."
            quality_warning = f"{quality_warning} {extra}".strip()
        self.ensure_no_pending_paid_jobs()
        self.clear_checkpoint()
        return {
            "ok": True,
            "output_path": str(output),
            "cost_usd": round(self.total_spent, 4),
            "cost_breakdown": self.cost_breakdown(),
            "generated_counts": self.generated_counts,
            "requested_counts": requested_counts,
            "failures": self.failures[-20:],
            "warning": quality_warning,
            "duration": duration,
        }

    def cleanup(self):
        if self.checkpoint_path and self.checkpoint_path.exists():
            try:
                self.save_checkpoint()
            except OSError:
                pass
        shutil.rmtree(self.workspace, ignore_errors=True)
        self.operation_ledger.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    config = json.loads(args.job)
    pipeline = None
    result = None
    try:
        pipeline = Pipeline(config)
        result = pipeline.run()
    except InterruptedError as error:
        result = {
            "ok": False, "error": str(error),
            "cost_usd": round(pipeline.total_spent if pipeline else 0, 4),
            "cost_breakdown": pipeline.cost_breakdown() if pipeline else {},
            "failures": pipeline.failures[-8:] if pipeline else [],
        }
    except ProviderTemporarilyUnavailableError as error:
        result = {
            "ok": False, "retryable": True, "error": str(error),
            "retry_after_seconds": getattr(error, "retry_after", 30),
            "cost_usd": round(pipeline.total_spent if pipeline else 0, 4),
            "cost_breakdown": pipeline.cost_breakdown() if pipeline else {},
            "failures": pipeline.failures[-8:] if pipeline else [],
        }
    except Exception as error:
        result = {
            "ok": False, "error": str(error),
            "cost_usd": round(pipeline.total_spent if pipeline else 0, 4),
            "cost_breakdown": pipeline.cost_breakdown() if pipeline else {},
            "failures": pipeline.failures[-8:] if pipeline else [],
        }
    finally:
        if pipeline:
            pipeline.cleanup()
    print("VYT_RESULT:" + json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
