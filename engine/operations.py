import hashlib
import json
import sqlite3
import threading
from pathlib import Path


class OperationRecoveryRequired(RuntimeError):
    """A paid request may have been submitted but has no recoverable remote ID."""


class OperationLedger:
    """Conservative local ledger for paid provider operations."""

    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            str(self.database_path), timeout=5.0, check_same_thread=False
        )
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.lock = threading.RLock()
        with self.lock, self.connection:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS provider_operations (
                    operation_key TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    scene_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    asset_type TEXT NOT NULL,
                    prompt_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    remote_job_id TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS provider_operations_job_idx "
                "ON provider_operations(job_id, scene_id)"
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cost_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    operation_key TEXT,
                    provider TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    amount_usd REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self.connection.execute(
                "CREATE INDEX IF NOT EXISTS cost_events_job_idx ON cost_events(job_id, kind)"
            )

    @staticmethod
    def prompt_hash(prompt):
        normalized = " ".join(str(prompt or "").split()).strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def operation_key(job_id, scene_id, asset_type, provider, prompt, settings=None):
        payload = {
            "job_id": str(job_id),
            "scene_id": str(scene_id),
            "asset_type": str(asset_type),
            "provider": str(provider),
            "prompt_hash": OperationLedger.prompt_hash(prompt),
            "settings": settings or {},
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def prepare(self, operation_key, job_id, scene_id, provider, asset_type, prompt_hash):
        with self.lock, self.connection:
            row = self.connection.execute(
                "SELECT status, remote_job_id FROM provider_operations WHERE operation_key=?",
                (operation_key,),
            ).fetchone()
            if row:
                status, remote_job_id = row
                if status in {"submitted", "polling", "download_pending"} and remote_job_id:
                    return str(remote_job_id)
                if status == "prepared":
                    raise OperationRecoveryRequired(
                        f"Платная операция {operation_key[:12]} была подготовлена, "
                        "но remote ID не сохранился. Требуется reconciliation; новая покупка запрещена."
                    )
                if status == "completed":
                    return str(remote_job_id or "")
                self.connection.execute(
                    """
                    UPDATE provider_operations
                    SET status='prepared', remote_job_id=NULL, attempts=attempts+1,
                        last_error=NULL, updated_at=CURRENT_TIMESTAMP
                    WHERE operation_key=?
                    """,
                    (operation_key,),
                )
                return ""
            self.connection.execute(
                """
                INSERT INTO provider_operations(
                    operation_key, job_id, scene_id, provider, asset_type,
                    prompt_hash, status, attempts
                ) VALUES (?, ?, ?, ?, ?, ?, 'prepared', 1)
                """,
                (operation_key, str(job_id), str(scene_id), str(provider),
                 str(asset_type), str(prompt_hash)),
            )
            return ""

    def mark_submitted(self, operation_key, remote_job_id):
        if not remote_job_id:
            raise ValueError("Cannot mark a paid operation submitted without a remote ID.")
        with self.lock, self.connection:
            self.connection.execute(
                """
                UPDATE provider_operations
                SET status='submitted', remote_job_id=?, updated_at=CURRENT_TIMESTAMP
                WHERE operation_key=?
                """,
                (str(remote_job_id), operation_key),
            )

    def mark_completed(self, operation_key, remote_job_id=None):
        with self.lock, self.connection:
            self.connection.execute(
                """
                UPDATE provider_operations
                SET status='completed', remote_job_id=COALESCE(?, remote_job_id),
                    updated_at=CURRENT_TIMESTAMP
                WHERE operation_key=?
                """,
                (str(remote_job_id) if remote_job_id else None, operation_key),
            )

    def mark_failed(self, operation_key, error):
        with self.lock, self.connection:
            self.connection.execute(
                """
                UPDATE provider_operations
                SET status='failed', last_error=?, updated_at=CURRENT_TIMESTAMP
                WHERE operation_key=?
                """,
                (str(error)[:1000], operation_key),
            )

    def record_cost(self, job_id, kind, amount_usd, provider="unknown", operation_key=None, metadata=None):
        amount = max(0.0, float(amount_usd or 0.0))
        if amount <= 0.0:
            return
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO cost_events(
                    job_id, operation_key, provider, kind, amount_usd, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(job_id), str(operation_key) if operation_key else None,
                 str(provider), str(kind), amount,
                 json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)),
            )

    def cost_totals(self, job_id):
        with self.lock:
            rows = self.connection.execute(
                "SELECT kind, ROUND(SUM(amount_usd), 8) FROM cost_events "
                "WHERE job_id=? GROUP BY kind",
                (str(job_id),),
            ).fetchall()
        return {str(kind): float(amount or 0.0) for kind, amount in rows}

    def close(self):
        with self.lock:
            self.connection.close()
