# Using agent-teams burns less Claude quota — without you having to manage it

> What many users notice for real: doing research / coding / writing docs through
> agent-teams visibly **lowers the % of your Claude subscription quota used per
> 5-hour window / per week**. The good part — it comes from the system's design,
> **not something you have to budget tokens for by hand**.

---

## TL;DR

agent-teams lowers your quota burden three main ways — **all automatic:**
1. Heavy reading is offloaded into a throwaway sub-agent → your main session stays lean
2. Stable system prompts → you benefit from caching automatically
3. Context is scoped per project/role → no irrelevant baggage burning quota

You just **state the goal**; the system handles the rest — no counting tokens, no
manually clearing context.

---

## Side by side: plain Claude Code vs through agent-teams

> agent-teams **runs on top of Claude Code** — plain Claude Code "can do this too"
> if you manage it with discipline. What agent-teams adds is making the
> quota-efficient pattern the **default, automatic, reusable, and tracked** — so the
> burden lands on the system, not on you.

| Aspect | Plain Claude Code | Through agent-teams |
|---|---|---|
| Offloading heavy work to a sub-agent | Possible, but you must invoke + craft the prompt every time | The Lead decomposes + delegates for you, by default |
| Main-session context | Grows over a long chat if you're not careful | Stays flat — heavy work lives in sub-agents automatically |
| Reusing context / methodology | Start over, re-explain each session | Playbooks + Kanban + shared zones kept for reuse |
| Caching | Works if the prefix stays stable (long chats shift it) | Structured stable system prompts → more reliable cache |
| Context scoping | You manage it (risk of irrelevant baggage) | Auto-scoped per project/role |
| Cost/quota visibility | Read off the usage meter yourself | Per-session cost tracker + budget soft-warn |
| **User burden** | You need discipline to manage context/tokens | The system handles it — you focus on the work |
| **Effect on quota (long, read-heavy work)** | **Depends on user discipline** | **Lower by default** |

**A real measured number:** prompt-caching of the stable specialist context measured a
**~77.5% input-cost reduction** on a 10-iteration scenario (see README) — not just theory.

---

## What the system does automatically (= less burden on you)

### 1. The Lead offloads heavy work to specialists itself
You just say "research / fix this code / write this doc" → the Lead decomposes the
work and spawns specialists. The **heavy reading (files, data, big logs)** happens in
the sub-agent's context. **You don't decide what to offload** — it's the default behavior.

### 2. Context is isolated + discarded automatically
A sub-agent works in its own context window and **returns only a summary**. The heavy
material it read is not dragged back into the main session → **the main thread stays
slim**. You **don't have to clear context or worry about the chat bloating** — the
system discards the temporary context for you.

### 3. Caching comes with the structure
CLAUDE.md / team playbooks / agent definitions are **stable** prompts → cache-eligible.
Repeated work on the same project earns **cache-reads** (counted far lighter against
quota than fresh input) automatically. **No configuration needed.**

### 4. Context is scoped per project / role
Each project carries its own context (shared / role-state); unrelated projects don't
mix → no "irrelevant stuff" carried around burning quota for no reason.

### 5. Cost is visible without you doing the accounting
The system tracks token/cost per session and **soft-warns as you near budget** → you
get the usage picture without keeping a token ledger yourself.

---

## Why it really lowers quota (the mechanism, briefly)

| | Plain long chat | Through agent-teams |
|---|---|---|
| Heavy material (files/logs/source) | Lives in the main thread, re-sent **every turn** | Read **once** in a sub-agent, then discarded |
| Cumulative cost as the chat grows | Grows **quadratically** (worse the longer it runs) | Grows **linearly** (main thread stays flat) |
| Repeated prompts | Paid in full each time | Earns **cache-reads** |

Net effect: **long sessions consume quota noticeably slower.**

---

## Where you'll see it clearly (set expectations honestly)

- ✅ **Full benefit:** research, coding, writing docs/manuals — **long + read-heavy +
  delegatable** work
- ➖ **Little benefit:** short one-shot Q&A (too small to be worth the spawn overhead)

---

## Small habits that help (low effort)

1. **State the goal and let the Lead delegate** — don't drag heavy reading into the main thread
2. **New topic = new session** — don't let an old context blob linger and re-send every turn
3. **Work on the same project repeatedly** to land cache hits

> The system handles the rest — the goal is for you to focus on the *work*, not on saving tokens.

---

## Technical note (for honesty)

- What drops is the **% of subscription quota** (from offload + caching) — Claude's own
  real meter, trustworthy.
- The **total token count across the whole system** is not necessarily lower — the work
  still has a cost, it's just arranged more efficiently. So measure **% quota / cost ($)**,
  not raw token count.
- Don't use the token numbers an **agent prints about itself** as evidence (models
  estimate their own cost poorly) — rely on Claude's usage meter / the cost tracker only.
