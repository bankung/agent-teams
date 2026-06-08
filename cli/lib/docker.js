'use strict';
// cli/lib/docker.js — Docker daemon check + compose runner helpers.
// Zero runtime dependencies; uses only Node built-ins.

const { spawnSync, spawn } = require('child_process');

/**
 * Check whether the `git` CLI is on PATH.
 * Returns { ok: true } or { ok: false, message: string }.
 */
function checkGit() {
  const result = spawnSync('git', ['--version'], { stdio: 'pipe', shell: false });
  if (result.error || result.status === null) {
    return {
      ok: false,
      message: [
        'git is not installed or not on PATH.',
        'Install Git (https://git-scm.com/downloads) and re-run this command.',
        'git is required to clone the agent-teams repository in standalone mode.',
      ].join('\n'),
    };
  }
  return { ok: true };
}

/**
 * Check whether the Docker CLI is on PATH and the daemon is reachable.
 * Returns { ok: true } or { ok: false, message: string }.
 */
function checkDocker() {
  // 1. Is `docker` on PATH?
  const whichResult = spawnSync('docker', ['--version'], { stdio: 'pipe', shell: false });
  if (whichResult.error || whichResult.status === null) {
    return {
      ok: false,
      message: [
        'Docker is not installed or not on PATH.',
        'Install Docker Desktop (https://docs.docker.com/get-docker/) and re-run this command.',
        'Docker is a required prerequisite — npm does not install it.',
      ].join('\n'),
    };
  }

  // 2. Is the daemon responding? (`docker info` exits non-zero when daemon is down.)
  // F-04: timeout:10000 prevents a hung daemon from freezing the install indefinitely.
  const infoResult = spawnSync('docker', ['info'], { stdio: 'pipe', shell: false, timeout: 10000 });
  if (infoResult.status !== 0) {
    return {
      ok: false,
      message: [
        'Docker is installed but the daemon is not responding.',
        'Start Docker Desktop (or run `sudo systemctl start docker` on Linux) and re-run:',
        '    npx agent-teams up',
        'Troubleshooting: https://docs.docker.com/get-docker/',
      ].join('\n'),
    };
  }

  return { ok: true };
}

/**
 * Run `docker compose -p agent-teams [-f <composeFile>] <args>` with stdio inherited.
 * Returns exit code (integer). Streams all output directly to the terminal.
 *
 * @param {string[]} args        Arguments after the compose preamble
 * @param {object}   [env]       Additional env vars to merge (e.g. { MIGRATION_TARGET: 'live' })
 * @param {object}   [spawnOpts] Extra options forwarded to spawn() (e.g. { cwd: repoRoot })
 * @param {string}   [composeFile] Optional path to an alternate compose file (e.g. docker-compose.images.yml).
 *                                 When omitted, Docker uses the default docker-compose.yml discovery.
 */
function compose(args, env = {}, spawnOpts = {}, composeFile = null) {
  const fileArgs = composeFile ? ['-f', composeFile] : [];
  return new Promise((resolve) => {
    const child = spawn(
      'docker',
      ['compose', '-p', 'agent-teams', ...fileArgs, ...args],
      {
        stdio: 'inherit',
        shell: false,
        env: { ...process.env, ...env },
        ...spawnOpts,
      }
    );
    child.on('close', (code) => resolve(code ?? 1));
    child.on('error', (err) => {
      process.stderr.write(`ERROR: failed to spawn docker: ${err.message}\n`);
      resolve(1);
    });
  });
}

module.exports = { checkGit, checkDocker, compose };
