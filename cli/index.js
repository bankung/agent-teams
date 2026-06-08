#!/usr/bin/env node
'use strict';
// cli/index.js — agent-teams CLI entrypoint.
//
// Subcommands:
//   up      Full install / bring-up (idempotent). Works in-repo OR standalone.
//   down    Stop containers without wiping data.
//   status  Show container health + API probe.
//   reset   Destructive wipe + rebuild (requires confirmation).
//   help    Print usage.
//   --help, -h, --version, -v  Aliases handled below.
//
// Zero runtime dependencies — only Node built-ins.

const path = require('path');
const fs   = require('fs');
const { spawnSync, spawn } = require('child_process');
const { checkGit, checkDocker, compose } = require('./lib/docker');
const { ensureEnv, readEnvPort } = require('./lib/env');
const { waitForHealthy } = require('./lib/health');
const { openUrl } = require('./lib/open-url');
const { requireConfirmation } = require('./lib/confirm');

// ---------------------------------------------------------------------------
// Constants / defaults
// ---------------------------------------------------------------------------
const VERSION      = '0.1.0';
const PROJECT_NAME = 'agent-teams';
const REPO_URL     = 'https://github.com/bankung/agent-teams.git';

// The CLI is published from the repo root, so __dirname = <repo>/cli.
// In IN-REPO mode the package root IS the repo root — docker-compose.yml sits
// one level up from __dirname.  In STANDALONE (npx from empty dir) mode the
// package ships only cli/ + README so that file is absent; resolveRepoRoot()
// handles the distinction at runtime.
const PKG_PARENT = path.resolve(__dirname, '..');

// ---------------------------------------------------------------------------
// Help text
// ---------------------------------------------------------------------------
const HELP = `
Usage: npx @bankung/agent-teams <command> [options]

Commands:
  up [targetDir]  Build and start all services (Docker Compose). Idempotent.
                    In standalone mode (run outside a cloned repo), clones the
                    repository first. Optional targetDir overrides the default
                    clone destination (<cwd>/agent-teams).
  down            Stop all services (no data loss — volumes are kept).
  status          Show container health and probe the API on :8456.
  reset           DESTRUCTIVE: wipe all data (Postgres volume) and rebuild from
                    scratch. Requires typing 'WIPE' to confirm, or pass --yes.
  help            Print this message.

Options:
  --help, -h       Print this message.
  --version, -v    Print version.
  --yes            Skip interactive confirmation for reset.
  --images, --pull (up only) Pull pre-built images from GHCR instead of
                    building from source. Requires no git clone — the CLI ships
                    docker-compose.images.yml and .env.example. Images must be
                    published to GHCR by the release CI first. Set
                    AGENT_TEAMS_VERSION in .env to pin a specific release tag
                    (default: latest).

Prerequisites:
  Docker Desktop (or Docker Engine) must be installed and running.
  git must be installed and on PATH (required for standalone/clone mode only;
  NOT required when using --images).
  Neither Docker nor git is installed by this CLI.
  Docker: https://docs.docker.com/get-docker/
  git:    https://git-scm.com/downloads

Examples:
  npx @bankung/agent-teams up                    # clone + build from source
  npx @bankung/agent-teams up --images           # pull pre-built GHCR images
  npx @bankung/agent-teams status
  npx @bankung/agent-teams down
  npx @bankung/agent-teams reset --yes
  npx @bankung/agent-teams up ~/my-agent-teams

Bin alias: the package exposes the \`agent-teams\` bin alias.
After a global install (\`npm install -g @bankung/agent-teams\`) you can run:
  agent-teams up
  agent-teams up --images
`.trim();

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function die(msg, code = 1) {
  process.stderr.write(`ERROR: ${msg}\n`);
  process.exit(code);
}

function log(msg) {
  process.stdout.write(`==> ${msg}\n`);
}

function banner() {
  console.log(`
=========================================================================
agent-teams is installed and running.

Next steps:
  1. Open http://localhost:5431 in your browser.
  2. Click the 'demo-tour' project. Try a task. (5 min walkthrough.)
  3. Read QUICKSTART.md (at the repo root) for the full intro.

Need help? See README.md or run:  npx @bankung/agent-teams --help
=========================================================================
`);
}

// ---------------------------------------------------------------------------
// Images-compose resolution (--images / --pull mode)
//
// docker-compose.images.yml is shipped inside the npm package (alongside
// .env.example) so `npx @bankung/agent-teams up --images` works from an
// EMPTY directory — no git clone needed.
//
// Search order:
//   1. PKG_PARENT/docker-compose.images.yml  (in-repo or package-root mode)
//   2. process.cwd()/docker-compose.images.yml (user ran from repo dir)
//
// Returns the absolute path to the compose file, or throws if not found.
// ---------------------------------------------------------------------------
function resolveImagesCompose() {
  const candidates = [
    path.join(PKG_PARENT, 'docker-compose.images.yml'),
    path.join(process.cwd(), 'docker-compose.images.yml'),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new Error(
    'docker-compose.images.yml not found.\n' +
    '  Searched:\n' +
    candidates.map((c) => `    ${c}`).join('\n') + '\n' +
    '  The file ships with the npm package — try reinstalling:\n' +
    '    npm install -g @bankung/agent-teams'
  );
}

// ---------------------------------------------------------------------------
// Repo-root resolution (NEW FEATURE — clone capability)
//
// Returns the absolute path to the repo root.
//
// IN-REPO mode:    docker-compose.yml exists at PKG_PARENT (the package root,
//                  one level above this file's __dirname).  Use PKG_PARENT as-is.
//
// STANDALONE mode: docker-compose.yml is absent.  Clone or reuse a clone in
//                  `targetDir` (default: <cwd>/agent-teams).
// ---------------------------------------------------------------------------
async function resolveRepoRoot(targetDir) {
  const composeFile = path.join(PKG_PARENT, 'docker-compose.yml');

  if (fs.existsSync(composeFile)) {
    log(`IN-REPO mode — using repo root: ${PKG_PARENT}`);
    return PKG_PARENT;
  }

  // STANDALONE mode — need git
  const git = checkGit();
  if (!git.ok) die(git.message, 1);

  const cloneDir = targetDir
    ? path.resolve(targetDir)
    : path.join(process.cwd(), 'agent-teams');

  // Guard: refuse if cloneDir resolves to a system directory.
  // Covers POSIX (/etc, /usr, /bin, /sbin, /lib, /boot) and Windows (C:\Windows).
  const normalClone = cloneDir.replace(/\\/g, '/');
  const SYSTEM_DIRS_RE = /^(\/etc|\/usr|\/bin|\/sbin|\/lib|\/boot|[A-Za-z]:\/Windows)(\/|$)/i;
  if (SYSTEM_DIRS_RE.test(normalClone)) {
    die(`Refusing to clone into a system directory: ${cloneDir}`, 1);
  }

  log(`STANDALONE mode — resolved clone directory: ${cloneDir}`);

  if (fs.existsSync(cloneDir)) {
    // Directory exists — check whether it already contains the repo
    const cloneCompose = path.join(cloneDir, 'docker-compose.yml');
    if (fs.existsSync(cloneCompose)) {
      log(`Clone directory already contains a repo — reusing: ${cloneDir}`);
      // Optional: git pull --ff-only (non-fatal on failure)
      log('Attempting git pull --ff-only to get latest changes (non-fatal if it fails)...');
      const pullResult = spawnSync('git', ['pull', '--ff-only'], {
        cwd: cloneDir,
        stdio: 'inherit',
        shell: false,
        timeout: 300_000,
      });
      if (pullResult.status !== 0) {
        process.stderr.write('WARN: git pull --ff-only failed — continuing with existing clone.\n');
      }
      return cloneDir;
    } else {
      die(
        `Directory "${cloneDir}" already exists but does not contain agent-teams (docker-compose.yml missing).\n` +
        `  Choose an empty or clean target directory:\n` +
        `    npx @bankung/agent-teams up /path/to/empty-dir`,
        1
      );
    }
  }

  // Clone the repo
  log(`Cloning ${REPO_URL} into ${cloneDir} ...`);
  log('(First run builds Docker images from source — this may take several minutes.)');
  const cloneResult = spawnSync('git', ['clone', REPO_URL, cloneDir], {
    stdio: 'inherit',
    shell: false,
    timeout: 300_000,
  });
  if (cloneResult.status !== 0) {
    die('git clone failed. Check the error above. Ensure the repo URL is public and git is on PATH.', 1);
  }

  return cloneDir;
}

// ---------------------------------------------------------------------------
// Tier-preset step (BLOCKER-1)
//
// Mirrors install.sh step 5 / install.ps1 step 5.
// Prompts for Claude Code plan, runs the tier-set script when Pro is chosen.
// Non-interactive (no TTY or NON_INTERACTIVE env var set) → skip silently.
// ---------------------------------------------------------------------------
async function runTierStep(repoRoot) {
  const isInteractive =
    process.stdin.isTTY &&
    !process.env.NON_INTERACTIVE;

  if (!isInteractive) {
    log('Non-interactive mode — defaulting to TIER MAX.');
    return;
  }

  // Prompt (mirrors install.sh exactly)
  process.stdout.write('\nClaude Code plan? [m]ax / [p]ro  (default: max, Enter to skip): ');

  const planInput = await new Promise((resolve) => {
    const readline = require('readline');
    // terminal:false intentional — we wrote the prompt manually above (process.stdout.write)
    // to avoid readline's built-in prompt echoing; terminal:false suppresses the duplicate.
    const rl = readline.createInterface({ input: process.stdin, output: process.stdout, terminal: false });
    // We already wrote the prompt above; just read one line.
    rl.once('line', (line) => { rl.close(); resolve(line.trim()); });
    rl.once('close', () => resolve(''));
  });

  const isProPlan = /^(p|pro)$/i.test(planInput);
  const tierChoice = isProPlan ? 'l2' : 'max';

  if (tierChoice === 'l2') {
    log('Pro plan selected — applying TIER L2 preset...');
    const isWindows = process.platform === 'win32';
    if (isWindows) {
      const tierScript = path.join(repoRoot, 'bin', 'agent-teams-tier-set.ps1');
      if (fs.existsSync(tierScript)) {
        const result = spawnSync('powershell.exe', ['-NonInteractive', '-File', tierScript, 'l2'], {
          stdio: 'inherit',
          shell: false,
          cwd: repoRoot,
        });
        if (result.status !== 0) {
          process.stderr.write('WARN: agent-teams-tier-set.ps1 exited non-zero — tier may not be applied.\n');
        }
      } else {
        process.stderr.write(`WARN: bin\\agent-teams-tier-set.ps1 not found — skipping tier apply. Run it manually.\n`);
      }
    } else {
      const tierScript = path.join(repoRoot, 'bin', 'agent-teams-tier-set.sh');
      if (fs.existsSync(tierScript)) {
        const result = spawnSync('bash', [tierScript, 'l2'], {
          stdio: 'inherit',
          shell: false,
          cwd: repoRoot,
        });
        if (result.status !== 0) {
          process.stderr.write('WARN: agent-teams-tier-set.sh exited non-zero — tier may not be applied.\n');
        }
      } else {
        process.stderr.write(`WARN: bin/agent-teams-tier-set.sh not found — skipping tier apply. Run it manually.\n`);
      }
    }
    log('TIER L2 active. Restart your Claude Code session to pick up new model defaults.');
  } else {
    log('TIER MAX active (operator default — no agent file changes).');
  }
}

// ---------------------------------------------------------------------------
// SEC-4: Production-readiness warnings (shared by cmdUp and cmdUpImages)
//
// Non-fatal — operator must decide. Never throws.
// ---------------------------------------------------------------------------
function warnIfInsecure(envRoot) {
  const envFile = path.join(envRoot, '.env');
  if (!fs.existsSync(envFile)) return;
  try {
    const raw = fs.readFileSync(envFile, 'utf8');
    const get = (key) => {
      // Escape key to prevent regex injection (hardcoded callers, but defensive).
      const safeKey = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const m = raw.match(new RegExp(`^${safeKey}=(.*)$`, 'm'));
      return m ? m[1].trim() : '';
    };
    const secretKey = get('SECRET_KEY');
    const appEnv    = get('APP_ENV');
    const appDebug  = get('APP_DEBUG');
    const warns = [];
    if (!secretKey || secretKey.includes('dev-secret') || secretKey.length < 32) {
      warns.push('  - SECRET_KEY is empty or weak. Generate a strong random value before going live.');
    }
    if (appEnv === 'development') {
      warns.push('  - APP_ENV=development is set. Change to "production" for public deployments.');
    }
    if (appDebug === 'true') {
      warns.push('  - APP_DEBUG=true is set. Disable before exposing the stack to the internet.');
    }
    if (warns.length) {
      process.stderr.write(
        '\nWARN: Production-readiness issues detected in .env:\n' +
        warns.join('\n') + '\n' +
        'Set secure values in .env before exposing this stack publicly.\n\n'
      );
    }
  } catch (_) { /* best-effort; never fatal */ }
}

// ---------------------------------------------------------------------------
// Subcommand: up
// ---------------------------------------------------------------------------
async function cmdUp(argv) {
  // Flags
  const useImages = argv.includes('--images') || argv.includes('--pull');

  // Optional positional argument: targetDir for standalone clone (ignored in --images mode)
  const positional = argv.filter((a) => !a.startsWith('-'));
  const targetDir  = positional[0] || null;

  // 1. Docker daemon check
  const docker = checkDocker();
  if (!docker.ok) die(docker.message, 1);
  log('Docker daemon OK.');

  if (useImages) {
    // --images / --pull mode: pull pre-built GHCR images — no clone needed.
    await cmdUpImages(argv);
    return;
  }

  // 2. Resolve repo root (IN-REPO or STANDALONE/clone)
  const repoRoot = await resolveRepoRoot(targetDir);

  // 3. .env scaffold + CREDENTIALS_MASTER_KEY
  ensureEnv(repoRoot);
  warnIfInsecure(repoRoot);

  // 4. docker compose up -d --build
  log('Building and starting services (docker compose up -d --build)...');
  const upExit = await compose(['up', '-d', '--build'], {}, { cwd: repoRoot });
  if (upExit !== 0) die('docker compose up failed. Inspect the output above.', 2);

  // 5. Schema migration (bypasses the L10 live-DB guard — safe on fresh install)
  log('Running schema migration...');
  log('  (MIGRATION_TARGET=live bypasses the live-DB guard — safe on fresh or idempotent re-run)');
  const migrateExit = await compose(
    ['exec', '-T', '-e', 'MIGRATION_TARGET=live', 'api', 'alembic', 'upgrade', 'head'],
    {},
    { cwd: repoRoot }
  );
  if (migrateExit !== 0) die('Schema migration failed. Check logs: docker compose logs api', 5);

  // 6. Wait for API healthy
  const apiPort  = readEnvPort(repoRoot, 'API_PORT', '8456');
  const healthUrl = `http://localhost:${apiPort}/api/projects`;
  log(`Waiting for API at ${healthUrl} (cap 60s)...`);
  const healthy = await waitForHealthy(healthUrl, { timeoutMs: 60000, intervalMs: 5000 });
  if (!healthy) die('API did not become healthy within 60s. Check logs: docker compose logs api', 3);
  log('API healthy.');

  // 7. Seed (idempotent — re-runs are no-ops)
  log('Running seed (docker compose exec -T api python -m scripts.seed)...');
  log('  (SEED_TARGET=production bypasses the L11 guard — safe on fresh or idempotent re-run)');
  const seedExit = await compose(
    ['exec', '-T', '-e', 'SEED_TARGET=production', 'api', 'python', '-m', 'scripts.seed'],
    {},
    { cwd: repoRoot }
  );
  if (seedExit !== 0) die('Seed failed. Check logs: docker compose logs api', 4);

  // 8. Tier-preset step (BLOCKER-1)
  await runTierStep(repoRoot);

  // 9. Banner + open browser
  banner();
  const webPort  = readEnvPort(repoRoot, 'WEB_PORT', '5431');
  openUrl(`http://localhost:${webPort}/p/agent-teams`);
}

// ---------------------------------------------------------------------------
// Subcommand: up --images (pull mode)
//
// Pulls pre-built images from GHCR and starts the stack using
// docker-compose.images.yml. No git clone required. The compose file and
// .env.example are shipped inside the npm package.
// ---------------------------------------------------------------------------
async function cmdUpImages(_argv) {
  // Resolve the bundled images-compose file (shipped with the npm package).
  let imagesCompose;
  try {
    imagesCompose = resolveImagesCompose();
  } catch (err) {
    die(err.message, 1);
  }
  log(`Using images compose: ${imagesCompose}`);

  // The env file lives next to the compose file (package root) in standalone
  // mode, or at the repo root in in-repo mode. Both are the same directory.
  const envRoot = path.dirname(imagesCompose);

  // Scaffold .env from .env.example (ships with the package).
  ensureEnv(envRoot);

  // SEC-4: Non-fatal production-readiness WARN (shared warnIfInsecure — also called from cmdUp).
  warnIfInsecure(envRoot);

  // Pull images first.
  log('Pulling images from GHCR (docker compose pull)...');
  const pullExit = await compose(['pull'], {}, { cwd: envRoot }, imagesCompose);
  if (pullExit !== 0) die('docker compose pull failed. Check the output above.', 2);

  // Start stack (no --build — images already pulled).
  log('Starting services (docker compose up -d)...');
  const upExit = await compose(['up', '-d'], {}, { cwd: envRoot }, imagesCompose);
  if (upExit !== 0) die('docker compose up failed. Inspect the output above.', 2);

  // Schema migration.
  log('Running schema migration...');
  log('  (MIGRATION_TARGET=live bypasses the live-DB guard — safe on fresh or idempotent re-run)');
  const migrateExit = await compose(
    ['exec', '-T', '-e', 'MIGRATION_TARGET=live', 'api', 'alembic', 'upgrade', 'head'],
    {},
    { cwd: envRoot },
    imagesCompose
  );
  if (migrateExit !== 0) die('Schema migration failed. Check logs: docker compose -f docker-compose.images.yml logs api', 5);

  // Wait for API healthy.
  const apiPort  = readEnvPort(envRoot, 'API_PORT', '8456');
  const healthUrl = `http://localhost:${apiPort}/api/projects`;
  log(`Waiting for API at ${healthUrl} (cap 60s)...`);
  const healthy = await waitForHealthy(healthUrl, { timeoutMs: 60000, intervalMs: 5000 });
  if (!healthy) die('API did not become healthy within 60s. Check logs: docker compose -f docker-compose.images.yml logs api', 3);
  log('API healthy.');

  // Seed (idempotent).
  log('Running seed...');
  log('  (SEED_TARGET=production bypasses the L11 guard — safe on fresh or idempotent re-run)');
  const seedExit = await compose(
    ['exec', '-T', '-e', 'SEED_TARGET=production', 'api', 'python', '-m', 'scripts.seed'],
    {},
    { cwd: envRoot },
    imagesCompose
  );
  if (seedExit !== 0) die('Seed failed. Check logs: docker compose -f docker-compose.images.yml logs api', 4);

  // Tier preset — only fires when bin/ is present (not shipped in npm package).
  if (fs.existsSync(path.join(envRoot, 'bin'))) {
    await runTierStep(envRoot);
  } else {
    log('Tier setup scripts (bin/) not present in this install — skipping tier step.');
    log('  To configure Claude Code tiers, clone the repo: https://github.com/bankung/agent-teams');
  }

  // Banner + open browser.
  banner();
  const webPort  = readEnvPort(envRoot, 'WEB_PORT', '5431');
  openUrl(`http://localhost:${webPort}/p/agent-teams`);
}

// ---------------------------------------------------------------------------
// Subcommand: down
// ---------------------------------------------------------------------------
async function cmdDown() {
  const docker = checkDocker();
  if (!docker.ok) die(docker.message, 1);

  // Resolution order:
  //   1. docker-compose.yml at PKG_PARENT  → in-repo mode (normal dev build)
  //   2. docker-compose.images.yml          → standalone --images mode
  //   3. docker-compose.yml at cwd          → fallback
  //   4. neither found                       → graceful exit
  const devCompose    = path.join(PKG_PARENT, 'docker-compose.yml');
  let imagesComposePath = null;
  try { imagesComposePath = resolveImagesCompose(); } catch (_) { /* not found */ }

  let repoRoot     = null;
  let composeArg   = null; // null = use default docker-compose.yml discovery

  if (fs.existsSync(devCompose)) {
    repoRoot = PKG_PARENT;
    // composeArg stays null — docker compose will pick docker-compose.yml
  } else if (imagesComposePath) {
    repoRoot   = path.dirname(imagesComposePath);
    composeArg = imagesComposePath;
  } else {
    const cwdCompose = path.join(process.cwd(), 'docker-compose.yml');
    if (fs.existsSync(cwdCompose)) {
      repoRoot = process.cwd();
    } else {
      log('Nothing to stop — no compose file found.');
      process.exit(0);
    }
  }

  log('Stopping services (volumes preserved)...');
  const code = await compose(['down'], {}, { cwd: repoRoot }, composeArg);
  process.exit(code);
}

// ---------------------------------------------------------------------------
// Subcommand: status
// ---------------------------------------------------------------------------
async function cmdStatus() {
  const docker = checkDocker();
  if (!docker.ok) die(docker.message, 1);

  // Mirror cmdDown resolution: prefer dev compose, fall back to images compose.
  const devCompose    = path.join(PKG_PARENT, 'docker-compose.yml');
  let imagesComposePath = null;
  try { imagesComposePath = resolveImagesCompose(); } catch (_) { /* not found */ }

  let repoRoot   = null;
  let composeArg = null;

  if (fs.existsSync(devCompose)) {
    repoRoot = PKG_PARENT;
  } else if (imagesComposePath) {
    repoRoot   = path.dirname(imagesComposePath);
    composeArg = imagesComposePath;
  } else {
    const cwdCompose = path.join(process.cwd(), 'docker-compose.yml');
    repoRoot = fs.existsSync(cwdCompose) ? process.cwd() : PKG_PARENT;
  }

  // Show `docker compose ps` output
  log('Container status:');
  await compose(['ps'], {}, { cwd: repoRoot }, composeArg);

  // Probe API
  const apiPort  = readEnvPort(repoRoot, 'API_PORT', '8456');
  const healthUrl = `http://localhost:${apiPort}/api/projects`;
  log(`Probing API at ${healthUrl}...`);
  const healthy = await waitForHealthy(healthUrl, { timeoutMs: 5000, intervalMs: 1000 });
  if (healthy) {
    log(`API is reachable at ${healthUrl}`);
  } else {
    process.stderr.write(`WARN: API at ${healthUrl} did not respond. Stack may be starting.\n`);
    process.exit(1);
  }
}

// ---------------------------------------------------------------------------
// Subcommand: reset
// ---------------------------------------------------------------------------
async function cmdReset(argv) {
  const docker = checkDocker();
  if (!docker.ok) die(docker.message, 1);

  // BLOCKER-2 guard 1: refuse to run from a worktree.
  // Check the resolved repo root (PKG_PARENT in in-repo mode) and cwd.
  const repoRootForReset = fs.existsSync(path.join(PKG_PARENT, 'docker-compose.yml'))
    ? PKG_PARENT
    : process.cwd();

  // Normalise separators for the pattern check (handle both / and \)
  const repoRootNorm = repoRootForReset.replace(/\\/g, '/');
  if (repoRootNorm.includes('.claude/worktrees')) {
    die(
      `Refusing to reset from a worktree path: ${repoRootForReset}\n` +
      `  cd to the main repo checkout first.`,
      1
    );
  }

  // BLOCKER-2 guard 2: docker-compose.yml must exist.
  const composeFilePath = path.join(repoRootForReset, 'docker-compose.yml');
  if (!fs.existsSync(composeFilePath)) {
    die(
      `docker-compose.yml not found in ${repoRootForReset}.\n` +
      `  Reset requires the full repo. Run 'up' first to clone it, then re-run 'reset' from that directory.`,
      1
    );
  }

  const hasYes = argv.includes('--yes') || process.env.AGENT_TEAMS_RESET_YES === '1';

  if (!hasYes) {
    const confirmed = await requireConfirmation(
      `\nThis will:\n` +
      `  - Stop all agent-teams containers (compose project: ${PROJECT_NAME}).\n` +
      `  - DELETE the Postgres volume (every project, task, and history row is gone).\n` +
      `  - Re-build and re-seed from scratch.\n`
    );
    if (!confirmed) {
      console.log('Aborted.');
      process.exit(0);
    }
  }

  log(`docker compose -p ${PROJECT_NAME} down -v`);
  const downCode = await compose(['down', '-v'], {}, { cwd: repoRootForReset });
  if (downCode !== 0) die('docker compose down -v failed.', downCode);

  log('Re-running full install...');
  // F-01: pass the original flags (e.g. --images) so `reset --images` rebuilds via images.
  await cmdUp(argv);
}

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------
async function main() {
  const [,, cmd, ...rest] = process.argv;

  if (!cmd || cmd === 'help' || cmd === '--help' || cmd === '-h') {
    console.log(HELP);
    process.exit(0);
  }

  if (cmd === '--version' || cmd === '-v') {
    console.log(`agent-teams ${VERSION}`);
    process.exit(0);
  }

  switch (cmd) {
    case 'up':
      await cmdUp(rest);
      break;
    case 'down':
      await cmdDown();
      break;
    case 'status':
      await cmdStatus();
      break;
    case 'reset':
      // F-02: pass only `rest` — omit the literal "reset" string that was incorrectly injected.
      await cmdReset(rest);
      break;
    default:
      process.stderr.write(`Unknown command: ${cmd}\n\n`);
      console.log(HELP);
      process.exit(1);
  }
}

main().catch((err) => {
  process.stderr.write(`Unhandled error: ${err.message}\n`);
  process.exit(1);
});
