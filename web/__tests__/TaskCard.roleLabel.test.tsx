// Kanban #2826 — TaskCard's ROLE_LABEL map used to cover ONLY the 6 dev-range
// role codes (1-6). A non-dev-team task (seo=21, social=51, ...) rendered a
// literal "role21" / "role51" chip instead of a human label. This locks the
// fix: every named TaskRole code across all 6 team ranges renders readable
// text, and the `role${n}` fallback is reserved for truly UNNAMED codes.
//
// TaskCard is a pure presentational component once useSortable is mocked (no
// network, no context) — mirrors the setup in TaskCard.blockedBadge.test.tsx.

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { TaskRead } from "@/lib/api";
import { TaskRole, TaskStatus } from "@/lib/constants";
import { TaskCard } from "@/components/TaskCard";

// ---------------------------------------------------------------------------
// Mocks — isolate dnd-kit and Icon (no SVG stubs needed in jsdom).
// ---------------------------------------------------------------------------
vi.mock("@dnd-kit/sortable", () => ({
  useSortable: () => ({
    attributes: {},
    listeners: {},
    setNodeRef: () => undefined,
    transform: null,
    transition: undefined,
    isDragging: false,
  }),
}));

vi.mock("@dnd-kit/utilities", () => ({
  CSS: { Transform: { toString: () => "" } },
}));

vi.mock("@/components/Icon", () => ({ Icon: () => null }));
vi.mock("@/components/RunModeBadge", () => ({ RunModeBadge: () => null }));
vi.mock("@/components/TaskKindBadge", () => ({ TaskKindBadge: () => null }));
vi.mock("@/components/PendingBadge", () => ({ PendingBadge: () => null }));
vi.mock("@/components/RecurrenceIndicator", () => ({ RecurrenceIndicator: () => null }));
vi.mock("@/components/StepCounter", () => ({ StepCounter: () => null }));
vi.mock("@/components/TaskActivityStrip", () => ({ TaskActivityStrip: () => null }));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
let nextId = 1;

function makeTask(overrides: Partial<TaskRead> = {}): TaskRead {
  return {
    id: nextId++,
    project_id: 1,
    parent_task_id: null,
    title: "test task",
    description: null,
    process_status: TaskStatus.TODO,
    priority: 2,
    assigned_role: null,
    run_mode: "manual",
    task_kind: "human",
    task_type: "feature",
    due_date: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    sort_order: null,
    milestone_id: null,
    acceptance_criteria: null,
    is_template: false,
    is_pending: false,
    recurrence_rule: null,
    recurrence_timezone: "UTC",
    next_fire_at: null,
    spawned_from_task_id: null,
    scheduled_at: null,
    blocked_by: null,
    interaction_kind: "work",
    question_payload: null,
    resume_context: null,
    status_change_reason: null,
    estimated_input_tokens: null,
    estimated_output_tokens: null,
    estimated_cost_usd: null,
    model_override: null,
    halt_reason: null,
    operator_gate: null,
    operator_gate_note: null,
    ...overrides,
  } as TaskRead;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("TaskCard — role chip covers non-dev roles (#2826)", () => {
  it("renders a human label for a dev-range role code (regression baseline)", () => {
    const task = makeTask({ assigned_role: TaskRole.FRONTEND });
    render(<TaskCard task={task} />);
    expect(screen.getByText("frontend")).toBeInTheDocument();
  });

  it("renders 'seo strategist' — NOT literal 'role21' — for a seo-team role code", () => {
    const task = makeTask({ assigned_role: TaskRole.SEO_STRATEGIST });
    render(<TaskCard task={task} />);
    expect(screen.getByText("seo strategist")).toBeInTheDocument();
    expect(screen.queryByText("role21")).not.toBeInTheDocument();
  });

  it("renders 'content writer' — NOT literal 'role51' — for a social-range role code", () => {
    const task = makeTask({ assigned_role: TaskRole.CONTENT_WRITER });
    render(<TaskCard task={task} />);
    expect(screen.getByText("content writer")).toBeInTheDocument();
    expect(screen.queryByText("role51")).not.toBeInTheDocument();
  });

  it("falls back to `role${n}` for a genuinely unnamed/reserved code", () => {
    // 7 is inside the dev range (1-10) but intentionally unnamed/reserved —
    // the fallback is expected (documented) behaviour here, not a bug.
    const task = makeTask({ assigned_role: 7 });
    render(<TaskCard task={task} />);
    expect(screen.getByText("role7")).toBeInTheDocument();
  });
});
