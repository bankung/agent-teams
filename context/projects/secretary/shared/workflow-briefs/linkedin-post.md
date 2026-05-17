# Workflow brief — LinkedIn post

> Spawn template for `secretary` agent. Lead reads this when operator says "draft a linkedin post on X" / "find topic ideas" / "post about Y" / similar.
>
> Lead spawn invocation:
> `Research / outline / draft LinkedIn post per context/projects/secretary/shared/linkedin-strategy.md and voice.md. Topic: {operator-provided OR propose 3 candidates}. HITL pause before post goes live. Log to general/linkedin-log-{YYYY-MM}.md.`

## Pre-flight (Lead checks)

- [ ] Lead extracted `operator_context` — recommended: `linkedin_handle` (sanity), `operator_themes` (3-5), `audience` OR `audience_NOT_for`, `stance_for_this_post` (optional); optional `operator_rss_feeds` for source augmentation
- [ ] `general/voice-samples.md` exists (operator's authored samples) OR operator pastes 1+ sample inline with the request
- [ ] Chrome MCP connected + LinkedIn logged-in
- [ ] Operator stated topic OR confirmed "propose 3 candidates"
- [ ] Today's date directory exists

If pre-flight fails → halt + report.

## Mode A — Topic provided by operator

1. **Read frameworks + operator_context + voice samples**:
   - `shared/linkedin-strategy.md` — generic framework (hooks, CTA conventions, content sources)
   - `shared/voice.md` — generic anti-patterns + tone framework
   - `operator_context` from spawn brief — themes / audience / stance
   - `general/voice-samples.md` (if exists) — operator's authored writing as voice exemplars
   - If voice samples missing both in file AND spawn brief → drafts will be safer-but-blander; flag in HITL
2. **Research the topic** (≤15 min):
   - `WebSearch` / `firecrawl-search` for recent angles
   - Read 2-3 reference articles, extract 1 specific insight from each
   - Look up any technical claims operator might make (don't hallucinate facts)
3. **Outline** (3-5 bullet points) in `_scratch/linkedin-outline-{slug}.md`
4. **Draft** in `general/{YYYY-MM-DD}/linkedin-draft-{slug}.md`:
   - Format per `linkedin-strategy.md` length + format mix
   - Voice per `voice.md` (no AI-tells, anti-patterns checked)
   - Hook per voice.md opener pattern (specific observation, NEVER a question)
   - Body: per outline
   - Soft CTA per voice.md preference
   - Hashtags: 2-4 lowercase per linkedin-strategy.md
5. **Self-check** (mandatory before HITL):
   - [ ] No banned phrases from voice.md AVOID list
   - [ ] Em-dash count ≤ length-based budget (1 per 200 words)
   - [ ] Length within linkedin-strategy.md target
   - [ ] No generic conclusion that restates the body
   - [ ] Hook is specific (not generic motivational)
6. **HITL pause**:
   ```
   question: "Post this LinkedIn draft as-is? ({N} words on '{topic_short}')"
   options: ["approve_post", "edit_draft", "save_for_later", "skip"]
   ```
7. **On approve_post**:
   - Chrome MCP → navigate LinkedIn home → click "Start a post" / create new post
   - Paste draft via `form_input` / `type`
   - Verify preview (screenshot)
   - HITL pause 2nd time: "Final preview looks correct? Click Post?"
     - Options: `["confirm_post", "abort"]`
     - default_answer: "abort"
   - On confirm → click Post → capture confirmation screenshot
   - Log to `general/linkedin-log-{YYYY-MM}.md` with URL
8. **On edit_draft**: operator provides edits → re-draft → re-HITL
9. **On save_for_later**: stash in `general/linkedin-drafts-queue.md` with topic + date saved
10. **On skip**: log + delete draft

## Mode B — Operator asked "find topic ideas"

1. **Read** `linkedin-strategy.md` themes + recent content sources
2. **Scan content discovery sources** (last 48h):
   - RSS feeds from linkedin-strategy.md
   - WebSearch for theme keywords
   - Operator's "Recent topics" section in linkedin-strategy.md
3. **Filter** to operator's themes (NOT anti-themes)
4. **Score 3 candidates** with operator's specific angle:
   - Title (10-15 words)
   - Angle (1 sentence: what's operator's specific take)
   - Theme it fits
   - Source(s) for backup
5. **Surface to Lead** (no HITL — operator picks):
   ```
   Topic candidates for next LinkedIn post:
   1. "Auditor pattern in LangGraph — why retry-on-self beats retry-on-timeout"
      Angle: contrarian take — most agent frameworks default to timeout retries; auditor classification gives smarter recovery
      Theme: AI agent engineering
      Sources: langgraph 1.x docs, operator's own #952 work
   2. "..."
   3. "..."
   ```
6. **Operator picks 1** → re-enter Mode A with that topic

## Quality gate (drafting)

Before EVERY HITL pause for `approve_post`, secretary must verify:

- [ ] Voice.md anti-pattern scan clean
- [ ] No mention of operator's employer / colleagues without explicit permission marker
- [ ] No competitor mentioned negatively (legal risk)
- [ ] No salary / financial specifics
- [ ] No nationalism / politics / religion
- [ ] No content from anti-themes
- [ ] Source citations included (if facts claimed)
- [ ] Length within target
- [ ] Reads as operator's voice (compare against sample in voice.md)

If 3+ items fail → halt + escalate "draft consistently fails quality gate; topic may not fit voice or operator needs to refine criteria".

## Failure modes

- Topic requested falls under anti-themes → halt + ask operator to confirm override
- Research surfaces zero usable sources → halt + ask operator for source guidance
- Draft fails self-check 2x → halt + escalate
- LinkedIn paste-area not found → halt + report (UI change)
- LinkedIn shows "post limit reached" → halt + report

## Per-run output

`general/{YYYY-MM-DD}/linkedin-summary.md`:
```markdown
# LinkedIn session — {YYYY-MM-DD HH:MM}

- Topic: {topic} (operator-provided | proposed)
- Sources researched: N
- Draft length: N words
- Self-check: pass / fail (details)
- Status: posted / pending_approval / saved_for_later / skipped
- URL (if posted): https://...
```

## Operator-facing summary format

```
✍️ LinkedIn draft ready

Topic: "Auditor pattern in LangGraph"
Length: 320 words
Voice check: ✅ passed (no AI-tells, 1 em-dash, narrative format)
Hook: "Most agent frameworks treat failure as a timeout — auditor flips it."
Sources: 2 (langgraph docs, operator's #952 commit)

📝 Draft: context/projects/secretary/general/2026-05-17/linkedin-draft-auditor.md

Approve to post, edit, save for later, or skip?
```

## Tuning hooks

- **Themes / anti-themes**: edit `linkedin-strategy.md`
- **Length / format**: edit `linkedin-strategy.md` "Post format preferences"
- **Sources**: edit `linkedin-strategy.md` "Content discovery sources"
- **Voice**: edit `voice.md` (especially "Voice samples" section)
