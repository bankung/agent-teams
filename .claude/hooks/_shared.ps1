# _shared.ps1 — shared helpers for the consolidated Bash PreToolUse gate.
#
# Dot-source this file from pretooluse-bash-gate.ps1 AND from the standalone
# approval-policies-gate.ps1 (used on the WebFetch matcher). Every
# security-critical function lives here exactly once (DRY).
#
# Functions exported:
#   Emit-Decision        — write a PreToolUse decision JSON to stdout (+ Write-GateLog)
#   Write-GateLog        — gate decision log line (#3487)
#   Fail-Open-Ask        — emit ask + stderr warn, then exit 0
#   Get-ProjectId        — resolve bound project_id from file (or fixture override)
#   Invoke-CachedPolicyFetch — Lever B: TTL-cached project fetch; NO curl if fresh
#   Invoke-PolicyRuleEval    — evaluate approval_policies rules against a tool call
#   Get-ShellSegments    — quote-aware split of a command into simple-command token lists (#3486)
#   Get-SegmentHead      — index of a segment's real command word (skips VAR=, sudo, do, ...)
#   Get-DockerVerb       — docker / docker-compose subcommand past global flags
#   Get-DestructiveHit   — option-C destructive class for one command (#3486)
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
    Write-GateLog -Decision $Decision -Reason $Reason
}

# ---------------------------------------------------------------------------
# Write-GateLog (#3487) — one tab-separated line per Bash/PowerShell gate decision to
# _runtime/pretooluse-gate.log: ts, session, agent, tool, decision, reason (first line),
# command (credential-shaped values masked). Only the gate sets $script:GateLogCmd, so
# approval-policies-gate's decisions are not logged. Allow lines need GATE_LOG_ALLOW=1 or a
# _runtime/GATE_LOG_ALLOW file (the 14-day class-tuning window). Rotates to .1 at 5 MB.
# Never throws — a log fault must not change a decision.
# ---------------------------------------------------------------------------
$script:GateLogCmd = $null; $script:GateLogMeta = ''
# Mask credential-shaped values in a log line (the whole line: a reason can quote the command too).
function Hide-Secrets {
    param([string]$Text)
    return $Text `
        -replace '(?i)((?:proxy-)?authorization\s*:\s*)(?:(?:bearer|basic|token|digest)\s+)?[^\s"'']+', '$1***' `
        -replace '(?i)(cookie\s*:\s*)[^"''\t]+', '$1***' `
        -replace '(?i)(\b[\w-]*(?:token|secret|pass(?:word|wd|phrase)?|pwd|api[_-]?key|access[_-]?key|private[_-]?key|authorization|cookie)[\w-]*["'']?\s*[:=]\s*["'']?)(?:(?:bearer|basic|token)\s+)?[^\s"'']+', '$1***' `
        -replace '(?i)(--?(?:token|password|passwd|pass|secret|api-?key|oauth2-bearer|proxy-user|cookie)[\s=]+["'']?)[^\s"'']+', '$1***' `
        -replace '(?i)(/bot\d+:)[\w-]+', '$1***' `
        -replace '\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]+', '***' `
        -replace '(\s-b\s*["'']?)[^\s"'']+', '$1***' `
        -replace '\bA(?:KIA|SIA)[0-9A-Z]{12,}', '***' `
        -replace '(\s-u\s*|\s--user[\s=])[^\s"'']+', '$1***' `
        -replace '(://[^/\s:@"'']+:)[^@\s/"'']+@', '$1***@' `
        -replace '(?i)([?&](?:key|token|access_token|api_key|sig|signature|password)=)[^&\s"'']+', '$1***' `
        -replace '\b(?:gh[pousr]_[A-Za-z0-9]{8,}|sk-[A-Za-z0-9_-]{8,}|xox[bpas]-[A-Za-z0-9-]{8,})', '***'
}
function Write-GateLog {
    param([string]$Decision, [string]$Reason)
    if ($null -eq $script:GateLogCmd) { return }
    try {
        $runtime = Join-Path $PSScriptRoot '..\..\_runtime'
        if ($Decision -eq 'allow' -and $env:GATE_LOG_ALLOW -ne '1' -and -not (Test-Path (Join-Path $runtime 'GATE_LOG_ALLOW'))) { return }
        $log = Join-Path $runtime 'pretooluse-gate.log'
        if ((Test-Path $log) -and (Get-Item $log).Length -gt 5MB) { Move-Item $log "$log.1" -Force }
        $cut = { param($s, $n) $t = ($s -replace '\s+', ' ').Trim(); if ($t.Length -gt $n) { $t.Substring(0, $n) + '...' } else { $t } }
        $line = (Get-Date).ToString('o'), $script:GateLogMeta, $Decision, (& $cut (($Reason -split "`n")[0]) 100), (& $cut $script:GateLogCmd 160) -join "`t"
        [IO.File]::AppendAllText($log, (Hide-Secrets $line) + "`n", [Text.Encoding]::UTF8)
    } catch { }
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
    param([string]$SessionId = $null)
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
        # #2692: per-session binding ONLY — read lead_project_id_<sid>.txt and NEVER
        # fall back to the global file. The global belongs to whichever session bound
        # last (possibly another project), so reading it is the cross-session
        # wrong-project bug. EVERY caller passes a session_id; a miss returns $null so
        # the gate fails open to ASK / spawn-block goes inactive — never another
        # session's project (#2692 review WARN-1). A session-less scheduled hook does
        # its own direct global read (see seo-ranking-report.ps1, KNOWN-GAP-1 #2694).
        if (-not $SessionId) { return $null }
        # Defense-in-depth (#2692 review MINOR-1/NIT-1): only UUID-shaped session ids,
        # anchored with \z (not $, which also matches before a trailing newline in PS),
        # so a crafted value can't traverse out of _runtime via the filename.
        if ($SessionId -notmatch '^[a-zA-Z0-9\-]{8,64}\z') { return $null }
        $projectIdFile = Join-Path $repoRoot ("_runtime\lead_project_id_$SessionId.txt")
    }

    # -LiteralPath so a metacharacter in the (UUID-guarded) path can't glob; the read
    # stays fail-soft (no -ErrorAction Stop: there is no surrounding try/catch here, and
    # a read error must fall through to $null = fail-open-ASK, not throw).
    if (-not (Test-Path -LiteralPath $projectIdFile)) { return $null }
    $raw = (Get-Content -Raw -LiteralPath $projectIdFile).Trim()
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


# ---------------------------------------------------------------------------
# Shell segmenting (#3486, hardened in the #3483 batch review) — rules match a SEGMENT's
# command word, never raw text, so a commit message, JSON payload, heredoc body or
# `node -e "..."` string that merely mentions `rm -rf` / `pytest` / `DELETE FROM` cannot
# trip (or dodge) a guard.
# - Segments split on unquoted ; && || | & newline (+ backtick for Bash) and, in the default
#   view, ( ) { }. The -KeepBrackets (statement) view keeps brackets inside words so flags
#   after `@{..}` / `(..)` stay on their command; the gate checks both views.
# - Heredoc bodies and here-strings are attached to their segment (-Heredocs); a heredoc
#   marker with no terminator line is not a heredoc (`$(( 1 << 2 ))`). Unquoted heredoc
#   bodies have their $( ) / backticks segmented, as the shell expands them.
# - Shells anywhere in a segment are followed: bash|sh -c / -lc, pwsh -Command (any prefix),
#   -EncodedCommand, a positional PowerShell script; a head shell with no script runs its
#   stdin (heredoc / here-string body segmented, a pipe -> synthetic __shell_stdin__).
# - Synthetic segments the destructive class asks on: __shell_stdin__, __nested_too_deep__
#   (> 3 nesting levels), __opaque_script__ (undecodable -EncodedCommand). A word left open by an
#   unclosed quote / ${ at end of input has the text after its opener re-segmented (Get-UnclosedAt).
# - A 3 s budget (-Clock) throws past its limit; the gate turns that into ask, so a slow parse
#   can never run into the hook timeout and silently skip the deny guards.
# shortcut: regex tokenizer, not a shell parser — $VAR paths, eval, ${IFS}/brace-expansion
# obfuscation and aliases stay opaque (it is a guard rail, not a sandbox); upgrade: a real
# parser if the decision log (#3487) shows misses.
# ---------------------------------------------------------------------------
function Get-SegmentHead {
    param([string[]]$Tokens)
    $prefix = @('builtin', 'do', 'then', 'else', 'elif', 'if', 'while', 'until', '!', '.')
    # wrappers that run the next word as the command; listed flags take a value
    $wrappers = @{ sudo = @('-u', '-g', '-U', '-C', '-h', '-p', '-D'); env = @('-u', '-C', '-S'); nice = @('-n')
                   xargs = @('-n', '-I', '-L', '-P', '-d', '-s', '-E', '-a'); timeout = @('-s', '-k'); stdbuf = @()
                   ionice = @('-c', '-n'); exec = @('-a'); time = @('-f', '-o'); command = @(); nohup = @(); setsid = @() }
    $i = 0
    while ($i -lt $Tokens.Count) {
        $t = $Tokens[$i]
        if ($t -match '^[A-Za-z_][A-Za-z0-9_]*\+?=' -or $prefix -contains $t) { $i++; continue }
        if ($t -match '^\$[\w:]+$' -and ($i + 1) -lt $Tokens.Count -and $Tokens[$i + 1] -in @('=', '+=')) { $i += 2; continue }
        $leaf = Get-CommandLeaf $t
        if ($wrappers.ContainsKey($leaf)) {
            $i++
            while ($i -lt $Tokens.Count -and $Tokens[$i] -like '-*') { if ($wrappers[$leaf] -ccontains $Tokens[$i]) { $i += 2 } else { $i++ } }
            if ($leaf -eq 'timeout') { $i++ }   # the duration
            continue
        }
        break
    }
    return $i
}

function Get-CommandLeaf {
    param([string]$Token)
    $t = $Token
    if ($t -match '^\$[\w:]+=(.+)$') { $t = $Matches[1] }   # PS `$r=Invoke-RestMethod`
    return ((($t -split '[\\/]')[-1]) -replace '(?i)\.exe$', '').ToLowerInvariant()
}

# What the shell token at -At runs: @{kind='script'; text; ps} (-c / -lc, -Command, -EncodedCommand,
# a positional PowerShell script), 'file' (a script path), 'opaque' (undecodable), or 'stdin'.
function Get-ShellScriptArg {
    param([string[]]$Tokens, [int]$At)
    $ps = (Get-CommandLeaf $Tokens[$At]) -in @('pwsh', 'powershell')
    for ($k = $At + 1; $k -lt $Tokens.Count; $k++) {
        $t = $Tokens[$k]
        $tail = @(if ($k + 1 -lt $Tokens.Count) { $Tokens[($k + 1)..($Tokens.Count - 1)] })
        if ($ps) {
            if ($t -match '^(?i)-(c|co|com|comm|comma|comman|command)$') { return @{ kind = 'script'; ps = $true; text = ($tail -join ' ') } }
            if ($t -match '^(?i)-(e|ec|en|enc|enco|encod|encode|encoded\w*)$') {
                try { return @{ kind = 'script'; ps = $true; text = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String([string]$tail[0])) } }
                catch { return @{ kind = 'opaque' } }
            }
            if ($t -match '^(?i)-(f|fi|fil|file)$') { return @{ kind = 'file' } }
            if ($t -match '^(?i)-(ex\w*|ep|w|wi\w*|of|ou\w*|if|inp\w*|v|ve\w*|conf\w*|wd|wo\w*|se\w*|ps\w*)$') { $k++; continue }
            if ($t -like '-*') { continue }
            return @{ kind = 'script'; ps = $true; text = (@($Tokens[$k..($Tokens.Count - 1)]) -join ' ') }
        }
        if ($t -cmatch '^-[a-zA-Z]*c[a-zA-Z]*$') {   # -c, -lc, -ce, -c -e ... : the script is the next non-option word
            foreach ($w in $tail) { if ($w -notlike '-*' -and $w -notlike '+*') { return @{ kind = 'script'; ps = $false; text = $w } } }
            return @{ kind = 'stdin' }
        }
        if ($t -in @('-o', '+o', '-O', '+O', '--rcfile', '--init-file')) { $k++; continue }
        if ($t -like '-*' -or $t -like '+*') { continue }
        return @{ kind = 'file' }
    }
    return @{ kind = 'stdin' }
}

# Index of the `)` closing a $( whose body starts at -At (or the text length when unclosed). Quote-
# aware like the shell: parens inside '..' or ".." do not count; a ".." may hold a nested $( .. ).
function Find-SubstEnd {
    param([string]$Text, [int]$At, [char]$Esc)
    $stack = New-Object 'System.Collections.Generic.Stack[char]'; $stack.Push([char]'(')
    $i = $At
    while ($i -lt $Text.Length) {
        $c = $Text[$i]
        if ($c -eq $Esc) { $i += 2; continue }
        if ($stack.Peek() -eq [char]'"') {
            if ($c -eq '"') { [void]$stack.Pop() }
            elseif ($c -eq '$' -and $i + 1 -lt $Text.Length -and $Text[$i + 1] -eq '(') { $stack.Push([char]'('); $i++ }
            $i++; continue
        }
        if ($c -eq "'") { $k = $Text.IndexOf([char]"'", $i + 1); if ($k -lt 0) { return $Text.Length }; $i = $k + 1; continue }
        if ($c -eq '"') { $stack.Push([char]'"') }
        elseif ($c -eq '(') { $stack.Push([char]'(') }
        elseif ($c -eq ')') { [void]$stack.Pop(); if ($stack.Count -eq 0) { return $i } }
        $i++
    }
    return $Text.Length
}

# Bodies of $( .. ) (and bash backticks) in $Text, ended by Find-SubstEnd so `"$(dirname "$0")"` and
# `"$(cd "/x" && rm -rf "y")"` keep their inner quotes. A $( inside double quotes runs; inside bash
# single quotes it does not. -NoQuotes: heredoc bodies, where quote characters are literal text.
function Get-Substitutions {
    param([string]$Text, [switch]$PowerShell, [switch]$NoQuotes)
    $out = New-Object System.Collections.Generic.List[string]
    $esc = if ($PowerShell) { [char]'`' } else { [char]'\' }
    $sq = $false; $dq = $false; $i = 0
    while ($i -lt $Text.Length) {
        $c = $Text[$i]
        if ($sq) { if ($c -eq "'") { $sq = $false }; $i++; continue }
        if ($c -eq $esc) { $i += 2; continue }
        if (-not $NoQuotes) {
            if ($c -eq '"') { $dq = -not $dq; $i++; continue }
            if ($c -eq "'" -and -not $dq) { $sq = $true; $i++; continue }
        }
        if ($c -eq '$' -and $i + 1 -lt $Text.Length -and $Text[$i + 1] -eq '(') {
            $j = Find-SubstEnd -Text $Text -At ($i + 2) -Esc $esc
            $out.Add($Text.Substring($i + 2, $j - $i - 2))
            $i = $j + 1; continue
        }
        if (-not $PowerShell -and $c -eq '`') {
            $k = $Text.IndexOf([char]'`', $i + 1); if ($k -lt 0) { $k = $Text.Length }
            $out.Add($Text.Substring($i + 1, $k - $i - 1)); $i = $k + 1; continue
        }
        $i++
    }
    return ,$out.ToArray()
}

# Does a psql argument carry SQL? -c / -f, also clustered (-tAc); a value-taking option ends the
# cluster (-hlocalhost is -h with value "localhost").
function Test-PsqlCommandFlag {
    param([string]$Token)
    if ($Token -match '^--(command|file)') { return $true }
    if ($Token -cnotmatch '^-[a-zA-Z]') { return $false }
    foreach ($ch in $Token.Substring(1).ToCharArray()) {
        if ('cf'.IndexOf($ch) -ge 0) { return $true }
        if ('dhpUvPoLTFR'.IndexOf($ch) -ge 0) { return $false }
    }
    return $false
}

# Index just past a quote / ${ opened in $Text and still open at its end, else -1. Only called for
# the word that reached end of input: the linear patterns let an unclosed construct swallow the rest
# of the line, so the caller re-segments the text after the opener — nothing hides behind it (a real
# unclosed quote makes the shell reject the line anyway; a nested-quote misparse stays harmless).
function Get-UnclosedAt {
    param([string]$Text, [switch]$PowerShell)
    $esc = if ($PowerShell) { [char]'`' } else { [char]'\' }
    $state = ''; $at = -1
    for ($i = 0; $i -lt $Text.Length; $i++) {
        $c = $Text[$i]
        if ($state -eq "'") { if ($c -eq "'") { $state = '' }; continue }
        if ($c -eq $esc) { $i++; continue }
        if ($state -eq '"') { if ($c -eq '"') { $state = '' }; continue }
        if ($state -eq '{') { if ($c -eq '}') { $state = '' }; continue }
        if ($c -eq "'" -or $c -eq '"') { $state = [string]$c; $at = $i + 1 }
        elseif ($c -eq '$' -and $i + 1 -lt $Text.Length -and $Text[$i + 1] -eq '{') { $state = '{'; $i++; $at = $i + 1 }
    }
    if ($state) { return $at } else { return -1 }
}

function Get-ShellSegments {
    # -Heredocs (optional hashtable): filled with segment index -> heredoc / here-string text fed to it.
    # -Fragment: text recovered from behind an unclosed quote — maybe prose, so a bare shell word in
    #   it is not taken as "a shell reading stdin" (real commands in it are still judged).
    param([string]$Command, [switch]$PowerShell, [int]$Depth = 0, [hashtable]$Heredocs,
          [switch]$KeepBrackets, [Diagnostics.Stopwatch]$Clock, [switch]$Fragment)
    if (-not $Clock) { $Clock = [Diagnostics.Stopwatch]::StartNew() }
    if ($Clock.ElapsedMilliseconds -gt 3000) { throw 'command segmenting exceeded its 3 s budget' }
    $segs = New-Object System.Collections.Generic.List[object]
    if (-not $Command) { return ,$segs.ToArray() }
    if ($Depth -gt 3) { $segs.Add([string[]]@('__nested_too_deep__')); return ,$segs.ToArray() }
    $br = if ($KeepBrackets) { '' } else { '(){}' }
    # unterminated quotes / ${ consume to the end once (linear time; the shell would reject them anyway)
    # Line continuation (bash `\`+LF, PS backtick+LF) is a word piece the unquote step deletes — done in
    # the tokenizer, not as a pre-pass, so a comment, an escaped `\\` or a heredoc body is not joined to
    # the next line (#3483 review round 3: `echo hi # note\`+LF+`rm -rf x` hid the rm).
    $s = $Command
    if ($PowerShell) {
        $dq = '"(?:`[\s\S]|""|[^"`])*"?'
        $plain = '`\r?\n|(?s:@''\r?\n.*?\r?\n''@|@"\r?\n.*?\r?\n"@)|[^\s''"`;|&<>' + $br + ']|`.'
        $sep = '&&|\|\||[;|&\n' + $br + ']'
        $unq = "'([^']*)'?|""((?:``[\s\S]|""""|[^""``])*)""?|``\r?\n|``(.)"
        $lcOnly = '^(`\r?\n)+$'
        $hd = ''
    } else {
        $dq = '"(?:\\[\s\S]|[^"\\])*"?'
        $plain = '\\\r?\n|[^\s''"`;|&<>\\' + $br + ']|\\.'
        $sep = '&&|\|\||[;|&\n`' + $br + ']'
        $unq = "'([^']*)'?|""((?:\\[\s\S]|[^""\\])*)""?|\\\r?\n|\\(.)"
        $lcOnly = '^(\\\r?\n)+$'
        # heredoc marker: the whole delimiter word incl. quoted / escaped parts; bash strips the quotes and
        # backslashes (<<E"O"F and <<A\ B end at EOF / "A B")
        $hd = '(?<hd><<-?[ \t]*(?<hw>(?:\\.|''[^''\n]*''|"[^"\n]*"|[^\s;&|<>()''"\\])+))|'
    }
    $piece = '''[^'']*''?|' + $dq + '|\$\{[^}]*\}?|' + $plain
    $rx = [regex]::new($hd + '(?<c>(?<=^|[\s;|&(){}])#[^\n]*)|(?<r>\d*(?:>>|>&|<&|>\||<<<|<<-?|<>|[<>])(?:\d+|-)?)|(?<sep>' + $sep + ')|(?<w>(?:' + $piece + ')+)', [Text.RegularExpressions.RegexOptions]::None, [TimeSpan]::FromSeconds(1))   # a timeout throws -> the gate asks
    $order = New-Object System.Collections.Generic.List[object]   # own segments @{id; tok} and recursed string[] in order
    $feeds = @{}                                                    # own segment id -> stdin text (heredoc / here-string)
    $cur = New-Object System.Collections.Generic.List[string]; $curId = 0
    $pending = New-Object System.Collections.Generic.List[object]
    $skipNext = $false; $hereStr = $false; $pos = 0; $n = 0
    while ($true) {
        if ((++$n % 200) -eq 0 -and $Clock.ElapsedMilliseconds -gt 3000) { throw 'command segmenting exceeded its 3 s budget' }
        $m = $rx.Match($s, $pos)
        if ($m.Success) {
            $pos = $m.Index + $m.Length
            if ($m.Groups['hd'].Success) {
                $hw = $m.Groups['hw'].Value
                $pending.Add(@{ word = ($hw -replace '\\(.)', '$1' -replace '[''"]', ''); quoted = $hw -match '[''"\\]'; owner = $curId }); continue
            }
            if ($m.Groups['c'].Success) { continue }
            if ($m.Groups['r'].Success) { $hereStr = $m.Value -match '<<<'; $skipNext = $m.Value -notmatch '&(\d+|-)$'; continue }
            if ($m.Groups['w'].Success) {
                $raw = $m.Value
                if ($pos -ge $s.Length -and $raw -match '[''"]|\$\{') {
                    # PS here-strings are one literal; an apostrophe inside one is not a quote
                    $scan = if ($PowerShell) { [regex]::Replace($raw, '(?s)@''\r?\n.*?\r?\n''@|@"\r?\n.*?\r?\n"@', "''") } else { $raw }
                    $at = Get-UnclosedAt -Text $scan -PowerShell:$PowerShell
                    if ($at -ge 0 -and $at -lt $scan.Length) {
                        foreach ($x in (Get-ShellSegments -Command $scan.Substring($at) -PowerShell:$PowerShell -Depth ($Depth + 1) -KeepBrackets:$KeepBrackets -Clock $Clock -Fragment)) { $order.Add($x) }
                    }
                }
                $text = if ($raw -match '[''"`\\]') { [regex]::Replace($raw, $unq, { param($x) $x.Groups[1].Value + $x.Groups[2].Value + $x.Groups[3].Value }) } else { $raw }
                if ($raw -match $lcOnly) { continue }   # a bare continuation between words is whitespace
                if ($hereStr) { $feeds[$curId] = "$($feeds[$curId])`n$text"; $hereStr = $false; $skipNext = $false; continue }
                if ($skipNext) { $skipNext = $false; continue }
                if ($raw.Contains('$(') -or (-not $PowerShell -and $raw.Contains('`'))) {
                    foreach ($sub in (Get-Substitutions -Text $raw -PowerShell:$PowerShell)) {
                        foreach ($x in (Get-ShellSegments -Command $sub -PowerShell:$PowerShell -Depth ($Depth + 1) -KeepBrackets:$KeepBrackets -Clock $Clock -Fragment:$Fragment)) { $order.Add($x) }
                    }
                }
                $cur.Add($text)
                continue
            }
        }
        # separator or end of input: close the segment
        $skipNext = $false; $hereStr = $false
        if ($cur.Count -gt 0) {
            $order.Add(@{ id = $curId; tok = $cur.ToArray() })
            $cur = New-Object System.Collections.Generic.List[string]; $curId++
        }
        if (-not $m.Success) { break }
        # first newline after heredoc marker(s): attach each body to its segment, then skip it
        if ($m.Value -eq "`n" -and $pending.Count) {
            foreach ($p in $pending) {
                $t = ([regex]('(?m)^[ \t]*' + [regex]::Escape($p.word) + '[ \t]*\r?$')).Match($s, $pos)
                if (-not $t.Success) { continue }   # no terminator line: not a heredoc — keep tokenizing
                $body = $s.Substring($pos, $t.Index - $pos)
                $pos = $t.Index + $t.Length
                $feeds[$p.owner] = "$($feeds[$p.owner])`n$body"
                if (-not $p.quoted) {
                    foreach ($sub in (Get-Substitutions -Text $body -NoQuotes)) { foreach ($x in (Get-ShellSegments -Command $sub -Depth ($Depth + 1) -KeepBrackets:$KeepBrackets -Clock $Clock -Fragment:$Fragment)) { $order.Add($x) } }
                }
            }
            $pending.Clear()
        }
    }
    $shells = @('bash', 'sh', 'zsh', 'dash', 'ksh', 'pwsh', 'powershell')
    foreach ($item in $order) {
        if ($item -isnot [hashtable]) { $segs.Add($item); continue }
        $tok = $item.tok; $feed = $feeds[$item.id]
        if ($null -ne $Heredocs -and $feed) { $Heredocs[$segs.Count] = $feed }
        $segs.Add($tok)
        $h = Get-SegmentHead -Tokens $tok
        $headLeaf = if ($h -lt $tok.Count) { Get-CommandLeaf $tok[$h] } else { '' }
        # a shell after a container/remote runner (`docker exec -i db sh <<EOF`, `... | docker exec -i x sh`)
        # reads its stdin just like a head shell does
        $runner = $headLeaf -in @('docker', 'docker-compose', 'podman', 'kubectl', 'ssh', 'wsl')
        for ($j = $h; $j -lt $tok.Count; $j++) {
            if (-not ($tok[$j].IndexOf('sh', [StringComparison]::OrdinalIgnoreCase) -ge 0)) { continue }   # every shell name contains 'sh'
            $leaf = Get-CommandLeaf $tok[$j]
            if ($shells -notcontains $leaf) { continue }
            $arg = Get-ShellScriptArg -Tokens $tok -At $j
            $sub = $null; $subPs = $leaf -in @('pwsh', 'powershell')
            if ($arg.kind -eq 'script') { $sub = $arg.text; $subPs = $arg.ps }
            elseif ($arg.kind -eq 'opaque') { $segs.Add([string[]]@('__opaque_script__')) }
            elseif ($arg.kind -eq 'stdin' -and ($j -eq $h -or $runner)) {
                if ($feed) { $sub = $feed } elseif (-not $Fragment) { $segs.Add([string[]]@('__shell_stdin__')) }
            }
            if ($sub) { foreach ($x in (Get-ShellSegments -Command $sub -PowerShell:$subPs -Depth ($Depth + 1) -KeepBrackets:$KeepBrackets -Clock $Clock -Fragment:$Fragment)) { $segs.Add($x) } }
        }
        # eval / Invoke-Expression run their arguments as a command line
        $rest = @(if ($h + 1 -lt $tok.Count) { $tok[($h + 1)..($tok.Count - 1)] })
        $evalText = $null; $evalPs = $PowerShell
        if ($headLeaf -eq 'eval') { $evalText = $rest -join ' '; $evalPs = $false }
        elseif ($headLeaf -in @('iex', 'invoke-expression')) { $evalText = @($rest | Where-Object { $_ -notmatch '^(?i)-Command$' }) -join ' '; $evalPs = $true }
        if ($evalText) { foreach ($x in (Get-ShellSegments -Command $evalText -PowerShell:$evalPs -Depth ($Depth + 1) -KeepBrackets:$KeepBrackets -Clock $Clock -Fragment:$Fragment)) { $segs.Add($x) } }
        # the command a container runs (`docker compose exec -T db rm -rf /var/lib/...`) is judged as its own segment
        if ($headLeaf -in @('docker', 'docker-compose')) {
            $inner = Get-ContainerCommand -Tokens $tok
            if ($inner.Count) { $segs.Add([string[]]$inner) }
        }
    }
    return ,$segs.ToArray()
}

# The command after the container / service / image of a docker [compose] exec|run segment, or @().
function Get-ContainerCommand {
    param([string[]]$Tokens)
    $v = Get-DockerVerb -Tokens $Tokens
    if (-not $v) { return @() }
    $sub = $v.sub; $r = $v.rest
    if ($sub -eq 'container' -and $r.Count) { $sub = $r[0].ToLowerInvariant(); $r = @($r | Select-Object -Skip 1) }
    if ($sub -notin @('exec', 'run')) { return @() }
    $valueFlags = @('-e', '--env', '-u', '--user', '-w', '--workdir', '--env-file', '--index', '-v', '--volume', '--name',
                    '--entrypoint', '-p', '--publish', '-l', '--label', '--network', '--net', '--mount', '--platform',
                    '-m', '--memory', '--cpus', '-h', '--hostname', '--add-host', '--device', '--restart', '--log-driver')
    for ($k = 0; $k -lt $r.Count; $k++) {
        if ($r[$k] -like '-*') { if ($valueFlags -contains $r[$k]) { $k++ }; continue }
        return @(if ($k + 1 -lt $r.Count) { $r[($k + 1)..($r.Count - 1)] })
    }
    return @()
}

# Returns @{ compose; sub; rest } for a docker / docker-compose segment (global flags
# like -p/-f/--project-directory/--profile/-l skipped), or $null for any other command.
function Get-DockerVerb {
    param([string[]]$Tokens)
    $h = Get-SegmentHead -Tokens $Tokens
    if ($h -ge $Tokens.Count) { return $null }
    $leaf = Get-CommandLeaf $Tokens[$h]
    if ($leaf -notin @('docker', 'docker-compose')) { return $null }
    $compose = $leaf -eq 'docker-compose'
    $valueFlags = @('-p', '--project-name', '-f', '--file', '--project-directory', '--profile', '--env-file',
                    '--context', '-c', '-H', '--host', '-l', '--log-level', '--config', '--ansi', '--parallel', '--progress')
    $i = $h + 1
    while ($i -lt $Tokens.Count) {
        $t = $Tokens[$i]
        if ($t -like '-*') { if ($valueFlags -contains $t) { $i += 2 } else { $i++ }; continue }
        if (-not $compose -and $t -eq 'compose') { $compose = $true; $i++; continue }
        break
    }
    if ($i -ge $Tokens.Count) { return $null }
    $rest = @(if ($i + 1 -lt $Tokens.Count) { $Tokens[($i + 1)..($Tokens.Count - 1)] })
    return @{ compose = $compose; sub = $Tokens[$i].ToLowerInvariant(); rest = $rest }
}

# Any token a long option or its GNU-style abbreviation (`--har` = --hard, `--rec` = --recursive)?
function Test-LongOpt {
    param([object[]]$Tokens, [string[]]$Full)
    foreach ($t in $Tokens) {
        if ($t -notlike '--?*') { continue }
        $name = ($t -split '=')[0]
        if ($name.Length -lt 4) { continue }
        foreach ($f in $Full) { if ($f.StartsWith($name, [StringComparison]::Ordinal)) { return $true } }
    }
    return $false
}

# Option-C destructive class (#3486; operator chose C 2026-10-08). Returns
# @{ rule; segment } for the first hit, else $null. The gate turns a hit into ASK.
function Get-DestructiveHit {
    param([string]$Command, [switch]$PowerShell, [object[]]$Segments)
    if ($null -eq $Segments) { $Segments = Get-ShellSegments -Command $Command -PowerShell:$PowerShell }
    # a path is throwaway when one of its components is a throwaway dir (whole component, not a substring)
    $safePath = '(?i)(^|[\\/])(_scratch[\w.-]*|\.next[\w.-]*|node_modules|tmp|temp|scratchpad|\$TEMP|\$TMPDIR|\$env:TEMP|\$env:TMP)([\\/]|$)|\.tsbuildinfo$'
    $synthetic = @{ '__shell_stdin__' = 'shell reading commands from stdin / a pipe'; '__nested_too_deep__' = 'shell nesting deeper than 3 levels'
                    '__opaque_script__' = 'undecodable -EncodedCommand' }
    # NAME=value / $name = value set earlier in this command, so `S=<scratchpad>; rm -rf $S/x` resolves.
    # Bash names are case-sensitive; any other kind of (re)assignment forgets the name.
    $vars = if ($PowerShell) { @{} } else { New-Object 'System.Collections.Generic.Dictionary[string,string]' }
    foreach ($seg in $Segments) {
        $h = Get-SegmentHead -Tokens $seg
        $psVar = if ($seg.Count -ge 2 -and $seg[0] -match '^\$(\w+)$') { $Matches[1] } else { $null }
        if ($psVar -and $seg.Count -eq 3 -and $seg[1] -eq '=') { $vars[$psVar] = $seg[2] }
        elseif ($psVar -and $seg[1] -in @('+=', '-=', '*=', '/=')) { [void]$vars.Remove($psVar) }
        if ($h -ge $seg.Count) {   # assignment-only segment (a `S=x cmd $S` prefix does not change what $S expands to)
            foreach ($t in $seg) {
                if ($t -match '^([A-Za-z_]\w*)=(.*)$') { $vars[$Matches[1]] = $Matches[2] }
                elseif ($t -match '^([A-Za-z_]\w*)\+=') { [void]$vars.Remove($Matches[1]) }
            }
            continue
        }
        $leaf = Get-CommandLeaf $seg[$h]
        $rest = @(if ($h + 1 -lt $seg.Count) { $seg[($h + 1)..($seg.Count - 1)] })
        if ($leaf -in @('export', 'declare', 'local', 'readonly', 'typeset', 'for', 'read')) {
            foreach ($t in $rest) { if ($t -match '^([A-Za-z_]\w*)') { [void]$vars.Remove($Matches[1]) } }
            continue
        }
        $rule = $null
        if ($synthetic.ContainsKey($seg[$h])) { $rule = $synthetic[$seg[$h]] }
        elseif ($leaf -in @('rm', 'remove-item', 'ri', 'del', 'erase') -or ($PowerShell -and $leaf -in @('rd', 'rmdir'))) {
            $psStyle = ($PowerShell -and $seg[$h] -notmatch '(?i)\.exe$') -or $leaf -ne 'rm'   # rm.exe takes bash-style flags
            $rec = if ($psStyle) { @($rest | Where-Object { $_ -match '^-r(e(c(u(r(s(e)?)?)?)?)?)?(:\$true)?$' }) }
                   else { @($rest | Where-Object { $_ -cmatch '^-[a-zA-Z]*[rR]' }) + @(if (Test-LongOpt $rest '--recursive') { '--recursive' }) }
            # PS parameters whose value is not a path (-ErrorAction SilentlyContinue, -Include *.log, ...)
            $valueFlag = '^(?i)-(ea|erroraction|ev|errorvariable|wa|warningaction|include|exclude|filter|credential|stream|ov|outvariable)$'
            $paths = New-Object System.Collections.Generic.List[string]
            for ($k = 0; $k -lt $rest.Count; $k++) {
                if ($rest[$k] -like '-*') { if ($psStyle -and $rest[$k] -match $valueFlag) { $k++ }; continue }
                $paths.Add([regex]::Replace($rest[$k], '\$\{?(\w+)\}?', { param($m) if ($vars.ContainsKey($m.Groups[1].Value)) { $vars[$m.Groups[1].Value] } else { $m.Value } }))
            }
            $unsafe = @($paths | Where-Object { $_ -notmatch $safePath -or $_ -match '\.\.' })
            if ($rec.Count -and ($paths.Count -eq 0 -or $unsafe.Count)) { $rule = 'rm -r outside throwaway paths' }
        } elseif ($leaf -eq 'git') {
            $i = 0
            while ($i -lt $rest.Count -and $rest[$i] -like '-*') {
                if ($rest[$i] -in @('-C', '-c', '--git-dir', '--work-tree', '--namespace')) { $i += 2 } else { $i++ }
            }
            $sub = if ($i -lt $rest.Count) { $rest[$i] } else { '' }
            $r = @(if ($i + 1 -lt $rest.Count) { $rest[($i + 1)..($rest.Count - 1)] })
            switch ($sub) {
                'checkout'   {   # paths without `--` (#3500): 2+ positionals once -b/-B/--orphan values are skipped,
                                 # one that looks like a file (x.py), or --ours/--theirs
                                 $pos = 0; $file = $false
                                 for ($k = 0; $k -lt $r.Count; $k++) { if ($r[$k] -cin @('-b', '-B', '--orphan')) { $k++ } elseif ($r[$k] -notlike '-*') { $pos++; if ($r[$k] -match '\.[A-Za-z]\w*$') { $file = $true } } }
                                 if ($r -contains '--' -or $r -contains '.' -or $pos -ge 2 -or $file -or $r -ccontains '-f' -or (Test-LongOpt $r @('--force', '--ours', '--theirs'))) { $rule = 'git checkout <path>/-f (discards edits)' } }
                'switch'     { if ($r -ccontains '-f' -or (Test-LongOpt $r @('--force', '--discard-changes'))) { $rule = 'git switch -f (discards edits)' } }
                'restore'    { if (-not ($r -ccontains '-S' -or (Test-LongOpt $r '--staged')) -or $r -ccontains '-W' -or (Test-LongOpt $r '--worktree')) { $rule = 'git restore (discards edits)' } }
                'reset'      { if (Test-LongOpt $r '--hard') { $rule = 'git reset --hard' } }
                'clean'      { if ((@($r | Where-Object { $_ -cmatch '^-[a-zA-Z]*f' }).Count -or (Test-LongOpt $r '--force')) -and
                                   -not (@($r | Where-Object { $_ -cmatch '^-[a-zA-Z]*n' }).Count -or (Test-LongOpt $r '--dry-run'))) { $rule = 'git clean -f' } }
                'push'       { if (@($r | Where-Object { $_ -cmatch '^-[a-zA-Z]*[fd]' -or $_ -match '^[+:]' }).Count -or (Test-LongOpt $r @('--force', '--force-with-lease', '--delete', '--mirror'))) { $rule = 'git push --force/--delete/--mirror' } }
                'branch'     { if (@($r | Where-Object { $_ -cmatch '^-[a-zA-Z]*D' }).Count -or ((Test-LongOpt $r '--delete') -and ($r -ccontains '-f' -or (Test-LongOpt $r '--force')))) { $rule = 'git branch -D' } }
                'stash'      { if ($r.Count -and $r[0] -in @('drop', 'clear')) { $rule = 'git stash drop/clear' } }
                'rm'         { if (@($r | Where-Object { $_ -cmatch '^-[a-zA-Z]*r' }).Count -and $r -notcontains '--cached') { $rule = 'git rm -r' } }
                'update-ref' { if ($r -ccontains '-d' -or $r -contains '--delete') { $rule = 'git update-ref -d' } }
            }
        } elseif ($leaf -eq 'find') {
            # find -delete / -exec rm (#3500): a tree walk, so judged by the start paths like rm -r
            $roots = New-Object System.Collections.Generic.List[string]
            foreach ($t in $rest) { if ($t -cin @('-H', '-L', '-P')) { continue }; if ($t -match '^[-(!]') { break }; $roots.Add($t) }
            $del = $rest -ccontains '-delete'
            for ($k = 0; -not $del -and $k -lt $rest.Count - 1; $k++) {
                if ($rest[$k] -cin @('-exec', '-execdir', '-ok', '-okdir') -and (Get-CommandLeaf $rest[$k + 1]) -eq 'rm') { $del = $true }
            }
            if ($del -and ($roots.Count -eq 0 -or @($roots | Where-Object { $_ -notmatch $safePath -or $_ -match '\.\.' }).Count)) { $rule = 'find -delete / -exec rm outside throwaway paths' }
        } elseif ($leaf -in @('curl', 'wget', 'invoke-restmethod', 'invoke-webrequest', 'irm', 'iwr')) {
            for ($j = 0; $j -lt $rest.Count; $j++) {
                $a = $rest[$j]; $next = if ($j + 1 -lt $rest.Count) { $rest[$j + 1] } else { '' }
                $verb = $null   # curl -X / -sX / -XDELETE, --request[=], wget --method[=], PS -Method[:]
                if ($leaf -eq 'curl' -and $a -cmatch '^-[a-zA-Z]*X(.*)$') { $verb = if ($Matches[1]) { $Matches[1] } else { $next } }
                elseif ($a -match '^(?i)--(request|method)(=(.*))?$') { $verb = if ($Matches[2]) { $Matches[3] } else { $next } }
                elseif ($a -match '^(?i)-Me(t(h(o(d)?)?)?)?(:(.*))?$') { $verb = if ($Matches[5]) { $Matches[6] } else { $next } }
                if ($verb -match '^(?i)\s*DELETE$') { $rule = 'HTTP DELETE'; break }
            }
        } elseif ($leaf -in @('docker', 'docker-compose')) {
            $v = Get-DockerVerb -Tokens $seg
            if ($v) {
                $sub = $v.sub; $r = $v.rest
                if ($sub -eq 'container' -and $r.Count) { $sub = $r[0].ToLowerInvariant(); $r = @($r | Select-Object -Skip 1) }
                $r0 = if ($r.Count) { $r[0] } else { '' }
                if (($sub -in @('volume', 'image') -and $r0 -in @('rm', 'remove', 'prune')) -or
                    ($sub -eq 'system' -and $r0 -eq 'prune') -or $sub -eq 'rmi' -or
                    ($sub -in @('down', 'rm') -and (@($r | Where-Object { $_ -cmatch '^-[a-zA-Z]*v' }).Count -or (Test-LongOpt $r '--volumes')))) {
                    $rule = if ($sub -in @('volume', 'image', 'system')) { "docker $sub $r0" } elseif ($sub -eq 'rmi') { 'docker rmi' } else { "docker $sub -v" }
                }
            }
        }
        if ($rule) { return @{ rule = $rule; segment = ($seg -join ' ') } }
    }
    return $null
}
