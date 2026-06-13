# netops — Network diagnosis team (DRAFT skeleton)

> DRAFT in _scratch/ — diagnosis-ONLY scope. No config changes, ever. Move to
> `.codex/teams/netops.md` only after review. Extends AGENTS.md universal rules.

## Scope guardrail (non-negotiable for this team)

- **READ-ONLY diagnosis only.** Specialists may run *read* commands (ping, traceroute,
  dig/nslookup, `show` commands, log/metric reads, monitoring-API GETs). They may
  NEVER push device config, restart services, or mutate infra.
- Any recommended fix is *output as a proposal* for a human to execute. Equivalent of
  the universal "DB writes go through FastAPI only" rule → here: "device changes are
  human-only; agents propose, never apply."
- Sensitive data (configs, IPs, creds-in-logs) → sanitize before it lands in agent
  context (reuse #1123 halt_reason/context sanitization pattern). Device creds, if
  ever needed for live read, come from the credentials vault (#1326), HITL-gated.

## Two trigger modes — same roster, different entry

| Mode | Trigger | Example |
|---|---|---|
| **On-demand** | User files a Kanban task | "diagnose: VLAN20 users can't reach internet" |
| **Scheduled** | Task `recurrence_rule` (internal recurrence engine, ticks 60s) | "nightly health sweep", "hourly DNS/latency check" |

Scheduled mode = **clone of the scheduled-auditor pattern** (#1210/#1211): a recurring task
fires → specialist runs the read-only check battery → emits a structured report →
flags anomalies → notify/HITL only when something is off (quiet when healthy).

## Roster (diagnosis specialists — all read-only) — estate: Fortinet fw + MikroTik + Ubiquiti, monitored by Zabbix

| Role | Phase | Lane | Reads |
|---|---|---|---|
| `netops-monitoring-reader` | **P1 (start here)** | Zabbix alert correlation across the whole estate | Zabbix JSON-RPC (GET methods only) / pasted export |
| `netops-l2l3` | P1/P2 | MikroTik switching + routing (interfaces, routes, ARP) | RouterOS read-only `print`/`monitor`, exported config |
| `netops-firewall` | P2 | Fortinet ACL / NAT / session / policy path | FortiOS read-only `get`/`diagnose`, log search |
| `netops-wifi` | P2 | Ubiquiti RF, AP health, client assoc, roaming | UniFi controller stats (read), AP status |
| `netops-dns-dhcp` | P2 | name resolution + addressing | dig/nslookup, DHCP leases/logs |

Lead does triage + integration itself (no dedicated triage agent for the PoC).
Lead decomposes by OSI layer → spawns the relevant lanes in parallel → integrates into
one diagnosis with a ranked hypothesis list + recommended (human-executed) fix.

**PoC roster (TEAM_ROSTERS[netops] for the first onboarding):** start with
`netops-monitoring-reader` + `netops-l2l3`. Add firewall / wifi / dns-dhcp in P2
(each addition is just a constants.py roster line + one `.codex/agents/*.md`).

## Lifecycle (per incident / per scheduled fire)

1. **Triage** — classify symptom, pick lanes, set acceptance_criteria
   (e.g. "root cause identified", "evidence cited", "fix proposed + risk-rated").
2. **Diagnose (parallel)** — each lane runs its read-only battery, returns
   structured findings (evidence + confidence).
3. **Integrate** — Lead merges, ranks hypotheses, picks most-probable root cause.
4. **Report** — structured output: symptom → evidence → root cause → proposed fix
   (with risk + rollback note) → "execute manually" flag.
5. **(scheduled only) Notify-on-anomaly** — quiet if healthy; push/digest if flagged.
6. **Verify** — after human applies fix, re-run the failing check to confirm
   (acceptance_criteria → passed).

## Research-first

Non-trivial incident opens with a research spawn (Haiku) — pull vendor docs / error-code
meaning / known-issue advisories before the specialist diagnoses. Escape valve: skip for
obvious single-lane symptoms.

## Tooling phases (build order) — Zabbix-first

- **Phase 1a (zero setup, on-demand):** operator pastes/exports the Zabbix "Problems"
  view → `netops-monitoring-reader` correlates → ranked root cause. No integration at all.
- **Phase 1b (scheduled unlock):** one READ-ONLY Zabbix JSON-RPC token (vault #1326) →
  agent pulls `problem.get`/`event.get`/`history.get` itself → hourly/daily sweep via
  the recurrence engine (auditor pattern): quiet when clean, digest on problems.
- **Phase 2:** live read-only drill-down per vendor — MikroTik RouterOS API / SSH `print`,
  Fortinet FortiOS `get`/`diagnose`, UniFi controller read API (via MCP). Adds the
  vendor lane agents.
- **Phase 3 (optional):** more scheduled batteries + quiet-hours (#1283) + push digest.

## Anti-patterns (team-specific)

- Specialist runs a write/config command → HARD STOP (read-only team).
- Auto-applying a proposed fix → never; human executes.
- Dumping raw 50k-line logs into Lead context → specialists summarize; cite line refs.
- Treating a scheduled sweep as "all clear" when a data source was unreachable →
  report the gap, don't silently pass (no-silent-caps rule).
