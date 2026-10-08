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
# Shell segmenting (#3486) — rules match a SEGMENT's command word, never raw text,
# so a commit message, JSON payload, heredoc body or `node -e "..."` string that
# merely mentions `rm -rf` / `pytest` / `DELETE FROM` cannot trip (or dodge) a guard.
# Segments split on unquoted ; && || | & newline ( ) { } (+ backtick for Bash).
# Heredoc bodies, PS here-strings, comments and redirect targets are dropped;
# $( ) / backtick bodies inside double quotes and the script after
# bash|sh|pwsh|powershell -c/-Command are segmented recursively.
# shortcut: regex tokenizer, not a shell parser — $VAR paths, eval and aliases stay
# opaque; upgrade: a real parser if the decision log (#3487) shows misses.
# ---------------------------------------------------------------------------
function Get-SegmentHead {
    param([string[]]$Tokens)
    $prefix = @('time', 'exec', 'nohup', 'command', 'builtin', 'do', 'then', 'else', 'elif', '!', '.')
    # wrappers that run the next word as the command; listed flags take a value
    $wrappers = @{ sudo = @('-u', '-g', '-U', '-C', '-h', '-p', '-D'); env = @('-u', '-C', '-S'); nice = @('-n')
                   xargs = @('-n', '-I', '-L', '-P', '-d', '-s', '-E', '-a'); timeout = @('-s', '-k'); stdbuf = @(); ionice = @('-c', '-n') }
    $i = 0
    while ($i -lt $Tokens.Count) {
        $t = $Tokens[$i]
        if ($t -match '^[A-Za-z_][A-Za-z0-9_]*=' -or $prefix -contains $t) { $i++; continue }
        if ($t -match '^\$[\w:]+$' -and ($i + 1) -lt $Tokens.Count -and $Tokens[$i + 1] -eq '=') { $i += 2; continue }
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

function Get-ShellSegments {
    # -Heredocs (optional hashtable): filled with segment index -> heredoc body fed to that segment.
    param([string]$Command, [switch]$PowerShell, [int]$Depth = 0, [hashtable]$Heredocs)
    $segs = New-Object System.Collections.Generic.List[object]
    if (-not $Command -or $Depth -gt 3) { return ,$segs.ToArray() }
    if ($PowerShell) {
        $s = [regex]::Replace($Command, '`\r?\n', ' ')
        # a here-string is one quoted word, so a command written inside it stays text
        $dq = '"(?:`.|""|[^"`])*"'; $plain = '(?s:@''\r?\n.*?\r?\n''@|@"\r?\n.*?\r?\n"@)|[^\s''"`;|&(){}<>]|`.'; $sep = '&&|\|\||[;|&\n(){}]'
        $unq = "'([^']*)'|""((?:``.|""""|[^""``])*)""|``(.)"
        $hd = ''
    } else {
        $s = [regex]::Replace($Command, '\\\r?\n', ' ')
        $dq = '"(?:\\.|[^"\\])*"'; $plain = '[^\s''"`;|&(){}<>\\]|\\.'; $sep = '&&|\|\||[;|&\n(){}`]'
        $unq = "'([^']*)'|""((?:\\.|[^""\\])*)""|\\(.)"
        # heredoc marker, matched by the tokenizer so a quoted "<<X" stays text
        $hd = "(?<hd><<-?[ \t]*(?<hq>['""]?)(?<hw>\w+)\k<hq>)|"
    }
    $piece = "'[^']*'|$dq|\$\{[^}]*\}|$plain"
    $rx = [regex]"$hd(?<c>(?<=^|[\s;|&(){}])#[^\n]*)|(?<r>\d*(?:>>|>&|<&|>\||<<<|<<-?|<>|[<>])(?:\d+|-)?)|(?<sep>$sep)|(?<w>(?:$piece)+)"
    $cur = New-Object System.Collections.Generic.List[string]
    $skipNext = $false
    $pendingHd = New-Object System.Collections.Generic.List[string]
    $hdOwner = $null; $hdOwnerIndex = -1
    $pos = 0
    while ($true) {
        $m = $rx.Match($s, $pos)
        if ($m.Success) {
            $pos = $m.Index + $m.Length
            if ($m.Groups['hd'].Success) { $pendingHd.Add($m.Groups['hw'].Value); $hdOwner = $cur; continue }
            if ($m.Groups['c'].Success) { continue }
            if ($m.Groups['r'].Success) { $skipNext = $m.Value -notmatch '&(\d+|-)$'; continue }
            if ($m.Groups['w'].Success) {
                if ($skipNext) { $skipNext = $false; continue }
                $raw = $m.Value
                foreach ($q in [regex]::Matches($raw, $dq)) {
                    $subs = @([regex]::Matches($q.Value, '\$\(((?:[^()]|\([^()]*\))*)\)') | ForEach-Object { $_.Groups[1].Value })
                    if (-not $PowerShell) { $subs += @([regex]::Matches($q.Value, '`([^`]*)`') | ForEach-Object { $_.Groups[1].Value }) }
                    foreach ($sub in $subs) { foreach ($x in (Get-ShellSegments -Command $sub -PowerShell:$PowerShell -Depth ($Depth + 1))) { $segs.Add($x) } }
                }
                $cur.Add([regex]::Replace($raw, $unq, { param($x) $x.Groups[1].Value + $x.Groups[2].Value + $x.Groups[3].Value }))
                continue
            }
        }
        # separator or end of input: close the segment
        $skipNext = $false
        if ($cur.Count -gt 0) {
            $tok = $cur.ToArray()
            if ([object]::ReferenceEquals($cur, $hdOwner)) { $hdOwnerIndex = $segs.Count }
            $segs.Add($tok)
            $h = Get-SegmentHead -Tokens $tok
            if ($h -lt $tok.Count -and (Get-CommandLeaf $tok[$h]) -in @('bash', 'sh', 'zsh', 'dash', 'pwsh', 'powershell')) {
                $isPs = (Get-CommandLeaf $tok[$h]) -in @('pwsh', 'powershell')
                for ($j = $h + 1; $j -lt $tok.Count - 1; $j++) {
                    if ($tok[$j] -match '^-(c|command)$') {
                        foreach ($x in (Get-ShellSegments -Command $tok[$j + 1] -PowerShell:$isPs -Depth ($Depth + 1))) { $segs.Add($x) }
                        break
                    }
                }
            }
            $cur = New-Object System.Collections.Generic.List[string]
        }
        if (-not $m.Success) { break }
        # first newline after heredoc marker(s): skip each body through its terminator line
        if ($m.Value -eq "`n" -and $pendingHd.Count) {
            $bodyStart = $pos
            foreach ($word in $pendingHd) {
                $t = ([regex]"(?m)^[ \t]*$([regex]::Escape($word))[ \t]*\r?$").Match($s, $pos)
                $pos = if ($t.Success) { $t.Index + $t.Length } else { $s.Length }
            }
            if ($null -ne $Heredocs -and $hdOwnerIndex -ge 0) { $Heredocs[$hdOwnerIndex] = $s.Substring($bodyStart, $pos - $bodyStart) }
            $pendingHd.Clear(); $hdOwner = $null; $hdOwnerIndex = -1
        }
    }
    return ,$segs.ToArray()
}

# Returns @{ compose; sub; rest } for a docker / docker-compose segment (global flags
# like -p/-f/--project-directory/--profile skipped), or $null for any other command.
function Get-DockerVerb {
    param([string[]]$Tokens)
    $h = Get-SegmentHead -Tokens $Tokens
    if ($h -ge $Tokens.Count) { return $null }
    $leaf = Get-CommandLeaf $Tokens[$h]
    if ($leaf -notin @('docker', 'docker-compose')) { return $null }
    $compose = $leaf -eq 'docker-compose'
    $valueFlags = @('-p', '--project-name', '-f', '--file', '--project-directory', '--profile', '--env-file',
                    '--context', '-c', '-H', '--host', '--log-level', '--config', '--ansi', '--parallel', '--progress')
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

# Option-C destructive class (#3486; operator chose C 2026-10-08). Returns
# @{ rule; segment } for the first hit, else $null. The gate turns a hit into ASK.
function Get-DestructiveHit {
    param([string]$Command, [switch]$PowerShell, [object[]]$Segments)
    if ($null -eq $Segments) { $Segments = Get-ShellSegments -Command $Command -PowerShell:$PowerShell }
    # a path is throwaway when one of its components is a throwaway dir (whole component, not a substring)
    $safePath = '(?i)(^|[\\/])(_scratch[\w.-]*|\.next[\w.-]*|node_modules|tmp|temp|scratchpad|\$TEMP|\$TMPDIR|\$env:TEMP|\$env:TMP)([\\/]|$)|\.tsbuildinfo$'
    $vars = @{}   # NAME=value / $name = value set earlier in this command, so `S=<scratchpad>; rm -rf $S/x` resolves
    foreach ($seg in $Segments) {
        $h = Get-SegmentHead -Tokens $seg
        if ($seg.Count -eq 3 -and $seg[0] -match '^\$(\w+)$' -and $seg[1] -eq '=') { $vars[$Matches[1]] = $seg[2] }
        if ($h -ge $seg.Count) {   # assignment-only segment (a `S=x cmd $S` prefix does not change what $S expands to)
            foreach ($t in $seg) { if ($t -match '^([A-Za-z_]\w*)=(.*)$') { $vars[$Matches[1]] = $Matches[2] } }
            continue
        }
        $leaf = Get-CommandLeaf $seg[$h]
        $rest = @(if ($h + 1 -lt $seg.Count) { $seg[($h + 1)..($seg.Count - 1)] })
        $rule = $null
        if ($leaf -in @('rm', 'remove-item', 'ri', 'del', 'erase') -or ($PowerShell -and $leaf -in @('rd', 'rmdir'))) {
            $rec = if ($PowerShell -or $leaf -ne 'rm') { @($rest | Where-Object { $_ -match '^-r(e(c(u(r(s(e)?)?)?)?)?)?(:\$true)?$' }) }
                   else { @($rest | Where-Object { $_ -cmatch '^-[a-zA-Z]*[rR]' -or $_ -eq '--recursive' }) }
            $paths  = @($rest | Where-Object { $_ -notlike '-*' } | ForEach-Object {
                [regex]::Replace($_, '\$\{?(\w+)\}?', { param($m) if ($vars.ContainsKey($m.Groups[1].Value)) { $vars[$m.Groups[1].Value] } else { $m.Value } }) })
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
                'checkout' { if ($r -contains '--' -or $r -contains '.') { $rule = 'git checkout -- (discards edits)' } }
                'restore'  { if (-not ($r -contains '--staged' -or $r -ccontains '-S') -or $r -contains '--worktree' -or $r -ccontains '-W') { $rule = 'git restore (discards edits)' } }
                'reset'    { if ($r -contains '--hard') { $rule = 'git reset --hard' } }
                'clean'    { if (@($r | Where-Object { $_ -cmatch '^-[a-zA-Z]*f' -or $_ -eq '--force' }).Count) { $rule = 'git clean -f' } }
                'push'     { if (@($r | Where-Object { $_ -in @('--force', '--delete') -or $_ -like '--force-with-lease*' -or $_ -cmatch '^-[a-zA-Z]*[fd]' -or $_ -match '^[+:]' }).Count) { $rule = 'git push --force/--delete' } }
                'branch'   { if (@($r | Where-Object { $_ -cmatch '^-[a-zA-Z]*D' }).Count -or ($r -contains '--delete' -and ($r -contains '--force' -or $r -contains '-f'))) { $rule = 'git branch -D' } }
                'stash'    { if ($r.Count -and $r[0] -in @('drop', 'clear')) { $rule = 'git stash drop/clear' } }
                'rm'       { if (@($r | Where-Object { $_ -cmatch '^-[a-zA-Z]*r' }).Count -and $r -notcontains '--cached') { $rule = 'git rm -r' } }
            }
        } elseif ($leaf -in @('curl', 'invoke-restmethod', 'invoke-webrequest', 'irm', 'iwr')) {
            for ($j = 0; $j -lt $rest.Count; $j++) {
                $a = $rest[$j]; $next = if ($j + 1 -lt $rest.Count) { $rest[$j + 1] } else { '' }
                $verb = $null   # curl -X / -sX / -XDELETE, --request[=], PS -Method[:]
                if ($a -cmatch '^-[a-zA-Z]*X(.*)$') { $verb = if ($Matches[1]) { $Matches[1] } else { $next } }
                elseif ($a -match '^(?i)--request(=(.*))?$') { $verb = if ($Matches[1]) { $Matches[2] } else { $next } }
                elseif ($a -match '^(?i)-Me(t(h(o(d)?)?)?)?(:(.*))?$') { $verb = if ($Matches[5]) { $Matches[6] } else { $next } }
                if ($verb -match '^(?i)\s*DELETE$') { $rule = 'HTTP DELETE'; break }
            }
        } elseif ($leaf -in @('docker', 'docker-compose')) {
            $v = Get-DockerVerb -Tokens $seg
            if ($v) {
                $r = $v.rest; $r0 = if ($r.Count) { $r[0] } else { '' }
                if (($v.sub -in @('volume', 'image') -and $r0 -in @('rm', 'remove', 'prune')) -or
                    ($v.sub -eq 'system' -and $r0 -eq 'prune') -or $v.sub -eq 'rmi' -or
                    ($v.sub -in @('down', 'rm') -and @($r | Where-Object { $_ -eq '--volumes' -or $_ -cmatch '^-[a-zA-Z]*v' }).Count)) {
                    $rule = if ($v.sub -in @('volume', 'image', 'system')) { "docker $($v.sub) $r0" } else { "docker $($v.sub) -v" }
                    if ($v.sub -eq 'rmi') { $rule = 'docker rmi' }
                }
            }
        }
        if ($rule) { return @{ rule = $rule; segment = ($seg -join ' ') } }
    }
    return $null
}
