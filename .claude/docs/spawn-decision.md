# Spawn-vs-Lead-direct decision tree (#1189)

Before spawning a subagent, walk this ladder — first exit wins. Complements Karpathy lane
item 5 (token economy) and golden-rule carve-out E (#2385: Lead may directly make ≤5-line
doc/comment/CSS single-file edits it already has in context).

1. **Can Lead finish it in ≤5 Read/Edit/curl calls without pulling >10k tokens of new
   context?** YES → Lead-direct. Spawn overhead ≈ a full context bootstrap + report
   round-trip — often more than the task itself for small edits.
2. **Does the work need isolated context** — exploration that would pollute the Lead's
   context, or expected output ≥10k tokens? NO → Lead-direct in current context.
3. **Is it pattern-bound specialist work** (a role playbook exists: dev-*, content-*,
   secretary patterns)? YES → spawn the specialist (consistency + role-state compounding).
4. **Value check:** operator time saved + decision quality > spawn cost (~$0.10–1.00)?
   NO → Lead-direct or defer.

**Hard overrides (never Lead-direct regardless of size):** target-project artifact writes
beyond carve-out E; trust-boundary/security-sensitive edits; email actions (secretary-role
only, #1585).

## Worked examples

| Task | Verdict | Why |
|---|---|---|
| Read 1 file, edit 3 doc lines | Lead-direct | carve-out E territory |
| Rename a variable across 2 files | Lead-direct | mechanical, ≤5 calls |
| Compare 2 CV files | Lead-direct | parse + compare fits current context |
| Compare 2 CVs × 6 JDs with per-JD drill-down | Spawn | cross-multiplied reads want isolation |
| Scan inbox → top-3 unread + actions | Spawn (secretary) | pattern-bound + gated tool surface |
| New endpoint + migration + tests | Spawn (dev-sr-backend) | multi-file specialist work |
| Audit 30 agent files → recommendation table | Spawn | >10k context pull, report-shaped output |
| Fix an obvious 1-line failing import | Lead-direct | smallest surgical edit, verify inline |

Measurement: project-auditor trends the Lead-direct share on small tasks over coming
sessions (#1189 AC3); revisit thresholds when real data disagrees with the ladder.
