'use strict';
// cli/lib/health.js — Poll an HTTP endpoint until it returns 200.
// Zero runtime dependencies; uses Node built-in `http` module.

const http = require('http');

/**
 * Poll `url` every `intervalMs` until it returns HTTP 200 or `timeoutMs` elapses.
 *
 * @param {string} url
 * @param {object} opts
 * @param {number} [opts.timeoutMs=60000]   Total wait budget in ms.
 * @param {number} [opts.intervalMs=5000]   Polling interval in ms.
 * @param {number} [opts.probeTimeoutMs=5000] Per-request timeout in ms.
 * @returns {Promise<boolean>} true if healthy, false if timed out.
 */
function waitForHealthy(url, { timeoutMs = 60000, intervalMs = 5000, probeTimeoutMs = 5000 } = {}) {
  return new Promise((resolve) => {
    const deadline = Date.now() + timeoutMs;
    let elapsed = 0;

    function probe() {
      const req = http.get(url, { timeout: probeTimeoutMs }, (res) => {
        res.resume(); // drain response body
        if (res.statusCode === 200) {
          resolve(true);
        } else {
          scheduleNext();
        }
      });
      req.on('error', () => scheduleNext());
      req.on('timeout', () => { req.destroy(); scheduleNext(); });
    }

    function scheduleNext() {
      // Use the full budget: only give up AFTER Date.now() has reached the deadline.
      // The old check (Date.now() + intervalMs > deadline) abandoned the last probe
      // ~1 interval early (wastes ~5s of a 60s cap).
      if (Date.now() >= deadline) {
        resolve(false);
        return;
      }
      process.stdout.write(`    ...still waiting (${elapsed}s elapsed)\n`);
      elapsed += Math.round(intervalMs / 1000);
      setTimeout(probe, intervalMs);
    }

    probe();
  });
}

module.exports = { waitForHealthy };
