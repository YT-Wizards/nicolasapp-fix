const fs = require('fs');
const path = require('path');

let DatabaseSync;
try {
  ({ DatabaseSync } = require('node:sqlite'));
} catch (error) {
  throw new Error(`VYT requires an Electron runtime with node:sqlite support: ${error.message}`);
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);

class JobStore {
  constructor(filePath) {
    this.filePath = path.resolve(filePath);
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true, mode: 0o700 });
    this.db = new DatabaseSync(this.filePath);
    this.db.exec(`
      PRAGMA journal_mode = WAL;
      PRAGMA foreign_keys = ON;
      PRAGMA busy_timeout = 5000;
      CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        source TEXT NOT NULL,
        source_identity TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
      CREATE INDEX IF NOT EXISTS jobs_source_idx ON jobs(source_identity);
      CREATE TABLE IF NOT EXISTS cost_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        operation_key TEXT,
        provider TEXT NOT NULL,
        kind TEXT NOT NULL,
        amount_usd REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE INDEX IF NOT EXISTS cost_events_job_idx ON cost_events(job_id, kind);
    `);
    this.db.prepare(`
      INSERT OR IGNORE INTO schema_migrations(version, applied_at)
      VALUES(1, datetime('now'))
    `).run();
  }

  loadJobs() {
    return this.db.prepare(`
      SELECT payload_json FROM jobs
      WHERE status NOT IN ('completed', 'failed', 'cancelled')
      ORDER BY created_at DESC
    `).all().flatMap((row) => {
      try {
        const job = JSON.parse(row.payload_json);
        return job && job.id ? [job] : [];
      } catch {
        return [];
      }
    });
  }

  upsertJob(job) {
    if (!job?.id) throw new Error('Cannot persist a job without an id.');
    const payload = JSON.stringify(job);
    this.db.prepare(`
      INSERT INTO jobs(id, status, source, source_identity, payload_json, created_at, updated_at)
      VALUES(?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        status=excluded.status,
        source=excluded.source,
        source_identity=excluded.source_identity,
        payload_json=excluded.payload_json,
        updated_at=excluded.updated_at
    `).run(
      String(job.id),
      String(job.status || 'queued'),
      String(job.source || ''),
      String(job.sourceIdentity || job.source || ''),
      payload,
      String(job.createdAt || new Date().toISOString()),
      String(job.updatedAt || new Date().toISOString()),
    );
  }

  findActiveBySource(sourceIdentity) {
    const row = this.db.prepare(`
      SELECT payload_json FROM jobs
      WHERE source_identity = ? AND status NOT IN ('completed', 'failed', 'cancelled')
      ORDER BY created_at DESC LIMIT 1
    `).get(String(sourceIdentity || ''));
    if (!row) return null;
    try { return JSON.parse(row.payload_json); } catch { return null; }
  }

  costTotals(jobId) {
    return this.db.prepare(`
      SELECT kind, ROUND(SUM(amount_usd), 8) AS amount
      FROM cost_events WHERE job_id=? GROUP BY kind
    `).all(String(jobId || '')).reduce((totals, row) => {
      totals[row.kind] = Number(row.amount || 0);
      return totals;
    }, {});
  }

  recoverInterruptedJobs() {
    const now = new Date().toISOString();
    this.db.prepare(`
      UPDATE jobs
      SET status='queued', updated_at=?,
          payload_json=json_set(payload_json, '$.status', 'queued', '$.phase', 'Возобновление после перезапуска', '$.detail', 'Незавершённое задание восстановлено из SQLite')
      WHERE status IN ('running', 'paused', 'waiting_for_provider', 'waiting_for_download', 'rendering', 'recoverable')
    `).run(now);
  }

  close() {
    this.db.close();
  }
}

module.exports = { JobStore, TERMINAL_STATUSES };
