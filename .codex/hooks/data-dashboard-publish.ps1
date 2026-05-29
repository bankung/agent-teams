# DRAFT (Kanban #1271 AC3) — Data Analytics team dashboard-publish audit hook.
# PostToolUse on Write for dashboard-designer (any agent writing *dashboard*.md
# or *data-analytics*spec*.md). Scans the written content for PII / financial /
# health vocabulary and emits a stderr WARN + audit-log entry. PostToolUse is
# informational only — cannot block. The point is to leave a trail.
#
# DRAFT ONLY — do NOT install. Lead handles agent file + .codex/hooks/ placement
# per feedback_codex_dir_humans_only.md.
#
# Registration snippet (Lead writes into .codex/agents/<agent>.md frontmatter):
#   hooks:
#     PostToolUse:
#       - matcher: Write
#         hooks:
#           - type: command
#             command: powershell -NoProfile -ExecutionPolicy Bypass -File .codex/hooks/data-dashboard-publish.ps1
#
# Audit log: _scratch/data-audit-trail.log (future: POST to /api/audit-events
# once that endpoint exists — same TODO as the SEO ranking-report hook).
# Fail-soft on any parse error — emit allow + exit 0.

$ErrorActionPreference = 'Continue'

# Minimum-viable PII/financial/health vocabulary. 12 entries; intentionally
# narrow to keep false-positive rate manageable. Word-boundary regex.
$SensitiveKeywords = @(
    'email',
    'phone',
    'ssn',
    'passport',
    'credit_card',
    'salary',
    'bank_account',
    'patient_id',
    'diagnosis',
    'prescription',
    'medical_record_no',
    'date_of_birth'
)

$AuditLog = '_scratch/data-audit-trail.log'

function Emit-Allow {
    $out = @{
        hookSpecificOutput = @{
            hookEventName      = "PostToolUse"
            permissionDecision = "allow"
        }
    } | ConvertTo-Json -Compress -Depth 4
    Write-Output $out
}

try {
    $payloadRaw = [Console]::In.ReadToEnd()
    if (-not $payloadRaw) {
        Emit-Allow
        exit 0
    }
    $payload = $payloadRaw | ConvertFrom-Json
} catch {
    Emit-Allow
    exit 0
}

$toolName = $payload.tool_name
if ($toolName -ne 'Write') {
    Emit-Allow
    exit 0
}

# tool_response.success can be missing on older payload shapes; treat null as
# success (don't penalize unknown).
$success = $payload.tool_response.success
if ($success -eq $false) {
    Emit-Allow
    exit 0
}

$filePath = $payload.tool_input.file_path
if (-not $filePath) {
    Emit-Allow
    exit 0
}

# Path-pattern scope: dashboard markdown OR data-analytics spec markdown.
$isDashboard      = ($filePath -match '(?i)dashboard.*\.md$')
$isAnalyticsSpec  = ($filePath -match '(?i)data-analytics.*spec.*\.md$')
if (-not ($isDashboard -or $isAnalyticsSpec)) {
    Emit-Allow
    exit 0
}

# Content extraction. The PostToolUse payload may carry tool_input.content
# (Write tool's source-of-truth) — prefer that over reading from disk because
# disk reads need extra permission grants.
$content = $payload.tool_input.content
if (-not $content) {
    # Fallback: try to read the file. If it fails, fail-soft to allow.
    try {
        if (Test-Path -LiteralPath $filePath) {
            $content = Get-Content -LiteralPath $filePath -Raw -ErrorAction Stop
        }
    } catch {
        # Couldn't read — fail-soft.
        Emit-Allow
        exit 0
    }
}

if (-not $content) {
    Emit-Allow
    exit 0
}

# Scan for sensitive keywords. Word-boundary, case-insensitive.
$found = @()
foreach ($kw in $SensitiveKeywords) {
    if ($content -match "(?i)\b$([regex]::Escape($kw))\b") {
        $found += $kw
    }
}

if ($found.Count -gt 0) {
    $timestamp = Get-Date -Format 'yyyy-MM-ddTHH:mm:ssZ'
    $foundList = ($found -join ', ')
    $warnLine  = "[sensitive-data-touch] $timestamp file=$filePath keywords=$foundList"

    # stderr WARN — Lead sees it; PostToolUse stdout would be additionalContext,
    # but we keep the audit signal on stderr per existing hook conventions.
    [Console]::Error.WriteLine($warnLine)

    # Append to audit log. Best-effort; fail-soft.
    try {
        $auditDir = Split-Path -Parent $AuditLog
        if ($auditDir -and -not (Test-Path -LiteralPath $auditDir)) {
            New-Item -ItemType Directory -Path $auditDir -Force | Out-Null
        }
        Add-Content -LiteralPath $AuditLog -Value $warnLine -ErrorAction Stop
    } catch {
        # TODO (Kanban #?): POST to /api/audit-events instead of log file.
        # Mirrors the SEO ranking-report hook future-work comment.
    }
}

Emit-Allow
exit 0
