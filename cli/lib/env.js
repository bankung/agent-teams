'use strict';
// cli/lib/env.js — .env scaffold + CREDENTIALS_MASTER_KEY Fernet generation.
// Zero runtime dependencies; uses only Node built-ins.

const fs   = require('fs');
const path = require('path');
const crypto = require('crypto');

/**
 * Ensure .env exists (copied from .env.example if absent) and that
 * CREDENTIALS_MASTER_KEY is set to a valid Fernet key.
 *
 * Idempotent: never overwrites an existing non-empty key.
 *
 * @param {string} repoRoot  Absolute path to the repo root.
 */
function ensureEnv(repoRoot) {
  const envFile     = path.join(repoRoot, '.env');
  const envExample  = path.join(repoRoot, '.env.example');

  // ---- scaffold .env from .env.example if missing --------------------------
  if (!fs.existsSync(envFile)) {
    if (fs.existsSync(envExample)) {
      fs.copyFileSync(envExample, envFile);
      // chmod 0600: no-op on Windows but protects .env on shared POSIX hosts.
      try { fs.chmodSync(envFile, 0o600); } catch (_) { /* Windows — ignore */ }
      console.log('==> .env not found — copied from .env.example.');
    } else {
      console.warn('WARN: .env.example not found. You may need to create .env manually.');
    }
  }

  // ---- CREDENTIALS_MASTER_KEY ----------------------------------------------
  if (!fs.existsSync(envFile)) return; // nothing to do if we still have no .env

  let content = fs.readFileSync(envFile, 'utf8');

  // Match the line only when the value is non-empty (non-whitespace after '=').
  const keyPresent = /^CREDENTIALS_MASTER_KEY=\S+/m.test(content);

  if (keyPresent) {
    console.log('==> CREDENTIALS_MASTER_KEY already set — leaving untouched.');
    return;
  }

  // Generate: URL-safe base64 of 32 random bytes.
  // Node's 'base64url' encoding (available since Node 15) produces the exact
  // URL-safe alphabet (- and _ instead of + and /) that Python's Fernet expects.
  // 32 bytes → 44-char base64url string (no padding added by 'base64url').
  // Fernet requires standard base64 padding; we append '=' to make it 44 chars.
  //
  // Actually: 32 bytes → ceil(32/3)*4 = 44 chars in standard base64.
  // base64url omits the trailing '=' but Fernet checks for it.
  // We generate via 'base64' then swap +/ → -_ to stay explicit about format.
  const rawB64 = crypto.randomBytes(32).toString('base64'); // 44 chars, ends with '='
  const fernetKey = rawB64.replace(/\+/g, '-').replace(/\//g, '_');

  console.log('==> CREDENTIALS_MASTER_KEY is missing/empty — generating a Fernet key...');

  // Replace an existing empty-value line, or append if the line is absent.
  if (/^CREDENTIALS_MASTER_KEY=/m.test(content)) {
    content = content.replace(/^CREDENTIALS_MASTER_KEY=.*/m, `CREDENTIALS_MASTER_KEY=${fernetKey}`);
  } else {
    content = content.trimEnd() + `\nCREDENTIALS_MASTER_KEY=${fernetKey}\n`;
  }

  fs.writeFileSync(envFile, content, { encoding: 'utf8', mode: 0o600 });
  // chmod 0600: defensive belt-and-suspenders in case umask overrode mode above.
  try { fs.chmodSync(envFile, 0o600); } catch (_) { /* Windows — ignore */ }

  console.log('');
  console.log('NOTICE: A new CREDENTIALS_MASTER_KEY has been generated and written to .env.');
  console.log('        Back it up securely (password manager / offline storage). Losing this');
  console.log('        key makes ALL stored vault credentials permanently unrecoverable.');
  console.log('');
}

/**
 * Read a port from the .env file (if present) with a fallback default.
 * Used so the health-wait targets the right port when API_PORT / WEB_PORT differ.
 *
 * @param {string} repoRoot
 * @param {string} varName   e.g. 'API_PORT'
 * @param {string} fallback  e.g. '8456'
 */
function readEnvPort(repoRoot, varName, fallback) {
  const envFile = path.join(repoRoot, '.env');
  if (!fs.existsSync(envFile)) return fallback;
  const content = fs.readFileSync(envFile, 'utf8');
  // Find the line for varName, then strip inline comments before matching digits.
  // e.g. `API_PORT=8456 # note` — split at first '#', keep left side, then match.
  // Escape varName to prevent regex injection (caller-controlled string).
  const safeVarName = varName.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const lineMatch = content.match(new RegExp(`^${safeVarName}=(.*)`, 'm'));
  if (!lineMatch) return fallback;
  const valueRaw  = lineMatch[1].split('#')[0].trim(); // strip inline comment
  const digitMatch = valueRaw.match(/^(\d+)/);
  return digitMatch ? digitMatch[1] : fallback;
}

module.exports = { ensureEnv, readEnvPort };
