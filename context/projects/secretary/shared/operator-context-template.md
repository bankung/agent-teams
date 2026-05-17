# Operator context — template (copy + fill on disk)

> **DO NOT commit a filled version.** This file is the COMMITTED TEMPLATE (no PII). Operator copies to `context/projects/secretary/general/operator-context.md` (gitignored) and fills locally.
>
> Quick start:
> ```bash
> cp context/projects/secretary/shared/operator-context-template.md \
>    context/projects/secretary/general/operator-context.md
> # then edit general/operator-context.md (gitignored — safe to put PII)
> ```
>
> Lead reads `general/operator-context.md` at session start when operator types `secretary ครับ` and provides `using my saved context`. Per-session inline values still OVERRIDE file values on conflict.

---

# Personal context (LOCAL ONLY — gitignored)

> Fill what you want persisted. Leave blank what you'd rather type inline. Anything sensitive (salary, target companies) — your call.
> 
> Format: YAML-ish, but Lead is lenient — it'll read free-form keys and values too. Just keep top-level sections obvious.

## identity

```yaml
name: "<Full English name as on CV / LinkedIn>"
signature: "<how you sign off informally — e.g. 'Best,' / 'Cheers,' / first-name-only>"
email: "<primary email>"
phone: "<+66 ..., only if used for job forms>"
linkedin_url: "https://linkedin.com/in/<handle>"
linkedin_handle: "<handle>"
github_url: "https://github.com/<handle>"
resume_path: "<absolute path on this machine, e.g. C:\\Users\\banku\\Documents\\Personal\\cv\\latest.pdf>"
language_default: "Thai"   # or "English", "mixed"
```

## defaults_for_email_triage

```yaml
signature_style: "Best,\n<your-name>"
tone_for_unknowns: "formal-warm"   # or casual / crisp
priority_senders:
  # always reply_now regardless of subject
  - "<boss-email>"
  - "<key-client-domain>"
auto_archive_overrides:
  # senders to ALWAYS auto-archive even if not matching generic patterns
  - "<ex-vendor>"
trusted_senders:
  # NOT to auto-archive even if matches newsletter pattern
  - "<specific newsletter you actually read>"
skip_folders:
  # Gmail labels / folders to ignore entirely
  - "Promotions"
  - "Personal Finance"
read_dont_process:
  # senders secretary should never classify (sensitive personal)
  - "<family member>"
  - "<bank>"
mentor_friends_casual:
  # known relationships → reply tone = casual
  - "<friend-domain>"
```

## defaults_for_job_apply

```yaml
target_roles:
  # role titles + synonyms; secretary uses for filter + scoring
  - "<role 1>"
  - "<role 2>"
acceptable_roles:
  # roles you'd consider but didn't actively target
  - "<role>"
anti_titles:
  # auto-skip regardless of other matches
  - "Junior"
  - "Intern"
  - "QA-only"

must_have_skills:
  # name + weight (1-25)
  - { name: "<skill>", weight: 20 }
  - { name: "<skill>", weight: 15 }
nice_to_have_skills:
  - { name: "<skill>", weight: 5 }

salary_floor_thb: 0           # monthly THB; secretary auto-skips below
salary_target_thb: 0          # monthly THB; secretary boosts score above
salary_currency_conversions:  # only if applying outside TH
  USD_monthly_floor: 0
  SGD_monthly_floor: 0

preferred_locations:
  - "Remote"
  - "Bangkok"
acceptable_locations:
  - "<city>"
unacceptable_locations:
  - "<city>"
time_zone_overlap_with: "UTC+7"
min_hours_overlap: 4

preferred_stages:
  - "Series A-C startup"
avoided_stages:
  - "Pre-seed"
  - "Enterprise IT services"
blacklist_companies:
  # auto-skip regardless of score
  - "<name>"

work_authorization:
  citizenship: "<country>"
  visa_status: "<if applying abroad>"

per_run_caps:
  listings_reviewed: 20
  applications_proposed: 5
  applications_submitted: 3   # anti-spam — keep low

sources:
  # YOUR filtered search URLs; secretary navigates these via Chrome MCP
  jobsdb_url: ""
  linkedin_url: ""
  # add additional sources as needed
```

## defaults_for_linkedin

```yaml
audience:
  - "<your primary audience>"
  - "<secondary audience>"
audience_NOT_for:
  - "AI hustle-bros"
  - "<other groups you don't want to attract>"

operator_themes:
  # 3-5 themes you want to be associated with
  - "<theme 1>"
  - "<theme 2>"
  - "<theme 3>"

anti_themes:
  # NEVER draft on these regardless of how trending
  - "politics / nationalism"
  - "religion / spirituality"
  - "salary / financial advice"
  - "<your specific avoid list>"

operator_rss_feeds:
  # domain-specific feeds Lead uses for topic discovery
  - "<URL>"
  - "<URL>"

operator_newsletter_subscriptions:
  - "<name>"

stance_defaults:
  default_stance: "personal-experience"
  # alternatives: "neutral-summary" / "contrarian-but-respectful" / "hot-take"
```

## defaults_for_daily_digest

```yaml
delivery_window: "18:00 ICT"   # when to surface end-of-day digest if Lead auto-prompts
include_sections:
  - "action_required"
  - "completed"
  - "aging"
  - "budget"
  - "suggested_focus"
collapse_sections_if_empty: true
mobile_format: true             # Lead renders compact (≤500 words) for mobile reading
```

## escalation_contacts

```yaml
# If secretary halts on an unrecoverable failure mid-workflow, who to surface to first
primary: "<your-email-or-phone>"
secondary: "<backup contact>"
```

## test_mode (optional)

```yaml
# Use during first few sessions to keep blast radius small
dry_run_all_external_actions: false   # if true, secretary drafts but NEVER submits
require_hitl_on_auto_archive: false   # if true, even auto_archive pauses for approval
max_actions_per_session: 0            # 0 = no cap; >0 = hard limit
```

---

## What Lead does with this file

When operator types `secretary ครับ` + `using my saved context`:

1. Lead reads `context/projects/secretary/general/operator-context.md` (this file, filled by operator)
2. Lead validates required sections present per upcoming workflow
3. Lead extracts the relevant subset for the spawn brief (e.g., email-triage spawn doesn't need `defaults_for_job_apply`)
4. Lead passes to secretary as `operator_context` in spawn brief
5. **Inline session-time values still OVERRIDE file values on conflict** — e.g., operator says `propose 10 jobs today` while file says `applications_proposed: 5` → secretary uses 10 for this session only

## What Lead does NOT do with this file

- Does NOT commit edits to it (gitignored — confirmed via .gitignore: `context/projects/secretary/general`)
- Does NOT echo PII back to operator in chat unless asked (avoid screenshot leakage on mobile)
- Does NOT pass full file contents to subagent specialists outside `secretary` agent (need-to-know)
- Does NOT log file contents in commits / audit trail

## Recovery: file lost / corrupted

If the gitignored file goes missing, no harm — operator types `context: { ... }` inline at session start; secretary halts asking for missing fields one at a time if needed.

Optionally re-derive from this template:
```bash
cp context/projects/secretary/shared/operator-context-template.md \
   context/projects/secretary/general/operator-context.md
```
then refill.

## Field-naming tolerance

Lead's parser tolerates common variations:
- `target_roles` / `targetRoles` / `target-roles` — all read as same field
- Comments (`#` lines) OK
- Top-level keys can be omitted; Lead infers from nesting

If field naming is ambiguous, Lead asks operator at session start (one-time) and remembers for the session.
