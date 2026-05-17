# API contracts — secretary

> **N/A for this project.** Secretary doesn't own its own API surface — it consumes:
> - The agent-teams platform Kanban API (`http://localhost:8456/api/*`) via `X-Project-Id: 599` header
> - Browser-side authenticated services via Chrome MCP (Gmail / LinkedIn / JobsDB / etc.)
> - Public web via `WebFetch` / `firecrawl-*` tools
>
> See `.claude/agents/secretary.md` "Available tools" section for the canonical list.

If secretary work later requires a backend extension (e.g., new endpoint for tracking applications cross-project), file the spec here at that time. Until then this file is intentionally empty.
