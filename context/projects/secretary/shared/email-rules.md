# Email Rules: Classification + Auto-archive patterns

**Purpose:** Define the decision tree for classifying any incoming email into one of 4 buckets: `reply-now` / `reply-later` / `archive` / `escalate`. Generic patterns live here; operator-specific overrides arrive via `operator_context.email_rules_overlay` at spawn time.

**Source:** Secretary agent definition, Pattern 1, lines 141–148. See also `profile.md` for optional overlay fields.

## Priority handling policy (operator-instructed 2026-05-18)

The classification buckets below (`reply-now` / `reply-later` / `archive` / `escalate`) describe **WHAT** to do. This section describes **HOW** to do it — specifically the operator-approval flow per priority tier. Apply this overlay before the classification action fires.

### Three priority tiers

- **🟢 Low priority + matched explicit pattern** (transactional receipts, marketing promos, system notifications matching auto-archive list):
  - **Action: mark-as-read** (NOT archive — operator wants emails to remain searchable in inbox)
  - **HITL: NONE** — secretary auto-applies
  - **Report:** count only in digest

- **🟡 Middle priority** (operational signals, partial pattern match, ambiguity — e.g., delistings of crypto assets operator might hold, system maintenance notices, regulatory guidelines):
  - **Action:** GROUP similar emails (by sender / sub-pattern / topic) and present as a BATCH
  - **HITL: required PER GROUP** (NOT per email) — operator approves the whole group at once, or specifies exceptions
  - **Report:** list groups in `## Action-required` section

- **🔴 High priority** (security alerts on real surfaces, financial requests with action needed, recruiter at on-target role, escalate-class):
  - **Action: leave UNREAD** — do not auto-mark-read or archive
  - **HITL: NONE this spawn** — operator reviews personally
  - **Report:** count only + list in escalate section

### Operational notes

- **LOW vs MID** distinction = pattern certainty. LOW = clearly fits explicit auto-archive list. MID = partial match OR operational/regulatory content (delistings, maintenance, regulatory updates).
- **HIGH is rare** — reserve for items where operator's personal judgment is the actual value (recruiter at target company, security alert on financial account, legal communication).
- **LOW bulk action: prefer `mark-as-read` OVER `archive`** — operator's preference (distinct from prior pure auto-archive convention). Reason: searchability in inbox > inbox-zero discipline.
- **Override:** if `email_rules_overlay.priority_overrides` is set in `operator_context`, those override the default tier assignment per sender/subject pattern.

### Validation history

- 2026-05-18 Bitkub/Orbix HITL group test (Kanban task #1175 era): filter caught 14 LOW emails out of 24 unread cluster, marked-read in bulk (no per-email HITL); 9 MID preserved unread (delistings CLEAR/ALPHA/DAI, ORBIX maintenance ×2, NDID suspension, scam warning, out-of-service, regulatory guidelines); 0 HIGH found. Filter imperfection noted: Thai-only scam warning "ระวังมิจฉาชีพ" slipped past English-keyword exclusion — operator may add Thai keywords or accept minor imperfection.

## Classification buckets

### 1. Reply-now
**Condition:** Requires immediate action; delay reduces value or breaks workflow.

Examples:
- Direct question from close contact (mentor, team lead, close friend) that needs an answer today.
- Time-sensitive approval request ("Can you review this PR by 5pm?").
- Scheduling request with a deadline (meeting invite, RSVP needed by tomorrow).
- Recruiter follow-up after operator expressed strong interest.

**Action:** Draft reply per `voice.md`. Return to Lead with draft in Action-required queue. Operator approves or edits before send.

### 2. Reply-later
**Condition:** Needs a reply, but no urgency. Can batch with weekly or bi-weekly email round.

Examples:
- Non-time-critical question from a colleague or contact.
- FYI email that warrants acknowledgment but not today.
- Feedback or suggestion that's helpful but not blocking.
- General networking follow-up.

**Action:** Stash in `context/projects/secretary/general/triage-<date>.md` with sender, subject, and 1-line action needed. Secretary will surface in daily digest for operator to batch-reply.

### 3. Archive (auto-archive without reading reply)
**Condition:** No action needed; safe to remove from inbox without operator review.

Examples:
- Newsletters and subscriptions (even if unread).
- Transactional emails: receipts, shipment notifications, password resets, account confirmations.
- Automated alerts / status updates with no action item (CI/CD passed, server uptime report, weekly metrics digest).
- "No reply" senders (noreply@, Billing <billing@>, notifications@).
- Marketing / promotional emails.

**Action:** Archive silently. No operator notification needed. Count in end-of-session report ("X emails auto-archived").

### 4. Escalate
**Condition:** Requires operator attention but NOT a standard reply. May need context, decision, or forwarding.

Examples:
- Legal inquiry or document requiring review.
- Financial request: invoice, refund request, cost negotiation, subscription cancellation.
- Security alert (password compromise, account access attempt, data breach notice).
- Recruiter reaching out with a strong match for operator's target roles (not escalate due to urgency, but due to decision complexity — operator decides to engage, decline, or defer).
- Conflict or ambiguity that contradicts rules (e.g., VIP person sends promotional email; rule says auto-archive, but priority_senders says "keep").
- Unknown sender claiming authority or requesting action (phishing risk assessment).

**Action:** Flag in triage report. Include sender, subject, and 1-line reasoning. Let operator decide next step (forward to advisor, review fully, decline). Do NOT auto-reply or archive.

## Decision tree (if/then ladder)

Apply rules in this order. First match wins.

```
IF sender domain is in noreply / notifications / billing / system addresses
  → ARCHIVE (transactional, no-reply)

ELSE IF email subject matches auto_archive_overrides exception (from operator_context)
  → Use override (may reverse an archive decision)

ELSE IF sender is in priority_senders list (VIP, mentor, family, close friend)
  → READ FULL EMAIL (see substeps below)
    → IF asks question or seeks decision
         → REPLY-NOW
       ELSE IF FYI / check-in with no action
         → REPLY-LATER
       ELSE IF financial / legal / security implication
         → ESCALATE

ELSE IF email is promotional / marketing / newsletter
  → ARCHIVE

ELSE IF subject indicates transactional (receipt, confirmation, reset, alert)
  → ARCHIVE

ELSE IF email is from recruiter or job board
  → READ SUBJECT + FIRST 50 CHARS OF BODY
    → IF matches operator's target_roles (from operator_context)
         → ESCALATE (operator decides engagement)
       ELSE IF generic outreach or poor fit
         → ARCHIVE

ELSE IF sender is known but not priority (colleague, peer, acquaintance)
  → READ FULL EMAIL
    → IF asks direct question or requests action
         → REPLY-NOW or REPLY-LATER (based on urgency)
       ELSE IF FYI / social
         → REPLY-LATER or ARCHIVE (depends on relationship)

ELSE IF sender is unknown
  → TREAT AS RISKY. READ FULL EMAIL.
    → IF requests personal info / login / payment
         → ESCALATE (phishing check)
       ELSE IF is obvious spam / malware link
         → ARCHIVE
       ELSE IF cold outreach with clear value (job, collaboration, investment)
         → ESCALATE (operator reviews; too much signal to ignore)
       ELSE IF rambling or unclear intent
         → ARCHIVE

ELSE (default)
  → REPLY-LATER (benefit of doubt; let operator batch later)
```

## Explicit auto-archive patterns

These patterns ALWAYS auto-archive (no operator intervention):

1. **Email provider system messages**
   - Gmail tips / Google Workspace alerts / account summaries
   - Bounce-backs, delivery failures, DKIM/SPF warnings

2. **Subscription confirmations + newsletters**
   - "Welcome to [service]" onboarding emails
   - Unsubscribe footer present + no custom content in body

3. **Receipts + transactional records**
   - Order shipped / delivery notifications
   - Receipt email from any e-commerce / SaaS / service
   - Invoice from vendor (unless operator_context says "route to finance")

4. **Automated alerts + metrics**
   - CI/CD success/failure (unless operator marks "REPLY-LATER" for failures)
   - Server uptime / performance reports
   - Weekly metrics digest / analytics summary
   - Backup confirmation, update installed

5. **Scheduling + calendar automation**
   - Calendar invite (secretary handles separately via Pattern 5)
   - Zoom/Meet link + reminder ("meeting in 15 mins")
   - Auto-acknowledgment ("Your reply was received")

6. **Marketing + promotional**
   - Subject line contains: "limited offer", "ends tonight", "sale", "promotion", "exclusive deal"
   - Bulk sender indicator: CC'd to >10 other addresses, generic greeting, no personalization

## Explicit escalate patterns

These patterns ALWAYS escalate (operator review required):

1. **Legal**
   - Subject: "subpoena", "legal action", "terms of service", "agreement review"
   - Body contains legal language ("hereby", "indemnify", "breach", "liable")

2. **Financial**
   - Subject or body: "invoice", "payment", "refund", "billing", "subscription", "charge", "transfer"
   - Any email requesting credit card / bank info
   - Cost negotiation or contract amendment

3. **Security / Account**
   - Subject: "password reset", "verify identity", "confirm login", "unusual activity", "suspicious access"
   - Unverified sender claiming to be from service operator uses
   - Any email with "click here to confirm" from unknown domain

4. **Recruiter outreach (on-target)**
   - Sender is recruiter / hiring manager from company on operator's target list
   - Sender is LinkedIn recruiter with clear role matching operator's `target_roles`
   - Operator decides: engage / decline / defer

5. **Conflict / ambiguous**
   - Rule says archive (e.g., newsletter) but sender is in priority_senders (contradiction).
   - Email violates multiple rules with different outcomes.
   - Secretary unsure of classification.

## 🔒 Critical: Do-not-read-body patterns (security hardening, 2026-05-18)

**Subjects matching the patterns below are CLASSIFIED `escalate` AND secretary MUST NOT read the email body — metadata only.**

Reason: subject lines themselves often contain credentials / codes / verification tokens (e.g., `Rolife CAPTCHA — Your verification code is: 644444`). Bodies almost always contain credentials, reset links, OTP tokens, or session secrets. Secretary has no business processing this data — operator handles personally.

### Subject patterns (case-insensitive, English + Thai)

**English (any of):**
- `verification code` / `verification token`
- `OTP` (any context: "Your OTP", "OTP for sign-in", etc.)
- `password reset` / `reset your password` / `reset password`
- `2FA` / `two-factor` / `two-step`
- `security code` / `security token`
- `one-time` / `one time` (code/password/passcode)
- `sign-in code` / `signin code` / `login code`
- `confirmation code`
- `magic link` / `passwordless`
- `confirm your account` / `verify your email` (when paired with token/code in body — assume worst-case)

**Thai (any of):**
- `รหัสยืนยัน` / `รหัสผ่าน` / `รหัส OTP`
- `รหัสครั้งเดียว` / `รหัสเข้าใช้`
- `ยืนยันตัวตน` / `ยืนยันการเข้าใช้`
- `ลิงก์เข้าใช้` (magic link)

### MANDATORY behavior

When secretary encounters a match:

1. **DO NOT** call `mcp__Claude_in_Chrome__read_page` on the email body
2. **DO NOT** call `mcp__Claude_in_Chrome__get_page_text` on the opened email
3. **DO NOT** click into the email row (which would open + auto-mark-read + expose body)
4. **DO NOT** include any body snippet in reports / drafts / digests beyond what is already visible in the inbox-list metadata row (sender, subject, timestamp, has-attachment)
5. **DO NOT** echo the subject if it appears to contain a literal code (e.g., subject like `Your code is: 123456` — truncate at the colon and report as `Your code is: [REDACTED]`)
6. Classify as `escalate` in the triage report
7. Action note for operator: `"Verification/credential email — operator handles personally"`

### If body is already exposed (accidental open / agent read it before classification)

Halt immediately. Fire **failure-modes.md Category 9** (credential-content-exposed — to be added):
- Do NOT include exposed content in final report
- Do NOT save draft to disk
- Surface to operator with minimal info: `"Credential email body was read before classification could fire. Recommend operator rotate the affected credential as precaution. Sender: <X>, Subject: [REDACTED]"`

### Exception (very narrow)

If operator EXPLICITLY in their spawn brief says `operator_explicit_read_credential_email: true` AND names the specific message-id, secretary may proceed. This is a manual override for legitimate edge cases (e.g., operator wants help parsing a complex multi-step recovery email). Default = blocked.

### Cross-ref

- `failure-modes.md` Category 9 (to be added in next session — see Kanban follow-up)
- Triggered by: 2026-05-18 incident where `Rolife CAPTCHA — Your verification code is: 644444` was reported in classify-spawn output (code expired, no harm, but pattern needs prevention)

## Bilingual exclusion keyword reference (added 2026-05-18 per #1195)

When constructing Gmail filter URLs with exclusion keywords (e.g., `-delisting -suspension`), use **both English AND Thai equivalents** for operator's bilingual inbox. English-only filters miss Thai-subject emails — observed 2026-05-18 when Bitkub Thai-only scam warning "ระวังมิจฉาชีพ" slipped past English-only filter into the LOW group.

| Pattern domain | English exclude keywords | Thai exclude keywords |
|---|---|---|
| Exchange listing changes | `delisting` / `unlisting` / `delist` | `เพิกถอน` / `ถอดออก` / `ยกเลิกการซื้อขาย` |
| Service status / outage | `suspension` / `"out of service"` / `outage` / `downtime` | `หยุดให้บริการ` / `ปิดปรับปรุง` / `ระงับการใช้งาน` |
| Maintenance | `maintenance` / `upgrade` / `scheduled maintenance` | `บำรุงรักษา` / `ปรับปรุง` / `แจ้งซ่อมบำรุง` |
| Regulatory / compliance | `guidelines` / `compliance` / `regulation` / `unfair` | `แนวทาง` / `หลักเกณฑ์` / `กฎ` / `ระเบียบ` / `กำกับดูแล` |
| Security notice / scam warning | `"security alert"` / `phishing` / `"unusual activity"` / `scam` | `ระวังมิจฉาชีพ` / `ปลอดภัย` / `เตือนภัย` / `แจ้งเตือน` |
| Account changes | `"account closed"` / `terminate` / `deactivate` | `ปิดบัญชี` / `ยุติบริการ` / `ยกเลิกบัญชี` |
| Financial / billing | `invoice` / `receipt` / `billing` / `payment` | `ใบกำกับภาษี` / `ใบเสร็จ` / `ใบแจ้งหนี้` / `ชำระเงิน` |

### Usage in filter construction

When agent (or Lead-direct) constructs a Gmail search URL for the LOW group (excluding MID priority items):

❌ English-only (incomplete): `is:unread (from:bitkub) -delisting -maintenance`
✅ Bilingual: `is:unread (from:bitkub) -delisting -maintenance -เพิกถอน -ปรับปรุง`

URL-encoding required for Thai characters — use `[uri]::EscapeDataString($string)` in PowerShell or equivalent. Hand-crafting Thai URL encoding is error-prone.

### Operator-specific patterns

If operator has narrow personal patterns (e.g., specific Thai phrase from a known sender), add via `operator_context.email_rules_overlay.priority_overrides`.

## Channel UI differences (added 2026-05-18 per #1176)

Secretary supports both Gmail (`mail.google.com`) and Outlook (`outlook.live.com` / hotmail.com accounts) for email triage. UI mechanics differ — specialist agents + Lead-direct flows must account for per-channel quirks:

| Concern | Gmail | Outlook |
|---|---|---|
| **Compose URL pre-fill** | FULL: `?view=cm&fs=1&to=...&su=...&body=...` honors all 3 fields | PARTIAL: `?deeplink/compose?subject=...` honors subject only; to + body need manual fill via form_input / computer.type |
| **Auto-signature** | Operator-configured; NOT auto-inserted in compose body | **AUTO-INSERTS** operator signature at body cursor on compose open (includes phone PII). Avoid pre-filling body via URL to prevent duplication. See `.claude/docs/url-deeplink-tricks.md` "Outlook auto-signature quirk" section. |
| **Bulk select** | Master checkbox + chevron dropdown (All/None/Read/Unread/Starred/Unstarred) | Different UI — verify before assuming |
| **Mark-as-read** | Envelope-with-checkmark icon in toolbar after selection (OR keyboard `Shift+I` if shortcuts enabled — confirm operator's setting) | Different icon location/label |
| **Archive** | Box-with-down-arrow icon | Usually labeled "Archive" text button |
| **Default page size** | 100 conversations | Different |
| **Bilingual content** | Same — both inboxes may contain TH+EN; use bilingual exclusion (see section above) | Same |

When constructing Lead-direct or specialist-agent flows that span both channels, account for per-channel selectors + signature handling. URL-deeplink details: `.claude/docs/url-deeplink-tricks.md`.

## Runtime operator overrides

Field name: `operator_context.email_rules_overlay`

Structure:
```json
{
  "priority_senders": ["alice@company.com", "mentor@university.edu"],
  "auto_archive_overrides": {
    "newsletter@tech-publication.com": "reply-later:archive-after-read",
    "cto@startup.com": "never-auto-archive"
  },
  "read_dont_process": ["partner@company.com"],
  "skip_folders": ["[Gmail]/Drafts", "Archive"],
  "recruiter_domains": ["linkedin.com", "builtin.com"]
}
```

- **priority_senders:** VIP list. Secretary reads in full; default to REPLY-NOW if asks question.
- **auto_archive_overrides:** <sender_email> or <domain> mapped to override rule. Use for exceptions (e.g., "usually archive newsletters, but not from this one").
- **read_dont_process:** Read for awareness, never reply or archive without operator intervention (e.g., business partner; every email is decision point).
- **skip_folders:** Folders to ignore entirely (e.g., drafts, archive, old projects).
- **recruiter_domains:** Treat emails from these domains as ESCALATE-recruiter instead of spam.

If override conflicts with default rule, **override wins**. Example: sender is in `auto_archive_overrides.never-auto-archive` → REPLY-LATER or ESCALATE (do not auto-archive).

## Metrics + reporting

At end of email-triage session, report:

- Total unread: N
- Classified reply-now: N (with count of HITL drafts queued)
- Classified reply-later: N (stashed in triage-<date>.md)
- Auto-archived: N
- Escalated to operator: N (listed in Action-required)

Example:
```
Emails scanned: 47
- reply-now (HITL queue): 3
- reply-later (triage stash): 8
- auto-archived: 31
- escalate-review: 5
```
