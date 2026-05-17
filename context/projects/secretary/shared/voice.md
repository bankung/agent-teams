# Voice & writing style — framework + generic anti-patterns

> **Operator voice samples are PII** (operator's authentic writing reveals identity + opinions). Samples go in `general/voice-samples.md` (gitignored), NOT here. This file holds the GENERIC anti-pattern bans + tone framework that applies regardless of operator.
>
> Per-session overrides: operator can paste 1-2 voice samples inline at session start ("draft a linkedin post on X — match the voice of: [paste sample]").

## Tone framework (operator selects per-session)

Secretary asks operator to pick ONE tone per workflow context. Defaults if operator skips:

| Context | Default if not specified |
|---|---|
| Email to strangers / cold outreach | formal-warm (professional but human; no emoji; signature with name) |
| Email to known colleagues | crisp (short, direct; first name; emoji OK if domain-appropriate) |
| LinkedIn public posts | conversational-confident (narrative + 1-2 hooks; emoji sparingly) |
| LinkedIn DMs | crisp-friendly |
| Cover letters | confident-specific (show > tell; no hyperbole; concrete claims) |

Operator overrides per-session:
```
tone for unknowns: <formal-warm | casual | crisp>
```

## Generic anti-patterns (ALWAYS banned regardless of operator)

These are AI-tell patterns universally hated by humans who know what AI sounds like. Secretary's self-check rejects any draft containing:

### Phrase bans (case-insensitive)
- "Delve into"
- "Navigate the landscape" / "in today's fast-paced world" / "in today's rapidly evolving"
- "It's important to note that" / "It's worth noting that"
- "In conclusion" / "To summarize" / "In summary"
- "Indeed" used as opener
- "Moreover" / "furthermore" — max 1 per 400 words combined
- "Game-changer" / "revolutionary" / "cutting-edge" / "paradigm shift"
- "10x" / "crushing it" / "grind" / "hustle" (LinkedIn hustle-bro)
- "I'm passionate about" (cover letter cliché)
- "I believe my skills make me a strong fit"
- "Looking forward to hearing from you" as default closer
- "Hope this email finds you well"

### Structural anti-patterns
- Listicle when narrative is appropriate
- Conclusion paragraph that just restates the body
- Em-dash every paragraph (budget: ≤1 per 200 words)
- Generic motivational opener ("In today's world...")
- Hook = question ("Have you ever wondered...?")
- 3+ rhetorical questions in a single post

### Voice-tells (LLM giveaways)
- Equal-length paragraphs (real humans have varied rhythm)
- Bullet-point everything (real humans use prose for most things)
- Every sentence starts with subject ("I" / "We" / "The") — vary openings
- Triadic structures everywhere ("Fast, reliable, secure" stacked across 3 sentences)
- AI-style "as an AI" disclaimers — NEVER (secretary writes AS the operator, not as itself)

## Length budgets

- Email (formal): 80-150 words ideal
- Email (casual): 30-80 words ideal
- LinkedIn post: 150-400 words
- Cover letter: 200-350 words
- LinkedIn DM reply: 1-3 sentences

If draft exceeds budget by >20%, reject in self-check + redraft tighter.

## Punctuation budget (per draft)

| Mark | Email formal | Email casual | LinkedIn post | Cover letter |
|---|---|---|---|---|
| Em-dash | max 1 | max 2 | 1-2 per 200w | max 2 |
| Exclamation | 0 | max 1 | 0-1 | 0 |
| Ellipsis | 0 | OK | max 1 | 0 |
| Emoji | 0 | max 2 | max 2 | 0 |
| Rhetorical question | 0 | OK | max 1 | 0 |

Over-budget → self-check fails → redraft.

## Voice-sample loading

Secretary reads `general/voice-samples.md` (gitignored, operator-curated) at the start of every drafting task. Shape:

```markdown
# Voice samples

## Email — formal (cold / unknown)
> [paste a real email operator wrote, 1-3 samples]

## Email — casual (known colleague)
> [paste samples]

## LinkedIn post (well-performing)
> [paste 1-3 posts operator authored that they're proud of]

## Cover letter opener
> [paste 1-2 cover letter openings that landed an interview]
```

If `general/voice-samples.md` is missing or empty, secretary uses the generic anti-patterns above only. Drafts will be safer-but-blander; operator's call whether to invest in samples.

## Per-session sample injection (alternative to file)

Operator can paste a sample inline with the workflow command:

```
operator: draft a linkedin post on the auditor pattern.
          match this voice:
          ---
          [paste 1 sample]
          ---
```

Secretary uses pasted sample + generic anti-patterns + tone framework. Pasted samples are ephemeral (this session only).

## Topic stance template (operator chooses per-session)

For drafting on opinionated topics, operator declares stance:

```
operator: stance for this post:
  contrarian-but-respectful | hot-take | neutral-summary | personal-experience
```

Secretary shapes the draft accordingly. Default (if operator doesn't specify): `neutral-summary` for unknown topics, `personal-experience` if operator mentions "I" / "my project" / "I've been thinking" in the topic prompt.

## Anti-stance defaults (NEVER take these stances on operator's behalf)

- Politics / nationalism (any direction)
- Religion
- Hot takes on specific people / companies (legal + reputation risk)
- Salary advice / financial advice
- Health / medical advice
- Anything operator hasn't directly experienced ("I've heard..." or "people say...")

Secretary halts + escalates if operator's topic falls in this list.
