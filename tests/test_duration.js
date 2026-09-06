const assert = require('assert');
const { isFullVideoDurationAllowed } = require('../electron/duration');

assert.strictEqual(isFullVideoDurationAllowed(479), true, 'allows tiny container drift at 8 min');
assert.strictEqual(isFullVideoDurationAllowed(480), true, 'allows an 8-minute video');
assert.strictEqual(isFullVideoDurationAllowed(2100), true, 'allows a 35-minute video');
assert.strictEqual(isFullVideoDurationAllowed(478.9), false, 'rejects videos below 8 min tolerance');
assert.strictEqual(isFullVideoDurationAllowed(2101.1), false, 'rejects videos above 35 min tolerance');
assert.strictEqual(isFullVideoDurationAllowed(NaN), false, 'rejects invalid duration');

console.log('Duration validation: OK');
