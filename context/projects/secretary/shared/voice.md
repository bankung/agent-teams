# Voice: Drafting tone + anti-patterns

**Purpose:** Define the generic tone framework + common anti-patterns for all secretary-drafted content (email replies, cover letters, LinkedIn posts). Operator-specific voice examples (POV, jargon, personal phrases) arrive at runtime via `operator_context.stance_for_this_post` or Lead's spawn notes.

## General drafting rules

**Sentence length & clarity**
- Aim for 12–18 words per sentence average. Short sentences are faster to read and act on.
- Variant: one 20–25 word sentence OK per paragraph for rhythm, but never 3 in a row.
- Long-windedness kills intent. "I wanted to reach out regarding your recent inquiry" → "I got your message."

**Concrete over abstract**
- Show, don't theorize. "I led a migration that cut latency by 40%" beats "I have strong backend skills."
- Specificity = credibility. Use numbers, dates, company names (if public). Vague claims ("excellent communicator") signal AI-draft.

**No corporate jargon**
- Avoid: "delve into", "leverage", "synergy", "paradigm shift", "bandwidth", "circle back", "touch base", "at the end of the day"
- Use: "explore", "use", "collaboration", "change", "capacity", "follow up", "discuss", "ultimately"
- Test: Would you say this in a voice call? If not, it's jargon.

**Avoid LLM hedging**
- Strike: "it is worth noting that", "it should be noted", "as I mentioned earlier", "I believe", "I think", "in my opinion"
- Use: Make claims directly. "The team shipped 3 features in Q1" not "I believe we achieved three features..."
- Caveat: if genuinely uncertain, say "I'm not certain about X" — honest uncertainty is better than hedge-speak.

**Active voice, short auxiliaries**
- "We deployed the fix at 6pm" not "The fix was deployed by us at 6pm."
- "I revised the schema" not "I was required to revise the schema."
- Exception: "I was impressed" (passive OK for emotion) vs. "I were impressed by" (never this form).

**Avoid emoji in formal channels** (email, cover letter)
- LinkedIn post: emoji OK if operator's brand uses it (check `operator_context.stance_for_this_post`); otherwise skip.
- Email reply: no emoji unless operator explicitly says "casual tone with emoji" in spawn context.
- Cover letter: never emoji.

## Channel-specific overlays

### Email reply tone

**Opener:**
- To close contact (existing relationship): "Thanks for reaching out." / "Got your message."
- To unknown sender or cold outreach: "Thanks for the email." / "Hi [name], I appreciate you writing."

**Middle (respond to request):**
- Mirror the sender's tone level (formal recruiter → formal; casual friend → casual).
- If they asked a question, answer in 1–2 sentences, then ask your own if needed. Avoid monologue.
- If they sent an opportunity (job, collaboration), say what you need to decide. "I'd love to learn more about X — could you send [specific info]?"

**Closer:**
- "Looking forward to hearing from you." (formal)
- "Let me know what works." (neutral)
- "Chat soon!" (casual, friend-to-friend)
- **Avoid:** "Thanks in advance", "ASAP", "urgent" (unless truly time-critical; overuse trains inbox fatigue).

**Signature:**
- Operator's name + optional title (pulled from `profile.md`).
- No need to repeat email — Gmail shows it.
- Example: "Jane / Senior Backend Engineer"

### Cover letter tone

**Paragraph 1: Hook + role fit** (~3 sentences)
- Why you're writing (found the role, referred by contact, company's mission resonates).
- **One sentence showing you understand the role.** "Your team is scaling GraphQL adoption; I've led 2 migrations and can hit the ground running."
- Avoid: "I am excited to apply for the position of X" (passive, boring).

**Paragraph 2: Proof** (~4 sentences, 2–3 concrete achievements)
- Pattern: "When [situation], I [action that maps to role], resulting in [metric/outcome]."
- Example: "At Startup Y, I migrated from Postgres 10 → 14 with zero downtime, reducing query latency by 35% and unblocking 5 pending features."
- Connect explicitly to the role. "This mirrors your team's focus on infrastructure stability."

**Paragraph 3: Close + call-to-action** (~2 sentences)
- "I'm eager to discuss how my experience in [2–3 relevant areas] can contribute."
- "Happy to discuss schedule / jump on a call / send references — what works for you?"
- Avoid: "Sincerely", "Yours truly" — too formal. Use "Thanks," / "Best," / "Cheers,".

### LinkedIn post tone

**Opener: Hook** (1 sentence, question or bold claim)
- Example: "I spent 2 years rebuilding our payment system and made every mistake twice."
- Avoid: "Happy to share my thoughts on X" (weaselly), "Quick thought..." (undersells), "Unpopular opinion:" (edgelord).

**Body: 3–5 points or one extended narrative**
- If list: one sentence per point, max 10 words. Bold the insight; rest is context.
- If narrative: short anecdote → lesson → broader takeaway.
- Each point: action/fact → insight. "We split traffic 80/20 and hit parity in 1 week, proving feature flags beat big-bang deploys."

**Takeaway line** (1 sentence)
- Summarize the post's insight in one memorable sentence.
- "This is why incremental > all-or-nothing." / "Monitoring > guessing."

**CTA: Question or invitation** (1 sentence)
- "What's your team's secret to shipping fast?" — invites engagement.
- "Curious if you've faced this trade-off" — opens discussion.
- Avoid: "Feel free to reach out", "DM me", "Let's connect" (every post says this).

## Anti-patterns to catch (secretary self-check)

| Anti-pattern | Fix |
|---|---|
| "I am writing to inform you that..." | "Just wanted to share: ..." or skip preamble |
| "It should be noted that the aforementioned..." | Use clear pronouns. "That feature is now ready." |
| "Seeking to leverage our synergies..." | "Let's work together on X." |
| "At this juncture in time..." | "Now we can..." / "This lets us..." |
| "I would appreciate your consideration..." | "I'd love your feedback." |
| "As a senior engineer with deep expertise..." | Show don't tell. "I led 3 migrations..." |
| "Looking forward to the opportunity to..." | "Happy to [action]." |
| Multiple exclamation marks (!!!, !!!!) | One per email, max. Otherwise looks frantic. |
| All caps (URGENT, ASAP, MUST) | Lower case + context. "This ships Friday, so approval needed by EOD Wed." |
| "Just circling back on..." (3rd+ time) | Change tone: "I realize I've asked twice; final check — are we moving forward?" |

## Operator-specific voice overrides at runtime

If spawn brief includes `stance_for_this_post`, `operator_themes`, or explicit voice direction, those OVERRIDE the generic rules above for that session. Examples:

- `stance_for_this_post: "technical + witty, emoji OK"` → use puns, emoji, assume technical audience.
- `voice_direction: "more formal, fewer contractions"` → use "do not" not "don't", formal openers.
- Implied from context: reply to family friend → very casual; recruiter email → formal.

When override is ambiguous, ask Lead for clarification in the return report.
