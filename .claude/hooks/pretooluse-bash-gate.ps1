# pretooluse-bash-gate.ps1 — consolidated Bash PreToolUse dispatcher (Lever A).
#
# Replaces the 5 sequential Bash PreToolUse hooks with ONE process:
#   approval-policies-gate.ps1   (policy eval + Lever B cache)
#   block-raw-sql-dml.ps1        (deny raw SQL DML)
#   block-curl-delete.ps1        (ask on curl DELETE)
#   block-bitdefender-triggers.ps1 (deny AV-trigger shapes)
#   block-pytest-on-live-db.ps1  (deny pytest against live DB)
#
# Guards fire in THE SAME ORDER as the original settings.json array
# (approval-policies first, then the four block-* in list order).
# First guard that emits a decision exits immediately — identical to the
# sequential hook chain behaviour.
#
# Fail-open-to-ask on infra error (payload unreadable / project_id missing /
# API unreachable) — same as original approval-policies-gate.ps1.
# Fail-safe (deny) for the block-* guards — same as the originals.
#
# Promote path: _scratch/hooks-draft/pretooluse-bash-gate.ps1
#            -> .claude/hooks/pretooluse-bash-gate.ps1
# Then update settings.json Bash PreToolUse to single entry (see
# _scratch/hooks-draft/settings-bash-matcher.json).

$ErrorActionPreference = 'Stop'

# Dot-source shared helpers (same dir as this script).
. (Join-Path $PSScriptRoot '_shared.ps1')

# ---------------------------------------------------------------------------
# Read stdin once
# ---------------------------------------------------------------------------
$payloadRaw = $null
$payload    = $null
try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) { Fail-Open-Ask -WarnMsg 'empty PreToolUse payload' -Source 'pretooluse-bash-gate' }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    Fail-Open-Ask -WarnMsg "payload not valid JSON: $($_.Exception.Message)" -Source 'pretooluse-bash-gate'
}

$toolName = $payload.tool_name
if (-not $toolName) { Fail-Open-Ask -WarnMsg 'tool_name missing from payload' -Source 'pretooluse-bash-gate' }

$toolInput = $payload.tool_input
$cmd = if ($toolInput) { [string]$toolInput.command } else { '' }

# Severity aggregation — preserve the original deny > ask > allow precedence ACROSS
# all guards (the 5 separate hooks let Claude Code take the most-restrictive result).
# A 'deny' from any guard short-circuits immediately (deny is maximal). An 'ask' is
# RECORDED here (not exited) so a later guard can still escalate to deny. If no guard
# denies, we emit ask (if any was recorded) else allow. This is what stops an operator
# auto_approve rule from suppressing a block-* deny.
$askReason = $null

# ---------------------------------------------------------------------------
# GUARD 1 — approval-policies-gate  (Lever B cached)
# Same logic as approval-policies-gate.ps1, using shared helpers.
# ---------------------------------------------------------------------------
$projectId = Get-ProjectId
$policies = $null
if ($null -eq $projectId) {
    # Infra error at the approval stage — record an ask candidate but DO NOT exit;
    # the block-* deny guards below still run on $cmd and must win over ask.
    [Console]::Error.WriteLine("WARN: pretooluse-bash-gate: _runtime/lead_project_id.txt missing or invalid ; ask candidate")
    if (-not $askReason) { $askReason = 'pretooluse-bash-gate fallthrough: _runtime/lead_project_id.txt missing or invalid' }
} else {
    $fetchResult = Invoke-CachedPolicyFetch -ProjectId $projectId
    if ($fetchResult.failed) {
        [Console]::Error.WriteLine("WARN: pretooluse-bash-gate: API unreachable for project $projectId ; ask candidate")
        if (-not $askReason) { $askReason = "pretooluse-bash-gate fallthrough: API unreachable for project $projectId" }
    } else {
        $policies = $fetchResult.policies
    }
}

if ($null -ne $policies) {
    # Extract URL and serialized content for rule matching.
    $targetUrl         = $null
    $serializedContent = ''
    if ($toolInput) {
        if ($toolInput.PSObject.Properties.Name -contains 'url') {
            $targetUrl = [string]$toolInput.url
        } elseif ($toolName -eq 'Bash' -and $toolInput.PSObject.Properties.Name -contains 'command') {
            $urlMatch = [regex]::Match($cmd, '(?i)https?://[^\s"''<>]+')
            if ($urlMatch.Success) { $targetUrl = $urlMatch.Value }
        }
        try {
            $serializedContent = $toolInput | ConvertTo-Json -Compress -Depth 6
        } catch {
            $serializedContent = [string]$toolInput
        }
    }

    $evalResult = Invoke-PolicyRuleEval `
        -Policies $policies `
        -ToolName $toolName `
        -TargetUrl $targetUrl `
        -SerializedContent $serializedContent

    if ($evalResult.matched) {
        # Preserve precedence:
        #   auto_deny (deny)            → maximal → emit + short-circuit (exit 2).
        #   requires_attention (ask)    → record ask; a later block-* deny can still win.
        #   auto_approve (allow)        → lowest severity; record NOTHING. An operator
        #                                 auto_approve rule must NOT suppress a block-* deny.
        if ($evalResult.decision -eq 'deny') {
            Emit-Decision -Decision 'deny' -Reason $evalResult.reason
            exit 2
        } elseif ($evalResult.decision -eq 'ask') {
            if (-not $askReason) { $askReason = $evalResult.reason }
        }
    }
    # No rule matched → fall through to block-* guards then default-allow.
}
# approval_policies null → no policy constraints; fall through.

# ---------------------------------------------------------------------------
# GUARD 2 — block-raw-sql-dml  (deny)
# Mirror of block-raw-sql-dml.ps1 logic, in-process.
# ---------------------------------------------------------------------------
if ($cmd) {
    $firstWord = (($cmd -replace '^\s+', '') -split '\s+')[0]
    $safeWrappers = @('git', 'echo', 'cat', 'head', 'tail', 'less', 'more',
                      'ls', 'pwd', 'cd', 'grep', 'awk', 'sed', 'find',
                      'diff', 'wc', 'sort', 'uniq', 'cut', 'tr')
    if ($safeWrappers -notcontains $firstWord) {
        $isPsqlExec   = $cmd -match '\bpsql\b[^\|;]*\s-c\b'
        $isPythonExec = $cmd -match '\bpython3?\b[^\|;]*\s-c\b'
        if ($isPsqlExec -or $isPythonExec) {
            $dmlPatterns = @(
                '\bDELETE\s+FROM\b',
                '\bUPDATE\s+\w+\s+SET\b',
                '\bINSERT\s+INTO\b',
                '\bTRUNCATE\b',
                '\bDROP\s+(TABLE|DATABASE|SCHEMA|INDEX|CONSTRAINT|VIEW)\b',
                '\bALTER\s+TABLE\b'
            )
            foreach ($pattern in $dmlPatterns) {
                if ($cmd -match "(?i)$pattern") {
                    $reason = @"
Raw SQL DML detected (pattern: $pattern).

Subagents must NEVER execute destructive SQL via psql -c or python -c — even for cleanup of
test-leaked rows, even on already-soft-deleted rows, even when the call looks "obviously safe".

Required behavior:
  1. Diagnose with SELECT / \d / EXPLAIN (read-only — these are not blocked).
  2. Propose the exact statement + row counts in your final report.
  3. Stop. Lead surfaces to user; user runs it.

If you are the user and want to run this manually, edit .claude/settings.json to remove the
PreToolUse hook (or run the command in a separate terminal outside Claude Code). The friction
of disabling the hook IS the gate — see .claude/docs/lessons.md "Raw SQL DML is human-only".
"@
                    Emit-Decision -Decision 'deny' -Reason $reason
                    exit 2
                }
            }
        }
    }
}

# ---------------------------------------------------------------------------
# GUARD 3 — block-curl-delete  (ask)
# Mirror of block-curl-delete.ps1 logic, in-process.
# ---------------------------------------------------------------------------
if ($cmd) {
    $tokens    = ($cmd -replace '^\s+', '') -split '\s+'
    $firstWord = $tokens[0]
    while ($firstWord -match '^[A-Z_][A-Z0-9_]*=') {
        $tokens    = $tokens | Select-Object -Skip 1
        $firstWord = $tokens[0]
    }
    if ($firstWord -match '^curl(\.exe)?$') {
        if ($cmd -match '(?i)(?:^|\s)(?:-X|--request)\s+DELETE\b') {
            $reason = @"
curl DELETE detected — forcing permission prompt (overriding allowlist).

The trailing-wildcard allowlist patterns (Bash(curl ... :*)) accept any suffix,
which would let `-X DELETE` slip in via the wildcard tail. This hook routes
every curl DELETE through the normal permission prompt so the user gets a
deliberate yes/no on each one.

If you (the user) intend this DELETE: click "yes" at the prompt.
Otherwise: click "no".

Preferred alternatives for routine task removal:
  - Soft-delete via API: PATCH /api/tasks/{id} with {"process_status": 6}
  - Hard-delete via direct human-approved DB op (separate terminal, manual psql)
"@
            # curl DELETE is an ASK (not deny). Record it; do NOT exit — a later
            # block-* guard could still escalate this command to deny.
            if (-not $askReason) { $askReason = $reason }
        }
    }
}

# ---------------------------------------------------------------------------
# GUARD 4 — block-bitdefender-triggers  (deny)
# Mirror of block-bitdefender-triggers.ps1 logic, in-process.
# ---------------------------------------------------------------------------
if ($cmd) {
    $bdTriggers = @(
        @{
            pattern = ';\s*\$\w+\s*=\s*\$LASTEXITCODE'
            hint    = 'Multi-statement chain capturing $LASTEXITCODE. Split into separate tool calls.'
        },
        @{
            pattern = ';\s*exit\s+\$'
            hint    = 'Multi-statement chain with explicit exit-code propagation. Split into separate tool calls.'
        },
        @{
            pattern = 'Out-File[^\r\n]*LocalAppData[^\r\n]*Temp[^\r\n]*claude'
            hint    = 'Out-File writing to %LocalAppData%\Temp\claude\ inside a -Command chain. Use the Write tool instead.'
        },
        @{
            pattern = '-EncodedCommand\s+[A-Za-z0-9+/]{40,}'
            hint    = '-EncodedCommand with non-trivial base64 payload. Use plain -Command (single statement) or the Write tool.'
        },
        @{
            pattern = '-NoProfile[^"\r\n]*-NonInteractive[^"\r\n]*-Command\s+"[^"]*;'
            hint    = '-NoProfile -NonInteractive -Command with multi-statement chain. Split into separate calls.'
        }
    )

    foreach ($t in $bdTriggers) {
        if ($cmd -match $t.pattern) {
            $reason = @"
Bitdefender-trigger pattern detected (matched: $($t.pattern)).

$($t.hint)

Why blocked:
  Bitdefender heuristic flags certain PowerShell invocation shapes (multi-statement -Command
  chains with exit-code capture, Out-File to claude temp, encoded payloads). Blocked calls
  waste turn time and surface as opaque AV errors.

Common fixes:
  - Multi-step shell work: separate single-purpose Bash/PowerShell calls instead of ;-chained.
  - File writes: use the Write tool (FS-mediated, no shell wrapper).
  - Exit code propagation: run command standalone, then check `$LASTEXITCODE` in a follow-up call.
  - For health probes: split into separate curl + Write-Output rather than ;-chained exit propagation.
"@
            Emit-Decision -Decision 'deny' -Reason $reason
            exit 2
        }
    }
}

# ---------------------------------------------------------------------------
# GUARD 5 — block-pytest-on-live-db  (deny)
# Mirror of block-pytest-on-live-db.ps1 logic, in-process.
# ---------------------------------------------------------------------------
if ($cmd -and ($cmd -match '(?i)\bpytest\b')) {
    # Bypass valve.
    if ($env:BYPASS_LIVE_DB_PYTEST_HOOK -eq "1") {
        [Console]::Error.WriteLine("[BYPASS] pretooluse-bash-gate: block-pytest-on-live-db BYPASSED via BYPASS_LIVE_DB_PYTEST_HOOK=1")
        # fall through to allow
    } else {
        # L1.5 check #1 — python -c "...pytest..."
        if ($cmd -match '(?i)python\s+-c\s+["''][^"'']*pytest') {
            $reason = @"
pytest blocked: invocation via 'python -c "...pytest..."' looks like a hook-bypass attempt.

There is no legitimate reason to invoke pytest via 'python -c' in this repo.
If you have a real need, run pytest directly (or via 'python -m pytest') so
the L1 hook + conftest in-process rewrite can verify the DB target.

See context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.

Bypass valve (rare legitimate needs): set BYPASS_LIVE_DB_PYTEST_HOOK=1 in
the SAME shell — the hook honours it and emits a [BYPASS] marker for audit.
"@
            Emit-Decision -Decision 'deny' -Reason $reason
            exit 2
        }

        # L1.5 check #2 — inline DATABASE_URL= prefix
        $inlineMatch = [regex]::Match($cmd, '(?i)DATABASE_URL=([^\s"'']+)')
        if ($inlineMatch.Success) {
            $inlineUrl  = $inlineMatch.Groups[1].Value
            $normalized = ($inlineUrl -replace '\?.*$', '') -replace '/+$', ''
            if ($normalized -notmatch '(?i)_test$') {
                $reason = @"
pytest blocked: inline DATABASE_URL=$inlineUrl in the bash command string
points at a non-_test DB.

Inline `DATABASE_URL=... pytest ...` (or `DATABASE_URL=... docker compose exec
api pytest ...`) bypasses the parent-shell env check because bash inline env
does not propagate to the PowerShell parent scope. This is the exact pattern
that wiped the dev DB on 2026-05-17.

Either:
  - drop the inline prefix and let conftest's in-process rewrite handle it, or
  - set the inline URL explicitly to ...agent_teams_test.

See context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.

Bypass valve (rare legitimate needs): set BYPASS_LIVE_DB_PYTEST_HOOK=1 in
the SAME shell — the hook honours it and emits a [BYPASS] marker for audit.
"@
                Emit-Decision -Decision 'deny' -Reason $reason
                exit 2
            }
        }

        # L1.5 check #3 — docker compose exec ... pytest
        if ($cmd -match '(?i)docker\s+compose\s+(-p\s+\S+\s+)?exec\s+.*\bpytest\b') {
            if ($env:DOCKER_PYTEST_VERIFIED -ne "1") {
                $reason = @"
pytest blocked: 'docker compose exec ... pytest' uses CONTAINER env, not the
parent shell env. The L1 hook cannot verify the container's DATABASE_URL
from outside the container.

Before re-running, MANUALLY verify the container's DATABASE_URL targets a
_test DB:

    docker compose exec api printenv DATABASE_URL

If — and only if — the printed URL ends in `_test`, set
DOCKER_PYTEST_VERIFIED=1 in the SAME shell and retry. This is the operator
attestation that you checked the container env.

See context/projects/agent-teams/shared/incidents/2026-05-17-dev-db-wipe.md.

Bypass valve (rare legitimate needs): set BYPASS_LIVE_DB_PYTEST_HOOK=1 in
the SAME shell — the hook honours it and emits a [BYPASS] marker for audit.
"@
                Emit-Decision -Decision 'deny' -Reason $reason
                exit 2
            }
        }

        # L1 — parent-shell DATABASE_URL
        $dbUrl = $env:DATABASE_URL
        if ($dbUrl) {
            $normalized = ($dbUrl -replace '\?.*$', '') -replace '/+$', ''
            if ($normalized -notmatch '(?i)_test$') {
                $reason = @"
pytest blocked: DATABASE_URL is live-pointed ($dbUrl).
Either unset DATABASE_URL (let conftest's in-process rewrite handle it)
or set DATABASE_URL=postgresql://...agent_teams_test explicitly.

This hook prevents the 2026-05-17 dev DB wipe class of incident
(pytest fixtures leaking destructive DDL/DML into the live agent_teams
DB via lru_cache poisoning). See context/projects/agent-teams/shared/
incidents/2026-05-17-dev-db-wipe.md.

Bypass valve (for rare legitimate live-DB pytest needs): set env var
BYPASS_LIVE_DB_PYTEST_HOOK=1 in the SAME shell — the hook honours it
and emits a warning marker for audit.
"@
                Emit-Decision -Decision 'deny' -Reason $reason
                exit 2
            }
        }
    }
}

# ---------------------------------------------------------------------------
# Default: no guard fired → allow
# (Mirrors the original approval-policies default-allow at #1614.)
# ---------------------------------------------------------------------------
# Final severity aggregation: no guard denied. Emit ask if any guard recorded one,
# else default-allow (mirrors the original approval-policies default-allow at #1614).
if ($askReason) {
    Emit-Decision -Decision 'ask' -Reason $askReason
    exit 0
}
Emit-Decision -Decision 'allow' -Reason 'pretooluse-bash-gate: no guard matched — default allow'
exit 0
