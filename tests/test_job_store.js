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
