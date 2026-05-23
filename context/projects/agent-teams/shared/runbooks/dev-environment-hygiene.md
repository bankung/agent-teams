# Dev environment hygiene runbook

Windows-specific ops snags that affect the Claude Code harness running against agent-teams. Operator-desk reference.

## Bitdefender ATD blocks harness PowerShell — fix (2026-05-23, #1462)

### Symptom

Bitdefender's **Advanced Threat Defense** ("Malicious command line detected" notification) silently blocks `powershell.exe` invocations matching the Claude Code harness wrap pattern:

```
powershell.exe -NoProfile -NonInteractive -Command "<harness wrap of Bash tool>"
powershell.exe -NoProfile -NonInteractive -NoLogo -EncodedCommand <base64>
```

Detected 2026-05-23 while investigating #1454 (78-file context/ deletion). Bitdefender event log showed **52 blocked invocations** between 2026-05-14 and 2026-05-22 on operator's machine. The block is heuristic and non-deterministic — most invocations succeed; a fraction get flagged.

### Impact

- Silent partial failures of Bash tool calls (docker exec, pytest, git, file ops)
- Possible upstream cause of #1454 (a mid-flight harness command leaves filesystem in inconsistent state when terminated by AV)
- Operator may observe: "agent reported done but no diff", "tests pass-then-fail next run", "context files mysteriously gone"

### Resolution

1. Open Bitdefender → **Protection** → **Advanced Threat Defense** → **Manage exceptions**
2. Tab: **Advanced Threat Defense** (NOT Antivirus tab — different scanner layer)
3. **+ Add an Exception**
4. Enter process path: `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`
5. Save

### Verify

Run any harness Bash command (e.g., `docker compose ps`) and confirm completion with expected output + exit 0. No Bitdefender notification.

### 7-day re-check (2026-05-30)

Bitdefender → **Notifications** / **History** → filter "Malicious command line" → confirm zero new entries since 2026-05-23 exclusion. If new entries appear, the exclusion is not catching all variants — investigate the exact command line in the new entries.

### Connects to

- `#1454` (78-file context/ deletion — possibly downstream of pre-exclusion blocks)
- `#1462` (this fix's task)
