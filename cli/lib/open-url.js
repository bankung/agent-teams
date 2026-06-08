'use strict';
// cli/lib/open-url.js — Best-effort cross-platform browser opener.
// Always prints the URL; never throws or fails the caller.

const { spawn } = require('child_process');

// Safe URL pattern: only localhost URLs with a numeric port and a restricted path.
// This guards against metacharacter injection into cmd.exe on Windows even when
// shell:false is used (cmd /c start re-parses the URL argument).
// The port is already digit-constrained by readEnvPort, so the risk is low —
// this is defense-in-depth.
const SAFE_URL_RE = /^https?:\/\/localhost:\d+\/[A-Za-z0-9/_-]*$/;

/**
 * Print `url` and attempt to open it in the default browser.
 * Failure is silently swallowed — this must never block the install.
 * If the URL does not match the safe pattern, the auto-open is skipped
 * and only the printed URL is shown.
 *
 * SECURITY: This function is only safe for URLs constructed internally by the
 * CLI (localhost + digit port + restricted path). Never pass user-supplied or
 * externally-sourced URLs — the SAFE_URL_RE guard is defense-in-depth, not a
 * general sanitiser.
 *
 * @param {string} url  Must be a compiler-controlled localhost URL (see SAFE_URL_RE).
 */
function openUrl(url) {
  console.log(`\nOpen in your browser: ${url}\n`);

  if (!SAFE_URL_RE.test(url)) {
    // URL contains unexpected characters — skip auto-open, just rely on the print above.
    return;
  }

  let cmd, args;
  switch (process.platform) {
    case 'win32':
      // `start` is a cmd.exe builtin; run via cmd /c start
      cmd  = 'cmd';
      args = ['/c', 'start', '', url];
      break;
    case 'darwin':
      cmd  = 'open';
      args = [url];
      break;
    default: // Linux + everything else
      cmd  = 'xdg-open';
      args = [url];
      break;
  }

  try {
    const child = spawn(cmd, args, { stdio: 'ignore', detached: true, shell: false });
    child.unref(); // don't keep the Node process alive waiting for the browser
  } catch (_) {
    // best-effort — ignore all errors
  }
}

module.exports = { openUrl };
