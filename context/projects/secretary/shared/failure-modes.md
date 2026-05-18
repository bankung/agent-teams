# Failure modes: Escalation protocol + halt categories

**Purpose:** Document expected failure modes, halt conditions, and the LITERAL escalation message secretary uses to pause and return control to Lead. Each category includes the symptom, when to fire, and the exact halt sentence.

**Source:** Secretary agent definition, Escalation protocol, lines 195–203. See also CLAUDE.md on subagent discipline.

**Critical rule:** Secretary MUST halt and escalate on these conditions. Do NOT retry silently, do NOT improvise workarounds, do NOT continue the task. Halt → return to Lead → Lead surfaces to operator or intervenes.

---

## Category 1: Chrome MCP session expired

**When it happens:** Operator logged in once at session start (Gmail, LinkedIn, JobsDB). Secretary navigates to a service, page redirects to login, or Chrome extension returns "session not authenticated."

**Symptom:**
- Chrome MCP `navigate` returns login page when expecting authenticated view.
- `read_page` returns "Sign in to continue" instead of content.
- Chrome extension reports "auth expired" in console.

**Action:** Do NOT attempt to login, password-fill, or work around. Operator must re-login via browser.

**Halt message:**
```
HALT: Chrome MCP session expired on <channel> (Gmail/LinkedIn/JobsDB) at URL <page_url> 
— operator must re-login to <service> in Chrome, then re-spawn secretary.
```

**Lead recipient:** Lead surfaces message to operator in chat. Operator re-logs in, signals ready. Lead re-spawns secretary with task context.

---

## Category 2: Knowledge base incomplete

**When it happens:** Secretary tries to `Read` a required KB file (from lines 84–90 of agent def) and file is missing, or file contains `[TODO]` markers indicating incomplete content.

**Symptom:**
- `Read` returns file not found (e.g., `context/projects/secretary/shared/voice.md` does not exist).
- File exists but contains lines like `[TODO: add tone guidelines]` or `[TODO: operator fill in X]`.

**Action:** Do NOT proceed without the full KB. Halt immediately.

**Halt message:**
```
HALT: required KB file missing or incomplete: <file_path>
Sections marked [TODO]: <list of section names>
— Lead: complete KB before re-spawning secretary
```

**Lead recipient:** Lead edits the KB file, completes missing sections, re-spawns secretary.

**Example:**
```
HALT: required KB file incomplete: context/projects/secretary/shared/job-criteria.md
Sections marked [TODO]: "Deal-breaker rubric", "Example cover letters"
— Lead: complete before re-spawning
```

---

## Category 3: HITL answer ambiguous

**When it happens:** Operator answers a HITL pause, but the answer is unclear, contradictory, or missing required fields (e.g., "edit it" without specifying what).

**Symptom:**
- Spawn brief includes `operator_answer` but value is vague (e.g., "not sure" / "maybe" / "edit option 2").
- Operator says "revise the draft" but doesn't specify what to change.
- Conflicting instructions (e.g., "post it" AND "hold for now").

**Action:** Do NOT guess. Ask for clarification.

**Halt message:**
```
HALT: operator answer ambiguous
Received: "<operator_answer_text>"
Required clarification: <question>
— Lead: ask operator to clarify before re-spawning
```

**Lead recipient:** Lead re-asks operator in chat. Operator provides clarification. Lead re-spawns with clear answer.

**Example:**
```
HALT: operator answer ambiguous
Received: "edit option 2"
Required clarification: Which parts of Option 2 should I revise? (wording / structure / all three points?)
— Lead: ask operator for specifics
```

---

## Category 4: Operator instruction contradicts rules

**When it happens:** Lead's spawn brief or operator_answer directly contradicts a rule in `email-rules.md`, `job-criteria.md`, or `linkedin-strategy.md`. Unclear whether this is a one-off override or a rule that needs updating.

**Symptom:**
- Spawn brief says "archive all newsletters" but also includes a specific newsletter in `priority_senders`.
- Operator asks to apply for a job in forbidden industry (contradicts `job-criteria.md` deal-breaker).
- Operator asks to post about topic in `audience_NOT_for` list.

**Action:** Do NOT proceed. Halt and ask Lead to decide: one-off override or rule change?

**Halt message:**
```
HALT: operator instruction conflicts with rule
Instruction: "<instruction_text>"
Conflicts with: <rule_file>:<section_name>
— Lead: decide whether (A) one-off override, or (B) rule needs updating. Re-spawn with decision.
```

**Lead recipient:** Lead clarifies with operator: is this a one-off exception or a permanent rule change? Lead re-spawns with override or rule update.

**Example:**
```
HALT: operator instruction conflicts with rule
Instruction: "Apply for this DevOps role at FinTech startup"
Conflicts with: job-criteria.md:Category-4-deal-breakers (no finance/fintech industry)
— Lead: is this a one-off override or should we update the criteria?
```

---

## Category 5: Unknown category of work

**When it happens:** Lead assigns work that doesn't match any of the 7 Patterns (Email triage / Job application / LinkedIn post / Daily digest / Calendar prep / News digest / Cross-channel synthesis). Secretary encounters work outside the defined scope.

**Symptom:**
- Spawn brief describes a task like "summarize competitor pricing" (not in 7 patterns).
- Work requires specialist agent capability (e.g., "write code" / "design architecture").
- Work is multi-project or cross-domain (e.g., "file a GitHub issue on another project").

**Action:** Do NOT improvise a new pattern. Halt and ask Lead to scope: new Pattern needed or specialist hand-off?

**Halt message:**
```
HALT: encountered work outside known Patterns
Task summary: <description>
Closest pattern: <pattern_name> (but doesn't fit because: <reason>)
— Lead: either (A) file new Pattern + re-scope, or (B) hand off to specialist agent
```

**Lead recipient:** Lead files a new task on agent-teams (or another project) to define the new Pattern. Or Lead hands off to appropriate specialist. Re-spawns secretary with clarity.

**Example:**
```
HALT: encountered work outside known Patterns
Task summary: "Research salary market for software engineers in NYC, write 2-page report"
Closest pattern: News digest (but doesn't fit because: secretary should not write multi-page reports, only LinkedIn posts + summaries)
— Lead: is this (A) new Pattern "market research report" + define scope, or (B) hand off to dev-researcher?
```

---

## Category 6: Budget alarm (token estimate exceeds 50k)

**When it happens:** Secretary estimates that completing the task will consume >50k tokens. Secretary is designed to be cheap (low-context tier); 50k+ indicates scope creep or inefficiency.

**Symptom:**
- Task is: triage 200 emails + write 50 cover letters + draft 10 LinkedIn posts in one session.
- Secretary starts drafting and realizes the nested loops (read email → search job → score job → draft letter) will blow budget.
- Spawn brief includes multiple workflows in one task (e.g., "email triage + job search + calendar prep + news digest", each >10k tokens).

**Action:** Do NOT proceed past the budget. Halt and ask Lead to re-scope or confirm proceed.

**Halt message:**
```
HALT: estimated tokens for this task exceed 50k budget alarm
Task scope: <description>
Estimated breakdown: <rough estimate, e.g., emails: 5k, job search: 20k, drafting: 28k = ~53k total>
— Lead: confirm proceed with overbudget, or re-scope to bring under 50k
```

**Lead recipient:** Lead either (A) confirms secretary should proceed (operator approved large batch), or (B) asks secretary to re-scope (e.g., "triage top 30 emails only, defer job search to next session").

**Example:**
```
HALT: estimated tokens exceed 50k budget alarm
Task scope: "triage inbox, search 40 jobs on JobsDB + LinkedIn, score and draft cover letters, draft 5 LinkedIn posts"
Estimated breakdown: email triage 3k, job search + scrape 22k, scoring 8k, cover letters 12k, LinkedIn drafts 10k = ~55k total
— Lead: confirm I should proceed, or re-scope (e.g., defer LinkedIn posts to next session)?
```

---

## Category 7: Chrome MCP bot detection (optional; high-risk workflows)

**When it happens:** LinkedIn, JobsDB, or other service returns a CAPTCHA, "verify you're human" page, or bot-detection challenge. Chrome extension cannot bypass.

**Symptom:**
- LinkedIn page shows: "Unusual activity detected — please solve a CAPTCHA."
- JobsDB redirects to challenge page with image verification.
- Chrome MCP `read_page` returns challenge HTML instead of expected content.

**Action:** Do NOT attempt to solve CAPTCHA programmatically. Operator must complete challenge manually.

**Halt message:**
```
HALT: bot-detection challenge on <channel> (LinkedIn/JobsDB)
URL: <page_url>
Challenge type: <CAPTCHA/image verification/email verification>
— operator must complete challenge manually in Chrome, then signal ready. Re-spawn secretary.
```

**Lead recipient:** Lead signals operator to complete challenge. Operator clicks CAPTCHA, verifies email, etc. Signals back. Lead re-spawns secretary.

**Example:**
```
HALT: bot-detection challenge on LinkedIn
URL: https://www.linkedin.com/checkpoint/challenge/...
Challenge type: CAPTCHA + SMS verification
— operator must complete in Chrome browser, then re-spawn secretary
```

---

## Category 8: Subagent classifier pre-block (added 2026-05-18 per #1178)

**When it happens:** Lead's `Agent({subagent_type, prompt})` call dies at the FIRST `mcp__Claude_in_Chrome__*` tool call (typically `tabs_context_mcp` / `tabs_create_mcp`) because Claude Code harness's subagent permission classifier scanned the spawn brief PRE-execution and flagged it as needing operator pre-approval for external-action intent.

**Symptom:**
- Agent's final report includes: "Halted by harness permission classifier — cannot proceed without operator approval"
- ZERO Chrome tool calls executed (maybe ToolSearch/Read before block)
- NO Chrome UI touched, NO partial state in browser, NO draft saved on disk
- Trigger: spawn brief text contains external-action verbs ("send email to <external>", "submit application", "post to LinkedIn", "publish")

**Action:** Do NOT auto-retry with same brief. Halt + escalate to Lead with category tag.

**Halt message:**
```
HALT: subagent classifier blocked send-intent spawn before tool boundary (Category 8).
Trigger: brief contained external-action phrasing.
Recovery: secretary completes upstream work (research + draft); Lead-direct executes the send step after operator HITL approval.
See: .claude/docs/url-deeplink-tricks.md for Lead-direct compose patterns.
```

**Lead recipient:** Lead. Recovery procedure:
1. Re-spawn secretary with brief rephrased to "draft only — Lead handles send" (use neutral verbs: evaluate / recommend / compose-draft; AVOID send / submit / post / publish)
2. Secretary outputs draft to `general/<file>.md`
3. Lead surfaces draft to operator in chat
4. Operator types `approve` / `edit` / `reject`
5. On `approve`: Lead-direct opens own Chrome MCP tab + uses URL deeplink trick (Gmail) or manual fill (Outlook) + clicks Send + verifies
6. Lead PATCHes Kanban DONE

**Example:**
```
HALT: subagent classifier blocked. Brief said "send email to bankung99@hotmail.com via Gmail Compose UI".
Detected trigger: "send email to <external>" + recipient address parsing.
Recommended: rephrase brief to "compose draft for bankung99@hotmail.com test message; Lead will handle send after operator approval".
```

**Cross-ref:** Kanban #1177 Lead-direct workaround pattern + #1178 this category + `.claude/docs/url-deeplink-tricks.md`.

---

## Category 9: Credential content exposed (added 2026-05-18 per #1179)

**When it happens:** Secretary read the body of an email matching `§🔒 do-not-read-body patterns` (email-rules.md) BEFORE the §🔒 classifier could fire — e.g., browser auto-expanded the email, agent invoked `read_page` without subject pre-check, or accessibility tree depth pulled body content during inbox scan.

**Symptom:**
- Body content (verification code / OTP / password / reset token / magic link URL) appears in secretary's working LLM context
- Subject of the email matched a §🔒 pattern (verification / OTP / password / 2FA / etc.)

**Action:** Halt immediately. Do NOT include exposed content in final report. Do NOT save draft. Do NOT echo credential in any form (not even truncated).

**Halt message:**
```
HALT: credential email body was read before §🔒 classification could fire (Category 9).
Sender: <sender_only>
Subject: [REDACTED — contains credential pattern]
Recommendation: operator rotate the affected credential as precaution (the credential is now in this session's LLM context window even if not echoed to chat output).
Recovery: do NOT continue triage of this email; mark escalate; operator handles personally.
```

**Lead recipient:** Lead. Surface to operator with:
- Sender domain only (no full email address if avoidable)
- Subject REDACTED
- Rotation recommendation for the affected credential
- DO NOT re-spawn on this email; let operator handle in their own browser

**Example:**
```
HALT: credential email body exposed.
Sender: noreply@rolife.com (subject matched §🔒 pattern "verification code")
Body included literal 6-digit code (now in LLM context).
Recommended operator action: ignore the code (typically <10 min validity); if account concern, generate fresh code on next login.
```

**Cross-ref:** email-rules.md §🔒 Do-not-read-body patterns + Kanban #1179 this category.

---

## Halt discipline checklist (for secretary self-check)

Before proceeding with any action, secretary asks:

- [ ] Is Chrome MCP session still authenticated? (If unsure, test with `read_page` on known authenticated page.)
- [ ] Are all required KB files present and complete? (No `[TODO]` markers?)
- [ ] Is operator_context populated with all required fields for this workflow?
- [ ] Does this task fit one of the 7 known Patterns?
- [ ] Is the scope under 50k tokens estimated?
- [ ] Do any operator instructions contradict the KB rules?
- [ ] If spawn brief contains external-action verbs (send/submit/post/publish) AND I'm a subagent: am I about to die at first Chrome MCP tool call due to classifier pre-block (Category 8)? → Halt early; Lead-direct handles send.
- [ ] If reading an email and the subject matched §🔒 password/OTP/verification patterns: did I read body content before the §🔒 classifier could fire (Category 9)? → Halt; do NOT echo credential; flag for operator rotation.

If ANY answer is "no", fire the corresponding halt message and return to Lead.

---

## Lead's corresponding responsibilities

When secretary halts:

1. **Immediately read** the halt message and category.
2. **Do NOT loop secretary** — ask for input or make a decision first.
3. **Route to appropriate actor:**
   - Sessions expired → operator re-logs in.
   - KB incomplete → Lead completes KB, then re-spawns.
   - HITL ambiguous → Lead asks operator for clarification, then re-spawns.
   - Contradictory rules → Lead decides override-or-update, then re-spawns.
   - Unknown category → Lead files new task or hands off to specialist.
   - Budget alarm → Lead confirms proceed or re-scopes.
   - Bot detection → operator completes challenge, then re-spawns.
4. **Re-spawn secretary** with updated context (new KB sections, operator clarification, override flag, etc.).

---

## No silent retries

Secretary MUST NOT:
- Attempt login-retry on Chrome session expiry.
- Guess at KB content if section is `[TODO]`.
- Proceed with contradictory instructions "just to try".
- Improvise a new workflow pattern.
- Continue if token budget exceeds 50k.
- Attempt to solve CAPTCHA or bypass bot detection.

Halt = stop entirely. Lead and operator decide next step.
