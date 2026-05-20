# SEO fact-check gate (PreToolUse) — YMYL citation enforcement.
#
# Trigger: PreToolUse on Edit + Write tools when invoked by seo-strategist
# or content-seo-optimizer agents.
#
# Logic: scans the proposed file content for YMYL (Your-Money-Your-Life)
# keywords. If found, requires a citation marker in the same content blob.
# YMYL pages get extra scrutiny from Google's E-E-A-T rater guidelines and
# misinformation in these areas can cause real-world harm — hence the gate.
#
# Registration in .claude/settings.json (operator step — DO NOT auto-write):
#   "PreToolUse": [{"matcher": "Edit|Write",
#                   "hooks": [{"type":"command",
#                              "command":".claude/hooks/seo-factcheck-gate.ps1"}]}]
#
# Decision rules:
#   - YMYL keyword present + NO citation marker -> deny (with hint)
#   - YMYL keyword present + citation marker present -> allow
#   - No YMYL keyword -> allow
#   - Internal error -> allow + stderr WARN (fail-open)
#
# Kanban #1266 AC1 — operator cp's from _scratch/ to .claude/hooks/ per
# feedback_claude_dir_humans_only.md standing rule.

$ErrorActionPreference = 'Continue'

function Emit-Decision {
    param(
        [Parameter(Mandatory = $true)][ValidateSet('allow', 'deny')][string]$Decision,
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

function Fail-Open {
    param([string]$WarnMsg)
    [Console]::Error.WriteLine("WARN: seo-factcheck-gate: $WarnMsg ; failing open (allow)")
    Emit-Decision -Decision 'allow' -Reason "seo-factcheck-gate fail-open: $WarnMsg"
    exit 0
}

# Read stdin payload --------------------------------------------------------
try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) { Fail-Open -WarnMsg 'empty PreToolUse payload' }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    Fail-Open -WarnMsg "payload not valid JSON: $($_.Exception.Message)"
}

# Scope to invoking agent (only gate SEO-lane agents) -----------------------
# Claude Code passes the invoking agent name via tool_input.subagent_type for
# Agent calls; for direct edits by a subagent, the harness sets agent_name on
# the payload root. Check both — fail-open if neither present (operator may
# also wire this hook with a matcher that scopes by agent at the settings
# layer, in which case agent_name is irrelevant here).
$agentName = $null
if ($payload.PSObject.Properties.Name -contains 'agent_name') {
    $agentName = [string]$payload.agent_name
} elseif ($payload.tool_input -and $payload.tool_input.PSObject.Properties.Name -contains 'subagent_type') {
    $agentName = [string]$payload.tool_input.subagent_type
}
$seoAgents = @('seo-strategist', 'content-seo-optimizer')
if ($agentName -and ($seoAgents -notcontains $agentName)) {
    # Not an SEO-lane agent — allow without inspection.
    Emit-Decision -Decision 'allow' -Reason "seo-factcheck-gate: agent '$agentName' out of scope"
    exit 0
}

# Extract content to scan ---------------------------------------------------
$toolName  = $payload.tool_name
$toolInput = $payload.tool_input
if (-not $toolInput) { Fail-Open -WarnMsg 'tool_input missing from payload' }

$content = ''
switch ($toolName) {
    'Write' {
        if ($toolInput.PSObject.Properties.Name -contains 'content') {
            $content = [string]$toolInput.content
        }
    }
    'Edit' {
        # Edit replaces old_string with new_string — only new_string is the
        # content being introduced into the file. Scan that.
        if ($toolInput.PSObject.Properties.Name -contains 'new_string') {
            $content = [string]$toolInput.new_string
        }
    }
    default {
        # Hook fired on a tool we don't scan — allow.
        Emit-Decision -Decision 'allow' -Reason "seo-factcheck-gate: tool '$toolName' not in scope"
        exit 0
    }
}

if (-not $content) {
    Emit-Decision -Decision 'allow' -Reason 'seo-factcheck-gate: empty content'
    exit 0
}

# YMYL keyword list ---------------------------------------------------------
# Pulled from Google Search Quality Evaluator Guidelines (2022 rev) YMYL
# categories: Health/Safety, Finance, Legal, Civics. Anchored with
# word-boundaries to avoid e.g. "treatmental" matching "treatment".
$ymylKeywords = @(
    'medical', 'medication', 'dosage', 'treatment', 'diagnosis', 'prescription',
    'legal advice', 'lawyer', 'attorney',
    'financial advice', 'investment return', 'tax advice', 'insurance claim'
)

$matchedYmyl = $null
foreach ($kw in $ymylKeywords) {
    $pattern = '\b' + [regex]::Escape($kw) + '\b'
    if ([regex]::IsMatch($content, $pattern, 'IgnoreCase')) {
        $matchedYmyl = $kw
        break
    }
}

if (-not $matchedYmyl) {
    Emit-Decision -Decision 'allow' -Reason 'seo-factcheck-gate: no YMYL keyword present'
    exit 0
}

# Citation marker check -----------------------------------------------------
# Liberal pattern — accept any of: markdown link, bracketed source/citation,
# bare URL, numeric reference [1], or "Source:" heading. Operator-tuned: an
# AI-generated YMYL claim without ANY of these is the actual risk surface;
# false-positive cost (operator has to add a citation) is low.
$citationPatterns = @(
    '\[source:',
    '\[citation:',
    'https?://',
    '\[\d+\]',
    '(?i)^source:',
    '(?i)\bsources?:\s',
    '(?i)\bcitation:\s',
    '(?i)\breference:\s'
)

$hasCitation = $false
foreach ($pat in $citationPatterns) {
    if ([regex]::IsMatch($content, $pat)) {
        $hasCitation = $true
        break
    }
}

if ($hasCitation) {
    Emit-Decision -Decision 'allow' -Reason "seo-factcheck-gate: YMYL keyword '$matchedYmyl' present with citation marker"
    exit 0
}

# YMYL present, no citation -> deny.
$denyReason = @"
seo-factcheck-gate: YMYL keyword '$matchedYmyl' detected in content WITHOUT a
citation marker.

YMYL (Your-Money-Your-Life) content — medical, legal, financial, civic — is
held to E-E-A-T standards by Google rater guidelines and by operator policy
in this repo. Misinformation in these areas can cause real-world harm, so
the gate requires a verifiable source for every YMYL claim.

To unblock, add ANY of the following to the same content blob:
  - A bracketed citation: [source: <name>] or [citation: <doi/url>]
  - An inline URL: https://...
  - A numbered footnote: [1]
  - A "Source:" / "Reference:" / "Citation:" line

If the YMYL keyword is incidental (e.g. quoting another article's title that
mentions 'treatment'), restructure the line so the keyword appears inside
quoted/cited context — the citation marker pattern will then satisfy the gate.

Kanban #1266 AC1. See _scratch/draft-seo-factcheck-gate.ps1 source for details.
"@

Emit-Decision -Decision 'deny' -Reason $denyReason
exit 2
