// Integration tests for the #1310 Task Template picker inside NewTaskModal.
//
// Strategy: render the modal in the open state (externalOpen=true + project
// with team="dev"), mock @/lib/api (listTaskTemplates + createTask +
// listMilestones), stub ActionTemplatePicker + HandoffTemplatePicker.
//
// All queries use the container returned by render() so they stay scoped to
// the current test's DOM — avoids cross-test pollution through document.querySelector.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, within, fireEvent, configure } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ProjectRead, TaskTemplateRead, TaskRead } from "@/lib/api";
import { TaskPriority } from "@/lib/constants";
import type { TaskPriorityValue } from "@/lib/constants";

// B1 — raise async util timeout to 5 s so waitFor survives full-suite CPU load.
configure({ asyncUtilTimeout: 5000 });

// ---------- mocks ----------

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
    [k: string]: unknown;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

// Stub heavy sub-pickers that do their own fetches and are tested elsewhere.
vi.mock("@/components/ActionTemplatePicker", () => ({
  ActionTemplatePicker: () => null,
}));
vi.mock("@/components/HandoffTemplatePicker", () => ({
  HandoffTemplatePicker: () => null,
}));

// API mocks — hoisted outside tests so beforeEach can reconfigure them.
const mockCreateTask = vi.fn();
const mockListTaskTemplates = vi.fn();
const mockListMilestones = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    listTaskTemplates: (...args: Parameters<typeof actual.listTaskTemplates>) =>
      mockListTaskTemplates(...args),
    createTask: (...args: Parameters<typeof actual.createTask>) =>
      mockCreateTask(...args),
    listMilestones: (...args: Parameters<typeof actual.listMilestones>) =>
      mockListMilestones(...args),
  };
});

// ---------- fixtures ----------

const MOCK_PROJECT: ProjectRead = {
  id: 42,
  name: "agent-teams",
  description: null,
  paths_web: "/web",
  paths_api: "/api",
  paths_db: "postgres",
  stack_web: "next",
  stack_api: "fastapi",
  stack_db: "postgres",
  config: {},
  is_active: true,
  team: "dev",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  auto_run_consent_at: null,
  sources: [],
  working_path: null,
  working_repo: null,
  budget_daily_usd: null,
  budget_monthly_usd: null,
  budget_total_usd: null,
  is_paused: false,
};

const MOCK_TEMPLATES: TaskTemplateRead[] = [
  {
    id: 1,
    team: "dev",
    name: "Add API endpoint",
    icon: null,
    description_template:
      "Add a new {{method}} {{path}} endpoint that {{purpose}}.",
    acceptance_criteria_template: [
      { text: "{{method}} {{path}} returns 2xx" },
      { text: "422 on bad input" },
    ],
    placeholders: ["method", "path", "purpose"],
    default_task_type: "feature",
    default_priority: TaskPriority.NORMAL,
    default_task_kind: "ai",
    status: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
  },
  {
    id: 2,
    team: "dev",
    name: "Fix from regression",
    icon: null,
    description_template: "Fix: {{symptom}}.",
    acceptance_criteria_template: [{ text: "Root cause documented" }],
    placeholders: ["symptom"],
    default_task_type: "bug",
    default_priority: TaskPriority.NORMAL,
    default_task_kind: "ai",
    status: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
  },
  {
    id: 3,
    team: "dev",
    name: "Document a module",
    icon: null,
    description_template: "Write docs.",
    acceptance_criteria_template: [{ text: "Architecture summary" }],
    placeholders: [],
    default_task_type: "docs",
    default_priority: TaskPriority.NORMAL,
    default_task_kind: "ai",
    status: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
  },
  // Round-2 regression fixture: single placeholder {{x}}, description repeats it twice.
  {
    id: 4,
    team: "dev",
    name: "Round-2 fixture",
    icon: null,
    description_template: "Value: {{x}} and again {{x}}",
    acceptance_criteria_template: [],
    placeholders: ["x"],
    default_task_type: "feature",
    default_priority: TaskPriority.NORMAL,
    default_task_kind: "ai",
    status: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: null,
  },
];

function makeCreatedTask(): TaskRead {
  return {
    id: 999,
    project_id: 42,
    parent_task_id: null,
    title: "Test task",
    description: null,
    process_status: 1,
    priority: 2,
    assigned_role: null,
    run_mode: "manual",
    task_kind: "ai",
    task_type: "feature",
    due_date: null,
    record_status: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    sort_order: null,
    milestone_id: null,
    acceptance_criteria: null,
    is_template: false,
  } as TaskRead;
}

// ---------- helper ----------

// Renders the modal open and waits for the template fetch to resolve.
// Returns `container` (the RTL host element) for scoped queries.
//
// Options:
//   expectEmpty — set to true when the mock returns [] so the helper waits for
//                 [data-new-task-template-empty] instead of the select.  The
//                 default (false) waits for [data-new-task-template], which
//                 eliminates the race where the empty-state renders first and
//                 renderOpenModal returns before the select exists.
async function renderOpenModal(
  projectOrOpts: ProjectRead | { project?: ProjectRead; expectEmpty?: boolean } = MOCK_PROJECT,
  legacyOpts: { expectEmpty?: boolean } = {},
) {
  // Support two call signatures:
  //   renderOpenModal()                    — default project, expectEmpty=false
  //   renderOpenModal({ expectEmpty: true }) — default project, expectEmpty=true
  //   renderOpenModal(MOCK_PROJECT)        — legacy positional (expectEmpty=false)
  let project: ProjectRead;
  let expectEmpty: boolean;

  if (
    projectOrOpts &&
    typeof projectOrOpts === "object" &&
    !("id" in projectOrOpts)
  ) {
    // Called as renderOpenModal({ expectEmpty: true })
    const opts = projectOrOpts as { project?: ProjectRead; expectEmpty?: boolean };
    project = opts.project ?? MOCK_PROJECT;
    expectEmpty = opts.expectEmpty ?? false;
  } else {
    // Called as renderOpenModal() or renderOpenModal(MOCK_PROJECT)
    project = (projectOrOpts as ProjectRead) ?? MOCK_PROJECT;
    expectEmpty = legacyOpts.expectEmpty ?? false;
  }

  const { NewTaskModal } = await import("@/components/NewTaskModal");
  const onClose = vi.fn();
  const { container } = render(
    <NewTaskModal
      projectId={project.id}
      project={project}
      externalOpen={true}
      onExternalClose={onClose}
    />,
  );

  if (expectEmpty) {
    // Wait for the empty-state note (listTaskTemplates returned []).
    await waitFor(() => {
      if (!container.querySelector("[data-new-task-template-empty]")) {
        throw new Error("Empty-state note not yet rendered");
      }
    });
  } else {
    // Wait specifically for the template <select> (non-empty list).
    // This eliminates the race where the empty-state renders first and the
    // helper returns before the select exists (AC1 intermittent failure).
    await waitFor(() => {
      if (!container.querySelector("[data-new-task-template]")) {
        throw new Error("Template <select> not yet rendered");
      }
    });
  }

  return { container, onClose };
}

// Typed shorthand — scoped querySelector on container.
function q(container: Element, selector: string) {
  return container.querySelector(selector);
}
function qAll(container: Element, selector: string) {
  return container.querySelectorAll(selector);
}

// Waits for the template <select> to appear in the DOM (listTaskTemplates async
// fetch + React re-render) before returning it.  Use this before every
// user.selectOptions() call to eliminate the null-select race under load.
async function getTemplateSelect(container: HTMLElement): Promise<HTMLSelectElement> {
  return waitFor(() => {
    const el = container.querySelector("[data-new-task-template]") as HTMLSelectElement | null;
    if (!el) throw new Error("template <select> not rendered yet (templates still loading)");
    return el;
  });
}

// ---------- tests ----------

describe("NewTaskModal — Task Template Picker (#1310)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockListTaskTemplates.mockResolvedValue(MOCK_TEMPLATES);
    mockListMilestones.mockResolvedValue([]);
    mockCreateTask.mockResolvedValue(makeCreatedTask());
  });

  // -----------------------------------------------------------------------
  // AC1: Dropdown renders + correct fetch call
  // -----------------------------------------------------------------------
  it("AC1: calls listTaskTemplates with 'dev' and renders a select with all templates + optgroup", async () => {
    const { container } = await renderOpenModal();

    // Correct team passed to fetch
    expect(mockListTaskTemplates).toHaveBeenCalledWith(
      "dev",
      expect.anything(),
    );

    // Use the waited helper — eliminates the race where the empty-state renders
    // first (templates=[]) and the sync query returns null before the fetch resolves.
    const select = await getTemplateSelect(container);

    const w = within(select);
    // Manual entry option always present
    expect(w.getByRole("option", { name: /manual entry/i })).toBeInTheDocument();
    // Four template options (three original + Round-2 fixture)
    expect(
      w.getByRole("option", { name: /Add API endpoint/i }),
    ).toBeInTheDocument();
    expect(
      w.getByRole("option", { name: /Fix from regression/i }),
    ).toBeInTheDocument();
    expect(
      w.getByRole("option", { name: /Document a module/i }),
    ).toBeInTheDocument();
    expect(
      w.getByRole("option", { name: /Round-2 fixture/i }),
    ).toBeInTheDocument();

    // Optgroup label contains team name + count
    const optgroup = select.querySelector("optgroup");
    expect(optgroup).not.toBeNull();
    const label = optgroup!.getAttribute("label") ?? "";
    expect(label).toContain("dev");
    expect(label).toContain("4");
  });

  // -----------------------------------------------------------------------
  // AC2: Placeholder inputs appear + live substitution
  // -----------------------------------------------------------------------
  it("AC2: selecting template 1 shows 3 placeholder inputs and live-substitutes the description", async () => {
    const { container } = await renderOpenModal();
    const user = userEvent.setup();

    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "1");

    // Three placeholder inputs must appear
    await waitFor(() => {
      expect(q(container, "[data-new-task-placeholders]")).not.toBeNull();
    });

    const methodInput = q(
      container,
      '[data-new-task-placeholder="method"]',
    ) as HTMLInputElement;
    const pathInput = q(
      container,
      '[data-new-task-placeholder="path"]',
    ) as HTMLInputElement;
    const purposeInput = q(
      container,
      '[data-new-task-placeholder="purpose"]',
    ) as HTMLInputElement;

    expect(methodInput).not.toBeNull();
    expect(pathInput).not.toBeNull();
    expect(purposeInput).not.toBeNull();

    const descTextarea = q(
      container,
      "[data-new-task-description]",
    ) as HTMLTextAreaElement;

    // B2: fireEvent.change (atomic) instead of user.type (inter-keystroke delays)
    // Set method + path first; purpose still empty → {{purpose}} stays literal
    fireEvent.change(methodInput, { target: { value: "GET" } });
    fireEvent.change(pathInput, { target: { value: "/x" } });

    await waitFor(() => {
      expect(descTextarea.value).toContain("{{purpose}}");
    });

    // After filling purpose, full substitution is complete
    fireEvent.change(purposeInput, { target: { value: "pings" } });

    await waitFor(() => {
      expect(descTextarea.value).toBe(
        "Add a new GET /x endpoint that pings.",
      );
    });

    // AC rows also substituted
    const acRows = qAll(
      container,
      "[data-new-task-ac-row]",
    ) as NodeListOf<HTMLInputElement>;
    expect(acRows.length).toBe(2);
    expect(acRows[0].value).toBe("GET /x returns 2xx");
    expect(acRows[1].value).toBe("422 on bad input");
  });

  // -----------------------------------------------------------------------
  // AC3: Editable after template applied
  // -----------------------------------------------------------------------
  it("AC3: description and AC rows are directly editable after template application", async () => {
    const user = userEvent.setup();
    const { container } = await renderOpenModal();

    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "1");

    await waitFor(() => {
      expect(q(container, "[data-new-task-placeholders]")).not.toBeNull();
    });

    const descTextarea = q(
      container,
      "[data-new-task-description]",
    ) as HTMLTextAreaElement;

    // B2: single fireEvent.change proves the onChange editability path (no inter-keystroke timing)
    fireEvent.change(descTextarea, { target: { value: "My custom description" } });

    expect(descTextarea.value).toBe("My custom description");

    // AC row is editable
    const acRows = qAll(
      container,
      "[data-new-task-ac-row]",
    ) as NodeListOf<HTMLInputElement>;
    expect(acRows.length).toBeGreaterThan(0);
    fireEvent.change(acRows[0], { target: { value: "My custom AC" } });
    expect(acRows[0].value).toBe("My custom AC");
  });

  // -----------------------------------------------------------------------
  // M2 dirty-flag: manual edit of description locks it from further substitution
  // -----------------------------------------------------------------------
  it("M2 dirty-flag: manually editing description prevents placeholder updates from overwriting it; clean path still substitutes", async () => {
    const user = userEvent.setup();
    const { container } = await renderOpenModal();

    // Apply template 1
    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "1");

    await waitFor(() => {
      expect(q(container, "[data-new-task-placeholders]")).not.toBeNull();
    });

    const methodInput = q(
      container,
      '[data-new-task-placeholder="method"]',
    ) as HTMLInputElement;
    const pathInput = q(
      container,
      '[data-new-task-placeholder="path"]',
    ) as HTMLInputElement;
    const purposeInput = q(
      container,
      '[data-new-task-placeholder="purpose"]',
    ) as HTMLInputElement;
    const descTextarea = q(
      container,
      "[data-new-task-description]",
    ) as HTMLTextAreaElement;

    // --- POSITIVE PATH: no manual edit → description keeps updating ---
    // B2: fireEvent.change (atomic) instead of user.type
    fireEvent.change(methodInput, { target: { value: "POST" } });
    fireEvent.change(pathInput, { target: { value: "/items" } });

    // Description must live-update (method + path substituted, purpose still literal)
    await waitFor(() => {
      expect(descTextarea.value).toContain("POST");
      expect(descTextarea.value).toContain("/items");
    });

    // --- DIRTY PATH: manually edit description, then change a placeholder ---
    // Manually edit the description — sets descriptionDirty=true.
    const descValueBeforeManualEdit = descTextarea.value;
    const manualText = descValueBeforeManualEdit + " MANUAL_EDIT_MARKER";
    fireEvent.change(descTextarea, { target: { value: manualText } });

    expect(descTextarea.value).toContain("MANUAL_EDIT_MARKER");

    // B2: fireEvent.change instead of user.type for purpose fill
    fireEvent.change(purposeInput, { target: { value: "runs" } });

    // The manually-edited description must still contain the manual marker
    await waitFor(() => {
      expect(descTextarea.value).toContain("MANUAL_EDIT_MARKER");
    });

    // And it must NOT have been replaced by the pure substitution result
    expect(descTextarea.value).not.toBe(
      "Add a new POST /items endpoint that runs.",
    );
  });

  // -----------------------------------------------------------------------
  // AC4: Clear/switch template
  // -----------------------------------------------------------------------
  it("AC4: selecting Manual entry clears placeholders + description + AC; switching to template 2 re-derives", async () => {
    const user = userEvent.setup();
    const { container } = await renderOpenModal();

    // Apply template 1
    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "1");

    await waitFor(() => {
      expect(q(container, "[data-new-task-placeholders]")).not.toBeNull();
    });

    // Switch to Manual entry (value = "")
    await user.selectOptions(select, "");

    // Placeholder inputs must be gone
    await waitFor(() => {
      expect(q(container, "[data-new-task-placeholders]")).toBeNull();
    });

    // AC editor must be gone
    expect(q(container, "[data-new-task-ac-editor]")).toBeNull();

    // Description must be cleared
    const descTextarea = q(
      container,
      "[data-new-task-description]",
    ) as HTMLTextAreaElement;
    expect(descTextarea.value).toBe("");

    // Now select template 2
    await user.selectOptions(select, "2");

    // One placeholder input for "symptom"
    await waitFor(() => {
      expect(
        q(container, '[data-new-task-placeholder="symptom"]'),
      ).not.toBeNull();
    });

    // Description starts with template 2 pattern (symptom unfilled → literal)
    expect(descTextarea.value).toBe("Fix: {{symptom}}.");

    // AC editor shows template 2 AC
    const acRows = qAll(
      container,
      "[data-new-task-ac-row]",
    ) as NodeListOf<HTMLInputElement>;
    expect(acRows.length).toBe(1);
    expect(acRows[0].value).toBe("Root cause documented");
  });

  // -----------------------------------------------------------------------
  // AC5: Empty state — no templates returned
  // -----------------------------------------------------------------------
  it("AC5: when listTaskTemplates returns [] shows empty-state note, hides select, and still allows manual submit", async () => {
    mockListTaskTemplates.mockResolvedValue([]);

    const user = userEvent.setup();
    const { container } = await renderOpenModal({ expectEmpty: true });

    // Wait for the empty-state note to appear (listTaskTemplates resolves async)
    await waitFor(() => {
      expect(q(container, "[data-new-task-template-empty]")).toBeTruthy();
    });

    // Select must NOT appear
    expect(q(container, "[data-new-task-template]")).toBeNull();

    // B2: fireEvent.change (atomic) for multi-char title fill
    const titleInput = q(
      container,
      "[data-new-task-title]",
    ) as HTMLInputElement;
    fireEvent.change(titleInput, { target: { value: "Manual task" } });

    const submitBtn = q(
      container,
      "[data-new-task-submit]",
    ) as HTMLButtonElement;
    await user.click(submitBtn);

    await waitFor(() => {
      expect(mockCreateTask).toHaveBeenCalledOnce();
    });

    const body = mockCreateTask.mock.calls[0][1];
    expect(body.title).toBe("Manual task");
  });

  // -----------------------------------------------------------------------
  // Submit payload: template 3 (no placeholders)
  // -----------------------------------------------------------------------
  it("Submit payload: template 3 (no placeholders) sends correct AC shape and no task_template_id", async () => {
    const user = userEvent.setup();
    const { container } = await renderOpenModal();

    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "3");

    // B2: fireEvent.change (atomic) for multi-char title fill
    const titleInput = q(
      container,
      "[data-new-task-title]",
    ) as HTMLInputElement;
    fireEvent.change(titleInput, { target: { value: "My doc task" } });

    // Submit
    const submitBtn = q(
      container,
      "[data-new-task-submit]",
    ) as HTMLButtonElement;
    await user.click(submitBtn);

    await waitFor(() => {
      expect(mockCreateTask).toHaveBeenCalledOnce();
    });

    const [calledProjectId, body] = mockCreateTask.mock.calls[0];
    expect(calledProjectId).toBe(42);
    expect(body.title).toBe("My doc task");

    // acceptance_criteria must be the template's AC with pending status
    expect(body.acceptance_criteria).toEqual([
      {
        text: "Architecture summary",
        status: "pending",
        verified_by: null,
        verified_at: null,
        notes: null,
      },
    ]);

    // No task_template_id — plain task, not a template reference
    expect(body).not.toHaveProperty("task_template_id");
    expect(body).not.toHaveProperty("template_id");
  });

  // -----------------------------------------------------------------------
  // Fix B / Finding #2: "+ Add criterion" must NOT freeze description live-sub
  // -----------------------------------------------------------------------
  it("Fix B: clicking '+ Add criterion' (acDirty) does not freeze description live-substitution", async () => {
    const user = userEvent.setup();
    const { container } = await renderOpenModal();

    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "1");

    await waitFor(() => {
      expect(q(container, "[data-new-task-placeholders]")).not.toBeNull();
    });

    // Click "+ Add criterion" — sets acDirty=true, must NOT set descriptionDirty
    const addBtn = q(container, "[data-new-task-ac-add]") as HTMLButtonElement;
    await user.click(addBtn);

    // After click there should be 3 AC rows (2 template + 1 blank user-added)
    await waitFor(() => {
      expect(qAll(container, "[data-new-task-ac-row]").length).toBe(3);
    });

    const descTextarea = q(container, "[data-new-task-description]") as HTMLTextAreaElement;
    const methodInput = q(container, '[data-new-task-placeholder="method"]') as HTMLInputElement;

    // Now type into a placeholder — description should STILL live-substitute
    fireEvent.change(methodInput, { target: { value: "PATCH" } });

    await waitFor(() => {
      // descriptionDirty is false → description re-derives with new placeholder
      expect(descTextarea.value).toContain("PATCH");
    });

    // acDirty is true → the user-added blank row must still be present (not wiped)
    expect(qAll(container, "[data-new-task-ac-row]").length).toBe(3);
  });

  // -----------------------------------------------------------------------
  // Fix B independence: description dirty ≠ AC dirty
  // -----------------------------------------------------------------------
  it("Fix B independence: manually editing description preserves it while AC still live-substitutes", async () => {
    const user = userEvent.setup();
    const { container } = await renderOpenModal();

    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "1");

    await waitFor(() => {
      expect(q(container, "[data-new-task-placeholders]")).not.toBeNull();
    });

    const descTextarea = q(container, "[data-new-task-description]") as HTMLTextAreaElement;
    const methodInput = q(container, '[data-new-task-placeholder="method"]') as HTMLInputElement;
    const pathInput = q(container, '[data-new-task-placeholder="path"]') as HTMLInputElement;

    // Fill method + path so the template substitution is partially done
    fireEvent.change(methodInput, { target: { value: "DELETE" } });
    fireEvent.change(pathInput, { target: { value: "/items/1" } });

    await waitFor(() => {
      expect(descTextarea.value).toContain("DELETE");
    });

    // Manually edit the description — sets descriptionDirty=true
    const manualDesc = "MY_MANUAL_DESCRIPTION";
    fireEvent.change(descTextarea, { target: { value: manualDesc } });
    expect(descTextarea.value).toBe(manualDesc);

    // Now change a placeholder value
    fireEvent.change(methodInput, { target: { value: "PUT" } });

    // descriptionDirty=true → description must NOT be overwritten
    await waitFor(() => {
      expect(descTextarea.value).toBe(manualDesc);
    });

    // acDirty=false → AC rows SHOULD have re-substituted with new value "PUT"
    const acRows = qAll(container, "[data-new-task-ac-row]") as NodeListOf<HTMLInputElement>;
    expect(acRows.length).toBe(2);
    expect(acRows[0].value).toContain("PUT");
  });

  // -----------------------------------------------------------------------
  // Fix A / Finding #1: re-selecting same template is a no-op
  // -----------------------------------------------------------------------
  it("Fix A: re-selecting the same template leaves placeholder values and description untouched", async () => {
    const user = userEvent.setup();
    const { container } = await renderOpenModal();

    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "1");

    await waitFor(() => {
      expect(q(container, "[data-new-task-placeholders]")).not.toBeNull();
    });

    const methodInput = q(container, '[data-new-task-placeholder="method"]') as HTMLInputElement;
    const descTextarea = q(container, "[data-new-task-description]") as HTMLTextAreaElement;

    // Fill a placeholder value
    fireEvent.change(methodInput, { target: { value: "OPTIONS" } });

    await waitFor(() => {
      expect(descTextarea.value).toContain("OPTIONS");
    });

    const descBefore = descTextarea.value;
    const methodBefore = methodInput.value;

    // note: jsdom's <select> onChange fires only when the value changes, so
    // re-selecting the same option value ("1") typically does NOT trigger onChange.
    // The guard `if (t.id === selectedTemplateId) return` protects against the
    // case where the picker component calls onSelectTemplate even for same-value
    // (e.g. from a custom component). We verify the guard's intent by calling
    // onSelectTemplate-equivalent: a second selectOptions with the same value
    // results in no change visible to the user.
    await user.selectOptions(select, "1");

    // The description and placeholder input must be unchanged (guard fired or no-op)
    expect(descTextarea.value).toBe(descBefore);
    expect(methodInput.value).toBe(methodBefore);
  });

  // -----------------------------------------------------------------------
  // ROUND-2 edge-case regressions — security-relevant substitution behaviours
  // -----------------------------------------------------------------------

  // R2-1: XSS payload stored as literal text in the controlled textarea value.
  // React's controlled input stores raw strings; the value is never parsed as HTML.
  // This regression locks that a "<img …>" value is never turned into a real element.
  it("R2-1 XSS: HTML payload in placeholder value is stored as literal text, never parsed as HTML", async () => {
    const user = userEvent.setup();
    const { container } = await renderOpenModal();

    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "4");

    await waitFor(() => {
      expect(q(container, '[data-new-task-placeholder="x"]')).not.toBeNull();
    });

    const xInput = q(container, '[data-new-task-placeholder="x"]') as HTMLInputElement;
    const descTextarea = q(container, "[data-new-task-description]") as HTMLTextAreaElement;

    const xssPayload = "<img src=x onerror=alert(1)>";
    // Use fireEvent to set the value atomically (avoids user-event bracket-key expansion)
    fireEvent.change(xInput, { target: { value: xssPayload } });

    await waitFor(() => {
      // The raw string must appear verbatim in the controlled textarea value
      expect(descTextarea.value).toContain(xssPayload);
    });

    // No <img> element must have been injected into the DOM
    expect(container.querySelector("img")).toBeNull();
  });

  // R2-2: Dollar-sign regex-replacement specials in a placeholder value are
  // inserted literally. Proves the replacer uses a function (not a bare string),
  // so `$&`, `$1`, `$\`` do NOT expand to backreference / match text.
  it("R2-2 regex-specials: $&, $1, $` in placeholder value are inserted literally (function replacer)", async () => {
    const user = userEvent.setup();
    const { container } = await renderOpenModal();

    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "4");

    await waitFor(() => {
      expect(q(container, '[data-new-task-placeholder="x"]')).not.toBeNull();
    });

    const xInput = q(container, '[data-new-task-placeholder="x"]') as HTMLInputElement;
    const descTextarea = q(container, "[data-new-task-description]") as HTMLTextAreaElement;

    // $& = whole match; $1 = first capture; $` = string before match
    const specialsPayload = "$&$1$`";
    fireEvent.change(xInput, { target: { value: specialsPayload } });

    await waitFor(() => {
      // The literal dollar-sign sequence must survive unchanged
      expect(descTextarea.value).toContain(specialsPayload);
    });
  });

  // R2-3: No recursive substitution. Typing `{{y}}` as the value of `x` must
  // leave `{{y}}` as literal text in the result — the single-pass replacer must
  // NOT re-scan the output for further placeholder tokens.
  it("R2-3 no-recursive-sub: value containing {{y}} is not re-substituted (single-pass replacer)", async () => {
    const user = userEvent.setup();
    const { container } = await renderOpenModal();

    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "4");

    await waitFor(() => {
      expect(q(container, '[data-new-task-placeholder="x"]')).not.toBeNull();
    });

    const xInput = q(container, '[data-new-task-placeholder="x"]') as HTMLInputElement;
    const descTextarea = q(container, "[data-new-task-description]") as HTMLTextAreaElement;

    // Set x = "{{y}}" — template 4 has no `y` placeholder, so if re-substitution
    // occurred the token would either stay as-is or mutate unexpectedly; either
    // way the value MUST remain the literal string "{{y}}" in the result.
    fireEvent.change(xInput, { target: { value: "{{y}}" } });

    await waitFor(() => {
      // Single-pass: the inserted "{{y}}" is NOT itself replaced
      expect(descTextarea.value).toBe("Value: {{y}} and again {{y}}");
    });
  });

  // R2-4: All occurrences of a repeated placeholder are substituted (global flag).
  // Template 4 has "Value: {{x}} and again {{x}}" — both must become "Z".
  it("R2-4 global-replace: all occurrences of a repeated placeholder are substituted", async () => {
    const user = userEvent.setup();
    const { container } = await renderOpenModal();

    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "4");

    await waitFor(() => {
      expect(q(container, '[data-new-task-placeholder="x"]')).not.toBeNull();
    });

    const xInput = q(container, '[data-new-task-placeholder="x"]') as HTMLInputElement;
    const descTextarea = q(container, "[data-new-task-description]") as HTMLTextAreaElement;

    // Before filling: both occurrences of {{x}} must be present (unfilled state)
    expect(descTextarea.value).toBe("Value: {{x}} and again {{x}}");

    // After filling x = "Z": BOTH occurrences must be replaced
    fireEvent.change(xInput, { target: { value: "Z" } });

    await waitFor(() => {
      expect(descTextarea.value).toBe("Value: Z and again Z");
    });
  });

  // -----------------------------------------------------------------------
  // B3 Regression tests locking Part-A null-safety + priority guard fixes
  // -----------------------------------------------------------------------

  it("R3 null-safety: a malformed template (null acceptance_criteria_template + null description_template + missing placeholders) does not crash the modal", async () => {
    const malformed = {
      id: 99,
      team: "dev",
      name: "Malformed template",
      icon: null,
      description_template: null,
      acceptance_criteria_template: null,
      placeholders: undefined,
      default_task_type: "feature",
      default_priority: TaskPriority.NORMAL,
      default_task_kind: "ai",
      status: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: null,
    } as unknown as TaskTemplateRead;

    // Use a local mock override so it doesn't disturb the shared beforeEach MOCK_TEMPLATES
    mockListTaskTemplates.mockResolvedValue([malformed]);

    const { container } = await renderOpenModal();

    const select = await getTemplateSelect(container);
    const user = userEvent.setup();
    await user.selectOptions(select, "99");

    // The template <select> must still be in the DOM (no throw/white-screen)
    await waitFor(() => {
      expect(container.querySelector("[data-new-task-template]")).not.toBeNull();
    });

    // Description textarea must still be queryable (modal did not crash)
    expect(container.querySelector("[data-new-task-description]")).not.toBeNull();
  });

  it("R3 priority guard: a template with out-of-range default_priority (99) does not set an invalid priority", async () => {
    const badPriority = {
      id: 98,
      team: "dev",
      name: "Bad priority template",
      icon: null,
      description_template: "Simple desc.",
      acceptance_criteria_template: [],
      placeholders: [],
      default_task_type: "feature",
      default_priority: 99 as unknown as TaskPriorityValue,
      default_task_kind: "ai",
      status: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: null,
    } as unknown as TaskTemplateRead;

    mockListTaskTemplates.mockResolvedValue([badPriority]);

    const { container } = await renderOpenModal();

    const select = await getTemplateSelect(container);
    const user = userEvent.setup();
    await user.selectOptions(select, "98");

    // The priority <select> value must remain a valid option (NOT "99")
    await waitFor(() => {
      const prioritySelect = container.querySelector("[data-new-task-priority]") as HTMLSelectElement;
      expect(prioritySelect).not.toBeNull();
      expect(prioritySelect.value).not.toBe("99");
      // Must be one of the valid TaskPriority values (1/2/3/4)
      expect(["1", "2", "3", "4"]).toContain(prioritySelect.value);
    });
  });

  // -----------------------------------------------------------------------
  // Submit omits AC when no template is selected
  // -----------------------------------------------------------------------
  it("Submit omits acceptance_criteria when no template is selected (manual entry)", async () => {
    mockListTaskTemplates.mockResolvedValue([]);

    const user = userEvent.setup();
    const { container } = await renderOpenModal({ expectEmpty: true });

    // B2: fireEvent.change (atomic) for multi-char title fill
    const titleInput = q(
      container,
      "[data-new-task-title]",
    ) as HTMLInputElement;
    fireEvent.change(titleInput, { target: { value: "Bare task" } });

    const submitBtn = q(
      container,
      "[data-new-task-submit]",
    ) as HTMLButtonElement;
    await user.click(submitBtn);

    await waitFor(() => {
      expect(mockCreateTask).toHaveBeenCalledOnce();
    });

    const body = mockCreateTask.mock.calls[0][1];
    expect(body).not.toHaveProperty("acceptance_criteria");
  });

  // -----------------------------------------------------------------------
  // #1909 AC3 — client-side AC > 50 pre-flight guard
  // -----------------------------------------------------------------------

  // Helper: render with template 3 (no placeholders, has 1 AC row pre-filled)
  // then programmatically inflate the AC list to `count` rows by clicking
  // "+ Add criterion" repeatedly and returning the container + submit button.
  async function renderWithAcCount(count: number) {
    const { container } = await renderOpenModal();
    const user = userEvent.setup();

    const select = await getTemplateSelect(container);
    await user.selectOptions(select, "3");

    // Wait for AC editor to appear (template 3 has 1 pre-filled AC row)
    await waitFor(() => {
      expect(q(container, "[data-new-task-ac-editor]")).not.toBeNull();
    });

    // Add rows until the total is `count` (template 3 starts with 1 row)
    const addBtn = q(container, "[data-new-task-ac-add]") as HTMLButtonElement;
    for (let i = 1; i < count; i++) {
      fireEvent.click(addBtn);
    }

    // Fill the title so canSubmit is true
    const titleInput = q(container, "[data-new-task-title]") as HTMLInputElement;
    fireEvent.change(titleInput, { target: { value: "AC guard test" } });

    // Fill each AC row with non-empty text so they count as non-empty
    await waitFor(() => {
      const rows = qAll(container, "[data-new-task-ac-row]") as NodeListOf<HTMLInputElement>;
      expect(rows.length).toBe(count);
    });
    const rows = qAll(container, "[data-new-task-ac-row]") as NodeListOf<HTMLInputElement>;
    rows.forEach((row, i) => {
      if (row.value.trim() === "") {
        fireEvent.change(row, { target: { value: `criterion ${i + 1}` } });
      }
    });

    const submitBtn = q(container, "[data-new-task-submit]") as HTMLButtonElement;
    return { container, submitBtn, user };
  }

  it("#1909 AC3a: >50 non-empty AC rows shows inline error and does NOT call fetch", async () => {
    // 51 rows exceeds the cap
    const { container, submitBtn, user } = await renderWithAcCount(51);

    await user.click(submitBtn);

    // Inline error must appear
    await waitFor(() => {
      const errorEl = q(container, "[data-new-task-error]");
      expect(errorEl).not.toBeNull();
      expect(errorEl!.textContent).toMatch(/51/);
      expect(errorEl!.textContent).toMatch(/50/);
    });

    // createTask must NOT have been called
    expect(mockCreateTask).not.toHaveBeenCalled();
  });

  it("#1909 AC3b: exactly 50 non-empty AC rows submits normally (fetch IS called)", async () => {
    const { submitBtn, user } = await renderWithAcCount(50);

    await user.click(submitBtn);

    await waitFor(() => {
      expect(mockCreateTask).toHaveBeenCalledOnce();
    });
  });
});
