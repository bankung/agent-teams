# Output compression discipline — subagent reports

> Added 2026-05-18 per Kanban #1188. Applies to ALL subagent reports (secretary + future specialists). Saves ~3-5k tokens per spawn at scale.

## Why

Subagent final reports often include narrative preamble that:
- Adds 3-5k tokens per spawn (just boilerplate)
- Slows Lead's parsing (Lead must skim to find actual data)
- Dominates Lead's context window with low-information content

At scale (100s of spawns/day for headless cron-batched ops), this compounds: 5k × 200 spawns = 1M tokens of pure narrative = ~$15/day wasted on chrome.

## Required output discipline

### ✅ ALLOWED output shapes

- Markdown tables (for ranked / categorized data)
- Bulleted lists (for sequential items)
- Section headings (`## Summary`, `## Counts`, `## Action-required`)
- 1-2 sentence Summary AT THE TOP (not preamble — actual summary)
- Code blocks for literal data (URLs, IDs, exact quotes)

### ❌ FORBIDDEN output patterns

- "Let me think through this..."
- "I will now..."
- "Here is my analysis..."
- "In summary," / "To summarize," (at START of report — use the `## Summary` heading instead)
- "Based on my findings,"
- "I have completed..."
- "It is important to note that..."
- "As you can see,"
- Restating the spawn brief back to Lead ("You asked me to scan inbox...")

### ⚠️ AVOID (but not strictly forbidden)

- Mid-report transition phrases ("Moving on to...", "Now let's look at...")
- Hedging language ("It seems that...", "It appears...") — use direct: "Inbox has 215 unread."
- Apologies ("Apologies for the long output", etc.)

## Examples

### ❌ Verbose (3.2k tokens of preamble)

```
Let me think through this email triage task carefully.

I will now navigate to Gmail and scan the inbox for unread emails per the priority handling policy you established.

Here is my analysis of the top 30 unread emails:

Based on my findings, the dominant pattern is automation noise...

[actual data buried 500 words in]
```

### ✅ Compressed (0.3k tokens of structure)

```
## Summary
30/30 classified; 29 archive-class (automation/marketing/transactional); 0 reply-now/later; 1 escalate (operator self-test).

## Classification table
[markdown table]

## Counts
- archive: 29
- escalate: 1
- HITL pauses: 0
```

→ **10× less tokens for SAME information density.**

## Enforcement

1. **Spawn brief should include this rule explicitly** at the end:
   > "Output format: structured markdown only. NO narrative preamble. Forbidden phrases per .claude/docs/output-compression-discipline.md. Report sections only."

2. **Agent .md files should reference this doc** in their output format section (`see .claude/docs/output-compression-discipline.md`)

3. **Lead reviews subagent reports** for compression violations — if observed, note in Kanban + adjust agent prompt

## Operator-facing override

For interactive Lead sessions where operator may benefit from conversational warmth (rare), Lead can override per-spawn with:
> "...Output format: standard conversational report — narrative OK."

Default = compressed.

## Cost rationale

- Today's secretary spawns: avg ~5k tokens of preamble per report
- Compressed spawns: avg ~500 tokens of structure
- Savings: ~4.5k tokens/spawn × $0.000015/token (Sonnet output) = ~$0.07/spawn
- 100 spawns/day = $7/day saved
- 1000 spawns/day (high-volume scale) = $70/day saved

## Cross-ref

- Kanban #1188 (this task)
- Applies to: secretary + secretary-email-triage + secretary-job-scout + secretary-linkedin-content + auditor + future specialists
- Companion: .claude/docs/url-deeplink-tricks.md (Mode A compose+send cost reduction)
