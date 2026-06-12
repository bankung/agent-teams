---
story: <slug-kebab-case>
version: 1
updated: <YYYY-MM-DD>
updated_by: lead @ #<task-id>
---

<!-- STORY DOC — mutable thread STATE ("what is true NOW"), single writer = Lead.
     Counterpart: the activity rail holds the immutable per-task EVENTS.
     Rules (locked 2026-06-12, Kanban #2332):
     - Open a story only when a thread has >=2-3 related tasks or the operator names a workstream.
     - Tasks link here via a `story: <slug>` line in their Kanban description.
     - UPDATE PROTOCOL (optimistic lock): re-read this file IMMEDIATELY before writing;
       if `version` differs from what you read at task start -> someone wrote in between:
       re-read, merge, THEN bump. Never blind-overwrite. Bump `version` on EVERY edit.
     - Cross-stamp: the task's rail close-checkpoint says `story <slug> -> v<N>`.
     - In git repos this file rides the SAME docs commit as the task that changed it.
     - Every line artifact-backed (hash / #task-id / file:line / command output), written
       AFTER AC verification only. This task's scope only. No verbatim subagent paste.
       Committed-name vocabulary only. No env-state as durable fact (env is re-verified
       at every pickup; an env caveat belongs in the description of the task acting on it).
     - Body cap ~150 lines; changelog cap ~20 lines (older history = git). -->

## Current state

<!-- What is true for this thread right now. Terse declaratives with artifacts. -->

## Open threads

<!-- - #<id> — <what is pending there> ("none" if nothing deferred) -->

## Gotchas

<!-- Durable footguns discovered in this thread (file:line / command + symptom + fix). -->

## Decisions pointer

<!-- decisions.md entry titles (dates) that lock this thread's architecture. -->

## Changelog

<!-- v<N> <YYYY-MM-DD> #<task> — <one line>  (newest on top, cap ~20) -->
