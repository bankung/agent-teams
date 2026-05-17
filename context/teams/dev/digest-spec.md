# Daily digest content spec (dev lead)

> **Scope:** cross-project methodology — every `lead='dev'` project that opts into the digest consumes this shape. Lead is the only writer of this file.
> **Origin:** Kanban #1009 (P3 doc) — locks the schema before #958 (delivery) and #1011 (HITL nudge) light up.

## Purpose

Open the digest, in 30 seconds answer:
1. **What got done** since last check-in?
2. **What's stuck** and needs unblocking?
3. **What needs my decision today?**

If a section answers none of these, it does not belong in the digest. Push the rest into the dashboard / Kanban list.

## Hard constraint — one mobile viewport

The whole digest MUST fit ONE scroll on 375px-wide viewport (iPhone 13 mini baseline). Anything more = noise. Sections that overflow are TRUNCATED with a `[N more →]` deep link.

Render budget per section (mobile HTML):
| Section | Max card height (px) | Max items |
|---|---|---|
| 0 — header | 56 | 1 |
| 1 — Completed 24h | 120 | counter + 3 projects |
| 2 — Stuck | 140 | 3 (oldest first) |
| 3 — Inbox | 200 | 5 (oldest first) |
| 4 — Budget | 100 | per project ≤4 visible |
| 5 — Next action | 80 | 1 |
| Footer | 32 | deep link to dashboard |

Total ≈ 730px content + 60px chrome = 790px. Comfortably under one scroll on a 700px-visible viewport (after browser chrome). On smaller viewports (320px) the cards stack vertically and the user gets a fractional second scroll — acceptable.

## Schema (top level)

```json
{
  "generated_at": "2026-05-18T08:00:00+07:00",
  "operator_timezone": "Asia/Bangkok",
  "window": {"from": "2026-05-17T08:00:00+07:00", "to": "2026-05-18T08:00:00+07:00"},
  "sections": {
    "completed": {...},
    "stuck": {...},
    "inbox": {...},
    "budget": {...},
    "next_action": {...}
  },
  "deep_links": {
    "dashboard": "https://<host>/",
    "inbox": "https://<host>/inbox"
  }
}
```

The renderer (HTML + text fallback) consumes this single JSON object. Email-side rendering is out of scope here — covered by #958.

---

## Section 1 — Completed in last 24h

**Question answered:** "what got done?"

```json
"completed": {
  "total_count": 7,
  "by_project": [
    {"project_id": 1, "project_name": "agent-teams", "count": 4},
    {"project_id": 599, "project_name": "secretary", "count": 2},
    {"project_id": 12, "project_name": "novel-drift", "count": 1}
  ]
}
```

**Render rules:**
- One-line summary: `✓ 7 tasks done · agent-teams 4 · secretary 2 · novel-drift 1`
- If `total_count == 0`: render `— No tasks closed in the last 24h.` (no project breakdown)
- Project breakdown TRUNCATED to top 3 by count (descending); 4th+ folded into `+ N more`
- Tap on a project chip → deep link to `/<project>?period=24h&status=done`

**Data source:** `tasks` table — `process_status = 5 AND completed_at >= window.from`.

---

## Section 2 — Currently stuck

**Question answered:** "what's stuck and why?"

Definition of "stuck":
- `process_status = 4 (BLOCKED)` AND `updated_at` older than 24h, OR
- `process_status = 3 (REVIEW)` AND `updated_at` older than 12h.

The REVIEW threshold is tighter because REVIEW is an active state — a human is supposed to act on it; 12h means it slipped.

```json
"stuck": {
  "total_count": 4,
  "items": [
    {
      "task_id": 1042,
      "project_id": 1,
      "project_name": "agent-teams",
      "title": "Refactor approval evaluator for cross-container",
      "status": "blocked",
      "blocked_reason": "Waits #957 contract decision",
      "age_hours": 38,
      "deep_link": "https://<host>/projects/1/tasks/1042"
    }
  ]
}
```

**Render rules:**
- Max 3 items (oldest first by `updated_at`)
- Each item: 1 line title (truncated to 60 chars) + 1 line status badge + 1 line reason (truncated to 80 chars)
- Status badge: `BLOCKED 38h` or `REVIEW 14h` — color: orange if 12-24h, red if >24h
- If `blocked_reason` is null: render `— no reason given` in italics (signal to the operator that the blocker stamp is missing)
- Tap on item → deep link to task detail
- If `total_count > 3`: footer chip `+ N more stuck →` → deep link to `/?status=blocked,review&sort=oldest`
- If `total_count == 0`: section collapses to single line `✓ Nothing stuck.`

**Data source:**
- BLOCKED: `process_status=4 AND updated_at < now() - interval '24 hours'`
- REVIEW: `process_status=3 AND updated_at < now() - interval '12 hours'`
- `blocked_reason`: derived from `status_change_reason` of the most recent history row where `to_status=4`. NULL if not stamped.

---

## Section 3 — Your inbox

**Question answered:** "what needs my decision today?"

Inbox = open `interaction_kind in ('question', 'decision')` tasks that are NOT auto-policied (i.e., still need a human).

```json
"inbox": {
  "total_count": 5,
  "items": [
    {
      "task_id": 998,
      "project_id": 599,
      "project_name": "secretary",
      "title": "Approve LinkedIn post draft (voice match: 8.5/10)",
      "interaction_kind": "decision",
      "age_hours": 5,
      "quick_actions": [
        {"action": "approve", "label": "✓ Approve", "url": "https://<host>/api/tasks/998/decide?action=approve"},
        {"action": "reject", "label": "✗ Reject", "url": "https://<host>/api/tasks/998/decide?action=reject"}
      ],
      "deep_link": "https://<host>/projects/599/tasks/998"
    }
  ]
}
```

**Render rules:**
- Max 5 items (oldest first)
- Each item: 1 line title (truncated 50 chars) + 1 line inline action buttons
- `quick_actions` rendered as inline `<a>` buttons (HTML) or `[✓ Approve] [✗ Reject]` brackets (text)
- Only show quick actions for the 2 most-common shapes (`approve/reject` decisions; `yes/no` questions). Complex multi-option decisions render `[View →]` only.
- Tap title → deep link to task detail; tap action button → triggers `POST /api/tasks/<id>/decide` (handled per #1001)
- If `total_count > 5`: footer chip `+ N more in inbox →` → deep link to `/inbox`
- If `total_count == 0`: section collapses to single line `✓ Inbox zero.`

**Data source:** `tasks` table — `interaction_kind IN ('question', 'decision') AND process_status NOT IN (5, 6, 7) AND blocked_by IS NULL`. Auto-policied items are excluded by the approval evaluator path (already resolved before this query).

**Quick actions safety:** the action URL embeds a signed one-shot token (TTL = digest validity, e.g. 4h). After expiry the link 410s — forces the operator to use the UI for a fresh decision. (Design carried over from #1001; this spec just consumes it.)

---

## Section 4 — Budget

**Question answered:** "am I bleeding money?"

```json
"budget": {
  "items": [
    {
      "project_id": 1,
      "project_name": "agent-teams",
      "today_spend_usd": 12.40,
      "today_cap_usd": 15.00,
      "projection_usd": 18.50,
      "percent_of_cap": 0.83,
      "flag": "warn"
    }
  ]
}
```

**Render rules:**
- One row per project, sorted by `percent_of_cap` descending (worst first)
- Each row: project name | `$X.XX / $Y.YY` (today / cap) | progress bar
- Color:
  - `flag = "ok"` (<80%): green bar
  - `flag = "warn"` (80-100%): orange bar, flag `⚠`
  - `flag = "over"` (>100%): red bar, flag `🔴` + projection delta
- Projection shown only if `flag != "ok"`. Format: `→ proj $18.50 (+23%)`
- Max 4 rows visible inline. 5th+ folded into `+ N more →` deep link to `/billing`
- If all projects under 50% cap: section collapses to single line `✓ Budget healthy (all <50%).`
- If a project has NO cap configured: omit from this section (don't render `$X / $-`)

**Data source:** `GET /api/projects/{id}/pl?period=daily` (existing endpoint). The digest builder fans out one call per project that has `cost_today > 0` for the window, runs in parallel with a 2s timeout per call. Failed calls render with `?` placeholder rather than missing the row.

**Threshold rationale:** the 80% warn threshold mirrors #951 (cost soft-warn). Same threshold = consistent operator mental model.

---

## Section 5 — Suggested next action

**Question answered:** "if I have 10 minutes, what should I do?"

```json
"next_action": {
  "task_id": 998,
  "project_id": 599,
  "project_name": "secretary",
  "title": "Approve LinkedIn post draft",
  "reason": "oldest in inbox (5h) and you've approved 3/3 last week",
  "score": 0.84,
  "deep_link": "https://<host>/projects/599/tasks/998"
}
```

**Render rules:**
- ONE recommendation only. Picking a top-N defeats the purpose ("if I had to do one thing").
- One line title + one line reason (max 80 chars) + deep link
- If `next_action` is null (no actionable items): render `— Nothing pressing. Take a break.`

**Data source:** `GET /api/user/next-action?limit=1` (delivered by Kanban #1010). The digest builder consumes the top result. Ranking weights live in #1010; this spec just consumes the answer.

---

## Plain text fallback

Some delivery channels (terminal-based email clients, accessibility readers, plain-text MTAs) won't render HTML. The plain-text fallback drops images / buttons but preserves the same section order:

```
agent-teams daily digest — 2026-05-18 08:00 ICT
=================================================

✓ Completed (24h): 7 tasks
  · agent-teams 4 · secretary 2 · novel-drift 1

⚠ Stuck (4):
  · [BLOCKED 38h] #1042 Refactor approval evaluator
    Waits #957 contract decision
    https://<host>/projects/1/tasks/1042
  · [REVIEW 14h] #1078 Review PR for budget endpoint
    https://<host>/projects/1/tasks/1078
  · ... + 2 more — https://<host>/?status=blocked,review

📥 Inbox (5):
  · #998 secretary — Approve LinkedIn post draft (5h)
    [✓ Approve] https://<host>/api/tasks/998/decide?action=approve
    [✗ Reject]  https://<host>/api/tasks/998/decide?action=reject
  · ...

💰 Budget:
  · agent-teams   $12.40 / $15.00  (83% — proj $18.50) ⚠
  · secretary     $ 3.10 / $10.00  (31%)
  · novel-drift   $ 0.40 / $ 5.00  ( 8%)

→ Next action: Approve LinkedIn post draft (#998, secretary)
  Reason: oldest in inbox (5h)
  https://<host>/projects/599/tasks/998

Dashboard: https://<host>/
```

**Plain text constraints:**
- ASCII-safe symbols (✓ ⚠ 📥 💰 → are all single-codepoint Unicode; safe in MIME `Content-Type: text/plain; charset=utf-8`). If a downstream channel rejects emoji (e.g. SMS via Twilio), substitute with `[OK]`, `[STUCK]`, `[INBOX]`, `[BUDGET]`, `->`.
- Line length capped at 78 chars (RFC 5322).
- Deep links always full URL (no `<a>` markup possible).

---

## Empty-state behaviour (one-liners)

Each section has a degenerate render when empty so the operator doesn't waste eyes scanning:

| Section | Empty-state line |
|---|---|
| Completed | `— No tasks closed in the last 24h.` |
| Stuck | `✓ Nothing stuck.` |
| Inbox | `✓ Inbox zero.` |
| Budget | `✓ Budget healthy (all <50%).` |
| Next action | `— Nothing pressing. Take a break.` |

If ALL 5 sections are empty (idle weekend), the digest still renders — just very short. Operator can configure "skip digest if completely idle" as a project setting later (out of scope for this spec).

---

## Section order rationale

The order — Completed → Stuck → Inbox → Budget → Next action — is deliberate:

1. **Completed first:** positive reinforcement. Lead with the wins.
2. **Stuck second:** highest cognitive priority. Things that need un-blocking decay fastest.
3. **Inbox third:** the bulk of operator's work. Comes after Stuck because Stuck is rarer + more urgent.
4. **Budget fourth:** financial guard-rail. Should be a glance, not a deep-dive.
5. **Next action last:** call-to-action. Last thing the operator sees before closing the email.

Do NOT reorder without operator feedback. The order encodes 3 design decisions:
- Lead with positives, not negatives (avoid email-anxiety)
- Action ask is LAST (memorable closing CTA)
- Budget is between obligations (inbox) and motivation (next action) — caught in passing, doesn't overshadow

---

## Localization

- Operator timezone: `operator_timezone` field drives `generated_at` + `window.*` formatting and "age" computations ("38h" = computed in operator tz).
- Section labels in the operator's preferred language (per project setting `digest_language`, defaults to project's `lead` language: `en` for dev-lead projects).
- Numbers / currency formatted per locale (e.g., `$12.40` US, `12,40 €` EU).

Out of scope for v1: bidirectional text support (RTL). Defer until first RTL operator surfaces.

---

## Versioning + change protocol

Spec version: **v1** (this doc).

Breaking changes (renaming top-level keys, removing sections, changing section order) require:
1. Bump version to `v2` and stamp in `digest_spec_version` JSON field
2. Renderer code dual-supports both versions for one release cycle
3. Note in `context/projects/agent-teams/shared/decisions.md`

Additive changes (new optional fields, new section appended at end) are version-compatible — no bump needed.

---

## Cross-references

- **#958** Daily digest push delivery — consumes this JSON shape; renders HTML + text; sends via Web Push (#955) + optional email
- **#1010** Next-action recommender API — produces the Section 5 payload
- **#1001** Push notification quick-actions — produces the inbox quick_actions URL shape
- **#955** Web Push notifications — delivery substrate for the digest itself
- **#951** Cost soft-warn 80% threshold — re-used in Section 4 budget flag
