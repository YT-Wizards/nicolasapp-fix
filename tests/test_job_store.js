const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const test = require('node:test');
const { JobStore } = require('../electron/job-store');

test('persists jobs and recovers interrupted work as queued', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vyt-job-store-'));
  const database = path.join(directory, 'vyt.sqlite');
  const store = new JobStore(database);
  const job = {
    id: 'job-1',
    title: 'Example',
    source: '/tmp/source.mp4',
    sourceIdentity: '/tmp/source.mp4',
    status: 'running',
    phase: 'Создание B-roll',
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
  store.upsertJob(job);
  assert.equal(store.findActiveBySource(job.sourceIdentity).id, job.id);
  store.close();

  const reopened = new JobStore(database);
  reopened.recoverInterruptedJobs();
  const recovered = reopened.loadJobs();
  assert.equal(recovered.length, 1);
  assert.equal(recovered[0].status, 'queued');
  assert.equal(recovered[0].phase, 'Возобновление после перезапуска');
  reopened.close();
  fs.rmSync(directory, { recursive: true, force: true });
});

test('aggregates durable cost ledger by kind', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vyt-cost-store-'));
  const store = new JobStore(path.join(directory, 'vyt.sqlite'));
  store.db.prepare(`
    INSERT INTO cost_events(job_id, provider, kind, amount_usd)
    VALUES (?, ?, ?, ?), (?, ?, ?, ?), (?, ?, ?, ?)
  `).run('job-2', 'algrow', 'charged', 0.4, 'job-2', 'algrow', 'charged', 0.6, 'job-2', 'algrow', 'avoided_duplicate', 0.8);
  assert.deepEqual(store.costTotals('job-2'), { charged: 1, avoided_duplicate: 0.8 });
  store.close();
  fs.rmSync(directory, { recursive: true, force: true });
});
