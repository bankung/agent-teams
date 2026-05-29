# Block PowerShell-invocation shapes that Bitdefender's heuristic flags. Pre-emptive: the
# agent never triggers a blocked call → no wasted turn time on opaque "Access is denied"
# bounces from the AV layer.
#
# Inspects PreToolUse on Bash and PowerShell. Returns "deny" + split-command guidance when
# matched. Lets benign multi-statement chains through (e.g. `cd dir; ls`) because the
# detectors target the specific shapes Bitdefender flags, not all `;` chains.
#
# Trigger shapes observed 2026-05-15+ on Bitdefender Windows endpoint:
#   - PowerShell-style chain with $LASTEXITCODE capture or `exit $...` propagation
#   - Out-File writing to %LocalAppData%\Temp\claude (dropper-like pattern)
#   - -EncodedCommand with non-trivial base64 payload
#   - -NoProfile -NonInteractive -Command "...;..." (multi-statement)
#
# Mirror shape of block-raw-sql-dml.ps1 (PreToolUse strike pattern, proven).

$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$cmd = $payload.tool_input.command

if (-not $cmd) { exit 0 }

$triggers = @(
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

foreach ($t in $triggers) {
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

        $output = @{
            hookSpecificOutput = @{
                hookEventName            = "PreToolUse"
                permissionDecision       = "deny"
                permissionDecisionReason = $reason
            }
        } | ConvertTo-Json -Compress -Depth 4

        Write-Output $output
        exit 2
    }
}

exit 0
