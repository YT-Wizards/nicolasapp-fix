function createdAtValue(job, index) {
  const timestamp = Date.parse(String(job?.createdAt || ''));
  return Number.isFinite(timestamp) ? timestamp : Number.MAX_SAFE_INTEGER + index;
}

/**
 * Returns the next durable jobs an execution adapter may claim.
 * The adapter owns process creation; this module only enforces FIFO and the
 * concurrency invariant at the queue seam.
 */
function selectRunnableJobs(jobs, runningIds = [], maxRunning = Number.POSITIVE_INFINITY) {
  const running = new Set(runningIds || []);
  const slots = Math.max(0, Number(maxRunning || 0) - running.size);
  if (!slots) return [];
  return (Array.isArray(jobs) ? jobs : [])
    .map((job, index) => ({ job, index }))
    .filter(({ job }) => job?.status === 'queued' && !running.has(job.id))
    .sort((left, right) => createdAtValue(left.job, left.index) - createdAtValue(right.job, right.index))
    .slice(0, slots)
    .map(({ job }) => job);
}

module.exports = { selectRunnableJobs };
