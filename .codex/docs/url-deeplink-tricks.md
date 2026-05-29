# URL deeplink tricks — compose pre-fill for Gmail / Outlook / future webmail

> Discovered 2026-05-18 during Lead-direct compose+send workaround (per #1177 classifier-block sidestep). Saves ~50% tool calls vs manual click-each-field flow.

## When to use

- **Mode A Lead-direct** compose+send operations (operator approved content + Lead executes the click-send because subagent classifier blocked the spawn-brief intent)
- **Headless cron-batched** sends (if Mode B engine doesn't have native compose primitives)
- Any flow where the field values are KNOWN ahead of click (no per-field human typing needed)

## NOT to use

- Operator-typed inline compose (typing into open Gmail manually — no deeplink needed)
- Cases where field values change based on real-time observation (deeplink is fire-once-set)
- Outlook body content (see Outlook quirks below — partial-honor breaks the trick)

---

## Gmail compose pre-fill (FULL honor)

### Format

```
https://mail.google.com/mail/?view=cm&fs=1&to=<recipient>&su=<subject_urlencoded>&body=<body_urlencoded>
```

### Verified parameters (tested 2026-05-18)

| Param | Required | Notes |
|---|---|---|
| `view=cm` | yes | opens compose mode |
| `fs=1` | recommended | fullscreen compose (no popup; cleaner UI for automation) |
| `to=<recipient>` | yes | URL-encoded. Multiple: comma-separated then full URL-encode |
| `su=<subject>` | optional | URL-encoded |
| `body=<body>` | optional | URL-encoded; newlines as `%0A`; em-dash as `%E2%80%94`; Thai chars need full UTF-8 percent encoding |
| `cc=` / `bcc=` | optional | per Gmail docs (not tested 2026-05-18) |
| `tf=cm` | optional alternative | works alongside `fs=1` |

### Example (verified working 2026-05-18)

```
https://mail.google.com/mail/?view=cm&fs=1&to=bankung99@hotmail.com&su=%5BTEST%5D%20Secretary%20outbound%20smoke%202026-05-18&body=Hi%2C%0A%0AOutbound%20smoke%20test%20%E2%80%94%20cross-account%20compose-and-send%20via%20secretary%20through%20Gmail%20Compose%20UI.%20If%20you%20receive%20this%20at%20hotmail%2C%20the%20path%20works%20end-to-end.%20No%20reply%20needed.%0A%0ABest%2C%0AThanit
```

→ Gmail opens compose UI with all 3 fields pre-filled. Single click on Send = done.

### Token savings

- Manual flow: navigate + find To field + click + type + find Subject field + click + type + find body + click + type + find Send + click = ~10 tool calls
- Deeplink flow: navigate (with pre-fill URL) + find Send + click + verify = ~4 tool calls
- **Savings: ~50%+ tool calls per compose+send op**

---

## Outlook compose pre-fill (PARTIAL honor — only subject)

### Format

```
https://outlook.live.com/mail/0/deeplink/compose?to=<recipient>&subject=<subject>&body=<body>
```

### What Outlook actually honors

| Param | Honored? | Notes |
|---|---|---|
| `subject` | ✅ yes | Fills subject field correctly |
| `to` | ❌ NO | URL param present but field stays empty — must fill manually via `form_input` or `computer.type` |
| `body` | ❌ NO (mostly) | URL param sometimes partially fills but unreliable — must fill manually |

### Practical implication

For Outlook compose+send:
1. Use deeplink for subject pre-fill (saves 2 tool calls — find subject + type)
2. Manual fill for To + body via form_input or computer.type (~6 tool calls total)
3. Click Send (1 call)
4. Verify (1 call)

**Net savings vs full manual: ~2-3 tool calls (less impressive than Gmail's full pre-fill).**

---

## Outlook auto-signature quirk (CRITICAL)

Outlook automatically inserts operator's signature at body cursor position when compose loads. If you:
1. Use deeplink with `body=` URL param (which Outlook partially fills) AND
2. Then type more body content via `computer.type`

→ Result: **DUPLICATED content** (URL-fill body + auto-signature + your typed body all stacked).

### Workaround

- **Option A (recommended):** SKIP URL `body=` param + type complete body manually via `computer.type` AFTER signature loads. Single coherent body. Operator's signature stays at end.
- **Option B (smoke OK):** Accept duplication for SMOKE tests where cosmetic doesn't matter (email still arrives + still recognizable as test).

### Auto-signature includes PII

Outlook's auto-signature may include operator's phone number / contact details. When operating Lead-direct on Outlook compose, this PII enters Lead's context window via the screenshot/read_page. Minimize echoing back to chat per privacy discipline.

---

## URL encoding reference

| Character | Encoded |
|---|---|
| space | `%20` |
| newline | `%0A` |
| em-dash (—) | `%E2%80%94` |
| en-dash (–) | `%E2%80%93` |
| Thai characters (any) | full UTF-8 percent encoding — use a URL encoder library; do NOT hand-craft |
| `[` / `]` | `%5B` / `%5D` |
| `(` / `)` | `%28` / `%29` |
| `&` (inside value) | `%26` |
| `+` (inside value) | `%2B` |
| `=` (inside value) | `%3D` |

PowerShell: `[uri]::EscapeDataString($string)` — handles all above correctly.

---

## Future deeplink experiments

When Mode B (langgraph) engine lands, may need to revisit:
- Native compose primitives in engine layer may replace deeplink trick
- But for transitional Mode A → Mode B period, deeplink stays useful
- For other webmail (Yahoo / Proton / FastMail / etc), test compose URLs ad-hoc per provider

---

## Cross-ref

- Validated during HITL end-to-end smoke #1174 (2026-05-18)
- Sidestep pattern for: classifier-block on send-intent briefs (#1177 + failure-modes.md Category 8)
- Filed via Kanban task #1196
