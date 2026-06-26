// KillProjectModal + ProjectConsentGrantModal — operator-token wiring tests.
// Kanban #2503 Fix1.
//
// Asserts that when the operator-token field is filled, killProject /
// reviveProject / grantConsent are invoked WITH that token in the expected
// argument position. All api calls are mocked — no network.
//
// Determinism (#1310): async assertions use waitFor / findBy*.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor, configure } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ProjectRead } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

// ---------------------------------------------------------------------------
// Mock @/lib/api
// ---------------------------------------------------------------------------
const mockKillProject = vi.fn();
const mockReviveProject = vi.fn();
const mockGrantConsent = vi.fn();
const mockSetProjectToolsConfig = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    killProject: (...a: unknown[]) => mockKillProject(...a),
    reviveProject: (...a: unknown[]) => mockReviveProject(...a),
    grantConsent: (...a: unknown[]) => mockGrantConsent(...a),
    setProjectToolsConfig: (...a: unknown[]) => mockSetProjectToolsConfig(...a),
  };
});

// ---------------------------------------------------------------------------
// Mock next/navigation
// ---------------------------------------------------------------------------
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/p/test-project",
  useSearchParams: () => ({ get: () => null }),
}));

// ---------------------------------------------------------------------------
// Imports (AFTER mocks)
// ---------------------------------------------------------------------------
import { KillProjectModal } from "@/components/KillProjectModal";
import { ProjectConsentGrantModal } from "@/components/ProjectConsentGrantModal";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const SUCCESS_KILL_REVIVE = {
  success: true,
  project_id: 1,
  action: "kill" as const,
  is_killed: true,
  killed_at: "2026-01-01T00:00:00Z",
  killed_reason: "test",
  drain_summary: {},
  audit_id: 1,
};

const SUCCESS_PROJECT_READ: Partial<ProjectRead> = {
  id: 1,
  name: "test-project",
};

function makeProject(overrides: Partial<ProjectRead> = {}): ProjectRead {
  return {
    id: 1,
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
    is_killed: false,
    ...overrides,
  } as ProjectRead;
}

beforeEach(() => {
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// KillProjectModal — kill mode
// ---------------------------------------------------------------------------

describe("KillProjectModal (kill mode) — operator token", () => {
  it("passes the operator token as the 5th arg to killProject when filled", async () => {
    const user = userEvent.setup();
    mockKillProject.mockResolvedValueOnce({ ...SUCCESS_KILL_REVIVE, action: "kill" });

    const project = makeProject();
    render(<KillProjectModal project={project} mode="kill" />);

    // Open modal
    const triggerBtn = document.body.querySelector(
      "[data-kill-project-trigger='kill']",
    ) as HTMLButtonElement;
    await user.click(triggerBtn);

    // Type project name
    const nameInput = document.body.querySelector(
      "[data-kill-project-name-input]",
    ) as HTMLInputElement;
    await user.type(nameInput, "test-project");

    // Type reason (>=10 chars)
    const reasonInput = document.body.querySelector(
      "[data-kill-project-reason]",
    ) as HTMLTextAreaElement;
    await user.type(reasonInput, "test reason for killing");

    // Type operator token
    const tokenInput = document.body.querySelector(
      "[data-kill-project-operator-token]",
    ) as HTMLInputElement;
    await user.type(tokenInput, "my-secret-token");

    // Submit
    const submitBtn = document.body.querySelector(
      "[data-kill-project-submit]",
    ) as HTMLButtonElement;
    await user.click(submitBtn);

    await waitFor(() => expect(mockKillProject).toHaveBeenCalledTimes(1));

    const [projectId, body, force, actor, token] = mockKillProject.mock.calls[0];
    expect(projectId).toBe(1);
    expect(body).toEqual({ reason: "test reason for killing" });
    expect(force).toBe(false);
    expect(actor).toBeUndefined();
    expect(token).toBe("my-secret-token");
  });

  it("passes empty operatorToken when the token field is left blank (backward-compat)", async () => {
    const user = userEvent.setup();
    mockKillProject.mockResolvedValueOnce({ ...SUCCESS_KILL_REVIVE, action: "kill" });

    const project = makeProject();
    render(<KillProjectModal project={project} mode="kill" />);

    await user.click(
      document.body.querySelector("[data-kill-project-trigger='kill']") as HTMLButtonElement,
    );
    await user.type(
      document.body.querySelector("[data-kill-project-name-input]") as HTMLInputElement,
      "test-project",
    );
    await user.type(
      document.body.querySelector("[data-kill-project-reason]") as HTMLTextAreaElement,
      "test reason here",
    );
    await user.click(
      document.body.querySelector("[data-kill-project-submit]") as HTMLButtonElement,
    );

    await waitFor(() => expect(mockKillProject).toHaveBeenCalledTimes(1));
    // token arg is the 5th positional — empty string means applyOperatorToken
    // will NOT set the header (non-empty-after-trim gate).
    const [, , , , token] = mockKillProject.mock.calls[0];
    expect(token).toBe("");
  });
});

// ---------------------------------------------------------------------------
// KillProjectModal — revive mode
// ---------------------------------------------------------------------------

describe("KillProjectModal (revive mode) — operator token", () => {
  it("passes the operator token as the 3rd arg to reviveProject when filled", async () => {
    const user = userEvent.setup();
    mockReviveProject.mockResolvedValueOnce({ ...SUCCESS_KILL_REVIVE, action: "revive", is_killed: false });

    const project = makeProject({ is_killed: true });
    render(<KillProjectModal project={project} mode="revive" />);

    // Open modal
    await user.click(
      document.body.querySelector("[data-kill-project-trigger='revive']") as HTMLButtonElement,
    );

    // Type operator token
    const tokenInput = document.body.querySelector(
      "[data-kill-project-operator-token]",
    ) as HTMLInputElement;
    await user.type(tokenInput, "revive-token");

    // Submit
    await user.click(
      document.body.querySelector("[data-kill-project-submit]") as HTMLButtonElement,
    );

    await waitFor(() => expect(mockReviveProject).toHaveBeenCalledTimes(1));

    const [projectId, actor, token] = mockReviveProject.mock.calls[0];
    expect(projectId).toBe(1);
    expect(actor).toBeUndefined();
    expect(token).toBe("revive-token");
  });

  it("submit is enabled in revive mode even when token field is empty", async () => {
    const user = userEvent.setup();
    mockReviveProject.mockResolvedValueOnce({ ...SUCCESS_KILL_REVIVE, action: "revive", is_killed: false });

    const project = makeProject({ is_killed: true });
    render(<KillProjectModal project={project} mode="revive" />);

    await user.click(
      document.body.querySelector("[data-kill-project-trigger='revive']") as HTMLButtonElement,
    );

    const submitBtn = document.body.querySelector(
      "[data-kill-project-submit]",
    ) as HTMLButtonElement;
    // Token field intentionally left empty — submit must NOT be disabled.
    expect(submitBtn.disabled).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// ProjectConsentGrantModal — operator token
// ---------------------------------------------------------------------------

describe("ProjectConsentGrantModal — operator token", () => {
  it("passes the operator token as the 3rd arg to grantConsent when filled", async () => {
    const user = userEvent.setup();
    mockGrantConsent.mockResolvedValueOnce(SUCCESS_PROJECT_READ);

    const project = { id: 1, name: "test-project" };
    render(<ProjectConsentGrantModal project={project} />);

    // Open modal
    await user.click(
      document.body.querySelector("[data-consent-grant-trigger]") as HTMLButtonElement,
    );

    // Type project name
    await user.type(
      document.body.querySelector("[data-consent-grant-input]") as HTMLInputElement,
      "test-project",
    );

    // Type operator token
    await user.type(
      document.body.querySelector("[data-consent-grant-operator-token]") as HTMLInputElement,
      "consent-secret",
    );

    // Submit
    await user.click(
      document.body.querySelector("[data-consent-grant-submit]") as HTMLButtonElement,
    );

    await waitFor(() => expect(mockGrantConsent).toHaveBeenCalledTimes(1));

    const [projectId, confirmName, token] = mockGrantConsent.mock.calls[0];
    expect(projectId).toBe(1);
    expect(confirmName).toBe("test-project");
    expect(token).toBe("consent-secret");
  });

  it("calls grantConsent without a token when the field is left empty", async () => {
    const user = userEvent.setup();
    mockGrantConsent.mockResolvedValueOnce(SUCCESS_PROJECT_READ);

    const project = { id: 1, name: "test-project" };
    render(<ProjectConsentGrantModal project={project} />);

    await user.click(
      document.body.querySelector("[data-consent-grant-trigger]") as HTMLButtonElement,
    );
    await user.type(
      document.body.querySelector("[data-consent-grant-input]") as HTMLInputElement,
      "test-project",
    );
    await user.click(
      document.body.querySelector("[data-consent-grant-submit]") as HTMLButtonElement,
    );

    await waitFor(() => expect(mockGrantConsent).toHaveBeenCalledTimes(1));
    const [, , token] = mockGrantConsent.mock.calls[0];
    // Empty string — applyOperatorToken will NOT set the header.
    expect(token).toBe("");
  });

  it("submit is NOT disabled when token field is empty (token is optional)", async () => {
    const user = userEvent.setup();
    const project = { id: 1, name: "test-project" };
    render(<ProjectConsentGrantModal project={project} />);

    await user.click(
      document.body.querySelector("[data-consent-grant-trigger]") as HTMLButtonElement,
    );

    // Type project name (required), leave token blank
    await user.type(
      document.body.querySelector("[data-consent-grant-input]") as HTMLInputElement,
      "test-project",
    );

    const submitBtn = document.body.querySelector(
      "[data-consent-grant-submit]",
    ) as HTMLButtonElement;
    expect(submitBtn.disabled).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// ProjectConsentGrantModal — posture radio (#2732 Option C)
// ---------------------------------------------------------------------------

describe("ProjectConsentGrantModal — posture radio", () => {
  it("default posture (Q&A): submit calls grantConsent but NOT setProjectToolsConfig", async () => {
    const user = userEvent.setup();
    mockGrantConsent.mockResolvedValueOnce(SUCCESS_PROJECT_READ);

    const project = { id: 1, name: "test-project" };
    render(<ProjectConsentGrantModal project={project} />);

    await user.click(
      document.body.querySelector("[data-consent-grant-trigger]") as HTMLButtonElement,
    );
    await user.type(
      document.body.querySelector("[data-consent-grant-input]") as HTMLInputElement,
      "test-project",
    );

    // Q&A radio is default — do NOT click standard
    await user.click(
      document.body.querySelector("[data-consent-grant-submit]") as HTMLButtonElement,
    );

    await waitFor(() => expect(mockGrantConsent).toHaveBeenCalledTimes(1));
    expect(mockSetProjectToolsConfig).not.toHaveBeenCalled();
  });

  it("Standard posture: submit calls grantConsent then setProjectToolsConfig with correct body", async () => {
    const user = userEvent.setup();
    mockGrantConsent.mockResolvedValueOnce(SUCCESS_PROJECT_READ);
    mockSetProjectToolsConfig.mockResolvedValueOnce(SUCCESS_PROJECT_READ);

    const project = { id: 1, name: "test-project" };
    render(<ProjectConsentGrantModal project={project} />);

    await user.click(
      document.body.querySelector("[data-consent-grant-trigger]") as HTMLButtonElement,
    );
    await user.type(
      document.body.querySelector("[data-consent-grant-input]") as HTMLInputElement,
      "test-project",
    );

    // Select standard posture
    await user.click(
      document.body.querySelector("[data-consent-posture-standard]") as HTMLInputElement,
    );

    await user.click(
      document.body.querySelector("[data-consent-grant-submit]") as HTMLButtonElement,
    );

    await waitFor(() => expect(mockGrantConsent).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(mockSetProjectToolsConfig).toHaveBeenCalledTimes(1));

    const [projectId, toolsConfig, token] = mockSetProjectToolsConfig.mock.calls[0];
    expect(projectId).toBe(1);
    expect(toolsConfig).toEqual({
      tools_enabled: true,
      auto_allow_tiers: ["read"],
      halt_tiers: ["write", "network", "destructive"],
    });
    expect(token).toBe("");
  });

  it("partial-failure: grantConsent succeeds but setProjectToolsConfig rejects → distinct error text renders", async () => {
    const user = userEvent.setup();
    mockGrantConsent.mockResolvedValueOnce(SUCCESS_PROJECT_READ);
    mockSetProjectToolsConfig.mockRejectedValueOnce(new Error("tools write failed"));

    const project = { id: 1, name: "test-project" };
    render(<ProjectConsentGrantModal project={project} />);

    await user.click(
      document.body.querySelector("[data-consent-grant-trigger]") as HTMLButtonElement,
    );
    await user.type(
      document.body.querySelector("[data-consent-grant-input]") as HTMLInputElement,
      "test-project",
    );

    // Select standard posture to trigger the tools write
    await user.click(
      document.body.querySelector("[data-consent-posture-standard]") as HTMLInputElement,
    );

    await user.click(
      document.body.querySelector("[data-consent-grant-submit]") as HTMLButtonElement,
    );

    // Modal stays open with the partial-failure error (consent succeeded, tools write failed).
    await waitFor(() => {
      const el = document.body.querySelector("[data-consent-grant-error]") as HTMLElement | null;
      expect(el).not.toBeNull();
      expect(el?.textContent).toMatch(/Consent granted, but enabling Standard tools failed/);
    });
  });
});
