# DashboardWelcomeBanner — manual smoke checklist

> Kanban #1362 T3. No Jest harness in this repo. Verify these 4 scenarios manually.

## Setup

Open `/dashboard` in a browser (http://localhost:5431/dashboard).  
Use DevTools → Application → Local Storage to inspect / clear the `agent-teams.dashboard.welcomeDismissed` key.

---

## Scenario 1 — Banner shows (new user, no own project)

**Preconditions:**
- `agent-teams.dashboard.welcomeDismissed` key NOT present in localStorage
- The only active projects are `agent-teams` and/or `demo-tour` (no user-created projects)

**Expected:**
- Banner renders at the top of the dashboard (above the header)
- Banner shows: "👋 Welcome to agent-teams"
- "Try the demo-tour project" is a clickable link to `/p/demo-tour`
- X button is visible

---

## Scenario 2 — Banner hidden when localStorage flag is set

**Preconditions:**
- Set `agent-teams.dashboard.welcomeDismissed = "true"` in localStorage (manually or via scenario 3)
- Reload the page

**Expected:**
- Banner is NOT rendered (no `data-welcome-banner` element in DOM)

---

## Scenario 3 — X dismiss writes localStorage flag and hides banner

**Preconditions:**
- Clear `agent-teams.dashboard.welcomeDismissed` from localStorage
- Reload so banner is visible (scenario 1 state)

**Steps:**
1. Click the X button

**Expected:**
- Banner unmounts (disappears immediately)
- `agent-teams.dashboard.welcomeDismissed` = `"true"` in localStorage
- Reloading the page keeps the banner hidden (scenario 2)

---

## Scenario 4 — Banner auto-hides when user has ≥1 own project

**Preconditions:**
- Clear `agent-teams.dashboard.welcomeDismissed` from localStorage
- At least one active project exists whose name is NOT `agent-teams` or `demo-tour`

**Expected:**
- Banner is NOT rendered
- `agent-teams.dashboard.welcomeDismissed` is NOT written to localStorage (so if the user later deletes all own projects and reloads, the banner returns)

---

## Link check

Click "Try the demo-tour project" → should navigate to `/p/demo-tour`.
