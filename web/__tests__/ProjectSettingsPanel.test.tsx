// ProjectSettingsPanel — Thinking effort section tests (Kanban #2300).
//
// Covered:
//   (1) renders "Default (off)" selected when effort_mode is null
//   (2) selecting "auto" + save fires PATCH with {effort_mode:"auto"}
//   (3) selecting "Default (off)" (i.e. null) + save fires PATCH with {effort_mode:null}
//
// Async: findBy/waitFor only (no sync querySelector on post-async state).

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  waitFor,
  configure,
} from "@testing-library/react";
import type { ProjectRead } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

// ── api mock ──────────────────────────────────────────────────────────────────
const mockUpdateProject = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    updateProject: (...args: Parameters<typeof actual.updateProject>) =>
      mockUpdateProject(...args),
  };
});

// ── next/navigation mock ──────────────────────────────────────────────────────
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
}));

// Imported AFTER mocks register.
import { ProjectSettingsPanel } from "@/components/ProjectSettingsPanel";

function makeProject(overrides: Partial<ProjectRead> = {}): ProjectRead {
  return {
    id: 42,
    name: "test-project",
    description: null,
    paths_web: "",
    paths_api: "",
    paths_db: "",
    stack_web: null,
    stack_api: null,
    stack_db: null,
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
    hitl_nudge_threshold_hours: null,
    effort_mode: null,
    ...overrides,
  };
}

beforeEach(() => {
  mockUpdateProject.mockReset();
  mockUpdateProject.mockResolvedValue(makeProject());
});

describe("ProjectSettingsPanel — Thinking effort", () => {
  it("renders 'Default (off)' selected when effort_mode is null", async () => {
    render(<ProjectSettingsPanel project={makeProject({ effort_mode: null })} />);
    const select = document.querySelector(
      "[data-project-effort-mode-select]",
    ) as HTMLSelectElement;
    expect(select).not.toBeNull();
    // The selected option text should match "Default (off)".
    const selectedOption = select.options[select.selectedIndex];
    expect(selectedOption.text).toBe("Default (off)");
  });

  it("selecting 'auto' and saving fires PATCH with {effort_mode:'auto'}", async () => {
    render(<ProjectSettingsPanel project={makeProject({ effort_mode: null })} />);
    const select = document.querySelector(
      "[data-project-effort-mode-select]",
    ) as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "auto" } });

    const saveBtn = document.querySelector(
      "[data-project-effort-mode-save]",
    ) as HTMLButtonElement;
    fireEvent.click(saveBtn);

    await waitFor(() => expect(mockUpdateProject).toHaveBeenCalledTimes(1));
    expect(mockUpdateProject).toHaveBeenCalledWith(42, { effort_mode: "auto" });
  });

  it("selecting 'Default (off)' and saving fires PATCH with {effort_mode:null}", async () => {
    // Start with a non-null value so switching to null makes the form dirty.
    render(
      <ProjectSettingsPanel project={makeProject({ effort_mode: "high" })} />,
    );
    const select = document.querySelector(
      "[data-project-effort-mode-select]",
    ) as HTMLSelectElement;
    // "__null__" is the internal encoding for null (see component source).
    fireEvent.change(select, { target: { value: "__null__" } });

    const saveBtn = document.querySelector(
      "[data-project-effort-mode-save]",
    ) as HTMLButtonElement;
    fireEvent.click(saveBtn);

    await waitFor(() => expect(mockUpdateProject).toHaveBeenCalledTimes(1));
    expect(mockUpdateProject).toHaveBeenCalledWith(42, { effort_mode: null });
  });
});
