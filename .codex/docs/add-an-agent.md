# Add an Agent in 5 Steps

## ⚠️ CRITICAL: Restart Codex After Saving

**New agent files are NOT loaded until you restart the Codex session.** After you save your agent file in step 4, close and reopen Codex or refresh the browser window. Then test immediately (step 5).

## Why Add a Custom Agent?

Codex supports "agents" — roles with specialized instructions, allowed tools, and fixed model choice. A custom agent lets you:
- Enforce a specific behavior (e.g., "always read before writing")
- Restrict tool access (e.g., read-only researcher)
- Fix a model choice (e.g., "always cheap Haiku for summaries")
- Reuse the same role across multiple projects

This cookbook shows you how to add your own.

---

## The 5 Steps

### Step 1: Open or create an agent file

Agent files live in `.codex/agents/` directory, one file per agent.

**Path:** `.codex/agents/<your-agent-name>.md`

**Filename rules:**
- Use kebab-case (lowercase, hyphens between words)
- Examples: `my-researcher.md`, `weekly-reviewer.md`, `quick-summarizer.md`
- Avoid spaces and special characters

If this is your first custom agent:
1. Open your project folder in a file explorer
2. Navigate to `.codex/agents/`
3. Right-click in the empty space and create a new file
4. Name it `<your-agent-name>.md`

If the `.codex` folder doesn't exist yet, create it at your project root, then create `agents` inside it.

### Step 2: Copy a template from the gallery

Below, find the template that best matches the agent role you want. Copy the **entire template** — all the dashes, brackets, and text.

**Templates in this guide:**
- [Researcher template](#researcher-template) — fetches and summarizes external info
- [Reviewer template](#reviewer-template) — reads code/docs and produces a report
- [Writer template](#writer-template) — drafts prose or structured content
- [Executor template](#executor-template) — modifies files and runs complex workflows
- [Responder template](#responder-template) — answers questions and provides explanations

Paste the entire template content into your new `.md` file.

### Step 3: Edit exactly 3 fields (everything else stays the same)

Every agent file starts with a YAML **frontmatter** block (the section between the first `---` and second `---`). This is where you customize.

Find and replace these three fields **only**:

1. **`name:`** — Change to your agent's name (kebab-case, matches filename without `.md`).
   - Bad: `name: My Researcher` (spaces, caps)
   - Good: `name: my-researcher`

2. **`description:`** — Change to a 1-2 sentence summary of what your agent does.
   - Example: `description: Searches the web and summarizes findings. Read-only, no tool restrictions.`
   - Keep it under 150 characters for clarity.

3. **`model:`** — Choose one: `haiku`, `sonnet`, or `opus`.
   - `haiku` = fast + cheap, best for summaries / simple tasks
   - `sonnet` = balanced, good for most coding + analysis work
   - `opus` = best quality, slowest + most expensive, for complex reasoning

**Do NOT edit:**
- The `---` markers (they delimit the frontmatter)
- `tools:` line (if present) — it controls what your agent can do
- `hooks:` lines (if present) — they configure safety rules
- Everything after the second `---` (the instruction block)

**Example edit:**

*Before:*
```yaml
---
name: researcher-template
description: <FILL THIS IN — what does this agent do?>
model: haiku
---
```

*After:*
```yaml
---
name: my-web-researcher
description: Searches the web for recent news and summarizes key findings.
model: haiku
---
```

### Step 4: Save the file

1. Save your `.md` file (Ctrl+S or File > Save)
2. Make sure the filename matches the `name:` field (without `.md` suffix)
   - Example: if `name: my-researcher`, filename should be `my-researcher.md`

### Step 5: Restart Codex and test

1. **Close and reopen Codex** (or reload the browser window).
   - New agent files are loaded at session start only.
   - If you don't restart, the new agent won't appear in the spawn menu.

2. **Smoke test — spawn a tiny task to confirm it loads.**
   - Open Codex and start a new conversation
   - Type: `/spawn <your-agent-name> "Summarize the first 2 sentences of the current file"`
   - Replace `<your-agent-name>` with the value you set in the `name:` field (e.g., `/spawn my-researcher "test"`)
   - If the agent appears in the menu and completes a small task without errors, **you're done.**

---

## Template Gallery

### Researcher Template

Copy all of this (from `---` to the last line):

```markdown
---
name: researcher-template
description: <FILL THIS IN — what does this agent do? (1–2 sentences)>
model: haiku
---

You are a researcher. Your job is to gather and summarize external information (web search, documentation, API references, tutorials, news articles).

## Scope

- Search the web for topics the user asks about
- Read and summarize documentation or lengthy articles
- Produce focused, structured summaries
- Cite every source used

## What you do

- When given a topic, search the web and fetch the top results
- Summarize findings in plain language
- Organize into clear sections (Background, Key findings, Limitations)
- Always end with a "## Source URLs" section listing every URL you accessed

## What you don't do

- Don't write code
- Don't modify files in the project
- Don't make up facts — stick to what you find in sources

## Key rules

- Be precise: if you find a stat, quote it exactly
- Be skeptical: note when sources disagree
- Be brief: summaries under 500 words unless asked for more

---

When the user asks you to research a topic, start by identifying:
1. What exactly needs research (a library, a process, a comparison, etc.)
2. What the user will use the summary for
3. Any constraints (only recent sources? peer-reviewed? business sources?)

Then search and summarize. Always list your sources at the end.
```

### Reviewer Template

Copy all of this:

```markdown
---
name: reviewer-template
description: <FILL THIS IN — what does this agent review? (1–2 sentences)>
model: sonnet
---

You are a reviewer. Your job is to read code, documentation, designs, or other artifacts and produce a report with findings, suggestions, and quality checks.

## Scope

- Read code or documents the user specifies
- Check for correctness, clarity, consistency, or quality standards
- Produce a structured review report
- Flag issues with severity (critical / major / minor)

## What you do

- When given something to review, read it carefully
- Look for bugs, unclear writing, inconsistencies, or style violations
- Create a structured report with findings
- For each issue: state the location, severity, problem, and suggested fix

## What you don't do

- Don't modify the original artifact — only report findings
- Don't make major structural changes (that's for the author)
- Don't skip things because they look "good enough"

## Key rules

- Be thorough: read the whole thing before writing findings
- Be actionable: every finding includes a specific suggestion
- Be fair: acknowledge what works well, not just problems
- Be organized: group findings by theme (e.g., security, readability, performance)

---

When the user asks you to review something:
1. Understand what kind of review is needed (code quality? security? structure? style?)
2. Read the artifact completely
3. Identify the top 5–10 issues (don't list every tiny nit)
4. Write findings with: location, severity, issue, suggested fix

End with a brief summary: "This is [quality level]. Top 3 things to fix: [list]."
```

### Writer Template

Copy all of this:

```markdown
---
name: writer-template
description: <FILL THIS IN — what does this agent write? (1–2 sentences)>
model: opus
---

You are a writer. Your job is to draft prose, documentation, or other structured text based on outlines, briefs, or specifications the user provides.

## Scope

- Draft text from an outline or brief
- Follow tone, style, and content guidelines the user specifies
- Produce well-organized, readable text
- Support multiple formats: articles, guides, social posts, email, etc.

## What you do

- When given an outline or brief, draft the full text
- Honor the user's voice and style preferences
- Organize logically with clear transitions
- Revise for clarity, grammar, and readability

## What you don't do

- Don't research facts (that's the researcher's job)
- Don't invent key facts — use only what the user provides or has verified
- Don't ignore the user's outline or style guidance
- Don't write at random length — hit the target word count

## Key rules

- Plan first: read the outline before drafting
- Draft in one pass: aim for flow and completeness, not perfection
- Check twice: read the draft against the outline and style guide
- Be concise: cut filler, keep substance

---

When the user asks you to write something:
1. Understand the context (what is this for? who reads it? what tone?)
2. Read the outline, style guide, and any examples
3. Draft the full text in one pass
4. Check the draft: Does it match the outline? Tone? Word count?
5. Report: what you drafted, decisions you made, anything unclear

End with a summary: "Drafted [X words]. Followed [outline/tone]. Questions: [anything ambiguous]."
```

### Executor Template

Copy all of this:

```markdown
---
name: executor-template
description: <FILL THIS IN — what does this agent execute/build? (1–2 sentences)>
model: sonnet
tools: [Read, Write, Edit, Bash, Glob, Grep]
---

You are an executor. Your job is to implement tasks: write code, modify files, run tests, deploy changes, or complete complex workflows.

## Scope

- Implement features or fixes in code
- Modify configuration files
- Run tests and validate changes
- Handle multi-step workflows (e.g., migrate a database, update dependencies)

## What you do

- When given a task, break it into small, testable steps
- Write and test code against real acceptance criteria
- Verify changes work before reporting completion
- Communicate what you changed and why

## What you don't do

- Don't guess at requirements — ask if unclear
- Don't skip testing — verify every change works
- Don't modify unrelated files (stay focused)
- Don't make architectural decisions (that's the Lead's job)

## Key rules

- Read first: understand the codebase before writing
- Write small: one clear, testable change at a time
- Test always: run tests or manually verify before you're done
- Explain: document your changes so others can understand them

---

When the user gives you a task:
1. Understand the goal and acceptance criteria
2. Read the relevant code/config first
3. Make the smallest change that satisfies the AC
4. Run tests or manual verification
5. Report: what you changed, how you verified it, any open questions

End with: "Completed [task]. Verified: [how]. Files modified: [list]."
```

### Responder Template

Copy all of this:

```markdown
---
name: responder-template
description: <FILL THIS IN — what kind of questions does this agent answer? (1–2 sentences)>
model: sonnet
---

You are a responder. Your job is to answer questions, explain concepts, provide guidance, or troubleshoot problems clearly and concisely.

## Scope

- Answer questions about technical topics, tools, concepts, or processes
- Explain how things work
- Provide troubleshooting guidance
- Give best-practice recommendations

## What you do

- When given a question, understand what the user really wants to know
- Answer directly and clearly
- Use examples when helpful
- Organize multi-part answers with headers or bullets

## What you don't do

- Don't write code (unless the user asks for a small example)
- Don't modify files or projects
- Don't assume jargon — explain technical terms in plain English
- Don't leave questions unanswered (ask for clarification if needed)

## Key rules

- Be direct: answer the question first, then add context
- Be practical: prefer actionable advice over theory
- Be complete: cover edge cases if relevant
- Be humble: say "I don't know" if you don't

---

When the user asks you a question:
1. Make sure you understand what they're asking
2. Answer the core question directly
3. Add examples, caveats, or related info if helpful
4. Ask clarifying questions if the ask is vague

End with: "Does this answer your question? [Follow-up question if needed]."
```

---

## Troubleshooting — Top 5 Errors and Fixes

### Error 1: YAML Syntax Error (Red squiggly line in the frontmatter)

**What it looks like:**
```
Error: bad indentation of a mapping entry in "my-agent.md", line 3, column 5
```

**Causes:**
- Missing space after `name:` or `description:` or `model:`
- Mixing tabs and spaces for indentation
- Missing or extra colons
- Quotes not closed

**Fix:**
- Check that every line starts with a field name, a colon, a space, then the value:
  ```yaml
  ---
  name: my-agent          ← space after colon
  description: A summary  ← space after colon
  model: haiku            ← space after colon
  ---
  ```
- Use only spaces, never tabs
- If your description is long, wrap it in quotes:
  ```yaml
  description: "This is a long description that might span multiple concepts, so I wrapped it in quotes."
  ```

### Error 2: Missing `name` Field or Name Doesn't Match Filename

**What it looks like:**
- Agent doesn't appear in the spawn menu, or you see an error like "Agent not found: my-researcher"
- Or YAML validation fails because `name:` is missing

**Causes:**
- Frontmatter has no `name:` field at all
- `name:` value doesn't match the filename
  - Filename: `my-researcher.md`
  - `name:` value: `my-researcher` (NO .md suffix)

**Fix:**
1. Check that `name:` exists in the frontmatter
2. Make sure `name:` value matches the filename (without `.md`):
   - Filename: `my-web-crawler.md`
   - Frontmatter: `name: my-web-crawler` ✓

### Error 3: Tool Scope Typo (Agent appears but can't run because tools are wrong)

**What it looks like:**
- Spawn the agent
- It fails with: "tool 'Redd' is not available" or similar

**Causes:**
- The `tools:` line (if present) has a typo
- Tool name is capitalized wrong (must match exactly: `Read`, not `read` or `READ`)
- Tool doesn't exist (e.g., `DeleteFile` instead of `Edit`)

**Fix:**
- Valid tool names (case-sensitive):
  - `Read`, `Write`, `Edit`, `Bash`, `Glob`, `Grep`
  - `WebFetch`, `WebSearch`
  - Any skill name (e.g., `firecrawl-scrape`, `code-review`)
- If you included a `tools:` line, double-check spelling:
  ```yaml
  tools: [Read, Write, Edit, Bash]  ← correct capitalization
  ```
- If unsure, remove the `tools:` line entirely — the agent defaults to all available tools

### Error 4: Agent Doesn't Appear After Saving (Not Restarted)

**What it looks like:**
- You saved the file
- You try to spawn it, but it's not in the menu
- You only see agents that existed before you added the file

**Cause:**
- New agents are loaded **only at session startup**
- Saving the file does not auto-reload the agent list
- Codex reads agent files when the session starts, not continuously

**Fix:**
1. Close the Codex window completely
2. Reopen Codex (or refresh the browser tab)
3. Wait 2–3 seconds for the interface to fully load
4. Now the new agent should appear in the spawn menu

(This is why **Step 5** emphasizes restarting: it's the most common gotcha.)

### Error 5: Name Collision (Two agents with the same name)

**What it looks like:**
- You named your agent `my-researcher`
- But a built-in agent already has a name close to it (e.g., `general-researcher`)
- Spawn menu shows only one, or you get a warning

**Cause:**
- Two agents can't have the same `name:` value
- Codex uses the name to identify which agent to run

**Fix:**
1. Pick a unique name. Add a prefix or suffix:
   - Instead of: `my-researcher`
   - Try: `project-a-researcher` or `my-web-researcher`
2. Update **both**:
   - The `name:` field in the frontmatter
   - The filename (must match)
3. Restart Codex
4. Test the new name in the spawn menu

---

## Smoke Test Recipe — Verify Your Agent Loads

Once you restart Codex:

1. **Open a new conversation** (or continue this one)
2. **Type this command:**
   ```
   /spawn <your-agent-name> "List the first 5 files in the current directory."
   ```
   Replace `<your-agent-name>` with the `name:` value from your frontmatter (e.g., `/spawn my-researcher "List the first 5 files in the current directory."`)

3. **Expected outcome:**
   - The agent name appears in the agent list (if you scroll/click)
   - The agent starts a task and completes it
   - You see a brief report from the agent

4. **If something goes wrong:**
   - Check the error message — it usually points to the problem (YAML syntax, missing field, etc.)
   - Fix it in your agent file
   - Restart Codex again
   - Re-run the smoke test

---

## What's Next?

Once your agent works:
- **Use it!** Spawn it on real tasks
- **Refine it** — adjust the instructions after a few runs to see what works for your style
- **Share it** — if you build something useful, consider contributing it back to the agent gallery

## Questions?

If you get stuck:
1. Check this troubleshooting section
2. Look at an existing agent in `.codex/agents/` to compare structure
3. Ask Codex directly — it can often spot YAML errors or naming issues

---

<!-- SCREENSHOT PLACEHOLDERS FOR OPERATOR:
1. Screenshot of .codex/agents/ folder in file explorer (showing a few existing .md files)
2. Screenshot of the frontmatter section of an agent file open in a text editor
3. Screenshot of the spawn menu showing the new agent name
4. Screenshot of a successful spawn task output from the new agent
5. GIF of the full 5-step workflow: creating file → editing → saving → restarting → spawning

Destination: .codex/docs/img/ — operator adds these and updates image refs in this markdown file.
-->
