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

Prerequisites:
  Docker Desktop (or Docker Engine) must be installed and running.
  git must be installed and on PATH (required for standalone/clone mode).
  Neither Docker nor git is installed by this CLI.
  Docker: https://docs.docker.com/get-docker/
  git:    https://git-scm.com/downloads

Examples:
  npx @bankung/agent-teams up
  npx @bankung/agent-teams status
  npx @bankung/agent-teams down
  npx @bankung/agent-teams reset --yes
  npx @bankung/agent-teams up ~/my-agent-teams

Bin alias: the package exposes the \`agent-teams\` bin alias.
After a global install (\`npm install -g @bankung/agent-teams\`) you can run:
  agent-teams up
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
// Subcommand: up
// ---------------------------------------------------------------------------
async function cmdUp(argv) {
  // Optional positional argument: targetDir for standalone clone
  const positional = argv.filter((a) => !a.startsWith('-'));
  const targetDir  = positional[0] || null;

  // 1. Docker daemon check
  const docker = checkDocker();
  if (!docker.ok) die(docker.message, 1);
  log('Docker daemon OK.');

  // 2. Resolve repo root (IN-REPO or STANDALONE/clone)
  const repoRoot = await resolveRepoRoot(targetDir);

  // 3. .env scaffold + CREDENTIALS_MASTER_KEY
  ensureEnv(repoRoot);

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
// Subcommand: down
// ---------------------------------------------------------------------------
async function cmdDown() {
  const docker = checkDocker();
  if (!docker.ok) die(docker.message, 1);

  // Resolve repo root so compose cwd is correct in standalone mode too.
  // For down we don't need to clone — if no compose file exists, nothing to stop.
  const composeFile = path.join(PKG_PARENT, 'docker-compose.yml');
  const repoRoot = fs.existsSync(composeFile) ? PKG_PARENT : process.cwd();

  log('Stopping services (volumes preserved)...');
  const code = await compose(['down'], {}, { cwd: repoRoot });
  process.exit(code);
}

// ---------------------------------------------------------------------------
// Subcommand: status
// ---------------------------------------------------------------------------
async function cmdStatus() {
  const docker = checkDocker();
  if (!docker.ok) die(docker.message, 1);

  const composeFile = path.join(PKG_PARENT, 'docker-compose.yml');
  const repoRoot = fs.existsSync(composeFile) ? PKG_PARENT : process.cwd();

  // Show `docker compose ps` output
  log('Container status:');
  await compose(['ps'], {}, { cwd: repoRoot });

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
  await cmdUp([]);
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
      await cmdReset([cmd, ...rest]);
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
