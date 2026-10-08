---
name: zb-test
description: >-
  Run agent-teams api pytest IN-SESSION against the isolated api-test + db-test containers
  (internal network, no route to the live db) — scoped selector or full suite — and report the
  real output. Use when the Lead or a subagent needs to run api tests: "run the tests", "pytest
  this", "zb-test", "check the test file passes". NOT for super-critical runs the operator wants
  to witness (use bin/run-tests.ps1, operator-run) and NOT for langgraph tests.
argument-hint: "<tests/x.py[::test] ...> | full"
allowed-tools:
  - Bash(docker compose -p agent-teams --profile test run --rm api-test pytest:*)
  - Bash(docker compose -p agent-teams --profile test stop db-test)
  - Bash(docker compose -p agent-teams --profile test rm -f db-test)
  - Bash(docker network rm agent-teams_testnet)
metadata:
  version: 1.0.0
  category: testing
  tags: [pytest, test, isolated, api-test]
---

# /zb-test — in-session pytest on the isolated test stack (#3480)

Selectors are in `$ARGUMENTS` (paths relative to `/repo/api`, e.g. `tests/test_pricing.py` or
`tests/test_x.py::test_y`; `full` = whole suite).

## Why this is safe (do not "simplify" it away)
`api-test` and `db-test` (docker-compose.yml, profile `test`) sit ONLY on the internal `testnet`
network: the live `db` hostname does not resolve, there is no route to it, no live secret is passed
in, and there is no internet. The entrypoint migrates the throwaway `db-test` first. The Bash gate
(GUARD 5) allows exactly the command shape below and denies `run ... api ... pytest` and plain
`docker exec ... pytest`.

## Step 1 — run (exact shape; no chaining, quotes, `-e`, or `--entrypoint`)
```
docker compose -p agent-teams --profile test run --rm api-test pytest -q <selectors>
```
- `full` → drop the selectors. The full suite takes ~15-20 min: run it with `run_in_background`
  and wait for the completion notice (do not poll).
- Selectors may only contain letters, digits, `_ . / : - [ ]` and spaces (the gate rejects anything
  else); use separate selectors instead of `-k "..."` expressions.

## Step 2 — clean up (every run)
```
docker compose -p agent-teams --profile test stop db-test
docker compose -p agent-teams --profile test rm -f db-test
docker network rm agent-teams_testnet
```
(The network rm may report "not found" when compose already removed it — fine.)

## Step 3 — report
Paste the real pytest tail (the pass/fail line and any failure blocks), not a count. For new tests,
re-run them with `-v`. A failing collection/migration step is a failure — report it verbatim.

## When to use the operator runner instead
`bin/run-tests.ps1` (#3479, operator-only) runs in the LIVE api container with a before/after live
count check. Use it for super-critical runs the operator wants to witness, or as the fallback when
the test stack is broken.

## Related skills
- `zb-task-done` — AC that cite tests point at this skill's pasted output.
- `zb-git-commit` — commit after the tests pass.
