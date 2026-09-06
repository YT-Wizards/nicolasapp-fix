const assert = require('node:assert/strict');
const test = require('node:test');
const { selectRunnableJobs } = require('../electron/job-queue');

test('selects oldest queued jobs up to the available concurrency', () => {
  const jobs = [
    { id: 'new', status: 'queued', createdAt: '2026-09-06T10:02:00.000Z' },
    { id: 'running', status: 'running', createdAt: '2026-09-06T10:00:00.000Z' },
    { id: 'old', status: 'queued', createdAt: '2026-09-06T10:01:00.000Z' },
    { id: 'paused', status: 'paused', createdAt: '2026-09-06T09:59:00.000Z' },
  ];

  assert.deepEqual(
    selectRunnableJobs(jobs, ['running'], 2).map((job) => job.id),
    ['old'],
  );
});

test('does not select a queued job already claimed by a process', () => {
  const jobs = [
    { id: 'claimed', status: 'queued', createdAt: '2026-09-06T10:00:00.000Z' },
    { id: 'next', status: 'queued', createdAt: '2026-09-06T10:01:00.000Z' },
  ];

  assert.deepEqual(
    selectRunnableJobs(jobs, ['claimed'], 2).map((job) => job.id),
    ['next'],
  );
});
