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

### Pivot 2026-05-23 — exclusion abandoned, detection chosen

Initial fix was a process exception for `powershell.exe` in Bitdefender → Advanced Threat Defense. **Reverted same day** after security review. Reasoning:

- **PS is a top-tier LOLBin.** ATD catches behavioral patterns (fileless malware, encoded payloads, in-memory .NET execution, defense disabling) that file-scan + URL-block layers don't catch. Blanket-excluding `powershell.exe` weakens defense across EVERY project on this PC, not just agent-teams.
- **Real problem is silent-fail invisibility,** not block rate. ~5-10% of harness PS invocations are blocked, but the harness reports success-shape output to Lead so the block is invisible. Likely the upstream cause of #1454 (mid-flight harness command terminated by AV leaves filesystem inconsistent).
- **Fix the visibility, keep the protection.** Bitdefender ATD stays active. Detection-based approach (#1463) pushes ntfy alert to operator's phone whenever a block event fires, so operator (and Lead) know to re-run the affected command.

### Resolution (detection-based)

1. **Leave Bitdefender ATD active.** Do NOT add a process exception for `powershell.exe`. Any previous exception must be removed: Bitdefender → Manage exceptions → Advanced Threat Defense tab → delete the `c:\windows\system32\windowspowershell\v1.0\powershell.exe` entry.
2. **Install the block-event poller** — see #1463 for implementation. Polls Bitdefender event log every 5-10min, posts to ntfy when a "Malicious command line detected" event fires for the harness PS pattern.
3. **When a ntfy alert fires:** identify the affected harness invocation (timestamp + truncated command line in alert body), manually re-run the affected work. Until #1463 lands, operator periodically checks Bitdefender Notifications / History by hand.

### Verify (detection mechanism — pending #1463)

After #1463 lands: trigger a known-blockable pattern → expect ntfy push on phone within one polling cycle. Until then: blocks remain silent; operator's signal is "agent claims success but no diff lands" or "context file vanishes".

### Connects to

- `#1454` (78-file context/ deletion — likely downstream of silent harness blocks; may self-resolve once #1463 visibility is live)
- `#1462` (parent fix-task; pivoted 2026-05-23 from exclusion → detection)
- `#1463` (detection mechanism replacing the abandoned exclusion approach)
- `#1192` (ntfy push channel reused by #1463 poller)

## Recurring web 500 / unstyled "หน้าเละ" — corrupt `.next` (2026-05-31, #1624)

### Symptom
The web container (`next dev`) serves a broken page: either every `GET /` returns 500 with `TypeError: e[o] is not a function` at `.next/server/webpack-runtime.js`, OR the HTML loads (200) but every `/_next/static/*` chunk 404s (page renders unstyled — "หน้าเละ"). Both = an inconsistent `.next/`.

### Root cause
`next dev` keeps an incremental build manifest in `.next/`. It corrupts when:
- **(a) hot-reload churn** — rapid successive FE edits (a dev-frontend agent rewriting several components) race the incremental compiler. Observed 2026-05-27/28 (#1183, #1620 edits).
- **(b) a concurrent `next build`** — running `npm run build` against the same bind-mounted `web/` overwrites the dev server's `.next` with prod-hashed chunks → the live dev server 404s every chunk. Observed 2026-05-31 (#1726 verification ran `npm run build`).

### Mitigation (AC2 option b — documented restart)
- **Recover:** `docker compose -p agent-teams restart web` → `next dev` regenerates `.next` cleanly. Operator hard-refreshes (Ctrl+Shift+R) to drop cached broken assets.
- **Prevent (b):** NEVER run `npm run build` / `next build` against the live container's `web/`. Verify FE changes with `npx tsc --noEmit` + `npm run lint` only (neither writes a prod `.next`). Saved as operator memory `no-next-build-against-live-dev`.
- **Prevent (a):** after a burst of FE edits, if the page misbehaves, restart web FIRST (rule out `.next` corruption before debugging code).

### Connects to
- `#1726` (2026-05-31 incident — dev-frontend ran `npm run build` during FE verification)
