const MIN_FULL_VIDEO_SECONDS = 8 * 60;
const MAX_FULL_VIDEO_SECONDS = 35 * 60;
const DURATION_TOLERANCE_SECONDS = 1;

function isFullVideoDurationAllowed(duration) {
  const seconds = Number(duration);
  return Number.isFinite(seconds)
    && seconds >= MIN_FULL_VIDEO_SECONDS - DURATION_TOLERANCE_SECONDS
    && seconds <= MAX_FULL_VIDEO_SECONDS + DURATION_TOLERANCE_SECONDS;
}

module.exports = {
  MIN_FULL_VIDEO_SECONDS,
  MAX_FULL_VIDEO_SECONDS,
  DURATION_TOLERANCE_SECONDS,
  isFullVideoDurationAllowed
};
