# _shared.ps1 — shared helpers for the consolidated Bash PreToolUse gate.
#
# Dot-source this file from pretooluse-bash-gate.ps1 AND from the standalone
# approval-policies-gate.ps1 (used on WebFetch / Chrome matchers). Every
# security-critical function lives here exactly once (DRY).
#
# Functions exported:
#   Emit-Decision        — write a PreToolUse decision JSON to stdout
#   Fail-Open-Ask        — emit ask + stderr warn, then exit 0
#   Get-ProjectId        — resolve bound project_id from file (or fixture override)
#   Invoke-CachedPolicyFetch — Lever B: TTL-cached project fetch; NO curl if fresh
#   Invoke-PolicyRuleEval    — evaluate approval_policies rules against a tool call
#
# Lever B cache contract:
#   Cache file: _runtime\approval_policies_cache_<projectId>.json
#   Shape: { "fetched_at_unix": <int>, "policies": <approval_policies-or-null>,
#            "is_killed": <bool> }
#   TTL: 60 seconds. Fresh -> use cached value, NO curl.
#   ANY cache read/parse error -> ignore cache, do a live fetch (fail-safe).
#   Live fetch failure -> return sentinel @{ failed = $true } (caller → ask).
#   The cached value is the `approval_policies` sub-field plus the `is_killed` flag of
#   the project row (NOT the full row) — compact, staleness bounded by the TTL.
#   is_killed is consumed by block-spawn-on-killed-project.ps1 (R2/#2541) so the spawn
#   gate shares this cache instead of doing its own per-spawn GET.
#
# Test overrides (env vars, same contract as original gate):
#   APPROVAL_POLICIES_GATE_PROJECT_FILE  — path to a fake lead_project_id.txt
#   APPROVAL_POLICIES_GATE_POLICY_FILE   — path to a fake project-row JSON file
#                                          (bypasses ALL HTTP; also bypasses cache)
#   APPROVAL_POLICIES_CACHE_TTL_SECONDS  — override TTL for testing (default 60)
#   APPROVAL_POLICIES_CACHE_DIR          — override cache dir for testing

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Emit-Decision
# ---------------------------------------------------------------------------
function Emit-Decision {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('allow', 'deny', 'ask')][string]$Decision,
        [Parameter(Mandatory = $true)][string]$Reason
    )
    $out = @{
        hookSpecificOutput = @{
            hookEventName            = "PreToolUse"
            permissionDecision       = $Decision
            permissionDecisionReason = $Reason
        }
    } | ConvertTo-Json -Compress -Depth 6
    Write-Output $out
}

# ---------------------------------------------------------------------------
# Fail-Open-Ask
# ---------------------------------------------------------------------------
function Fail-Open-Ask {
    param([string]$WarnMsg, [string]$Source = 'approval-policies-gate')
    [Console]::Error.WriteLine("WARN: ${Source}: $WarnMsg ; falling through to ask")
    Emit-Decision -Decision 'ask' -Reason "${Source} fallthrough: $WarnMsg"
    exit 0
}

# ---------------------------------------------------------------------------
# Get-ProjectId
# Resolves the bound project_id.  Returns $null on any failure (caller decides).
# ---------------------------------------------------------------------------
function Get-ProjectId {
    $projectIdFile = $env:APPROVAL_POLICIES_GATE_PROJECT_FILE
    if (-not $projectIdFile) {
        # Derive repo root from caller's $PSScriptRoot or fallback to CLAUDE_PROJECT_DIR.
        # Both the standalone gate and the dispatcher live in .claude/hooks/; the
        # _runtime dir is two levels up (repo root).
        $repoRoot = $null
        if ($PSScriptRoot) {
            $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
        } elseif ($env:CLAUDE_PROJECT_DIR) {
            $repoRoot = $env:CLAUDE_PROJECT_DIR
        }
        if (-not $repoRoot) { return $null }
        $projectIdFile = Join-Path $repoRoot '_runtime\lead_project_id.txt'
    }

    if (-not (Test-Path $projectIdFile)) { return $null }
    $raw = (Get-Content -Raw -Path $projectIdFile).Trim()
    $projectId = 0
    if (-not [int]::TryParse($raw, [ref]$projectId) -or $projectId -le 0) { return $null }
    return $projectId
}

# ---------------------------------------------------------------------------
# Invoke-CachedPolicyFetch  (Lever B)
#
# Returns a result object:
#   { policies = <PSObject|$null>; is_killed = <bool>; failed = $false }  on success
#   { policies = $null;           is_killed = $false;  failed = $true  }  on infra error
#
# "policies" is the `approval_policies` sub-field (may be $null if the project
# has none — that is a success, not a failure). "is_killed" mirrors the project
# row's kill-switch flag for the spawn gate (R2/#2541).
# ---------------------------------------------------------------------------
function Invoke-CachedPolicyFetch {
    param(
        [Parameter(Mandatory = $true)][int]$ProjectId
    )

    $success = [pscustomobject]@{ policies = $null; is_killed = $false; failed = $false }

    # --- Test override: APPROVAL_POLICIES_GATE_POLICY_FILE -------------------
    # When set, skip both cache AND HTTP; load the fixture file directly.
    # Preserves the same fixture-override contract as the original gate.
    $policyFile = $env:APPROVAL_POLICIES_GATE_POLICY_FILE
    if ($policyFile) {
        if (-not (Test-Path $policyFile)) {
            return [pscustomobject]@{ policies = $null; failed = $true }
        }
        try {
            $projectJson = (Get-Content -Raw -Path $policyFile) | ConvertFrom-Json
            $success.policies = $projectJson.approval_policies
            $success.is_killed = [bool]$projectJson.is_killed
            return $success
        } catch {
            return [pscustomobject]@{ policies = $null; failed = $true }
        }
    }

    # --- Derive cache file path ----------------------------------------------
    $ttlSeconds = 60
    if ($env:APPROVAL_POLICIES_CACHE_TTL_SECONDS) {
        $parsed = 0
        if ([int]::TryParse($env:APPROVAL_POLICIES_CACHE_TTL_SECONDS, [ref]$parsed) -and $parsed -ge 0) {
            $ttlSeconds = $parsed
        }
    }

    $cacheDir = $env:APPROVAL_POLICIES_CACHE_DIR
    if (-not $cacheDir) {
        # _runtime/ lives at the repo root — same derivation as Get-ProjectId.
        $repoRoot = $null
        if ($PSScriptRoot) {
            $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..')
        } elseif ($env:CLAUDE_PROJECT_DIR) {
            $repoRoot = $env:CLAUDE_PROJECT_DIR
        }
        if ($repoRoot) {
            $cacheDir = Join-Path $repoRoot '_runtime'
        }
    }

    $cacheFile = $null
    if ($cacheDir) {
        $cacheFile = Join-Path $cacheDir "approval_policies_cache_${ProjectId}.json"
    }

    # --- Try reading cache ---------------------------------------------------
    if ($cacheFile -and (Test-Path $cacheFile)) {
        try {
            $cached = (Get-Content -Raw -Path $cacheFile) | ConvertFrom-Json
            $fetchedAt = [int]$cached.fetched_at_unix
            $nowUnix   = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
            $ageSeconds = $nowUnix - $fetchedAt
            if ($ageSeconds -ge 0 -and $ageSeconds -lt $ttlSeconds) {
                # Cache hit — return without curling.
                $success.policies = $cached.policies
                $success.is_killed = [bool]$cached.is_killed
                return $success
            }
            # Cache expired — fall through to live fetch.
        } catch {
            # Corrupt/unreadable cache — ignore and do a live fetch.
            # NEVER let a bad cache suppress a deny; live fetch is the safe path.
        }
    }

    # --- Live fetch ----------------------------------------------------------
    $apiUrl = "http://localhost:8456/api/projects/$ProjectId"
    $body = $null
    try {
        $body = & curl.exe --silent --max-time 3 --fail -H "X-Project-Id: $ProjectId" $apiUrl 2>$null
    } catch {
        return [pscustomobject]@{ policies = $null; failed = $true }
    }
    if ($LASTEXITCODE -ne 0 -or -not $body) {
        return [pscustomobject]@{ policies = $null; failed = $true }
    }

    $projectJson = $null
    try {
        $projectJson = $body | ConvertFrom-Json
    } catch {
        return [pscustomobject]@{ policies = $null; failed = $true }
    }

    # --- Write-through cache -------------------------------------------------
    if ($cacheFile) {
        try {
            $nowUnix = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
            $cacheObj = @{
                fetched_at_unix = $nowUnix
                policies        = $projectJson.approval_policies
                is_killed       = [bool]$projectJson.is_killed
            } | ConvertTo-Json -Compress -Depth 8
            # Ensure _runtime/ exists (it should, but guard anyway).
            $cacheParent = Split-Path -Parent $cacheFile
            if ($cacheParent -and -not (Test-Path $cacheParent)) {
                New-Item -ItemType Directory -Path $cacheParent -Force | Out-Null
            }
            [System.IO.File]::WriteAllText(
                $cacheFile, $cacheObj,
                (New-Object System.Text.UTF8Encoding($false))
            )
        } catch {
            # Cache write failure is non-fatal; we still have the live result.
        }
    }

    $success.policies = $projectJson.approval_policies
    $success.is_killed = [bool]$projectJson.is_killed
    return $success
}

# ---------------------------------------------------------------------------
# Test-PolicyRule  (internal helper)
# ---------------------------------------------------------------------------
function Test-PolicyRule {
    param($Rule, [string]$ToolName, [string]$Url, [string]$Content)
    $match = $Rule.match
    if ($null -eq $match) { return $false }

    $sawLayerBKey = $false

    if ($match.PSObject.Properties.Name -contains 'tool_name') {
        $sawLayerBKey = $true
        $want = [string]$match.tool_name
        if ($want -and $want -ne $ToolName) { return $false }
    }
    if ($match.PSObject.Properties.Name -contains 'target_url_pattern') {
        $sawLayerBKey = $true
        $pat = [string]$match.target_url_pattern
        if ($pat) {
            if (-not $Url) { return $false }
            try {
                if (-not [regex]::IsMatch($Url, $pat)) { return $false }
            } catch { return $false }
        }
    }
    if ($match.PSObject.Properties.Name -contains 'content_predicate') {
        $sawLayerBKey = $true
        $pat = [string]$match.content_predicate
        if ($pat) {
            try {
                if (-not [regex]::IsMatch($Content, $pat)) { return $false }
            } catch { return $false }
        }
    }
    if (-not $sawLayerBKey) { return $false }
    return $true
}

# ---------------------------------------------------------------------------
# Invoke-PolicyRuleEval
#
# Evaluate the approval_policies rules against a tool call.
# Returns a result object:
#   { matched = $true; decision = 'allow'|'deny'|'ask'; reason = <string> }
#   { matched = $false }   — no rule matched → caller emits default-allow
# ---------------------------------------------------------------------------
function Invoke-PolicyRuleEval {
    param(
        [Parameter(Mandatory = $true)]$Policies,   # approval_policies sub-object
        [Parameter(Mandatory = $true)][string]$ToolName,
        [string]$TargetUrl    = $null,
        [string]$SerializedContent = ''
    )

    $noMatch = [pscustomobject]@{ matched = $false }

    if ($null -eq $Policies) { return $noMatch }
    $rules = $Policies.rules
    if ($null -eq $rules -or $rules.Count -eq 0) { return $noMatch }

    foreach ($rule in $rules) {
        if (Test-PolicyRule -Rule $rule -ToolName $ToolName -Url $TargetUrl -Content $SerializedContent) {
            $action    = [string]$rule.action
            $ruleName  = if ($rule.name)   { [string]$rule.name }   else { '(unnamed rule)' }
            $ruleReason = if ($rule.reason) { [string]$rule.reason } else { "matched rule '$ruleName'" }

            switch ($action) {
                'auto_approve' {
                    return [pscustomobject]@{
                        matched = $true; decision = 'allow'
                        reason = "approval-policies-gate: $ruleName — $ruleReason"
                    }
                }
                'auto_deny' {
                    return [pscustomobject]@{
                        matched = $true; decision = 'deny'
                        reason = "approval-policies-gate: $ruleName — $ruleReason"
                    }
                }
                'requires_attention' {
                    return [pscustomobject]@{
                        matched = $true; decision = 'ask'
                        reason = "approval-policies-gate: $ruleName — $ruleReason"
                    }
                }
                default {
                    [Console]::Error.WriteLine("WARN: approval-policies-gate: rule '$ruleName' has unknown action '$action' ; treating as ask")
                    return [pscustomobject]@{
                        matched = $true; decision = 'ask'
                        reason = "approval-policies-gate: $ruleName has unknown action '$action'"
                    }
                }
            }
        }
    }

    return $noMatch
}
